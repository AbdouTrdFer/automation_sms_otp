"""Classification engine for detecting OTP platform response signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import DetectionConfig, Rule, TestStatus


@dataclass
class ClassificationResult:
    status: TestStatus
    category: str
    keyword: str


def parse_rules(rules: list[str | dict[str, Any] | Rule] | None) -> list[Rule]:
    """Parse list of strings, dicts, or Rule objects into normalized Rule objects."""
    if not rules:
        return []
    parsed: list[Rule] = []
    for item in rules:
        if isinstance(item, Rule):
            parsed.append(Rule(keyword=item.keyword.lower().strip(), category=item.category))
        elif isinstance(item, dict):
            kw = str(item.get("keyword", "")).lower().strip()
            cat = str(item.get("category", "default"))
            parsed.append(Rule(keyword=kw, category=cat))
        elif isinstance(item, str):
            parsed.append(Rule(keyword=item.lower().strip(), category="default"))
    return parsed


def baseline_keywords(text: str, config: DetectionConfig) -> set[str]:
    """Collect any keywords present in page text before action is taken."""
    if not text:
        return set()
    text_lower = text.lower()
    matches = set()
    all_rules = (config.failure_rules or []) + (config.success_rules or [])
    for rule in all_rules:
        if rule.keyword and rule.keyword in text_lower:
            matches.add(rule.keyword)
    return matches


def classify_text(
    text: str,
    config: DetectionConfig,
    excluded: set[str] | None = None,
) -> ClassificationResult | None:
    """Classify text against failure and success rules. Failure rules take precedence."""
    if not text:
        return None

    text_lower = text.lower()
    ex = excluded if excluded is not None else set()

    # 1. Failure rules evaluated first
    for rule in (config.failure_rules or []):
        if rule.keyword and (rule.keyword in text_lower) and (rule.keyword not in ex):
            return ClassificationResult(
                status=TestStatus.FAILED,
                category=rule.category,
                keyword=rule.keyword,
            )

    # 2. Success rules evaluated next
    for rule in (config.success_rules or []):
        if rule.keyword and (rule.keyword in text_lower) and (rule.keyword not in ex):
            return ClassificationResult(
                status=TestStatus.SUCCESS,
                category=rule.category,
                keyword=rule.keyword,
            )

    return None
