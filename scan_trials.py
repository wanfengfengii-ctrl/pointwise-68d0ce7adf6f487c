"""现场轨迹与故障夹具。用 check-fixture 核对普通班上的注入。"""
import argparse
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.catalog import find_result_for_scan, open_archive_store
from archive.retrieve import retrieve
from capture.recorder import record
from capture.release import PortUnavailable
from capture.storage import open_device_store
from demo import open_lab
from disposition.receipts import PendingReceipt
from disposition.revision_journal import RequestParameterConflict as RevisionParameterConflict
from disposition.requests import submit
from disposition.resume import retry
from intake.receive import stage
from quality.plan import SamplingPlan


PLAN = SamplingPlan("coat-two", 2, "µm", "head-A")
VALUES = (5, 17)


class PortFault:
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
            if self.when == "after":
                method(*args, **kwargs)
            self.hit = True
            raise PortUnavailable(f"fixture interrupted {name} ({self.when})")

        return interrupt


@contextmanager
def reject_archive_commit(db):
    db.executescript("""
        CREATE TEMP TABLE fixture_parent (id INTEGER PRIMARY KEY);
        CREATE TEMP TABLE fixture_child (
            id INTEGER REFERENCES fixture_parent(id) DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TEMP TRIGGER fixture_commit_fault AFTER INSERT ON main.archive_catalog BEGIN
            INSERT INTO fixture_child VALUES (1);
        END;
    """)
    try:
        yield
    finally:
        if db.in_transaction:
            db.rollback()
        db.executescript("""
            DROP TRIGGER fixture_commit_fault;
            DROP TABLE fixture_child;
            DROP TABLE fixture_parent;
        """)


@contextmanager
def expect_error(error_type):
    try:
        yield
    except error_type:
        return
    raise AssertionError(f"expected {error_type.__name__}")


def no_result_or_release(archive_db, port):
    assert find_result_for_scan(archive_db, "S") is None
    assert port.released("S") is None


def check_fixture(directory):
    with open_lab(directory) as (device_db, archive_db, port):
        record(device_db, "S", PLAN.source, 31, PLAN, VALUES)
        stage(archive_db, "S", port.read)
        with reject_archive_commit(archive_db):
            try:
                submit(archive_db, port, "F", "S", "file")
            except sqlite3.IntegrityError as error:
                assert "FOREIGN KEY" in str(error)
            else:
                raise AssertionError("fixture did not stop the archive commit")
            assert not archive_db.in_transaction
            no_result_or_release(archive_db, port)
        for when in ("before", "after"):
            fault = PortFault(port, "release", when)
            pending = retry(archive_db, fault, "F", "S", "file")
            assert fault.hit and isinstance(pending, PendingReceipt)
            assert (port.released("S") is None) == (when == "before")

    with closing(open_archive_store(directory / "archive.sqlite")) as archive_db:
        entry = find_result_for_scan(archive_db, "S")
        assert entry is not None
        payload = retrieve(archive_db, entry.result_id)
        assert (payload.provenance.trigger, payload.readings, payload.spread) == (31, VALUES, 12)
        print(f"夹具核对完成：{entry.result_id}，触发 {payload.provenance.trigger}，极差 {payload.spread}")


def check_receipt(receipt, expected_status):
    assert receipt.status == expected_status, receipt
    assert bool(receipt.result_id) == (expected_status == "filed"), receipt


def offline_result(directory, result_id, version, trigger):
    with closing(open_archive_store(directory / "archive.sqlite")) as archive_db:
        payload = retrieve(archive_db, result_id)
        assert payload.provenance.scan_id == "S"
        assert (payload.version, payload.provenance.trigger) == (version, trigger)
        assert (payload.readings, payload.unit, payload.spread) == (VALUES, PLAN.unit, 12)
        return {
            "result_id": result_id,
            "version": payload.version,
            "trigger": payload.provenance.trigger,
            "readings": list(payload.readings),
            "unit": payload.unit,
            "spread": payload.spread,
        }


def rescan_wins(directory):
    from capture.recorder import record_revisable, rescan
    from disposition.requests import approve
    from intake.receive import stage_review

    with open_lab(directory) as (device_db, archive_db, port):
        old, trigger = record_revisable(device_db, "S", PLAN.source, 31, PLAN, VALUES)
        viewed, readout = port.review("S")
        assert (old, trigger) == (viewed, readout.header.identity.trigger) and trigger == 31
        assert stage_review(archive_db, "S", old, port.review).values == VALUES
        fault = PortFault(port, "seal_revision", "before")
        check_receipt(approve(archive_db, fault, "F", "S", old), "pending")
        assert fault.hit
        no_result_or_release(archive_db, port)

    with closing(open_device_store(directory / "device.sqlite")) as peer:
        current, trigger = rescan(peer, "S", 32, VALUES)
        assert current != old and trigger == 32

    with open_lab(directory) as (device_db, archive_db, port):
        check_receipt(approve(archive_db, port, "F", "S", old), "obsolete")
        with expect_error(RevisionParameterConflict):
            approve(archive_db, port, "F", "S", current)
        with expect_error(ValueError):
            stage_review(archive_db, "S", old, port.review)
        with expect_error(ValueError):
            submit(archive_db, port, "W", "S", "withdraw")
        no_result_or_release(archive_db, port)
        viewed, readout = port.review("S")
        assert (viewed, readout.header.identity.trigger) == (current, 32)
        receipt = approve(archive_db, port, "G", "S", current)
        check_receipt(receipt, "filed")
        released = port.released("S")
        assert (released.request_id, released.identity.trigger) == ("G", 32)
        record(device_db, "T", PLAN.source, 33, PLAN, (2, 8))
    return offline_result(directory, receipt.result_id, current, 32)


def seal_wins(directory):
    from capture.recorder import record_revisable, rescan
    from disposition.requests import approve

    with open_lab(directory) as (device_db, archive_db, port):
        version, _ = record_revisable(device_db, "S", PLAN.source, 31, PLAN, VALUES)
        viewed, readout = port.review("S")
        assert (viewed, readout.header.identity.trigger) == (version, 31)
        fault = PortFault(port, "seal_revision", "after")
        check_receipt(approve(archive_db, fault, "F", "S", version), "pending")
        assert fault.hit
        no_result_or_release(archive_db, port)

    with closing(open_device_store(directory / "device.sqlite")) as peer:
        with expect_error(ValueError):
            rescan(peer, "S", 32, VALUES)

    with open_lab(directory) as (_, archive_db, port):
        with expect_error(ValueError):
            submit(archive_db, port, "V1", "S", "file")
        with reject_archive_commit(archive_db):
            try:
                pending = approve(archive_db, port, "F", "S", version)
            except sqlite3.DatabaseError:
                pass
            else:
                check_receipt(pending, "pending")
            assert not archive_db.in_transaction
            no_result_or_release(archive_db, port)
            viewed, readout = port.review("S")
            assert (viewed, readout.header.identity.trigger) == (version, 31)
            assert tuple(row.value for row in readout.rows) == VALUES

    with open_lab(directory) as (device_db, archive_db, port):
        receipt = approve(archive_db, port, "F", "S", version)
        check_receipt(receipt, "filed")
        assert approve(archive_db, port, "F", "S", version).result_id == receipt.result_id
        released = port.released("S")
        assert (released.request_id, released.identity.trigger) == ("F", 31)
        record(device_db, "T", PLAN.source, 33, PLAN, (2, 8))
    return offline_result(directory, receipt.result_id, version, 31)


def run_scenarios(directory, *, demo: bool):
    for scenario in (rescan_wins, seal_wins):
        case_dir = directory / scenario.__name__
        case_dir.mkdir()
        result = scenario(case_dir)
        if demo:
            print(json.dumps({"case": scenario.__name__, **result}, ensure_ascii=False))
        else:
            print(
                f"核对完成：{scenario.__name__} 领到 {result['result_id']} "
                f"v{result['version']} 触发 {result['trigger']} 极差 {result['spread']}"
            )


def run_verify() -> int:
    """运行清单 verify 入口：原回归 + 普通件夹具 + 重扫两种先后与失败续办。"""
    import unittest

    suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        return 1
    with TemporaryDirectory() as root:
        directory = Path(root)
        check_fixture(directory)
        run_scenarios(directory, demo=False)
    print("核对完成：原回归与普通件夹具通过；重扫先成签触发 32，封定先成及续办签触发 31。")
    return 0


def run_demo() -> int:
    """运行清单 demo 入口：原交班演示后追加亲自运行的重扫轨迹（断源领到触发 32）。"""
    import demo as shift_demo

    shift_demo.main()
    with TemporaryDirectory() as root:
        run_scenarios(Path(root), demo=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("check-fixture", "verify", "demo", "verify-all", "demo-all"),
    )
    mode = parser.parse_args().mode
    if mode == "verify-all":
        raise SystemExit(run_verify())
    if mode == "demo-all":
        raise SystemExit(run_demo())
    with TemporaryDirectory() as root:
        directory = Path(root)
        if mode == "check-fixture":
            check_fixture(directory)
            return
        run_scenarios(directory, demo=(mode == "demo"))
        if mode == "verify":
            print("核对完成：重扫先成签触发 32，封定先成及续办签触发 31。")


if __name__ == "__main__":
    main()
