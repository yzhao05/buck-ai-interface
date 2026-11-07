"""Memory ownership regressions for frequent polling, parsing and Agent starts."""
import asyncio
import sqlite3
import hashlib
import json
import tracemalloc
import threading
from collections import OrderedDict

import pytest

from backend import agent_tasks, plecs_bridge, harness_runtime, main
from backend.data import read_csv
from backend.archive import ArchiveStore
from conftest import REAL_RUN
from backend.provider_errors import AgentCapacityError, AgentMemoryError, provider_failure
from backend import memory_usage


def test_capacity_error_is_not_reported_as_bad_api_key():
    result = provider_failure(AgentCapacityError())
    assert result['error_code'] == 'agent_capacity_busy'
    assert 'API key' not in result['provider_error']


def test_archive_manifest_has_bounded_python_peak(tmp_path):
    store = ArchiveStore(tmp_path)
    record = dict(id='test', updated=1, file_epoch=0, messages=[])
    store.merge('owner', record)
    raw = b'1' * (8 * 1024 * 1024)
    store.save_file('owner', 'test', 0, hashlib.sha256(raw).hexdigest(), 'wave.csv', raw)
    tracemalloc.start()
    try:
        result = store.merge('owner', record)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert result['files'][0]['valid']
    assert peak < len(raw) // 2


def test_heartbeat_does_not_load_saved_analysis_request(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_tasks, 'ROOT', tmp_path)
    request = json.dumps(dict(current_result_analysis=True, result_id='fit', history='x'*4_000_000))
    with agent_tasks.database() as db:
        db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)', ('task','owner','completed',request,'{}',0))
    tracemalloc.start()
    try:
        snapshot = agent_tasks.read('owner','task',include_binding=False)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < len(request) // 2
    assert snapshot['state'] == 'completed'
    assert agent_tasks.read('owner','task')['analysis_result_id'] == 'fit'


def test_render_guard_closes_running_harness_and_allows_retry(tmp_path, monkeypatch):
    monkeypatch.setenv('RENDER', 'true')
    monkeypatch.setattr(memory_usage, 'CGROUP', tmp_path)
    mib = 1024 * 1024
    (tmp_path/'memory.max').write_text(str(512*mib))
    (tmp_path/'memory.current').write_text(str(500*mib))
    (tmp_path/'memory.stat').write_text(f'inactive_file {64*mib}')
    closed = []
    async def work(*args):
        (tmp_path/'memory.stat').write_text('inactive_file 0')
        try:
            await asyncio.Event().wait()
        finally:
            closed.append(True)
    async def success(*args):
        return 'ok'
    async def scenario():
        monkeypatch.setattr(harness_runtime, '_run', work)
        with pytest.raises(AgentMemoryError) as exc:
            await asyncio.wait_for(REAL_RUN([], [], None, None), 5)
        assert provider_failure(exc.value)['error_code'] == 'agent_memory_pressure'
        assert closed == [True]
        monkeypatch.setattr(harness_runtime, '_run', success)
        with pytest.raises(AgentMemoryError):
            await REAL_RUN([], [], None, None)
        (tmp_path/'memory.current').write_text(str(190*mib))
        assert await REAL_RUN([], [], None, None) == 'ok'
    asyncio.run(scenario())


@pytest.mark.parametrize('module', [agent_tasks, plecs_bridge])
@pytest.mark.parametrize('fail', [False, True])
def test_database_connection_is_closed(module, fail, tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    try:
        with module.database() as connection:
            connection.execute('SELECT 1')
            if fail:
                raise ValueError('rollback')
    except ValueError:
        pass
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')


def test_runtime_slot_covers_direct_calls_and_releases_on_cancel(monkeypatch):
    monkeypatch.setattr(harness_runtime, 'runtime_slots', threading.BoundedSemaphore(1))
    async def scenario():
        entered = asyncio.Event()
        async def blocking(*args):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(harness_runtime, '_run', blocking)
        task = asyncio.create_task(REAL_RUN([], [], None, None))
        await entered.wait()
        with pytest.raises(RuntimeError, match='capacity'):
            await REAL_RUN([], [], None, None)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async def success(*args):
            return 'ok'
        monkeypatch.setattr(harness_runtime, '_run', success)
        assert await REAL_RUN([], [], None, None) == 'ok'
    asyncio.run(scenario())


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'gb18030'])
def test_csv_encoding_and_values_are_preserved(encoding):
    raw = ('时间;电流;电压\r\n' + ''.join(f'{i};1;2\r\n' for i in range(10000))).encode(encoding)
    data = read_csv(raw, 'test.csv')
    assert list(data.frame.columns) == ['时间', '电流', '电压']
    assert data.frame.iloc[-1].tolist() == [9999, 1, 2]


def test_parser_capacity_and_failure_release(monkeypatch):
    slot = threading.Lock()
    monkeypatch.setattr(main, 'dataset_parse_slot', slot)
    slot.acquire()
    with pytest.raises(main.HTTPException) as exc:
        main.add_dataset(b'', 'test.csv', 'owner')
    assert exc.value.status_code == 503
    slot.release()
    with pytest.raises(main.HTTPException):
        main.add_dataset(b'', 'test.csv', 'owner')
    assert slot.acquire(blocking=False)
    slot.release()


def test_upload_keeps_active_dataset_in_memory_accounting(monkeypatch):
    monkeypatch.setattr(main, 'datasets', OrderedDict())
    monkeypatch.setattr(main, 'jobs', OrderedDict())
    raw = b'time,current,voltage\n' + b'0,1,2\n' * 30
    first = main.add_dataset(raw, 'one.csv', 'owner')['dataset_id']
    main.add_dataset(raw, 'two.csv', 'owner')
    main.jobs['active'] = {'dataset_id': first, 'status': 'running'}
    main.add_dataset(raw, 'three.csv', 'owner')
    assert first in main.datasets
    assert main.datasets[first]['size'] > len(raw)
    assert len(main.datasets) == 2
    second = next(key for key in main.datasets if key != first)
    main.jobs['queued'] = {'dataset_id': second, 'status': 'queued'}
    with pytest.raises(main.HTTPException) as exc:
        main.add_dataset(raw, 'four.csv', 'owner')
    assert exc.value.status_code == 409
    assert set(main.datasets) == {first, second}
