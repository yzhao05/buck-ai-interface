"""Known parasitics: independent circuit data, identification, DCM and gradients."""
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from pydantic import ValidationError

from backend.schemas import FitOptions
from backend.identification import integral_fit, weak_integral_seed, rollout
from backend.parasitics import resistances


def options(**changes):
    return FitOptions(**dict(dict(include_parasitics=True, inductor_resistance_ohm=.25,
        capacitor_esr_ohm=.12, mode="LC", resistance_ohm=6, method="weighted_integral"), **changes))


@pytest.fixture(scope="module")
def circuit_data():
    # Independent nodal equations, RK45 at exact switching edges; no fitter rollout.
    L, C, R, rl, rc = 60e-6, 500e-6, 6., .25, .12
    t = np.arange(8001)*50e-9
    gate = (np.arange(len(t)) % 200 < 90).astype(float)
    states = np.empty((len(t), 2)); states[0] = [2., 8.]
    edges = np.unique(np.r_[0, np.flatnonzero(np.diff(gate))+1, len(t)-1])
    for a,b in zip(edges[:-1],edges[1:]):
        def rhs(_,x):
            i,vc=x
            ic=(i-vc/R)/(1+rc/R)
            vo=vc+rc*ic
            return [(28*gate[a]-vo-rl*i)/L,ic/C]
        result=solve_ivp(rhs,(t[a],t[b]),states[a],t_eval=t[a:b+1],rtol=1e-10,atol=1e-12)
        states[a:b+1]=result.y.T
    vo=(states[:,1]+rc*states[:,0])/(1+rc/R)
    assert states[:,0].min()>0
    return np.column_stack((t,states[:,0],vo,gate))


def test_required_values_and_disabled_model():
    for kw in ({}, {"inductor_resistance_ohm":0}, {"capacitor_esr_ohm":0},
               {"inductor_resistance_ohm":-1,"capacitor_esr_ohm":0},
               {"inductor_resistance_ohm":float("nan"),"capacitor_esr_ohm":0}):
        with pytest.raises(ValidationError):
            FitOptions(include_parasitics=True,**kw)
    assert resistances(options(include_parasitics=False))==(0,0)
    assert resistances(options(inductor_resistance_ohm=0,capacitor_esr_ohm=0))==(0,0)


@pytest.mark.parametrize("mode",["LC","LCR"])
@pytest.mark.parametrize("fitter",[integral_fit,weak_integral_seed])
def test_estimation_with_known_resistances(circuit_data,mode,fitter):
    params,diag=fitter(circuit_data,options(mode=mode))
    np.testing.assert_allclose(params,[60e-6,500e-6,6],rtol=.008)
    assert not diag["parameter_at_bound"]
    predicted=rollout(circuit_data,params,28,series_resistances=(.25,.12))
    np.testing.assert_allclose(predicted,circuit_data[:,1:3],rtol=.012,atol=.015)


def test_nonideal_rollout_matches_independent_circuit(circuit_data):
    predicted=rollout(circuit_data,[60e-6,500e-6,6],28,series_resistances=(.25,.12))
    np.testing.assert_allclose(predicted,circuit_data[:,1:3],rtol=2e-6,atol=2e-6)


def test_zero_resistance_preserves_ideal_behavior(circuit_data):
    for fitter in (integral_fit,weak_integral_seed):
        ideal,_=fitter(circuit_data,FitOptions(mode="LC"))
        zero,_=fitter(circuit_data,options(inductor_resistance_ohm=0,capacitor_esr_ohm=0))
        np.testing.assert_array_equal(ideal,zero)
    p=[60e-6,500e-6,6]
    np.testing.assert_array_equal(rollout(circuit_data,p,28),rollout(circuit_data,p,28,series_resistances=(0,0)))


def test_dcm_discharge_uses_r_plus_esr():
    t=np.linspace(0,.001,201)
    a=np.column_stack((t,np.zeros_like(t),np.full_like(t,8),np.zeros_like(t)))
    out=rollout(a,[60e-6,500e-6,6],28,series_resistances=(.25,.12))
    np.testing.assert_allclose(out[:,1],8*np.exp(-t/(6.12*500e-6)),rtol=1e-9)
    assert np.all(out[:,0]==0)


def test_weighted_integral_with_sense_branch(circuit_data):
    # A known parallel sensing conductance supplies an exactly consistent fifth column.
    # Total load in the generated data is 6 ohm: choose Rload=8, sense=vo/24.
    train=np.column_stack((circuit_data,circuit_data[:,2]/24))
    params,_=weak_integral_seed(train,options(resistance_ohm=8))
    np.testing.assert_allclose(params,[60e-6,500e-6,8],rtol=.008)


def test_pann_step_and_gradients():
    torch=pytest.importorskip("torch")
    from backend.vendor.pann_net import EulerCell_Buck
    from backend.pann_parasitics import SeriesResistanceCell
    base=EulerCell_Buck(28,1e-8,60e-6,500e-6,6).double()
    cell=SeriesResistanceCell(base,.25,.12)
    states=torch.tensor([[2.,8.],[0.,8.]],dtype=torch.float64)
    inputs=torch.tensor([[1.,.002,.003],[0.,.002,.003]],dtype=torch.float64)
    actual=cell(inputs,states)
    i,vo=states[:,0],states[:,1]
    vc=(1+.12/base.Ro)*vo-.12*(i-inputs[:,1])
    ic=i-vo/base.Ro-inputs[:,1]
    expected_i=torch.stack((i[0]+base.dt/base.L*(28-vo[0]-.25*i[0]),i[1])).flatten()
    expected_vo=(vc+base.dt/base.Co*ic+.12*(expected_i-inputs[:,2]))/(1+.12/base.Ro)
    torch.testing.assert_close(actual,torch.stack((expected_i,expected_vo),dim=1))
    actual.square().sum().backward()
    for attr in ("L","Co","Ro"):
        assert torch.isfinite(getattr(base,attr).grad).all()


def test_closed_loop_identification_with_parasitics():
    import pandas as pd
    from backend.controller import profile_for, closed_loop_rollout
    from backend.data import read_csv
    from backend.identification import identify
    from backend.schemas import Mapping
    opt=options(pwm_source="controller",controller_confirmed=True,start_s=0,end_s=.0006,pwm_denoise="none",
                inductor_resistance_ohm=.05,capacitor_esr_ohm=.005)
    t=np.arange(30001)*20e-9
    y=closed_loop_rollout(t,[60e-6,500e-6,6],28,[0,12],profile_for(opt))
    data=read_csv(pd.DataFrame(dict(t=t,i=y[:,0],v=y[:,1])).to_csv(index=False).encode(),"nonideal.csv")
    result=identify(data,Mapping(time="t",current="i",voltage="v"),opt)
    np.testing.assert_allclose([result["parameters"]["L_H"],result["parameters"]["C_F"]],[60e-6,500e-6],rtol=.02)
    assert result["metrics"]["current_nrmse_pct"]<2
    assert result["metrics"]["voltage_nrmse_pct"]<2
    assert result["diagnostics"]["power_stage_model"]=="known_series_resistances"
    assert not any("忽略 ESR" in note for note in result["warnings"])
