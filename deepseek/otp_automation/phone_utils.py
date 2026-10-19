"""Phone number normalization, validation, and masking utilities."""

from __future__ import annotations

import re
from typing import Any


def _to_text(val: Any) -> str:
    """Convert raw input value to string without scientific notation or trailing .0."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        if val.is_integer():
            return str(int(val))
        return str(val)
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    # Handle exponential notation from spreadsheet exports (e.g. 2,338E+11 or 2.338E+11)
    if ("e" in s.lower()) and ("+" in s or "-" in s):
        try:
            s_clean = s.replace(",", ".")
            f = float(s_clean)
            if f.is_integer():
                return str(int(f))
            return str(f)
        except ValueError:
            pass
    return s


def normalize_phone(
    raw: Any,
    country_code: str = "",
    min_digits: int = 7,
    max_digits: int = 15,
) -> str | None:
    """Normalize phone number to digits string. Returns None if invalid."""
    text = _to_text(raw).strip()
    if not text:
        return None

    # Reject if it contains alphabetic characters (e.g. "abc", "nope")
    if re.search(r"[a-zA-Z]", text):
        return None

    # NANP validation: numbers explicitly starting with +1 require 11 digits (1 + 10 digits)
    if text.startswith("+1"):
        digits_raw = re.sub(r"\D", "", text)
        if len(digits_raw) != 11:
            return None

    digits = re.sub(r"\D", "", text)
    if not digits:
        return None

    if len(digits) < min_digits or len(digits) > max_digits:
        return None

    # Strip country code prefix if requested and present
    if country_code:
        cc = country_code.lstrip("+").strip()
        if cc and digits.startswith(cc):
            stripped = digits[len(cc):]
            if len(stripped) >= min_digits:
                return stripped
            if not stripped:
                return None
            return stripped

    return digits


def is_valid_phone(
    raw: Any,
    country_code: str = "",
    min_digits: int = 7,
    max_digits: int = 15,
) -> bool:
    """Check if raw value represents a valid phone number."""
    return normalize_phone(raw, country_code=country_code, min_digits=min_digits, max_digits=max_digits) is not None


def mask_phone(phone: Any) -> str:
    """Mask phone number, preserving edge characters for privacy and debugging."""
    text = _to_text(phone).strip()
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    n = len(digits)
    if n <= 4:
        return "*" * n
    return digits[:2] + ("*" * (n - 4)) + digits[-2:]
