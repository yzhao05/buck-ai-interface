"""Switched Buck waveform fit. The CSV current is the winding current, vo is terminal voltage."""
import numpy as np
from .full_stage_circuit import STEP, simulate
from .controller import PROFILE, profile_for
from scipy.optimize import least_squares

# Result key, option (SI), numerical starting scale, upper search limit.
PARTS = (
    ("L_H", "inductance_h", 1e-4, 1.),
    ("rL_ohm", "inductor_resistance_ohm", .1, 1000.),
    ("Cw_F", "winding_capacitance_f", 1e-10, 1e-3),
    ("C_F", "capacitance_f", 1e-4, 1.),
    ("rC_ohm", "capacitor_esr_ohm", .1, 1000.),
    ("ESL_H", "capacitor_esl_h", 1e-8, .1),
    ("Ron_ohm", "switch_resistance_ohm", .05, 1000.),
)


def predict(t, history, options, parts):
    times, gate = history if history is not None else (None, None)
    profile = PROFILE if options.controller is None else profile_for(options.model_copy(update={"include_parasitics": False}))
    return simulate(t, gate, options.vin, options.resistance_ohm, parts,
                    options.frequency_hz, profile=profile, gate_times=times)


def unobservable_keys(settings):
    """Unknown components in open or shorted branches, in result order."""
    inactive = set()
    if settings.get("capacitance_f") == 0:
        inactive.update(("rC_ohm", "ESL_H"))
    if settings.get("inductance_h") == 0 and settings.get("inductor_resistance_ohm") == 0:
        inactive.add("Cw_F")
    return [key for key, field, _, _ in PARTS if key in inactive and settings.get(field) is None]


def fit(train, options, history):
    given = [getattr(options, field) for _,field,_,_ in PARTS]
    unavailable = unobservable_keys(options.model_dump())
    disconnected = {i for i, part in enumerate(PARTS) if part[0] in unavailable}
    free = np.array([i for i,v in enumerate(given) if v is None and i not in disconnected], dtype=int)
    parts = np.array([default if v is None else v for v,(_,_,default,_) in zip(given,PARTS)])
    status = {key: ("estimate" if v is None else "ignored" if v==0 else "fixed")
              for v,(key,*_) in zip(given,PARTS)}
    diagnostics = {"power_stage_model":"full_nonideal_buck", "parameter_status":status,
                   "method":"Switched-circuit waveform least squares", "drive_source": "measured PWM" if history is not None else "PLECS RC closed loop",
                   "fixed_step_s": STEP, "sampling_branch_included": True,
                   "identifiability_condition_number":1., "parameter_at_bound":False}
    for i in disconnected:
        parts[i] = 0.  # Numerical placeholder used only by the simulator.
        status[PARTS[i][0]] = "unobservable"
    diagnostics["unobservable_parameters"] = unavailable
    diagnostics["weak_parameters"] = unavailable
    if not len(free):
        return parts,diagnostics
    data = train[:,1:3]
    scale = np.maximum(np.ptp(data,axis=0),[1e-6,1e-6])
    dt = np.diff(train[:,0])
    weight = np.sqrt(np.r_[dt[0],(dt[:-1]+dt[1:])/2,dt[-1]] / np.mean(dt))
    def residual(log_values):
        p = parts.copy()
        p[free] = np.exp(log_values)
        predicted = predict(train[:,0],history,options,p)
        return (((predicted-data)/scale)*weight[:,None]).ravel()
    bounds = (np.log([1e-16]*len(free)), np.log([PARTS[i][3] for i in free]))
    def optimize(seed):
        return least_squares(residual, np.log(seed[free]), bounds=bounds, diff_step=1e-5, max_nfev=120)

    varying_resonance = 2 in free or 5 in free
    try:
        result = optimize(parts)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        if not varying_resonance:
            raise
        result = None
    # If the initial resonance is outside the measurement bandwidth, try a
    # second basin whose capacitive/inductive reactance equals the load at
    # the sampling Nyquist frequency. Fixed and omitted values stay intact.
    if varying_resonance and (result is None or not result.success or np.sqrt(np.mean(result.fun**2)) > 1e-5):
        seed = parts.copy()
        omega = np.pi / np.median(dt)
        for i, value in ((2, 1/(omega*options.resistance_ohm)), (5, options.resistance_ohm/omega)):
            if i in free:
                seed[i] = np.clip(value, 1e-16, PARTS[i][3])
        try:
            alternate = optimize(seed)
        except (ValueError, np.linalg.LinAlgError, FloatingPointError):
            if result is None or not result.success:
                raise
        else:
            if alternate.success and (result is None or not result.success or alternate.cost < result.cost):
                result = alternate
    if result is None or not result.success:
        raise ValueError("七参数拟合未收敛，请检查单位、PWM 对齐和初始状态，或固定已知器件值。")
    parts[free] = np.exp(result.x)
    # Log sensitivities compare proportional changes across H, F and ohms.
    _,s,vh = np.linalg.svd(result.jac,full_matrices=False)
    tol = max(s[0]*1e-7,1e-8*np.sqrt(len(train)))
    weak = np.sum(vh[s<tol]**2,axis=0)>.05
    weak |= result.active_mask!=0
    # A small normalized sensitivity relative to the residual also indicates
    # poor practical resolution even if the Jacobian is mathematically full rank.
    weak |= np.linalg.norm(result.jac,axis=0) < np.linalg.norm(result.fun)*.05
    # Flag small parasitics whose resonance is poorly resolved in this trace.
    for i, partner in ((2,0),(5,3)):
        if i in free and parts[partner]>0 and np.median(dt)>np.pi*np.sqrt(parts[i]*parts[partner])/4:
            weak[free==i] = True
    diagnostics.update(weak_parameters=unavailable+[PARTS[i][0] for i in free[weak]],
                       identifiability_condition_number=float(min(s[0]/max(s[-1],1e-30),1e30)),
                       parameter_at_bound=bool(np.any(result.active_mask)),evaluations=result.nfev)
    return parts,diagnostics

