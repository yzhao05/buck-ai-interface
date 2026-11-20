"""Fixed or estimated series resistances; CSV voltage is terminal vo."""
import numpy as np
from scipy.optimize import least_squares, lsq_linear

JOINT_MODE = "LC_RL_RC"
PARAMETER_KEYS = ("L_H", "C_F", "R_ohm", "rL_ohm", "rC_ohm")


def estimated_resistances(options):
    return tuple(getattr(options, "mode", None) == JOINT_MODE and
                 (getattr(options, key, None) is None or (getattr(options, key) == 0 and not getattr(options, "joint_zero_is_fixed", False)))
                 for key in ("inductor_resistance_ohm", "capacitor_esr_ohm"))


def resistances(options):
    if getattr(options, "mode", None) == JOINT_MODE:
        # Unknown entries are zero ONLY while constructing training regressions.
        # Forward validation must use the fitted values from parameters[3:5].
        values = tuple(getattr(options, key, None) or 0. for key in
                       ("inductor_resistance_ohm", "capacitor_esr_ohm"))
        if not np.isfinite(values).all() or min(values) < 0:
            raise ValueError("寄生电阻必须为非负有限数值。")
        return values
    if not getattr(options, "include_parasitics", False):
        return 0., 0.
    rl = getattr(options, "inductor_resistance_ohm", None)
    rc = getattr(options, "capacitor_esr_ohm", None)
    if rl is None or rc is None or not np.isfinite([rl, rc]).all() or min(rl, rc) < 0:
        raise ValueError("考虑寄生电阻时，必须填写有效的电感绕组电阻和电容 ESR。")
    return float(rl), float(rc)


def regression_condition(design):
    norms = np.linalg.norm(design, axis=0)
    if np.any(norms < 1e-14):
        raise ValueError("联合辨识动态不足，无法分离元件与寄生电阻。")
    condition = float(np.linalg.cond(design / norms))
    if not np.isfinite(condition) or condition > 1e5:
        raise ValueError("联合辨识回归病态，请增加动态激励或固定部分电阻。")
    return condition


def fit_joint_parameters(ld, li, lu, dv, di, ds, v, j, options):
    """Training-only integral/weak regression with fixed load R.

    L D(i)+rL I(i)=I(q Vin-vo).
    C D(vo)-(C*rC) D(i-vo/R-isense)=I(i-vo/R-isense).
    Positive provided resistances stay fixed; zero/None are estimated separately.
    """
    rl, rc = resistances(options)
    learn_l, learn_c = estimated_resistances(options)
    lx = np.column_stack((ld*1e-4, li*.1)) if learn_l else ld[:, None]*1e-4
    lc = regression_condition(lx)
    lf = lsq_linear(lx, lu if learn_l else lu-rl*li,
                    bounds=([.001, 0], [1000, 10000]) if learn_l else ([.001], [1000]))
    if not lf.success:
        raise ValueError("联合辨识回归未收敛，请核对数据与激励。")
    inductance = lf.x[0]*1e-4
    if learn_l:
        rl = lf.x[1]*.1
    dic, target = di-dv/options.resistance_ohm-ds, j-v/options.resistance_ohm
    if learn_c:
        # Linear C/C*rC seed; then enforce C>0 and 0<=rC<=1000 together.
        design = np.column_stack((dv*1e-3, -dic*1e-4))
        regression_condition(design)
        seed = lsq_linear(design, target, bounds=([1e-5, 0], [1000, 1e7]))
        scale = max(np.linalg.norm(target), 1e-12)
        def residual(x):
            return (x[0]*1e-3*(dv-x[1]*.1*dic)-target)/scale
        def jacobian(x):
            return np.column_stack((1e-3*(dv-x[1]*.1*dic), -x[0]*1e-4*dic))/scale
        cf = least_squares(residual, [seed.x[0], np.clip(seed.x[1]/seed.x[0], 0, 10000)],
                           jac=jacobian, bounds=([1e-5, 0], [1000, 10000]),
                           x_scale="jac", ftol=1e-12, xtol=1e-12, gtol=1e-12)
        if not cf.success:
            raise ValueError("联合辨识回归未收敛，请核对数据与激励。")
        capacitance, rc = cf.x[0]*1e-3, cf.x[1]*.1
        cc = regression_condition(jacobian(cf.x))
    else:
        design = (dv-rc*dic)[:, None]*1e-3
        cc = regression_condition(design)
        cf = lsq_linear(design, target, bounds=([1e-5], [1000]))
        if not cf.success:
            raise ValueError("联合辨识回归未收敛，请核对数据与激励。")
        capacitance = cf.x[0]*1e-3
    return np.array([inductance, capacitance, options.resistance_ohm, rl, rc]), {
        "L_rL_condition_number": lc, "C_rC_condition_number": cc,
        "identifiability_condition_number": max(lc, cc), "CR_condition_number": cc,
        "parameter_at_bound": bool(np.any(lf.active_mask) or np.any(cf.active_mask)),
        "estimated_resistances": {"rL_ohm": learn_l, "rC_ohm": learn_c}}


def fit_capacitor(dv, di, ds, voltage_integral, current_integral, options):
    """Both endpoint and weak derivatives: C*(dv-rc*di+rc*ds+rc*G*dv)+G*V=J.

    G=1/Rload. The bilinear C*G term must not be dropped in LCR mode.
    """
    _, rc = resistances(options)
    z = dv - rc*di + rc*ds
    if options.mode == "LC":
        g = 1/options.resistance_ohm
        design = (z+rc*g*dv)[:, None]*1e-3
        if np.linalg.norm(design) < 1e-14:
            raise ValueError("电容内部电压动态不足，无法辨识 C。")
        fit = lsq_linear(design, current_integral-g*voltage_integral, bounds=([1e-5], [1000]))
        return fit.x[0]*1e-3, options.resistance_ohm, 1., bool(np.any(fit.active_mask))
    design = np.column_stack((z*1e-3, voltage_integral))
    seed = lsq_linear(design, current_integral, bounds=([1e-5, 1e-5], [1000, 100]))
    scale = max(np.linalg.norm(current_integral), 1e-12)
    def residual(x):
        c, g = x[0]*1e-3, x[1]
        return (c*(z+rc*g*dv)+g*voltage_integral-current_integral)/scale
    def jacobian(x):
        return np.column_stack((1e-3*(z+rc*x[1]*dv), x[0]*1e-3*rc*dv+voltage_integral))/scale
    fit = least_squares(residual, seed.x, jac=jacobian, bounds=([1e-5, 1e-5], [1000, 100]),
                        x_scale="jac", ftol=1e-12, xtol=1e-12, gtol=1e-12)
    jac = jacobian(fit.x)
    norms = np.linalg.norm(jac, axis=0)
    if (norms < 1e-14).any():
        raise ValueError("电压动态不足，C/R 无法分离。")
    condition = float(np.linalg.cond(jac/norms))
    if not fit.success or not np.isfinite(condition) or condition > 1e5:
        raise ValueError("含 ESR 的 C/R 回归病态，请增加动态激励。")
    return fit.x[0]*1e-3, 1/fit.x[1], condition, bool(np.any(fit.active_mask))
