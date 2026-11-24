from archive.catalog import find_result_for_scan
from capture.readout import UnknownScan
from capture.recorder import record
from capture.slots import CaptureIdentityConflict, SlotBusy, find_slot_scan
from disposition.receipts import WithdrawalReceipt
from quality.plan import SamplingPlan
from tests import LabCase


class CaptureTests(LabCase):
    def test_busy_slot(self):
        original = self.capture()
        with self.assertRaises(SlotBusy):
            self.capture('T', 32, (101, 103))
        self.reopen()
        self.assertEqual(find_slot_scan(self.device_db, 'S'), original)
        reply = self.device.read('S')
        self.assertEqual(reply.header.identity.trigger, 31)
        self.assertEqual(tuple(row.value for row in reply.rows), (5, 17))
        with self.assertRaises(UnknownScan):
            self.device.read('T')

    def test_record_input_types_and_plan(self):
        original = self.capture()
        for bad in (True, 1.5, None, '17'):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                self.capture('T', 32, (5, bad))
        for values in ((), (5,), (5, 6, 7), b'17', '17'):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.capture('T', 32, values)
        for plan in (
            SamplingPlan('invalid', 0, 'µm', 'head-A'),
            SamplingPlan('invalid', True, 'µm', 'head-A'),
            SamplingPlan('invalid', 2, '', 'head-A'),
        ):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.capture('T', 32, plan=plan)
        for trigger in (-1, True, '32'):
            with self.subTest(trigger=trigger), self.assertRaises(ValueError):
                self.capture('T', trigger)
        with self.assertRaises(ValueError):
            record(self.device_db, 'T', 'head-B', 32, self.plan, (5, 17))
        self.assertEqual(find_slot_scan(self.device_db, 'S'), original)

    def test_released_identity_cannot_be_reused(self):
        scan = self.capture()
        receipt = self.submit('W', operation='withdraw')
        self.assertEqual(receipt, WithdrawalReceipt('W', 'S'))
        with self.assertRaises(UnknownScan):
            self.device.read('S')
        with self.assertRaises(CaptureIdentityConflict):
            self.capture('S', 32)
        with self.assertRaises(CaptureIdentityConflict):
            self.capture('T', 31)
        self.assertEqual(self.device.released('S').identity, scan.identity)
        self.capture('T', 32)
        self.assertIsNone(find_result_for_scan(self.archive_db, 'S'))
        self.assertEqual(self.device.read('T').header.identity.trigger, 32)
