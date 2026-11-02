"""Package entry point for the 1D fin heat-transfer simulation model.

This package (`finmodel`) implements both the transient 1D finite-volume
numerical solver and the steady-state 1D analytical solver for extended-surface
(fin) and composite-bar heat transfer problems, together with 2D charting,
3D mesh animation, workbook parsing, and pre-programmed teaching cases.
"""

# Re-export the main numerical result container (`SimulationResult`) and the
# time-marching solver function (`simulate`) at the package top level so callers
# can simply write `from finmodel import simulate, SimulationResult`.
from .numerical import SimulationResult, simulate  # Main numerical solver API

# Explicitly declare the public symbols exported when `from finmodel import *` is used.
__all__ = ["SimulationResult", "simulate"]  # Public package exports
