"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .excel_io import read_test_cases
from .logging_setup import configure_logging
from .models import ExecutionResult, SkippedItem
from .phone_utils import mask_phone
from .reporter import format_summary, summarize, write_report
from .runner import OtpRunner
from .template import create_template


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="otp-automation",
        description="Automate OTP requests on an authorized test platform.",
    )
    parser.add_argument("--config", default="config/config.yaml", help="Path to YAML config.")
    parser.add_argument("--url", help="Target platform URL (overrides config).")
    parser.add_argument("--input", dest="input_file", help="Input .csv / .xlsx file (overrides config).")
    parser.add_argument("--phone-column", dest="phone_column", help="Phone column name (e.g. 'Number', 'phone').")
    parser.add_argument("--country-code", dest="country_code", help="Calling code (e.g. '269' for Comoros).")
    parser.add_argument("--country-name", dest="country_name", help="Country name for dropdown (e.g. 'Comoros').")
    parser.add_argument("--output", dest="output_file", help="Output report path.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=None,
                        help="Run the browser headless (default: from config).")
    parser.add_argument("--log-level", default=None, help="DEBUG | INFO | WARNING | ERROR")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only validate input and print eligible cases.")
    parser.add_argument("--make-template", action="store_true",
                        help="Create an example input workbook and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.make_template:
        path = create_template()
        print(f"Template written to: {path.resolve()}")
        return 0

    cfg = load_config(
        args.config,
        url=args.url,
        input_file=args.input_file,
        phone_column=args.phone_column,
        country_code=args.country_code,
        country_name=args.country_name,
        output_file=args.output_file,
        headless=args.headless,
        log_level=args.log_level,
    )
    log = configure_logging(cfg.log_level, cfg.log_file)

    log.info("Target URL        : %s", cfg.target.url or "<EMPTY - set target.url or pass --url>")
    log.info("Input file        : %s", Path(cfg.input.file).resolve())
    log.info("Output report     : %s", Path(cfg.reporting.output_file).resolve())
    log.info("Headless browser  : %s", cfg.browser.headless)

    try:
        cases, skipped = read_test_cases(
            cfg.input.file,
            sheet=cfg.input.sheet,
            column=cfg.input.column,
            country_code=cfg.input.country_code,
            min_digits=cfg.input.min_digits,
            max_digits=cfg.input.max_digits,
        )
    except (FileNotFoundError, ValueError) as exc:
        log.error("Input error: %s", exc)
        return 2

    for item in skipped:
        log.warning("Skipping row %d (%s): %s",
                    item.row, item.reason, (item.raw[:24] + "…") if len(item.raw) > 24 else item.raw)

    log.info("Eligible test cases: %d (skipped %d)", len(cases), len(skipped))

    if args.dry_run:
        for c in cases:
            log.info("  row %-4d  %s", c.row, mask_phone(c.normalized))
        return 0

    if not cases:
        log.warning("Nothing to do.")
        return 0

    if not cfg.target.url.strip():
        log.error("target.url is empty. Set it in config/config.yaml, via --url, "
                  "or the OTP_TARGET_URL environment variable.")
        return 2

    results: list[ExecutionResult] = []

    def _save_progress(current_results: list[ExecutionResult]) -> Path:
        all_report_items: list[Any] = list(current_results) + list(skipped)
        all_report_items.sort(key=lambda x: x.test_case.row if isinstance(x, ExecutionResult) else x.row)
        return write_report(
            all_report_items,
            cfg.reporting.output_file,
            also_write_csv=cfg.reporting.also_write_csv,
        )

    try:
        with OtpRunner(cfg, log) as runner:
            results = runner.run(cases, on_case_complete=_save_progress)
    except KeyboardInterrupt:
        log.warning("Run interrupted by user (Ctrl+C). Finalizing report with completed cases...")
    except Exception as exc:  # noqa: BLE001 - top-level guard
        log.exception("Fatal error during execution: %s", exc)
        return 1
    finally:
        if results or skipped:
            report_path = _save_progress(results)
            log.info("Report written: %s", report_path.resolve())

    summary = summarize(results)
    print(format_summary(summary))
    return 0 if summary["counts"]["ERROR"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())