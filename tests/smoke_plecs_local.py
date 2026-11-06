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
    parser.add_argument('--parameter', required=True)
    parser.add_argument('--value', required=True, type=float)
    args = parser.parse_args()
    source = args.model.resolve()
    owner = 'local-integration-check'
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    plecs_local.configure(owner, args.executable, str(source.parent))
    connection = plecs_local.ensure_running(owner)
    assert connection['status'] == 'ready', connection
    found = plecs_local.search_models(owner, [str(source)])
    assert found['status'] == 'selected', found
    model = found['selection']['model']
    opened = plecs_diagnostics.open_model(owner, model)
    assert opened['status'] == 'opened', opened
    try:
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
