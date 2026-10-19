# Offline Phone-Number Validation and Reporting

This project validates phone numbers from CSV or Excel files and creates a masked report. It is deliberately offline-only: it does not open a browser, contact Binance, send OTP requests, or perform authentication.

## Architecture and workflow

- `otp_report.phone_numbers`: normalizes numbers to E.164 and masks them.
- `otp_report.input_processing`: marks entries as eligible, duplicate, empty, or invalid.
- `otp_report.classification`: pure helper for classifying explicit response text; it does not interact with a platform.
- `otp_report.reporting`: creates the output report without writing full phone numbers.
- `otp_report.cli`: reads CSV/XLSX input and runs the offline workflow.
- `tests`: unit tests for phone-number validation, deduplication, masking, and conservative response classification.

Each input row appears in the report. Its `status` remains `UNKNOWN` and its response category is `NOT_RUN_OFFLINE`, because no platform was contacted. `input_result` indicates whether the number is eligible for a separately authorized, controlled test.

## Requirements and installation (Windows PowerShell)

Python 3.10 or later is recommended.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Input format and execution

The input file must be `.csv` or `.xlsx` and contain a phone-number column. The tool automatically recognizes `phone`, `phone_number`, `mobile`, or `number`; otherwise, supply the column name explicitly.

```powershell
python -m otp_report.cli ".\last algeria 5k.csv" --output ".\offline_report.xlsx"
```

For a different input column:

```powershell
python -m otp_report.cli ".\numbers.xlsx" --phone-column "Test Number" --output ".\offline_report.csv"
```

Local numbers use Algeria's country calling code (`213`) by default. To override the country calling code, use `--default-country-code`, or set `DEFAULT_COUNTRY_CODE`:

```powershell
$env:DEFAULT_COUNTRY_CODE = "1"
python -m otp_report.cli ".\numbers.csv" --output ".\offline_report.xlsx"
```

The report masks numbers, preserves source-row references, reports empty/malformed/duplicate entries, and includes UTC execution timestamps and a zero duration for this non-execution workflow. Full input numbers are never copied into the report or logged. Keep the original input file secured because it may contain personal data.

## Example input

`example_numbers.xlsx` is an Excel input template containing synthetic numbers from the reserved `202-555-01xx` range. `example_numbers.csv` contains the same sample data in CSV format.

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Assumptions and limitations

- Accepted numbers are normalized to E.164. Local numbers are interpreted using the configured default country calling code; Algeria's domestic leading zero is removed when the default is `213`.
- Duplicate detection uses normalized values, so differently formatted versions of the same number are treated as duplicates.
- Response classification returns success or failure only when exactly one category has an explicit configured phrase; missing or conflicting evidence is `UNKNOWN`.
- This project does not automate OTP requests or support live account registration. Use a provider-issued sandbox and synthetic test numbers for any future authorized end-to-end testing, and do not use real user lists against production services.
- This template and report do not contain OTPs, passwords, session tokens, or unmasked phone numbers.
