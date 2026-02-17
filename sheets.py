from __future__ import annotations

import logging
from datetime import datetime

import gspread
from rapidfuzz import fuzz

from config import (
    COLUMNS,
    FUZZY_MATCH_CUTOFF,
    SHEET_NAME,
    SHEETS_ID,
)
from google_auth import get_google_creds

logger = logging.getLogger(__name__)

def _get_client() -> gspread.Client:
    creds = get_google_creds()
    return gspread.authorize(creds)


def _get_sheet() -> gspread.Worksheet:
    client = _get_client()
    spreadsheet = client.open_by_key(SHEETS_ID)
    try:
        worksheet = spreadsheet.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=len(COLUMNS))
        worksheet.append_row(COLUMNS)
        logger.info("Created worksheet '%s' with headers", SHEET_NAME)
    return worksheet


def append_purchase(data: dict) -> int:
    """Append a purchase row. Returns the row number."""
    sheet = _get_sheet()
    row = [
        data.get("date", datetime.now().strftime("%Y-%m-%d")),
        data.get("item", ""),
        data.get("store", ""),
        str(data.get("price", "")),
        str(data.get("quantity", "")),
        str(data.get("unit_price", "")),
        data.get("card_used", ""),
        str(data.get("cashback", "")),
        str(data.get("confidence", "")),
        data.get("notes", ""),
    ]
    sheet.append_row(row, value_input_option="USER_ENTERED")
    return sheet.row_count


def get_all_purchases() -> list[dict]:
    """Return all purchases as list of dicts."""
    sheet = _get_sheet()
    records = sheet.get_all_records()
    return records


def find_best_price(item_query: str) -> dict | None:
    """Find best price for an item using fuzzy matching.

    Returns dict with item, min, max, avg, count, and matches list.
    """
    records = get_all_purchases()
    if not records:
        return None

    matches = []
    for row in records:
        item_name = str(row.get("Item", ""))
        if not item_name:
            continue
        score = fuzz.token_set_ratio(item_query.lower(), item_name.lower())
        if score >= FUZZY_MATCH_CUTOFF:
            try:
                price = float(row.get("Price", 0))
            except (ValueError, TypeError):
                continue
            matches.append({
                "item": item_name,
                "price": price,
                "store": row.get("Store", ""),
                "date": row.get("Date", ""),
                "score": score,
            })

    if not matches:
        return None

    prices = [m["price"] for m in matches]
    matches.sort(key=lambda m: m["price"])

    return {
        "query": item_query,
        "min": min(prices),
        "max": max(prices),
        "avg": round(sum(prices) / len(prices), 2),
        "count": len(matches),
        "matches": matches,
    }


def is_good_deal(item_query: str, proposed_price: float) -> dict:
    """Check if proposed price is a good deal compared to history."""
    result = find_best_price(item_query)
    if not result:
        return {
            "verdict": "unknown",
            "message": f"No price history found for '{item_query}'.",
        }

    if proposed_price <= result["min"]:
        verdict = "great"
        msg = f"Great deal! {proposed_price} is at or below the best price ({result['min']})"
    elif proposed_price <= result["avg"]:
        verdict = "good"
        msg = f"Good deal. {proposed_price} is below average ({result['avg']})"
    elif proposed_price <= result["max"]:
        verdict = "fair"
        msg = f"Fair price. {proposed_price} is above average ({result['avg']}) but below max ({result['max']})"
    else:
        verdict = "bad"
        msg = f"Expensive! {proposed_price} is above the max recorded price ({result['max']})"

    return {
        "verdict": verdict,
        "message": msg,
        "history": result,
    }
