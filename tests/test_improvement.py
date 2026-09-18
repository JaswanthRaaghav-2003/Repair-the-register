"""Tests for the customer filter improvement on invoices."""
import tempfile
import unittest
from pathlib import Path
from ledger import storage, reporting


class CustomerFilterImprovementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'test_ledger.sqlite3'
        self.db = storage.connect(self.db_path)
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_filter_by_valid_customer(self):
        """In the seed demo:
        HARBOR has 2 invoices: INV-100 (1250.00) and INV-101 (300.00).
        MAPLE has 2 invoices: INV-200 (1250.00) and INV-201 (600.00).
        NORTH has 2 invoices: INV-300 (19.99) and INV-301 (100.00).
        """
        harbor_invs = reporting.invoices(self.db, customer='HARBOR')
        self.assertEqual(len(harbor_invs), 2)
        self.assertTrue(all(r['customer_id'] == 'HARBOR' for r in harbor_invs))
        inv_numbers = {r['invoice_number'] for r in harbor_invs}
        self.assertEqual(inv_numbers, {'INV-100', 'INV-101'})

    def test_filter_by_customer_and_status(self):
        """HARBOR INV-101 is paid (via SEED-1), INV-100 is open.
        Combining status='open' and customer='HARBOR' should return only INV-100.
        Combining status='paid' and customer='HARBOR' should return only INV-101.
        """
        harbor_open = reporting.invoices(self.db, status='open', customer='HARBOR')
        self.assertEqual(len(harbor_open), 1)
        self.assertEqual(harbor_open[0]['invoice_number'], 'INV-100')
        self.assertEqual(harbor_open[0]['status'], 'open')

        harbor_paid = reporting.invoices(self.db, status='paid', customer='HARBOR')
        self.assertEqual(len(harbor_paid), 1)
        self.assertEqual(harbor_paid[0]['invoice_number'], 'INV-101')
        self.assertEqual(harbor_paid[0]['status'], 'paid')

    def test_filter_by_invalid_customer_raises(self):
        """Filtering by an unknown customer ID must raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            reporting.invoices(self.db, customer='NONEXISTENT')
        self.assertIn('Unknown customer_id', str(ctx.exception))

    def test_default_customer_none_returns_all(self):
        """When customer is None or omitted, all invoices are returned."""
        all_invs = reporting.invoices(self.db)
        self.assertEqual(len(all_invs), 6)


if __name__ == '__main__':
    unittest.main()
