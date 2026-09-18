# ClearLedger Handover & Engineering Report

## Executive Summary

ClearLedger was audited against [`BUSINESS_RULES.md`](file:///d:/Mystri/BUSINESS_RULES.md) and [`fixtures/README.md`](file:///d:/Mystri/fixtures/README.md). All **six deliberately seeded defects** across the import, matching, storage, reporting, and browser workflows were identified, prioritized, repaired, and rigorously verified. In addition, an intuitive **Customer Register Filter** improvement was implemented and tested to solve the owner's workflow bottleneck when managing individual client accounts.

All existing records in the owner's register (`fixtures/existing-register.sqlite3`) have been preserved intact, and the application has been verified to support new imports and persist state seamlessly across server restarts.

---

## 1. Investigation & Prioritization

The application was inspected against the business specification and the owner's observations. Six distinct defects were reproduced:

| Priority | Defect Area | Location | Owner Observation / Impact | Root Cause |
| :--- | :--- | :--- | :--- | :--- |
| **P0** | **Invoice Idempotency & Conflict** | [`ledger/storage.py`](file:///d:/Mystri/ledger/storage.py) | *"When I retry an import, the numbers sometimes move again."* Data corruption & duplicate billing. | `insert_invoice` lacked deduplication. Retrying an import inserted duplicate invoice records, doubling balances and distorting totals. Conflicting updates were also inserted rather than rejected. |
| **P0** | **Payment Allocation Integrity** | [`ledger/matching.py`](file:///d:/Mystri/ledger/matching.py) | Severe misallocation of funds across customers. | `find_invoice` matched payments to *any* candidate invoice that matched `amount`, cross-matching payments to different customers and masking unmatched payments. |
| **P1** | **Batch Import Row Isolation** | [`ledger/importing.py`](file:///d:/Mystri/ledger/importing.py) | *"An import said it was complete, but I couldn't find the records I expected."* | A list comprehension validated all rows prior to insertion (`[normalize(...) for row in reader]`). A single invalid row (e.g. `samples/invoices-mixed.csv`) raised `ValueError`, aborting the entire batch with HTTP 400. |
| **P1** | **Open Invoices Filter Inversion** | [`ledger/reporting.py`](file:///d:/Mystri/ledger/reporting.py) | *"The open-invoice view doesn't seem to agree with the overview."* Revenue loss from missed collections. | `requested = {'open': 'paid', 'paid': 'paid'}[status]` mapped `'open'` to `'paid'`, displaying paid invoices when the user asked for open invoices. |
| **P2** | **Export CSV Float Truncation** | [`ledger/reporting.py`](file:///d:/Mystri/ledger/reporting.py) | *"The downloaded report and the screen don't always agree."* Discrepancy in tax and accounting exports. | `int(item[key] * 100) / 100` caused IEEE-754 binary floating-point truncation (e.g., `19.99 * 100 = 1998.999...` truncated to `19.98`). |
| **P2** | **Browser Feedback & Error Visibility** | [`web/app.js`](file:///d:/Mystri/web/app.js) | Complete opacity on upload results; claimed success even when uploads failed. | `submitImport` never checked `response.ok`, did not parse the JSON response, ignored errors, and unconditionally displayed `"Import complete. Your records are ready."` regardless of outcome. |

### Prioritization Rationale
1. **P0 (Data & Financial Integrity)**: Duplicate invoices and misallocated payments corrupt the database and ledger state irreversibly. These were resolved first to ensure a reliable foundation.
2. **P1 (Core Operational Workflows)**: The inability to import files containing errors and an inverted open-invoice view directly prevent daily billing operations.
3. **P2 (Export Fidelity & User Experience)**: Floating-point truncation and false browser success messages break user trust and accounting parity between UI and exports.

---

## 2. Repairs & Architecture Changes

### A. Strict Payment Matching (`ledger/matching.py`)
- Removed arbitrary `amount_match` logic.
- Matched payments strictly against `(customer_id, invoice_number)` using `invoice_by_key(db, payment['customer_id'], payment['invoice_number'])`.
- Returns `None` if unreferenced, correctly persisting unmatched payments with `invoice_id = NULL`.

### B. Storage & Idempotent Ingestion (`ledger/storage.py`)
- Added unique index: `CREATE UNIQUE INDEX IF NOT EXISTS idx_invoices_customer_invoice ON invoices (customer_id, invoice_number);`.
- In `insert_invoice`:
  - Checked `invoice_by_key(db, customer_id, invoice_number)`.
  - If identical details (`amount` and `due_date` match at currency precision): returns `'skipped'`.
  - If existing key with different details: raises `ValueError` preserving original data.
  - If new: inserts record and returns `'imported'`.
- In `insert_payment`:
  - Updated amount comparison to use 2-decimal rounded equality (`round(float(old['amount']), 2) == round(float(row['amount']), 2)`), preventing floating-point false mismatches.

### C. Row-Level Import Resilience (`ledger/importing.py`)
- Replaced pre-loop list comprehension with iterative parsing: `for line, raw_row in enumerate(reader, 2):`.
- Enclosed per-row normalization and insertion in `try...except ValueError`.
- Valid rows are committed; invalid rows increment `rejected` and capture the 1-based CSV line number and reason in `result['errors']`.
- Valid headers with no data rows return zero counts; invalid headers reject the entire file with HTTP 400.

### D. Exact Monetary Precision & Filtering (`ledger/reporting.py`)
- Fixed status filtering: `if status != 'all': result = [r for r in result if r['status'] == status]`.
- Standardized currency rounding across all queries (`round(float(val), 2)`), ensuring balances and overview outstanding preserve cents without float drift.
- Fixed `export_csv` formatting using `f"{row[key]:.2f}"`, ensuring values like `19.99` export as `19.99` rather than `19.98`.

### E. Honest & Diagnostic Browser Feedback (`web/app.js`, `web/index.html`)
- Updated `submitImport`:
  - Validates file presence before upload.
  - Checks `response.ok`. If false, displays `Import failed: <reason>`.
  - If successful, parses JSON and renders exact counts: `Import complete: X imported, Y skipped, Z rejected.`
  - Appends detailed bullet list of rejected lines and reasons (`• Line 3: amount must be a positive decimal...`).
  - Calls `refresh()` to sync metrics and register.

---

## 3. Implemented Product Improvement: Customer Register Filter

### Problem It Solves
The business owner frequently interacts with specific clients (`HARBOR`, `MAPLE`, `NORTH`) to review invoices or follow up on overdue payments. Previously, the register displayed all clients intermixed; reviewing a specific client required visually scanning or manually filtering an export.

### Implementation
1. **Backend (`ledger/reporting.py`)**: Extended `invoices(db, status='all', customer=None)`. Validates `customer` against known customer IDs (returning HTTP 400 / `ValueError` for unknown IDs). Filters rows by `r['customer_id'] == customer`.
2. **HTTP API (`ledger/http_app.py`)**: `GET /api/invoices?status=...&customer=HARBOR` parses the optional `customer` query parameter while strictly maintaining backward compatibility.
3. **Frontend (`web/index.html`, `web/app.js`)**: Added a `<select id="customer">` dropdown in the register actions bar. Selecting a customer re-queries `/api/invoices` and refreshes the register table in real time.

### Automated Checks
Covered in `tests/test_improvement.py`:
- `test_filter_by_valid_customer`: Verifies filtering by `HARBOR` returns only HARBOR invoices.
- `test_filter_by_customer_and_status`: Combines `status='open'` with `customer='HARBOR'`.
- `test_filter_by_invalid_customer_raises`: Unknown customer ID raises `ValueError('Unknown customer_id')` (HTTP 400).
- `test_default_customer_none_returns_all`: Omitted filter returns all invoices.

---

## 4. Verification Evidence & Reproductions

### A. Failing-Before / Passing-After Reproductions

1. **Defect 1 (Status Filter)**:
   - *Before*: `invoices(db, status='open')` returned 1 record (`INV-101`, status `paid`).
   - *After*: Returns 5 records (`INV-100`, `INV-200`, `INV-300`, `INV-201`, `INV-301`), all with status `open`.
   - *Test*: `test_defect1_open_invoices_filter` in `tests/test_defects.py`.

2. **Defect 2 (Export Cents Truncation)**:
   - *Before*: `export_csv(db)` emitted `NORTH,INV-300,19.98,10.00,9.98,open`.
   - *After*: Emits `NORTH,INV-300,19.99,10.00,9.99,open`.
   - *Test*: `test_defect2_export_csv_preserves_cents` in `tests/test_defects.py`.

3. **Defect 3 (Payment Matching by Amount)**:
   - *Before*: Payment for non-existent invoice with amount `1250.00` matched `HARBOR INV-100`.
   - *After*: Retained as unmatched (`invoice_id = None`); invoice balances unchanged.
   - *Test*: `test_defect3_payment_matching_strictly_by_key` in `tests/test_defects.py`.

4. **Defect 4 (Invoice Duplicate Ingestion)**:
   - *Before*: Re-importing `HARBOR,INV-100,1250.00,2026-09-01` added a second row, incrementing total invoices from 6 to 7.
   - *After*: Returns `skipped: 1`, total invoices remain 6, outstanding remains 3209.99.
   - *Test*: `test_defect4_invoice_import_idempotency_and_conflict` in `tests/test_defects.py`.

5. **Defect 5 (Row-Level Validation)**:
   - *Before*: Importing `samples/invoices-mixed.csv` failed with HTTP 400; 0 rows imported.
   - *After*: Returns `{"imported": 2, "skipped": 0, "rejected": 1, "errors": [{"line": 3, "reason": "..."}]}`; valid invoices `INV-103` and `INV-203` are saved.
   - *Test*: `test_defect5_row_level_validation_invoices_mixed` in `tests/test_defects.py`.

### B. Custom Input Cases Designed
1. **Overpayment Accounting (`test_custom_overpayment_handling`)**:
   - Paid INR 150.00 against `NORTH INV-301` (amount 100.00).
   - Balance becomes `-50.00`, status is `'paid'`.
   - Verified that overview total outstanding is not reduced by the overpayment, preserving positive balance summation.
2. **Multi-Payment Cents Aggregation (`test_custom_multiple_payments_exact_cents`)**:
   - Applied three split payments: `250.33`, `250.33`, and `250.34` against `INV-100`.
   - Verified total paid is exactly `751.00` and balance is `499.00` with zero floating point drift.
3. **Invalid Header vs. All-Invalid Rows (`test_defect5_header_rejection_vs_all_invalid_rows`)**:
   - Verified invalid header throws `ValueError` (HTTP 400).
   - Verified valid header with all invalid rows returns HTTP 200 with `imported: 0, rejected: 3`.

### C. Owner Register Preservation Verification
Verified via `tests/test_preservation.py`:
- Restored `fixtures/existing-register.sqlite3` to `.local/clearledger.sqlite3`.
- Verified starting totals: 3 customers, 9 invoices, 5 payments, 7 open invoices, INR 3,698.19 outstanding, 1 unmatched payment (`KEEP-U1`).
- Imported new records: `samples/invoices-new.csv` (2 invoices) and `samples/payments.csv` (3 payments).
- Reopened database connection (simulating server restart).
- Confirmed all 9 original invoices + 2 new invoices (total 11) and 5 original payments + 3 new payments (total 8) persist correctly.
- Original fixture files in `fixtures/` remain bit-for-bit identical (hashes verified).

---

## 5. Execution and Test Results

### Test Suite Execution
```text
python -m unittest discover -s tests -v
```

```text
test_custom_multiple_payments_exact_cents (test_defects.DefectRegressionTests.test_custom_multiple_payments_exact_cents) ... ok
test_custom_overpayment_handling (test_defects.DefectRegressionTests.test_custom_overpayment_handling) ... ok
test_defect1_open_invoices_filter (test_defects.DefectRegressionTests.test_defect1_open_invoices_filter) ... ok
test_defect2_export_csv_preserves_cents (test_defects.DefectRegressionTests.test_defect2_export_csv_preserves_cents) ... ok
test_defect3_payment_matching_strictly_by_key (test_defects.DefectRegressionTests.test_defect3_payment_matching_strictly_by_key) ... ok
test_defect4_invoice_import_idempotency_and_conflict (test_defects.DefectRegressionTests.test_defect4_invoice_import_idempotency_and_conflict) ... ok
test_defect5_header_rejection_vs_all_invalid_rows (test_defects.DefectRegressionTests.test_defect5_header_rejection_vs_all_invalid_rows) ... ok
test_defect5_row_level_validation_invoices_mixed (test_defects.DefectRegressionTests.test_defect5_row_level_validation_invoices_mixed) ... ok
test_export_endpoint (test_http.HttpIntegrationTests.test_export_endpoint) ... ok
test_import_invalid_header_returns_400 (test_http.HttpIntegrationTests.test_import_invalid_header_returns_400) ... ok
test_import_partial_success (test_http.HttpIntegrationTests.test_import_partial_success) ... ok
test_invoices_status_and_customer_endpoints (test_http.HttpIntegrationTests.test_invoices_status_and_customer_endpoints) ... ok
test_overview_endpoint (test_http.HttpIntegrationTests.test_overview_endpoint) ... ok
test_default_customer_none_returns_all (test_improvement.CustomerFilterImprovementTests.test_default_customer_none_returns_all) ... ok
test_filter_by_customer_and_status (test_improvement.CustomerFilterImprovementTests.test_filter_by_customer_and_status) ... ok
test_filter_by_invalid_customer_raises (test_improvement.CustomerFilterImprovementTests.test_filter_by_invalid_customer_raises) ... ok
test_filter_by_valid_customer (test_improvement.CustomerFilterImprovementTests.test_filter_by_valid_customer) ... ok
test_existing_register_matches_expected_reference (test_preservation.ExistingRegisterPreservationTests.test_existing_register_matches_expected_reference) ... ok
test_new_imports_and_persistence_across_restart (test_preservation.ExistingRegisterPreservationTests.test_new_imports_and_persistence_across_restart) ... ok
test_export_has_header (test_smoke.SmokeTests.test_export_has_header) ... ok
test_one_valid_invoice (test_smoke.SmokeTests.test_one_valid_invoice) ... ok
test_payment_reference_when_amount_is_unique (test_smoke.SmokeTests.test_payment_reference_when_amount_is_unique) ... ok
test_seed_is_repeatable (test_smoke.SmokeTests.test_seed_is_repeatable) ... ok
test_seed_summary (test_smoke.SmokeTests.test_seed_summary) ... ok

----------------------------------------------------------------------
Ran 24 tests in 0.733s

OK
```

### Running the Application

1. **Restore Existing Register**:
   ```bash
   python restore_fixture.py --replace
   ```
2. **Start Server**:
   ```bash
   python app.py
   ```
   Open `http://127.0.0.1:8787` in any modern web browser.
3. **Reset to Synthetic Demo (Optional)**:
   ```bash
   python app.py reset-demo
   ```

---

## 6. Remaining Limits & Recommended Next Steps

- **Customer-Specific CSV Export**: While `GET /api/invoices?customer=HARBOR` supports customer filtering, `GET /api/export` currently exports all invoices. A customer query parameter on the export endpoint could be added in the next release.
- **Unmatched Payment Rematching**: As noted in `BUSINESS_RULES.md`, automatic rematching of historical unmatched payments when a matching invoice is imported later is outside the current scope; an explicit reconciliation action could be introduced in the future.
