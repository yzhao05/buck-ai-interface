"""Evidence ledger, not a prescribed reasoning algorithm."""
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1,max_length=80)
    cause: str = Field(min_length=1,max_length=1200)
    status: Literal['open','supported','rejected','unresolved','systematic_floor']
    supporting_evidence: list[str] = Field(default_factory=list,max_length=20)
    opposing_evidence: list[str] = Field(default_factory=list,max_length=20)
    assessment: str = Field(max_length=2000)


class Update(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hypotheses: list[Hypothesis] = Field(max_length=20)
    next_step: str = Field(max_length=2000)
    stop_reason: Literal['continue','sufficient_evidence','systematic_floor','diminishing_returns','needs_user','budget']
    rationale: str = Field(max_length=2000)


TOOLS = [
    {'type':'function','function':{'name':'read_diagnosis','description':'读取当前结果的假设、支持/反对证据和下一步。它是可更新记录，不要求固定顺序或固定实验次数。','parameters':{'type':'object','properties':{},'additionalProperties':False}}},
    {'type':'function','function':{'name':'record_diagnosis','description':'更新原因判断和继续/停止理由。证据引用使用实际工具返回的 evidence_id。系统性误差或边际信息增益很低时可停止；新证据也可以重新打开假设。不要记录内部思维链，只记录可审阅的依据摘要。','parameters':Update.model_json_schema()}}
]


class Notebook:
    def __init__(self, path, result_id):
        self.path, self.result_id = Path(path), result_id
        self.data = {'result_id':result_id,'hypotheses':[],'evidence':{},'experiments':{}}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if saved.get('result_id') == result_id:
                self.data = saved

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        temp.replace(self.path)

    def evidence(self, name, output):
        key = 'e'+str(len(self.data['evidence'])+1)
        self.data['evidence'][key] = {'tool':name,'output':output}
        self.save()
        return key

    def update(self, args):
        update = Update.model_validate(args)
        for hypothesis in update.hypotheses:
            references = hypothesis.supporting_evidence+hypothesis.opposing_evidence
            if any(ref not in self.data['evidence'] for ref in references):
                raise ValueError('Unknown evidence ID; read actual tool results first')
            if hypothesis.status in {'supported','rejected','systematic_floor'} and not references:
                raise ValueError('A resolved hypothesis requires actual evidence')
            if hypothesis.status in {'supported','rejected','systematic_floor'}:
                usable = [self.data['evidence'][ref]['output'] for ref in references]
                if not any(isinstance(item,dict) and not item.get('error') and
                           item.get('status') not in {'failed','unavailable','uncertain','unconfigured'} for item in usable):
                    raise ValueError('Failed or missing checks cannot resolve a hypothesis')
        self.data.update(update.model_dump())
        self.save()
        return {'status':'recorded',**update.model_dump()}
