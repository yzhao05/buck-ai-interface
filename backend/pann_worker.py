"""JSON-only bridge for an explicitly configured local PyTorch interpreter."""
import json
import sys
from types import SimpleNamespace

import numpy as np

from .pann_adapter import refine


def main():
    data = json.load(sys.stdin)
    parameters, diagnostics = refine(np.asarray(data["train"]), np.asarray(data["initial"]), SimpleNamespace(**data["options"]))
    print(json.dumps({"parameters": parameters.tolist(), "diagnostics": diagnostics}, ensure_ascii=True))


if __name__ == "__main__":
    main()
