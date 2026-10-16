"""Known Case09/10 controller; adapted from the user's local PANN experiments.

Training reconstruction consumes training voltage only. Validation is an
autonomous four-state simulation, with no observed gates/voltages after x0.
This is an explicit circuit profile, not generic inference of unknown control.
"""
import numpy as np
from scipy.linalg import expm

PROFILE = {
    "id": "plecs_case09_rc_v1", "R1_ohm": 11000., "R2_ohm": 85000.,
    "R3_ohm": 120000., "R4_ohm": 47000., "C2_F": 1.1e-9,
    "C3_F": 2.7e-9, "opamp_gain": 1e6, "reference_V": 5.,
    "pwm_gain": .25, "duty_min": .1, "duty_max": .9,
    "frequency_hz": 100000., "carrier_phase_at_t0": 0.,
    "initial_C2_C3_V": [0., 0.], "dt_s": 20e-9,
}


def quantities(q, vo):
    g1, g3, g4 = 1/11000., 1/120000., 1/47000.
    gain, reference = 1e6, 5.
    denominator = g1 + g4 + (gain+1)*g3
    vm = (g1*(vo-q[0]) + g3*(gain*reference-q[1]))/denominator
    amplifier = gain/denominator*(reference*(g1+g4+g3)-g1*(vo-q[0])+g3*q[1])
    sense = (vo-vm-q[0])/11000.
    dq = np.array([(sense-q[0]/85000.)/1.1e-9,
                   (amplifier-vm-q[1])/(120000.*2.7e-9)])
    return dq, min(.9, max(.1, .25*amplifier)), sense


def validate_profile(a, options):
    if options.mode != "LC":
        raise ValueError("控制器重建仅支持固定已知负载 R 的 L/C 辨识。")
    if not options.controller_confirmed:
        raise ValueError("请先确认控制电路与 Case09/10 配置一致；不能将此重建方式用于任意三列 CSV。")
    if options.method == "integral":
        raise ValueError("控制器重建请选择 PANN 或抗噪加权积分。")
    if not np.isclose(options.frequency_hz, 100000., rtol=0, atol=1e-6):
        raise ValueError("当前控制器配置要求开关频率为 100 kHz。")
    if abs(a[0, 0]) > 1e-12 or (options.start_s is not None and abs(options.start_s) > 1e-12):
        raise ValueError("控制器重建需要从仿真 t=0 开始的 CSV，辨识开始时间设为 0。")
    end = a[-1, 0] if options.end_s is None else options.end_s
    if end >= .001-1e-12:
        raise ValueError("当前控制器重建区间须结束于 1 ms 之前，并保持负载恒定；建议结束时间 0.8 ms。")


def reconstruct_training(train):
    """Known controller driven by uniformly interpolated TRAINING data only."""
    dt = PROFILE["dt_s"]
    grid = np.arange(int(np.floor(train[-1, 0]/dt+1e-9))+1)*dt
    current = np.interp(grid, train[:, 0], train[:, 1])
    voltage = np.interp(grid, train[:, 0], train[:, 2])
    origin = quantities(np.zeros(2), 0.)[0]
    mat = np.zeros((5, 5))
    for j in range(2):
        basis = np.zeros(2); basis[j] = 1.
        mat[:2, j] = quantities(basis, 0.)[0]-origin
    mat[:2, 2] = quantities(np.zeros(2), 1.)[0]-origin
    mat[:2, 4] = origin; mat[2, 3] = 1.
    transition = expm(mat*dt)
    q = np.zeros(2)
    drive = np.empty((len(grid), 2))
    for k, vo in enumerate(voltage):
        _, duty, sense = quantities(q, vo)
        drive[k] = [float((k % 500)/500 < duty), sense]
        if k+1 < len(grid):
            q = (transition @ np.array([*q, vo, (voltage[k+1]-vo)/dt, 1.]))[:2]
    if not np.isfinite(drive).all():
        raise ValueError("控制器重建出现非有限值，请核对波形和电路配置。")
    return np.column_stack((grid, current, voltage, drive))


def closed_loop_rollout(times, parameters, vin, initial_state):
    """Explicit Euler at 20 ns, matching the old autonomous circuit simulator."""
    L, C, R = parameters
    dt = PROFILE["dt_s"]
    grid = np.arange(int(np.ceil(times[-1]/dt))+1)*dt
    predicted = np.empty((len(grid), 2))
    predicted[0] = np.maximum(initial_state, 0)
    q = np.zeros(2)
    for k in range(len(grid)-1):
        current, vo = predicted[k]
        dq, duty, sense = quantities(q, vo)
        gate = (k % 500)/500 < duty
        va = vin if gate else vo if current <= 0 else 0.
        predicted[k+1] = [max(0., current+dt/L*(va-vo)), vo+dt/C*(current-vo/R-sense)]
        q += dt*dq
        if not np.isfinite(predicted[k+1]).all() or np.max(np.abs(predicted[k+1])) > 1e7 or not np.isfinite(q).all():
            raise ValueError("闭环验证发散，请核对控制电路、负载和辨识参数。")
    return np.column_stack([np.interp(times, grid, predicted[:, j]) for j in (0, 1)])
