import sqlite3
from dataclasses import replace

from archive.catalog import find_result_for_scan
from archive.retrieve import UnknownResult, retrieve
from disposition.journal import find_terminal
from disposition.receipts import FileReceipt, PendingReceipt
from intake.receive import stage
from intake.staging import StagingConflict, load_staged_scan
from quality.plan import SamplingPlan
from tests import LabCase


class IntakeTests(LabCase):
    def test_first_scan(self):
        original = self.capture()
        staged = self.stage()
        self.assertEqual(staged.identity, original.identity)
        self.assertEqual(staged.identity.trigger, 31)
        self.assertEqual(staged.plan, self.plan)
        self.assertEqual(staged.plan.unit, 'µm')
        self.assertEqual(staged.values, (5, 17))
        self.assertEqual(tuple(row.position for row in staged.readings), (1, 2))
        self.reopen()
        self.assertEqual(load_staged_scan(self.archive_db, 'S'), staged)
        self.assertEqual(self.device.read('S').header.identity, original.identity)
        self.assertIsNone(find_result_for_scan(self.archive_db, 'S'))
        with self.assertRaises(UnknownResult):
            retrieve(self.archive_db, 'LS:S')

    def test_short_scan_and_empty_scan(self):
        self.capture()
        full = self.device.read('S')
        for rows in ((), full.rows[:1], full.rows + full.rows[:1]):
            with self.subTest(count=len(rows)), self.assertRaisesRegex(ValueError, 'planned number'):
                stage(self.archive_db, 'S', lambda _: replace(full, rows=rows))
        self.reopen()
        self.assertIsNone(load_staged_scan(self.archive_db, 'S'))
        self.assertEqual(self.device.read('S'), full)

    def test_trigger_mismatch(self):
        self.capture()
        full = self.device.read('S')
        second = replace(full.rows[1], identity=replace(full.rows[1].identity, trigger=32))
        with self.assertRaisesRegex(ValueError, 'provenance'):
            stage(self.archive_db, 'S', lambda _: replace(full, rows=(full.rows[0], second)))
        self.reopen()
        self.assertIsNone(load_staged_scan(self.archive_db, 'S'))
        self.assertEqual(self.device.read('S'), full)

    def test_noninteger_values_from_read_port(self):
        self.capture()
        full = self.device.read('S')
        for bad in (True, 17.0, None, '17'):
            second = replace(full.rows[1], value=bad)
            with self.subTest(value=bad), self.assertRaisesRegex(ValueError, 'integer'):
                stage(self.archive_db, 'S', lambda _: replace(full, rows=(full.rows[0], second)))
        self.assertIsNone(load_staged_scan(self.archive_db, 'S'))
        self.assertEqual(self.device.read('S'), full)

    def test_position_unit_and_original_mismatch(self):
        self.capture()
        full = self.device.read('S')
        first, second = full.rows
        invalid_readouts = (
            replace(full, rows=(second, first)),
            replace(full, rows=(first, replace(second, position=1))),
            replace(full, rows=(first, replace(second, position=3))),
            replace(full, rows=(first, replace(second, unit='mm'))),
            replace(full, header=replace(full.header, unit='mm')),
            replace(full, header=replace(full.header, identity=replace(full.header.identity, scan_id='T'))),
            replace(full, header=replace(full.header, plan=replace(full.header.plan, source='head-B'))),
        )
        for readout in invalid_readouts:
            with self.subTest(readout=readout), self.assertRaises(ValueError):
                stage(self.archive_db, 'S', lambda _: readout)
        self.assertIsNone(load_staged_scan(self.archive_db, 'S'))
        self.assertEqual(self.device.read('S'), full)

    def test_duplicate_and_contradictory_staging(self):
        self.capture()
        stored = self.stage()
        self.assertEqual(self.stage(), stored)
        full = self.device.read('S')
        altered = replace(full, rows=(full.rows[0], replace(full.rows[1], value=23)))
        with self.assertRaises(StagingConflict):
            stage(self.archive_db, 'S', lambda _: altered)
        self.reopen()
        self.assertEqual(load_staged_scan(self.archive_db, 'S'), stored)
        self.assertEqual(self.archive_db.execute('SELECT COUNT(*) FROM staged_headers').fetchone(), (1,))
        self.assertEqual(self.archive_db.execute('SELECT COUNT(*) FROM staged_readings').fetchone(), (2,))

    def test_stage_retry(self):
        custom_plan = SamplingPlan('coating-night-custom', 2, 'nm', 'head-A')
        self.capture(plan=custom_plan)
        readout = self.device.read('S')
        self.archive_db.executescript('''
            CREATE TEMP TRIGGER fail_second_reading BEFORE INSERT ON staged_readings
            WHEN NEW.position = 2 BEGIN
                SELECT RAISE(ABORT, 'interrupted staging write');
            END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'interrupted staging write'):
            self.stage()
        self.assertEqual(self.submit('F'), PendingReceipt('F', 'S', 'awaiting_stage'))
        self.reopen()
        self.reinitialize_twice()
        for table in ('staged_headers', 'staged_readings'):
            self.assertEqual(self.archive_db.execute(f'SELECT COUNT(*) FROM {table}').fetchone(), (0,))
        self.assertIsNone(find_terminal(self.archive_db, 'S'))
        self.assertEqual(self.device.read('S'), readout)
        self.assertIsNone(self.device.released('S'))
        staged = self.stage()
        self.assertEqual(staged.plan, custom_plan)
        self.assertEqual(staged.values, (5, 17))
        self.assertEqual(self.stage(), staged)
        receipt = self.retry('F')
        self.assertIsInstance(receipt, FileReceipt)
        self.assertEqual(retrieve(self.archive_db, receipt.result_id).unit, 'nm')
        self.assertEqual(self.archive_db.execute('SELECT COUNT(*) FROM staged_headers').fetchone(), (1,))
        self.assertEqual(self.archive_db.execute('SELECT COUNT(*) FROM staged_readings').fetchone(), (2,))
