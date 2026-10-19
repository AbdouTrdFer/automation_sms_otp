"""Configuration loader for OTP automation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .classifier import parse_rules
from .models import (
    AppConfig,
    AuthConfig,
    BrowserConfig,
    CaptchaConfig,
    DetectionConfig,
    InputConfig,
    ReportingConfig,
    SafetyConfig,
    SelectorsConfig,
    TargetConfig,
)

load_dotenv()


def _ensure_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    return [str(item) for item in val]


def _find_config_file(config_path: str | Path) -> Path:
    p = Path(config_path)
    if p.exists():
        return p
    # Look relative to current working directory or deepseek folder
    candidates = [
        Path.cwd() / p,
        Path.cwd() / "deepseek" / p,
        Path(__file__).parent.parent / p,
        Path(__file__).parent.parent / "config" / "config.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return p


def load_config(
    config_path: str | Path = "config/config.yaml",
    url: str | None = None,
    input_file: str | None = None,
    phone_column: str | None = None,
    country_code: str | None = None,
    country_name: str | None = None,
    output_file: str | None = None,
    headless: bool | None = None,
    log_level: str | None = None,
) -> AppConfig:
    resolved_path = _find_config_file(config_path)
    data: dict[str, Any] = {}
    if resolved_path.exists():
        with open(resolved_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # Target
    target_dict = data.get("target") or {}
    target_url = url or os.getenv("OTP_TARGET_URL") or target_dict.get("url", "")
    # Normalize URL: strip markdown-wrapped formats like @url:`https://...`
    raw_url = str(target_url or "").strip()
    if raw_url.startswith("@url:"):
        raw_url = raw_url[5:].strip()
    while raw_url.startswith("`") and raw_url.endswith("`"):
        raw_url = raw_url[1:-1].strip()
    target = TargetConfig(url=raw_url)

    # Input
    input_dict = data.get("input") or {}
    resolved_input_file = input_file or input_dict.get("file", "input/test_numbers.xlsx")
    resolved_column = phone_column or input_dict.get("column", "phone_number")
    resolved_cc = country_code or os.getenv("DEFAULT_COUNTRY_CODE") or input_dict.get("country_code", "269")
    resolved_cname = country_name or input_dict.get("country_name", "Comoros")

    input_cfg = InputConfig(
        file=str(resolved_input_file),
        sheet=input_dict.get("sheet"),
        column=str(resolved_column),
        country_code=str(resolved_cc),
        country_name=str(resolved_cname),
        min_digits=int(input_dict.get("min_digits", 7)),
        max_digits=int(input_dict.get("max_digits", 15)),
    )

    # Browser
    browser_dict = data.get("browser") or {}
    resolved_headless = headless
    if resolved_headless is None:
        env_headless = os.getenv("OTP_HEADLESS")
        if env_headless is not None:
            resolved_headless = env_headless.lower() in ("1", "true", "yes")
        else:
            resolved_headless = bool(browser_dict.get("headless", True))

    browser = BrowserConfig(
        headless=resolved_headless,
        channel=browser_dict.get("channel"),
        slow_mo_ms=int(browser_dict.get("slow_mo_ms", 0)),
        viewport_width=int(browser_dict.get("viewport_width", 1440)),
        viewport_height=int(browser_dict.get("viewport_height", 900)),
        locale=str(browser_dict.get("locale", "en-US")),
        user_agent=browser_dict.get("user_agent"),
        ignore_https_errors=bool(browser_dict.get("ignore_https_errors", False)),
        default_timeout_ms=int(browser_dict.get("default_timeout_ms", 15000)),
        navigation_timeout_ms=int(browser_dict.get("navigation_timeout_ms", 45000)),
        fresh_context_per_case=bool(browser_dict.get("fresh_context_per_case", True)),
        block_resource_types=_ensure_list(browser_dict.get("block_resource_types")),
    )

    # Selectors
    sel_dict = data.get("selectors") or {}
    selectors = SelectorsConfig(
        phone_input=_ensure_list(sel_dict.get("phone_input", [
            "input[data-e2e='input-username']",
            "input[name='username']",
            "#bn-formItem-NwCozg1O",
            "#bn-formItem-KEDyUSWB",
        ])),
        country_input=_ensure_list(sel_dict.get("country_input", [
            ".bn-sdd-input input",
            ".bn-sdd-input",
            "div[role='group'].bn-sdd-input input",
            "input[aria-label='Text field']",
        ])),
        country_code=str(resolved_cc),
        country_name=str(resolved_cname),
        verify_input_value=bool(sel_dict.get("verify_input_value", True)),
        typing_delay_ms=int(sel_dict.get("typing_delay_ms", 40)),
        agreement_checkbox=_ensure_list(sel_dict.get("agreement_checkbox", [
            ".agreement-checkbox",
            "div[role='checkbox'][name='agreement']",
            "#bn-formItem-TM237UX5",
            "#bn-formItem-9woCYcyM",
        ])),
        agreement_checked_attribute=str(sel_dict.get("agreement_checked_attribute", "aria-checked")),
        agreement_checked_value=str(sel_dict.get("agreement_checked_value", "true")),
        agreement_required=bool(sel_dict.get("agreement_required", True)),
        submit_button=_ensure_list(sel_dict.get("submit_button", [
            "button[aria-label='Register']",
            "button.bn-button__primary",
        ])),
        text_container=str(sel_dict.get("text_container", "body")),
    )

    # Detection
    det_dict = data.get("detection") or {}
    detection = DetectionConfig(
        success_rules=parse_rules(det_dict.get("success_keywords")),
        failure_rules=parse_rules(det_dict.get("failure_keywords")),
        success_selectors=_ensure_list(det_dict.get("success_selectors")),
        failure_selectors=_ensure_list(det_dict.get("failure_selectors")),
        poll_interval_ms=int(det_dict.get("poll_interval_ms", 500)),
        response_timeout_ms=int(det_dict.get("response_timeout_ms", 20000)),
        ignore_baseline_matches=bool(det_dict.get("ignore_baseline_matches", True)),
    )

    # Safety
    safety_dict = data.get("safety") or {}
    safety = SafetyConfig(
        stop_on_first_error=bool(safety_dict.get("stop_on_first_error", False)),
        max_consecutive_problems=int(safety_dict.get("max_consecutive_problems", 5)),
        delay_between_cases_ms=int(safety_dict.get("delay_between_cases_ms", 1500)),
    )

    # Reporting
    rep_dict = data.get("reporting") or {}
    resolved_output = output_file or rep_dict.get("output_file", "output/report.xlsx")
    reporting = ReportingConfig(
        output_file=str(resolved_output),
        also_write_csv=bool(rep_dict.get("also_write_csv", True)),
        screenshot_on=_ensure_list(rep_dict.get("screenshot_on", ["ERROR", "UNKNOWN"])),
        screenshot_dir=str(rep_dict.get("screenshot_dir", "output/screenshots")),
        screenshot_full_page=bool(rep_dict.get("screenshot_full_page", False)),
    )

    # Auth
    auth_dict = data.get("auth") or {}
    auth = AuthConfig(
        enabled=bool(auth_dict.get("enabled", False)),
        login_url=str(auth_dict.get("login_url", "")),
        username_input=str(auth_dict.get("username_input", "input[name='username']")),
        password_input=str(auth_dict.get("password_input", "input[name='password']")),
        submit_button=str(auth_dict.get("submit_button", "button[type='submit']")),
        success_selector=str(auth_dict.get("success_selector", "")),
    )

    captcha_dict = data.get("captcha") or {}
    captcha_provider = str(captcha_dict.get("provider", "none")).lower()
    if captcha_provider not in {"none", "nopecha"}:
        raise ValueError("captcha.provider must be one of: none, nopecha")
    captcha_type = str(captcha_dict.get("type", "auto")).lower()
    if captcha_type not in {"recaptcha2", "hcaptcha", "auto"}:
        raise ValueError("captcha.type must be one of: recaptcha2, hcaptcha, auto")
    captcha_timeout_ms = int(captcha_dict.get("timeout_ms", 2000))
    if captcha_timeout_ms <= 0:
        raise ValueError("captcha.timeout_ms must be greater than 0")
    captcha_solve_timeout_ms = int(captcha_dict.get("solve_timeout_ms", 30000))
    if captcha_solve_timeout_ms <= 0:
        raise ValueError("captcha.solve_timeout_ms must be greater than 0")
    captcha = CaptchaConfig(
        enabled=bool(captcha_dict.get("enabled", False)),
        provider=captcha_provider,
        timeout_ms=captcha_timeout_ms,
        solve_timeout_ms=captcha_solve_timeout_ms,
        sitekey_override=captcha_dict.get("sitekey_override") or None,
        type=captcha_type,
    )

    # Logging
    log_dict = data.get("logging") or {}
    resolved_level = log_level or log_dict.get("level", "INFO")
    resolved_log_file = log_dict.get("file", "output/run.log")

    return AppConfig(
        target=target,
        input=input_cfg,
        browser=browser,
        selectors=selectors,
        detection=detection,
        safety=safety,
        reporting=reporting,
        auth=auth,
        captcha=captcha,
        log_level=str(resolved_level).upper(),
        log_file=str(resolved_log_file),
    )
