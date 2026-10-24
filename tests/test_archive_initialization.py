from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from backend import archive


def test_first_concurrent_requests_initialize_once(tmp_path):
    for attempt in range(8):
        store = archive.ArchiveStore(tmp_path / str(attempt))
        barrier = threading.Barrier(12)
        def read(i):
            barrier.wait(timeout=20)
            return store.index(str(i))
        with ThreadPoolExecutor(max_workers=12) as pool:
            assert list(pool.map(read, range(12))) == [[]] * 12
        # No initialization connection should remain open (important on Windows).
        database = store.directory / "archive.sqlite3"
        database.rename(database.with_suffix(".closed"))


def test_failed_initialization_closes_connection_and_can_retry(tmp_path, monkeypatch):
    store = archive.ArchiveStore(tmp_path)
    connections = []
    connect = sqlite3.connect
    class FailingConnection:
        row_factory = None
        closed = False
        def execute(self, query):
            raise sqlite3.OperationalError("simulated initialization failure")
        def close(self):
            self.closed = True
    def fail_once(*args, **kwargs):
        db = FailingConnection() if not connections else connect(*args, **kwargs)
        connections.append(db)
        return db
    monkeypatch.setattr(archive.sqlite3, "connect", fail_once)
    with pytest.raises(sqlite3.OperationalError):
        store.index("owner")
    assert connections[0].closed
    assert not store._initialized
    assert store.index("owner") == []
