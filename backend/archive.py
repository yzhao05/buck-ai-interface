"""Private, non-expiring conversation/CSV replicas. Recovery keys are never stored.

SQLite transactions merge immutable message IDs, latest metadata and permanent
conversation tombstones. File generations prevent stale clients undoing Clear CSV.
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import Field

from .data import MAX_BYTES
from .memory_usage import log_memory
from .schemas import StrictModel, SavedExperiment
from .vision import ChatImage


class SavedMessage(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    created: int = Field(ge=0, le=9007199254740991)
    role: Literal["user", "assistant"]
    content: str = Field(max_length=100000)
    mode: str = Field(default="", max_length=200)
    response: dict | None = None
    images: list[ChatImage] = Field(default_factory=list, max_length=3)


class SavedConversation(StrictModel):
    id: UUID
    updated: int = Field(ge=0, le=9007199254740991)
    meta_updated: int = Field(default=0, ge=0, le=9007199254740991)
    file_epoch: int = Field(default=0, ge=0, le=9007199254740991)
    draft: str = Field(default="", max_length=4000)
    fields: dict[str, str] = Field(default_factory=dict, max_length=30)
    attachment: dict | None = None
    messages: list[SavedMessage] = Field(default_factory=list, max_length=10000)
    experiments: list[SavedExperiment] = Field(default_factory=list, max_length=200)


class ArchiveStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._initialization_lock = threading.Lock()
        self._initialized = False

    @contextmanager
    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.directory / "archive.sqlite3", timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with self._initialization_lock:
                if not self._initialized:
                    db.execute("PRAGMA journal_mode=WAL")
                    db.execute("CREATE TABLE IF NOT EXISTS conversations (owner TEXT, id TEXT, deleted INTEGER NOT NULL DEFAULT 0, payload TEXT, PRIMARY KEY(owner,id))")
                    db.execute("CREATE TABLE IF NOT EXISTS files (owner TEXT, id TEXT, epoch INTEGER, sha TEXT, filename TEXT, raw BLOB, PRIMARY KEY(owner,id,sha))")
                    db.execute("CREATE TABLE IF NOT EXISTS archive_versions (owner TEXT, id TEXT, revision TEXT, updated INTEGER, PRIMARY KEY(owner,id))")
                    # Tokens change for metadata, file writes and deletions, including
                    # legacy clients. No CSV bytes are read to build the index.
                    for event in ('INSERT', 'UPDATE'):
                        db.execute(f"""CREATE TRIGGER IF NOT EXISTS archive_conversation_{event.lower()}
                            AFTER {event} ON conversations BEGIN
                            INSERT OR REPLACE INTO archive_versions VALUES
                            (NEW.owner, NEW.id, lower(hex(randomblob(16))), COALESCE(json_extract(NEW.payload,'$.updated'),0)); END""")
                    for event, row in (('INSERT','NEW'), ('UPDATE','NEW'), ('DELETE','OLD')):
                        db.execute(f"""CREATE TRIGGER IF NOT EXISTS archive_file_{event.lower()}
                            AFTER {event} ON files BEGIN UPDATE archive_versions
                            SET revision=lower(hex(randomblob(16))) WHERE owner={row}.owner AND id={row}.id; END""")
                    db.execute("""INSERT OR IGNORE INTO archive_versions
                        SELECT owner,id,lower(hex(randomblob(16))),COALESCE(json_extract(payload,'$.updated'),0) FROM conversations""")
                    db.commit()
                    self._initialized = True
            with db: yield db
        finally: db.close()

    def index(self, owner, versions=False):
        with self.connect() as db:
            if versions:
                return [dict(r) for r in db.execute("""SELECT c.id,c.deleted,v.revision,v.updated
                    FROM conversations c LEFT JOIN archive_versions v ON c.owner=v.owner AND c.id=v.id
                    WHERE c.owner=?""", (owner,))]
            return [{"id":r["id"], "deleted":bool(r["deleted"])} for r in db.execute("SELECT id,deleted FROM conversations WHERE owner=?",(owner,))]

    @staticmethod
    def _revision(db, owner, identifier):
        row = db.execute("SELECT revision FROM archive_versions WHERE owner=? AND id=?", (owner, identifier)).fetchone()
        return row['revision'] if row else None

    def get(self, owner, identifier, verify=True):
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM conversations WHERE owner=? AND id=?",(owner,identifier)).fetchone()
            if row is None: raise HTTPException(404, "Archive conversation not found")
            if row["deleted"]: return {"deleted":True,"id":identifier}
            record = json.loads(row["payload"])
            files = self._manifest(db, owner, identifier, record["file_epoch"], verify)
            return {"deleted":False,"record":record,"files":files,"revision":self._revision(db, owner, identifier)}

    @staticmethod
    def _manifest(db, owner, identifier, epoch, verify=True):
        files = []
        rows = db.execute("SELECT rowid,sha,filename,length(raw) AS size FROM files WHERE owner=? AND id=? AND epoch=?",
                          (owner, identifier, epoch))
        for row in rows:
            valid = True  # Uploads are verified; periodic/explicit audits recheck storage.
            if verify:
                digest = hashlib.sha256()
                with db.blobopen("files", "raw", row["rowid"], readonly=True) as blob:
                    while chunk := blob.read(64 * 1024):
                        digest.update(chunk)
                valid = digest.hexdigest() == row['sha']
            files.append({"sha256":row["sha"], "filename":row["filename"], "size":row["size"],
                          "valid":valid, "verified":verify})
        return files

    def merge(self, owner, incoming, verify=True):
        identifier = incoming["id"]
        if len(json.dumps(incoming, ensure_ascii=False).encode()) > 8*1024*1024:
            raise HTTPException(413, "Conversation archive exceeds 8 MB")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM conversations WHERE owner=? AND id=?",(owner,identifier)).fetchone()
            if row and row["deleted"]: return {"deleted":True,"id":identifier}
            previous = json.loads(row["payload"]) if row else incoming
            # Stable tie-break avoids oscillation for identical client timestamps.
            def version(r):
                meta = {k:r.get(k) for k in ("draft","fields","attachment")}
                return (r.get("meta_updated",0), json.dumps(meta,sort_keys=True,ensure_ascii=False))
            merged = dict(max((previous,incoming), key=version))
            messages = {m["id"]:m for m in incoming["messages"]}
            messages.update({m["id"]:m for m in previous["messages"]})
            merged["messages"] = sorted(messages.values(),key=lambda m:(m["created"],m["id"]))
            experiments = {}
            for run in [*previous.get("experiments", []), *incoming.get("experiments", [])]:
                old = experiments.get(run["id"])
                def run_version(item):
                    return (item["updated"], json.dumps(item, sort_keys=True))
                if old is None or run_version(run) > run_version(old):
                    experiments[run["id"]] = run
            merged["experiments"] = sorted(experiments.values(), key=lambda r:(r["created"],r["id"]))
            merged["updated"] = max(previous["updated"],incoming["updated"])
            merged["file_epoch"] = max(previous.get("file_epoch",0),incoming.get("file_epoch",0))
            attachment = merged.get("attachment")
            if attachment and attachment.get("csvEpoch",0) != merged["file_epoch"]: merged["attachment"] = None
            encoded = json.dumps(merged,ensure_ascii=False)
            if len(encoded.encode()) > 8*1024*1024 or len(messages)>10000 or len(experiments)>200:
                raise HTTPException(413,"Conversation archive is full; start a new conversation")
            if row is None or json.loads(row['payload']) != merged:
                db.execute("INSERT OR REPLACE INTO conversations VALUES(?,?,0,?)",(owner,identifier,encoded))
            db.execute("DELETE FROM files WHERE owner=? AND id=? AND epoch<>?",(owner,identifier,merged["file_epoch"]))
            files = self._manifest(db, owner, identifier, merged["file_epoch"], verify)
            revision = self._revision(db, owner, identifier)
        return {"deleted":False, "record":merged, "files":files, "revision":revision}

    def delete(self, owner, identifier):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO conversations VALUES(?,?,1,NULL)",(owner,identifier))
            db.execute("DELETE FROM files WHERE owner=? AND id=?",(owner,identifier))

    def save_file(self, owner, identifier, epoch, sha, filename, raw):
        if not raw or len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=sha:
            raise HTTPException(422,"CSV checksum or size is invalid")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM conversations WHERE owner=? AND id=?",(owner,identifier)).fetchone()
            if not row or row["deleted"] or json.loads(row["payload"])["file_epoch"]!=epoch:
                raise HTTPException(409,"Archive changed; synchronize again")
            used = db.execute("SELECT COALESCE(SUM(length(raw)),0) FROM files WHERE owner=? AND NOT(id=? AND sha=?)",(owner,identifier,sha)).fetchone()[0]
            if used+len(raw)>int(os.getenv("ARCHIVE_MAX_BYTES",str(512*1024*1024))):
                raise HTTPException(507,"Backend archive storage quota exceeded")
            db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?)",(owner,identifier,epoch,sha,filename[:150],raw))

    def file(self, owner, identifier, sha):
        with self.connect() as db:
            row = db.execute("SELECT raw FROM files WHERE owner=? AND id=? AND sha=?",(owner,identifier,sha)).fetchone()
            if row is None: raise HTTPException(404,"Archived CSV not found")
            if hashlib.sha256(row["raw"]).hexdigest()!=sha: raise HTTPException(409,"Archived CSV checksum failed")
            return row["raw"]


store = ArchiveStore(Path(__file__).resolve().parents[1] / "runtime" / "archive")
router = APIRouter(prefix="/api/archive")


def owner(x_archive_key: str = Header(...)):
    if not re.fullmatch(r"[a-f0-9]{64}",x_archive_key): raise HTTPException(400,"Invalid sync recovery code")
    return hashlib.sha256(x_archive_key.encode()).hexdigest()


@router.get("")
def index(account=Depends(owner), x_archive_protocol: str = Header(default='')):
    return {"conversations":store.index(account, versions=x_archive_protocol == '2')}


@router.get("/conversations/{identifier}")
def get_conversation(identifier: UUID, verify: bool = True, account=Depends(owner)):
    return store.get(account,str(identifier),verify=verify)


@router.post("/conversations/{identifier}")
def merge_conversation(identifier: UUID, body: SavedConversation, verify: bool = True, account=Depends(owner)):
    if identifier != body.id: raise HTTPException(422,"Conversation ID mismatch")
    log_memory('before_archive_merge')
    try:
        return store.merge(account,body.model_dump(mode="json"),verify=verify)
    finally:
        log_memory('after_archive_merge')


@router.delete("/conversations/{identifier}")
def delete_conversation(identifier: UUID, account=Depends(owner)):
    store.delete(account,str(identifier))
    return {"deleted":True}


@router.post("/conversations/{identifier}/files/{sha}")
async def save_file(identifier: UUID, sha: str, epoch: int, file: UploadFile = File(...), account=Depends(owner)):
    try:
        raw = await file.read(MAX_BYTES+1)
        store.save_file(account,str(identifier),epoch,sha,Path(file.filename or "waveform.csv").name,raw)
        return {"sha256":sha}
    finally: await file.close()


@router.get("/conversations/{identifier}/files/{sha}")
def get_file(identifier: UUID, sha: str, account=Depends(owner)):
    return Response(store.file(account,str(identifier),sha),media_type="text/csv",headers={"Cache-Control":"no-store"})
