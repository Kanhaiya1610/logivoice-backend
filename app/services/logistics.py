"""Conservative, segment-local logistics extraction using only explicit evidence.

Numbers accept signed integers, decimal dots/commas, and consistently grouped
US/European forms (1,234.50 / 1.234,50). A lone separator followed by exactly
three digits is ambiguous and rejected; repeated three-digit groups are allowed.
Spaces, exponents, percentages and nonfinite values are not numbers here. Decimal
precision is retained, without rounding or computing missing invoice values.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


_MAX_ITEMS = 200
_MAX_HEADER_LENGTH = 512
_MAX_LINE_LENGTH = 2048

# Recognized ISO 4217 codes, rather than arbitrary three-letter words. Evidence
# must be a Currency label or an affix on a recognized monetary value.
_CURRENCIES = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB
BRL BSD BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP
DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF
IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT
LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN
MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD
RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS
TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VED VES VND VUV WST XAF
XCD XCG XOF XPF YER ZAR ZMW ZWG
""".split())
_CURRENCY_TOKEN = re.compile(r"US\$|CA\$|C\$|AU\$|A\$|NZ\$|[A-Za-z]{3}|[$€£¥]", re.I)
_SYMBOL_CODES = {
    "US$": "USD", "CA$": "CAD", "C$": "CAD", "AU$": "AUD",
    "A$": "AUD", "NZ$": "NZD", "€": "EUR", "£": "GBP",
}

_AMOUNT_LABEL = re.compile(
    r"^(?P<label>sub[ -]?total|total[ \t]+tax|sales[ \t]+tax|tax(?![ \t]+id\b)|vat|"
    r"grand[ \t]+total|invoice[ \t]+total|total[ \t]+amount|amount[ \t]+due|total)"
    r"(?=[ \t:=]|$)(?P<value>.*)$", re.I,
)
_TOTAL_PRIORITY = {
    "grand total": 0, "invoice total": 1, "total amount": 2,
    "amount due": 3, "total": 4,
}
_TAX_RATE = re.compile(r"^(?:\([ \t]*[0-9]+(?:[.,][0-9]+)?[ \t]*%[ \t]*\)|"
                       r"[0-9]+(?:[.,][0-9]+)?[ \t]*%)(?=[ \t:=]|$)[ \t]*")
_REFERENCE_LABEL = re.compile(
    r"^(?P<label>purchase[ \t]+order|p\.?o\.?|tracking|awb|waybill)"
    r"(?:[ \t]+(?:number|no\.?|id))?"
    r"(?=[ \t:=#]|$)(?P<value>.*)$", re.I,
)
_CARRIER_LABEL = re.compile(r"^(?:shipping[ \t]+)?carrier(?=[ \t:=]|$)(.*)$", re.I)
_DUE_LABEL = re.compile(r"^(?:due[ \t]+date|payment[ \t]+due(?:[ \t]+date)?)"
                        r"(?=[ \t:=]|$)(.*)$", re.I)
_CURRENCY_LABEL = re.compile(r"^currency(?=[ \t:=]|$)(.*)$", re.I)
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
_DAY = r"(?:0?[1-9]|[12][0-9]|3[01])"
_MONTH = r"(?:0?[1-9]|1[0-2])"
_MONTH_NAME = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
               r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_DATE_VALUE = re.compile(
    rf"(?:[0-9]{{4}}(?P<ymd>[-/.]){_MONTH}(?P=ymd){_DAY}|"
    rf"{_DAY}(?P<dmy>[-/.]){_MONTH}(?P=dmy)(?:[0-9]{{4}}|[0-9]{{2}})|"
    rf"{_MONTH}(?P<mdy>[-/.]){_DAY}(?P=mdy)(?:[0-9]{{4}}|[0-9]{{2}})|"
    rf"{_DAY}(?:st|nd|rd|th)?[ \t]+{_MONTH_NAME}[ \t]+[0-9]{{4}}|"
    rf"{_MONTH_NAME}[ \t]+{_DAY}(?:st|nd|rd|th)?(?:,[ \t]*|[ \t]+)[0-9]{{4}})", re.I,
)


def _payload(value: str) -> str:
    return value.strip(" \t").lstrip(":=").strip(" \t")


def _number(value: str) -> str | None:
    """Normalize a whole numeric token; never salvage a numeric substring."""
    value = value.strip()
    if len(value) > 100 or not re.fullmatch(r"[+-]?[0-9]+(?:[.,][0-9]+)*", value):
        return None
    sign = "-" if value.startswith("-") else ""
    unsigned = value.lstrip("+-")
    if "." in unsigned and "," in unsigned:
        decimal = "." if unsigned.rfind(".") > unsigned.rfind(",") else ","
        grouping = "," if decimal == "." else "."
        integer, fraction = unsigned.rsplit(decimal, 1)
        if not re.fullmatch(rf"[0-9]{{1,3}}(?:{re.escape(grouping)}[0-9]{{3}})+", integer):
            return None
        if not fraction.isascii() or not fraction.isdigit():
            return None
        normalized = integer.replace(grouping, "") + "." + fraction
    elif "." in unsigned or "," in unsigned:
        separator = "." if "." in unsigned else ","
        parts = unsigned.split(separator)
        if len(parts) == 2:
            if len(parts[1]) == 3:
                return None
            normalized = ".".join(parts)
        else:
            if not (1 <= len(parts[0]) <= 3 and all(len(part) == 3 for part in parts[1:])):
                return None
            normalized = "".join(parts)
    else:
        normalized = unsigned
    try:
        number = Decimal(sign + normalized)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return format(number.copy_abs() if number.is_zero() else number, "f")


def _money(value: str) -> tuple[str | None, set[str]]:
    """Allow currency affixes only, keeping ambiguous $/yen symbols uncoded."""
    value = value.strip()
    codes = set()
    affixes = set()
    for leading in (True, False):
        while value:
            matches = list(_CURRENCY_TOKEN.finditer(value))
            match = (matches[0] if leading else matches[-1]) if matches else None
            if match is None or (match.start() != 0 if leading else match.end() != len(value)):
                break
            token = match.group().upper()
            code = _SYMBOL_CODES.get(token)
            if code is None and token in _CURRENCIES:
                code = token
            if code is None and token not in {"$", "¥"}:
                return None, codes
            kind = "code" if token in _CURRENCIES else "symbol"
            if kind in affixes:
                return None, codes
            affixes.add(kind)
            if code:
                codes.add(code)
            value = (value[match.end():] if leading else value[:match.start()]).strip()
    amount = _number(value)
    if len(codes) > 1:
        amount = None
    return amount, codes


def _unique(values: list[str | None]) -> str | None:
    return values[0] if values and values[0] is not None and len(set(values)) == 1 else None


def _preferred_amount(candidates: list[tuple[int, str | None]]) -> str | None:
    if not candidates:
        return None
    priority = min(priority for priority, _ in candidates)
    values = [value for rank, value in candidates if rank == priority]
    if any(value is None for value in values):
        return None
    # Equivalent repetitions may differ in printed precision; retain the first.
    return values[0] if len({Decimal(value) for value in values}) == 1 else None


_HEADERS = {
    "description": "description", "product": "description", "sku": "sku",
    "qty": "quantity", "quantity": "quantity", "unit": "unit",
    "unit price": "unit_price", "amount": "amount",
}
_SECTION = re.compile(r"^(?:product[ \t]+list|items|line[ \t]+items)[ \t]*:[ \t]*$", re.I)
_UNITS = (r"pcs?|pieces?|ea|each|units?|kg|g|lbs?|oz|tons?|t|l|ml|liters?|litres?|"
          r"m|cm|mm|ft|in|boxes|box|cartons?|packs?|pallets?|sets?|rolls?")
_SIMPLE_ITEM = re.compile(
    rf"^(?P<description>[^:|\t]{{1,512}}?)[ \t]+(?P<quantity>[+-]?[0-9][0-9.,]*)"
    rf"[ \t]+(?P<unit>{_UNITS})$", re.I,
)
_OTHER_LABEL = re.compile(
    r"^(?:[A-Za-z][A-Za-z .()/#-]{0,60}[:=]|"
    r"(?:invoice|customer|bill[ \t]+to|ship[ \t]+to|payment[ \t]+terms|notes?)\b)", re.I,
)


def _boundary(line: str) -> bool:
    return any(pattern.match(line) for pattern in (
        _AMOUNT_LABEL, _REFERENCE_LABEL, _CARRIER_LABEL, _DUE_LABEL,
        _CURRENCY_LABEL, _OTHER_LABEL,
    ))


def _cells(line: str, layout: tuple[str, bool, bool]) -> list[str] | None:
    delimiter, left, right = layout
    line = line.strip(" ")
    if delimiter == "|":
        if left:
            if not line.startswith("|"):
                return None
            line = line[1:]
        if right:
            if not line.endswith("|"):
                return None
            line = line[:-1]
    if delimiter == "spaces":
        return re.split(r" {2,}", line.strip())
    return [cell.strip() for cell in line.split(delimiter)]


def _table_header(line: str) -> tuple[tuple[str, bool, bool], list[str]] | None:
    if len(line) > _MAX_HEADER_LENGTH:
        return None
    stripped = line.strip(" ")
    if "|" in line:
        layout = ("|", stripped.startswith("|"), stripped.endswith("|"))
    elif "\t" in line:
        layout = ("\t", False, False)
    elif re.search(r" {2,}", line.strip()):
        layout = ("spaces", False, False)
    else:
        return None
    cells = _cells(line, layout)
    headers = [_HEADERS.get(re.sub(r"[ \t]+", " ", cell).casefold()) for cell in cells]
    if (len(headers) < 2 or None in headers or "description" not in headers
            or len(set(headers)) != len(headers)):
        return None
    return layout, headers


def _table_item(cells: list[str], headers: list[str]) -> tuple[dict | None, set[str]]:
    if len(cells) != len(headers):
        return None, set()
    row = dict(zip(headers, cells))
    description = row["description"].strip()
    if not description or len(description) > 512 or not any(char.isalpha() for char in description):
        return None, set()
    item = {"description": description, "sku": None, "quantity": None,
            "unit": None, "unit_price": None, "amount": None}
    codes = set()
    for key in ("sku", "quantity", "unit", "unit_price", "amount"):
        value = row.get(key, "").strip()
        if not value:
            continue
        if key == "sku":
            parsed = value if _IDENTIFIER.fullmatch(value) else None
        elif key == "unit":
            parsed = value if re.fullmatch(_UNITS, value, flags=re.I) else None
        elif key == "quantity":
            parsed = _number(value)
        else:
            parsed, evidence = _money(value)
            codes.update(evidence)
        if parsed is None:
            return None, set()
        item[key] = parsed
    return item, codes


def _line_items(lines: list[str]) -> tuple[list[dict], set[str]]:
    items = []
    codes = set()
    table = None
    simple = False
    for raw in lines:
        if len(items) >= _MAX_ITEMS:
            break
        line = raw.strip()
        if not line or len(raw) > _MAX_LINE_LENGTH:
            table, simple = None, False
            continue
        header = _table_header(raw)
        if header:
            table, simple = header, False
            continue
        if _SECTION.fullmatch(line):
            table, simple = None, True
            continue
        if _boundary(line):
            table, simple = None, False
            continue
        if table:
            if re.fullmatch(r"[|: +\-\t]+", line):
                continue  # Optional Markdown-style table rule.
            layout, headers = table
            cells = _cells(raw, layout)
            if cells is None or len(cells) != len(headers):
                table = None
                continue
            item, evidence = _table_item(cells, headers)
            if item:
                items.append(item)
                codes.update(evidence)
        elif simple:
            match = _SIMPLE_ITEM.fullmatch(line.lstrip("-*• "))
            quantity = _number(match["quantity"]) if match else None
            if match and quantity is not None and any(char.isalpha() for char in match["description"]):
                items.append({
                    "description": match["description"].strip(), "sku": None,
                    "quantity": quantity, "unit": match["unit"],
                    "unit_price": None, "amount": None,
                })
            else:
                simple = False
    return items, codes


def extract_logistics_fields(text: str) -> dict:
    """Return additive flat fields for exactly the supplied logical segment.

    Total priority: grand total > invoice total > total amount > amount due >
    total. Total tax outranks tax/sales tax/VAT; equal-priority conflicting or
    invalid values yield null, without fallback or arithmetic. Only explicitly
    labeled numeric/English-month due dates are retained verbatim. Currency
    evidence is limited to Currency labels and valid monetary fields/cells;
    conflicting ISO codes yield null, and bare $/yen never identify a currency.

    Items require an explicit list section or a fully recognized pipe/tab/2+
    space header. Invalid numeric cells reject the row; missing cells stay null.
    Blank lines, labels and lost column alignment end a section. At most 200
    items are returned; headers over 512 and rows over 2048 characters are ignored.
    """
    result = dict.fromkeys((
        "subtotal", "tax", "total", "currency", "due_date",
        "purchase_order_number", "tracking_number", "carrier",
    ))
    result["line_items"] = []
    if not isinstance(text, str) or not text:
        return result
    lines = text.splitlines()
    amounts = {key: [] for key in ("subtotal", "tax", "total")}
    references = {key: [] for key in ("due_date", "purchase_order_number", "tracking_number", "carrier")}
    codes = set()
    for raw in lines:
        if len(raw) > _MAX_LINE_LENGTH:
            continue
        line = raw.strip()
        match = _AMOUNT_LABEL.match(line)
        if match:
            label = re.sub(r"[ \t]+", " ", match["label"].lower())
            value = _payload(match["value"])
            if label in _TOTAL_PRIORITY:
                key, priority = "total", _TOTAL_PRIORITY[label]
            elif label.replace(" ", "").replace("-", "") == "subtotal":
                key, priority = "subtotal", 0
            else:
                key, priority = "tax", 0 if label == "total tax" else 1
                value = _payload(_TAX_RATE.sub("", value, count=1))
                # A rate without a separate amount remains absent.
            amount, evidence = _money(value)
            amounts[key].append((priority, amount))
            if amount is not None:
                codes.update(evidence)
            continue
        match = _CURRENCY_LABEL.match(line)
        if match:
            value = _payload(match[1])
            _, evidence = _money(value)
            if re.fullmatch(r"[A-Za-z]{3}|US\$|CA\$|C\$|AU\$|A\$|NZ\$|[$€£¥]", value, re.I):
                codes.update(evidence)
            continue
        match = _REFERENCE_LABEL.match(line)
        if match:
            label = match["label"].lower()
            key = "purchase_order_number" if label.startswith("p") else "tracking_number"
            value = _payload(match["value"].lstrip(" \t#:=")).strip()
            references[key].append(value if _IDENTIFIER.fullmatch(value) else None)
            continue
        match = _DUE_LABEL.match(line)
        if match:
            value = _payload(match[1])
            references["due_date"].append(value if _DATE_VALUE.fullmatch(value) else None)
            continue
        match = _CARRIER_LABEL.match(line)
        if match:
            value = _payload(match[1])
            references["carrier"].append(
                value if value and len(value) <= 128 and any(char.isalpha() for char in value)
                and not _boundary(value) and not re.search(r"[:=|\t]", value) else None
            )
    result.update({key: _preferred_amount(values) for key, values in amounts.items()})
    result.update({key: _unique(values) for key, values in references.items()})
    result["line_items"], item_codes = _line_items(lines)
    codes.update(item_codes)
    result["currency"] = next(iter(codes)) if len(codes) == 1 else None
    return result
