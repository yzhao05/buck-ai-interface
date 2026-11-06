"""Real local RPC integration check on a user-selected model; no model API calls."""
import argparse
import hashlib
import json
from pathlib import Path
from backend import plecs_local, plecs_diagnostics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--executable', default='')
    parser.add_argument('--open-only', action='store_true', help='Check/search/register/open a copy without simulation')
    parser.add_argument('--parameter')
    parser.add_argument('--value', type=float)
    args = parser.parse_args()
    if not args.open_only and (args.parameter is None or args.value is None):
        parser.error('--parameter and --value are required unless --open-only is used')
    source = args.model.resolve()
    owner = 'local-integration-check'
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    plecs_local.configure(owner, args.executable, str(source.parent))
    connection = plecs_local.ensure_running(owner)
    assert connection['status'] == 'ready', connection
    found = plecs_local.search_models(owner, [str(source)])
    assert found['status'] == 'matched', found
    model = plecs_local.register(owner, found['match']['path'])['model']
    opened = plecs_diagnostics.open_model(owner, model)
    assert opened['status'] == 'opened', opened
    try:
        if args.open_only:
            assert hashlib.sha256(source.read_bytes()).hexdigest() == before
            print(json.dumps({'connection':connection,'search':found['status'],'opened':opened,
                              'source_unchanged':True,'simulated':False},ensure_ascii=False))
            return
        result = plecs_diagnostics.experiment({'model': model, 'parameter': args.parameter,
            'values': [args.value], 'hypothesis': 'Selected component sensitivity',
            'expected_observation': 'Measure waveform change for one component intervention'}, owner)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        print(json.dumps({'connection':connection, 'search':found['status'], 'opened':opened,
                          'source_unchanged':True, 'experiment':result}, ensure_ascii=False, indent=2))
        assert result['status'] == 'completed', result
    finally:
        with plecs_diagnostics.proxy() as rpc:
            rpc.plecs.close(opened['opened_model'])


if __name__ == '__main__':
    main()
