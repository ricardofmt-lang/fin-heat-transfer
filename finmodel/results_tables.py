"""Tabular layout of the values printed by the numerical Excel macro."""

from __future__ import annotations

import pandas as pd

from finmodel.numerical import SimulationResult


def output_frames(result: SimulationResult) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return temperature, heat flux and grid tables, including Excel's blanks.

    ``PrintValues`` leaves the first heat-flux row empty. The grid worksheet
    prints one more face coordinate and resistance than it does cell widths,
    centers and Fourier numbers. Retaining those slots matters when comparing
    a CSV export or table directly with the workbook.
    """
    n = result.grid.n
    temperature = pd.DataFrame(result.temperature_k, columns=[f"cell_{i}" for i in range(1, n + 1)])
    temperature.insert(0, "time_s", result.times_s)

    initial_flux = [None] * (n + 1)
    flux = pd.DataFrame(
        [initial_flux, *result.cross_section_heat_flux_w],
        columns=[f"face_{i}" for i in range(1, n + 2)],
    )
    flux.insert(0, "time_s", result.times_s)

    grid = pd.DataFrame({
        "index": range(1, n + 2),
        "dx_m": [*result.grid.dx[1 : n + 1], None],
        "center_m": [*result.cell_centers_m, None],
        "fourier_number": [*result.grid.fourier_number[1 : n + 1], None],
        "face_position_m": result.face_positions_m,
        "local_resistance_m2K_W": result.grid.local_resistance[1 : n + 2],
    })
    return temperature, flux, grid
