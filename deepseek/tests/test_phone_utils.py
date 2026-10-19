from __future__ import annotations

import pytest

from otp_automation.phone_utils import _to_text, is_valid_phone, mask_phone, normalize_phone


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+1 202 555 0143", "12025550143"),
        ("+1 (202) 555-0144", "12025550144"),
        ("+1.202.555.0145", "12025550145"),
        ("  12025550146  ", "12025550146"),
        ("+44 7700 900123", "447700900123"),
        (12025550147, "12025550147"),
        (12025550148.0, "12025550148"),
    ],
)
def test_normalize_accepts_common_formats(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "abc", "123", "12-34", "+1 202 555 014", "12 34 56 78 90 12 34 56 78"],
)
def test_normalize_rejects_invalid(raw):
    assert normalize_phone(raw) is None


def test_normalize_strips_country_code_when_present():
    assert normalize_phone("+1 202 555 0143", country_code="1") == "2025550143"
    assert normalize_phone("12025550143", country_code="+1") == "2025550143"
    assert normalize_phone("2025550143", country_code="1") == "2025550143"
    assert normalize_phone("2694226383", country_code="269") == "4226383"
    assert normalize_phone("+269 422 6383", country_code="269") == "4226383"


def test_normalize_rejects_out_of_range_lengths():
    assert normalize_phone("123456", min_digits=7) is None
    assert normalize_phone("1234567890123456", max_digits=15) is None
    assert normalize_phone("1234567890123456") is None  # 16 digits


def test_is_valid_phone_wrapper():
    assert is_valid_phone("+1 202 555 0143")
    assert not is_valid_phone("nope")


def test_mask_phone_keeps_edges():
    assert mask_phone("12025550143") == "12*******43"
    assert mask_phone("12") == "**"
    assert mask_phone("") == ""
    assert mask_phone("+1 202 555 0143") == "12*******43"


def test_to_text_avoids_scientific_notation():
    assert _to_text(12025550143.0) == "12025550143"
    assert _to_text(12025550143) == "12025550143"
    assert _to_text(None) == ""
    assert _to_text(12.5) == "12.5"