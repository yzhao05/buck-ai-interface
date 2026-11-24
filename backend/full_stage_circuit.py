"""Fixed-clock nonideal Buck power and compensation circuit.

The descriptor state is iL, vSW-vo, iESL, vC, vC2, vC3, vo, 1.
The last coordinate carries the DC sources through the affine maps.
"""
import numpy as np
from .controller import PROFILE, quantities

STEP = 2e-9
BLOCK = 128
CHANNEL, FREEWHEEL, OPEN, BODY = range(4)


def feedback(profile):
    """Linear RC state derivatives, sense current and amplifier voltage."""
    origin = np.zeros(8)
    def signals(z):
        dq, _, sense = quantities(z[4:6], z[6], profile)
        # Recover the amplifier output from the series R3-C3 branch.
        vm = z[6] - profile["R1_ohm"]*sense - z[4]
        amp = vm + z[5] + dq[1]*profile["R3_ohm"]*profile["C3_F"]
        return np.array([*dq, sense, amp])
    bias = signals(origin)
    coeff = np.column_stack([signals(e)-bias for e in np.eye(8)])
    coeff[:, -1] = bias
    return coeff


def transitions(parts, vin, load, profile):
    L, rl, cw, c, rc, esl, ron = parts
    if L == rl == 0:
        cw = 0.  # The winding short bypasses its parallel capacitor.
        if c > 0 and rc == esl == ron == 0:
            raise ValueError("主电感支路短接且开关、输出电容均理想时，电源直接接到已充电电容，切换瞬间无有限电流解。")
    if c == 0:
        rc = esl = 0.  # No current flows in an open capacitor branch.
    dq2, dq3, sense, amp = feedback(profile)
    current = sense.copy()
    current[2] += 1
    current[6] += 1/load
    mass = np.diag([L, cw, esl, c, 1, 1, 0])
    # M dx/dt = F x + b, including Kirchhoff constraints for omitted components.
    eq = np.zeros((7, 8))
    eq[0, :2] = [-rl, 1]
    eq[1] = current
    eq[1, 0] -= 1
    eq[2, [2, 3, 6]] = [-rc, -1, 1]
    eq[3, 2] = 1
    eq[4:6] = [dq2, dq3]
    maps, projections = [], []
    # Three-stage Radau IIA evaluated as its [2/3] Pade function on
    # (M-hF)^-1 M. This form also permits algebraic rows (singular M).
    eye = np.eye(8)
    M = np.eye(8)
    M[:7,:7] = mass
    for mode in (CHANNEL, FREEWHEEL, OPEN, BODY):
        eq[6] = 0
        if mode == OPEN:
            eq[6] = current
        else:
            eq[6,[1,6]] = 1
            if mode == CHANNEL:
                eq[6] += ron*current
            if mode in (CHANNEL,BODY):
                eq[6,-1] -= vin
        F = np.zeros((8,8))
        F[:7] = eq
        z = np.linalg.solve(M-STEP*F, M)
        # Set Z=(I-hA)^-1 and multiply numerator/denominator by Z^3.
        d = z-eye
        z2, d2 = z@z, d@d
        z3 = z2@z
        numer = z3 + .4*z2@d + .05*z@d2
        denom = z3 - .6*z2@d + .15*z@d2 - d2@d/60
        advance = np.linalg.solve(denom,numer)
        advance[-1] = eye[-1]
        powers = np.empty((BLOCK,8,8))
        powers[0] = advance
        for k in (1,2,4,8,16,32,64):
            powers[k:2*k] = powers[:k] @ powers[k-1]
        maps.append(powers)
        # Algebraic right limit at a switching edge. A floating ideal
        # winding can impose a derivative constraint; take the map limit.
        algebraic = np.diag(M)==0
        lhs, rhs = eye.copy(), eye.copy()
        lhs[algebraic] = F[algebraic]
        lhs[algebraic,-1] = 0
        rhs[algebraic] = 0
        rhs[algebraic,-1] = -F[algebraic,-1]
        try:
            projections.append(np.linalg.solve(lhs,rhs))
        except np.linalg.LinAlgError:
            projections.append(np.linalg.solve(M-STEP*1e-5*F,M))
    return maps, projections, current, amp


def simulate(t, gate, vin, load, parts, frequency, profile=PROFILE, gate_times=None):
    """Start at model power-up; sample the completed internal ticks at CSV times."""
    last = int(np.floor(t[-1]/STEP+1e-7))
    if t[0] < -STEP/2 or t[-1]*frequency > 3000+1e-8:
        raise ValueError("七参数模型从上电时刻积分，请核对时间原点或缩短记录。")
    samples = np.maximum(0,np.floor(t/STEP+1e-7).astype(int))
    maps, proj, current, amp = transitions(parts, vin, load, profile)
    # Fixed model initial conditions, including polarity at both capacitors.
    state = np.array([0.,12.,0.,12.,*profile["initial_C2_C3_V"],0.,1.])
    if gate is not None:
        gt = t if gate_times is None else gate_times
        if abs(gt[0]) > STEP/2:
            raise ValueError("实测 PWM 回放需要从上电时刻开始的 PWM 记录。")
        edges = np.flatnonzero(np.diff(gate)!=0)+1
        edge_ticks = np.ceil(gt[edges]/STEP-1e-7).astype(int)
        levels = np.r_[gate[0],gate[edges]].astype(bool)
    phase = profile["carrier_phase_at_t0"]
    gain = profile["pwm_gain"]
    low, high = profile["duty_min"], profile["duty_max"]
    def drive(ticks, states):
        if gate is not None:
            return levels[np.searchsorted(edge_ticks,ticks,side="right")]
        duty = np.clip(gain*(states@amp), low, high)
        carrier = np.remainder(ticks*(STEP*frequency)+phase+1e-14,1.)
        return carrier < duty
    def allowed(mode, states, on):
        sw = states[...,1]+states[...,6]
        j = states@current
        if mode == OPEN or mode == CHANNEL:
            return (sw >= -1e-8) & (sw <= vin+1e-8)
        if mode == BODY:
            return j <= 1e-8
        return j >= (vin/max(parts[6],1e-30))*on-1e-8

    def switch(state,on):
        for candidate in ((CHANNEL,FREEWHEEL,BODY) if on else (FREEWHEEL,OPEN,BODY)):
            z = proj[candidate]@state
            if allowed(candidate,z,on):
                return candidate,z
        # A crossing inside a tick may require clamping at its end.
        for candidate in ((CHANNEL,FREEWHEEL,BODY) if on else (OPEN,FREEWHEEL,BODY)):
            z = maps[candidate][0]@state
            if allowed(candidate,z,on):
                return candidate,proj[candidate]@state
        raise ValueError("七参数模型的二极管约束无解，请核对器件值和单位。")

    on = bool(drive(0,state))
    mode,state = switch(state,on)
    output = np.empty((len(t),2))
    end = np.searchsorted(samples,0,side="right")
    output[:end] = state[[0,6]]
    tick = 0
    offsets = np.arange(1,BLOCK+1)
    lookahead = BLOCK
    while tick < last:
        size = min(lookahead,last-tick)
        path = maps[mode][:size]@state
        desired = drive(tick+offsets[:size],path)
        changes = np.flatnonzero((desired!=on) | ~allowed(mode,path,on))
        n = int(changes[0])+1 if len(changes) else size
        previous = state if n == 1 else path[n-2]
        state = path[n-1]
        if len(changes):
            if not allowed(mode,state,on):
                target,_ = switch(state,on)
                if mode in (OPEN,CHANNEL):
                    edge = vin if target == BODY else 0.
                    g0, g1 = previous[1]+previous[6]-edge, state[1]+state[6]-edge
                else:
                    edge = vin/max(parts[6],1e-30) if on and mode == FREEWHEEL else 0.
                    g0, g1 = previous@current-edge, state@current-edge
                alpha = np.clip(g0/(g0-g1),0.,1.) if g0 != g1 else 0.
                crossing = proj[target] @ (previous + alpha*(state-previous))
                state = crossing + (1-alpha)*(maps[target][0]@crossing-crossing)
                mode = target
            new_on = bool(desired[n-1])
            if new_on != on or not allowed(mode,state,on):
                on = new_on
                mode,state = switch(state,on)
            path[n-1] = state
        next_end = np.searchsorted(samples,tick+n,side="right")
        indices = samples[end:next_end]-tick-1
        output[end:next_end] = path[indices][:,[0,6]]
        end = next_end
        tick += n
        lookahead = min(BLOCK, 2*n)
    if not np.isfinite(output).all():
        raise ValueError("七参数模型的端电压或电流超出数值范围。")
    return output
