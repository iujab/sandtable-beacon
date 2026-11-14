"""Test set-up: offline, replay-mode LLM, fixture data, and a private data directory so a running demo is never disturbed."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["LLM_BACKEND"] = "replay"
os.environ["LLM_RECORD"] = "0"
os.environ["SANDTABLE_DATA_MODE"] = "fixture"
for k in ("COSTAR_MODE", "MATTERPORT_MODE", "VEXCEL_MODE", "SANDTABLE_SCALE"):
    os.environ.pop(k, None)
os.environ["SANDTABLE_DATA_DIR"] = tempfile.mkdtemp(prefix="sandtable-test-")
os.environ["UWBKIT_URL"] = "ws://127.0.0.1:9/stream"      # nothing listens there: tests never touch a real hub
