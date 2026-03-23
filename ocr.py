import asyncio
import logging
from pathlib import Path

from config import TEMP_DIR

logger = logging.getLogger(__name__)


# --- Google Drive OCR ---

async def drive_ocr(image_path: str) -> str:
    """Upload image to Google Drive as Doc (triggers OCR), export text, delete."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    from google_auth import get_google_creds

    creds = get_google_creds()

    loop = asyncio.get_event_loop()

    def _do_drive_ocr():
        service = build("drive", "v3", credentials=creds)

        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
        }
        ext = Path(image_path).suffix.lower()
        mime_type = mime_map.get(ext, "image/jpeg")

        file_metadata = {
            "name": f"pricewise_ocr_{Path(image_path).stem}",
            "mimeType": "application/vnd.google-apps.document",
        }
        media = MediaFileUpload(image_path, mimetype=mime_type)

        file = service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()
        file_id = file["id"]

        try:
            text = service.files().export(
                fileId=file_id, mimeType="text/plain"
            ).execute()
            if isinstance(text, bytes):
                text = text.decode("utf-8")
            return text.strip()
        finally:
            service.files().delete(fileId=file_id).execute()

    return await loop.run_in_executor(None, _do_drive_ocr)


# --- OCR ---

async def dual_ocr(image_path: str) -> dict:
    """Run Drive OCR. Returns dict with result.

    Returns {"vision": None, "drive": text_or_None}.
    """
    results = {"vision": None, "drive": None}

    try:
        results["drive"] = await drive_ocr(image_path)
        logger.info("Drive OCR succeeded (%d chars)", len(results["drive"]))
    except Exception as e:
        logger.warning("Drive OCR failed: %s", e)

    return results
