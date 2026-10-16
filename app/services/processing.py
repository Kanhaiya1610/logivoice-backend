"""Synchronous extraction, deterministic classification, and field parsing."""

from __future__ import annotations

import io
import os
import re
from datetime import datetime, timezone

from .barcode import decode_image, decode_pdf
from .nlp import classify_document, extract_entities


class ProcessingError(Exception):
    """The selected document processor is unavailable or failed."""


def _configure_tesseract(pytesseract) -> None:
    configured_path = os.getenv("TESSERACT_PATH", "").strip()
    if configured_path:
        pytesseract.pytesseract.tesseract_cmd = configured_path


EXTENSION_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
}


def validate_and_read(upload, max_bytes: int) -> tuple[str, str, bytes]:
    """Validate extension, declared MIME, and file signature/content."""

    from pathlib import PurePath
    from werkzeug.utils import secure_filename

    safe_name = secure_filename(upload.filename or "")
    if not safe_name:
        raise ValueError("Each uploaded file must have a filename")
    suffix = PurePath(safe_name).suffix.lower()
    if suffix not in EXTENSION_MIME:
        raise ValueError("Supported file types are PDF, PNG, JPG/JPEG, and TXT")

    declared = (upload.mimetype or "").lower()
    expected = EXTENSION_MIME[suffix]
    compatible_mimes = {expected, "application/octet-stream", ""}
    if suffix in {".jpg", ".jpeg"}:
        compatible_mimes.add("image/jpg")
    if declared not in compatible_mimes:
        raise ValueError(f"The MIME type does not match {suffix}")

    content = upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise OverflowError("The file exceeds the configured per-file size limit")
    if not content:
        raise ValueError("Empty files are not supported")

    if suffix == ".pdf" and not content.startswith(b"%PDF"):
        raise ValueError("The uploaded file is not a valid PDF")
    if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The uploaded file is not a valid PNG")
    if suffix in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("The uploaded file is not a valid JPEG")
    if suffix == ".txt":
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("TXT files must be UTF-8 encoded") from error

    if suffix in {".png", ".jpg", ".jpeg"}:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(content)) as image:
                image.verify()
        except ImportError as error:  # pragma: no cover - Pillow is required in deployments
            raise ProcessingError("Image validation is unavailable") from error
        except Exception as error:
            raise ValueError("The uploaded image could not be decoded") from error

    return safe_name, expected, content


def extract_text(content: bytes, suffix: str) -> str:
    if suffix == ".txt":
        return content.decode("utf-8-sig")

    if suffix == ".pdf":
        extracted = []
        attempted = False
        try:
            import pdfplumber

            attempted = True
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                extracted = [(page.extract_text() or "") for page in pdf.pages]
        except ImportError:
            pass
        except Exception:
            extracted = []

        text = "\n".join(part for part in extracted if part).strip()
        if text:
            return text

        try:
            import fitz

            attempted = True
            document = fitz.open(stream=content, filetype="pdf")
            try:
                text = "\n".join(page.get_text() for page in document).strip()
            finally:
                document.close()
            if text:
                return text
        except ImportError:
            fitz = None
        except Exception as error:
            raise ProcessingError("PDF text extraction failed") from error

        if fitz is None:
            raise ProcessingError("PDF extraction requires pdfplumber or PyMuPDF")

        # A scanned PDF has no embedded text. Rasterize its pages and use the
        # same OCR engine as image uploads instead of returning a false success.
        try:
            import pytesseract
            from PIL import Image

            _configure_tesseract(pytesseract)
            document = fitz.open(stream=content, filetype="pdf")
            try:
                pages = []
                for page in document:
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    with Image.open(io.BytesIO(pixmap.tobytes("png"))) as image:
                        pages.append(pytesseract.image_to_string(image, lang="eng"))
            finally:
                document.close()
            return "\n".join(page.strip() for page in pages if page.strip()).strip()
        except ImportError as error:
            raise ProcessingError("Scanned PDF OCR requires pytesseract and Pillow") from error
        except Exception as error:
            raise ProcessingError("Scanned PDF OCR is unavailable; install the Tesseract binary") from error

    if suffix in {".png", ".jpg", ".jpeg"}:
        try:
            import pytesseract
            from PIL import Image

            _configure_tesseract(pytesseract)
            with Image.open(io.BytesIO(content)) as image:
                return pytesseract.image_to_string(image, lang="eng").strip()
        except ImportError as error:
            raise ProcessingError("Image OCR requires pytesseract and Pillow") from error
        except Exception as error:
            raise ProcessingError("Image OCR is unavailable; install the Tesseract binary") from error

    raise ProcessingError("No processor is available for this file type")


CLASSIFICATION_KEYWORDS = {
    "Invoice": {
        "invoice": 3,
        "bill": 1,
        "total": 1,
        "subtotal": 1,
        "tax": 1,
        "amount due": 2,
        "payment terms": 1,
    },
    "Delivery Note": {
        "delivery note": 3,
        "delivered": 1,
        "consignee": 2,
        "received by": 2,
        "dispatch": 1,
        "waybill": 2,
    },
    "Packing Slip": {
        "packing slip": 3,
        "package": 1,
        "qty": 1,
        "quantity": 1,
        "pieces": 1,
        "contents": 1,
    },
    "Purchase Order": {
        "purchase order": 3,
        "po number": 2,
        "vendor": 1,
        "requisition": 1,
        "unit price": 1,
        "authorized": 1,
    },
}


def classify_text(text: str) -> tuple[str, float]:
    """Backward-compatible wrapper around the broader content classifier."""

    return classify_document(text)


def _first_match(patterns: list[str], text: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            value = match.group(1).strip(" \t:.-,#")
            if value:
                return value.splitlines()[0].strip()
    return None


_NON_VALUE_LABELS = {
    "address", "mobil phone", "mobile phone", "phone", "work phone", "title",
    "state", "zip", "city", "country", "notes", "customer", "organization",
}


def _usable_field_value(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.split(
        r"\s+(?=(?:title|first name|last name|email|e-mail|work phone|mobile phone|mobil phone|phone|address|state|zip|notes)\s*[:=-])",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t:.-,#")
    if not cleaned or cleaned.casefold() in _NON_VALUE_LABELS:
        return None
    return cleaned


def extract_fields(text: str, entities: dict | None = None) -> dict:
    invoice_number = _first_match(
        [
            r"(?:invoice\s*(?:number|no\.?|#)?|bill\s*(?:number|no\.?|#))\s*[:.-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})",
            r"\bINV[-\s]?([A-Z0-9/_-]{3,})\b",
        ],
        text,
    )
    customer_name = _first_match(
        [r"(?:customer|client|bill\s+to|sold\s+to)\s*[:.-]\s*([^\n]+)"],
        text,
    )
    address = _first_match(
        [r"(?:delivery\s+address|ship\s+to|address|location)\s*[:.-]\s*([^\n]+)"],
        text,
    )
    date_value = _first_match(
        [
            r"(?:invoice\s+date|delivery\s+date|order\s+date|date)\s*[:.-]\s*([^\n]+)",
            r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b",
            r"\b(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b",
        ],
        text,
    )
    entities = entities or extract_entities(text)
    address = _usable_field_value(address)
    customer_name = _usable_field_value(customer_name)
    date_value = _usable_field_value(date_value)
    return {
        "invoice_number": invoice_number,
        "customer_name": customer_name,
        "address": address,
        "date": date_value,
        "organizations": entities.get("organizations", []),
        "locations": entities.get("locations", []),
    }


def process_content(content: bytes, suffix: str, filename: str = "") -> dict:
    text = extract_text(content, suffix)
    classification, confidence = classify_document(text, filename=filename)
    entities = extract_entities(text)
    fields = extract_fields(text, entities=entities)
    fields["entities"] = entities
    if suffix == ".pdf":
        fields["barcodes"] = decode_pdf(content)
    elif suffix in {".png", ".jpg", ".jpeg"}:
        fields["barcodes"] = decode_image(content)
    return {
        "extracted_text": text,
        "extracted_fields": fields,
        "classification": classification,
        "classification_confidence": confidence,
    }


def processed_timestamp():
    return datetime.now(timezone.utc)
