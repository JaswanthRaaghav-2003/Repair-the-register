"""Comprehensive regression tests for all 6 deliberately seeded defects.
Includes failing-before / passing-after reproductions and custom input cases.
"""
import io
import tempfile
import unittest
from pathlib import Path
from ledger import storage, reporting, importing, matching


class DefectRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'test_ledger.sqlite3'
        self.db = storage.connect(self.db_path)
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    # --- Defect 1: Reporting - Open invoice filter ---
    def test_defect1_open_invoices_filter(self):
        """Reproduction & regression for Defect 1:
        In the seed data, there are 6 invoices:
        - 5 open (INV-100, INV-200, INV-300, INV-201, INV-301)
        - 1 paid (INV-101: amount 300.00, paid 300.00 via SEED-1)
        Prior to fix, status='open' erroneously returned the 1 paid invoice.
        After fix, status='open' must return the 5 open invoices.
        """
        open_invs = reporting.invoices(self.db, status='open')
        self.assertEqual(len(open_invs), 5)
        for inv in open_invs:
            self.assertEqual(inv['status'], 'open')
            self.assertGreater(inv['balance'], 0)

        paid_invs = reporting.invoices(self.db, status='paid')
        self.assertEqual(len(paid_invs), 1)
        self.assertEqual(paid_invs[0]['invoice_number'], 'INV-101')
        self.assertEqual(paid_invs[0]['status'], 'paid')

        all_invs = reporting.invoices(self.db, status='all')
        self.assertEqual(len(all_invs), 6)

    # --- Defect 2: Reporting - Export CSV float truncation ---
    def test_defect2_export_csv_preserves_cents(self):
        """Reproduction & regression for Defect 2:
        Invoice NORTH / INV-300 has amount 19.99 and paid 10.00 (balance 9.99).
        Prior to fix, int(19.99 * 100) / 100 truncated 19.99 to 19.98 due to IEEE-754.
        After fix, the export CSV must accurately preserve cents: 19.99 and 9.99.
        """
        csv_output = reporting.export_csv(self.db)
        lines = csv_output.strip().splitlines()
        # Find row for INV-300
        inv_300_line = next((line for line in lines if 'INV-300' in line), None)
        self.assertIsNotNone(inv_300_line)
        self.assertEqual(inv_300_line, 'NORTH,INV-300,19.99,10.00,9.99,open')

    # --- Defect 3: Matching - Payment attached by amount instead of key ---
    def test_defect3_payment_matching_strictly_by_key(self):
        """Reproduction & regression for Defect 3:
        Prior to fix, matching searched for ANY invoice with matching amount.
        In demo data, HARBOR INV-100 and MAPLE INV-200 both have amount 1250.00.
        A payment with amount 1250.00 for an unknown invoice or for MAPLE INV-200
        should NEVER be attached based on amount alone.
        """
        # Case 3a: Payment with non-existent invoice reference but matching amount
        # must remain UNMATCHED (invoice_id is None).
        csv_unmatched = (
            'payment_id,customer_id,invoice_number,amount\n'
            'PAY-UNMATCHED,HARBOR,INV-UNKNOWN,1250.00\n'
        )
        res = importing.import_csv(self.db, csv_unmatched, 'payments')
        self.assertEqual(res['imported'], 1)

        # Invoices INV-100 and INV-200 balances must NOT change!
        inv_100 = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        inv_200 = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-200')
        self.assertEqual(inv_100['paid'], 0.0)
        self.assertEqual(inv_200['paid'], 0.0)

        # The payment must be listed in unmatched_payments
        overview = reporting.overview(self.db)
        unmatched_ids = [p['payment_id'] for p in overview['unmatched_payments']]
        self.assertIn('PAY-UNMATCHED', unmatched_ids)

        # Case 3b: Payment with matching amount 1250.00 for MAPLE INV-200
        # must attach ONLY to MAPLE INV-200, NOT to HARBOR INV-100!
        csv_maple = (
            'payment_id,customer_id,invoice_number,amount\n'
            'PAY-MAPLE,MAPLE,INV-200,1250.00\n'
        )
        res_maple = importing.import_csv(self.db, csv_maple, 'payments')
        self.assertEqual(res_maple['imported'], 1)
        inv_100_after = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        inv_200_after = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-200')
        self.assertEqual(inv_100_after['paid'], 0.0)
        self.assertEqual(inv_200_after['paid'], 1250.00)
        self.assertEqual(inv_200_after['status'], 'paid')

    # --- Defect 4: Storage - Invoice import idempotency ---
    def test_defect4_invoice_import_idempotency_and_conflict(self):
        """Reproduction & regression for Defect 4:
        Prior to fix, re-importing invoices inserted duplicate rows into invoices table.
        After fix:
        - Re-importing identical invoice details skips the row without changing totals.
        - Importing existing key with different details rejects the row and preserves original.
        """
        initial_count = len(reporting.invoices(self.db))
        initial_outstanding = reporting.overview(self.db)['summary']['outstanding']

        # Re-import identical invoice that already exists in seed (HARBOR INV-100)
        csv_dup = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,INV-100,1250.00,2026-09-01\n'
        )
        res = importing.import_csv(self.db, csv_dup, 'invoices')
        self.assertEqual(res['imported'], 0)
        self.assertEqual(res['skipped'], 1)
        self.assertEqual(res['rejected'], 0)

        # Register counts and totals must remain unchanged
        self.assertEqual(len(reporting.invoices(self.db)), initial_count)
        self.assertEqual(reporting.overview(self.db)['summary']['outstanding'], initial_outstanding)

        # Import conflicting invoice (different amount)
        csv_conflict = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,INV-100,9999.00,2026-09-01\n'
        )
        res_conflict = importing.import_csv(self.db, csv_conflict, 'invoices')
        self.assertEqual(res_conflict['imported'], 0)
        self.assertEqual(res_conflict['skipped'], 0)
        self.assertEqual(res_conflict['rejected'], 1)
        self.assertEqual(res_conflict['errors'][0]['line'], 2)

        # Original invoice amount must be preserved
        inv_100 = storage.invoice_by_key(self.db, 'HARBOR', 'INV-100')
        self.assertEqual(inv_100['amount'], 1250.00)

    # --- Defect 5: Importing - Row-level validation and partial acceptance ---
    def test_defect5_row_level_validation_invoices_mixed(self):
        """Reproduction & regression for Defect 5:
        Prior to fix, an invalid row caused normalization to throw ValueError upfront,
        rejecting the whole file with HTTP 400.
        After fix, valid rows are imported while only invalid rows are rejected with line numbers.
        """
        # invoices-mixed.csv: Line 2 valid, Line 3 invalid (amount 'not-a-number'), Line 4 valid
        mixed_csv = (
            'customer_id,invoice_number,amount,due_date\n'
            'HARBOR,INV-103,84.00,2026-09-12\n'
            'NORTH,INV-302,not-a-number,2026-09-12\n'
            'MAPLE,INV-203,100.00,2026-09-13\n'
        )
        res = importing.import_csv(self.db, mixed_csv, 'invoices')
        self.assertEqual(res['imported'], 2)
        self.assertEqual(res['skipped'], 0)
        self.assertEqual(res['rejected'], 1)
        self.assertEqual(len(res['errors']), 1)
        self.assertEqual(res['errors'][0]['line'], 3)
        self.assertIn('amount', res['errors'][0]['reason'])

        # Check that valid rows were actually stored in DB
        inv_103 = storage.invoice_by_key(self.db, 'HARBOR', 'INV-103')
        self.assertIsNotNone(inv_103)
        self.assertEqual(inv_103['amount'], 84.00)

        inv_203 = storage.invoice_by_key(self.db, 'MAPLE', 'INV-203')
        self.assertIsNotNone(inv_203)
        self.assertEqual(inv_203['amount'], 100.00)

        # Invalid row was NOT stored
        inv_302 = storage.invoice_by_key(self.db, 'NORTH', 'INV-302')
        self.assertIsNone(inv_302)

    def test_defect5_header_rejection_vs_all_invalid_rows(self):
        """BUSINESS_RULES.md:
        - Invalid header rejects entire file with ValueError.
        - Valid header with all invalid rows returns HTTP 200 counts with rejected rows.
        - Valid header with no data rows returns imported=0, skipped=0, rejected=0.
        """
        # Invalid header raises ValueError
        with self.assertRaises(ValueError):
            importing.import_csv(self.db, 'bad,header,foo,bar\n1,2,3,4\n', 'invoices')

        # Empty data rows
        empty_res = importing.import_csv(self.db, 'customer_id,invoice_number,amount,due_date\n', 'invoices')
        self.assertEqual(empty_res, {'imported': 0, 'skipped': 0, 'rejected': 0, 'errors': []})

        # All invalid data rows
        all_bad_csv = (
            'customer_id,invoice_number,amount,due_date\n'
            'UNKNOWN,INV-X,50.00,2026-09-01\n'
            'HARBOR,INV-Y,-10.00,2026-09-01\n'
            'NORTH,INV-Z,10.00,not-a-date\n'
        )
        bad_res = importing.import_csv(self.db, all_bad_csv, 'invoices')
        self.assertEqual(bad_res['imported'], 0)
        self.assertEqual(bad_res['skipped'], 0)
        self.assertEqual(bad_res['rejected'], 3)
        self.assertEqual([e['line'] for e in bad_res['errors']], [2, 3, 4])

    # --- Custom Input Cases ---
    def test_custom_overpayment_handling(self):
        """BUSINESS_RULES.md:
        - Overpayments are permitted: show negative balance and mark invoice paid.
        - An overpayment on one invoice must not reduce another invoice's outstanding amount.
        - Overview outstanding is sum of positive invoice balances.
        """
        # INV-301 in demo has amount 100.00, paid 0.00
        # Pay 150.00 toward INV-301
        pay_csv = (
            'payment_id,customer_id,invoice_number,amount\n'
            'PAY-OVER,NORTH,INV-301,150.00\n'
        )
        res = importing.import_csv(self.db, pay_csv, 'payments')
        self.assertEqual(res['imported'], 1)

        inv_301 = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-301')
        self.assertEqual(inv_301['amount'], 100.00)
        self.assertEqual(inv_301['paid'], 150.00)
        self.assertEqual(inv_301['balance'], -50.00)
        self.assertEqual(inv_301['status'], 'paid')

        # Total outstanding should not be reduced by the -50.00 overpayment!
        # Before overpayment, demo outstanding was 3209.99.
        # Since INV-301 had 100.00 outstanding, removing its 100.00 outstanding leaves 3109.99.
        # It must NOT become 3109.99 - 50 = 3059.99!
        summary = reporting.overview(self.db)['summary']
        self.assertEqual(summary['outstanding'], 3109.99)

    def test_custom_multiple_payments_exact_cents(self):
        """Custom case: Multiple payments attached to one invoice preserving exact cents."""
        csv_payments = (
            'payment_id,customer_id,invoice_number,amount\n'
            'PAY-C1,HARBOR,INV-100,250.33\n'
            'PAY-C2,HARBOR,INV-100,250.33\n'
            'PAY-C3,HARBOR,INV-100,250.34\n'
        )
        res = importing.import_csv(self.db, csv_payments, 'payments')
        self.assertEqual(res['imported'], 3)

        inv_100 = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        # 250.33 + 250.33 + 250.34 = 751.00
        self.assertEqual(inv_100['paid'], 751.00)
        # 1250.00 - 751.00 = 499.00
        self.assertEqual(inv_100['balance'], 499.00)
        self.assertEqual(inv_100['status'], 'open')


if __name__ == '__main__':
    unittest.main()
