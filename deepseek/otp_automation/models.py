"""Data models and configuration definitions for OTP automation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal


CaptchaType = Literal["recaptcha2", "hcaptcha", "auto"]
CaptchaProvider = Literal["none", "nopecha"]


class TestStatus(str, Enum):
    __test__ = False
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"
    SKIPPED = "SKIPPED"


@dataclass
class Rule:
    keyword: str
    category: str = "default"


@dataclass
class DetectionConfig:
    success_rules: list[Rule] = field(default_factory=list)
    failure_rules: list[Rule] = field(default_factory=list)
    success_selectors: list[str] = field(default_factory=list)
    failure_selectors: list[str] = field(default_factory=list)
    poll_interval_ms: int = 500
    response_timeout_ms: int = 20000
    ignore_baseline_matches: bool = True


@dataclass
class TargetConfig:
    url: str = ""


@dataclass
class InputConfig:
    file: str = "input/test_numbers.xlsx"
    sheet: str | None = None
    column: str = "phone_number"
    country_code: str = ""
    country_name: str = "Comoros"
    min_digits: int = 7
    max_digits: int = 15


@dataclass
class BrowserConfig:
    headless: bool = True
    channel: str | None = None
    slow_mo_ms: int = 0
    viewport_width: int = 1440
    viewport_height: int = 900
    locale: str = "en-US"
    user_agent: str | None = None
    ignore_https_errors: bool = False
    default_timeout_ms: int = 15000
    navigation_timeout_ms: int = 45000
    fresh_context_per_case: bool = True
    block_resource_types: list[str] = field(default_factory=list)


@dataclass
class SelectorsConfig:
    phone_input: list[str] = field(default_factory=lambda: [
        "input[data-e2e='input-username']",
        "input[name='username']",
        "#bn-formItem-NwCozg1O",
        "#bn-formItem-KEDyUSWB",
    ])
    country_input: list[str] = field(default_factory=lambda: [
        ".bn-sdd-input input",
        ".bn-sdd-input",
        "div[role='group'].bn-sdd-input input",
        "input[aria-label='Text field']",
    ])
    country_code: str = "269"
    country_name: str = "Comoros"
    verify_input_value: bool = True
    typing_delay_ms: int = 40
    agreement_checkbox: list[str] = field(default_factory=lambda: [
        ".agreement-checkbox",
        "div[role='checkbox'][name='agreement']",
        "#bn-formItem-TM237UX5",
        "#bn-formItem-9woCYcyM",
    ])
    agreement_checked_attribute: str = "aria-checked"
    agreement_checked_value: str = "true"
    agreement_required: bool = True
    submit_button: list[str] = field(default_factory=lambda: [
        "button[aria-label='Register']",
        "button.bn-button__primary",
    ])
    text_container: str = "body"


@dataclass
class SafetyConfig:
    stop_on_first_error: bool = False
    max_consecutive_problems: int = 5
    delay_between_cases_ms: int = 1500


@dataclass
class ReportingConfig:
    output_file: str = "output/report.xlsx"
    also_write_csv: bool = True
    screenshot_on: list[str] = field(default_factory=lambda: ["ERROR", "UNKNOWN"])
    screenshot_dir: str = "output/screenshots"
    screenshot_full_page: bool = False


@dataclass
class AuthConfig:
    enabled: bool = False
    login_url: str = ""
    username_input: str = "input[name='username']"
    password_input: str = "input[name='password']"
    submit_button: str = "button[type='submit']"
    success_selector: str = ""


@dataclass
class CaptchaConfig:
    enabled: bool = False
    provider: CaptchaProvider = "none"
    timeout_ms: int = 2000
    solve_timeout_ms: int = 30000
    sitekey_override: str | None = None
    type: CaptchaType = "auto"


@dataclass
class AppConfig:
    target: TargetConfig = field(default_factory=TargetConfig)
    input: InputConfig = field(default_factory=InputConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    selectors: SelectorsConfig = field(default_factory=SelectorsConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    captcha: CaptchaConfig = field(default_factory=CaptchaConfig)
    log_level: str = "INFO"
    log_file: str = "output/run.log"


@dataclass
class TestCase:
    __test__ = False
    row: int
    raw: str
    normalized: str


@dataclass
class SkippedItem:
    row: int
    raw: str
    reason: str


@dataclass
class ExecutionResult:
    test_case: TestCase
    status: TestStatus
    category: str | None = None
    message: str = ""
    error: str | None = None
    duration_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    screenshot_path: str | None = None
