from archive.catalog import find_result_for_scan
from archive.retrieve import retrieve
from capture.readout import UnknownScan
from capture.recorder import record, record_revisable, rescan
from capture.release import PortUnavailable, UnauthorizedRelease
from capture.slots import SlotBusy, find_sealed_version
from disposition.requests import approve, submit
from disposition.revision_journal import RequestParameterConflict
from intake.receive import stage, stage_review
from tests import LabCase


class RescanCaptureTests(LabCase):
    def test_record_revisable_returns_first_version(self):
        version, trigger = record_revisable(
            self.device_db, 'S', 'head-A', 31, self.plan, (5, 17)
        )
        self.assertEqual((version, trigger), (1, 31))
        viewed, readout = self.device.review('S')
        self.assertEqual(viewed, 1)
        self.assertEqual(readout.header.identity.trigger, 31)
        self.assertEqual(
            tuple(row.value for row in readout.rows), (5, 17)
        )

    def test_rescan_issues_new_version_even_for_equal_values(self):
        record_revisable(self.device_db, 'S', 'head-A', 31, self.plan, (5, 17))
        version, trigger = rescan(self.device_db, 'S', 32, (5, 17))
        self.assertEqual((version, trigger), (2, 32))
        viewed, readout = self.device.review('S')
        self.assertEqual((viewed, readout.header.identity.trigger), (2, 32))
        self.assertEqual(tuple(row.value for row in readout.rows), (5, 17))
        self.reopen()
        viewed, readout = self.device.review('S')
        self.assertEqual((viewed, readout.header.identity.trigger), (2, 32))

    def test_rescan_keeps_the_slot(self):
        record_revisable(self.device_db, 'S', 'head-A', 31, self.plan, (5, 17))
        rescan(self.device_db, 'S', 32, (5, 17))
        with self.assertRaises(SlotBusy):
            record(self.device_db, 'T', 'head-A', 33, self.plan, (1, 2))

    def test_rescan_rejects_used_triggers(self):
        record_revisable(self.device_db, 'S', 'head-A', 31, self.plan, (5, 17))
        # 该原件自身用过的实际触发不得再用，同值重扫也必须另发版号。
        with self.assertRaises(ValueError):
            rescan(self.device_db, 'S', 31, (5, 17))
        new, _ = rescan(self.device_db, 'S', 32, (5, 17))
        self.assertEqual(new, 2)
        # 两个历史触发都已登记，都不可再用。
        for used in (31, 32):
            with self.subTest(used=used), self.assertRaises(ValueError):
                rescan(self.device_db, 'S', used, (5, 17))
        self.assertEqual(self.device.review('S')[0], 2)

    def test_rescan_requires_complete_readings(self):
        record_revisable(self.device_db, 'S', 'head-A', 31, self.plan, (5, 17))
        for bad in ((5,), (5, 17, 1), (), (5, True)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                rescan(self.device_db, 'S', 32, bad)
        self.assertEqual(self.device.review('S')[0], 1)

    def test_rescan_on_plain_original_is_rejected(self):
        record(self.device_db, 'S', 'head-A', 31, self.plan, (5, 17))
        with self.assertRaises(ValueError):
            rescan(self.device_db, 'S', 32, (5, 17))
        with self.assertRaises(UnknownScan):
            self.device.review('S')

    def test_sealed_original_cannot_be_rescanned(self):
        version, _ = record_revisable(
            self.device_db, 'S', 'head-A', 31, self.plan, (5, 17)
        )
        stage_review(self.archive_db, 'S', version, self.device.review)
        approve(self.archive_db, self.device, 'F', 'S', version)
        with self.assertRaises(ValueError):
            rescan(self.device_db, 'S', 32, (5, 17))
        self.assertEqual(find_sealed_version(self.device_db, 'S'), version)


class ReviewStagingTests(LabCase):
    def _register(self):
        version, _ = record_revisable(
            self.device_db, 'S', 'head-A', 31, self.plan, (5, 17)
        )
        return version

    def test_stage_review_keeps_versioned_original(self):
        version = self._register()
        staged = stage_review(self.archive_db, 'S', version, self.device.review)
        self.assertEqual(staged.values, (5, 17))
        self.assertEqual(staged.identity.trigger, 31)
        # 再次暂存同版同料保留一份。
        self.assertEqual(
            stage_review(self.archive_db, 'S', version, self.device.review), staged
        )
        rescan(self.device_db, 'S', 32, (5, 17))
        # 旧版暂存不随重扫改变，仍可查回。
        from intake.review_staging import load_review_staged_scan

        self.assertEqual(
            load_review_staged_scan(self.archive_db, 'S', version), staged
        )

    def test_stage_review_rejects_stale_version(self):
        old = self._register()
        rescan(self.device_db, 'S', 32, (5, 17))
        with self.assertRaises(ValueError):
            stage_review(self.archive_db, 'S', old, self.device.review)

    def test_stage_review_does_not_grant_rescan_immunity(self):
        version = self._register()
        stage_review(self.archive_db, 'S', version, self.device.review)
        # 仅暂存或读过不改变重扫资格。
        self.device.review('S')
        new, trigger = rescan(self.device_db, 'S', 32, (9, 21))
        self.assertEqual((new, trigger), (2, 32))
        # 旧版暂存与当前实扫不一致，不能再按旧版入账。
        with self.assertRaises(ValueError):
            stage_review(self.archive_db, 'S', version, self.device.review)


class ApprovalTests(LabCase):
    def _revisable(self, trigger=31, values=(5, 17)):
        return record_revisable(
            self.device_db, 'S', 'head-A', trigger, self.plan, values
        )

    def test_approve_files_current_version_and_releases_slot(self):
        version, _ = self._revisable()
        stage_review(self.archive_db, 'S', version, self.device.review)
        receipt = approve(self.archive_db, self.device, 'F', 'S', version)
        self.assertEqual(receipt.status, 'filed')
        self.assertIsNotNone(receipt.result_id)
        released = self.device.released('S')
        self.assertEqual(
            (released.request_id, released.operation, released.identity.trigger),
            ('F', 'approve', 31),
        )
        payload = retrieve(self.archive_db, receipt.result_id)
        self.assertEqual(
            (payload.version, payload.provenance.trigger, payload.readings, payload.spread),
            (version, 31, (5, 17), 12),
        )

    def test_approve_without_pre_staging_reads_live_review(self):
        version, _ = self._revisable()
        receipt = approve(self.archive_db, self.device, 'F', 'S', version)
        self.assertEqual(receipt.status, 'filed')
        payload = retrieve(self.archive_db, receipt.result_id)
        self.assertEqual((payload.version, payload.readings, payload.spread), (1, (5, 17), 12))

    def test_rescan_wins_obsoletes_old_approval(self):
        old, _ = self._revisable()
        stage_review(self.archive_db, 'S', old, self.device.review)
        fault = _FaultPort(self.device, 'seal_revision', 'before')
        self.assertEqual(
            approve(self.archive_db, fault, 'F', 'S', old).status, 'pending'
        )
        self.reopen()
        current, trigger = rescan(self.device_db, 'S', 32, (5, 17))
        self.assertEqual(trigger, 32)
        receipt = approve(self.archive_db, self.device, 'F', 'S', old)
        self.assertEqual((receipt.status, receipt.result_id), ('obsolete', None))
        self.assertIsNone(self.device.released('S'))
        self.assertIsNone(find_result_for_scan(self.archive_db, 'S'))
        # 同号异参拒绝。
        with self.assertRaises(RequestParameterConflict):
            approve(self.archive_db, self.device, 'F', 'S', current)
        # 新请求按当前实扫版核准。
        filed = approve(self.archive_db, self.device, 'G', 'S', current)
        self.assertEqual(filed.status, 'filed')
        payload = retrieve(self.archive_db, filed.result_id)
        self.assertEqual(
            (payload.version, payload.provenance.trigger, payload.spread),
            (current, 32, 12),
        )
        self.assertEqual(self.device.released('S').request_id, 'G')

    def test_seal_wins_blocks_rescan_and_continues_after_archive_fault(self):
        version, _ = self._revisable()
        fault = _FaultPort(self.device, 'seal_revision', 'after')
        self.assertEqual(
            approve(self.archive_db, fault, 'F', 'S', version).status, 'pending'
        )
        self.reopen()
        # 核准先成：设备阻止重扫。
        with self.assertRaises(ValueError):
            rescan(self.device_db, 'S', 32, (5, 17))
        from scan_trials import reject_archive_commit

        with reject_archive_commit(self.archive_db):
            receipt = approve(self.archive_db, self.device, 'F', 'S', version)
        self.assertEqual(receipt.status, 'pending')
        self.assertIsNone(self.device.released('S'))
        self.assertIsNone(find_result_for_scan(self.archive_db, 'S'))
        # 失答/失败重开仍按原请求续办。
        self.reopen()
        filed = approve(self.archive_db, self.device, 'F', 'S', version)
        self.assertEqual(filed.status, 'filed')
        again = approve(self.archive_db, self.device, 'F', 'S', version)
        self.assertEqual(again.result_id, filed.result_id)
        self.assertEqual(self.device.released('S').identity.trigger, 31)

    def test_ordinary_entry_points_refuse_revisable_originals(self):
        version, _ = self._revisable()
        stage(self.archive_db, 'S', self.device.read)
        with self.assertRaises(ValueError):
            submit(self.archive_db, self.device, 'F', 'S', 'file')
        with self.assertRaises(ValueError):
            submit(self.archive_db, self.device, 'W', 'S', 'withdraw')
        # 直接放行也受同一资格约束。
        identity = self.device.identity('S')
        with self.assertRaises(UnauthorizedRelease):
            self.device.release(identity, 'F', 'file')
        self.assertIsNone(self.device.released('S'))

    def test_sealed_version_cannot_be_approved_by_another_request(self):
        version, _ = self._revisable()
        first = approve(self.archive_db, self.device, 'F', 'S', version)
        self.assertEqual(first.status, 'filed')
        with self.assertRaises(ValueError):
            approve(self.archive_db, self.device, 'H', 'S', version)

    def test_unknown_and_bad_versions(self):
        version, _ = self._revisable()
        with self.assertRaises((UnknownScan, ValueError)):
            approve(self.archive_db, self.device, 'F', 'NOPE', version)
        for bad in (0, -1, True, '1'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                approve(self.archive_db, self.device, 'F', 'S', bad)


class _FaultPort:
    def __init__(self, port, method, when):
        self.port = port
        self.method = method
        self.when = when
        self.hit = False

    def __getattr__(self, name):
        method = getattr(self.port, name)
        if name != self.method:
            return method

        def interrupt(*args, **kwargs):
            if self.when == 'after':
                method(*args, **kwargs)
            self.hit = True
            raise PortUnavailable(f'fixture interrupted {name} ({self.when})')

        return interrupt
