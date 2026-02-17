import asyncio
import logging
from pathlib import Path

from config import TEMP_DIR

logger = logging.getLogger(__name__)


# --- macOS Vision OCR ---

def _vision_ocr_sync(image_path: str) -> str:
    """Run macOS Vision framework OCR synchronously."""
    import Quartz
    import Vision

    image_url = Quartz.CFURLCreateWithFileSystemPath(
        None, image_path, Quartz.kCFURLPOSIXPathStyle, False
    )
    ci_image = Quartz.CIImage.imageWithContentsOfURL_(image_url)
    if ci_image is None:
        raise RuntimeError(f"Could not load image: {image_path}")

    handler = Quartz.VNImageRequestHandler.alloc().initWithCIImage_options_(
        ci_image, None
    )
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    success = handler.performRequests_error_([request], None)
    if not success[0]:
        raise RuntimeError(f"Vision OCR failed: {success[1]}")

    results = request.results()
    lines = []
    for observation in results:
        candidate = observation.topCandidates_(1)
        if candidate:
            lines.append(candidate[0].string())

    return "\n".join(lines)


async def vision_ocr(image_path: str) -> str:
    """Run macOS Vision OCR in a thread executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _vision_ocr_sync, image_path)


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


# --- Dual OCR ---

async def dual_ocr(image_path: str) -> dict:
    """Run both OCR engines in parallel. Returns dict with both results.

    Each key is either the OCR text string or None if that engine failed.
    """
    results = {"vision": None, "drive": None}

    is_pdf = image_path.lower().endswith(".pdf")

    async def run_vision():
        try:
            results["vision"] = await vision_ocr(image_path)
            logger.info("Vision OCR succeeded (%d chars)", len(results["vision"]))
        except Exception as e:
            logger.warning("Vision OCR failed: %s", e)

    async def run_drive():
        try:
            results["drive"] = await drive_ocr(image_path)
            logger.info("Drive OCR succeeded (%d chars)", len(results["drive"]))
        except Exception as e:
            logger.warning("Drive OCR failed: %s", e)

    if is_pdf:
        logger.info("PDF detected — skipping Vision OCR (image-only), using Drive OCR")
        await run_drive()
    else:
        await asyncio.gather(run_vision(), run_drive())
    return results
