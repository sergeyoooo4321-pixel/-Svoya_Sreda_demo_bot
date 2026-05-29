from __future__ import annotations

import re


_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^0-9a-zа-яё+\s-]", re.IGNORECASE)


def normalize_text(value: str | None) -> str:
    """Normalize free-form Russian text for lightweight matching."""
    if not value:
        return ""

    text = value.strip().lower().replace("ё", "е")
    text = _NON_WORD_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def normalize_phone(value: str | None) -> str | None:
    """Return a Russian phone as +7XXXXXXXXXX, or None if it is invalid."""
    if not value:
        return None

    digits = re.sub(r"\D", "", value)
    if len(digits) == 10 and digits.startswith("9"):
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]

    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"
    return None


def extract_phone(value: str | None) -> str | None:
    """Extract and normalize the first likely Russian phone from a message."""
    if not value:
        return None

    match = re.search(
        r"(?:\+?7|8)?[\s(-]*9\d{2}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}",
        value,
    )
    return normalize_phone(match.group(0)) if match else None


def mask_phone(value: str | None) -> str:
    """Mask a phone for logs and admin summaries."""
    phone = normalize_phone(value)
    if not phone:
        return ""
    return f"{phone[:5]}******{phone[-2:]}"


def contains_any(text: str, phrases: tuple[str, ...] | list[str]) -> bool:
    """Check normalized text against normalized phrase fragments."""
    normalized = normalize_text(text)
    return any(normalize_text(phrase) in normalized for phrase in phrases)
