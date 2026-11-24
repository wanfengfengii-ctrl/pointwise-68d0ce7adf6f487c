import sqlite3

from archive.catalog import find_result_for_scan
from archive.provenance import TriggerProvenance
from archive.retrieve import UnknownResult, retrieve
from capture.readout import UnknownScan
from disposition.journal import find_terminal
from disposition.receipts import FileReceipt
from quality.plan import SamplingPlan
from tests import LabCase


class ArchiveTests(LabCase):
    def assert_payload(self, result_id, scan_id, trigger, readings, spread, unit='µm'):
        payload = retrieve(self.archive_db, result_id)
        self.assertEqual(payload.result_id, result_id)
        self.assertEqual(payload.provenance, TriggerProvenance(scan_id, 'head-A', trigger))
        self.assertEqual(payload.readings, readings)
        self.assertEqual(payload.spread, spread)
        self.assertEqual(payload.unit, unit)
        return payload

    def test_file_scan(self):
        self.capture()
        self.stage()
        receipt = self.submit('F')
        self.assertIsInstance(receipt, FileReceipt)
        self.assertEqual(receipt.scan_id, 'S')
        self.assertEqual(self.device.released('S').request_id, 'F')
        with self.assertRaises(UnknownScan):
            self.device.read('S')
        self.capture('T', 32, (20, 18))
        self.reopen()
        self.assert_payload(receipt.result_id, 'S', 31, (5, 17), 12)
        self.assertEqual(self.device.read('T').header.identity.trigger, 32)
        with self.assertRaises(UnknownResult):
            retrieve(self.archive_db, 'S')

    def test_unsorted_range(self):
        plan = SamplingPlan('unsorted', 3, 'µm', 'head-A')
        self.capture(readings=(17, -5, 8), plan=plan)
        self.stage()
        receipt = self.submit('F')
        self.assert_payload(receipt.result_id, 'S', 31, (17, -5, 8), 22)

    def test_exact_integer(self):
        self.capture(readings=(-2**63, 2**63))
        self.stage()
        large = self.submit('F')
        custom_plan = SamplingPlan('one-point', 1, 'nm', 'head-A')
        self.capture('T', 32, (-9,), custom_plan)
        self.stage('T')
        one = self.submit('F2', 'T')
        self.reopen()
        self.assert_payload(large.result_id, 'S', 31, (-2**63, 2**63), 2**64)
        self.assert_payload(one.result_id, 'T', 32, (-9,), 0, 'nm')

    def test_second_archive(self):
        self.capture()
        self.stage()
        first = self.submit('F')
        before = self.assert_payload(first.result_id, 'S', 31, (5, 17), 12)
        different_plan = SamplingPlan('coat-two-nm', 2, 'nm', 'head-A')
        self.capture('T', 49, (101, 99), different_plan)
        self.stage('T')
        second = self.submit('F2', 'T')
        self.assertNotEqual(first.result_id, second.result_id)
        self.reopen()
        self.assertEqual(retrieve(self.archive_db, first.result_id), before)
        self.assert_payload(second.result_id, 'T', 49, (101, 99), 2, 'nm')

    def test_archive_commit_is_atomic(self):
        self.capture()
        self.stage()
        self.archive_db.executescript('''
            CREATE TEMP TRIGGER fail_publication BEFORE INSERT ON archive_payloads BEGIN
                SELECT RAISE(ABORT, 'interrupted fixed payload write');
            END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'interrupted fixed payload write'):
            self.submit('F')
        self.reopen()
        self.assertIsNone(find_terminal(self.archive_db, 'S'))
        self.assertIsNone(find_result_for_scan(self.archive_db, 'S'))
        self.assertIsNone(self.device.released('S'))
        self.assertEqual(tuple(row.value for row in self.device.read('S').rows), (5, 17))
        receipt = self.retry('F')
        self.assert_payload(receipt.result_id, 'S', 31, (5, 17), 12)
