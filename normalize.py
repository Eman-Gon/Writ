"""
StateGuard — normalization contract.

CONTRACT FILE. Owned by neither workstream. Do not fork, do not reimplement,
do not "improve" locally. If it needs to change, change it here once.

WHY THIS EXISTS
---------------
Evidence verification is:

    normalize_field(proposed_value, kind) == normalize_field(raw_text[start:end], kind)

The connector produces `raw_text`. The gateway produces the comparison. If the
two sides normalize even slightly differently -- one whitespace rule, one
Unicode form -- every verification fails, and it presents as a logic bug in the
gateway when it is actually a contract gap. Both sides import from here.

THE TWO-STAGE RULE (this is the part that is easy to get wrong)
--------------------------------------------------------------
Stage 1 -- `normalize_text()`. The connector applies this ONCE at ingestion and
stores the result as `SourceEnvelope.raw_text`. Spans index into THAT string,
not into the original bytes. Never re-run it later; span offsets would shift.

Stage 2 -- `normalize_field()`. The gateway applies this at comparison time to
BOTH the proposed value AND the span slice. It is field-specific (an IBAN
strips spaces; an amount strips currency symbols), so it can never be applied
to `raw_text` -- doing so would destroy span alignment.

    connector:  raw_bytes -> extract -> normalize_text() -> raw_text  [spans index here]
    gateway:    normalize_field(value) == normalize_field(raw_text[a:b])
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Literal

FieldKind = Literal["iban", "amount", "date", "text"]


# ---------------------------------------------------------------------------
# Field-kind registry.
#
# Keyed by canonical_field. A canonical field with no entry raises -- it does
# NOT default to "text". Same principle as an unregistered destination: a gap
# is a vulnerability, not a default. Adding a RegistryEntry means adding a line
# here too.
# ---------------------------------------------------------------------------

FIELD_KINDS: dict[str, FieldKind] = {
    "ap.invoice.amount": "amount",
    "ap.invoice.due_date": "date",
    "vendor.profile.address": "text",
    "vendor.remittance.bank_account": "iban",
    "vendor.remittance.note": "text",
    "memory.descriptive": "text",
    "memory.authoritative": "text",
}


class UnknownFieldKind(Exception):
    """Raised for a canonical field with no normalization rule. Fail closed."""


def field_kind_for(canonical_field: str) -> FieldKind:
    try:
        return FIELD_KINDS[canonical_field]
    except KeyError as e:
        raise UnknownFieldKind(
            f"No normalization rule for {canonical_field!r}. "
            "Add one to FIELD_KINDS. Do not default to 'text'."
        ) from e


# ---------------------------------------------------------------------------
# Stage 1 -- connector, once, at ingestion.
# ---------------------------------------------------------------------------

_ZERO_WIDTH_AND_FORMAT = "Cf"          # includes ZWSP, ZWNJ, RLO, BOM
_CONTROL = "Cc"
_KEEP_CONTROL = "\n\t"


def normalize_text(s: str) -> str:
    """Canonical form for `SourceEnvelope.raw_text`. Spans index into this.

    - NFKC (folds compatibility forms: fullwidth digits, ligatures, etc.)
    - strips zero-width and format characters (the invisible-payload trick)
    - strips control characters except newline/tab
    - collapses every whitespace run -- including newlines -- to one space

    Collapsing newlines keeps offsets stable across PDF extractors that
    disagree about line breaks. For a demo this is the right trade.
    """
    s = unicodedata.normalize("NFKC", s)
    s = "".join(
        ch
        for ch in s
        if unicodedata.category(ch) != _ZERO_WIDTH_AND_FORMAT
        and (unicodedata.category(ch) != _CONTROL or ch in _KEEP_CONTROL)
    )
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Ambiguity detectors -- connector sets parse_paths_agree=False / AMBIGUOUS.
# ---------------------------------------------------------------------------


def is_normalization_ambiguous(s: str) -> bool:
    """True when NFC and NFKC disagree -- a compatibility-form trick."""
    return unicodedata.normalize("NFC", s) != unicodedata.normalize("NFKC", s)


def has_mixed_script(s: str) -> bool:
    """Crude confusables check: one token drawing letters from two scripts.

    NFKC does NOT fold homoglyphs -- Cyrillic 'а' and Latin 'a' are distinct
    codepoints that survive normalization and render identically. Any token
    mixing scripts is treated as adversarial.
    """
    for token in s.split():
        scripts: set[str] = set()
        for ch in token:
            if not ch.isalpha():
                continue
            name = unicodedata.name(ch, "")
            for script in ("CYRILLIC", "GREEK", "LATIN", "ARMENIAN"):
                if name.startswith(script):
                    scripts.add(script)
                    break
        if len(scripts) > 1:
            return True
    return False


# ---------------------------------------------------------------------------
# Stage 2 -- gateway, at comparison time, both sides.
# ---------------------------------------------------------------------------


def _normalize_iban(s: str) -> str:
    s = normalize_text(s)
    s = re.sub(r"[^0-9A-Za-z]", "", s)
    return s.upper()


def _normalize_amount(s: str) -> str:
    """Currency-agnostic. Handles 4,200.00 / 4.200,00 / $4200 / 4 200.

    Returns a canonical Decimal string: '4200' == '4,200.00' == '4.200,00'.
    """
    s = normalize_text(s)
    s = re.sub(r"[^\d.,\-]", "", s)
    if not s:
        return ""

    if "," in s and "." in s:
        # Whichever separator comes last is the decimal point.
        if s.rindex(",") > s.rindex("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        # Exactly two trailing digits after a single comma -> decimal comma.
        if len(parts) == 2 and len(parts[1]) == 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")

    try:
        return format(Decimal(s).normalize(), "f")
    except InvalidOperation:
        return ""


_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
    "%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y",
)


def _normalize_date(s: str) -> str:
    """ISO 8601 or empty. Empty is a verification failure, which is correct --
    an unparseable date on a high-impact field should fail closed."""
    from datetime import datetime

    s = normalize_text(s)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _normalize_plain_text(s: str) -> str:
    return normalize_text(s).casefold()


def normalize_field(value: str, kind: FieldKind) -> str:
    """Apply to BOTH the proposed value and the span slice. Never to raw_text."""
    if kind == "iban":
        return _normalize_iban(value)
    if kind == "amount":
        return _normalize_amount(value)
    if kind == "date":
        return _normalize_date(value)
    if kind == "text":
        return _normalize_plain_text(value)
    raise UnknownFieldKind(f"Unhandled field kind: {kind!r}")


# ---------------------------------------------------------------------------
# Collision detection -- a predicted failure mode, so measure it.
#
# Two DISTINCT source strings that normalize to the same protected value are a
# real attack surface (an attacker crafting a benign-looking string that
# normalizes onto a target IBAN). Log these; do not silently accept.
# ---------------------------------------------------------------------------


def collides(a: str, b: str, kind: FieldKind) -> bool:
    """True when two different raw strings normalize identically."""
    return a != b and normalize_field(a, kind) == normalize_field(b, kind)
