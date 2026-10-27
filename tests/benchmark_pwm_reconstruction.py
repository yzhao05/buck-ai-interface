"""Offline paired Case16A/B reconstruction study; never uses evaluation gates/truth in reconstruction.

Archives are pre-existing PLECS noise experiments (clean, 1% and 3%, seeds 12-14).
All filter settings/seeds, including rejected outputs, are retained.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.controller import reconstruct_training, PROFILE
from backend.identification import weak_integral_seed
from backend.schemas import FitOptions


def evaluate_gate(rebuilt, actual, t):
    def edge_errors(direction):
        ref=t[1:][np.diff(actual)==direction];est=t[1:][np.diff(rebuilt)==direction]
        if not len(ref) or not len(est):return {'count_difference':int(len(est)-len(ref)),'nearest_error_p95_s':None}
        distances=np.min(np.abs(ref[:,None]-est[None,:]),axis=1)
        return {'count_difference':int(len(est)-len(ref)),'nearest_error_p95_s':float(np.quantile(distances,.95))}
    cycles=len(t[:-1])//500
    duty=np.mean(rebuilt[:cycles*500].reshape(cycles,500),axis=1)
    reference=np.mean(actual[:cycles*500].reshape(cycles,500),axis=1)
    return {'rising':edge_errors(1),'falling':edge_errors(-1),
        'duty_mae':float(np.mean(np.abs(duty-reference))),
        'per_cycle_high_time_mae_s':float(np.mean(np.abs(duty-reference))*(t[1]-t[0])*500),
        'gate_disagreement_pct':float(100*np.mean(actual!=rebuilt))}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archives',type=Path,default=ROOT.parent/'仿真/buck_converter_with_parameter_sweep/test/results')
    parser.add_argument('--output',type=Path,default=ROOT/'tests/evidence/pwm_reconstruction_case16.json')
    args=parser.parse_args()
    report={'protocol':'Train/holdout split first, 0–0.8ms selected, first 70% only. Raw training current/voltage retained. Actual gates and component truths used only for evaluation. Parameter errors below are weighted-integral initialization, not PANN convergence. Thresholds are engineering screens, no automatic method selection.',
        'source_hashes':{},'code_hashes':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ('backend/controller.py','backend/pwm_reconstruction.py', 'tests/benchmark_pwm_reconstruction.py')},'runs':[]}
    settings=[('none',1)]+[(method,cycles) for method in ('sg','modified_sinc') for cycles in (1,2,4,6,8)]
    for case,truth in [('case16a',np.array([60e-6,500e-6,6.])),('case16b',np.array([40e-6,750e-6,6.]))]:
        path=args.archives/case/'training_inputs.npz';archive=np.load(path,allow_pickle=False)
        report['source_hashes'][case]=hashlib.sha256(path.read_bytes()).hexdigest()
        full_t=archive['time'];split=np.searchsorted(full_t,.7*.0008);t=full_t[:split+1]
        for index in range(7):
            raw=np.column_stack((t,archive['measured'][index,:split+1]))
            for method,cycles in settings:
                started=time.perf_counter();row={'case':case,'noise_pct':0 if index==0 else 1 if index<=3 else 3,'seed':None if index==0 else 12+(index-1)%3,'method':method,'window_cycles':cycles}
                try:
                    train,diag=reconstruct_training(raw,denoise=method,window_cycles=cycles,return_diagnostics=True)
                    row.update(filter=diag['filter'],quality=diag['quality'])
                    # Original archive's 0/1 drive is used AFTER reconstruction.
                    actual=archive['drive'][index,:,0]/28
                    indices=np.clip(np.searchsorted(full_t,train[:,0]+1e-15,side='right')-1,0,len(actual)-1)
                    actual=np.round(actual[indices])
                    row['reference_evaluation']=evaluate_gate(train[:,3],actual,train[:,0])
                    if diag['quality']['status']=='passed':
                        parameters,_=weak_integral_seed(train,FitOptions(mode='LC',method='weighted_integral',pwm_source='controller',controller_confirmed=True))
                        row.update(parameters=parameters.tolist(),parameter_error_pct=(100*(parameters/truth-1)).tolist())
                except ValueError as exc:row['error']=str(exc)
                row['elapsed_s']=time.perf_counter()-started;report['runs'].append(row)
            print(case,index,[(r['method'],r['window_cycles'],r.get('quality',{}).get('status'),r.get('parameter_error_pct')) for r in report['runs'][-len(settings):]],flush=True)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
