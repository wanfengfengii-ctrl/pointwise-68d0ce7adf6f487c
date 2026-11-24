# 涂层试片线扫归档台

设备值班员登记线扫头的具名原件，质检员入档或撤销，档案员按成果号复核读数。模拟线扫头只有一个物理槽。设备与档案分别使用 SQLite 存储，应用可在两端重开后续办已登记的请求。

## 运行

需要 Python 3.11，仅使用标准库。在项目根目录运行完整回归和演示：

```text
python3 -B check_project.py
python3 -B demo.py
```

`check_project.py` 由运行平台在物化时提供。完整 unittest 套件亦可直接运行：

```text
python3 -B -m unittest discover -s tests -t .
```

演示从 `sample.json` 读取三个试片，在临时目录创建两端数据库。演示校对实际读数、触发、极差、槽释放及撤销结果，再输出场景记录。

## 一次交班

1. 值班员为 S 选用两点微米计划，由 head-A 在触发 31 生成 `[5, 17]`。
2. `record` 绑定原件、触发和计划快照，S 占用设备槽。
3. `stage` 按原件号读取件头与行，核对顺序、点数、单位和出处，提交完整暂存。
4. 质检员 `submit` 入档请求，按当次计划计算极差 12 微米。
5. 档案固定结果，设备确认释放 S 后返回成果号。
6. 同槽登记并暂存 T。夜班 `retrieve` S 的成果号，取得原读数和触发 31。
7. 质检员因夹具异常提交 T 的撤销。设备放行后回包中断，请求返回待确认。
8. 重开两端，以原参数 `retry`，确认 T 已释放且无正式成果。
9. 用原槽登记 U，S 的历史成果保持原样。

## Python 接口

```python
from contextlib import closing
from functools import partial

from archive.catalog import open_archive_store
from archive.retrieve import retrieve
from capture.recorder import record
from capture.release import DevicePort
from capture.storage import open_device_store
from disposition.decision import lookup_release_basis
from disposition.requests import submit
from intake.receive import stage
from quality.plan import SamplingPlan

with closing(open_device_store('device.sqlite')) as device_db:
    with closing(open_archive_store('archive.sqlite')) as archive_db:
        device = DevicePort(device_db, partial(lookup_release_basis, archive_db))
        plan = SamplingPlan('coat-two', 2, 'µm', 'head-A')
        record(device_db, 'S', 'head-A', 31, plan, [5, 17])
        staged = stage(archive_db, 'S', device.read)
        receipt = submit(archive_db, device, 'F', 'S', 'file')
        payload = retrieve(archive_db, receipt.result_id)
        assert staged.values == payload.readings == (5, 17)
        assert payload.spread == 12
```

- `record(db, scan_id, device_id, trigger, plan, readings)` 保存同一次扫描。原件号在设备存储中唯一，同一设备的同一触发只能登记一次。槽忙报 `SlotBusy`，身份复用报 `CaptureIdentityConflict`。设备读口只读取当前匹配的原件。触发序号为非负整数。
- `stage(db, scan_id, reader)` 接收 `reader(scan_id)` 返回的件头及有序行。计划快照包含标识、正点数、单位和来源。点位从 1 起，须依次齐全。逐行出处及单位须与件头匹配。无效材料报 `ValueError`；同原件号的矛盾暂存报 `StagingConflict`。写入异常传给调用方，原事务回滚。相同材料重复接纳仍保留一份，已接纳载荷可用 `load_staged_scan` 查回。
- `submit(db, device, request_id, scan_id, operation)` 接受 `file` 和 `withdraw`；`retry` 使用同一签名，续办已登记的原参数。成功返回 `FileReceipt` 或 `WithdrawalReceipt`，后者代表已确认放行。`PendingReceipt.reason` 为 `awaiting_stage` 或 `unconfirmed`。前者可先完成暂存，后者可按原请求续办。请求登记本身写入失败时，可用原参数重新提交。未知重投报 `UnknownRequest`，参数变更报 `RequestParameterConflict`。竞争落败报 `DispositionConflict`，异常附有胜出请求。设备查无采集身份时报 `UnknownScan`。
- `retrieve(db, result_id)` 只接受正式成果号，返回 `FixedPayload`，包含原序整数读数、单位、触发出处和精确极差。未知号报 `UnknownResult`。极差使用最大整数减最小整数，单点为零，负数和超过 SQLite 有符号整数范围的值均可无损存取。空值、布尔值、浮点值及数字文本均被拒绝。

## 存储与恢复

- `open_device_store` 和 `open_archive_store` 分别打开并初始化设备端与档案端。初始化只补齐结构。再次初始化保留槽、用户采集时指定的计划、暂存、请求和成果。连接由调用方关闭。
- 每对设备与档案对应同一个线扫台。调用公开写入口时连接应处于空闲事务状态。并发调用使用各自打开的两端连接。设备端口由可信应用配置，按原件查询档案端提交后的处置依据。一次入档事务同时成立终局和固定成果，撤销终局排除该原件的发布。设备在自己的事务内解除占槽并记录实际释放。任何一端的回复丢失都可依据持久事实重投。
- `submit` 与 `retry` 可接收关键字参数 `hook`，在请求登记后、终局写入前后及放行调用前后同步通知调用方。通知位于写事务外，参数为事件名和原请求。端口中断用 `capture.release.PortUnavailable` 表示。

回归覆盖非法扫描、逐行触发矛盾、整件暂存回滚、历史领取、精确大整数、两种裁决顺序、未经授权的放行、请求号参数绑定及重开续办。

## 重扫约定

下列是本次新增行为的调用约定。上面的普通班继续使用原入口。

- `capture.recorder.record_revisable(db, scan_id, device_id, trigger, plan, readings)` 首次登记时选用重扫模式。`capture.recorder.rescan(db, scan_id, trigger, readings)` 仅改写同一原件在原槽中的当前实扫，沿用首次登记的计划与设备。两者成功返回 `(version, trigger)`。`version` 是该原件下唯一的正整数，同值重扫也必须另发版号。一次重扫须给出完整原读并使用未登记过的实际触发。已封定的原件重扫报 `ValueError`，保留原槽与原版。
- `DevicePort.review(scan_id)` 返回当前实扫的 `(version, ScanReadout)`，件头及全部读数属于同一版。`intake.receive.stage_review(db, scan_id, version, reader)` 可预先暂存这份原读，`reader` 按 `reader(scan_id)` 提供上述返回值。返回 `StagedScan`。实际返回版别与请求版别不同，或件头与原行不一致时，报 `ValueError` 且整份不入账。暂存或读过某个版本本身不改变重扫资格。无预先暂存的当前版也可以核准，连通两端时能取齐即可签发。
- `disposition.requests.approve(db, device, request_id, scan_id, version)` 首次办理及重投使用同一入口。返回带 `status` 和 `result_id` 属性的回执。`obsolete` 表示所阅版本已被重扫替换，`pending` 表示尚待确认或续办，均无成果号。`filed` 带正式成果号，表示已签存且已确认设备实际放行。相同请求号绑定原件及版别，同参续办或复领，异参报 `RequestParameterConflict`。别号已封定的同版不可再次核准。对重扫件调用普通入档或撤销应报 `ValueError`，直接放行须受同一资格约束。
- 核准交给 `DevicePort.seal_revision(request_id, scan_id, version)` 裁定。此设备端口可在重开后认回同一请求的实际封定结局。首次向该端口发请求前，档案须已持久登记核准参数。端口失答用 `PortUnavailable` 表示，调用方可以返回待办并重投。档案提交故障可返回待办或传出数据库异常，应保留可续办的原请求。档案正式提交前设备继续占槽，提交成功之后才可按同一核准确认放行。`DevicePort.released(scan_id)` 可按原件查到实际放行的请求号和触发。
- 重扫成果进入现有正式目录，仍用 `archive.retrieve.retrieve(db, result_id)` 查领。返回值以 `version` 属性给出获准实扫版，沿用 `provenance.scan_id`、`provenance.trigger`、`readings`、`unit` 和 `spread`。成果不依赖当前槽。原有成果可用原编号和原字段读取。

`scan_trials.py` 提供交班轨迹和故障夹具。`check-fixture` 用普通件检查端口中断与档案提交失败的注入。新增能力接通后，`verify` 核对两种先后、提交失败续办及断源领取，`demo` 输出亲自运行这些轨迹领到的实扫。将新增核验和演示接入现有 `.pointwise-runtime.json`，继续运行原回归和原交班演示。沿用清单中的 `version`、`kind`、`setup` 及参数数组形式，`verify` 在核对失败时应返回非零。
