"""Standalone community spots API server.
Run: python spots_server.py
Exposes GET /spots and POST /report on port 8090.
"""
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Load SA key from file if env var not set
if not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
    key_path = Path(__file__).parent / "sa-key.json"
    if key_path.exists():
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = key_path.read_text()

from sheets import add_spot, get_all_spots  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info(fmt % args)

    def _send(self, code, body, content_type="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path.startswith("/spots"):
            try:
                spots = get_all_spots()
                self._send(200, json.dumps(spots, ensure_ascii=False))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path.startswith("/report"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length))
                if not data.get("item") or not data.get("shop") or data.get("price") is None:
                    self._send(400, json.dumps({"error": "Missing item, shop or price"}))
                    return
                add_spot(data)
                self._send(200, json.dumps({"ok": True}))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))


if __name__ == "__main__":
    port = int(os.environ.get("SPOTS_PORT", 8090))
    server = HTTPServer(("0.0.0.0", port), Handler)
    logger.info("Spots API running on port %d", port)
    server.serve_forever()
