import hashlib
import os
import re
import pytesseract
from pdf2image import convert_from_bytes
from typing import Tuple, Dict

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.datamodel.base_models import InputFormat

from app.core.config import TESSERACT_CMD, OCR_THRESHOLD, ASSETS_DIR, API_PUBLIC_URL
from app.core.logging import logger

# Configure Tesseract
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

async def ocr_extract(content: bytes) -> str:
    """Fallback OCR for scanned documents."""
    images = convert_from_bytes(content)
    full_text = []
    for img in images:
        text = pytesseract.image_to_string(img)
        full_text.append(text)
    return "\n".join(full_text)

def get_docling_converter() -> DocumentConverter:
    """Configures Docling with table and image extraction enabled."""
    options = PdfPipelineOptions()
    options.do_ocr = True
    options.do_table_structure = True
    options.table_structure_options.mode = TableFormerMode.ACCURATE

    # Enable Visual Extraction (Step 2)
    options.generate_picture_images = True
    options.generate_table_images = True

    return DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options)
        }
    )

async def extract_text(file) -> Tuple[str, str, Dict[str, str]]:
    """
    Multimodal Extraction using Docling.
    Saves image crops to disk and returns an image_map for linking.
    """
    content = await file.read()
    converter = get_docling_converter()

    try:
        # Save temp file for Docling
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        result = converter.convert(tmp_path)
        os.unlink(tmp_path)

        image_map = {}
        os.makedirs(ASSETS_DIR, exist_ok=True)
        doc_hash = hashlib.md5(content).hexdigest()[:10]

        # 1. Export Markdown with embedded base64 images.
        #    ImageRefMode.EMBEDDED is the only Docling 2.x API that reliably
        #    surfaces image data regardless of PictureItem internals.
        try:
            from docling_core.types.doc import ImageRefMode
            raw_text = result.document.export_to_markdown(
                image_mode=ImageRefMode.EMBEDDED
            )
        except Exception:
            raw_text = result.document.export_to_markdown()

        # 2. Extract embedded base64 images → save as PNG files → replace with URLs.
        #    This keeps chunk text lean (no base64 blobs) while making images
        #    accessible via the static /assets route.
        import base64 as _b64
        pic_counter = 0

        def _save_and_link(match):
            nonlocal pic_counter
            b64_data = match.group(1).strip()
            try:
                img_bytes = _b64.b64decode(b64_data)
                img_filename = f"{doc_hash}_pic{pic_counter}.png"
                img_path = os.path.join(ASSETS_DIR, img_filename)
                with open(img_path, "wb") as fh:
                    fh.write(img_bytes)
                rel_path = f"/assets/images/{img_filename}"
                image_map[f"picture-{pic_counter}"] = rel_path
                full_url = f"{API_PUBLIC_URL}{rel_path}"
                logger.info(f"VISUAL: Saved picture-{pic_counter} → {img_filename}")
            except Exception as exc:
                logger.error(f"VISUAL: Failed to save picture-{pic_counter}: {exc}")
                full_url = ""
            finally:
                pic_counter += 1
            return f"\n![Figure]({full_url})\n" if full_url else ""

        # Matches: ![anything](data:image/TYPE;base64,DATA)
        raw_text = re.sub(
            r"!\[[^\]]*\]\(data:image/[^;]+;base64,([A-Za-z0-9+/=\s]+)\)",
            _save_and_link,
            raw_text,
        )

        logger.info(f"VISUAL: Extracted {pic_counter} picture(s) from {doc_hash}")

        # 3. Fallback to OCR if text is too short
        if len(raw_text.strip()) < OCR_THRESHOLD:
            logger.warning("EXTRACTION: Docling result too short. Triggering OCR Fallback...")
            raw_text = await ocr_extract(content)
            return raw_text, "ocr", {}

        return raw_text, "docling", image_map

    except Exception as e:
        logger.error(f"EXTRACTION: Docling failed: {e}. Falling back to OCR...")
        return await ocr_extract(content), "ocr", {}
