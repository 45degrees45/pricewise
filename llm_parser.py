import json
import logging

import httpx

from config import (
    ANTHROPIC_API_KEY,
    CONFIDENCE_THRESHOLD,
    LLM_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
)

logger = logging.getLogger(__name__)

# Lazy-init Anthropic client only when key is available
_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


def _call_ollama(system: str, user_text: str, max_tokens: int) -> str:
    """Call a local Ollama instance and return the assistant message text."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    resp = httpx.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def _call_llm(system: str, user_text: str, max_tokens: int) -> str:
    """Dispatch to Anthropic if key is set, otherwise Ollama."""
    if ANTHROPIC_API_KEY:
        logger.info("Using Anthropic (%s)", LLM_MODEL)
        client = _get_anthropic_client()
        response = client.messages.create(
            model=LLM_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        return response.content[0].text.strip()

    logger.info("Using Ollama (%s)", OLLAMA_MODEL)
    return _call_ollama(system, user_text, max_tokens).strip()


def _extract_json(raw: str):
    """Extract JSON from LLM response, handling markdown fences and surrounding text."""
    import re

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1).strip()
    else:
        # Try to find JSON array or object in the response
        json_match = re.search(r"(\[.*\]|\{.*\})", raw, re.DOTALL)
        if json_match:
            raw = json_match.group(1).strip()

    logger.debug("Extracted JSON text: %s", raw[:200])
    return json.loads(raw)


RECEIPT_SYSTEM_PROMPT = """You are a purchase data extractor. You receive OCR text from receipts/bills (possibly from two different OCR engines). Extract structured purchase data.

Rules:
- If two OCR sources are provided, reconcile differences by picking the most plausible reading
- Dates should be in YYYY-MM-DD format
- Prices should be numeric (no currency symbols)
- If multiple items exist, return a JSON array; if single item, return a single JSON object
- Set confidence 0.0-1.0 based on how clear/unambiguous the text is
- Use "notes" for anything uncertain or interesting

Indian receipt guidance:
- Indian store receipts (Lulu, DMart, BigBazaar, etc.) often have a MRP/LRP column and a Net Amount column
- The MRP/LRP column shows two values like "999.00/649.00" — the first is MRP, the second is LRP (store selling price)
- LRP (Lulu Retail Price) is the ACTUAL per-unit selling rate. Use LRP (the SECOND number) as "unit_price", NOT the MRP.
- If MRP and LRP differ, note "MRP ₹X, LRP ₹Y" in "notes"
- Net Amount / Nt Amt (last number on each line) = total paid for that line item. Use this as "price".
- "price" should always be the amount the customer actually paid (Net Amount), NOT MRP × quantity.
- Quantity is critical! Look for patterns like "2.000 KG", "3 NOS", "1.500 KG", "2 PCS", "500 GM" etc.
  - "2.000 KG" means quantity=2, "1.500 KG" means quantity=1.5, "500 GM" means quantity=0.5
  - "3 NOS" or "3 PCS" means quantity=3
  - Never default quantity to 1 if a quantity is present on the receipt line

Return ONLY valid JSON with these fields:
{
  "date": "YYYY-MM-DD",
  "item": "item name",
  "store": "store name",
  "price": 0.00,        // TOTAL price actually paid (Net Amount on Indian receipts)
  "quantity": 1,
  "unit_price": 0.00,   // per-unit selling rate (LRP on Indian receipts, NOT MRP)
  "card_used": "card name or empty string",
  "cashback": 0.00,
  "confidence": 0.9,
  "notes": ""
}

IMPORTANT: "price" is the TOTAL amount actually paid for that line item. "unit_price" is the per-unit cost. For example, 2 kg of rice at ₹60/kg → unit_price: 60, price: 120."""

TEXT_SYSTEM_PROMPT = """You are a purchase data extractor. The user describes a purchase in natural language (e.g., "Bought eggs ₹60 at DMart"). Extract structured purchase data.

Rules:
- Dates should be in YYYY-MM-DD format. If no date given, use "today" as a placeholder.
- Prices should be numeric (no currency symbols)
- Set confidence based on how complete the information is
- If quantity and price are given, compute unit_price

Return ONLY valid JSON with these fields:
{
  "date": "YYYY-MM-DD or today",
  "item": "item name",
  "store": "store name",
  "price": 0.00,
  "quantity": 1,
  "unit_price": 0.00,
  "card_used": "",
  "cashback": 0.00,
  "confidence": 0.9,
  "notes": ""
}"""


def parse_receipt(ocr_results: dict) -> list[dict]:
    """Parse receipt OCR text using LLM. Returns list of purchase dicts."""
    parts = []
    if ocr_results.get("vision"):
        parts.append(f"=== macOS Vision OCR ===\n{ocr_results['vision']}")
    if ocr_results.get("drive"):
        parts.append(f"=== Google Drive OCR ===\n{ocr_results['drive']}")

    if not parts:
        raise ValueError("No OCR text available to parse")

    user_text = "\n\n".join(parts)
    logger.info("OCR text:\n%s", user_text[:3000])
    logger.info("Sending %d chars to LLM for receipt parsing", len(user_text))

    raw = _call_llm(RECEIPT_SYSTEM_PROMPT, user_text, 4096)
    logger.info("LLM raw response (%d chars): %s", len(raw), raw[:2000])
    parsed = _extract_json(raw)
    logger.info("Parsed %d items: %s", len(parsed) if isinstance(parsed, list) else 1, json.dumps(parsed, indent=2)[:2000])
    if isinstance(parsed, dict):
        parsed = [parsed]
    return parsed


def parse_text(user_message: str) -> list[dict]:
    """Parse natural language purchase description using LLM."""
    logger.info("Sending text to LLM for parsing: %s", user_message[:100])

    raw = _call_llm(TEXT_SYSTEM_PROMPT, user_message, 1024)
    parsed = _extract_json(raw)
    if isinstance(parsed, dict):
        parsed = [parsed]
    return parsed


def needs_confirmation(purchase: dict) -> bool:
    """Check if a purchase needs manual confirmation due to low confidence."""
    try:
        return float(purchase.get("confidence", 0)) < CONFIDENCE_THRESHOLD
    except (ValueError, TypeError):
        return True
