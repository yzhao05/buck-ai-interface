"""One outbound connector for this Studio. Payloads and leases live only in RAM."""
import asyncio
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
import hmac
import os
from threading import Condition
import time
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request


def configured():
    return bool(os.environ.get("OPENAI_RELAY_TOKEN"))


def status():
    if not configured():
        return "direct"
    if len(os.environ["OPENAI_RELAY_TOKEN"]) < 32:
        return "invalid_token"
    with changed:
        return "online" if worker and time.monotonic() - last_contact < lease_seconds else "offline"


class RelayUnavailable(RuntimeError):
    pass


def authenticate(x_buck_relay_token: str = Header(default="")):
    expected = os.environ.get("OPENAI_RELAY_TOKEN", "")
    if len(expected) < 32 or not hmac.compare_digest(expected.encode(), x_buck_relay_token.encode()):
        raise HTTPException(403, "Invalid connector credential")


router = APIRouter(prefix="/api/openai-relay", dependencies=[Depends(authenticate)])
lease_seconds = 45
changed = Condition()
last_contact = 0.
worker = None
jobs = {}


@dataclass
class Job:
    payload: dict
    claimed: bool = False
    seen: float = 0.
    sequence: int = 0
    status: int | None = None
    complete: bool = False
    chunks: deque = field(default_factory=deque)
    failure: str = ""


def touch(identity):
    global worker, last_contact
    now = time.monotonic()
    if worker != identity and now - last_contact < lease_seconds and worker is not None:
        raise HTTPException(409, "Another connector is online")
    if worker != identity or now - last_contact >= lease_seconds:
        for job in jobs.values():
            if job.claimed and not job.complete:
                job.failure = "OpenAI connector disconnected during the request"
    worker, last_contact = identity, now
    changed.notify_all()


@router.post("/claim")
def claim(x_relay_instance: str = Header(min_length=16, max_length=64)):
    with changed:
        touch(x_relay_instance)
        for key, job in jobs.items():
            if not job.claimed and not job.failure:
                job.claimed = True
                job.seen = time.monotonic()
                return {"id": key, "payload": job.payload}
    return {}


def active_job(key, identity):
    job = jobs.get(key)
    if (not job or not job.claimed or job.failure or worker != identity
            or time.monotonic() - job.seen >= lease_seconds):
        raise HTTPException(410, "Request has ended")
    touch(identity)
    job.seen = last_contact
    return job


@router.post("/{key}/heartbeat")
def heartbeat(key: str, x_relay_instance: str = Header()):
    with changed:
        active_job(key, x_relay_instance)
    return {}


@router.post("/{key}/failed")
def failed(key: str, x_relay_instance: str = Header()):
    with changed:
        job = active_job(key, x_relay_instance)
        if not job.complete:
            job.failure = "OpenAI connector response was interrupted"
        changed.notify_all()
    return {}


def check(job):
    if job.failure:
        raise RelayUnavailable(job.failure)
    if worker is None or time.monotonic() - (job.seen if job.claimed else last_contact) >= lease_seconds:
        raise RelayUnavailable("OpenAI connector is offline")


@router.post("/{key}/reply")
async def reply(key: str, request: Request, status: int, seq: int, end: bool = False,
                x_relay_instance: str = Header()):
    if not 200 <= status <= 599 or seq < 0:
        raise HTTPException(422, "Invalid response frame")
    with changed:
        active_job(key, x_relay_instance)
    data = bytearray()
    async for part in request.stream():
        if len(data) + len(part) > 512 * 1024:
            raise HTTPException(413, "Frame too large")
        data.extend(part)
    while True:
        with changed:
            job = active_job(key, x_relay_instance)
            if seq < job.sequence:
                return {}
            if job.complete or seq != job.sequence or job.status not in (None, status):
                raise HTTPException(409, "Unexpected response frame")
            if not data or len(job.chunks) < 8:
                job.status = status
                if data:
                    job.chunks.append(bytes(data))
                job.sequence += 1
                job.complete = end
                changed.notify_all()
                return {}
        await asyncio.sleep(.05)


@contextmanager
def response(payload, cancelled=None):
    if status() == "invalid_token":
        raise RelayUnavailable("Studio OPENAI_RELAY_TOKEN must contain at least 32 characters")
    key = uuid4().hex
    job = Job(payload)
    def available():
        if cancelled and cancelled.is_set():
            raise RelayUnavailable("Request ended")
        check(job)

    def chunks():
        while True:
            with changed:
                if job.failure:
                    raise RelayUnavailable(job.failure)
                if job.chunks:
                    chunk = job.chunks.popleft()
                elif job.complete:
                    return
                else:
                    available()
                    changed.wait(1)
                    continue
            yield chunk
    with changed:
        check(job)
        jobs[key] = job
    try:
        with changed:
            while job.status is None:
                available()
                changed.wait(1)
        result = httpx.Response(job.status, content=chunks(), request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        try:
            if job.status == 599:
                raise RelayUnavailable("Local connector could not reach OpenAI")
            result.raise_for_status()
            yield result
        finally:
            result.close()
    finally:
        with changed:
            jobs.pop(key, None)
            changed.notify_all()


def request_json(payload):
    with response(payload) as result:
        result.read()
        return result.json()
