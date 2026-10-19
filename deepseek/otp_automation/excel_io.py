"""Excel and CSV input reader for test cases."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pandas as pd

from .models import SkippedItem, TestCase
from .phone_utils import _to_text, normalize_phone


def _find_file(path_str: str | Path) -> Path:
    p = Path(path_str)
    if p.exists():
        return p
    candidates = [
        Path.cwd() / p,
        Path.cwd() / "deepseek" / p,
        Path.cwd().parent / p,
        Path(__file__).parent.parent / p,
        Path(__file__).parent.parent.parent / p,
    ]
    for c in candidates:
        if c.exists():
            return c
    return p


def _match_column(columns: list[str], target: str) -> str | None:
    target_clean = target.strip().lower()
    for col in columns:
        if str(col).strip().lower() == target_clean:
            return col
    # Fallback common names
    fallbacks = ["phone_number", "phone", "phone number", "mobile", "number", "tel", "telephone"]
    for fb in fallbacks:
        for col in columns:
            if str(col).strip().lower() == fb:
                return col
    return None


def read_test_cases(
    file_path: str | Path,
    sheet: str | int | None = None,
    column: str = "phone_number",
    country_code: str = "",
    min_digits: int = 7,
    max_digits: int = 15,
) -> tuple[list[TestCase], list[SkippedItem]]:
    """Read and validate phone numbers from an Excel (.xlsx) or CSV (.csv) file."""
    path = _find_file(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    ext = path.suffix.lower()
    if ext == ".csv":
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="latin1")
    elif ext in (".xlsx", ".xls"):
        sheet_arg = sheet if sheet is not None else 0
        df = pd.read_excel(path, sheet_name=sheet_arg, dtype=str, keep_default_na=False)
    else:
        raise ValueError(f"Unsupported file format '{ext}'. Must be .csv or .xlsx/.xls.")

    matched_col = _match_column(list(df.columns), column)
    if not matched_col:
        available = ", ".join(repr(c) for c in df.columns)
        raise ValueError(f"Phone column '{column}' not found. Available columns: {available}")

    cases: list[TestCase] = []
    skipped: list[SkippedItem] = []
    seen: set[str] = set()

    for idx, raw_val in enumerate(df[matched_col]):
        row_num = idx + 2  # Excel row index (1-based, header is row 1)
        raw_text = _to_text(raw_val).strip()

        if not raw_text:
            skipped.append(SkippedItem(row=row_num, raw="", reason="empty"))
            continue

        normalized = normalize_phone(
            raw_text,
            country_code=country_code,
            min_digits=min_digits,
            max_digits=max_digits,
        )

        if normalized is None:
            skipped.append(SkippedItem(row=row_num, raw=raw_text, reason="invalid"))
            continue

        if normalized in seen:
            skipped.append(SkippedItem(row=row_num, raw=raw_text, reason="duplicate"))
            continue

        seen.add(normalized)
        cases.append(TestCase(row=row_num, raw=raw_text, normalized=normalized))

    return cases, skipped
