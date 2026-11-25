"""Outbound Responses API worker. All request and response data stays in memory."""
import argparse
from contextlib import ExitStack
import getpass
import json
import os
import queue
import sys
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .connector_http import OPENAI_HEARTBEAT_SECONDS, OPENAI_RECONNECT_SECONDS, bridge_post
from .openai_errors import bounded_body, safe_error

ENDPOINT = "https://api.openai.com/v1/responses"
PREFIX = "/api/openai-relay"
RETRY_INTERVAL = 3


def retry_connection(attempt, retryable, stopped):
    deadline = None
    remaining = OPENAI_RECONNECT_SECONDS
    while True:
        try:
            return attempt(remaining)
        except httpx.HTTPError as exc:
            if not retryable(exc) or stopped.is_set():
                raise
            if deadline is None:
                deadline = time.monotonic() + OPENAI_RECONNECT_SECONDS
                print("Connection interrupted. Retrying the current request for up to 60 seconds.",
                      file=sys.stderr, flush=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or stopped.wait(min(RETRY_INTERVAL, remaining)):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise


def retryable_frame_error(exc):
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (408, 425, 429, 500, 502, 503, 504)
    return isinstance(exc, httpx.TransportError)


def batches(upstream, stopped):
    """Drain streaming events during the Studio round trip, with bounded memory."""
    parts = queue.Queue(maxsize=16)

    def put(value):
        while not stopped.is_set():
            try:
                parts.put(value, timeout=.25)
                return
            except queue.Full:
                pass

    def read():
        try:
            for chunk in upstream.iter_bytes():
                for offset in range(0, len(chunk), 65536):
                    if stopped.is_set():
                        return
                    put(chunk[offset:offset + 65536])
        except Exception as exc:
            put(exc)
        finally:
            put(None)

    threading.Thread(target=read, daemon=True).start()
    while not stopped.is_set():
        try:
            part = parts.get(timeout=.5)
        except queue.Empty:
            continue
        data = bytearray()
        while isinstance(part, bytes):
            data.extend(part)
            if len(data) >= 256 * 1024:
                break
            try:
                part = parts.get_nowait()
            except queue.Empty:
                break
        if isinstance(part, Exception):
            raise part
        yield bytes(data), part is None
        if part is None:
            return


def forward(studio, provider, key, job):
    url = f"{PREFIX}/{job['id']}"
    stopped = threading.Event()
    last_contact = time.monotonic()

    def heartbeat():
        nonlocal last_contact
        while not stopped.wait(OPENAI_HEARTBEAT_SECONDS):
            try:
                ack = studio.post(url + "/heartbeat", timeout=10)
                if ack.is_success:
                    last_contact = time.monotonic()
                    continue
                if ack.status_code in (401, 403, 410):
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() - last_contact >= OPENAI_HEARTBEAT_SECONDS + OPENAI_RECONNECT_SECONDS:
                break
        if not stopped.is_set():
            stopped.set()
            provider.close()

    def send(seq, status, data=b"", end=False):
        nonlocal last_contact
        params = {"status": status, "seq": seq, "end": end}

        def post(remaining):
            # If only the acknowledgement was lost, the Studio deduplicates this sequence.
            studio.post(url + "/reply", params=params, content=data,
                        timeout=min(30, remaining)).raise_for_status()

        retry_connection(post, retryable_frame_error, stopped)
        last_contact = time.monotonic()

    monitor = threading.Thread(target=heartbeat, daemon=True)
    monitor.start()
    started = False
    try:
        payload = job["payload"]
        if payload.get("store") is not False:
            raise ValueError("Expected an ephemeral Responses request")
        with ExitStack() as stack:
            def connect(remaining):
                stream = provider.stream("POST", ENDPOINT, json=payload, headers={"Authorization": "Bearer " + key},
                                         timeout=httpx.Timeout(None, connect=min(20, remaining)))
                return stack.enter_context(stream)

            # Only retry failures before a provider connection was established.
            upstream = retry_connection(connect, lambda exc: isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)), stopped)
            started = True
            # Finite frames also work with Studio gateways that buffer HTTP request bodies.
            if not upstream.is_success:
                detail = safe_error(bounded_body(upstream), upstream.headers.get("x-request-id"))
                send(0, upstream.status_code, json.dumps({"error": detail}).encode(), end=True)
                return
            send(0, upstream.status_code)
            for seq, (data, end) in enumerate(batches(upstream, stopped), 1):
                send(seq, upstream.status_code, data, end)
    except Exception as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 410:
            return  # The Studio already closed or cancelled this request.
        try:
            if not started and isinstance(exc, httpx.RequestError):
                send(0, 599, end=True)
            else:
                studio.post(url + "/failed")
        except httpx.HTTPError:
            pass  # A disconnected worker also loses its per-request lease.
        raise
    finally:
        stopped.set()
        monitor.join(timeout=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="Buck Lab Studio HTTPS origin")
    args = parser.parse_args()
    url = urlsplit(args.server)
    if (url.scheme != "https" and not (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1"))
            or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/")):
        parser.error("Use an HTTPS Studio origin or a loopback development server")
    key = os.getenv("OPENAI_API_KEY", "").strip() or getpass.getpass("OpenAI API key: ").strip()
    token = os.getenv("OPENAI_RELAY_TOKEN", "").strip() or getpass.getpass("Studio OPENAI_RELAY_TOKEN: ").strip()
    if len(token) < 32 or not key:
        parser.error("Enter an API key and the Studio relay secret (at least 32 characters)")
    headers = {"X-Buck-Relay-Token": token, "X-Relay-Instance": uuid4().hex}
    with httpx.Client(base_url=args.server, headers=headers, timeout=30, follow_redirects=False) as studio:
        online = False
        claim_sequence = 0
        while True:
            try:
                job = bridge_post(studio, PREFIX + "/claim", headers={"X-Relay-Claim": str(claim_sequence)})
                # A lost reply retries this sequence; forwarding always begins with the next poll ID reserved.
                claim_sequence += 1
                if not online:
                    print("Connected. Waiting for OpenAI requests.", flush=True)
                    online = True
                if not job:
                    time.sleep(2)
                    continue
                with httpx.Client(timeout=httpx.Timeout(None, connect=20), follow_redirects=False) as provider:
                    forward(studio, provider, key, job)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    raise SystemExit("Studio rejected the connector credential. Check the relay secret and Studio access.")
                online = False
                print(f"Connector HTTP {exc.response.status_code}. Retrying in 3 seconds.", file=sys.stderr, flush=True)
                time.sleep(3)
            except httpx.HTTPError as exc:
                print(f"Connector connection error ({type(exc).__name__}).", file=sys.stderr, flush=True)
                online = False
                time.sleep(3)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
