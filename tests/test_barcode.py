"""Isolated tests for optional barcode and QR extraction."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from app.services import barcode


def _symbol(symbol_type, data):
    return SimpleNamespace(type=symbol_type, data=data)


def _install_pyzbar(monkeypatch, decoder):
    package = ModuleType("pyzbar")
    package.__path__ = []
    module = ModuleType("pyzbar.pyzbar")
    module.decode = decoder
    package.pyzbar = module
    monkeypatch.setitem(sys.modules, "pyzbar", package)
    monkeypatch.setitem(sys.modules, "pyzbar.pyzbar", module)


def _install_pil(monkeypatch, image_factory, opened):
    package = ModuleType("PIL")
    package.__path__ = []
    module = ModuleType("PIL.Image")

    def open_image(stream):
        payload = stream.read()
        opened.append(payload)
        return image_factory(payload)

    module.open = open_image
    package.Image = module
    monkeypatch.setitem(sys.modules, "PIL", package)
    monkeypatch.setitem(sys.modules, "PIL.Image", module)


class FakeImage:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    def close(self):
        self.closed = True


def test_decode_image_converts_bytes_and_deduplicates_in_order(monkeypatch):
    opened = []
    images = []

    def image_factory(payload):
        image = FakeImage(payload)
        images.append(image)
        return image

    decoded_images = []

    def decode(image):
        decoded_images.append(image)
        return [
            _symbol("QRCODE", b"first"),
            _symbol("QRCODE", b"first"),
            _symbol("CODE128", b"first"),
            _symbol("QRCODE", b"invalid-\xff"),
        ]

    _install_pil(monkeypatch, image_factory, opened)
    _install_pyzbar(monkeypatch, decode)

    result = barcode.decode_image(b"encoded-image")

    assert opened == [b"encoded-image"]
    assert decoded_images == images
    assert images[0].closed is True
    assert result == [
        {"type": "QRCODE", "data": "first"},
        {"type": "CODE128", "data": "first"},
        {"type": "QRCODE", "data": "invalid-�"},
    ]


def test_decode_image_accepts_an_image_object_without_pillow_import(monkeypatch):
    image = object()
    _install_pyzbar(
        monkeypatch,
        lambda received: [_symbol(b"QRCODE", b"hello")]
        if received is image
        else [],
    )

    assert barcode.decode_image(image) == [{"type": "QRCODE", "data": "hello"}]


def test_decode_pdf_rasterizes_each_page_and_keeps_page_records_distinct(monkeypatch):
    opened = []
    images = []

    def image_factory(payload):
        image = FakeImage(payload)
        images.append(image)
        return image

    def decode(image):
        if image.payload == b"page-one":
            return [
                _symbol("QRCODE", b"same"),
                _symbol("QRCODE", b"same"),
                _symbol("CODE128", b"same"),
            ]
        return [_symbol("QRCODE", b"same")]

    _install_pil(monkeypatch, image_factory, opened)
    _install_pyzbar(monkeypatch, decode)

    class Pixmap:
        def __init__(self, payload):
            self.payload = payload

        def tobytes(self, image_format):
            assert image_format == "png"
            return self.payload

    class Page:
        def __init__(self, payload):
            self.payload = payload
            self.pixmap_calls = []

        def get_pixmap(self, **kwargs):
            self.pixmap_calls.append(kwargs)
            return Pixmap(self.payload)

    pages = [Page(b"page-one"), Page(b"page-two")]

    class Document:
        def __iter__(self):
            return iter(pages)

        def close(self):
            opened.append("document-closed")

    fitz = ModuleType("fitz")
    fitz.Matrix = lambda x, y: (x, y)
    fitz.open = lambda stream, filetype: Document()
    monkeypatch.setitem(sys.modules, "fitz", fitz)

    result = barcode.decode_pdf(b"pdf-content")

    assert opened == [b"page-one", b"page-two", "document-closed"]
    assert all(page.pixmap_calls == [{"matrix": (2, 2), "alpha": False}] for page in pages)
    assert result == [
        {"type": "QRCODE", "data": "same", "page": 1},
        {"type": "CODE128", "data": "same", "page": 1},
        {"type": "QRCODE", "data": "same", "page": 2},
    ]
    assert all(image.closed for image in images)


def test_missing_dependencies_and_decoder_errors_return_empty_lists(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyzbar", None)
    monkeypatch.setitem(sys.modules, "pyzbar.pyzbar", None)
    assert barcode.decode_image(object()) == []

    monkeypatch.setitem(sys.modules, "fitz", None)
    assert barcode.decode_pdf(b"pdf-content") == []

    def failing_decoder(_image):
        raise OSError("system zbar unavailable")

    _install_pyzbar(monkeypatch, failing_decoder)
    assert barcode.decode_image(object()) == []


def test_dependency_status_contains_only_safe_boolean_flags(monkeypatch):
    _install_pyzbar(monkeypatch, lambda _image: [])
    fitz = ModuleType("fitz")
    monkeypatch.setitem(sys.modules, "fitz", fitz)

    status = barcode.barcode_dependency_status()

    assert status == {"pyzbar": True, "fitz": True, "ready": True}
    assert all(isinstance(value, bool) for value in status.values())
