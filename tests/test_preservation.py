"""Preservation and migration verification for the owner's existing register.
Ensures existing records are preserved, valid new imports work, and data survives restart.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from ledger import storage, reporting, importing

ROOT = Path(__file__).resolve().parent.parent


class ExistingRegisterPreservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'clearledger.sqlite3'
        source = ROOT / 'fixtures' / 'existing-register.sqlite3'
        shutil.copy2(source, self.db_path)
        with open(ROOT / 'fixtures' / 'expected-records.json', 'r', encoding='utf-8') as f:
            self.expected = json.load(f)

    def tearDown(self):
        self.tmp.cleanup()

    def test_existing_register_matches_expected_reference(self):
        """Verify that starting ClearLedger with existing-register.sqlite3
        preserves all customers, invoices, payments, allocations, and totals.
        """
        db = storage.connect(self.db_path)
        try:
            # Check customers
            db_customers = [dict(r) for r in db.execute('SELECT * FROM customers ORDER BY customer_id')]
            self.assertEqual(len(db_customers), len(self.expected['customers']))
            for exp_c, act_c in zip(self.expected['customers'], db_customers):
                self.assertEqual(exp_c['customer_id'], act_c['customer_id'])
                self.assertEqual(exp_c['name'], act_c['name'])

            # Check invoices
            db_invoices = [dict(r) for r in db.execute('SELECT * FROM invoices ORDER BY id')]
            self.assertEqual(len(db_invoices), len(self.expected['invoices']))
            for exp_i, act_i in zip(self.expected['invoices'], db_invoices):
                self.assertEqual(exp_i['id'], act_i['id'])
                self.assertEqual(exp_i['customer_id'], act_i['customer_id'])
                self.assertEqual(exp_i['invoice_number'], act_i['invoice_number'])
                self.assertEqual(float(exp_i['amount']), round(act_i['amount'], 2))
                self.assertEqual(exp_i['due_date'], act_i['due_date'])

            # Check payments
            db_payments = [dict(r) for r in db.execute('SELECT * FROM payments ORDER BY payment_id')]
            self.assertEqual(len(db_payments), len(self.expected['payments']))
            for exp_p, act_p in zip(self.expected['payments'], db_payments):
                self.assertEqual(exp_p['payment_id'], act_p['payment_id'])
                self.assertEqual(exp_p['customer_id'], act_p['customer_id'])
                self.assertEqual(exp_p['invoice_number'], act_p['invoice_number'])
                self.assertEqual(float(exp_p['amount']), round(act_p['amount'], 2))
                self.assertEqual(exp_p['invoice_id'], act_p['invoice_id'])

            # Check overview summary
            rep = reporting.overview(db)
            summary = rep['summary']
            self.assertEqual(summary['invoice_count'], self.expected['summary']['invoice_count'])
            self.assertEqual(summary['open_count'], self.expected['summary']['open_count'])
            self.assertEqual(summary['outstanding'], float(self.expected['summary']['outstanding']))

            # Unmatched payment KEEP-U1
            unmatched = rep['unmatched_payments']
            self.assertEqual(len(unmatched), 1)
            self.assertEqual(unmatched[0]['payment_id'], 'KEEP-U1')
            self.assertEqual(unmatched[0]['amount'], 33.33)

            # Check specific preserved invoices from fixtures/README.md
            harbor_700 = next(r for r in rep['invoices'] if r['customer_id'] == 'HARBOR' and r['invoice_number'] == 'KEEP-700')
            self.assertEqual(harbor_700['amount'], 456.78)
            self.assertEqual(harbor_700['paid'], 56.78)
            self.assertEqual(harbor_700['balance'], 400.00)
            self.assertEqual(harbor_700['status'], 'open')

            maple_700 = next(r for r in rep['invoices'] if r['customer_id'] == 'MAPLE' and r['invoice_number'] == 'KEEP-700')
            self.assertEqual(maple_700['amount'], 88.20)
            self.assertEqual(maple_700['paid'], 0.00)
            self.assertEqual(maple_700['balance'], 88.20)
            self.assertEqual(maple_700['status'], 'open')

            north_702 = next(r for r in rep['invoices'] if r['customer_id'] == 'NORTH' and r['invoice_number'] == 'KEEP-702')
            self.assertEqual(north_702['amount'], 150.00)
            self.assertEqual(north_702['paid'], 150.00)
            self.assertEqual(north_702['balance'], 0.00)
            self.assertEqual(north_702['status'], 'paid')
        finally:
            db.close()

    def test_new_imports_and_persistence_across_restart(self):
        """Verify valid new invoice and payment imports against restored register,
        and verify all data persists across app restart.
        """
        # Session 1: open DB and import new invoices & payments
        db = storage.connect(self.db_path)
        try:
            with open(ROOT / 'samples' / 'invoices-new.csv', 'r', encoding='utf-8') as f:
                inv_csv = f.read()
            inv_res = importing.import_csv(db, inv_csv, 'invoices')
            self.assertEqual(inv_res['imported'], 2)
            self.assertEqual(inv_res['skipped'], 0)
            self.assertEqual(inv_res['rejected'], 0)

            with open(ROOT / 'samples' / 'payments.csv', 'r', encoding='utf-8') as f:
                pay_csv = f.read()
            pay_res = importing.import_csv(db, pay_csv, 'payments')
            self.assertEqual(pay_res['imported'], 3)
            self.assertEqual(pay_res['skipped'], 0)
            self.assertEqual(pay_res['rejected'], 0)
        finally:
            db.close()

        # Session 2: simulate restart by reopening connection to the same database
        db_restarted = storage.connect(self.db_path)
        try:
            overview = reporting.overview(db_restarted)
            # Original 9 invoices + 2 new invoices = 11 invoices
            self.assertEqual(overview['summary']['invoice_count'], 11)

            # Original 5 payments + 3 new payments = 8 payments
            total_payments = db_restarted.execute('SELECT COUNT(*) FROM payments').fetchone()[0]
            self.assertEqual(total_payments, 8)

            # Check newly imported invoices exist
            inv_102 = storage.invoice_by_key(db_restarted, 'HARBOR', 'INV-102')
            self.assertIsNotNone(inv_102)
            self.assertEqual(inv_102['amount'], 80.00)

            inv_202 = storage.invoice_by_key(db_restarted, 'MAPLE', 'INV-202')
            self.assertIsNotNone(inv_202)
            self.assertEqual(inv_202['amount'], 200.00)

            # Check original invoices are still intact
            harbor_700 = storage.invoice_by_key(db_restarted, 'HARBOR', 'KEEP-700')
            self.assertIsNotNone(harbor_700)
            self.assertEqual(harbor_700['amount'], 456.78)

            # Check payment PAY-201 paid MAPLE INV-200 in full
            inv_200 = next(r for r in reporting.invoices(db_restarted) if r['invoice_number'] == 'INV-200')
            self.assertEqual(inv_200['paid'], 1250.00)
            self.assertEqual(inv_200['balance'], 0.00)
            self.assertEqual(inv_200['status'], 'paid')

            # Check PAY-404 is unmatched
            unmatched_pids = [p['payment_id'] for p in overview['unmatched_payments']]
            self.assertIn('PAY-404', unmatched_pids)
            self.assertIn('KEEP-U1', unmatched_pids)
            self.assertEqual(len(overview['unmatched_payments']), 2)
        finally:
            db_restarted.close()


if __name__ == '__main__':
    unittest.main()
