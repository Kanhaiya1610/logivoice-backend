"""Small, dependency-safe NLP helpers for logistics documents.

spaCy is intentionally optional here.  Importing this module never imports or
loads spaCy, and model loading only happens when :func:`extract_entities` is
called without an injected pipeline.  If the package or configured model is
not available, deterministic regular expressions provide a useful fallback.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Mapping


ENTITY_KEYS = (
    "people",
    "organizations",
    "locations",
    "dates",
    "emails",
    "phones",
    "urls",
)

_DEFAULT_MODEL = "en_core_web_sm"
_SPACY_LABELS = {
    "PERSON": "people",
    "ORG": "organizations",
    "GPE": "locations",
    "LOC": "locations",
    "FAC": "locations",
    "DATE": "dates",
}


def _empty_entities(source: str) -> dict[str, Any]:
    return {key: [] for key in ENTITY_KEYS} | {"source": source}


def _config_value(config: Any, key: str) -> str | None:
    if config is None:
        return None
    if isinstance(config, Mapping):
        value = config.get(key)
    else:
        getter = getattr(config, "get", None)
        value = getter(key) if callable(getter) else getattr(config, key, None)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _resolve_model_name(model_name: str | None = None, config: Any = None) -> str:
    if model_name and str(model_name).strip():
        return str(model_name).strip()

    for key in ("NLP_MODEL", "SPACY_MODEL", "NLP_MODEL_NAME", "SPACY_MODEL_NAME"):
        configured = _config_value(config, key)
        if configured:
            return configured

    for key in ("LOGIVOICE_NLP_MODEL", "SPACY_MODEL", "NLP_MODEL"):
        configured = os.getenv(key, "").strip()
        if configured:
            return configured
    return _DEFAULT_MODEL


@lru_cache(maxsize=8)
def _load_spacy_model(model_name: str):
    """Load one already-installed spaCy model, never downloading it."""

    try:
        import spacy  # type: ignore[import-not-found]
    except Exception:
        return None

    try:
        return spacy.load(model_name)
    except Exception:
        # A missing model, incompatible spaCy installation, or broken model
        # should all select the local fallback rather than fail a request.
        return None


def load_nlp(model_name: str | None = None, config: Any = None):
    """Return an installed spaCy pipeline, or ``None`` when unavailable.

    ``model_name`` takes precedence over configuration.  Configuration may be
    a Flask-like mapping/object containing ``NLP_MODEL`` or ``SPACY_MODEL``;
    environment variables use the same names.  No package or model download
    is attempted.
    """

    return _load_spacy_model(_resolve_model_name(model_name, config))


def _clean_value(value: Any) -> str:
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value.strip(" \t\r\n,;:.")


def _identity(bucket: str, value: str) -> str:
    if bucket == "phones":
        digits = re.sub(r"\D", "", value)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return digits or value.casefold()
    return value.casefold()


def _append_unique(result: dict[str, Any], bucket: str, value: Any) -> None:
    cleaned = _clean_value(value)
    if not cleaned:
        return
    marker = _identity(bucket, cleaned)
    if not marker:
        return
    if any(_identity(bucket, existing) == marker for existing in result[bucket]):
        return
    result[bucket].append(cleaned)


_MONTHS = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?"
)
_DATE_RE = re.compile(
    rf"\b(?:"
    rf"\d{{4}}[-/.]\d{{1,2}}[-/.]\d{{1,2}}|"
    rf"(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,\s*|\s+)\d{{4}}|"
    rf"\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}}|"
    rf"\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}"
    rf")\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+(?![\w.-])",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?i)(?<![\w@])(?:https?://|www\.)[^\s<>\"']+")
_PHONE_RE = re.compile(
    r"(?x)(?<![\w])(?:"
    r"\+?\d{1,3}[\s.-]?"
    r")?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{3,4}"
    r"(?:\s*(?:ext\.?|x)\s*\d{1,5})?(?![\w])|"
    r"(?<![\w])\+?\d{10,15}(?![\w])"
)

_ORGANIZATION_LABELS = (
    "company",
    "organization",
    "organisation",
    "vendor",
    "supplier",
    "carrier",
    "customer",
    "client",
    "bill to",
)
_LOCATION_LABELS = (
    "city",
    "country",
    "port",
    "warehouse",
    "location",
)
_ALL_LABELS = tuple(dict.fromkeys(_ORGANIZATION_LABELS + _LOCATION_LABELS))
_LABEL_ALTERNATION = "|".join(
    re.escape(label) for label in sorted(_ALL_LABELS, key=len, reverse=True)
)
_LABEL_RE = re.compile(
    rf"(?<![\w])(?P<label>{_LABEL_ALTERNATION})[ \t]*(?:[:=]|-)[ \t]*"
    rf"(?P<value>[^\r\n;]+)",
    re.IGNORECASE,
)
_NEXT_LABEL_RE = re.compile(
    rf"[ \t,]+(?=(?:{_LABEL_ALTERNATION})[ \t]*(?:[:=]|-))",
    re.IGNORECASE,
)
_JUNK_LABEL_VALUES = {"-", "--", "n/a", "na", "none", "null", "unknown"}


def _label_value(value: str) -> str:
    next_label = _NEXT_LABEL_RE.search(value)
    if next_label:
        value = value[: next_label.start()]
    return _clean_value(value)


def _is_usable_label_value(value: str) -> bool:
    return bool(value and value.casefold() not in _JUNK_LABEL_VALUES and re.search(r"\w", value))


def _regex_entities(text: str) -> dict[str, Any]:
    result = _empty_entities("regex")

    for match in _DATE_RE.finditer(text):
        _append_unique(result, "dates", match.group(0))
    for match in _EMAIL_RE.finditer(text):
        _append_unique(result, "emails", match.group(0))
    for match in _URL_RE.finditer(text):
        _append_unique(result, "urls", match.group(0).rstrip(".,;:!?)]}"))
    for match in _PHONE_RE.finditer(text):
        _append_unique(result, "phones", match.group(0).strip())

    organization_labels = {label.casefold() for label in _ORGANIZATION_LABELS}
    for match in _LABEL_RE.finditer(text):
        value = _label_value(match.group("value"))
        if not _is_usable_label_value(value):
            continue
        bucket = "organizations" if match.group("label").casefold() in organization_labels else "locations"
        _append_unique(result, bucket, value)

    return result


def _spacy_entities(text: str, nlp: Any) -> dict[str, Any] | None:
    try:
        document = nlp(text)
        entities = getattr(document, "ents", ())
        result = _empty_entities("spacy")
        for entity in entities:
            label = str(getattr(entity, "label_", getattr(entity, "label", ""))).upper()
            bucket = _SPACY_LABELS.get(label)
            if bucket:
                _append_unique(result, bucket, getattr(entity, "text", ""))
        return result
    except Exception:
        return None


def extract_entities(text: str, nlp: Any = None) -> dict[str, Any]:
    """Extract common document entities using spaCy or a regex fallback.

    Passing an ``nlp`` callable is useful for applications that already have a
    pipeline and for tests.  When it is omitted, the configured installed model
    is loaded lazily.  The returned values are plain strings/lists and are safe
    to serialize as JSON.
    """

    text = "" if text is None else str(text)
    if nlp is None:
        nlp = load_nlp()
    if nlp is not None:
        extracted = _spacy_entities(text, nlp)
        if extracted is not None:
            regex_entities = _regex_entities(text)
            added_regex_value = False
            for bucket in ENTITY_KEYS:
                before = len(extracted[bucket])
                for value in regex_entities[bucket]:
                    _append_unique(extracted, bucket, value)
                added_regex_value = added_regex_value or len(extracted[bucket]) > before
            if added_regex_value:
                extracted["source"] = "spacy+regex"
            return extracted
    return _regex_entities(text)


_CLASSIFICATION_RULES = {
    "Invoice": (
        (r"\binvoice\b", 6),
        (r"invoice\s*(?:number|no\.?|#)", 2),
        (r"amount\s+due|balance\s+due", 3),
        (r"subtotal", 1),
        (r"tax", 1),
        (r"payment\s+terms", 2),
        (r"due\s+date", 1),
        (r"\bbill\s+to\b", 2),
    ),
    "Delivery Note": (
        (r"delivery\s+(?:note|docket)", 7),
        (r"\bwaybill\b", 5),
        (r"\bconsignee\b", 3),
        (r"received\s+by|delivered\s+to", 2),
        (r"\bdispatch(?:ed)?\b", 2),
        (r"shipment|proof\s+of\s+delivery", 2),
    ),
    "Packing Slip": (
        (r"packing\s+(?:slip|list)", 7),
        (r"\bcontents\b", 2),
        (r"\bcarton(?:s)?\b|\bpackage(?:s)?\b", 2),
        (r"\b(?:qty|quantity|pieces)\b", 1),
        (r"\bsku\b|item\s+description", 1),
    ),
    "Purchase Order": (
        (r"purchase\s+order", 7),
        (r"\bp\.?o\.?\s*(?:number|no\.?|#)", 4),
        (r"\brequisition\b", 3),
        (r"unit\s+price", 1),
        (r"requested\s+delivery", 2),
        (r"\bauthorized\b", 1),
    ),
    "Resume/CV": (
        (r"\bresume\b", 7),
        (r"curriculum\s+vitae", 8),
        (r"\bc\.?v\.?\b", 6),
        (r"professional\s+(?:summary|experience)", 3),
        (r"work\s+experience|employment\s+history", 4),
        (r"\beducation\b", 2),
        (r"\bskills\b", 2),
        (r"\breferences\b", 1),
    ),
    "Receipt": (
        (r"\breceipt\b", 7),
        (r"payment\s+received", 3),
        (r"\btransaction\b", 2),
        (r"\bpaid\b", 2),
        (r"change\s+due", 3),
        (r"\bmerchant\b", 2),
        (r"\b(?:cash|credit\s+card)\b", 1),
    ),
}
_FILENAME_HINTS = {
    "Invoice": r"invoice|bill",
    "Delivery Note": r"delivery|dispatch|waybill",
    "Packing Slip": r"packing|packing[-_ ]?list",
    "Purchase Order": r"purchase[-_ ]?order|(?:^|[-_ .])po(?:[-_ .]|$)",
    "Resume/CV": r"resume|curriculum[-_ ]?vitae|(?:^|[-_ .])cv(?:[-_ .]|$)",
    "Receipt": r"receipt|payment",
}


def _classification_scores(text: str) -> dict[str, float]:
    return {
        label: sum(weight for pattern, weight in rules if re.search(pattern, text, re.IGNORECASE))
        for label, rules in _CLASSIFICATION_RULES.items()
    }


def classify_document(text: str, filename: str = "") -> tuple[str, float]:
    """Classify a document with content evidence taking precedence.

    A filename contributes only a small hint and cannot classify an otherwise
    unsupported or nearly empty document.  Confidence is a deterministic score
    in the inclusive ``0.0`` to ``0.99`` range.
    """

    content = "" if text is None else str(text).lower()
    content = re.sub(r"\s+", " ", content).strip()
    content_scores = _classification_scores(content)
    content_order = list(_CLASSIFICATION_RULES)
    content_label = max(content_order, key=lambda label: content_scores[label])
    content_score = content_scores[content_label]

    totals = dict(content_scores)
    lowered_filename = str(filename or "").lower().replace("\\", "/").rsplit("/", 1)[-1]
    for label, pattern in _FILENAME_HINTS.items():
        if re.search(pattern, lowered_filename, re.IGNORECASE):
            totals[label] += 0.5

    ordered = sorted(totals.items(), key=lambda item: (-item[1], content_order.index(item[0])))
    best_label, best_score = ordered[0]
    second_score = ordered[1][1]

    # Generic words such as "total", "skills", or a filename by itself are
    # intentionally insufficient evidence for a document type.
    if content_score < 3:
        return "Unknown", round(min(0.49, content_score / 6), 4)

    margin = max(0.0, best_score - second_score)
    strength = min(1.0, content_score / 8)
    separation = margin / (best_score + 1.0)
    confidence = 0.55 + (0.4 * strength * separation)
    if best_label != content_label:
        confidence -= 0.05
    return best_label, round(max(0.0, min(0.99, confidence)), 4)


__all__ = ["classify_document", "extract_entities", "load_nlp"]
