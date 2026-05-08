"""Launch the dataset curator desktop UI (PyQt + local HTTP bridge for the visualizer)."""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dataset_curator.interface import run_app

if __name__ == "__main__":
    run_app()
