"""End-to-end PANN smoke check after selecting the offline SG baseline.

Two 3% noise / seed12 cases; this is not the filter-selection sweep.
"""
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.data import read_csv
from backend.identification import identify
from backend.schemas import FitOptions, Mapping


def main():
    report = {'protocol':'Case16A/B, 3% noise seed12, raw three-column CSV, SG six-cycle reconstruction, fixed R=6, PANN joint initial states, 200 epochs. Truth used only after identification; no measured PWM supplied.',
              'code_hashes':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                ('backend/pwm_reconstruction.py','backend/controller.py','backend/identification.py','backend/pann_adapter.py')}, 'runs':[]}
    for case, truth in [('case16a',np.array([60e-6,500e-6,6.])),('case16b',np.array([40e-6,750e-6,6.]))]:
        source=ROOT.parent/'仿真/buck_converter_with_parameter_sweep/test/results'/case/'training_inputs.npz'
        archive=np.load(source,allow_pickle=False)
        frame=pd.DataFrame(np.column_stack((archive['time'],archive['measured'][4])),columns=['t','i','v'])
        data=read_csv(frame.to_csv(index=False).encode(),case+'_noise03_seed12.csv')
        result=identify(data,Mapping(time='t',current='i',voltage='v'),FitOptions(mode='LC',pwm_source='controller',
            controller_confirmed=True,method='pann',epochs=200,start_s=0,end_s=.0008),
            progress=lambda stage: print(case,stage,flush=True))
        p=result['parameters']
        errors=(100*(np.array([p['L_H'],p['C_F'],p['R_ohm']])/truth-1)).tolist()
        report['runs'].append({'case':case,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'parameter_error_pct':errors,'result':{k:v for k,v in result.items() if k!='chart'}})
        (ROOT/'tests/evidence/pwm_pann_smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(case,'finished',p,'signed parameter errors %',errors,flush=True)


if __name__=='__main__':main()
