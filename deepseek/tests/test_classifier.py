from __future__ import annotations

from otp_automation.classifier import baseline_keywords, classify_text, parse_rules
from otp_automation.models import DetectionConfig, TestStatus


def make_config(**overrides) -> DetectionConfig:
    base = dict(
        success_rules=parse_rules(["code sent", "verification code", "we sent"]),
        failure_rules=parse_rules(["invalid phone", "not recognized", "please enter a valid"]),
        success_selectors=[],
        failure_selectors=[],
        poll_interval_ms=100,
        response_timeout_ms=1000,
        ignore_baseline_matches=True,
    )
    base.update(overrides)
    return DetectionConfig(**base)


def test_parse_rules_supports_strings_and_dicts():
    rules = parse_rules(["Code Sent", {"keyword": "invalid phone", "category": "invalid"}])
    assert [r.keyword for r in rules] == ["code sent", "invalid phone"]
    assert rules[1].category == "invalid"


def test_success_keyword_detected():
    result = classify_text("We have sent you a verification code via SMS.", make_config())
    assert result is not None
    assert result.status is TestStatus.SUCCESS


def test_failure_keyword_takes_precedence_over_success():
    # Text contains BOTH a failure phrase and a success phrase.
    result = classify_text(
        "Invalid phone number. A verification code will be sent if it exists.",
        make_config(),
    )
    assert result is not None
    assert result.status is TestStatus.FAILED


def test_no_signal_returns_none():
    assert classify_text("Welcome. Please log in.", make_config()) is None


def test_baseline_keywords_excluded():
    cfg = make_config()
    baseline = baseline_keywords("Please enter a valid phone number.", cfg)
    assert "please enter a valid" in baseline

    # After baseline exclusion, the same failure phrase no longer matches.
    result = classify_text(
        "Please enter a valid phone number to continue.",
        cfg,
        excluded=baseline,
    )
    assert result is None


def test_baseline_matches_can_be_disabled_via_api():
    cfg = make_config()
    # When caller passes an empty excluded set, failure phrase still matches.
    result = classify_text("Please enter a valid phone number.", cfg, excluded=set())
    assert result is not None
    assert result.status is TestStatus.FAILED


def test_case_insensitive_matching():
    result = classify_text("WE SENT A VERIFICATION CODE", make_config())
    assert result is not None
    assert result.status is TestStatus.SUCCESS