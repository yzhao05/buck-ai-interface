import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient

from backend import archive, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(archive,"store",archive.ArchiveStore(tmp_path))
    monkeypatch.setattr(main,"TOKEN","archive-test-access-token")
    c=TestClient(main.app)
    c.headers.update({"Authorization":"Bearer archive-test-access-token","X-Archive-Key":"a"*64})
    return c


def record():
    return {"id":str(uuid.uuid4()),"updated":100,"meta_updated":100,"file_epoch":0,"draft":"draft","fields":{"vin":"28"},
            "messages":[{"id":"one","created":100,"role":"user","content":"hello"}],"attachment":None}


def test_private_sync_merge_persistence_and_tombstones(client):
    r=record();url="/api/archive/conversations/"+r["id"]
    assert client.post(url,json=r).status_code==200
    assert client.get(url).headers["cache-control"]=="no-store"
    assert client.get("/api/archive",headers={"X-Archive-Key":"b"*64}).json()["conversations"]==[]
    assert client.get(url,headers={"X-Archive-Key":"b"*64}).status_code==404
    assert client.get(url,headers={"Authorization":"Bearer wrong"}).status_code==401
    assert client.get(url,headers={"X-Archive-Key":"bad"}).status_code==400
    newer={**r,"meta_updated":200,"draft":"new draft","messages":[{"id":"two","created":200,"role":"assistant","content":"answer"}]}
    assert client.post(url,json=newer).status_code==200
    merged=client.post(url,json=r).json()["record"]
    assert merged["draft"]=="new draft" and len(merged["messages"])==2
    # A fresh store object (process restart) reads the same on-disk data.
    owner=hashlib.sha256(("a"*64).encode()).hexdigest()
    assert archive.ArchiveStore(archive.store.directory).get(owner,r["id"])["record"]==merged
    assert client.delete(url).status_code==200
    assert client.post(url,json=newer).json()["deleted"] is True
    assert client.get("/api/archive").json()["conversations"]==[{"id":r["id"],"deleted":True}]


def test_file_integrity_clear_generation_and_missing_replica(client):
    r=record();url="/api/archive/conversations/"+r["id"]
    client.post(url,json=r)
    raw=b"time,current,voltage\r\n0,1,12\r\n";sha=hashlib.sha256(raw).hexdigest()
    file_url=url+"/files/"+sha
    assert client.post(file_url+"?epoch=0",files={"file":("test.csv",raw)}).status_code==200
    assert client.get(file_url).content==raw
    assert client.get(file_url,headers={"X-Archive-Key":"b"*64}).status_code==404
    assert client.post(file_url+"?epoch=0",files={"file":("test.csv",b"bad")}).status_code==422
    owner=hashlib.sha256(("a"*64).encode()).hexdigest()
    # Simulate corruption; manifest exposes invalid bytes so a valid browser copy can repair it.
    with archive.store.connect() as db: db.execute("UPDATE files SET raw=? WHERE owner=?",(b"bad",owner))
    assert client.get(url).json()["files"][0]["valid"] is False
    assert client.get(file_url).status_code==409
    assert client.post(file_url+"?epoch=0",files={"file":("test.csv",raw)}).status_code==200
    assert client.get(file_url).content==raw
    # Clear CSV wins over a stale browser upload. Re-upload requires the new epoch.
    client.post(url,json={**r,"file_epoch":300,"meta_updated":300})
    assert client.get(file_url).status_code==404
    assert client.post(file_url+"?epoch=0",files={"file":("test.csv",raw)}).status_code==409
    assert client.post(url,json=r).json()["record"]["file_epoch"]==300
    assert client.post(file_url+"?epoch=300",files={"file":("test.csv",raw)}).status_code==200
    client.delete(url)
    assert client.get(file_url).status_code==404


def test_render_without_persistent_disk_is_reported(client, monkeypatch):
    monkeypatch.setenv("RENDER","true")
    monkeypatch.delenv("ARCHIVE_DIR",raising=False)
    health=client.get("/api/health").json()
    assert health["archive_enabled"] is True
    assert health["archive_persistent"] is False
