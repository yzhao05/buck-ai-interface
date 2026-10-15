"""Retrieval regressions and independent electrical checks of the documented equations."""
import asyncio
import json
from pathlib import Path

import numpy as np
import pytest

from backend import chat
from backend.knowledge import retrieve
from backend.knowledge_base.buck import FORMULA_DOCUMENTS
from backend.schemas import ChatRequest
from build_formula_reference import render

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = json.loads((ROOT / "backend/knowledge_base/model_evidence.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("query,expected", [
    ("控制电路 R1 R2 R3 R4 C2 C3 节点方程", "buck-controller-kcl"),
    ("模拟补偿器传递函数零极点", "buck-controller-transfer"),
    ("Buck CCM DCM 临界条件", "buck-dcm"),
    ("LCR 积分辨识公式", "buck-identification-formulas"),
    ("buck small signal transfer function", "buck-transfer"),
    ("PWM 占空比限幅", "buck-pwm"),
    ("RMS ripple sizing", "buck-ripple-sizing"),
    ("闭环目标为什么不是12V", "buck-control-dc"),
    ("负载跳变并联电阻", "buck-load-step"),
    ("电容还有多少寿命", "capacitor-life"),
])
def test_topic_retrieval(query, expected):
    assert expected in [d["id"] for d in retrieve(query)[:3]]


def test_formula_reference_and_provenance():
    assert len({d["id"] for d in FORMULA_DOCUMENTS}) == len(FORMULA_DOCUMENTS) == 24
    assert all(d["scope"] and d["equations"] and d["references"] for d in FORMULA_DOCUMENTS)
    assert (ROOT / "backend/knowledge_base/FORMULAS.md").read_text(encoding="utf-8") == render()
    assert set(EVIDENCE["models"]) == {"original", "case16a", "case16b", "case17"}


@pytest.mark.parametrize("message", ["解释 LCR 辨识公式", "derive the parameter identification equations",
                                     "参数辨识公式是什么？", "辨识公式的推导", "Identify parameters: explain the equations only"])
def test_formula_after_upload_does_not_start_fit(monkeypatch, message):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    calls = []

    async def invoke(name):
        calls.append(name)
        assert name != "start_identification"
        return {"rows": 1000}  # Intentionally insufficient for generic waveform reply.

    request = ChatRequest(message=message, dataset_id="uploaded", identification_enabled=True,
                          mapping={"time": "t", "current": "i", "voltage": "v"})
    response = asyncio.run(chat.answer(request, invoke))
    assert calls == []  # Formula route does not depend on the uploaded dataset.
    assert response["job_id"] is None
    assert "buck-identification-formulas" in response["answer"]


@pytest.mark.parametrize("message", ["请先辨识参数，再解释公式", "解释公式，并开始参数辨识",
                                     "Identify parameters and explain the equations"])
def test_local_mixed_request_starts_fit_and_explains(monkeypatch, message):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    calls = []

    async def invoke(name):
        calls.append(name)
        assert name == "start_identification"
        return {"job_id": "test-mixed-request", "status": "queued"}

    response = asyncio.run(chat.answer(ChatRequest(message=message, dataset_id="uploaded",
                                                   identification_enabled=True), invoke))
    assert calls == ["start_identification"]
    assert response["job_id"] == "test-mixed-request"
    assert "公式" in response["answer"]
    assert "未启动" not in response["answer"]


def controller_values():
    components = EVIDENCE["models"]["original"]["components"]
    return [float(components[n]["parameters"][p]) for n, p in
            [("R1", "R"), ("R2", "R"), ("R3", "R"), ("R4", "R"),
             ("C2", "C"), ("C3", "C"), ("Op-Amp", "A")]]


def test_compensator_factorization_and_documented_frequencies():
    r1, r2, r3, r4, c2, c3, gain = controller_values()
    tz1, tz2, tp = r3*c3, r2*c2, r1*r2/(r1+r2)*c2
    k = 1/(c3*(r1+r2))
    np.testing.assert_allclose(1/(2*np.pi*np.array([tz1, tz2, tp])),
                               [491.219, 1702.192, 14855.493], rtol=1e-6)
    assert 5*(r1+r2+r4)/r4 == pytest.approx(15.212766, abs=1e-6)
    for s in 2j*np.pi*np.geomspace(1, 1e5, 30):
        impedance_ratio = (r3+1/(s*c3))/(r1+r2/(1+s*r2*c2))
        factored = k*(1+s*tz1)*(1+s*tz2)/(s*(1+s*tp))
        pid = k/s + k*(tz1+tz2-tp) + k*(tz1*tz2-tp*(tz1+tz2-tp))*s/(1+s*tp)
        np.testing.assert_allclose([factored, pid], impedance_ratio, rtol=1e-12)


def test_full_closed_loop_formula_against_six_variable_kcl():
    """Solve raw resistor/capacitor KCL, independent of Zi/Zf reduction.

    Unknowns are iL, vo, va, vb, vm, vop. Keep vm+vop/A=vref
    explicitly to avoid ill-conditioning from eliminating a million-fold gain.
    Three excitations check reference, supply and load-disturbance signs.
    """
    r1, r2, r3, r4, c2, c3, a = controller_values()
    l, c, r, vin, km, duty = 60e-6, 500e-6, 6, 28, .25, .54
    g1, g3, g4 = 1/r1, 1/r3, 1/r4
    for s in 2j*np.pi*np.geomspace(.1, 1e4, 40):
        y2, y3 = 1/r2+s*c2, s*c3
        matrix = np.array([
            [s*l, 1, 0, 0, 0, -vin*km],
            [-1, s*c+1/r+g1, -g1, 0, 0, 0],
            [0, -g1, g1+y2, 0, -y2, 0],
            [0, 0, 0, g3+y3, -y3, -g3],
            [0, 0, -y2, -y3, y2+y3+g4, 0],
            [0, 0, 0, 0, 1, 1/a],
        ], dtype=complex)
        rhs = np.array([[0, duty, 0], [0, 0, -1], [0, 0, 0],
                        [0, 0, 0], [0, 0, 0], [1, 0, 0]], dtype=complex)
        nodal_vo = np.linalg.solve(matrix, rhs)[1]
        yi, yf = 1/(r1+r2/(1+s*r2*c2)), 1/(r3+1/(s*c3))
        b = yi+g4+(a+1)*yf
        gc, gr = a*yi/b, a*(yi+g4+yf)/b
        yload, yref = yi*(1-yi/b), -yi*a*yf/b
        den = l*c*s*s+l/r*s+1
        gvd, gvg, zout = vin/den, duty/den, l*s/den
        dcl = 1+km*gvd*gc+zout*yload
        expected = np.array([(km*gvd*gr-zout*yref)/dcl, gvg/dcl, -zout/dcl])
        np.testing.assert_allclose(nodal_vo, expected, rtol=2e-9, atol=1e-10)


def test_dcm_conversion_satisfies_cycle_current_balance():
    vin, r, fs, duty = 28, 12, 1e5, .3
    for ratio in [.1, .4, .8, 1]:
        lcrit = r*(1-duty)/(2*fs)
        l = ratio*lcrit
        k = 2*l*fs/r
        m = 2/(1+np.sqrt(1+4*k/duty**2))
        delta = duty*(1-m)/m
        peak = (vin-vin*m)*duty/(l*fs)
        assert peak*(duty+delta)/2 == pytest.approx(vin*m/r)
        assert duty+delta <= 1+1e-12
        if ratio == 1:
            assert m == pytest.approx(duty)


def test_esr_transfer_against_capacitor_and_inductor_kcl():
    l, c, r, rc, vin = 60e-6, 500e-6, 6, .05, 28
    for s in 2j*np.pi*np.geomspace(1, 1e5, 25):
        # Unknowns iL, vo, vC. Unit duty excitation.
        matrix = np.array([[s*l, 1, 0], [-1, 1/r, s*c], [0, 1, -(1+rc*c*s)]], dtype=complex)
        vo = np.linalg.solve(matrix, [vin, 0, 0])[1]
        transfer = vin*(1+s*rc*c)/(1+s*(l/r+rc*c)+s*s*l*c*(1+rc/r))
        assert vo == pytest.approx(transfer, rel=1e-12)
