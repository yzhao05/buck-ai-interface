"""Switched Buck waveform fit. The CSV current is the winding current, vo is terminal voltage."""
import numpy as np
from scipy.linalg import solve_banded
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


def _advance(h, on, start, vin, load, parts):
    """Backward Euler MNA on the original time grid, PWM held over [t[k], t[k+1]).
    States in each block: winding current, winding voltage, capacitor branch current,
    internal capacitor voltage, output voltage. Zero storage uses its algebraic limit.
    """
    inductance, rl, cw, c, rc, esl, ron = parts
    mass = np.array([inductance, cw, esl, c, 0.])
    # The four element laws and the fifth (switch-node) constraint form a
    # block-bidiagonal system. LAPACK solves all variable-step samples in O(N).
    law = np.array([[rl,-1,0,0,0], [1,0,-1,0,-1/load],
                    [0,0,rc,1,-1], [0,0,-1,0,0], [0,1,0,0,1]], dtype=float)
    idx = np.arange(len(h))*5
    band = np.zeros((10, 5*len(h)))
    for i,j in zip(*np.nonzero(law)):
        band[4+i-j,idx+j] = law[i,j]
    for i in range(4):
        band[4,idx+i] += mass[i]/h
        band[9,idx[:-1]+i] = -mass[i]/h[1:]
    rhs = np.zeros(5*len(h))
    rhs[:4] = mass[:4]*start[:4]/h[0]
    # The high-side channel is a resistor when on. The ideal diodes clamp SW
    # to 0 or Vin; while both block and PWM is off, total terminal current is zero.
    channel, low, floating, high = 0, 1, 2, 3
    mode = np.where(on,channel,low)
    tol = 1e-9
    for _ in range(12):
        active = mode==channel
        open_node = mode==floating
        band[7,idx+1] = ~open_node
        band[6,idx+2] = np.where(open_node,1.,ron*active)
        band[4,idx+4] = np.where(open_node,1/load,1+ron*active/load)
        rhs[idx+4] = vin*(active | (mode==high))
        try:
            states = solve_banded((5,4),band,rhs,check_finite=False).reshape(-1,5)
        except np.linalg.LinAlgError:
            break
        iw, vw, ic, vc, vo = states.T
        total = ic+vo/load
        sw = vo+vw
        next_mode = mode.copy()
        # At a clamp the switch channel can also conduct; test diode current.
        release_low = (mode==low) & (total < (vin/max(ron,1e-30))*on-tol)
        next_mode[release_low] = np.where(on[release_low],channel,floating)
        release_high = (mode==high) & (total>tol)
        next_mode[release_high] = np.where(on[release_high],channel,floating)
        next_mode[(open_node | active) & (sw < -tol)] = low
        next_mode[(open_node | active) & (sw > vin+tol)] = high
        if np.array_equal(mode,next_mode):
            return states
        mode = next_mode
    # In DCM a long blocking interval may advance one sample per active-set pass.
    # Solve it causally instead of imposing a limit on the length of that interval.
    output = np.empty((len(h),5))
    state = start
    for k,dt in enumerate(h):
        a = law + np.diag(mass/dt)
        b = mass/dt*state
        for candidate in ((channel,low,high) if on[k] else (low,floating,high)):
            rr = ron if candidate==channel else 0.
            a[4] = [0,1,rr,0,1+rr/load] if candidate!=floating else [0,0,1,0,1/load]
            b[4] = vin if candidate in (channel,high) else 0.
            try:
                z = np.linalg.solve(a,b)
            except np.linalg.LinAlgError:
                continue
            sw,j = z[1]+z[4],z[2]+z[4]/load
            if candidate in (channel,floating) and not -tol<=sw<=vin+tol:
                continue
            if ((candidate==low and j < vin*on[k]/max(ron,1e-30)-tol)
                    or (candidate==high and j>tol)):
                continue
            state=z
            output[k] = z
            break
        else:
            raise ValueError("开关节点约束无解，请核对接线、单位和输入参数。")
    return output


def simulate(t, gate, initial, vin, load, parts):
    """Carry all internal states between chunks; keep post-event PWM samples."""
    _, _, _, c, rc, _, ron = parts
    iw, vo = initial
    ic = iw-vo/load if c else 0.
    state = np.array([iw, vin*gate[0]-ron*gate[0]*iw-vo, ic, vo-rc*ic, vo])
    out = np.empty((len(t),2))
    out[0] = initial
    for left in range(0,len(t)-1,4096):
        right = min(left+4096,len(t)-1)
        dt = np.diff(t[left:right+1])
        q = gate[left:right+1]>.5
        edge = q[:-1]!=q[1:]
        # Two implicit substeps per interval plus a short right-limit step for
        # an edge. Output voltage may jump even with continuous stored energy.
        ends = np.cumsum(2+edge.astype(int))-1
        starts = np.r_[0,ends[:-1]+1]
        h = np.empty(ends[-1]+1)
        on = np.empty(len(h),bool)
        h[starts] = h[starts+1] = dt/2
        on[starts] = on[starts+1] = q[:-1]
        event_dt = dt[edge]*1e-6
        h[ends[edge]-1] -= event_dt
        h[ends[edge]] = event_dt
        on[ends[edge]] = q[1:][edge]
        states = _advance(h,on,state,vin,load,parts)
        out[left+1:right+1] = states[ends][:,[0,4]]
        state = states[-1]
    if not np.isfinite(out).all():
        raise ValueError("七参数模型的端电压或电流超出数值范围。")
    return out


def unobservable_keys(settings):
    """Unknown components in open or shorted branches, in result order."""
    inactive = set()
    if settings.get("capacitance_f") == 0:
        inactive.update(("rC_ohm", "ESL_H"))
    if settings.get("inductance_h") == 0 and settings.get("inductor_resistance_ohm") == 0:
        inactive.add("Cw_F")
    return [key for key, field, _, _ in PARTS if key in inactive and settings.get(field) is None]


def fit(train, options):
    given = [getattr(options, field) for _,field,_,_ in PARTS]
    unavailable = unobservable_keys(options.model_dump())
    disconnected = {i for i, part in enumerate(PARTS) if part[0] in unavailable}
    free = np.array([i for i,v in enumerate(given) if v is None and i not in disconnected], dtype=int)
    parts = np.array([default if v is None else v for v,(_,_,default,_) in zip(given,PARTS)])
    status = {key: ("estimate" if v is None else "ignored" if v==0 else "fixed")
              for v,(key,*_) in zip(given,PARTS)}
    diagnostics = {"power_stage_model":"full_nonideal_buck", "parameter_status":status,
                   "method":"Switched-circuit waveform least squares", "drive_source":"measured PWM",
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
        predicted = simulate(train[:,0],train[:,3],data[0],options.vin,options.resistance_ohm,p)
        return (((predicted-data)/scale)*weight[:,None]).ravel()
    result = least_squares(residual,np.log(parts[free]),bounds=(np.log([1e-16]*len(free)),
                           np.log([PARTS[i][3] for i in free])),diff_step=1e-5,max_nfev=120)
    if not result.success:
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

