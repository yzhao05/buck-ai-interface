"""Run with: python -m backend.plecs_companion --server https://... --owner UUID

Secrets are prompted, never placed in process arguments. Only outbound HTTPS
is required. Local tests may use an explicit loopback HTTP server.
"""
import argparse
import concurrent.futures
from contextlib import contextmanager
import getpass
import hashlib
import json
import os
import re
import time
from urllib.parse import urlparse
import httpx
from . import plecs_local
from .plecs_bridge import RESULT_REJECTIONS
from .plecs_execution import execute


@contextmanager
def exclusive_connector(cache):
    """Only the process owning this outbox may recover its running receipts."""
    with (cache / 'connector.lock').open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
        try:
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SystemExit('Cannot lock the local outbox. Check whether this pairing already has a running connector.') from exc
        yield  # Closing the descriptor releases the OS lock, including after a crash.


def receipt_path(cache, identifier):
    if not re.fullmatch(r'[0-9a-f]{32}',identifier):
        raise ValueError('Invalid bridge job ID')
    return cache / (identifier+'.json')


def save_receipt(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def perform_job(owner, cache, job):
    receipt = receipt_path(cache,job['id'])
    if receipt.exists():
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        return saved.get('result') or {'status':'uncertain','error':'Local execution was interrupted; not repeated.'}
    save_receipt(receipt,{'state':'running'})
    try:
        payload = dict(job['payload'])
        config = payload.pop('settings',{})
        if config:
            plecs_local.configure(owner,config['executable'],config['model_directory'])
        if job['deadline'] < time.time():
            raise ValueError('Job expired before execution')
        result = execute(owner,payload)
    except Exception as exc:
        result = {'status':'failed','error':str(exc)}
    # The delivery state and result are one atomic record. After a process
    # restart, flush_receipts() sends this result without executing the job.
    save_receipt(receipt,{'state':'pending','result':result})
    return result


def bridge_post(client, path, **kwargs):
    """Use the Studio API gateway when its browser entry requires login."""
    response = client.post(path, **kwargs)
    origin = client.base_url
    studio = (origin.scheme == 'https' and origin.port in (None, 443)
              and re.fullmatch(r'(?:[a-z0-9-]+\.ms\.show|studio-[a-z0-9-]+\.api-inference\.modelscope\.net)', origin.host))
    if response.status_code in (401, 403) and studio:
        try:
            body = response.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        error = body.get('error')
        error = error if isinstance(error, dict) else {}
        web_challenge = str(body.get('Code')) == '10011402001'
        api_challenge = (origin.host.endswith('.api-inference.modelscope.net')
                         and response.status_code == 401
                         and 'modelscope token' in str(error.get('message', '')).lower())
        if web_challenge or api_challenge:
            if 'Authorization' not in client.headers:
                if origin.host.endswith('.ms.show'):
                    slug = origin.host.removesuffix('.ms.show')
                    client.base_url = origin.copy_with(host=f'studio-{slug}.api-inference.modelscope.net')
                token = getpass.getpass(f'ModelScope SDK token for {client.base_url.host}: ').strip()
                if not token or not token.isascii() or any(c.isspace() or not c.isprintable() for c in token):
                    raise SystemExit('Enter a valid ModelScope SDK token to access the private Studio.')
                client.headers['Authorization'] = 'Bearer ' + token
                return bridge_post(client, path, **kwargs)
        if web_challenge or api_challenge or response.status_code == 403 or error:
            # Only expose gateway identifiers and known messages. Responses can
            # contain reflected credentials, HTML or other untrusted content.
            identifiers = []
            for name, value in [('code', body.get('Code') or error.get('code')),
                                ('request_id', body.get('RequestId') or error.get('request_id'))]:
                if value is not None and re.fullmatch(r'[0-9a-fA-F-]{1,64}', str(value)):
                    identifiers.append(f'{name}={value}')
            detail = ' '.join(identifiers)
            if web_challenge or api_challenge:
                reason = 'ModelScope rejected the SDK token. Check that it is current and belongs to an account with access to this Studio.'
            else:
                reason = 'Access denied. Check this account can open the private Studio and verify the API endpoint shown by ModelScope.'
            raise SystemExit(f'{reason} HTTP {response.status_code} at {origin.host}{path} {detail}'.rstrip())
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise SystemExit(f'Expected Buck Lab JSON at {path} (HTTP {response.status_code}). Check the backend app URL.')
    return data


def flush_receipts(client, cache, active_id=None):
    for path in sorted(cache.glob('*.json')):
        if path.stem == active_id:
            continue
        receipt_path(cache,path.stem)
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved['state'] in {'delivered','rejected'}:
            continue
        if saved['state']=='running':
            saved = {'state':'pending','result':{'status':'uncertain','error':'Local execution was interrupted; not repeated.'}}
            save_receipt(path,saved)
        try:
            ack = bridge_post(client, '/api/bridge/complete/'+path.stem, json={'result':saved['result']})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            try:
                conflict = exc.response.json()
            except ValueError:
                conflict = None
            if not isinstance(conflict, dict) or conflict.get('detail') not in RESULT_REJECTIONS:
                raise
            print('Result retained locally; cloud task no longer accepts it:',path.stem)
            saved['state']='rejected'
        else:
            if ack.get('status') != 'completed':
                raise SystemExit('Buck Lab did not acknowledge the result. The local receipt is still pending.')
            saved['state']='delivered'
        save_receipt(path,saved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True)
    parser.add_argument('--owner', required=True)
    args = parser.parse_args()
    url = urlparse(args.server)
    if not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
        parser.error('Enter the backend app URL from Connection settings.')
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost','127.0.0.1','::1'}):
        parser.error('Use HTTPS (HTTP is allowed only on loopback).')
    token = getpass.getpass('PLECS pairing token: ').strip()
    if not token or not token.isascii() or not token.isprintable():
        parser.error('Enter the pairing code from the current Buck Lab session.')
    headers = {'X-Session-ID': args.owner, 'X-Bridge-Token': token}
    # Bridge endpoints use the paired secret; the application access token
    # is neither needed nor stored on the companion.
    # Separate different servers and pairing generations; never deliver a
    # revoked pairing's local results using a new credential.
    cache_key = json.dumps([args.server.rstrip('/'),args.owner,token])
    cache = plecs_local.ROOT / ('companion-' + hashlib.sha256(cache_key.encode()).hexdigest()[:32])
    cache.mkdir(parents=True, exist_ok=True)
    print('Connecting to Buck Lab. Keep this terminal open; Ctrl+C stops polling.')
    with exclusive_connector(cache), httpx.Client(base_url=args.server.rstrip('/'), headers=headers, timeout=20, follow_redirects=False) as client, concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        pending = None
        online = False
        while True:
            try:
                # Re-deliver completed receipts after a network interruption;
                # result delivery is idempotent, simulation execution is not retried.
                if pending and pending[1].done():
                    pending[1].result()  # persisted before the future completes
                    pending = None
                flush_receipts(client,cache,pending[0] if pending else None)
                data = bridge_post(client, '/api/bridge/heartbeat' if pending else '/api/bridge/claim')
                if (pending and data.get('status') != 'online') or (not pending and 'job' not in data):
                    raise SystemExit('Unexpected bridge reply. Check the backend app URL and deployed Buck Lab version.')
                if not online:
                    print('PLECS companion connected.')
                    online = True
                job = data.get('job')
                if job:
                    pending = (job['id'], executor.submit(perform_job,args.owner,cache,job))
            except httpx.HTTPError as exc:
                online = False
                if isinstance(exc, httpx.HTTPStatusError):
                    code = exc.response.status_code
                    if code == 401:
                        raise SystemExit('HTTP 401: check the current Buck Lab session and its pairing code.')
                    if code < 500 and code not in (408, 429):
                        raise SystemExit(f'HTTP {code} at {exc.request.url.path}. Check the backend app URL, access and deployment.')
                    print(f'Bridge temporarily unavailable (HTTP {code}). Retrying.')
                else:
                    print('Bridge connection unavailable:', type(exc).__name__)
            time.sleep(2)


if __name__ == '__main__':
    main()
