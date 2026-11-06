"""One local executor used by the local API and the outbound companion."""
from . import plecs_local, plecs_diagnostics


def execute(owner, payload):
    name, args = payload['tool'], payload.get('arguments') or {}
    context = payload['context']
    def bind():
        state = plecs_local.settings(owner)
        state['model_context'] = context
        plecs_local.save(owner, state)
    def search():
        result = plecs_local.search_models(owner, payload.get('hints', []))
        bind()
        return result
    if name == 'connect_plecs':
        result = plecs_local.ensure_running(owner)
        if result.get('status') == 'ready':
            result['model_search'] = search()
        return result
    if name == 'search_plecs_models':
        return search()
    if name == 'select_plecs_model':
        path = args.get('path', '')
        if not path or path not in payload.get('message', ''):
            raise ValueError('只能选择用户明确提供的模型路径。')
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
        report = plecs_diagnostics.experiment(args, owner, payload.get('result_id'))
        if report.get('status') == 'completed':
            folder = plecs_diagnostics.ROOT / 'runtime/plecs' / __import__('hashlib').sha256(owner.encode()).hexdigest()[:20] / report['experiment_id']
            # Transport only; removed before any content is sent to the model.
            report['_artifacts'] = [(folder / f'case_{i}.csv').read_text(encoding='utf-8') for i in range(len(report['cases']))]
            report['_profile'] = plecs_diagnostics.profiles(owner)[args['model']]
        return report
    raise ValueError('Unsupported PLECS tool')
