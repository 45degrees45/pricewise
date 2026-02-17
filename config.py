import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED_USER_IDS = [
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
]

# Google
OAUTH_CREDENTIALS_PATH = os.environ.get(
    "OAUTH_CREDENTIALS_PATH", "oauth_credentials.json"
)
OAUTH_TOKEN_PATH = os.environ.get("OAUTH_TOKEN_PATH", "token.json")
SHEETS_ID = os.environ["SHEETS_ID"]

# Anthropic (optional – falls back to Ollama when unset)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL = "claude-sonnet-4-5-20250929"

# Ollama (local fallback)
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

# Sheet structure
SHEET_NAME = "Purchases"
COLUMNS = [
    "Date",
    "Item",
    "Store",
    "Price",
    "Quantity",
    "Unit Price",
    "Card Used",
    "Cashback",
    "Confidence",
    "Notes",
]

# Thresholds
CONFIDENCE_THRESHOLD = 0.7
FUZZY_MATCH_CUTOFF = 60

# Temp directory for downloaded images
TEMP_DIR = Path("/tmp/pricewise")
TEMP_DIR.mkdir(parents=True, exist_ok=True)
