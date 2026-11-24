from contextlib import ExitStack, closing, contextmanager
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from archive.catalog import initialize_archive_store, open_archive_store
from capture.recorder import record
from capture.release import DevicePort
from capture.storage import initialize_device_store, open_device_store
from disposition.decision import lookup_release_basis
from disposition.requests import submit
from disposition.resume import retry
from intake.receive import stage
from quality.plan import SamplingPlan


class LabCase(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.plan = SamplingPlan('coat-two', 2, 'µm', 'head-A')
        self.connections = ExitStack()
        self.addCleanup(self.connections.close)
        self.events = []
        self.reopen()

    def _connect(self, stack):
        device_db = stack.enter_context(closing(open_device_store(self.root / 'device.sqlite')))
        archive_db = stack.enter_context(closing(open_archive_store(self.root / 'archive.sqlite')))
        port = DevicePort(device_db, partial(lookup_release_basis, archive_db))
        return device_db, archive_db, port

    def reopen(self):
        self.connections.close()
        self.device_db, self.archive_db, self.device = self._connect(self.connections)

    @contextmanager
    def peer(self):
        with ExitStack() as connections:
            yield self._connect(connections)

    def reinitialize_twice(self):
        for _ in range(2):
            initialize_device_store(self.device_db)
            initialize_archive_store(self.archive_db)

    def capture(self, scan_id='S', trigger=31, readings=(5, 17), plan=None):
        if plan is None:
            plan = self.plan
        return record(self.device_db, scan_id, plan.source, trigger, plan, readings)

    def stage(self, scan_id='S'):
        return stage(self.archive_db, scan_id, self.device.read)

    def submit(self, request_id, scan_id='S', operation='file', *, hook=None):
        return submit(self.archive_db, self.device, request_id, scan_id, operation, hook=hook)

    def retry(self, request_id, scan_id='S', operation='file', *, hook=None):
        return retry(self.archive_db, self.device, request_id, scan_id, operation, hook=hook)

    def observe(self, event, request):
        self.events.append((request.request_id, event))
