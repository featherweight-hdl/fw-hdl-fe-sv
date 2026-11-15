"""fw-hdl-fe-sv: static elaboration of fw-hdl SV designs to Zuspec IR
(core-layer1.md §6)."""
from .elab import ElabResult, Headers, elaborate

__all__ = ["elaborate", "ElabResult", "Headers"]
