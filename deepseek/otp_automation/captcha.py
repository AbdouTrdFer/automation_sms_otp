"""CAPTCHA widget detection and solving for the browser runner.

This module provides a modular CAPTCHA pipeline:
  1. detect_captcha()  -- wait for a reCAPTCHA v2 / hCaptcha widget to appear.
  2. extract_sitekey() -- pull the sitekey from the page (or config override).
  3. solve_nopecha()   -- call the NopeCHA Token API to obtain a solution token.
  4. inject_token()    -- write the token into the page and trigger callbacks.
  5. verify_solved()   -- confirm the widget reflects a solved state.

Logging never emits API keys or tokens -- only the captcha type, steps,
and outcome.
"""

from __future__ import annotations

import json
import logging
import os
import time
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .models import CaptchaConfig, CaptchaType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOPECHA_API_URL = "https://api.nopecha.com/token"

ENV_API_KEY = "NOPECHA_API_KEY"

# Poll interval for the NopeCHA GET polling loop (seconds).
_POLL_INTERVAL_S = 1.0

# HTTP request timeout for individual NopeCHA API calls (seconds).
_HTTP_TIMEOUT_S = 30

# ---------------------------------------------------------------------------
# JavaScript snippets  (executed inside the browser page)
# ---------------------------------------------------------------------------

# Returns "recaptcha2", "hcaptcha", or null.
_DETECTION_SCRIPT = r"""expectedType => {
    const visible = element => {
        const style = window.getComputedStyle(element);
        return style.display !== "none" &&
            style.visibility !== "hidden" &&
            element.getClientRects().length > 0;
    };
    const frames = Array.from(document.querySelectorAll("iframe"));
    const recaptcha = frames.some(frame =>
        /(?:google\.com|recaptcha\.net)\/recaptcha/i.test(frame.src)
    ) || Array.from(document.querySelectorAll(
        ".g-recaptcha, #recaptcha, [id*='recaptcha']"
    )).some(visible);
    const hcaptcha = frames.some(frame =>
        /hcaptcha\.com/i.test(frame.src)
    ) || Array.from(document.querySelectorAll(
        ".h-captcha, #hcaptcha, [id*='hcaptcha']"
    )).some(visible);
    const generic = Array.from(document.querySelectorAll("[data-sitekey]")).some(visible);

    if (expectedType === "recaptcha2" && (recaptcha || generic)) return "recaptcha2";
    if (expectedType === "hcaptcha" && (hcaptcha || generic)) return "hcaptcha";
    if (recaptcha) return "recaptcha2";
    if (hcaptcha) return "hcaptcha";
    if (generic) return "unknown";
    return false;
}"""

# Extracts the sitekey from a visible reCAPTCHA / hCaptcha widget.
# Returns the sitekey string or null.
_SITEKEY_SCRIPT = r"""captchaType => {
    const widgetSelector = captchaType === 'recaptcha2'
        ? '.g-recaptcha[data-sitekey]'
        : captchaType === 'hcaptcha'
            ? '.h-captcha[data-sitekey]'
            : '[data-sitekey]';
    const widget = document.querySelector(widgetSelector);
    if (widget) {
        const sk = widget.getAttribute('data-sitekey');
        if (sk && sk.trim()) return sk.trim();
    }

    const all = Array.from(document.querySelectorAll('[data-sitekey]'));
    for (const el of all) {
        const style = window.getComputedStyle(el);
        const isVisible = style.display !== 'none'
            && style.visibility !== 'hidden'
            && el.getClientRects().length > 0;
        if (isVisible) {
            const sk = el.getAttribute('data-sitekey');
            if (sk && sk.trim()) return sk.trim();
        }
    }

    const iframes = Array.from(document.querySelectorAll('iframe'));
    for (const iframe of iframes) {
        const src = iframe.src || '';
        if (captchaType === 'recaptcha2' || /google\.com.*recaptcha/i.test(src)) {
            const match = src.match(/[?&]k=([^&]+)/);
            if (match) return decodeURIComponent(match[1]);
        }
        if (captchaType === 'hcaptcha' || /hcaptcha\.com/i.test(src)) {
            const match = src.match(/[?&]sitekey=([^&]+)/);
            if (match) return decodeURIComponent(match[1]);
        }
    }
    return null;
}"""

# Injects a solved token into the page and fires the widget callback.
_INJECT_SCRIPT = r"""(params) => {
    const captchaType = params.captchaType;
    const token = params.token;
    let responseId, responseName, apiName, widgetSelector;
    if (captchaType === 'recaptcha2') {
        responseId = 'g-recaptcha-response';
        responseName = 'g-recaptcha-response';
        apiName = 'grecaptcha';
        widgetSelector = '.g-recaptcha';
    } else if (captchaType === 'hcaptcha') {
        responseId = 'h-captcha-response';
        responseName = 'h-captcha-response';
        apiName = 'hcaptcha';
        widgetSelector = '.h-captcha';
    } else {
        return false;
    }

    const responseEl = document.getElementById(responseId);
    if (responseEl) {
        responseEl.value = token;
    }
    document.querySelectorAll('input[name="' + responseName + '"]').forEach(el => {
        el.value = token;
    });
    document.querySelectorAll('textarea[name="' + responseName + '"]').forEach(el => {
        el.value = token;
    });

    if (typeof window[apiName] !== 'undefined' && window[apiName]) {
        const api = window[apiName];
        if (typeof api.getResponse === 'function') {
            const original = api.getResponse.bind(api);
            api.getResponse = function() {
                const val = original();
                return val || token;
            };
        }
    }

    const widget = document.querySelector(widgetSelector);
    if (widget) {
        const callback = widget.getAttribute('data-callback');
        if (callback && typeof window[callback] === 'function') {
            window[callback](token, 'none');
        }
    }
    return true;
}"""

# Returns true when the widget reports a solved state.
_VERIFY_SCRIPT = r"""captchaType => {
    let responseId, apiName;
    if (captchaType === 'recaptcha2') {
        responseId = 'g-recaptcha-response';
        apiName = 'grecaptcha';
    } else if (captchaType === 'hcaptcha') {
        responseId = 'h-captcha-response';
        apiName = 'hcaptcha';
    } else {
        return false;
    }
    if (typeof window[apiName] !== 'undefined' && window[apiName]) {
        const api = window[apiName];
        if (typeof api.getResponse === 'function') {
            const resp = api.getResponse();
            if (resp && String(resp).length > 0) return true;
        }
    }
    const el = document.getElementById(responseId);
    if (el && el.value && el.value.length > 0) return true;
    return false;
}"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CaptchaDetectedError(RuntimeError):
    """Raised when a CAPTCHA is present and no solver is configured."""

    def __init__(self, captcha_type: str) -> None:
        self.captcha_type = captcha_type
        super().__init__(f"captcha detected ({captcha_type}); not configured to solve")


class CaptchaSolveError(RuntimeError):
    """Raised when solving a CAPTCHA fails (API, injection, or verification).

    This error is classified as ERROR by the runner -- never SUCCESS.
    """


# ---------------------------------------------------------------------------
# 1. Detection
# ---------------------------------------------------------------------------

def detect_captcha(page: Page, captcha_type: CaptchaType, timeout_ms: int) -> str | None:
    """Wait for a supported widget to appear; return its type or None on timeout."""
    try:
        result = page.wait_for_function(
            _DETECTION_SCRIPT,
            arg=captcha_type,
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError:
        return None
    return result.json_value()


# ---------------------------------------------------------------------------
# 2. Sitekey extraction
# ---------------------------------------------------------------------------

def extract_sitekey(page: Page, captcha_type: str) -> str | None:
    """Extract the reCAPTCHA/hCaptcha sitekey from the page."""
    try:
        return page.evaluate(_SITEKEY_SCRIPT, captcha_type)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 3. NopeCHA Token API
# ---------------------------------------------------------------------------

def _nopecha_api_key() -> str:
    """Read the NopeCHA API key from the environment (never logged)."""
    key = os.environ.get(ENV_API_KEY, "").strip()
    if not key:
        raise CaptchaSolveError(
            f"No {ENV_API_KEY} environment variable set; cannot call NopeCHA API"
        )
    return key


def solve_nopecha(
    sitekey: str,
    url: str,
    captcha_type: str,
    api_key: str | None = None,
    timeout_ms: int = 30000,
    log: logging.Logger | None = None,
) -> str:
    """Call the NopeCHA Token API and return the solved token.

    Uses the two-step pattern documented at https://developers.nopecha.com:
      1. POST  -> receive a job ID.
      2. GET   -> poll until the token is ready.

    Raises CaptchaSolveError on any failure.
    """
    import requests  # imported lazily so the module is importable without the package

    key = api_key or _nopecha_api_key()
    deadline = time.monotonic() + (timeout_ms / 1000.0)

    if log:
        log.info("NopeCHA: submitting job (type=%s, sitekey length=%d, url=%s)",
                 captcha_type, len(sitekey), url)

    # Step 1 -- submit the captcha for solving.
    try:
        resp = requests.post(
            NOPECHA_API_URL,
            json={
                "key": key,
                "type": captcha_type,
                "sitekey": sitekey,
                "url": url,
            },
            timeout=_HTTP_TIMEOUT_S,
        )
    except requests.exceptions.Timeout:
        raise CaptchaSolveError("NopeCHA API request timed out during job submission")
    except requests.exceptions.RequestException as exc:
        raise CaptchaSolveError(f"NopeCHA API request failed: {exc}")

    if resp.status_code != 200:
        raise CaptchaSolveError(
            f"NopeCHA API returned HTTP {resp.status_code}: {resp.text[:200]}"
        )

    try:
        body = resp.json()
    except json.JSONDecodeError:
        raise CaptchaSolveError("NopeCHA API returned a non-JSON response")

    job_id = body.get("data")
    if not job_id:
        msg = body.get("message", "unknown error")
        raise CaptchaSolveError(f"NopeCHA API error on submission: {msg}")

    if log:
        log.info("NopeCHA: job submitted, polling for token...")

    # Step 2 -- poll for the token.
    while time.monotonic() < deadline:
        try:
            poll = requests.get(
                NOPECHA_API_URL,
                params={"key": key, "id": job_id},
                timeout=_HTTP_TIMEOUT_S,
            )
        except requests.exceptions.Timeout:
            if log:
                log.debug("NopeCHA: poll timed out, retrying...")
            time.sleep(_POLL_INTERVAL_S)
            continue
        except requests.exceptions.RequestException as exc:
            raise CaptchaSolveError(f"NopeCHA API polling failed: {exc}")

        if poll.status_code != 200:
            raise CaptchaSolveError(
                f"NopeCHA API returned HTTP {poll.status_code} during polling"
            )

        try:
            body = poll.json()
        except json.JSONDecodeError:
            raise CaptchaSolveError(
                "NopeCHA API returned a non-JSON response during polling"
            )

        # Success -- the token is in 'data'.
        token = body.get("data")
        if token:
            if log:
                log.info("NopeCHA: token received (length=%d)", len(token))
            return token

        # Still processing.
        err_code = body.get("error")
        if err_code == 14:  # "Incomplete job"
            if log:
                log.debug("NopeCHA: job still processing...")
            time.sleep(_POLL_INTERVAL_S)
            continue

        # Genuine error.
        msg = body.get("message", f"error code {err_code}")
        raise CaptchaSolveError(f"NopeCHA API error during polling: {msg}")

    raise CaptchaSolveError(
        f"NopeCHA timed out after {timeout_ms}ms waiting for a solution"
    )


# ---------------------------------------------------------------------------
# 4. Token injection
# ---------------------------------------------------------------------------

def inject_token(page: Page, captcha_type: str, token: str) -> None:
    """Write the solved token into the page and fire any declared callback.

    Raises CaptchaSolveError if the injection expression cannot execute.
    """
    try:
        success = page.evaluate(
            _INJECT_SCRIPT,
            {"captchaType": captcha_type, "token": token},
        )
    except Exception as exc:
        raise CaptchaSolveError(f"Token injection failed: {exc}")
    if not success:
        raise CaptchaSolveError(
            f"Token injection returned false for captcha_type={captcha_type}"
        )


# ---------------------------------------------------------------------------
# 5. Verification
# ---------------------------------------------------------------------------

def verify_solved(page: Page, captcha_type: str, timeout_ms: int) -> bool:
    """Poll until the widget reports a solved state or the timeout expires."""
    try:
        result = page.wait_for_function(
            _VERIFY_SCRIPT,
            arg=captcha_type,
            timeout=timeout_ms,
        )
        return bool(result.json_value())
    except PlaywrightTimeoutError:
        return False


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def solve_captcha(
    page: Page,
    config: CaptchaConfig,
    detected_type: str,
    log: logging.Logger | None = None,
    api_key: str | None = None,
) -> None:
    """Full solve pipeline: extract sitekey -> call NopeCHA -> inject -> verify.

    Raises:
        CaptchaSolveError -- if any step fails or the widget doesn't verify.
    """
    log = log or logging.getLogger("otp_automation")

    # Step 1 -- resolve the sitekey.
    sitekey = config.sitekey_override or extract_sitekey(page, detected_type)
    if not sitekey:
        raise CaptchaSolveError(
            f"Could not extract sitekey for {detected_type} widget "
            "(set captcha.sitekey_override in config as a fallback)"
        )
    log.info("captcha: sitekey extracted (length=%d) for %s", len(sitekey), detected_type)

    # Step 2 -- obtain a token from NopeCHA.
    page_url = page.url or ""
    token = solve_nopecha(
        sitekey=sitekey,
        url=page_url,
        captcha_type=detected_type,
        api_key=api_key,
        timeout_ms=config.solve_timeout_ms,
        log=log,
    )

    # Step 3 -- inject the token into the page.
    inject_token(page, detected_type, token)
    log.info("captcha: token injected into page")

    # Step 4 -- verify the widget reflects a solved state.
    if not verify_solved(page, detected_type, config.timeout_ms):
        raise CaptchaSolveError(
            f"Token injected but widget did not verify as solved "
            f"within {config.timeout_ms}ms"
        )
    log.info("captcha: widget verified as solved -- ready to submit")



# ---------------------------------------------------------------------------
# Binance slider CAPTCHA (bs-modal) — separate from reCAPTCHA/hCaptcha
# ---------------------------------------------------------------------------

NOPECHA_RECOGNITION_URL = "https://api.nopecha.com/v1/recognition/geetest"

# Returns True when the .bs-modal slider element is visible.
_SLIDER_DETECTION_SCRIPT = r"""() => {
    const modal = document.querySelector(".bs-modal");
    if (!modal) return false;
    const style = window.getComputedStyle(modal);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (modal.getClientRects().length === 0) return false;
    return true;
}"""

# Returns true when solved, false when failed, null when undetermined.
_SLIDER_VERIFY_SCRIPT = r"""() => {
    const s = document.querySelector(".thumb-success");
    if (s) {
        const d = window.getComputedStyle(s).display;
        if (d !== "none") return true;
    }
    const f = document.querySelector(".thumb-fail");
    if (f) {
        const d = window.getComputedStyle(f).display;
        if (d !== "none") return false;
    }
    return null;
}"""

# Extracts the puzzle image URL from .bs-main-image's computed background-image.
_EXTRACT_SLIDER_IMAGE_SCRIPT = r"""() => {
    const mainImage = document.querySelector(".bs-main-image");
    if (!mainImage) return null;
    const bgImage = window.getComputedStyle(mainImage).backgroundImage;
    if (!bgImage || bgImage === "none") return null;
    const urlMatch = bgImage.match(/url\(\s*["\']?(.*?)["\']?\s*\)/i);
    return urlMatch ? urlMatch[1] : null;
}"""

# Returns bounding boxes for the slider thumb and container.
_GET_SLIDER_DIMENSIONS_SCRIPT = r"""() => {
    const thumb = document.querySelector(".bs-slide-thumb");
    const container = document.querySelector(".verify-slider.bs-slide-container");
    if (!thumb || !container) return null;
    const tb = thumb.getBoundingClientRect();
    const cb = container.getBoundingClientRect();
    return {
        thumbCenterX: tb.left + tb.width / 2,
        thumbCenterY: tb.top + tb.height / 2,
        thumbLeft: tb.left,
        containerWidth: cb.width
    };
}"""


def detect_biance_slider(page: Page, timeout_ms: int) -> bool:
    """Wait for the Binance slider CAPTCHA modal (.bs-modal) to appear.

    Returns True if the modal is visible within timeout_ms, False otherwise.
    """
    try:
        result = page.wait_for_function(
            _SLIDER_DETECTION_SCRIPT,
            timeout=timeout_ms,
        )
        return bool(result.json_value())
    except PlaywrightTimeoutError:
        return False


def _wait_for_loading_complete(page: Page, timeout_ms: int = 3000) -> None:
    """Wait for the slider loading indicator to finish."""
    try:
        page.wait_for_function(
            r"""() => {
                const tipMessage = document.querySelector(".bs-tipmessage-container");
                if (!tipMessage) return true;
                const display = window.getComputedStyle(tipMessage).display;
                return display === "none";
            }""",
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError:
        pass  # proceed anyway


def _url_to_base64(url: str, timeout_s: int = 10) -> str:
    """Fetch an image URL and convert to Base64 (no data-URL prefix)."""
    import requests
    import base64

    try:
        resp = requests.get(url, timeout=timeout_s)
    except requests.exceptions.Timeout:
        raise CaptchaSolveError("Failed to download puzzle image: request timed out")
    except requests.exceptions.RequestException as exc:
        raise CaptchaSolveError(f"Failed to download puzzle image: {exc}")
    if resp.status_code != 200:
        raise CaptchaSolveError(
            f"Failed to download puzzle image: HTTP {resp.status_code}"
        )
    return base64.b64encode(resp.content).decode("ascii")


def _call_nopecha_recognition(
    image_base64: str,
    api_key: str | None,
    log: logging.Logger | None = None,
) -> str:
    """Submit image to noPecha image-recognition API; return job ID."""
    import requests

    headers: dict[str, str] = {"Content-Type": "application/json"}
    payload: dict = {
        "task": "Slide the puzzle piece to complete the image",
        "image_data": [image_base64],
    }
    if api_key:
        payload["key"] = api_key  # noPecha expects key in JSON body, NOT as Authorization header
        if log:
            log.info("slider: submitting to noPecha with API key")
    else:
        if log:
            log.warning(
                "slider: no API key found - using keyless free tier "
                "(100/day per IP; IP may be Banned if previously flagged)"
            )

    try:
        resp = requests.post(
            NOPECHA_RECOGNITION_URL,
            json=payload,
            headers=headers,
            timeout=10,
        )
    except requests.exceptions.Timeout:
        raise CaptchaSolveError("noPecha API: request timed out during submission")
    except requests.exceptions.RequestException as exc:
        raise CaptchaSolveError(f"noPecha API: request failed: {exc}")

    if resp.status_code != 200:
        raise CaptchaSolveError(
            f"noPecha API: HTTP {resp.status_code}: {resp.text[:200]}"
        )

    try:
        body = resp.json()
    except json.JSONDecodeError:
        raise CaptchaSolveError("noPecha API: returned non-JSON response")

    job_id = body.get("data")
    if not job_id:
        msg = body.get("message", "unknown error")
        raise CaptchaSolveError(f"noPecha API: {msg}")

    if log:
        log.info("slider: job submitted, id=%s", job_id)
    return job_id


def _poll_slider_solution(
    job_id: str,
    api_key: str | None,
    timeout_ms: int = 30000,
    log: logging.Logger | None = None,
) -> dict:
    """Poll noPecha until the slider solution arrives."""
    import requests

    params: dict[str, str] = {"id": job_id}
    if api_key:
        params["key"] = api_key  # noPecha expects key as query param for GET polling
    elif log:
        log.debug("slider: polling keyless (no API key)")

    deadline = time.monotonic() + (timeout_ms / 1000.0)
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        try:
            resp = requests.get(
                NOPECHA_RECOGNITION_URL,
                params=params,
                timeout=10,
            )
        except requests.exceptions.Timeout:
            if log:
                log.debug("slider: poll timed out, retrying... (%d)", attempt)
            time.sleep(_POLL_INTERVAL_S)
            continue
        except requests.exceptions.RequestException as exc:
            raise CaptchaSolveError(f"noPecha API: polling failed: {exc}")

        if resp.status_code != 200:
            raise CaptchaSolveError(
                f"noPecha API: poll HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            body = resp.json()
        except json.JSONDecodeError:
            raise CaptchaSolveError("noPecha API: poll returned non-JSON")

        solution = body.get("data")
        if solution:
            if log:
                log.info("slider: solution received, x=%.1f%%", solution.get("x", 0))
            return solution

        if log:
            log.debug("slider: still processing... (%d)", attempt)
        time.sleep(_POLL_INTERVAL_S)

    raise CaptchaSolveError(
        f"noPecha API: timed out after {timeout_ms}ms waiting for slider solution"
    )


def _drag_slider(page: Page, percentage: float, log: logging.Logger | None = None) -> None:
    """Drag the Binance slider to the specified percentage position.

    CRITICAL: Must move mouse to the thumb *before* mouse.down(), because
    Playwright's mouse starts at (0, 0) — calling down() without first
    positioning the cursor does nothing useful on the slider.
    """
    dims = page.evaluate(_GET_SLIDER_DIMENSIONS_SCRIPT)
    if not dims:
        raise CaptchaSolveError("Slider elements not found (thumb or container)")

    if dims["containerWidth"] <= 0:
        raise CaptchaSolveError("Slider container width is invalid")

    target_distance = (percentage / 100) * dims["containerWidth"]
    # Target x = where the thumb's LEFT edge should end up
    target_x = dims["thumbLeft"] + target_distance

    if log:
        log.info(
            "slider: dragging (start_x=%dpx, start_y=%dpx, target_x=%dpx, "
            "distance=%dpx, container=%dpx, pct=%.1f%%)",
            int(dims["thumbCenterX"]),
            int(dims["thumbCenterY"]),
            int(target_x),
            int(target_distance),
            int(dims["containerWidth"]),
            percentage,
        )

    # Step 1: Move mouse to the center of the thumb FIRST (fixes the core bug)
    page.mouse.move(dims["thumbCenterX"], dims["thumbCenterY"])

    # Step 2: Press mouse down on the thumb
    page.mouse.down()

    # Step 3: Drag to target position with smooth multi-step movement
    page.mouse.move(target_x, dims["thumbCenterY"], steps=20)

    # Step 4: Release
    page.mouse.up()

    if log:
        log.info("slider: drag completed (released at x=%dpx)", int(target_x))


def _verify_slider_solved(page: Page, config: CaptchaConfig | None = None, timeout_ms: int = 5000) -> bool:
    """Check if the slider CAPTCHA was solved successfully.

    Success signals:
      - .thumb-success icon becomes visible, OR
      - .bs-modal disappears from the DOM (puzzle cleared)
    Failure signals:
      - .thumb-fail icon becomes visible
    """
    timeout = timeout_ms if config is None else max(timeout_ms, config.timeout_ms)

    # Give the page a moment to process the drag before checking
    time.sleep(1.0)

    _VERIFY_SCRIPT = r"""() => {
        // Success: thumb-success icon is visible
        const s = document.querySelector(".thumb-success");
        if (s) {
            const d = window.getComputedStyle(s).display;
            if (d !== "none") return true;
        }
        // Failure: thumb-fail icon is visible
        const f = document.querySelector(".thumb-fail");
        if (f) {
            const d = window.getComputedStyle(f).display;
            if (d !== "none") return false;
        }
        // Success: modal disappeared (puzzle cleared)
        const modal = document.querySelector(".bs-modal");
        if (!modal) return true;
        const style = window.getComputedStyle(modal);
        if (style.display === "none" || style.visibility === "hidden") return true;
        // Not yet determined
        return null;
    }"""

    try:
        result = page.wait_for_function(_VERIFY_SCRIPT, timeout=timeout_ms)
        val = result.json_value()
        if val is True:
            return True
        if val is False:
            return False
        return False
    except PlaywrightTimeoutError:
        # Last-ditch: check if modal is gone
        modal_gone = page.evaluate(r"""() => !document.querySelector(".bs-modal")""")
        if modal_gone:
            return True
        return False


def solve_biance_slider(
    page: Page,
    config: CaptchaConfig,
    log: logging.Logger | None = None,
) -> bool:
    """Full slider-solve pipeline: load → extract → noPecha → poll → drag → verify.

    Returns True if the slider was solved, raises CaptchaSolveError on failure.
    """
    log = log or logging.getLogger("otp_automation")

    # Step 0: wait for loading indicator to finish
    _wait_for_loading_complete(page, timeout_ms=3000)

    # Step 1: extract the puzzle image URL
    image_url = page.evaluate(_EXTRACT_SLIDER_IMAGE_SCRIPT)
    if not image_url:
        raise CaptchaSolveError("Could not extract puzzle image URL from .bs-main-image")

    # Convert URL to Base64
    base64_image = _url_to_base64(image_url)
    log.info("slider: image extracted (size=%dKB)", len(base64_image) // 1024)

    # Step 2: submit to noPecha recognition API
    api_key = os.environ.get(ENV_API_KEY)
    job_id = _call_nopecha_recognition(base64_image, api_key, log)

    # Step 3: poll for the solution
    solution = _poll_slider_solution(
        job_id, api_key, timeout_ms=config.solve_timeout_ms, log=log
    )

    # Step 4: drag the slider
    _drag_slider(page, solution.get("x", 0), log)

    # Step 5: verify success
    solved = _verify_slider_solved(page, config, config.timeout_ms)
    if not solved:
        # Retry: noPecha solutions can be slightly off. Try nudging +10px and -5px.
        for retry_pct in [solution.get("x", 0) + 5, solution.get("x", 0) - 3]:
            retry_pct = max(1.0, min(99.0, retry_pct))
            if log:
                log.warning("slider: first attempt failed, retrying at %.1f%%", retry_pct)
            _drag_slider(page, retry_pct, log)
            solved = _verify_slider_solved(page, config, config.timeout_ms)
            if solved:
                if log:
                    log.info("slider: CAPTCHA solved on retry!")
                return True

        raise CaptchaSolveError("Slider CAPTCHA was not solved (verification failed after retries)")

    log.info("slider: CAPTCHA solved!")
    return True
