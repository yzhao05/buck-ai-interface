"""Known series resistances; CSV voltage is terminal vo, never internal vC."""
import numpy as np
from scipy.optimize import least_squares, lsq_linear


def resistances(options):
    if not getattr(options, "include_parasitics", False):
        return 0., 0.
    rl = getattr(options, "inductor_resistance_ohm", None)
    rc = getattr(options, "capacitor_esr_ohm", None)
    if rl is None or rc is None or not np.isfinite([rl, rc]).all() or min(rl, rc) < 0:
        raise ValueError("考虑寄生电阻时，必须填写有效的电感绕组电阻和电容 ESR。")
    return float(rl), float(rc)


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
