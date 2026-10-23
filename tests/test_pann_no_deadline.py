"""Training completes epochs irrespective of elapsed wall-clock time."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from backend import pann_adapter
from backend.schemas import FitOptions

ROOT = Path(__file__).resolve().parents[1]


def test_interpreter_bridge_has_no_deadline(monkeypatch):
    monkeypatch.setenv("PANN_PYTHON", sys.executable)
    monkeypatch.setenv("APP_ACCESS_TOKEN", "private-test-token")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-test-key")
    monkeypatch.setattr(pann_adapter.importlib.util, "find_spec", lambda _: None)
    calls = []

    def worker(command, **kwargs):
        calls.append(command)
        assert kwargs.get("timeout") is None
        assert "APP_ACCESS_TOKEN" not in kwargs["env"]
        assert "DEEPSEEK_API_KEY" not in kwargs["env"]
        payload = json.loads(kwargs["input"])
        assert payload["options"]["epochs"] == 200
        return SimpleNamespace(returncode=0, stdout=json.dumps({"parameters": payload["initial"],
            "diagnostics": {"completed_epochs": 200, "stop_reason": "epoch_budget"}}))

    monkeypatch.setattr(pann_adapter.subprocess, "run", worker)
    params, diagnostics = pann_adapter.refine(np.ones((3, 4)), np.array([60e-6, 500e-6, 6]), FitOptions(epochs=200))
    assert calls == [[sys.executable, "-m", "backend.pann_worker"]]
    assert diagnostics["completed_epochs"] == 200
    assert params[2] == 6


def test_real_training_ignores_clock_jump():
    executable = sys.executable if importlib.util.find_spec("torch") else os.getenv("PANN_PYTHON")
    if not executable or not Path(executable).is_file():
        pytest.skip("PyTorch or PANN_PYTHON required for real training")
    # Simulate passing the old time budget without making the regression wait
    # two minutes. Only the adapter's clock is replaced, never torch's clocks.
    script = r'''
import itertools, json
from types import SimpleNamespace
import numpy as np
from backend import pann_adapter
clock = itertools.count(0, 1000)
pann_adapter.time = SimpleNamespace(monotonic=lambda: next(clock))
t = np.linspace(0, .00012, 1201)
train = np.column_stack((t, 2+.1*np.sin(t*10000), 12+.1*np.cos(t*10000), ((t*100000)%1 < .5).astype(float)))
for mode in ('joint', 'measured'):
    options = SimpleNamespace(vin=28, frequency_hz=100000, mode='LC', epochs=10, pann_initial_state=mode, pann_window_cycles=1)
    _, d = pann_adapter.refine(train, np.array([60e-6,500e-6,6]), options)
    assert d['completed_epochs'] == 10, d
    assert d['stop_reason'] == 'epoch_budget', d
    assert d['state_warmup_epochs'] == (50 if mode == 'joint' else 0), d
    assert d['history'][-1]['epoch'] == 10
print(json.dumps({'passed':True, 'modes':['joint','measured']}))
'''
    environment = {k:v for k,v in os.environ.items() if k not in ("DEEPSEEK_API_KEY", "APP_ACCESS_TOKEN")}
    environment["PYTHONIOENCODING"] = "utf-8"
    response = subprocess.run([executable, "-c", script], cwd=ROOT, env=environment, text=True,
                              encoding="utf-8", capture_output=True,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert response.returncode == 0, response.stderr
    assert json.loads(response.stdout)["passed"]
