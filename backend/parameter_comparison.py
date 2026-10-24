"""Attach user-provided references to a validated result for this request only."""
import math

from .convergence import assess_result


def with_ground_truth(result, truth):
    if truth is None:
        return result
    references = {"L_H": truth.L_H, "C_F": truth.C_F}
    errors = {key: abs(result["parameters"][unit] / references[unit] - 1) * 100
              for key, unit in (("L", "L_H"), ("C", "C_F"))}
    if not all(math.isfinite(value) for value in errors.values()):
        raise ValueError("参数相对误差超出可计算范围，请检查真实值及单位。")
    comparison = {"ground_truth": references, "relative_error_pct": errors, "source": "user_provided"}
    assessment = assess_result(result["metrics"], result.get("diagnostics", {}), result.get("method"))
    assessment["parameter_accuracy"] = {"status": "compared", **comparison}
    return {**result, "parameter_comparison": comparison, "parameter_error": comparison,
            "parameter_error_note": "已按用户提供的 L、C 真值计算相对误差；真值未独立核验，未参与训练。波形误差与参数误差分开评价。",
            "assessment": assessment}
