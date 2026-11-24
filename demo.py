import json
from contextlib import ExitStack, closing, contextmanager
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.catalog import find_result_for_scan, open_archive_store
from archive.retrieve import retrieve
from capture.recorder import record
from capture.release import DevicePort, PortUnavailable
from capture.storage import open_device_store
from disposition.approvals import lookup_seal_basis
from disposition.decision import lookup_release_basis
from disposition.receipts import FileReceipt, PendingReceipt, WithdrawalReceipt
from disposition.requests import submit
from disposition.resume import retry
from intake.receive import stage
from quality.plan import SamplingPlan


@contextmanager
def open_lab(directory):
    with ExitStack() as connections:
        device_db = connections.enter_context(closing(open_device_store(directory / 'device.sqlite')))
        archive_db = connections.enter_context(closing(open_archive_store(directory / 'archive.sqlite')))
        port = DevicePort(
            device_db,
            partial(lookup_release_basis, archive_db),
            partial(lookup_seal_basis, archive_db),
        )
        yield device_db, archive_db, port


def accept(device_db, archive_db, port, plan, scan):
    record(device_db, scan['scan_id'], plan.source, scan['trigger'], plan, scan['readings'])
    return stage(archive_db, scan['scan_id'], port.read)


def lose_release_reply(event, request):
    if event == 'after_release':
        raise PortUnavailable(f'reply lost after device handled {request.request_id}')


def report(case_id, source, expected, actual, downstream_expected, downstream_actual, code_paths, oracle):
    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'{case_id} diverged: {actual!r}, {downstream_actual!r}')
    evidence = {
        'case_id': case_id, 'input': source, 'expected': expected, 'actual': actual,
        'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
        'code_paths': code_paths, 'oracle': oracle,
    }
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))


def main():
    sample = json.loads(Path(__file__).with_name('sample.json').read_text(encoding='utf-8'))
    plan = SamplingPlan(**sample['plan'])
    first, second, third = sample['scans']
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        with open_lab(directory) as (device_db, archive_db, port):
            staged = accept(device_db, archive_db, port, plan, first)
            if staged.values != (5, 17) or find_result_for_scan(archive_db, 'S') is not None:
                raise AssertionError('接纳后应保留完整原件且尚无成果')
            signed = submit(archive_db, port, 'F', 'S', 'file')
            if not isinstance(signed, FileReceipt):
                raise AssertionError('S 未入档办结')
            accept(device_db, archive_db, port, plan, second)
            payload = retrieve(archive_db, signed.result_id)
            report(
                'file_scan', {'plan': sample['plan'], 'scan': first},
                {'readings': [5, 17], 'unit': 'µm', 'source': 'head-A', 'trigger': 31, 'spread': 12},
                {
                    'readings': list(payload.readings), 'unit': payload.unit,
                    'source': payload.provenance.device_id, 'trigger': payload.provenance.trigger,
                    'spread': payload.spread,
                },
                {'released_request': 'F', 'current_scan': 'T'},
                {
                    'released_request': port.released('S').request_id,
                    'current_scan': port.read('T').header.identity.scan_id,
                },
                ['capture/recorder.py', 'intake/receive.py', 'quality/range.py', 'archive/retrieve.py'],
                '手算 17 减 5 等于 12。槽内 T 的触发与 S 的固定触发不同。',
            )
            pending = submit(archive_db, port, 'W', 'T', 'withdraw', hook=lose_release_reply)
            if pending != PendingReceipt('W', 'T', 'unconfirmed'):
                raise AssertionError('T 的放行回包中断应报告待确认')

        with open_lab(directory) as (device_db, archive_db, port):
            restored = retry(archive_db, port, 'W', 'T', 'withdraw')
            if restored != WithdrawalReceipt('W', 'T'):
                raise AssertionError('T 的撤销续办结果不符')
            record(device_db, third['scan_id'], plan.source, third['trigger'], plan, third['readings'])
            release = port.released('T')
            report(
                'withdraw_scan.lost_release_reply', {'scan': second, 'request_id': 'W'},
                {'receipt': 'W', 'released_trigger': 32, 'formal_result': False},
                {
                    'receipt': restored.request_id, 'released_trigger': release.identity.trigger,
                    'formal_result': find_result_for_scan(archive_db, 'T') is not None,
                },
                {'next_scan': 'U', 'original_readings': [5, 17]},
                {
                    'next_scan': port.read('U').header.identity.scan_id,
                    'original_readings': list(retrieve(archive_db, signed.result_id).readings),
                },
                ['disposition/resume.py', 'capture/release.py', 'archive/catalog.py', 'archive/retrieve.py'],
                'W 对应 T 的触发 32。撤销件无成果，槽可登记 U，先前 S 仍应原样可领。',
            )


if __name__ == '__main__':
    main()
