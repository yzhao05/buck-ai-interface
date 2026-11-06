"""Run with: python -m backend.plecs_companion --server https://... --owner UUID

Secrets are prompted, never placed in process arguments. Only outbound HTTPS
is required. Local tests may use an explicit loopback HTTP server.
"""
import argparse
import concurrent.futures
import getpass
import hashlib
import json
import re
import time
from urllib.parse import urlparse
import httpx
from . import plecs_local
from .plecs_execution import execute


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
        response = client.post('/api/bridge/complete/'+path.stem,json={'result':saved['result']})
        if response.status_code in (400,404,409):
            print('Result retained locally; cloud task no longer accepts it:',path.stem)
            saved['state']='rejected'
        else:
            response.raise_for_status()
            saved['state']='delivered'
        save_receipt(path,saved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', required=True)
    parser.add_argument('--owner', required=True)
    args = parser.parse_args()
    url = urlparse(args.server)
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost','127.0.0.1','::1'}):
        parser.error('Use HTTPS (HTTP is allowed only on loopback).')
    token = getpass.getpass('PLECS pairing token: ')
    headers = {'X-Session-ID': args.owner, 'X-Bridge-Token': token}
    # Bridge endpoints use the paired secret; the application access token
    # is neither needed nor stored on the companion.
    # Separate different servers and pairing generations; never deliver a
    # revoked pairing's local results using a new credential.
    cache_key = json.dumps([args.server.rstrip('/'),args.owner,token])
    cache = plecs_local.ROOT / ('companion-' + hashlib.sha256(cache_key.encode()).hexdigest()[:32])
    cache.mkdir(parents=True, exist_ok=True)
    print('PLECS companion connected. Keep this terminal open; Ctrl+C stops polling.')
    with httpx.Client(base_url=args.server.rstrip('/'), headers=headers, timeout=20, follow_redirects=False) as client, concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        pending = None
        while True:
            try:
                # Re-deliver completed receipts after a network interruption;
                # result delivery is idempotent, simulation execution is not retried.
                if pending and pending[1].done():
                    pending[1].result()  # persisted before the future completes
                    pending = None
                flush_receipts(client,cache,pending[0] if pending else None)
                response = client.post('/api/bridge/heartbeat' if pending else '/api/bridge/claim')
                response.raise_for_status()
                job = response.json().get('job')
                if job:
                    pending = (job['id'], executor.submit(perform_job,args.owner,cache,job))
            except httpx.HTTPError as exc:
                print('Bridge connection unavailable:', type(exc).__name__)
            time.sleep(2)


if __name__ == '__main__':
    main()
