import asyncio
import logging
import os
from datetime import datetime

from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from config import ALLOWED_USER_IDS, COLUMNS, TELEGRAM_TOKEN, TEMP_DIR
from llm_parser import needs_confirmation, parse_receipt, parse_text
from ocr import dual_ocr
from sheets import append_purchase, find_best_price, is_good_deal

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Conversation states
CONFIRMING, EDITING_FIELD, EDITING_VALUE = range(3)

# Editable fields
EDITABLE_FIELDS = ["date", "item", "store", "price", "quantity", "card_used", "cashback", "notes"]

# Purchase keywords that trigger text parsing
PURCHASE_KEYWORDS = ["bought", "purchased", "paid", "spent", "cost", "₹", "rs", "inr"]


def auth_check(user_id: int) -> bool:
    """Check if user is authorized."""
    if not ALLOWED_USER_IDS:
        return True  # No restriction if list is empty
    return user_id in ALLOWED_USER_IDS


def format_purchase(p: dict) -> str:
    """Format a purchase dict for display."""
    lines = []
    lines.append(f"📦 *{p.get('item', 'Unknown')}*")
    if p.get("store"):
        lines.append(f"🏪 Store: {p['store']}")
    if p.get("date"):
        lines.append(f"📅 Date: {p['date']}")
    if p.get("price"):
        lines.append(f"💰 Price: ₹{p['price']}")
    if p.get("quantity") and p.get("quantity") != 1:
        lines.append(f"📊 Qty: {p['quantity']}")
    if p.get("unit_price"):
        lines.append(f"📊 Unit Price: ₹{p['unit_price']}")
    if p.get("card_used"):
        lines.append(f"💳 Card: {p['card_used']}")
    if p.get("cashback"):
        lines.append(f"🎁 Cashback: ₹{p['cashback']}")
    if p.get("confidence"):
        conf = float(p["confidence"])
        emoji = "🟢" if conf >= 0.7 else "🟡" if conf >= 0.4 else "🔴"
        lines.append(f"{emoji} Confidence: {conf:.0%}")
    if p.get("notes"):
        lines.append(f"📝 {p['notes']}")
    return "\n".join(lines)


def confirm_keyboard() -> InlineKeyboardMarkup:
    """Build confirm/edit/cancel keyboard for a single item."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Save", callback_data="confirm"),
            InlineKeyboardButton("✏️ Edit", callback_data="edit"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to summary", callback_data="back_summary"),
        ],
    ])


def confirm_all_keyboard() -> InlineKeyboardMarkup:
    """Build confirm-all/review/cancel keyboard for multi-item receipts."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm All", callback_data="confirm_all"),
            InlineKeyboardButton("🔍 Review", callback_data="review_pick"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ]
    ])


def item_picker_keyboard(purchases: list[dict]) -> InlineKeyboardMarkup:
    """Build a grid of numbered item buttons for picking which to review."""
    buttons = []
    row = []
    for i, p in enumerate(purchases):
        label = f"{i + 1}. {p.get('item', '?')[:15]}"
        row.append(InlineKeyboardButton(label, callback_data=f"pick_{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Back to summary", callback_data="back_summary")])
    return InlineKeyboardMarkup(buttons)


def field_keyboard() -> InlineKeyboardMarkup:
    """Build field selection keyboard for editing."""
    buttons = []
    row = []
    for field in EDITABLE_FIELDS:
        row.append(InlineKeyboardButton(field.replace("_", " ").title(), callback_data=f"field_{field}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="back")])
    return InlineKeyboardMarkup(buttons)


# --- Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not auth_check(update.effective_user.id):
        return
    await update.message.reply_text(
        "Welcome to PriceWise! 🛒\n\n"
        "Send me a receipt photo or describe a purchase.\n\n"
        "Commands:\n"
        "/best <item> - Find best prices\n"
        "/deal <price> <item> - Check if it's a good deal"
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle photo messages - download, OCR, parse, show preview."""
    if not auth_check(update.effective_user.id):
        return ConversationHandler.END

    await update.message.reply_text("Processing receipt... 🔍")

    # Get the best resolution photo
    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_path = TEMP_DIR / f"{photo.file_unique_id}.jpg"
    await file.download_to_drive(str(file_path))

    try:
        ocr_results = await dual_ocr(str(file_path))
        purchases = parse_receipt(ocr_results)
    except Exception as e:
        logger.error("Processing failed: %s", e)
        await update.message.reply_text(f"Failed to process receipt: {e}")
        return ConversationHandler.END
    finally:
        file_path.unlink(missing_ok=True)

    return await _show_purchases(update, context, purchases)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle document uploads (full resolution images)."""
    if not auth_check(update.effective_user.id):
        return ConversationHandler.END

    doc = update.message.document
    if not doc.mime_type or not (doc.mime_type.startswith("image/") or doc.mime_type == "application/pdf"):
        await update.message.reply_text("Please send an image or PDF file.")
        return ConversationHandler.END

    await update.message.reply_text("Processing receipt... 🔍")

    file = await doc.get_file()
    ext = os.path.splitext(doc.file_name or "image.jpg")[1] or ".jpg"
    file_path = TEMP_DIR / f"{doc.file_unique_id}{ext}"
    await file.download_to_drive(str(file_path))

    try:
        ocr_results = await dual_ocr(str(file_path))
        purchases = parse_receipt(ocr_results)
    except Exception as e:
        logger.error("Processing failed: %s", e)
        await update.message.reply_text(f"Failed to process receipt: {e}")
        return ConversationHandler.END
    finally:
        file_path.unlink(missing_ok=True)

    return await _show_purchases(update, context, purchases)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text messages - detect purchase descriptions and parse them."""
    if not auth_check(update.effective_user.id):
        return ConversationHandler.END

    text = update.message.text.lower()
    if not any(kw in text for kw in PURCHASE_KEYWORDS):
        await update.message.reply_text(
            "I didn't detect a purchase. Try describing it like:\n"
            "\"Bought eggs ₹60 at DMart\"\n\n"
            "Or send a receipt photo!"
        )
        return ConversationHandler.END

    try:
        purchases = parse_text(update.message.text)
    except Exception as e:
        logger.error("Text parsing failed: %s", e)
        await update.message.reply_text(f"Failed to parse: {e}")
        return ConversationHandler.END

    # Replace "today" placeholder with actual date
    for p in purchases:
        if p.get("date") == "today":
            p["date"] = datetime.now().strftime("%Y-%m-%d")

    return await _show_purchases(update, context, purchases)


async def _show_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE, purchases: list[dict]) -> int:
    """Show parsed purchases and ask for confirmation."""
    if not purchases:
        await update.message.reply_text("No purchases found in the data.")
        return ConversationHandler.END

    # Store in user data for the confirm/edit flow
    context.user_data["pending_purchases"] = purchases
    context.user_data["current_index"] = 0

    # Show all items in one summary message
    lines = []
    any_low_conf = False
    for i, p in enumerate(purchases):
        any_low_conf = any_low_conf or needs_confirmation(p)
        lines.append(f"*{i + 1}.* {p.get('item', 'Unknown')} — ₹{p.get('unit_price', p.get('price', '?'))} × {p.get('quantity', 1)} = ₹{p.get('price', '?')}")
    header = f"Found {len(purchases)} item(s):"
    if any_low_conf:
        header += " ⚠️ Some items have low confidence"
    summary = header + "\n\n" + "\n".join(lines)
    summary += f"\n\n_Total: ₹{sum(float(p.get('price', 0)) for p in purchases):,.2f}_"

    await update.message.reply_text(
        summary,
        reply_markup=confirm_all_keyboard(),
        parse_mode="Markdown",
    )
    return CONFIRMING


async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle confirm/edit/cancel button presses."""
    query = update.callback_query
    await query.answer()

    action = query.data
    purchases = context.user_data.get("pending_purchases", [])
    idx = context.user_data.get("current_index", 0)

    if not purchases or idx >= len(purchases):
        await query.edit_message_text("No pending purchase.")
        return ConversationHandler.END

    if action == "confirm_all":
        saved = 0
        for p in purchases:
            try:
                append_purchase(p)
                saved += 1
            except Exception as e:
                logger.error("Failed to save %s: %s", p.get("item"), e)
        total = sum(float(p.get("price", 0)) for p in purchases)
        await query.edit_message_text(f"✅ Saved {saved}/{len(purchases)} items. Total: ₹{total:,.2f}")
        context.user_data.pop("pending_purchases", None)
        return ConversationHandler.END

    elif action == "review_pick":
        # Show item picker grid
        total = sum(float(p.get("price", 0)) for p in purchases)
        await query.edit_message_text(
            f"Select an item to review:\n\n_Total: ₹{total:,.2f}_",
            reply_markup=item_picker_keyboard(purchases),
            parse_mode="Markdown",
        )
        return CONFIRMING

    elif action.startswith("pick_"):
        # User picked a specific item to review
        pick_idx = int(action.split("_")[1])
        context.user_data["current_index"] = pick_idx
        p = purchases[pick_idx]
        low_conf = " ⚠️ Low confidence!" if needs_confirmation(p) else ""
        await query.edit_message_text(
            f"Item ({pick_idx + 1}/{len(purchases)}):{low_conf}\n\n{format_purchase(p)}",
            reply_markup=confirm_keyboard(),
            parse_mode="Markdown",
        )
        return CONFIRMING

    elif action == "back_summary":
        # Go back to the full summary view
        lines = []
        for i, p in enumerate(purchases):
            lines.append(f"*{i + 1}.* {p.get('item', 'Unknown')} — ₹{p.get('unit_price', p.get('price', '?'))} × {p.get('quantity', 1)} = ₹{p.get('price', '?')}")
        total = sum(float(p.get("price", 0)) for p in purchases)
        summary = f"Found {len(purchases)} item(s):\n\n" + "\n".join(lines)
        summary += f"\n\n_Total: ₹{total:,.2f}_"
        await query.edit_message_text(
            summary,
            reply_markup=confirm_all_keyboard(),
            parse_mode="Markdown",
        )
        return CONFIRMING

    elif action == "confirm":
        # Single item confirm (from review view) — go back to summary
        p = purchases[idx]
        try:
            append_purchase(p)
        except Exception as e:
            logger.error("Failed to save: %s", e)
            await query.edit_message_text(f"❌ Failed to save: {e}")
            return ConversationHandler.END

        # Remove saved item and go back to summary
        purchases.pop(idx)
        context.user_data["pending_purchases"] = purchases
        if not purchases:
            await query.edit_message_text(f"✅ All items saved!")
            context.user_data.pop("pending_purchases", None)
            return ConversationHandler.END

        lines = []
        for i, p2 in enumerate(purchases):
            lines.append(f"*{i + 1}.* {p2.get('item', 'Unknown')} — ₹{p2.get('unit_price', p2.get('price', '?'))} × {p2.get('quantity', 1)} = ₹{p2.get('price', '?')}")
        total = sum(float(p2.get("price", 0)) for p2 in purchases)
        summary = f"✅ Saved {p.get('item')}. {len(purchases)} remaining:\n\n" + "\n".join(lines)
        summary += f"\n\n_Total: ₹{total:,.2f}_"
        await query.edit_message_text(
            summary,
            reply_markup=confirm_all_keyboard(),
            parse_mode="Markdown",
        )
        return CONFIRMING

    elif action == "edit":
        await query.edit_message_text(
            "Which field do you want to edit?",
            reply_markup=field_keyboard(),
        )
        return EDITING_FIELD

    elif action == "cancel":
        await query.edit_message_text("❌ Cancelled.")
        context.user_data.pop("pending_purchases", None)
        return ConversationHandler.END

    return CONFIRMING


async def field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle field selection for editing."""
    query = update.callback_query
    await query.answer()

    if query.data == "back":
        purchases = context.user_data.get("pending_purchases", [])
        idx = context.user_data.get("current_index", 0)
        p = purchases[idx]
        await query.edit_message_text(
            f"Updated preview:\n\n{format_purchase(p)}",
            reply_markup=confirm_keyboard(),
            parse_mode="Markdown",
        )
        return CONFIRMING

    field = query.data.replace("field_", "")
    context.user_data["editing_field"] = field
    purchases = context.user_data.get("pending_purchases", [])
    idx = context.user_data.get("current_index", 0)
    current_value = purchases[idx].get(field, "")

    await query.edit_message_text(
        f"Current {field}: `{current_value}`\n\nSend the new value:",
        parse_mode="Markdown",
    )
    return EDITING_VALUE


async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive new value for the field being edited."""
    field = context.user_data.get("editing_field")
    new_value = update.message.text.strip()
    purchases = context.user_data.get("pending_purchases", [])
    idx = context.user_data.get("current_index", 0)

    # Convert numeric fields
    if field in ("price", "quantity", "unit_price", "cashback"):
        try:
            new_value = float(new_value)
        except ValueError:
            await update.message.reply_text("Please enter a valid number.")
            return EDITING_VALUE

    purchases[idx][field] = new_value

    # Recompute unit_price if price or quantity changed
    if field in ("price", "quantity"):
        try:
            price = float(purchases[idx].get("price", 0))
            qty = float(purchases[idx].get("quantity", 1))
            if qty > 0:
                purchases[idx]["unit_price"] = round(price / qty, 2)
        except (ValueError, TypeError):
            pass

    await update.message.reply_text(
        f"Updated preview:\n\n{format_purchase(purchases[idx])}",
        reply_markup=confirm_keyboard(),
        parse_mode="Markdown",
    )
    return CONFIRMING


async def best_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /best command."""
    if not auth_check(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /best <item name>")
        return

    item_query = " ".join(context.args)
    result = find_best_price(item_query)

    if not result:
        await update.message.reply_text(f"No price history found for '{item_query}'.")
        return

    lines = [f"📊 *Price history for '{result['query']}'*\n"]
    lines.append(f"Min: ₹{result['min']}")
    lines.append(f"Max: ₹{result['max']}")
    lines.append(f"Avg: ₹{result['avg']}")
    lines.append(f"Records: {result['count']}\n")

    for m in result["matches"][:5]:
        lines.append(f"  ₹{m['price']} at {m['store']} ({m['date']})")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def deal_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /deal command."""
    if not auth_check(update.effective_user.id):
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /deal <price> <item name>")
        return

    try:
        price = float(context.args[0])
    except ValueError:
        await update.message.reply_text("First argument must be a price (number).")
        return

    item_query = " ".join(context.args[1:])
    result = is_good_deal(item_query, price)

    emoji_map = {"great": "🟢", "good": "🟡", "fair": "🟠", "bad": "🔴", "unknown": "⚪"}
    emoji = emoji_map.get(result["verdict"], "⚪")
    await update.message.reply_text(f"{emoji} {result['message']}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel command."""
    context.user_data.pop("pending_purchases", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def _run_webhook(ptb_app: Application) -> None:
    """Run the bot with an aiohttp web server (webhook + health check).

    If WEBHOOK_URL is set, registers the Telegram webhook.
    Always binds to PORT so Cloud Run health checks pass.
    """

    async def health_handler(request: web.Request) -> web.Response:
        return web.Response(text="OK")

    async def telegram_handler(request: web.Request) -> web.Response:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.update_queue.put(update)
        return web.Response()

    web_app = web.Application()
    web_app.router.add_get("/healthz", health_handler)
    web_app.router.add_post("/webhook", telegram_handler)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)

    async with ptb_app:
        await ptb_app.start()
        if config.WEBHOOK_URL:
            await ptb_app.bot.set_webhook(f"{config.WEBHOOK_URL}/webhook")
            logger.info("Webhook registered: %s/webhook", config.WEBHOOK_URL)
        else:
            logger.info("WEBHOOK_URL not set — server running but webhook not registered yet")
        await site.start()
        logger.info("Listening on port %d", config.PORT)
        try:
            await asyncio.Event().wait()
        finally:
            if config.WEBHOOK_URL:
                await ptb_app.bot.delete_webhook()
            await runner.cleanup()


def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.PHOTO, handle_photo),
            MessageHandler(filters.Document.IMAGE | filters.Document.PDF, handle_document),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
        ],
        states={
            CONFIRMING: [CallbackQueryHandler(confirm_callback)],
            EDITING_FIELD: [CallbackQueryHandler(field_callback)],
            EDITING_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("best", best_price))
    app.add_handler(CommandHandler("deal", deal_check))
    app.add_handler(conv_handler)

    # On Cloud Run (K_SERVICE is set) always use the web server so the port is bound.
    # Locally, fall back to polling when no WEBHOOK_URL is configured.
    on_cloud_run = bool(os.getenv("K_SERVICE"))
    if config.WEBHOOK_URL or on_cloud_run:
        logger.info("Bot starting in webhook/server mode...")
        asyncio.run(_run_webhook(app))
    else:
        logger.info("Bot starting in polling mode...")
        app.run_polling()


if __name__ == "__main__":
    main()
