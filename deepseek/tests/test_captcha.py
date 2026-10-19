from __future__ import annotations

import logging
from unittest.mock import Mock, patch

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from otp_automation.captcha import (
    CaptchaDetectedError,
    CaptchaSolveError,
    detect_captcha,
    extract_sitekey,
    inject_token,
    solve_captcha,
    solve_nopecha,
    verify_solved,
)
from otp_automation.config import load_config
from otp_automation.models import CaptchaConfig


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class _Result:
    """Mimics Playwright's ValueHandle for .json_value()."""

    def __init__(self, value: str | bool) -> None:
        self.value = value

    def json_value(self):
        return self.value


class _Page:
    """Minimal Playwright Page mock for detection / injection tests."""

    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.wait_args: dict = {}
        self.evaluate_calls: list = []
        self.url = "https://example.com/register"

    def wait_for_function(self, expression, **kwargs):
        self.wait_args = {"expression": expression, **kwargs}
        if self.error:
            raise self.error
        return _Result(self.result if self.result is not None else "")

    def evaluate(self, expression, arg=None):
        self.evaluate_calls.append((expression, arg))
        return self.result


# ---------------------------------------------------------------------------
# Detection tests (existing, kept)
# ---------------------------------------------------------------------------

def test_detect_captcha_returns_detected_widget_type():
    page = _Page(result="hcaptcha")
    result = detect_captcha(page, "auto", 2500)
    assert result == "hcaptcha"
    assert page.wait_args["arg"] == "auto"
    assert page.wait_args["timeout"] == 2500


def test_detect_captcha_returns_none_after_explicit_wait_times_out():
    page = _Page(error=PlaywrightTimeoutError("timed out"))
    assert detect_captcha(page, "recaptcha2", 1000) is None


# ---------------------------------------------------------------------------
# CaptchaDetectedError tests (existing, kept)
# ---------------------------------------------------------------------------

def test_detected_captcha_has_safe_typed_error():
    error = CaptchaDetectedError("recaptcha2")
    assert error.captcha_type == "recaptcha2"
    assert "solve" in str(error).lower() or "blocked" in str(error).lower()
    assert "token" not in str(error).lower()
    assert "api" not in str(error).lower()


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

def test_config_accepts_nopecha_provider(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "captcha:\n"
        "  enabled: true\n"
        "  provider: nopecha\n"
        "  type: recaptcha2\n"
        "  sitekey_override: ABC123xyz\n"
        "  timeout_ms: 3000\n"
        "  solve_timeout_ms: 25000\n",
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.captcha.enabled is True
    assert cfg.captcha.provider == "nopecha"
    assert cfg.captcha.type == "recaptcha2"
    assert cfg.captcha.sitekey_override == "ABC123xyz"
    assert cfg.captcha.timeout_ms == 3000
    assert cfg.captcha.solve_timeout_ms == 25000


def test_config_rejects_invalid_provider(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("captcha:\n  provider: bogus\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be one of: none, nopecha"):
        load_config(config_path)


# ---------------------------------------------------------------------------
# CaptchaSolveError tests
# ---------------------------------------------------------------------------

def test_solve_error_is_typed_and_inherits_runtime():
    error = CaptchaSolveError("verification failed")
    assert isinstance(error, RuntimeError)
    assert "verification" in str(error).lower()


def test_solve_error_message_has_no_api_key():
    error = CaptchaSolveError("sitekey could not be extracted")
    msg = str(error)
    assert "key" not in msg.lower() or "sitekey" in msg.lower()


# ---------------------------------------------------------------------------
# extract_sitekey tests
# ---------------------------------------------------------------------------

def test_extract_sitekey_returns_value_from_page():
    page = _Page(result="abc-sitekey-123")
    assert extract_sitekey(page, "recaptcha2") == "abc-sitekey-123"


def test_extract_sitekey_returns_none_on_evaluation_error():
    page = _Page()
    page.evaluate = Mock(side_effect=Exception("page crashed"))
    assert extract_sitekey(page, "hcaptcha") is None


def test_extract_sitekey_returns_none_when_js_returns_null():
    page = _Page(result=None)
    assert extract_sitekey(page, "recaptcha2") is None


# ---------------------------------------------------------------------------
# solve_nopecha tests
# ---------------------------------------------------------------------------

def _make_mock_job_response(job_id="job-123"):
    """Create a mock POST response with a valid job ID."""
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"data": job_id}
    return resp


def _make_mock_token_response(token="solved-token-value"):
    """Create a mock GET response with a solved token."""
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"data": token}
    return resp


def _make_mock_incomplete_response():
    """Create a mock GET response indicating the job is still processing."""
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = {"error": 14, "message": "Incomplete job"}
    return resp


def test_solve_nopecha_returns_token_after_polling():
    mock_post = _make_mock_job_response("job-456")
    mock_get = _make_mock_token_response("solved-token-value")

    with patch("requests.post", return_value=mock_post) as mock_post_fn, \
         patch("requests.get", return_value=mock_get) as mock_get_fn:
        token = solve_nopecha(
            sitekey="test-sitekey",
            url="https://example.com/page",
            captcha_type="recaptcha2",
            api_key="test-api-key",
            timeout_ms=10000,
        )

    assert token == "solved-token-value"
    mock_post_fn.assert_called_once()
    call = mock_post_fn.call_args
    assert call.kwargs["json"]["type"] == "recaptcha2"
    assert call.kwargs["json"]["sitekey"] == "test-sitekey"
    assert call.kwargs["json"]["url"] == "https://example.com/page"
    assert call.kwargs["json"]["key"] == "test-api-key"
    mock_get_fn.assert_called_once()
    get_call = mock_get_fn.call_args
    assert get_call.kwargs["params"]["id"] == "job-456"


def test_solve_nopecha_polls_when_incomplete_then_returns_token():
    mock_post = _make_mock_job_response("job-789")
    mock_get = _make_mock_incomplete_response()
    mock_get_success = _make_mock_token_response("final-token")

    with patch("requests.post", return_value=mock_post), \
         patch("requests.get", side_effect=[mock_get, mock_get_success]) as mock_get_fn:
        token = solve_nopecha(
            sitekey="sk", url="https://example.com",
            captcha_type="hcaptcha",
            api_key="key", timeout_ms=10000,
        )

    assert token == "final-token"
    assert mock_get_fn.call_count == 2


def test_solve_nopecha_times_out_on_incomplete_job():
    mock_post = _make_mock_job_response("job-timeout")
    mock_get = _make_mock_incomplete_response()

    with patch("requests.post", return_value=mock_post), \
         patch("requests.get", return_value=mock_get), \
         patch("time.sleep"):
        with pytest.raises(CaptchaSolveError, match="timed out"):
            solve_nopecha(
                sitekey="sk", url="https://example.com",
                captcha_type="recaptcha2",
                api_key="key", timeout_ms=500,
            )


def test_solve_nopecha_raises_on_invalid_api_key():
    mock_post = Mock()
    mock_post.status_code = 200
    mock_post.json.return_value = {"error": 1, "message": "Invalid API key"}

    with patch("requests.post", return_value=mock_post):
        with pytest.raises(CaptchaSolveError, match="Invalid API key"):
            solve_nopecha(
                sitekey="sk", url="https://example.com",
                captcha_type="recaptcha2",
                api_key="bad-key", timeout_ms=5000,
            )


def test_solve_nopecha_raises_on_non_200_post():
    mock_post = Mock()
    mock_post.status_code = 503
    mock_post.text = "Service Unavailable"

    with patch("requests.post", return_value=mock_post):
        with pytest.raises(CaptchaSolveError, match="HTTP 503"):
            solve_nopecha(
                sitekey="sk", url="https://example.com",
                captcha_type="recaptcha2",
                api_key="key", timeout_ms=5000,
            )


def test_solve_nopecha_raises_on_network_error():
    import requests as req_lib

    with patch("requests.post", side_effect=req_lib.exceptions.ConnectionError("DNS fail")):
        with pytest.raises(CaptchaSolveError, match="request failed"):
            solve_nopecha(
                sitekey="sk", url="https://example.com",
                captcha_type="recaptcha2",
                api_key="key", timeout_ms=5000,
            )


def test_solve_nopecha_missing_api_key_without_env():
    """When api_key is None and NOPECHA_API_KEY env is unset, raises."""
    import os
    old = os.environ.pop("NOPECHA_API_KEY", None)
    try:
        with pytest.raises(CaptchaSolveError, match="NOPECHA_API_KEY"):
            solve_nopecha(
                sitekey="sk", url="https://example.com",
                captcha_type="recaptcha2",
                api_key=None, timeout_ms=5000,
            )
    finally:
        if old is not None:
            os.environ["NOPECHA_API_KEY"] = old


def test_solve_nopecha_does_not_log_api_key_or_token(caplog):
    mock_post = _make_mock_job_response("job-secret")
    mock_get = _make_mock_token_response("SUPER_SECRET_TOKEN_VALUE")

    logger = logging.getLogger("test_nopecha_logging")
    with caplog.at_level(logging.DEBUG, logger="test_nopecha_logging"):
        with patch("requests.post", return_value=mock_post), \
             patch("requests.get", return_value=mock_get):
            solve_nopecha(
                sitekey="sk", url="https://example.com",
                captcha_type="recaptcha2",
                api_key="SUPER_SECRET_API_KEY_12345",
                timeout_ms=10000,
                log=logger,
            )

    log_output = caplog.text
    assert "SUPER_SECRET_API_KEY_12345" not in log_output
    assert "SUPER_SECRET_TOKEN_VALUE" not in log_output


# ---------------------------------------------------------------------------
# inject_token tests
# ---------------------------------------------------------------------------

def test_inject_token_calls_page_evaluate():
    page = _Page(result=True)
    inject_token(page, "recaptcha2", "dummy-token")
    assert len(page.evaluate_calls) == 1
    _, args = page.evaluate_calls[0]
    assert args == {"captchaType": "recaptcha2", "token": "dummy-token"}


def test_inject_token_raises_on_injection_failure():
    page = _Page()
    page.evaluate = Mock(side_effect=Exception("JS execution error"))
    with pytest.raises(CaptchaSolveError, match="injection failed"):
        inject_token(page, "recaptcha2", "token")


def test_inject_token_raises_when_js_returns_false():
    page = _Page(result=False)
    with pytest.raises(CaptchaSolveError, match="returned false"):
        inject_token(page, "hcaptcha", "token")


# ---------------------------------------------------------------------------
# verify_solved tests
# ---------------------------------------------------------------------------

def test_verify_solved_returns_true_when_widget_reports_solved():
    page = _Page(result=True)
    assert verify_solved(page, "recaptcha2", 5000) is True


def test_verify_solved_returns_false_on_timeout():
    page = _Page(error=PlaywrightTimeoutError("timed out"))
    assert verify_solved(page, "hcaptcha", 1000) is False


# ---------------------------------------------------------------------------
# solve_captcha orchestrator tests
# ---------------------------------------------------------------------------

def test_solve_captcha_uses_sitekey_override():
    """When sitekey_override is set, extract_sitekey should NOT be called."""
    page = _Page(result=None)
    page.url = "https://example.com"
    config = CaptchaConfig(
        enabled=True, provider="nopecha", type="recaptcha2",
        sitekey_override="override-key", timeout_ms=2000, solve_timeout_ms=5000,
    )

    with patch("otp_automation.captcha.solve_nopecha", return_value="tok") as mock_solve, \
         patch("otp_automation.captcha.inject_token") as mock_inject, \
         patch("otp_automation.captcha.verify_solved", return_value=True):
        solve_captcha(page, config, "recaptcha2", log=logging.getLogger("test"), api_key="key")

    # extract_sitekey is bypassed by the override
    assert page.evaluate_calls == []
    mock_solve.assert_called_once()


def test_solve_captcha_extracts_sitekey_when_no_override():
    page = _Page(result="extracted-sitekey")
    page.url = "https://example.com"
    config = CaptchaConfig(
        enabled=True, provider="nopecha", type="recaptcha2",
        sitekey_override=None, timeout_ms=2000, solve_timeout_ms=5000,
    )

    with patch("otp_automation.captcha.solve_nopecha", return_value="tok") as mock_solve, \
         patch("otp_automation.captcha.inject_token") as mock_inject, \
         patch("otp_automation.captcha.verify_solved", return_value=True):
        solve_captcha(page, config, "recaptcha2", log=logging.getLogger("test"), api_key="key")

    # extract_sitekey was called via page.evaluate
    assert len(page.evaluate_calls) >= 1
    mock_solve.assert_called_once()
    # Verify solve_nopecha received the extracted sitekey
    call = mock_solve.call_args
    assert call.kwargs["sitekey"] == "extracted-sitekey"


def test_solve_captcha_raises_when_sitekey_not_found():
    page = _Page(result=None)
    page.url = "https://example.com"
    config = CaptchaConfig(
        enabled=True, provider="nopecha", type="recaptcha2",
        sitekey_override=None, timeout_ms=2000, solve_timeout_ms=5000,
    )

    with pytest.raises(CaptchaSolveError, match="sitekey"):
        solve_captcha(page, config, "recaptcha2", api_key="key")


def test_solve_captcha_raises_when_nopecha_fails():
    page = _Page(result="sitekey-value")
    page.url = "https://example.com"
    config = CaptchaConfig(
        enabled=True, provider="nopecha", type="recaptcha2",
        sitekey_override="sk", timeout_ms=2000, solve_timeout_ms=5000,
    )

    with patch("otp_automation.captcha.solve_nopecha",
               side_effect=CaptchaSolveError("NopeCHA API error")):
        with pytest.raises(CaptchaSolveError, match="NopeCHA API error"):
            solve_captcha(page, config, "recaptcha2", api_key="key")


def test_solve_captcha_raises_when_verification_fails():
    page = _Page(result="sitekey-value")
    page.url = "https://example.com"
    config = CaptchaConfig(
        enabled=True, provider="nopecha", type="recaptcha2",
        sitekey_override="sk", timeout_ms=2000, solve_timeout_ms=5000,
    )

    with patch("otp_automation.captcha.solve_nopecha", return_value="tok"), \
         patch("otp_automation.captcha.inject_token"), \
         patch("otp_automation.captcha.verify_solved", return_value=False):
        with pytest.raises(CaptchaSolveError, match="did not verify"):
            solve_captcha(page, config, "recaptcha2", api_key="key")


def test_solve_captcha_does_not_log_token(caplog):
    page = _Page(result="sitekey-value")
    page.url = "https://example.com"
    config = CaptchaConfig(
        enabled=True, provider="nopecha", type="recaptcha2",
        sitekey_override="sk", timeout_ms=2000, solve_timeout_ms=5000,
    )

    logger = logging.getLogger("test_solve_logging")
    with caplog.at_level(logging.INFO, logger="test_solve_logging"):
        with patch("otp_automation.captcha.solve_nopecha",
                   return_value="ULTRA_SECRET_TOKEN_12345") as mock_solve, \
             patch("otp_automation.captcha.inject_token") as mock_inject, \
             patch("otp_automation.captcha.verify_solved", return_value=True):
            solve_captcha(page, config, "recaptcha2",
                          log=logger, api_key="ULTRA_SECRET_API_KEY_99999")

    log_output = caplog.text
    assert "ULTRA_SECRET_TOKEN_12345" not in log_output
    assert "ULTRA_SECRET_API_KEY_99999" not in log_output
