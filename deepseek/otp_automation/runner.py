"""Browser automation runner using Playwright."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from .captcha import (
    CaptchaDetectedError,
    CaptchaSolveError,
    detect_captcha,
    detect_biance_slider,
    solve_captcha,
    solve_biance_slider,
)
from .classifier import baseline_keywords, classify_text
from .models import AppConfig, ExecutionResult, TestCase, TestStatus
from .phone_utils import mask_phone


class OtpRunner:
    """Manages browser lifecycle and executes test cases using Playwright."""

    def __init__(self, config: AppConfig, log: logging.Logger | None = None) -> None:
        self.config = config
        self.log = log or logging.getLogger("otp_automation")
        self.playwright: Any = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._target_url: str = self._resolve_target_url(self.config.target.url)

    def _resolve_target_url(self, raw_url: str) -> str:
        """Resolve target URL, supporting web URLs (http/https) and local files (e.g. test.html)."""
        url_str = str(raw_url or "").strip()
        if not url_str:
            return ""

        if url_str.startswith(("http://", "https://", "file://")):
            return url_str

        # Check local file candidate paths
        candidate_paths = [
            Path(url_str),
            Path.cwd() / url_str,
            Path.cwd() / "deepseek" / url_str,
            Path(__file__).parent.parent.parent / url_str,
            Path(__file__).parent.parent.parent / "test.html",
        ]
        for candidate in candidate_paths:
            if candidate.exists() and candidate.is_file():
                return candidate.resolve().as_uri()

        return url_str

    def __enter__(self) -> "OtpRunner":
        self.log.info("Starting Playwright Chromium browser (headless=%s)...", self.config.browser.headless)
        self.playwright = sync_playwright().start()

        launch_args: dict[str, Any] = {
            "headless": self.config.browser.headless,
            "slow_mo": self.config.browser.slow_mo_ms,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
            ],
        }
        if self.config.browser.channel:
            launch_args["channel"] = self.config.browser.channel

        self.browser = self.playwright.chromium.launch(**launch_args)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.log.info("Closing browser context and stopping Playwright...")
        try:
            if self.page and not self.page.is_closed():
                self.page.close()
        except Exception:
            pass

        try:
            if self.context:
                self.context.close()
        except Exception:
            pass

        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass

        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    def _create_context(self) -> BrowserContext:
        """Create a fresh browser context with configured dimensions, locale, and anti-detection."""
        if not self.browser:
            raise RuntimeError("Browser is not initialized.")

        context_args: dict[str, Any] = {
            "viewport": {
                "width": self.config.browser.viewport_width,
                "height": self.config.browser.viewport_height,
            },
            "locale": self.config.browser.locale,
            "ignore_https_errors": self.config.browser.ignore_https_errors,
        }
        if self.config.browser.user_agent:
            context_args["user_agent"] = self.config.browser.user_agent

        context = self.browser.new_context(**context_args)
        context.set_default_timeout(self.config.browser.default_timeout_ms)
        context.set_default_navigation_timeout(self.config.browser.navigation_timeout_ms)

        # Anti-bot stealth init script
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        if self.config.browser.block_resource_types:
            blocked = set(self.config.browser.block_resource_types)
            context.route(
                "**/*",
                lambda route: route.abort() if route.request.resource_type in blocked else route.continue_(),
            )

        return context

    def _ensure_country_code(self, page: Page, target_code: str, target_name: str = "Comoros") -> bool:
        """Fast and robust check and update country code dropdown if present on page."""
        if not target_code:
            return True

        clean_target = str(target_code).lstrip("+").strip()
        target_name = (target_name or "Comoros").strip()

        # Find the country selector trigger on the main form
        sdd = page.locator(".bn-sdd-input").first
        try:
            if not sdd.is_visible(timeout=1000):
                # May take up to 2.5s for React to render after typing phone digits
                sdd.wait_for(state="visible", timeout=2500)
        except Exception:
            self.log.info("Country code selector not visible on page (may not be required).")
            return False

        try:
            # Check current country code value
            current_val = ""
            try:
                input_elem = page.locator(".bn-sdd-input input, input[aria-label='Text field']").first
                if input_elem.count() > 0:
                    current_val = input_elem.get_attribute("value") or ""
            except Exception:
                pass
            if not current_val:
                try:
                    current_val = sdd.inner_text() or ""
                except Exception:
                    pass

            self.log.info("Detected country code selector value: '%s'", current_val.strip())

            # Check if country already matches target (e.g. 269 or Comoros)
            if (
                current_val.startswith(clean_target)
                or f"-{clean_target}-" in current_val
                or f"+{clean_target}" in current_val
                or (target_name and target_name.lower() in current_val.lower())
            ):
                self.log.info("Country code already matches target: %s (%s)", clean_target, target_name)
                return True

            self.log.info("Switching country code from '%s' to '%s' (%s)...", current_val.strip(), clean_target, target_name)

            # Click trigger to open 'Select country/territory code' modal
            sdd.click()
            page.wait_for_timeout(250)

            # Search box inside modal: <input placeholder="Search" aria-label="Search">
            search_input = page.locator(
                "input[placeholder='Search'], input[aria-label='Search'], .bn-sdd-innerInputContainer input"
            ).first

            try:
                search_input.wait_for(state="visible", timeout=3000)
            except Exception:
                try:
                    sdd.click(force=True)
                    search_input.wait_for(state="visible", timeout=2000)
                except Exception as e:
                    self.log.warning("Could not open country code modal: %s", e)
                    return False

            # Type dial code (e.g. 269) into search
            search_input.fill(clean_target)
            page.wait_for_timeout(200)

            # Locate Comoros option in list
            opt_candidates = [
                page.locator(f"li[id*='{clean_target}']").first,
                page.locator(f"li:has-text('+{clean_target}')").first,
                page.locator(f"li:has-text('{target_name}')").first,
                page.locator(f"[role='option']:has-text('+{clean_target}')").first,
                page.locator(f"[role='option']:has-text('{target_name}')").first,
            ]
            clicked = False
            for opt in opt_candidates:
                try:
                    if opt.count() > 0 and opt.is_visible(timeout=800):
                        opt.click()
                        self.log.info("Selected country option '%s' by click", clean_target)
                        clicked = True
                        break
                except Exception:
                    continue

            # If not clicked, press Enter in search input
            if not clicked:
                try:
                    search_input.press("Enter")
                    self.log.info("Selected country option via Enter key in search box")
                    clicked = True
                except Exception:
                    pass

            # If search input still visible, try typing target_name ("Comoros")
            try:
                if search_input.is_visible(timeout=400):
                    search_input.fill(target_name)
                    page.wait_for_timeout(200)
                    opt_by_name = page.locator(f"li:has-text('{target_name}'), li[id*='{target_name}']").first
                    if opt_by_name.is_visible(timeout=800):
                        opt_by_name.click()
                    else:
                        search_input.press("Enter")
            except Exception:
                pass

            # Ensure modal closed
            try:
                modal = page.locator(".bn-sdd-dropdown.showing, input[placeholder='Search']").first
                if modal.is_visible(timeout=400):
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(200)
            except Exception:
                pass

            # Verify the updated country value
            new_val = ""
            try:
                input_elem = page.locator(".bn-sdd-input input, input[aria-label='Text field']").first
                if input_elem.count() > 0:
                    new_val = input_elem.get_attribute("value") or ""
            except Exception:
                pass
            if not new_val:
                try:
                    new_val = sdd.inner_text() or ""
                except Exception:
                    pass

            new_val = new_val.strip()
            if clean_target in new_val or target_name.lower() in new_val.lower():
                self.log.info("Country code successfully updated to: '%s'", new_val)
                return True
            else:
                self.log.warning("Country code still '%s' after switching attempt (target: %s)", new_val, clean_target)
                return False

        except Exception as exc:
            self.log.warning("Could not select country code %s: %s", clean_target, exc)

        return False

    def run_case(self, case: TestCase) -> ExecutionResult:
        """Execute a single test case through the automation workflow."""
        start_time = time.monotonic()
        context: BrowserContext | None = None
        page: Page | None = None

        try:
            if self.config.browser.fresh_context_per_case or not self.context or not self.page or self.page.is_closed():
                if self.page and not self.page.is_closed():
                    self.page.close()
                if self.context:
                    self.context.close()
                self.context = self._create_context()
                self.page = self.context.new_page()

            page = self.page
            context = self.context

            self.log.info("Row %-4d: Navigating to %s for %s", case.row, self._target_url, mask_phone(case.normalized))
            page.goto(
                self._target_url,
                timeout=self.config.browser.navigation_timeout_ms,
                wait_until="domcontentloaded",
            )

            # Allow page scripts and React hydration to finish charging
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(800)

            # Dismiss cookie banner if present so it doesn't obstruct interaction
            for cookie_sel in [
                "#onetrust-accept-btn-handler",
                "#onetrust-reject-all-handler",
                "button:has-text('Accept Cookies')",
                "button:has-text('Reject Additional Cookies')",
            ]:
                try:
                    c_btn = page.locator(cookie_sel).first
                    if c_btn.is_visible(timeout=1000):
                        c_btn.click()
                        break
                except Exception:
                    pass

            # Collect baseline text if configured
            baseline: set[str] = set()
            if self.config.detection.ignore_baseline_matches:
                try:
                    container = page.locator(self.config.selectors.text_container)
                    container_text = container.inner_text(timeout=2000) if container.count() > 0 else page.inner_text("body")
                    baseline = baseline_keywords(container_text, self.config.detection)
                except Exception:
                    baseline = set()

            # Locate phone input
            phone_locator = None
            for sel in self.config.selectors.phone_input:
                loc = page.locator(sel).first
                try:
                    if loc.is_visible(timeout=4000):
                        phone_locator = loc
                        break
                except Exception:
                    continue

            if not phone_locator:
                # Fallback: try finding first visible textbox/input
                for sel in self.config.selectors.phone_input:
                    try:
                        loc = page.wait_for_selector(sel, state="visible", timeout=self.config.browser.default_timeout_ms)
                        if loc:
                            phone_locator = page.locator(sel).first
                            break
                    except Exception:
                        continue

            if not phone_locator:
                raise RuntimeError(
                    f"Phone input not found. Attempted selectors: {self.config.selectors.phone_input}"
                )

            # Wait for phone input to be ready and interactive
            phone_locator.click()
            page.wait_for_timeout(200)
            phone_locator.fill("")

            # Type the phone number to trigger the dynamic country selector
            typing_delay = self.config.selectors.typing_delay_ms if self.config.selectors.typing_delay_ms > 0 else 35
            try:
                phone_locator.press_sequentially(case.normalized, delay=typing_delay)
            except AttributeError:
                phone_locator.type(case.normalized, delay=typing_delay)

            # Check if React late-hydration wiped the input field, and re-type if needed
            page.wait_for_timeout(500)
            if phone_locator.input_value() != case.normalized:
                self.log.info("Input was reset during page charging. Re-entering phone number %s", mask_phone(case.normalized))
                page.wait_for_timeout(400)
                phone_locator.click()
                phone_locator.press_sequentially(case.normalized, delay=typing_delay)
                page.wait_for_timeout(400)

            # Allow React to render the dynamic country selector (.bn-sdd-input)
            try:
                page.locator(".bn-sdd-input").first.wait_for(state="visible", timeout=3000)
            except Exception:
                page.wait_for_timeout(350)

            # Change country code from default (e.g. Morocco +212) to target (e.g. Comoros 269) BEFORE submit
            target_cc = getattr(self.config.selectors, "country_code", "") or self.config.input.country_code or "269"
            target_cname = getattr(self.config.selectors, "country_name", "") or getattr(self.config.input, "country_name", "Comoros") or "Comoros"
            self._ensure_country_code(page, target_code=target_cc, target_name=target_cname)

            # Post-check: ensure phone input didn't lose any digits
            if phone_locator.input_value() != case.normalized:
                self.log.info("Re-aligning phone input to full number: %s", mask_phone(case.normalized))
                phone_locator.click()
                phone_locator.fill(case.normalized)
                page.wait_for_timeout(200)

            # Final check before submit: verify country code is strictly target (Comoros 269)
            clean_target = str(target_cc).lstrip("+").strip()
            final_cc_input = page.locator(".bn-sdd-input input, input[aria-label='Text field']").first
            if final_cc_input.count() > 0 and final_cc_input.is_visible():
                current_cc_val = final_cc_input.get_attribute("value") or ""
                if clean_target not in current_cc_val and target_cname.lower() not in current_cc_val.lower():
                    self.log.warning("Country code still '%s', enforcing switch to %s (%s)...", current_cc_val, clean_target, target_cname)
                    self._ensure_country_code(page, target_code=target_cc, target_name=target_cname)
                    if phone_locator.input_value() != case.normalized:
                        phone_locator.fill(case.normalized)

            # Handle agreement checkbox if required
            if self.config.selectors.agreement_required:
                for cb_sel in self.config.selectors.agreement_checkbox:
                    cb_loc = page.locator(cb_sel).first
                    try:
                        if cb_loc.count() > 0:
                            current_attr = cb_loc.get_attribute(self.config.selectors.agreement_checked_attribute)
                            if current_attr != self.config.selectors.agreement_checked_value:
                                cb_loc.click()
                            break
                    except Exception:
                        continue

            # Locate and click submit button
            submit_locator = None
            for btn_sel in self.config.selectors.submit_button:
                btn_loc = page.locator(btn_sel).first
                try:
                    if btn_loc.count() > 0 and btn_loc.is_visible():
                        submit_locator = btn_loc
                        break
                except Exception:
                    continue

            if not submit_locator:
                raise RuntimeError(
                    f"Submit button not found. Attempted selectors: {self.config.selectors.submit_button}"
                )

            if self.config.captcha.enabled:
                detected_type = detect_captcha(
                    page,
                    self.config.captcha.type,
                    self.config.captcha.timeout_ms,
                )
                if detected_type:
                    if self.config.captcha.provider == "nopecha":
                        self.log.info(
                            "Row %d: CAPTCHA detected (%s); attempting to solve via NopeCHA.",
                            case.row,
                            detected_type,
                        )
                        solve_captcha(
                            page,
                            self.config.captcha,
                            detected_type,
                            log=self.log,
                        )
                        self.log.info(
                            "Row %d: CAPTCHA solved; proceeding to submit.",
                            case.row,
                        )
                    else:
                        self.log.warning(
                            "Row %d: CAPTCHA detected (%s); submission blocked.",
                            case.row,
                            detected_type,
                        )
                        raise CaptchaDetectedError(detected_type)

            submit_locator.click()

            # Detection polling
            result_status = TestStatus.UNKNOWN
            result_category = None
            result_message = ""
            deadline = time.monotonic() + (self.config.detection.response_timeout_ms / 1000.0)
            slider_solved = False

            while time.monotonic() < deadline:
                # 1. Check failure selectors
                for sel in self.config.detection.failure_selectors:
                    try:
                        if page.locator(sel).first.is_visible(timeout=100):
                            result_status = TestStatus.FAILED
                            result_category = "dom_failure_selector"
                            result_message = f"Matched failure selector: {sel}"
                            break
                    except Exception:
                        pass
                if result_status is not TestStatus.UNKNOWN:
                    break

                # 2. Check success selectors
                for sel in self.config.detection.success_selectors:
                    try:
                        if page.locator(sel).first.is_visible(timeout=100):
                            result_status = TestStatus.SUCCESS
                            result_category = "dom_success_selector"
                            result_message = f"Matched success selector: {sel}"
                            break
                    except Exception:
                        pass
                if result_status is not TestStatus.UNKNOWN:
                    break

                # 2.5. Check for and solve Binance slider CAPTCHA (bs-modal)
                if self.config.captcha.enabled and not slider_solved:
                    slider_visible = page.evaluate(
                        r"""() => !!document.querySelector(".bs-modal")"""
                    )
                    if slider_visible:
                        self.log.info("Row %d: slider CAPTCHA detected; solving...", case.row)
                        try:
                            solve_biance_slider(page, self.config.captcha, log=self.log)
                            slider_solved = True
                            self.log.info("Row %d: slider CAPTCHA solved; continuing.", case.row)
                        except CaptchaSolveError as exc:
                            slider_solved = True
                            self.log.error("Row %d: slider CAPTCHA failed: %s", case.row, exc)
                            result_status = TestStatus.FAILED
                            result_category = "captcha_solve_failed"
                            result_message = str(exc)
                            break

                # 3. Check page text against keyword rules
                try:
                    container = page.locator(self.config.selectors.text_container)
                    text_content = container.inner_text(timeout=500) if container.count() > 0 else page.inner_text("body")
                    classified = classify_text(text_content, self.config.detection, excluded=baseline)
                    if classified:
                        result_status = classified.status
                        result_category = classified.category
                        result_message = f"Matched keyword: {classified.keyword}"
                        break
                except Exception:
                    pass

                time.sleep(self.config.detection.poll_interval_ms / 1000.0)

            duration = time.monotonic() - start_time
            screenshot_path = self._maybe_screenshot(page, case.row, result_status)

            self.log.info(
                "Row %-4d: Result -> %s (category: %s, duration: %.2fs)",
                case.row,
                result_status.value,
                result_category or "none",
                duration,
            )

            return ExecutionResult(
                test_case=case,
                status=result_status,
                category=result_category,
                message=result_message,
                error=None,
                duration_seconds=duration,
                screenshot_path=screenshot_path,
            )

        except Exception as exc:
            duration = time.monotonic() - start_time
            if isinstance(exc, CaptchaDetectedError):
                category = "captcha_required"
                self.log.warning("Row %-4d: %s", case.row, exc)
            elif isinstance(exc, CaptchaSolveError):
                category = "captcha_solve_failed"
                self.log.error("Row %-4d: %s", case.row, exc)
            else:
                category = "execution_error"
                self.log.error("Row %-4d: Error during execution: %s", case.row, exc)
            screenshot_path = self._maybe_screenshot(page, case.row, TestStatus.ERROR)

            return ExecutionResult(
                test_case=case,
                status=TestStatus.ERROR,
                category=category,
                message="",
                error=str(exc),
                duration_seconds=duration,
                screenshot_path=screenshot_path,
            )

    def _maybe_screenshot(self, page: Page | None, row: int, status: TestStatus) -> str | None:
        """Capture screenshot if configured for the resulting status."""
        if not page or page.is_closed():
            return None

        status_str = status.value if isinstance(status, TestStatus) else str(status)
        if status_str not in self.config.reporting.screenshot_on:
            return None

        try:
            screenshot_dir = Path(self.config.reporting.screenshot_dir)
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            shot_path = screenshot_dir / f"row_{row}_{status_str.lower()}.png"
            page.screenshot(
                path=str(shot_path),
                full_page=self.config.reporting.screenshot_full_page,
            )
            return str(shot_path)
        except Exception as exc:
            self.log.warning("Could not capture screenshot for row %d: %s", row, exc)
            return None

    def run(
        self,
        cases: list[TestCase],
        on_case_complete: Any | None = None,
    ) -> list[ExecutionResult]:
        """Execute all test cases with safety checks and delays."""
        results: list[ExecutionResult] = []
        consecutive_problems = 0

        for idx, case in enumerate(cases):
            try:
                result = self.run_case(case)
                results.append(result)

                if on_case_complete:
                    try:
                        on_case_complete(results)
                    except Exception as cb_err:
                        self.log.debug("on_case_complete callback error: %s", cb_err)
            except KeyboardInterrupt:
                self.log.warning(
                    "KeyboardInterrupt (Ctrl+C) received during row %d. Halting runner gracefully...",
                    case.row,
                )
                break

            # Safety tracking
            if result.status in (TestStatus.ERROR, TestStatus.UNKNOWN):
                consecutive_problems += 1
            else:
                consecutive_problems = 0

            if consecutive_problems >= self.config.safety.max_consecutive_problems:
                self.log.error(
                    "Safety trigger: Reached %d consecutive problems. Aborting remaining test cases.",
                    consecutive_problems,
                )
                break

            if self.config.safety.stop_on_first_error and result.status is TestStatus.ERROR:
                self.log.error("Safety trigger: stop_on_first_error is enabled. Halting run.")
                break

            # Delay between cases
            if idx < len(cases) - 1 and self.config.safety.delay_between_cases_ms > 0:
                time.sleep(self.config.safety.delay_between_cases_ms / 1000.0)

        return results
