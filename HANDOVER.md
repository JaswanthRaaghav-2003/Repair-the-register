# Handover

- Name: Jaswanth Raaghav
- Email used for this application: jashraaghavjara@gmail.com
- Chosen track: Track A | Repair the register
- Why this track (one or two sentences): I chose Track A to tackle real-world ledger integrity, monetary calculation drift, and resilient ingestion failures, which are fundamental to building trustworthy financial software.
- Approximate total time, including setup and handover: ~3 hours

## Run and verify

### Prerequisites
- Python 3.10+ (standard library only; zero external dependencies).

### Run Test Suite
```bash
python -m unittest discover -s tests -v
```
Expected output: 24 tests run in <1.5s with status `OK`.

### Restore Existing Register & Run Server
```bash
python restore_fixture.py --replace
python app.py
```
Open `http://127.0.0.1:8787` in any modern browser. Verify 9 invoices, 5 payments, and INR 3,698.19 outstanding.

---

## What I delivered

I audited ClearLedger against [`BUSINESS_RULES.md`](file:///d:/Mystri/BUSINESS_RULES.md) and resolved all six seeded defects across the stack:
1. **Invoice Idempotency ([`ledger/storage.py`](file:///d:/Mystri/ledger/storage.py))**: Enforced unique indexing and duplicate checking; identical imports return `'skipped'`, and conflicting details raise errors.
2. **Strict Payment Matching ([`ledger/matching.py`](file:///d:/Mystri/ledger/matching.py))**: Removed amount-based cross-customer matching; payments link strictly by `(customer_id, invoice_number)`. Unreferenced payments remain unmatched (`invoice_id = NULL`).
3. **Row-Level Ingestion ([`ledger/importing.py`](file:///d:/Mystri/ledger/importing.py))**: Replaced batch validation with row-by-row parsing, importing valid rows while capturing line numbers and reasons for bad rows.
4. **Status Filter Parity ([`ledger/reporting.py`](file:///d:/Mystri/ledger/reporting.py))**: Fixed inverted dictionary mapping so `status='open'` returns open invoices.
5. **Exact Monetary Cents ([`ledger/reporting.py`](file:///d:/Mystri/ledger/reporting.py))**: Replaced floating-point integer truncation (`int(val * 100) / 100`) with exact formatting (`f"{val:.2f}"`), ensuring values like `19.99` export accurately.
6. **Diagnostic Browser Feedback ([`web/app.js`](file:///d:/Mystri/web/app.js))**: Added HTTP status validation, JSON error parsing, count reporting (`imported`, `skipped`, `rejected`), and line-level error summaries.

**Separate Improvement**: Added a **Customer Filter** across the API (`GET /api/invoices?customer=HARBOR`), reporting, and browser UI dropdown ([`web/index.html`](file:///d:/Mystri/web/index.html)), allowing the owner to isolate individual client balances.

---

## Evidence and limits

- **Failing-Before / Passing-After**: In [`tests/test_defects.py`](file:///d:/Mystri/tests/test_defects.py), `test_defect1_open_invoices_filter` previously returned 1 paid invoice when filtering for `open`; it now correctly returns the 5 open invoices. In `test_defect2_export_csv_preserves_cents`, invoice INV-300 previously exported as `19.98`; it now exports as `19.99`.
- **Existing-Register Preservation**: [`tests/test_preservation.py`](file:///d:/Mystri/tests/test_preservation.py) confirms that starting from `fixtures/existing-register.sqlite3` preserves all 9 invoices, 5 payments, and 3,698.19 INR outstanding, allows new imports, and survives restarts.
- **Changed-Input Case**: In `test_custom_overpayment_handling`, an overpayment of 150.00 on a 100.00 invoice was expected to mark the invoice paid with a negative balance (-50.00) without reducing other invoices' outstanding totals. Verified: total outstanding stayed at 3,109.99 (not reduced to 3,059.99).
- **Separate Improvement Check**: [`tests/test_improvement.py`](file:///d:/Mystri/tests/test_improvement.py) verifies valid customer filtering, combination with `status='open'`, and rejection of unknown customer IDs with HTTP 400.
- **Assumptions & Next Steps**: Assumed single-writer concurrency (per specification). For a production system, I would investigate SQLite WAL mode for concurrent readers/writers, and add customer filtering to `GET /api/export`.

---

## Tools and judgment

1. **Schema Migration vs. Constraints**:
   - *Suggestion*: Add a destructive migration with table recreation to enforce `UNIQUE(customer_id, invoice_number)`.
   - *Judgment*: Used `CREATE UNIQUE INDEX IF NOT EXISTS` in `storage.connect`. This enforces uniqueness without risking existing data in `existing-register.sqlite3`. Verified via `test_preservation.py`.
2. **Import Batch Rollback vs. Row Isolation**:
   - *Suggestion*: Catch errors outside the loop and abort.
   - *Judgment*: Moved normalization inside the row-by-row iteration within the database transaction, catching `ValueError` per row to record line-number diagnostics while committing valid rows. Verified via `samples/invoices-mixed.csv`.
3. **AI Tool Used**: Gemini Flash (Antigravity). Code, architectural decisions, and tests were verified through unit execution.
