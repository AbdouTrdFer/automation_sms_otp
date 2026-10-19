"""Tests for the Binance slider CAPTCHA (bs-modal) solver functions."""
from __future__ import annotations

import base64
import logging
from unittest.mock import MagicMock, Mock, patch

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from otp_automation.captcha import (
    CaptchaConfig,
    CaptchaSolveError,
    ENV_API_KEY,
    NOPECHA_RECOGNITION_URL,
    _call_nopecha_recognition,
    _poll_slider_solution,
    _url_to_base64,
    _drag_slider,
    _verify_slider_solved,
    detect_biance_slider,
    solve_biance_slider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_sleeps():
    """Prevent real sleep calls from slowing tests."""
    with patch("otp_automation.captcha.time.sleep"):
        yield


@pytest.fixture
def mock_page():
    page = MagicMock()
    page.mouse = MagicMock()
    page.evaluate = MagicMock()
    page.wait_for_function = MagicMock()
    return page


@pytest.fixture
def mock_config():
    return CaptchaConfig(
        enabled=True,
        provider="nopecha",
        type="auto",
        sitekey_override=None,
        timeout_ms=5000,
        solve_timeout_ms=30000,
    )


def _result(value):
    r = MagicMock()
    r.json_value.return_value = value
    return r


# ---------------------------------------------------------------------------
# detect_biance_slider
# ---------------------------------------------------------------------------

class TestDetectBianceSlider:
    def test_returns_true_when_modal_visible(self, mock_page):
        mock_page.wait_for_function.return_value = _result(True)
        assert detect_biance_slider(mock_page, 5000) is True

    def test_returns_false_on_timeout(self, mock_page):
        mock_page.wait_for_function.side_effect = PlaywrightTimeoutError("timed out")
        assert detect_biance_slider(mock_page, 1000) is False

    def test_uses_bs_modal_selector(self, mock_page):
        mock_page.wait_for_function.return_value = _result(True)
        detect_biance_slider(mock_page, 5000)
        js_script = mock_page.wait_for_function.call_args[0][0]
        assert ".bs-modal" in js_script


# ---------------------------------------------------------------------------
# _url_to_base64
# ---------------------------------------------------------------------------

class TestUrlToBase64:
    def test_converts_successfully(self):
        fake_img = b"fake-image-bytes"
        expected = base64.b64encode(fake_img).decode("ascii")
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.content = fake_img
        with patch("requests.get", return_value=mock_resp):
            result = _url_to_base64("https://example.com/puzzle.png")
        assert result == expected

    def test_raises_on_non_200(self):
        mock_resp = Mock()
        mock_resp.status_code = 404
        with patch("requests.get", return_value=mock_resp):
            with pytest.raises(CaptchaSolveError, match="HTTP 404"):
                _url_to_base64("https://example.com/puzzle.png")

    def test_raises_on_timeout(self):
        import requests as req_lib
        with patch("requests.get", side_effect=req_lib.exceptions.Timeout("Timed out")):
            with pytest.raises(CaptchaSolveError, match="timed out"):
                _url_to_base64("https://example.com/puzzle.png")


# ---------------------------------------------------------------------------
# _call_nopecha_recognition
# ---------------------------------------------------------------------------

class TestCallNopeChaRecognition:
    def test_returns_job_id_on_success(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": "job-12345"}
        with patch("requests.post", return_value=mock_resp) as mp:
            job_id = _call_nopecha_recognition("img", api_key="key", log=None)
        assert job_id == "job-12345"
        assert mp.call_args[1]["json"]["key"] == "key"
        assert "Authorization" not in mp.call_args[1]["headers"]

    def test_no_key_in_payload_without_api_key(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": "job-1"}
        with patch("requests.post", return_value=mock_resp) as mp:
            _call_nopecha_recognition("img", api_key=None, log=None)
        assert "key" not in mp.call_args[1]["json"]
        assert "Authorization" not in mp.call_args[1]["headers"]

    def test_raises_on_non_200(self):
        mock_resp = Mock()
        mock_resp.status_code = 401
        mock_resp.text = "Invalid API key"
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(CaptchaSolveError, match="HTTP 401"):
                _call_nopecha_recognition("img", api_key="bad", log=None)

    def test_raises_on_missing_data(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": "Rate limit exceeded"}
        with patch("requests.post", return_value=mock_resp):
            with pytest.raises(CaptchaSolveError, match="Rate limit"):
                _call_nopecha_recognition("img", api_key="key", log=None)


# ---------------------------------------------------------------------------
# _poll_slider_solution
# ---------------------------------------------------------------------------

class TestPollSliderSolution:
    def test_returns_solution_immediately(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"x": 67.5}}
        with patch("requests.get", return_value=mock_resp):
            result = _poll_slider_solution("job-1", api_key=None, timeout_ms=5000, log=None)
        assert result == {"x": 67.5}

    def test_polls_until_solution(self):
        pending = Mock()
        pending.status_code = 200
        pending.json.return_value = {"data": None}
        success = Mock()
        success.status_code = 200
        success.json.return_value = {"data": {"x": 42.0}}
        with patch("requests.get", side_effect=[pending, success]):
            result = _poll_slider_solution("job-1", api_key=None, timeout_ms=10000, log=None)
        assert result == {"x": 42.0}

    def test_times_out_without_solution(self):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {"data": None}
        with patch("requests.get", return_value=resp):
            with pytest.raises(CaptchaSolveError, match="timed out"):
                _poll_slider_solution("job-1", api_key=None, timeout_ms=500, log=None)

    def test_api_key_passed_as_query_param(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"x": 67.5}}
        with patch("requests.get", return_value=mock_resp) as mg:
            _poll_slider_solution("job-1", api_key="mykey", timeout_ms=5000, log=None)
        params = mg.call_args[1]["params"]
        assert params["id"] == "job-1"
        assert params["key"] == "mykey"


# ---------------------------------------------------------------------------
# _drag_slider
# ---------------------------------------------------------------------------

class TestDragSlider:
    def test_moves_to_thumb_before_down(self, mock_page):
        mock_page.evaluate.return_value = {
            "thumbCenterX": 50, "thumbCenterY": 100,
            "thumbLeft": 10, "containerWidth": 400,
        }
        _drag_slider(mock_page, 67.5, log=None)
        # First mouse.move should go to thumb center
        first_move = mock_page.mouse.move.call_args_list[0]
        assert first_move[0][0] == 50  # thumbCenterX
        assert first_move[0][1] == 100  # thumbCenterY
        # Then mouse.down
        mock_page.mouse.down.assert_called_once()

    def test_calls_mouse_down_move_up(self, mock_page):
        mock_page.evaluate.return_value = {
            "thumbCenterX": 50, "thumbCenterY": 100,
            "thumbLeft": 10, "containerWidth": 400,
        }
        _drag_slider(mock_page, 67.5, log=None)
        mock_page.mouse.down.assert_called_once()
        mock_page.mouse.up.assert_called_once()
        assert mock_page.mouse.move.call_count >= 2  # at least 2 moves (to thumb + drag)

    def test_raises_when_elements_missing(self, mock_page):
        mock_page.evaluate.return_value = None
        with pytest.raises(CaptchaSolveError, match="not found"):
            _drag_slider(mock_page, 67.5, log=None)

    def test_raises_on_zero_width(self, mock_page):
        mock_page.evaluate.return_value = {
            "thumbCenterX": 50, "thumbCenterY": 100,
            "thumbLeft": 10, "containerWidth": 0,
        }
        with pytest.raises(CaptchaSolveError, match="invalid"):
            _drag_slider(mock_page, 67.5, log=None)


# ---------------------------------------------------------------------------
# _verify_slider_solved
# ---------------------------------------------------------------------------

class TestVerifySliderSolved:
    def test_returns_true_on_success_icon(self, mock_page):
        mock_page.wait_for_function.return_value = _result(True)
        # _verify_slider_solved calls page.evaluate for modal check fallback
        mock_page.evaluate.return_value = False  # modal exists
        assert _verify_slider_solved(mock_page, None, 5000) is True

    def test_returns_false_on_fail_icon(self, mock_page):
        mock_page.wait_for_function.return_value = _result(False)
        mock_page.evaluate.return_value = False  # modal still exists
        assert _verify_slider_solved(mock_page, None, 5000) is False

    def test_returns_false_on_timeout_with_modal_still_present(self, mock_page):
        mock_page.wait_for_function.side_effect = PlaywrightTimeoutError("timed out")
        mock_page.evaluate.return_value = False  # modal exists → not solved
        assert _verify_slider_solved(mock_page, None, 1000) is False

    def test_returns_true_on_timeout_if_modal_gone(self, mock_page):
        mock_page.wait_for_function.side_effect = PlaywrightTimeoutError("timed out")
        mock_page.evaluate.return_value = True  # modal gone → solved
        assert _verify_slider_solved(mock_page, None, 1000) is True

    def test_uses_config_timeout_when_config_provided(self, mock_page, mock_config):
        """When config is provided, timeout should be max(timeout_ms, config.timeout_ms)."""
        mock_page.wait_for_function.return_value = _result(True)
        mock_page.evaluate.return_value = False
        # Should succeed with config-provided timeout
        assert _verify_slider_solved(mock_page, mock_config, 5000) is True


# ---------------------------------------------------------------------------
# solve_biance_slider (full pipeline)
# ---------------------------------------------------------------------------

class TestSolveBianceSlider:
    def test_full_pipeline_success(self, mock_page, mock_config):
        mock_page.evaluate.side_effect = [
            "https://example.com/puzzle.png",  # extract image URL
        ]
        # wait_for_function: loading_complete(True) → verify(True)
        mock_page.wait_for_function.side_effect = [_result(True), _result(True)]

        with patch("otp_automation.captcha._url_to_base64", return_value="base64img"),              patch("otp_automation.captcha._call_nopecha_recognition", return_value="job-abc"),              patch("otp_automation.captcha._poll_slider_solution", return_value={"x": 67.5}),              patch("otp_automation.captcha._drag_slider"),              patch("otp_automation.captcha._verify_slider_solved", return_value=True),              patch("otp_automation.captcha.os.environ.get", return_value="test-key"):
            result = solve_biance_slider(mock_page, mock_config, log=MagicMock())

        assert result is True

    def test_raises_when_no_image_url(self, mock_page, mock_config):
        mock_page.wait_for_function.side_effect = [_result(True)]
        mock_page.evaluate.side_effect = [None]  # no image URL found
        with pytest.raises(CaptchaSolveError, match="puzzle image"):
            solve_biance_slider(mock_page, mock_config, log=MagicMock())

    def test_raises_on_api_failure(self, mock_page, mock_config):
        mock_page.evaluate.side_effect = ["https://example.com/puzzle.png"]
        mock_page.wait_for_function.side_effect = [_result(True)]
        with patch("otp_automation.captcha._url_to_base64", return_value="base64img"),              patch("otp_automation.captcha._call_nopecha_recognition",
                   side_effect=CaptchaSolveError("HTTP 401: Invalid API key")),              patch("otp_automation.captcha.os.environ.get", return_value="key"):
            with pytest.raises(CaptchaSolveError, match="HTTP 401"):
                solve_biance_slider(mock_page, mock_config, log=MagicMock())

    def test_raises_on_poll_timeout(self, mock_page, mock_config):
        mock_page.evaluate.side_effect = ["https://example.com/puzzle.png"]
        mock_page.wait_for_function.side_effect = [_result(True)]
        with patch("otp_automation.captcha._url_to_base64", return_value="base64img"),              patch("otp_automation.captcha._call_nopecha_recognition", return_value="job-abc"),              patch("otp_automation.captcha._poll_slider_solution",
                   side_effect=CaptchaSolveError("timed out")),              patch("otp_automation.captcha.os.environ.get", return_value="key"):
            with pytest.raises(CaptchaSolveError, match="timed out"):
                solve_biance_slider(mock_page, mock_config, log=MagicMock())

    def test_retries_on_verify_failure(self, mock_page, mock_config):
        """When first attempt fails, should retry with adjusted percentage."""
        mock_page.evaluate.side_effect = ["https://example.com/puzzle.png"]
        mock_page.wait_for_function.side_effect = [_result(True), _result(True), _result(True)]
        with patch("otp_automation.captcha._url_to_base64", return_value="base64img"),              patch("otp_automation.captcha._call_nopecha_recognition", return_value="job-abc"),              patch("otp_automation.captcha._poll_slider_solution", return_value={"x": 67.5}),              patch("otp_automation.captcha._drag_slider") as mock_drag,              patch("otp_automation.captcha._verify_slider_solved",
                   side_effect=[False, False, True]) as mock_verify,              patch("otp_automation.captcha.os.environ.get", return_value="key"):
            result = solve_biance_slider(mock_page, mock_config, log=MagicMock())

        assert result is True
        assert mock_drag.call_count == 3  # original + 2 retries
        assert mock_verify.call_count == 3

    def test_raises_after_all_retries_fail(self, mock_page, mock_config):
        mock_page.evaluate.side_effect = ["https://example.com/puzzle.png"]
        mock_page.wait_for_function.side_effect = [_result(True)]
        with patch("otp_automation.captcha._url_to_base64", return_value="base64img"), \
             patch("otp_automation.captcha._call_nopecha_recognition", return_value="job-abc"), \
             patch("otp_automation.captcha._poll_slider_solution", return_value={"x": 67.5}), \
             patch("otp_automation.captcha._drag_slider"), \
             patch("otp_automation.captcha._verify_slider_solved", return_value=False), \
             patch("otp_automation.captcha.os.environ.get", return_value="key"):
            with pytest.raises(CaptchaSolveError, match="not solved"):
                solve_biance_slider(mock_page, mock_config, log=MagicMock())

    def test_does_not_log_api_key(self, mock_page, mock_config, caplog):
        mock_page.evaluate.side_effect = ["https://example.com/puzzle.png"]
        mock_page.wait_for_function.side_effect = [_result(True)]
        logger = logging.getLogger("test_slider_logging")
        with caplog.at_level(logging.DEBUG, logger="test_slider_logging"):
            with patch("otp_automation.captcha._url_to_base64", return_value="base64img"),                  patch("otp_automation.captcha._call_nopecha_recognition", return_value="job-abc"),                  patch("otp_automation.captcha._poll_slider_solution", return_value={"x": 67.5}),                  patch("otp_automation.captcha._drag_slider"),                  patch("otp_automation.captcha._verify_slider_solved", return_value=True),                  patch("otp_automation.captcha.os.environ.get", return_value="SECRET_KEY_999"):
                solve_biance_slider(mock_page, mock_config, log=logger)
        assert "SECRET_KEY_999" not in caplog.text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_nopecha_recognition_url(self):
        assert NOPECHA_RECOGNITION_URL == "https://api.nopecha.com/v1/recognition/geetest"

    def test_env_api_key(self):
        assert ENV_API_KEY == "NOPECHA_API_KEY"
