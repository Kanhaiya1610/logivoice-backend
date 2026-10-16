"""Optional QR and barcode extraction helpers.

The image decoder and the PDF renderer are deliberately imported inside the
functions that use them.  Barcode support is optional, so an unavailable
Python package, system library, or malformed document must not interrupt the
normal document-processing flow.
"""

from __future__ import annotations

import io
import os
from typing import Any, Callable


_MISSING = object()


def _load_decoder() -> Callable[[Any], Any] | None:
    """Load pyzbar's decoder, including its optional system dependency."""

    try:
        from pyzbar.pyzbar import decode
    except Exception:
        return None
    return decode


def _load_fitz() -> Any | None:
    """Load PyMuPDF only when PDF decoding is requested."""

    try:
        import fitz
    except Exception:
        return None
    return fitz


def _as_text(value: Any) -> str:
    """Return a JSON-safe string, replacing invalid UTF-8 bytes."""

    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _record(symbol: Any, page: int | object = _MISSING) -> dict[str, Any] | None:
    try:
        record: dict[str, Any] = {
            "type": _as_text(symbol.type),
            "data": _as_text(symbol.data),
        }
    except Exception:
        return None

    if page is not _MISSING:
        record["page"] = page
    return record


def _deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove exact records while retaining the first occurrence."""

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, Any]] = set()
    for record in records:
        key = (record["type"], record["data"], record.get("page"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _decode_symbols(decoder: Callable[[Any], Any], image: Any) -> list[dict[str, Any]]:
    try:
        symbols = decoder(image)
        records = []
        for symbol in symbols:
            record = _record(symbol)
            if record is not None:
                records.append(record)
        return _deduplicate(records)
    except Exception:
        return []


def decode_image(image_or_bytes: Any) -> list[dict]:
    """Decode symbols from a PIL image object or encoded image bytes.

    Optional dependency failures and decoder errors intentionally resolve to
    an empty list so callers can continue processing the document.
    """

    decoder = _load_decoder()
    if decoder is None:
        return []

    if isinstance(image_or_bytes, (bytes, bytearray, memoryview)):
        image = None
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(bytes(image_or_bytes)))
            return _decode_symbols(decoder, image)
        except Exception:
            return []
        finally:
            close = getattr(image, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    return _decode_symbols(decoder, image_or_bytes)


def _page_pixmap(page: Any, fitz: Any) -> Any:
    """Rasterize a page, using a higher-resolution matrix when available."""

    try:
        matrix = fitz.Matrix(2, 2)
    except Exception:
        matrix = None

    if matrix is not None:
        try:
            return page.get_pixmap(matrix=matrix, alpha=False)
        except TypeError:
            # Keep the helper friendly to alternate PyMuPDF-compatible page
            # implementations that expose only get_pixmap().
            pass
    return page.get_pixmap()


def _max_pdf_pages() -> int:
    try:
        configured = int(os.getenv("MAX_PDF_PAGES", "25"))
    except ValueError:
        configured = 25
    return max(1, min(configured, 100))


def decode_pdf(content: bytes) -> list[dict]:
    """Rasterize and decode every PDF page, adding a one-based page number."""

    fitz = _load_fitz()
    if fitz is None:
        return []

    document = None
    try:
        document = fitz.open(stream=content, filetype="pdf")
        records: list[dict[str, Any]] = []
        for page_number, page in enumerate(document, start=1):
            if page_number > _max_pdf_pages():
                break
            pixmap = _page_pixmap(page, fitz)
            image_bytes = pixmap.tobytes("png")
            for decoded in decode_image(image_bytes):
                if not isinstance(decoded, dict):
                    continue
                try:
                    record = {
                        "type": _as_text(decoded["type"]),
                        "data": _as_text(decoded["data"]),
                        "page": page_number,
                    }
                except Exception:
                    continue
                records.append(record)
        return _deduplicate(records)
    except Exception:
        return []
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def barcode_dependency_status() -> dict[str, bool]:
    """Report optional barcode dependencies without exposing environment data."""

    image_decoder_available = _load_decoder() is not None
    pdf_renderer_available = _load_fitz() is not None
    return {
        "pyzbar": image_decoder_available,
        "fitz": pdf_renderer_available,
        "ready": image_decoder_available and pdf_renderer_available,
    }
