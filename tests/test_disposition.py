from archive.catalog import find_result_for_scan
from archive.provenance import TriggerProvenance
from archive.retrieve import UnknownResult, retrieve
from capture.readout import UnknownScan
from capture.release import PortUnavailable, ReleaseFact, UnauthorizedRelease
from capture.slots import SlotBusy
from disposition.decision import DispositionConflict
from disposition.journal import RequestParameterConflict, UnknownRequest, find_terminal, load_progress, load_request
from disposition.receipts import FileReceipt, PendingReceipt, WithdrawalReceipt
from disposition.requests import submit
from intake.staging import load_staged_scan
from tests import LabCase


class DispositionTests(LabCase):
    def assert_original_result(self, result_id):
        result = retrieve(self.archive_db, result_id)
        self.assertEqual(result.readings, (5, 17))
        self.assertEqual(result.unit, 'µm')
        self.assertEqual(result.provenance, TriggerProvenance('S', 'head-A', 31))
        self.assertEqual(result.spread, 12)
        return result

    def test_withdraw_scan(self):
        first = self.capture()
        staged = self.stage()
        receipt = self.submit('W', operation='withdraw')
        self.assertEqual(receipt, WithdrawalReceipt('W', 'S'))
        self.reopen()
        self.assertEqual(self.retry('W', operation='withdraw'), receipt)
        self.assertEqual(self.device.released('S'), ReleaseFact(first.identity, 'W', 'withdraw'))
        self.assertEqual(load_staged_scan(self.archive_db, 'S'), staged)
        self.capture('T', 32, (42, 45))
        with self.assertRaises(UnknownScan):
            self.device.read('S')
        self.assertEqual(self.device.read('T').header.identity.trigger, 32)
        self.assertIsNone(find_result_for_scan(self.archive_db, 'S'))
        with self.assertRaises(UnknownResult):
            retrieve(self.archive_db, 'LS:S')

    def test_resume_file(self):
        original = self.capture()
        self.stage()

        def lose_reply(event, request):
            self.observe(event, request)
            if event == 'after_release':
                raise PortUnavailable('device release reply lost')

        self.assertEqual(self.submit('F', hook=lose_reply), PendingReceipt('F', 'S', 'unconfirmed'))
        entry = find_result_for_scan(self.archive_db, 'S')
        payload = self.assert_original_result(entry.result_id)
        self.assertIsNone(load_progress(self.archive_db, 'F'))
        self.assertEqual(self.device.released('S'), ReleaseFact(original.identity, 'F', 'file'))
        self.reopen()
        self.reinitialize_twice()
        following = self.capture('T', 33, (91, 94))
        receipt = self.retry('F', hook=self.observe)
        self.assertEqual(receipt, FileReceipt('F', 'S', entry.result_id))
        self.assertEqual(self.submit('F'), receipt)
        self.assertEqual(retrieve(self.archive_db, receipt.result_id), payload)
        self.assertEqual(self.device.read('T').header.identity, following.identity)
        self.assertEqual(tuple(row.value for row in self.device.read('T').rows), (91, 94))
        self.assertEqual(self.events.count(('F', 'before_release')), 1)
        self.assertEqual(self.events.count(('F', 'after_release')), 1)
        with self.assertRaises(DispositionConflict):
            self.submit('W', operation='withdraw')

    def test_withdraw_lost_release_reply(self):
        original = self.capture()
        self.stage()

        def lose_reply(event, request):
            if event == 'after_release':
                self.assertEqual(self.device.released('S'), ReleaseFact(original.identity, 'W', 'withdraw'))
                raise PortUnavailable('withdrawal reply lost')

        self.assertEqual(
            self.submit('W', operation='withdraw', hook=lose_reply),
            PendingReceipt('W', 'S', 'unconfirmed'),
        )
        self.assertIsNone(load_progress(self.archive_db, 'W'))
        self.reopen()
        receipt = self.retry('W', operation='withdraw')
        self.assertEqual(receipt, WithdrawalReceipt('W', 'S'))
        self.assertEqual(self.submit('W', operation='withdraw'), receipt)
        self.capture('T', 32)
        self.assertIsNone(find_result_for_scan(self.archive_db, 'S'))
        self.assertEqual(self.device.read('T').header.identity.scan_id, 'T')
        with self.assertRaises(UnknownScan):
            self.device.read('S')

    def test_ungranted_release(self):
        original = self.capture()
        self.stage()

        def interrupt_before_decision(event, request):
            self.observe(event, request)
            if event != 'before_decision':
                return
            self.assertEqual(load_request(self.archive_db, 'W'), request)
            self.assertIsNone(find_terminal(self.archive_db, 'S'))
            with self.assertRaises(UnauthorizedRelease):
                self.device.release(original.identity, 'W', 'withdraw')
            with self.assertRaises(SlotBusy):
                self.capture('T', 32)
            self.assertEqual(tuple(row.value for row in self.device.read('S').rows), (5, 17))
            raise PortUnavailable('decision port interrupted')

        pending = self.submit('W', operation='withdraw', hook=interrupt_before_decision)
        self.assertEqual(pending, PendingReceipt('W', 'S', 'unconfirmed'))
        self.assertIsNone(self.device.released('S'))
        self.assertIsNone(find_terminal(self.archive_db, 'S'))
        self.reopen()
        self.assertEqual(self.retry('W', operation='withdraw'), WithdrawalReceipt('W', 'S'))
        self.assertEqual(self.device.released('S').identity, original.identity)

    def test_reuse_request(self):
        self.capture()
        receipt = self.submit('W', operation='withdraw')
        original_release = self.device.released('S')
        following = self.capture('T', 32, (21, 34))
        self.stage('T')
        with self.assertRaises(UnknownRequest):
            self.retry('unknown', 'T')
        self.assertIsNone(load_request(self.archive_db, 'unknown'))
        for entry_point in (self.submit, self.retry):
            with self.subTest(entry=entry_point.__name__):
                with self.assertRaises(RequestParameterConflict):
                    entry_point('W', 'S', 'file')
                with self.assertRaises(RequestParameterConflict):
                    entry_point('W', 'T', 'withdraw')
        self.assertEqual(self.retry('W', operation='withdraw'), receipt)
        self.assertEqual(self.device.released('S'), original_release)
        self.assertIsNone(self.device.released('T'))
        self.assertIsNone(find_terminal(self.archive_db, 'T'))
        self.assertEqual(self.device.read('T').header.identity, following.identity)

    def test_competing_withdraw_first(self):
        original = self.capture()
        staged = self.stage()

        def finish_withdrawal_before_file(event, request):
            self.observe(event, request)
            if event != 'before_decision':
                return
            self.assertEqual(load_staged_scan(self.archive_db, 'S'), staged)
            with self.peer() as (_, peer_archive, peer_device):
                winner = submit(peer_archive, peer_device, 'W', 'S', 'withdraw', hook=self.observe)
                self.assertEqual(winner, WithdrawalReceipt('W', 'S'))
                self.assertEqual(peer_device.released('S'), ReleaseFact(original.identity, 'W', 'withdraw'))
            self.capture('T', 32, (81, 94))

        with self.assertRaises(DispositionConflict, msg='CAPTURE_TERMINAL_CONFLICT') as conflict:
            self.submit('F', hook=finish_withdrawal_before_file)
        self.assertEqual(conflict.exception.winner.request_id, 'W')
        self.assertEqual(self.events, [
            ('F', 'request_registered'), ('F', 'before_decision'),
            ('W', 'request_registered'), ('W', 'before_decision'), ('W', 'after_decision'),
            ('W', 'before_release'), ('W', 'after_release'),
        ])
        self.reopen()
        self.assertEqual(self.retry('W', operation='withdraw'), WithdrawalReceipt('W', 'S'))
        with self.assertRaises(DispositionConflict):
            self.retry('F')
        self.assertIsNone(find_result_for_scan(self.archive_db, 'S'), 'CAPTURE_TERMINAL_CONFLICT')
        with self.assertRaises(UnknownResult):
            retrieve(self.archive_db, 'LS:S')
        with self.assertRaises(UnknownScan):
            self.device.read('S')
        self.assertEqual(self.device.read('T').header.identity.trigger, 32)

    def test_competing_file_first(self):
        original = self.capture()
        self.stage()

        def interrupt_after_file_decision(event, request):
            self.observe(event, request)
            if event != 'after_decision':
                return
            with self.peer() as (_, peer_archive, peer_device):
                with self.assertRaises(DispositionConflict, msg='CAPTURE_TERMINAL_CONFLICT') as conflict:
                    submit(peer_archive, peer_device, 'W', 'S', 'withdraw', hook=self.observe)
                self.assertEqual(conflict.exception.winner.request_id, 'F')
                self.assertIsNone(peer_device.released('S'))
                self.assertEqual(peer_device.read('S').header.identity, original.identity)
            raise PortUnavailable('file decision reply lost')

        self.assertEqual(
            self.submit('F', hook=interrupt_after_file_decision),
            PendingReceipt('F', 'S', 'unconfirmed'),
        )
        self.assertNotIn(('W', 'before_release'), self.events)
        self.reopen()
        entry = find_result_for_scan(self.archive_db, 'S')
        payload = self.assert_original_result(entry.result_id)
        with self.assertRaises(DispositionConflict):
            self.retry('W', operation='withdraw')
        self.assertEqual(self.retry('F'), FileReceipt('F', 'S', entry.result_id))
        self.assertEqual(self.device.released('S'), ReleaseFact(original.identity, 'F', 'file'))
        self.assertEqual(self.retry('F'), FileReceipt('F', 'S', entry.result_id))
        self.assertEqual(retrieve(self.archive_db, entry.result_id), payload)
        self.assertEqual(self.archive_db.execute('SELECT COUNT(*) FROM archive_catalog').fetchone(), (1,))
