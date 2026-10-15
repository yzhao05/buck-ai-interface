"""Paired adapter benchmark on existing Case15/16 archives; never runs PLECS.

Run with project Python and PANN_PYTHON (or installed torch). Truth is used
only after fitting. Archive clean reference and feedback-current column are
never passed to the fitter. Every requested run, including failures, is saved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.identification import integral_fit, weak_integral_seed
from backend.pann_adapter import refine
from backend.schemas import FitOptions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--truth-l-uh", type=float, required=True)
    parser.add_argument("--truth-c-uf", type=float, required=True)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--seed-only", action="store_true", help="Evaluate initialization only; no PANN refinement")
    parser.add_argument("--indices", type=int, nargs="+", default=list(range(7)))
    parser.add_argument("--states", nargs="+", choices=["measured", "joint"], default=["measured", "joint"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    options = FitOptions(method="pann", mode="LC", resistance_ohm=6,
                         epochs=args.epochs, pann_window_cycles=args.cycles)
    archive = np.load(args.archive, allow_pickle=False)
    truth = np.array([args.truth_l_uh*1e-6, args.truth_c_uf*1e-6, 6.])
    report = {
        "archive": args.archive.parent.name + "/" + args.archive.name,
        "archive_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        "code_sha256": {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                        ("backend/pann_adapter.py", "backend/identification.py", "tests/benchmark_pann_noise.py")},
        "options": options.model_dump(), "seed_only": args.seed_only, "evaluation_truth_H_F_ohm": truth.tolist(),
        "protocol": "All archived 0–0.8 ms training measurements; actual gate=drive[:,:,0]/Vin (ZOH); no clean states, clean reference, feedback current or truth passed to fitting. Same weighted-integral seed for both state modes. Best noisy training loss selects parameters AND states. No holdout or autonomous closed-loop validation in this benchmark; LC only, R fixed. All requested seeds retained.",
        "runs": [],
    }
    for index in args.indices:
        t = archive["time"]
        drive = archive["drive"][index, :, 0] / options.vin
        gate = np.r_[drive, drive[-1]] if len(drive) == len(t)-1 else drive
        train = np.column_stack((t, archive["measured"][index], gate))
        label = "clean" if index == 0 else f"noise{1 if index <= 3 else 3:02d}_seed{12+(index-1)%3}"
        initial, seed_diagnostic = weak_integral_seed(train, options)
        old_seed, _ = integral_fit(train, options)
        for mode in (["initialization_only"] if args.seed_only else args.states):
            started = time.perf_counter()
            row = {"label": label, "index": index, "initial_state_mode": mode,
                   "initial_parameters": initial.tolist(), "old_integral_parameters": old_seed.tolist(),
                   "initial_error_pct": (100*(initial/truth-1)).tolist(),
                   "old_integral_error_pct": (100*(old_seed/truth-1)).tolist(), "seed_diagnostics": seed_diagnostic}
            try:
                params, diag = (initial, seed_diagnostic) if args.seed_only else refine(train, initial, options.model_copy(update={"pann_initial_state": mode}))
                row.update(parameters=params.tolist(), error_pct=(100*(params/truth-1)).tolist(), diagnostics=diag)
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            row["elapsed_s"] = round(time.perf_counter()-started, 3)
            report["runs"].append(row)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({key: row[key] for key in ("label", "initial_state_mode", "elapsed_s", "error_pct", "error") if key in row}), flush=True)


if __name__ == "__main__":
    main()
