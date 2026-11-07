#!/usr/bin/env python3
"""
Daily Kerala vegetable price scraper for Kottayam market.
Sources: VFPCK, todaypricerates.com, vegetablemarketprice.com
Outputs: docs/prices.json  +  optional Notion database sync.

Usage:
  python fetch_prices.py

Env vars for Notion sync (optional):
  NOTION_TOKEN   - Notion internal integration token
  NOTION_DB_ID   - Target database ID (without dashes)
"""

import json
import os
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

PRICES_JSON = Path("docs/prices.json")
IST = ZoneInfo("Asia/Kolkata")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Lowercase alias → item ID in prices.json
NAME_ALIASES: dict[str, str] = {
    "tomato": "tomato", "takkali": "tomato",
    "onion": "onion_big", "big onion": "onion_big", "onion big": "onion_big",
    "small onion": "shallot", "shallot": "shallot", "sambar onion": "shallot",
    "potato": "potato", "urulakizhangu": "potato",
    "green chilli": "green_chilli", "green chilly": "green_chilli",
    "chilli": "green_chilli", "chilly": "green_chilli",
    "cucumber": "cucumber", "vellarikka": "cucumber",
    "ladies finger": "ladies_finger", "lady finger": "ladies_finger",
    "okra": "ladies_finger", "vendakka": "ladies_finger",
    "brinjal": "brinjal", "eggplant": "brinjal", "vazhuthananga": "brinjal",
    "bitter gourd": "bitter_gourd", "bittergourd": "bitter_gourd",
    "karela": "bitter_gourd", "pavakka": "bitter_gourd",
    "snake gourd": "snake_gourd", "snakegourd": "snake_gourd", "padavalanga": "snake_gourd",
    "bottle gourd": "bottle_gourd", "bottlegourd": "bottle_gourd", "churakka": "bottle_gourd",
    "ash gourd": "ash_gourd", "ashgourd": "ash_gourd", "kumbalanga": "ash_gourd",
    "ridge gourd": "ridge_gourd", "ridgegourd": "ridge_gourd", "peechinga": "ridge_gourd",
    "pumpkin": "pumpkin", "mathan": "pumpkin",
    "cabbage": "cabbage", "muttakose": "cabbage",
    "cauliflower": "cauliflower",
    "carrot": "carrot",
    "beetroot": "beetroot", "beet": "beetroot",
    "capsicum": "capsicum", "bell pepper": "capsicum", "shimla mirch": "capsicum",
    "french beans": "french_beans", "beans": "french_beans",
    "drumstick": "drumstick", "muringakka": "drumstick",
    "ivy gourd": "ivy_gourd", "kovakka": "ivy_gourd", "tindora": "ivy_gourd",
    "breadfruit": "kadachakka", "kadachakka": "kadachakka",
    "coconut": "coconut", "thenga": "coconut",
    "tapioca": "tapioca", "kappa": "tapioca", "cassava": "tapioca",
    "koorkka": "koorkka", "chinese potato": "koorkka",
    "elephant yam": "elephant_yam", "chena": "elephant_yam", "yam": "elephant_yam",
    "colocasia": "colocasia", "chembu": "colocasia", "taro": "colocasia",
    "sweet potato": "sweet_potato",
    "raw mango": "raw_mango", "green mango": "raw_mango",
    "raw banana": "raw_banana", "plantain": "raw_banana", "nendran": "raw_banana",
    "banana flower": "banana_flower", "banana blossom": "banana_flower",
    "cowpea": "cowpea", "long beans": "cowpea", "payar": "cowpea",
    "kudampuli": "kudampuli", "pot tamarind": "kudampuli", "gamboge": "kudampuli",
    "tender coconut": "karikku", "karikku": "karikku", "coconut water": "karikku",
    "spinach": "spinach", "amaranth": "spinach", "cheera": "spinach",
    "curry leaves": "curry_leaves", "curry leaf": "curry_leaves", "karivepila": "curry_leaves",
    "coriander": "coriander", "coriander leaves": "coriander", "kothamalli": "coriander",
    "mint": "mint", "mint leaves": "mint", "puthina": "mint",
    "colocasia leaves": "colocasia_leaves", "taro leaves": "colocasia_leaves",
    "ginger": "ginger", "inji": "ginger",
    "garlic": "garlic", "vellulli": "garlic",
    "lemon": "lemon", "lime": "lemon", "cherunaranga": "lemon",
    "amla": "amla", "gooseberry": "amla", "nellika": "amla",
    "mushroom": "mushroom", "koon": "mushroom",
    "green peas": "green_peas", "peas": "green_peas",
}


def fuzzy_match(name: str) -> str | None:
    n = name.lower().strip()
    n = re.sub(r"\s+", " ", n)

    if n in NAME_ALIASES:
        return NAME_ALIASES[n]

    for alias, item_id in NAME_ALIASES.items():
        if alias in n or n in alias:
            return item_id

    best, best_id = 0.0, None
    for alias, item_id in NAME_ALIASES.items():
        score = SequenceMatcher(None, n, alias).ratio()
        if score > best and score > 0.78:
            best, best_id = score, item_id
    return best_id


def extract_price(text: str) -> float | None:
    cleaned = re.sub(r"[₹Rs\s,]", "", text)
    # Handle ranges like "30-40" → take lower
    range_m = re.match(r"(\d+\.?\d*)\s*[-–]\s*(\d+\.?\d*)", cleaned)
    if range_m:
        return float(range_m.group(1))
    m = re.search(r"\d+\.?\d*", cleaned)
    if m:
        val = float(m.group())
        if 1 <= val <= 3000:
            return val
    return None


def scrape_todaypricerates() -> dict[str, float]:
    url = "https://market.todaypricerates.com/Kottayam-vegetables-price-in-Kerala"
    prices: dict[str, float] = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) >= 2 and cells[0]:
                    for col in cells[1:3]:
                        price = extract_price(col)
                        if price:
                            item_id = fuzzy_match(cells[0])
                            if item_id and item_id not in prices:
                                prices[item_id] = price
                            break
        print(f"  todaypricerates.com → {len(prices)} items")
    except Exception as e:
        print(f"  todaypricerates.com failed: {e}")
    return prices


def scrape_vfpck() -> dict[str, float]:
    """VFPCK Kottayam wholesale prices (per kg)."""
    url = "https://www.vfpck.org/mwiseprice.asp?ID=9"
    prices: dict[str, float] = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        skip_headers = {"commodity", "sl no", "sl. no.", "item", "s.no", "s.no.", "no"}
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) < 2:
                    continue
                name = cells[0].strip()
                if name.lower() in skip_headers or not name:
                    continue
                # VFPCK columns: Commodity | Min | Max | Modal (or similar)
                # Take last numeric column (modal price)
                for col in reversed(cells[1:]):
                    price = extract_price(col)
                    if price:
                        item_id = fuzzy_match(name)
                        if item_id and item_id not in prices:
                            prices[item_id] = price
                        break
        print(f"  VFPCK → {len(prices)} items")
    except Exception as e:
        print(f"  VFPCK failed: {e}")
    return prices


def scrape_vegetablemarketprice() -> dict[str, float]:
    url = "https://vegetablemarketprice.com/market/kerala/today"
    prices: dict[str, float] = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) >= 2 and cells[0]:
                    for col in cells[1:3]:
                        price = extract_price(col)
                        if price:
                            item_id = fuzzy_match(cells[0])
                            if item_id and item_id not in prices:
                                prices[item_id] = price
                            break
        print(f"  vegetablemarketprice.com → {len(prices)} items")
    except Exception as e:
        print(f"  vegetablemarketprice.com failed: {e}")
    return prices


# ── Notion sync ───────────────────────────────────────────────────────────────

def _notion_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def _item_to_notion_props(item: dict, updated_iso: str) -> dict:
    props: dict = {
        "Name": {"title": [{"text": {"content": item["name"]}}]},
        "ItemID": {"rich_text": [{"text": {"content": item["id"]}}]},
        "Category": {"select": {"name": item["category"]}},
        "Malayalam": {"rich_text": [{"text": {"content": item["malayalam"]}}]},
        "Wholesale ₹": {"number": item["wholesale"]},
        "Retail Low ₹": {"number": item["retail_low"]},
        "Retail High ₹": {"number": item["retail_high"]},
        "Avoid Above ₹": {"number": item["avoid_above"]},
        "Unit": {"select": {"name": item["unit"]}},
        "Updated": {"date": {"start": updated_iso[:10]}},
    }
    if item.get("note"):
        props["Note"] = {"rich_text": [{"text": {"content": item["note"]}}]}
    return props


NOTION_DB_ID_DEFAULT = "ac7daa2b5c46461ca0e075939742db1b"
NOTION_CHANGELOG_PAGE_ID = os.environ.get(
    "NOTION_CHANGELOG_PAGE_ID", "411764cab288815ab750dbf64941e381"
)
HISTORY_JSON = Path("docs/price_history.json")
CHANGE_THRESHOLD_PCT = 5  # alert/highlight changes above this %


def sync_to_notion(data: dict) -> None:
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DB_ID", NOTION_DB_ID_DEFAULT)
    if not token or not db_id:
        print("Notion: NOTION_TOKEN or NOTION_DB_ID not set — skipping")
        return

    hdr = _notion_headers()

    # Query all existing pages to find item IDs
    existing: dict[str, str] = {}
    cursor = None
    while True:
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        try:
            r = requests.post(
                f"https://api.notion.com/v1/databases/{db_id}/query",
                headers=hdr, json=payload, timeout=30,
            )
            r.raise_for_status()
            resp = r.json()
        except Exception as e:
            print(f"Notion query failed: {e}")
            return

        for page in resp.get("results", []):
            props = page.get("properties", {})
            rt = props.get("ItemID", {}).get("rich_text", [])
            if rt:
                existing[rt[0]["text"]["content"]] = page["id"]

        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    updated = created = failed = 0
    for item in data["items"]:
        props = _item_to_notion_props(item, data["updated_iso"])
        try:
            if item["id"] in existing:
                r = requests.patch(
                    f"https://api.notion.com/v1/pages/{existing[item['id']]}",
                    headers=hdr, json={"properties": props}, timeout=30,
                )
                r.raise_for_status()
                updated += 1
            else:
                r = requests.post(
                    "https://api.notion.com/v1/pages",
                    headers=hdr,
                    json={"parent": {"database_id": db_id}, "properties": props},
                    timeout=30,
                )
                r.raise_for_status()
                created += 1
        except Exception as e:
            print(f"  Notion sync failed for {item['name']}: {e}")
            failed += 1

    print(f"Notion: updated={updated} created={created} failed={failed}")


def append_notion_changelog(date_str: str, movers: list[dict]) -> None:
    """Prepend a dated change block to the Notion changelog page."""
    token = os.environ.get("NOTION_TOKEN")
    page_id = NOTION_CHANGELOG_PAGE_ID
    if not token or not page_id:
        print("Notion changelog: NOTION_TOKEN or NOTION_CHANGELOG_PAGE_ID not set — skipping")
        return

    hdr = _notion_headers()
    if not movers:
        summary = "No significant price changes today."
    else:
        lines = []
        for m in movers:
            arrow = "⬆️" if m["change_pct"] > 0 else "⬇️"
            lines.append(
                f"{arrow} **{m['name']}**: ₹{m['prev']} → ₹{m['now']} "
                f"({m['change_pct']:+.1f}%)"
            )
        summary = "\n".join(lines)

    children = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": date_str}}]},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": summary}}]
            },
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]

    try:
        # Prepend by getting current children first, then rebuilding
        # Simpler: just append (Notion doesn't support prepend via API)
        r = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=hdr,
            json={"children": children},
            timeout=30,
        )
        r.raise_for_status()
        print(f"Notion changelog: appended {len(movers)} movers for {date_str}")
    except Exception as e:
        print(f"Notion changelog append failed: {e}")


# ── History tracking ──────────────────────────────────────────────────────────

def load_history() -> list[dict]:
    if HISTORY_JSON.exists():
        try:
            return json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def append_history(date_str: str, item_map: dict) -> None:
    history = load_history()
    snapshot = {
        "date": date_str,
        "prices": {iid: item["wholesale"] for iid, item in item_map.items()},
    }
    # Avoid duplicate date entries
    history = [h for h in history if h["date"] != date_str]
    history.append(snapshot)
    # Keep last 365 days
    history = history[-365:]
    HISTORY_JSON.write_text(json.dumps(history, ensure_ascii=False, separators=(",", ":")),
                            encoding="utf-8")
    print(f"History: saved snapshot for {date_str} ({len(snapshot['prices'])} items)")


def compute_changes(item_map: dict) -> list[dict]:
    """Return items whose wholesale price changed by >= CHANGE_THRESHOLD_PCT."""
    movers = []
    for item in item_map.values():
        prev = item.get("prev_wholesale")
        now  = item["wholesale"]
        if prev and prev != now:
            pct = (now - prev) / prev * 100
            item["change_pct"] = round(pct, 1)
            item["trend"] = "up" if pct > 0 else "down"
            if abs(pct) >= CHANGE_THRESHOLD_PCT:
                movers.append({
                    "name": item["name"],
                    "prev": prev,
                    "now":  now,
                    "change_pct": round(pct, 1),
                })
        else:
            item["change_pct"] = 0.0
            item["trend"] = "same"
    movers.sort(key=lambda m: abs(m["change_pct"]), reverse=True)
    return movers


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    data = json.loads(PRICES_JSON.read_text(encoding="utf-8"))
    item_map = {item["id"]: item for item in data["items"]}

    # Save today's prices as "prev" before overwriting
    for item in item_map.values():
        item["prev_wholesale"] = item["wholesale"]

    print("Scraping prices…")
    wholesale = scrape_vfpck()
    retail    = scrape_todaypricerates()
    fallback  = scrape_vegetablemarketprice()

    sources_hit: list[str] = []

    for item_id, item in item_map.items():
        if item_id in wholesale:
            item["wholesale"] = round(wholesale[item_id])
            sources_hit.append("VFPCK")

        retail_price = retail.get(item_id) or fallback.get(item_id)
        if retail_price:
            item["retail_low"]  = round(retail_price * 1.05)
            item["retail_high"] = round(retail_price * 1.25)
            item["avoid_above"] = round(retail_price * 1.55)
            sources_hit.append("todaypricerates" if item_id in retail else "vegetablemarketprice")

    now = datetime.now(IST)
    date_str = now.strftime("%Y-%m-%d")
    data["updated"]     = now.strftime("%-d %b %Y")
    data["updated_iso"] = now.isoformat()
    data["sources"]     = sorted(set(sources_hit)) if sources_hit else data["sources"]

    movers = compute_changes(item_map)
    print(f"Significant movers (≥{CHANGE_THRESHOLD_PCT}%): {len(movers)}")
    for m in movers:
        print(f"  {m['name']}: ₹{m['prev']} → ₹{m['now']} ({m['change_pct']:+.1f}%)")

    PRICES_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {PRICES_JSON}")

    append_history(date_str, item_map)
    sync_to_notion(data)
    append_notion_changelog(data["updated"], movers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
