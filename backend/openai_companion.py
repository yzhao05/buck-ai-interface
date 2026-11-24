"""Outbound Responses API worker. All request and response data stays in memory."""
import argparse
import getpass
import os
import queue
import sys
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .connector_http import bridge_post

ENDPOINT = "https://api.openai.com/v1/responses"
PREFIX = "/api/openai-relay"


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

    def heartbeat():
        last_ok = time.monotonic()
        while not stopped.wait(5):
            try:
                ack = studio.post(url + "/heartbeat", timeout=10)
                if ack.is_success:
                    last_ok = time.monotonic()
                    continue
                if ack.status_code in (401, 403, 410):
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() - last_ok >= 45:
                break
        if not stopped.is_set():
            stopped.set()
            provider.close()

    def send(seq, status, data=b"", end=False):
        params = {"status": status, "seq": seq, "end": end}
        try:
            reply = studio.post(url + "/reply", params=params, content=data)
        except httpx.TransportError:
            # The server may have accepted the frame before the acknowledgement was lost.
            reply = studio.post(url + "/reply", params=params, content=data)
        reply.raise_for_status()

    monitor = threading.Thread(target=heartbeat, daemon=True)
    monitor.start()
    started = False
    try:
        payload = job["payload"]
        if payload.get("store") is not False:
            raise ValueError("Expected an ephemeral Responses request")
        with provider.stream("POST", ENDPOINT, json=payload, headers={"Authorization": "Bearer " + key}) as upstream:
            started = True
            # Finite frames also work with Studio gateways that buffer HTTP request bodies.
            send(0, upstream.status_code, end=not upstream.is_success)
            if upstream.is_success:
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
        while True:
            try:
                job = bridge_post(studio, PREFIX + "/claim")
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
                print(f"Connector HTTP {exc.response.status_code}. Waiting to reconnect.", file=sys.stderr, flush=True)
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
