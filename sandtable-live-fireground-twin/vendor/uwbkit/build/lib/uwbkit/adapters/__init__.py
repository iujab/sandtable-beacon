"""Adapters for boards that ship with their own firmware. Each adapter turns one text line of a
vendor's serial output into kit raw messages (docs/CONTRACTS.md section 3), so the hub can use the
board without reflashing it. ``LineParser(proto="auto")`` tries them in this order."""
from __future__ import annotations

from .dwm1001 import Dwm1001Adapter
from .mauwb import MaUwbAdapter
from .vendor_examples import VendorExampleAdapter

__all__ = ["make_adapters", "Dwm1001Adapter", "MaUwbAdapter", "VendorExampleAdapter"]


def make_adapters(id_map: dict | None = None) -> dict:
    """Fresh (stateful) adapter instances, keyed by ``--proto`` name."""
    return {"mauwb": MaUwbAdapter(id_map), "dwm1001": Dwm1001Adapter(id_map),
            "example": VendorExampleAdapter(id_map)}
