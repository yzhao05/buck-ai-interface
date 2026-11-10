"""One local executor used by the local API and the outbound companion."""
from . import plecs_local, plecs_diagnostics


def execute(owner, payload):
    name, args = payload['tool'], payload.get('arguments') or {}
    context = payload['context']
    def bind():
        state = plecs_local.settings(owner)
        state['model_context'] = context
        plecs_local.save(owner, state)
    if name == 'check_plecs_connection':
        return {'status':'ready' if plecs_local.listening() else 'unavailable', 'port':1080, 'started':False}
    if name == 'connect_plecs':
        return plecs_local.ensure_running(owner)
    if name == 'search_plecs_models':
        return plecs_local.search_models(owner, args.get('hints', []), args.get('offset',0), args.get('limit',30))
    if name == 'select_plecs_model':
        path = args.get('path', '')
        # Enforce the configured filesystem boundary, not a substring of a chat turn.
        from pathlib import Path
        root = plecs_local.settings(owner).get('model_directory')
        if not path or not root or not Path(path).is_absolute() or not Path(path).resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('模型必须位于连接设置中的电路模型文件夹内。')
        result = plecs_local.register(owner, path)
        bind()
        return result
    if plecs_local.settings(owner).get('model_context') != context:
        raise ValueError('当前数据或电路已改变，请重新搜索或选择模型。')
    if name == 'get_plecs_models':
        return plecs_diagnostics.catalog(owner)
    if name == 'open_plecs_model':
        return plecs_diagnostics.open_model(owner, args.get('model'))
    if name == 'run_plecs_experiment':
        report = plecs_diagnostics.experiment(args, owner, payload.get('result_id'), include_profile=True)
        if report.get('status') == 'completed':
            folder = plecs_diagnostics.ROOT / 'runtime/plecs' / __import__('hashlib').sha256(owner.encode()).hexdigest()[:20] / report['experiment_id']
            # Transport only; removed before any content is sent to the model.
            report['_artifacts'] = [(folder / f'case_{i}.csv').read_text(encoding='utf-8') for i in range(len(report['cases']))]
        return report
    raise ValueError('Unsupported PLECS tool')
