r"""Tabular formatting of numerical simulation results into pandas DataFrames.

This module converts the raw arrays produced by the finite-volume solver
(`SimulationResult`) into three structured tables (`temperature`, `flux`, and
`grid`) that mirror the worksheet layout printed by the Excel macro:
1. `temperature`: One row per printed time $t_m$ and one column per cell center $i = 1 \dots N$.
2. `flux`: One row per printed time $t_m$ and one column per cell face $i = 1 \dots N+1$.
   At the initial state ($t = 0$), heat fluxes are left blank (`None`), matching the
   Excel `CrossSectionHeatFlux` worksheet where fluxes are only evaluated after time
   stepping begins.
3. `grid`: One row per index $1 \dots N+1$. Because a 1D mesh of $N$ finite-volume
   cells has $N+1$ cell faces, the $(N+1)$-th row contains the right-boundary face
   position $x_{f,N+1} = L$ and right-boundary local contact resistance, while the
   cell-only columns (`dx_m`, `center_m`, `fourier_number`) are `None` in row $N+1$.
"""

from __future__ import annotations  # Postpone evaluation of type annotations

import pandas as pd  # Used to construct tabular DataFrames for UI display and CSV export

from finmodel.numerical import SimulationResult  # Result container from the numerical solver


def output_frames(result: SimulationResult) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build the `(temperature, flux, grid)` pandas DataFrames from a `SimulationResult`."""
    # Total number of finite-volume control volumes (cells) along the fin length.
    n = result.grid.n
    # Create the temperature DataFrame with columns `cell_1` through `cell_n` (values in kelvin).
    temperature = pd.DataFrame(
        result.temperature_k,  # 2D list of shape (n_printed_times, n_cells)
        columns=[f"cell_{i}" for i in range(1, n + 1)],  # 1-based cell column names
    )
    # Insert the simulation time column (`time_s`, in seconds) as the first column (index 0).
    temperature.insert(0, "time_s", result.times_s)

    # Create a row of `n + 1` blank (`None`) entries for the initial state (t = 0 s),
    # because conductive/convective face heat fluxes are only printed at t > 0 in the workbook.
    initial_flux = [None] * (n + 1)
    # Build the cross-sectional heat-flux DataFrame with columns `face_1` through `face_{n+1}` (in watts).
    flux = pd.DataFrame(
        [initial_flux, *result.cross_section_heat_flux_w],  # Prepend the blank t=0 row before printed flux rows
        columns=[f"face_{i}" for i in range(1, n + 2)],  # 1-based face column names (n + 1 faces)
    )
    # Insert the simulation time column (`time_s`, in seconds) as the first column (index 0).
    flux.insert(0, "time_s", result.times_s)

    # Build the spatial mesh and dimensionless Fourier number table (`n + 1` rows to include face n + 1).
    grid = pd.DataFrame({
        # 1-based index running from 1 to n + 1 (cells use 1..n; faces use 1..n+1).
        "index": range(1, n + 2),
        # Control-volume axial widths Δx_i (m) for cells 1..n, plus `None` on the (n+1)-th face-only row.
        "dx_m": [*result.grid.dx[1 : n + 1], None],
        # Cell-center axial coordinates x_{c,i} (m) for cells 1..n, plus `None` on row n + 1.
        "center_m": [*result.cell_centers_m, None],
        # Cell mesh Fourier numbers Fo_i = α_i Δt / Δx_i^2 for cells 1..n, plus `None` on row n + 1.
        "fourier_number": [*result.grid.fourier_number[1 : n + 1], None],
        # Axial coordinates of all n + 1 cell faces x_{f,i} (m), from x_{f,1} = 0 to x_{f,n+1} = L.
        "face_position_m": result.face_positions_m,
        # Local thermal contact resistance R''_{c,i} (m²·K/W) located at each face i = 1..n+1.
        "local_resistance_m2K_W": result.grid.local_resistance[1 : n + 2],
    })
    # Return the three DataFrames in the order `(temperature, flux, grid)`.
    return temperature, flux, grid
