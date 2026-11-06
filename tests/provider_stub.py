"""Provider transport stub for API integration tests, never used by the app."""
import re
import copy
import json
import httpx
from backend import chat
from backend.task_router import make_route, TaskSelection, identification_intent
from backend.knowledge import formula_question
from backend.configuration import configuration_requested


def install(monkeypatch, *, execute=False, controls=False):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    captured = []
    async def classify(request):
        return make_route(rule_selection(request.message), request.message, 'agent')
    monkeypatch.setattr(chat, 'classify', classify)
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            payload = copy.deepcopy(kwargs['json'])
            captured.append(payload)
            if controls and payload['messages'][-1]['role'] != 'tool' and any(t['function']['name']=='run_diagnostic_controls' for t in payload['tools']):
                msg = {'role':'assistant','content':None,'tool_calls':[{'id':'controls','type':'function',
                    'function':{'name':'run_diagnostic_controls','arguments':'{}'}}]}
            elif execute and payload['messages'][-1]['role'] != 'tool':
                msg = {'role':'assistant', 'content':None, 'tool_calls':[{'id':'fit', 'type':'function',
                    'function':{'name':'start_identification', 'arguments':'{}'}}]}
            else:
                msg = {'role':'assistant', 'content':'Provider answer'}
            return httpx.Response(200, request=httpx.Request('POST',url), json={'choices':[{'message':msg}]})
    monkeypatch.setattr(chat.httpx, 'AsyncClient', Provider)
    return captured


# Deterministic classifier responses belong only to mocked-provider tests.
def rule_selection(message):
    text = message.lower()
    if re.search(r'\.plecs\b|(?:plecs|仿真).*(?:排查|实验|启动|唤醒|对照)|自主.*(?:排查|实验)|\bplecs\b.*(?:start|experiment|diagnos)', text):
        return TaskSelection(task='diagnosis')
    if configuration_requested(text):
        return TaskSelection(task="configuration")
    if identification_intent(text) == "execute":
        return TaskSelection(task="identification", secondary_tasks=["knowledge"] if formula_question(text) else [])
    if re.search(r"寿命|老化|器件|硬件|mosfet|igbt|esr|损耗|温升|选型|\b(?:life|lifetime|aging|thermal|hardware)\b", text):
        return TaskSelection(task="hardware", secondary_tasks=["knowledge"] if formula_question(text) else [])
    if formula_question(text) or re.search(r"原理|\bprinciple|how.*identif|辨识方法|如何辨识|怎么辨识", text):
        return TaskSelection(task="knowledge")
    if re.search(r"对比|比较|横向|纵向|误差|修改|计划|结果|偏差|噪声|残差|不确定性|一致性|重复采集|(?:辨识|识别|拟合).*完成|\b(?:compare|comparison|error|errors|result|results|diagnos|plan|rmse|noise|residual|uncertainty|repeatability)", text):
        return TaskSelection(task="diagnosis")
    if re.search(r"上传|导入|列映射|连接|界面|按钮|入口|使用|怎么用|\b(?:upload|import|mapping|connect|setup)\b", text):
        return TaskSelection(task="guidance")
    if re.search(r"波形|纹波|超调|\b(?:waveform|waveforms|ripple|overshoot)\b", text):
        return TaskSelection(task="waveform")
    return TaskSelection(task="general")
