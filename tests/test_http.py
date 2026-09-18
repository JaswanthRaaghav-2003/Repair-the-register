"""Integration tests for ClearLedger HTTP endpoints."""
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from ledger import storage
from ledger.http_app import make_server

ROOT = Path(__file__).resolve().parent.parent


class HttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'test_ledger.sqlite3'
        db = storage.connect(self.db_path)
        storage.seed(db)
        db.close()

        # Find an available port by passing 0 or using an ephemeral port
        self.server = make_server(self.db_path, ROOT / 'web', 0)
        self.port = self.server.server_port
        self.base_url = f'http://127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_overview_endpoint(self):
        with urllib.request.urlopen(f'{self.base_url}/api/overview') as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(data['summary']['invoice_count'], 6)
            self.assertEqual(data['summary']['open_count'], 5)
            self.assertEqual(data['summary']['outstanding'], 3209.99)

    def test_invoices_status_and_customer_endpoints(self):
        # status=open returns 5 invoices
        with urllib.request.urlopen(f'{self.base_url}/api/invoices?status=open') as resp:
            self.assertEqual(resp.status, 200)
            rows = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(len(rows), 5)
            self.assertTrue(all(r['status'] == 'open' for r in rows))

        # customer=HARBOR returns 2 invoices
        with urllib.request.urlopen(f'{self.base_url}/api/invoices?customer=HARBOR') as resp:
            self.assertEqual(resp.status, 200)
            rows = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r['customer_id'] == 'HARBOR' for r in rows))

        # invalid status returns 400
        req = urllib.request.Request(f'{self.base_url}/api/invoices?status=invalid')
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

        # invalid customer returns 400
        req = urllib.request.Request(f'{self.base_url}/api/invoices?customer=UNKNOWN')
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

    def test_export_endpoint(self):
        with urllib.request.urlopen(f'{self.base_url}/api/export') as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get_content_type(), 'text/csv')
            content = resp.read().decode('utf-8')
            self.assertTrue(content.startswith('customer_id,invoice_number,amount,paid,balance,status'))
            self.assertIn('NORTH,INV-300,19.99,10.00,9.99,open', content)

    def test_import_partial_success(self):
        # samples/invoices-mixed.csv has 2 valid, 1 invalid (line 3)
        with open(ROOT / 'samples' / 'invoices-mixed.csv', 'rb') as f:
            body = f.read()
        req = urllib.request.Request(
            f'{self.base_url}/api/import?kind=invoices',
            data=body,
            headers={'Content-Type': 'text/csv'}
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode('utf-8'))
            self.assertEqual(data['imported'], 2)
            self.assertEqual(data['skipped'], 0)
            self.assertEqual(data['rejected'], 1)
            self.assertEqual(len(data['errors']), 1)
            self.assertEqual(data['errors'][0]['line'], 3)

    def test_import_invalid_header_returns_400(self):
        with open(ROOT / 'samples' / 'wrong-header.csv', 'rb') as f:
            body = f.read()
        req = urllib.request.Request(
            f'{self.base_url}/api/import?kind=invoices',
            data=body,
            headers={'Content-Type': 'text/csv'}
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)
        err_data = json.loads(ctx.exception.read().decode('utf-8'))
        self.assertIn('error', err_data)
        self.assertIn('Expected CSV header', err_data['error'])


if __name__ == '__main__':
    unittest.main()
