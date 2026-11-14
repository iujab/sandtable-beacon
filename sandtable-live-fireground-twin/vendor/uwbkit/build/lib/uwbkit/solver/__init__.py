"""uwbkit.solver: UWB ranges -> positions and events (stdlib + numpy only).

- Tracker: robust LSQ + constant-velocity EKF per tag when anchors are known; MDS + SMACOF for an
  anchor-free mesh (canonical frame: ids[0] at the origin, ids[1] on +x, ids[2] with y > 0).
- solve_layout: anchor self-calibration from pairwise distances.
- canonical_frame: express any point set in that canonical frame.
- EventDetector: motion_start / motion_stop and proximity enter / exit with hysteresis.

Contract: docs/CONTRACTS.md section 2. Output messages: INTERFACE.md `pos` / `event` (plain floats, no NaN).
"""
from .events import EventDetector
from .geometry import canonical_frame, solve_layout
from .tracker import Tracker

__all__ = ["Tracker", "solve_layout", "canonical_frame", "EventDetector"]
