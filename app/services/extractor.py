import io
import os
import pytesseract
from PIL import Image
from pdf2image import convert_from_bytes
from typing import Tuple, Dict, Any

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.datamodel.base_models import InputFormat, DocItemLabel

from app.core.config import TESSERACT_CMD, OCR_THRESHOLD, ASSETS_DIR
from app.schemas.models import PaperMetadata
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
        
        # 1. Export Markdown
        raw_text = result.document.export_to_markdown()
        
        # 2. Visual Intelligence (Docling 2.x Upgrade - Step 54 Fix)
        image_map = {}
        os.makedirs(ASSETS_DIR, exist_ok=True)
        
        picture_count = 0
        for item, _level in result.document.iterate_items():
            if item.label == DocItemLabel.PICTURE:
                if hasattr(item, "image") and item.image:
                    try:
                        element_id = str(picture_count)
                        img_filename = f"picture_{element_id}.png"
                        img_path = os.path.join(ASSETS_DIR, img_filename)
                        
                        item.image.pil_image.save(img_path)
                        
                        element_key = f"picture-{element_id}"
                        image_map[element_key] = f"/assets/images/{img_filename}"
                        picture_count += 1
                    except Exception as e:
                        print(f"VISUAL: Failed to save picture {picture_count}: {e}")
                        logger.error(f"VISUAL: Failed to save picture {picture_count}: {e}")

        # 3. Enhance Markdown with stable references
        import re
        curr_img = 0
        def img_replacer(match):
            nonlocal curr_img
            res = f"<!-- picture-{curr_img} -->"
            curr_img += 1
            return res
        
        raw_text = re.sub(r"<!-- image -->", img_replacer, raw_text)

        # Fallback to OCR if text is suspicious
        if len(raw_text.strip()) < OCR_THRESHOLD:
            logger.warning("EXTRACTION: Docling result too short. Triggering OCR Fallback...")
            raw_text = await ocr_extract(content)
            return raw_text, "ocr", {}

        return raw_text, "docling", image_map

    except Exception as e:
        logger.error(f"EXTRACTION: Docling failed: {e}. Falling back to OCR...")
        return await ocr_extract(content), "ocr", {}
