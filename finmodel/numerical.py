"""Transient 1D finite-volume numerical heat-transfer solver for fins and composite bars.

Pedagogical Overview
--------------------
This module discretises the fin along its axial coordinate $x \\in [0, L]$ into
$N$ non-overlapping control volumes (cells) $i = 1 \\dots N$, bounded by $N+1$
cross-sectional faces $i = 1 \\dots N+1$.

For each control volume $i$ of volume $V_i$, density $\\rho_i$, and specific heat
capacity $c_{p,i}$, conservation of energy over a time step $\\Delta t$ states:

    $\\rho_i c_{p,i} V_i \\frac{T_i^{\\text{new}} - T_i^{\\text{old}}}{\\Delta t} = q_{\\text{left},i} - q_{\\text{right},i} - q_{\\text{lat,conv},i} - q_{\\text{lat,rad},i} + \\dot{q}_{\\text{gen},i} V_i$

1. **Axial Conduction & Contact Resistance Across Faces**:
   Between adjacent cell centers $i-1$ and $i$, the total area-specific thermal
   resistance at face $i$ is the series sum of half-cell conduction resistances
   and any local contact resistance $R''_{\\text{local},i}$:
       $R''_{f,i} = R''_{\\text{local},i} + \\frac{\\Delta x_{i-1}}{2 k_{i-1}} + \\frac{\\Delta x_i}{2 k_i}$
   so the conductive heat flow from cell $i-1$ into cell $i$ across face area $A_{f,i}$ is:
       $q_i = \\frac{A_{f,i}}{R''_{f,i}} (T_{i-1} - T_i)$

2. **Time Discretisation (`exp` vs. `imp`)**:
   - **Explicit (`exp`, Forward Euler)**: Evaluates spatial conduction and
     convection fluxes at the known time level $T^{\\text{old}}$. Simple direct
     update, but conditionally stable (requires mesh Fourier number
     $\\text{Fo}_i = \\frac{\\alpha_i \\Delta t}{\\Delta x_i^2} \\lesssim \\frac{1}{2}$).
   - **Implicit (`imp`, Backward Euler)**: Evaluates linear conduction and
     convection fluxes at the unknown new time level $T^{\\text{new}}$, yielding
     a tridiagonal system of linear equations solved in $O(N)$ operations via
     the Thomas algorithm (TDMA). Unconditionally stable for arbitrary $\\Delta t$.
   - **Thermal Radiation**: Because radiative heat transfer scales with $T^4$,
     surface emission is split in semi-implicit form as $\\sigma \\varepsilon (T^{\\text{old}})^3 T^{\\text{new}}$
     while irradiation from the surroundings $\\sigma \\alpha T_{\\text{sur}}^4$ is added
     to the known right-hand side vector.
"""

from __future__ import annotations  # Postpone evaluation of type annotations

from dataclasses import dataclass, field  # Used to define structured mesh and simulation result containers
import math  # Standard mathematical functions (`pi`, `sqrt`, `isfinite`)
from typing import Any  # Generic type annotation for configuration dictionaries

from finmodel.analytical import evaluate_analytical_from_numerical  # Automatic 1D steady-state analytical solver


# Stefan-Boltzmann constant σ in W/(m²·K⁴) for thermal radiation calculations.
SIGMA = 5.670373e-8  # W/(m²·K⁴)
# Exact ratio of a circle's circumference to its diameter π = 3.141592653589793...
PI = math.pi  # Dimensionless constant π


def _zeros(n: int) -> list[float]:
    """Allocate a 1-based floating-point list of length `n + 1` (valid indices `1..n`).

    Following the original Fortran/VBA convention makes the finite-volume loop
    indices (`1..n` for cells and `1..n+1` for faces) match the textbook
    indexing directly. Index `0` is unused padding.
    """
    # Return a list of `n + 1` zeros so 1-based index `n` is valid.
    return [0.0] * (n + 1)


@dataclass
class Grid:
    """Spatial finite-volume mesh, cell geometry, material properties, and face resistances.

    All array fields use 1-based indexing:
    - Cell arrays have valid indices `1..n` (length `n + 1`).
    - Face arrays have valid indices `1..n+1` (length `n + 2`).
    """

    n: int  # Total number of finite-volume control volumes (cells) along the fin
    dx: list[float]  # Axial cell widths Δx_i (m), indices 1..n
    xc: list[float]  # Axial cell-center coordinates x_{c,i} (m) measured from x = 0, indices 1..n
    xf: list[float]  # Axial cell-face coordinates x_{f,i} (m) measured from x = 0, indices 1..n+1
    conductivity: list[float]  # Cell thermal conductivities k_i (W/(m·K)), indices 1..n
    thermal_capacity: list[float]  # Cell specific heat capacities c_{p,i} (J/(kg·K)), indices 1..n
    density: list[float]  # Cell mass densities ρ_i (kg/m³), indices 1..n
    heat_generated: list[float]  # Cell volumetric heat generation rates q̇_{gen,i} (W/m³), indices 1..n
    fourier_number: list[float]  # Cell mesh Fourier numbers Fo_i = α_i Δt / Δx_i², indices 1..n
    local_resistance: list[float]  # Local contact resistances R''_{local,i} (m²·K/W) at faces 1..n+1
    face_resistance: list[float]  # Total area-specific resistance R''_{f,i} (m²·K/W) at faces 1..n+1
    face_area: list[float]  # Cross-sectional areas A_{f,i} (m²) at faces 1..n+1
    face_emissivity: list[float]  # Axial face emissivities ε_{f,i} at faces 1..n+1 (ends and gas-gap walls)
    face_absorptivity: list[float]  # Axial face absorptivities α_{f,i} at faces 1..n+1
    cell_area: list[float]  # Mean cross-sectional area A_{c,i} (m²) of cells 1..n
    cell_perimeter: list[float]  # Effective lateral perimeter P_i (m) of cells 1..n (includes slant factor for tapered fins)
    cell_volume: list[float]  # Exact 3D volume V_i (m³) of cells 1..n
    lateral_emissivity: list[float]  # Lateral surface emissivity ε_{lat,i} of cells 1..n
    lateral_absorptivity: list[float]  # Lateral surface absorptivity α_{lat,i} of cells 1..n
    gas_gap: list[bool]  # Boolean flag indicating whether cell i (1..n) is a 1-cell radiative gas gap


@dataclass
class SimulationResult:
    """Complete output of a numerical simulation run, including the automatic analytical comparison.

    Unlike `Grid` (which uses 1-based arrays internally), the printed result
    tables `temperature_k` and `cross_section_heat_flux_w` use standard Python
    0-based indexing so they map directly to `pandas.DataFrame` rows and columns.
    """

    times_s: list[float]  # Printed simulation times (s), starting with 0.0
    temperature_k: list[list[float]]  # Cell temperatures (K) at each printed time: shape (len(times_s), n)
    heat_flux_times_s: list[float]  # Printed times (s) for heat flux (excludes t = 0.0): shape (len(times_s) - 1,)
    cross_section_heat_flux_w: list[list[float]]  # Face heat rates (W) at each printed time > 0: shape (len(times_s) - 1, n + 1)
    grid: Grid  # The finite-volume mesh and property arrays used for the simulation
    warnings: list[str] = field(default_factory=list)  # Advisory warnings if temperatures cross minimum_k or maximum_k
    analytical_temperature_k: list[float] = field(default_factory=list)  # 1D steady-state analytical temperatures (K) at cells 1..n
    analytical_heat_flux_w: list[float] = field(default_factory=list)  # 1D steady-state analytical heat fluxes (W) at faces 1..n+1

    @property
    def cell_centers_m(self) -> list[float]:
        """Return the 0-based list of `n` cell-center coordinates $x_{c,1} \\dots x_{c,n}$ in metres."""
        # Slice out indices 1..n from the 1-based `grid.xc` array.
        return self.grid.xc[1 : self.grid.n + 1]

    @property
    def face_positions_m(self) -> list[float]:
        """Return the 0-based list of `n + 1` face coordinates $x_{f,1} \\dots x_{f,n+1}$ in metres."""
        # Slice out indices 1..n+1 from the 1-based `grid.xf` array.
        return self.grid.xf[1 : self.grid.n + 2]

    def as_dict(self) -> dict[str, Any]:
        """Serialize the simulation results and mesh geometry into a JSON-ready dictionary."""
        # Construct the base dictionary of time-series arrays and mesh coordinates.
        payload: dict[str, Any] = {
            "times_s": self.times_s,  # Printed time stamps (s)
            "temperature_k": self.temperature_k,  # 2D temperature matrix (K)
            "heat_flux_times_s": self.heat_flux_times_s,  # Printed heat-flux time stamps (s)
            "cross_section_heat_flux_w": self.cross_section_heat_flux_w,  # 2D face heat-flux matrix (W)
            "grid": {
                "n": self.grid.n,  # Number of cells
                "dx_m": self.grid.dx[1 : self.grid.n + 1],  # Cell widths (m)
                "cell_centers_m": self.cell_centers_m,  # Cell center positions (m)
                "face_positions_m": self.face_positions_m,  # Face positions (m)
                "local_resistance_m2K_W": self.grid.local_resistance[1 : self.grid.n + 2],  # Face contact resistances (m²·K/W)
                "fourier_number": self.grid.fourier_number[1 : self.grid.n + 1],  # Cell Fourier numbers
            },
        }
        # Include temperature range warnings if any were triggered during the run.
        if self.warnings:
            # Attach the list of human-readable warning strings.
            payload["warnings"] = self.warnings
        # Include the steady-state analytical cell temperatures if present.
        if self.analytical_temperature_k:
            # Attach the 1D analytical temperature profile (K).
            payload["analytical_temperature_k"] = self.analytical_temperature_k
        # Include the steady-state analytical face heat fluxes if present.
        if self.analytical_heat_flux_w:
            # Attach the 1D analytical heat-flux profile (W).
            payload["analytical_heat_flux_w"] = self.analytical_heat_flux_w
        # Return the complete serializable dictionary.
        return payload


def _validate(config: dict[str, Any]) -> None:
    """Check physical and numerical validity of all simulation parameters before building the grid."""
    # Extract the time-stepping controls dictionary.
    c = config["controls"]
    # Verify that the time discretisation method is either implicit (`imp`) or explicit (`exp`).
    if c["method"] not in {"imp", "IMP", "Imp", "exp", "EXP", "Exp"}:
        # Reject unknown time integration methods.
        raise ValueError("TimeDiscretisation must be imp or exp")
    # Verify that total duration, integration time step Δt, and print interval Δt_print are strictly positive.
    if c["duration_s"] <= 0 or c["time_step_s"] <= 0 or c["output_interval_s"] <= 0:
        # Reject zero or negative timing parameters.
        raise ValueError("Simulation duration, time step, and output interval must be positive")
    # Extract the fin geometry dictionary and shape name.
    g = config["geometry"]
    shape = g["shape"]
    # Validate cross-sectional dimensions according to the selected fin shape:
    if shape == "Rectangular":
        # A rectangular fin must have positive width W and positive thickness t.
        if g["width_mm"] <= 0 or g["thickness_mm"] <= 0:
            # Reject non-positive rectangular dimensions.
            raise ValueError("Rectangular fin requires positive width and thickness")
    elif shape == "Cylindrical":
        # A cylindrical pin fin must have a positive diameter D.
        if g["diameter_mm"] <= 0:
            # Reject non-positive cylinder diameter.
            raise ValueError("Cylindrical fin requires positive diameter")
    elif shape == "Pyramidal":
        # A pyramidal fin must have positive base dimensions and non-negative tip dimensions (0 is a pointed tip).
        if g["width_base_mm"] <= 0 or g["thickness_base_mm"] <= 0 or g["width_top_mm"] < 0 or g["thickness_top_mm"] < 0:
            # Reject invalid pyramidal dimensions.
            raise ValueError("Pyramidal fin requires positive base and non-negative top dimensions")
    elif shape == "Conical":
        # A conical fin must have a positive base diameter and a non-negative tip diameter (0 is a pointed cone).
        if g["diameter_base_mm"] <= 0 or g["diameter_top_mm"] < 0:
            # Reject invalid conical dimensions.
            raise ValueError("Conical fin requires positive base diameter and non-negative top diameter")
    else:
        # Reject any unrecognized geometry shape string.
        raise ValueError(f"Unknown fin shape: {shape}")

    # Extract the list of axial sections (`domains`).
    domains = config["domains"]
    # Require at least one axial section.
    if not domains:
        # Reject configurations with no sections.
        raise ValueError("At least one domain is required")
    # Validate each section's physical properties and local contact resistances.
    for i, d in enumerate(domains, start=1):
        # Section axial length L_i must be strictly positive.
        if d["length_mm"] <= 0:
            # Reject zero or negative section length.
            raise ValueError(f"Domain {i} length must be positive")
        # Solid sections (non-gas-gap) must contain at least 1 control volume.
        if not d.get("gas_gap", False) and int(d["n_cells"]) < 1:
            # Reject solid sections with fewer than 1 cell.
            raise ValueError(f"Domain {i} requires at least one cell")
        # Thermal conductivity k, specific heat capacity c_p, and density ρ must all be strictly positive
        # so thermal diffusivity α = k / (ρ c_p) and cell capacitance ρ c_p V are positive and finite.
        if d["conductivity"] <= 0 or d["thermal_capacity"] <= 0 or d["density"] <= 0:
            # Reject non-positive material properties.
            raise ValueError(f"Domain {i} conductivity, thermal capacity, and density must be positive")
        # Track the previous contact resistance position to ensure resistances are listed in ascending order of x.
        prev_distance = None
        # Validate each local contact resistance inside section `i`.
        for r in d.get("resistances", []):
            # Contact resistance R''_c (m²·K/W) cannot be negative (that would violate the Second Law).
            if float(r["value"]) < 0:
                # Reject negative contact resistance.
                raise ValueError(f"Domain {i} local resistance cannot be negative")
            # Position of the resistance measured from the start of section `i` (in mm).
            dist = float(r["distance_mm"])
            # The resistance position must lie within `[0, length_mm]` of its section.
            if not 0 <= dist <= float(d["length_mm"]):
                # Reject resistance positions outside the section boundaries.
                raise ValueError(f"Domain {i} resistance location is outside its domain")
            # Resistances within the same section must be given in strictly increasing axial order.
            if prev_distance is not None and dist <= prev_distance:
                # Reject out-of-order or duplicate resistance positions.
                raise ValueError(f"Domain {i} resistances must be in increasing order")
            # Update `prev_distance` for the next iteration.
            prev_distance = dist
    # Validate boundary condition types and initialization mode in `config["temperature"]`.
    t = config["temperature"]
    for key in ("left_boundary", "right_boundary"):
        # Each end boundary must be Dirichlet (`Temp`), Robin (`HeatFlux`), or homogeneous Neumann (`NoHeatFlux`).
        if t[key] not in {"Temp", "HeatFlux", "NoHeatFlux"}:
            # Reject unrecognized boundary condition types.
            raise ValueError(f"{key} must be Temp, HeatFlux, or NoHeatFlux")
    # Initialization mode must be uniform default (`DEFAULT`), uniform DEFCELL (`DEFCELL`), or per-cell (`CELL`).
    if t["initial_type"] not in {"DEFAULT", "DEFCELL", "CELL"}:
        # Reject unrecognized initialization modes.
        raise ValueError("Initial type must be DEFAULT, DEFCELL, or CELL")
    # If `CELL` initialization is selected, at least one cell temperature entry is required.
    if t["initial_type"] == "CELL" and not t.get("initial_cells"):
        # Reject empty `initial_cells` when `initial_type == "CELL"`.
        raise ValueError("CELL initialization requires at least one cell temperature")


def build_grid(config: dict[str, Any]) -> Grid:
    """Construct the 1D finite-volume mesh (`Grid`), cell geometry, and face thermal resistances."""
    # Run full input validation before allocating mesh arrays.
    _validate(config)
    # Extract the list of axial sections (`domains`) and the time step Δt (s).
    domains = config["domains"]
    dt = float(config["controls"]["time_step_s"])
    # Determine the number of control volumes in each section (gas gaps always use 1 cell).
    n_each = [1 if d.get("gas_gap", False) else int(d["n_cells"]) for d in domains]
    # Total number of control volumes N across the entire fin is the sum of cells in all sections.
    n = sum(n_each)
    # Allocate 1-based cell arrays (indices 1..n) and face arrays (indices 1..n+1).
    dx, xc, conductivity = _zeros(n), _zeros(n), _zeros(n)
    capacity, density, generated = _zeros(n), _zeros(n), _zeros(n)
    fourier = _zeros(n)
    lateral_e, lateral_a = _zeros(n), _zeros(n)
    gas_gap = [False] * (n + 1)
    xf, face_area, local_r = _zeros(n + 1), _zeros(n + 1), _zeros(n + 1)
    face_e, face_a, face_r = _zeros(n + 1), _zeros(n + 1), _zeros(n + 1)
    cell_area, perimeter, volume = _zeros(n), _zeros(n), _zeros(n)

    # Assign radiative absorptivity and emissivity at the outer left boundary face (face 1, x = 0)
    # from the first section, falling back to lateral properties if end-face properties are omitted.
    face_a[1] = float(domains[0].get("left_absorptivity", domains[0].get("lateral_absorptivity", 0)))
    face_e[1] = float(domains[0].get("left_emissivity", domains[0].get("lateral_emissivity", 0)))
    # Assign radiative absorptivity and emissivity at the outer right boundary face (face n + 1, x = L)
    # from the last section, falling back to lateral properties if end-face properties are omitted.
    face_a[n + 1] = float(domains[-1].get("right_absorptivity", domains[-1].get("lateral_absorptivity", 0)))
    face_e[n + 1] = float(domains[-1].get("right_emissivity", domains[-1].get("lateral_emissivity", 0)))

    # First pass over all sections: assign uniform nominal cell width Δx = L_domain / count
    # and copy section material/radiative properties into each cell `1..n`.
    cell = 0
    for d, count in zip(domains, n_each):
        # Convert section length from millimetres to metres.
        length = float(d["length_mm"]) / 1000
        # Populate each cell belonging to the current section.
        for _ in range(count):
            # Advance the 1-based global cell index.
            cell += 1
            # Nominal uniform cell width Δx (m) before any local contact resistance adjustments.
            dx[cell] = length / count
            # Thermal conductivity k_i (W/(m·K)).
            conductivity[cell] = float(d["conductivity"])
            # Specific heat capacity c_{p,i} (J/(kg·K)).
            capacity[cell] = float(d["thermal_capacity"])
            # Mass density ρ_i (kg/m³).
            density[cell] = float(d["density"])
            # Volumetric internal heat generation rate q̇_{gen,i} (W/m³).
            generated[cell] = float(d.get("heat_generated", 0))
            # Lateral surface emissivity ε_{lat,i} (dimensionless, 0..1).
            lateral_e[cell] = float(d.get("lateral_emissivity", 0))
            # Lateral surface absorptivity α_{lat,i} (dimensionless, 0..1).
            lateral_a[cell] = float(d.get("lateral_absorptivity", 0))
        # If this section is a 1-cell radiative gas gap, record its left and right bounding wall
        # emissivities and absorptivities on the two faces bounding `cell` (faces `cell` and `cell + 1`).
        if d.get("gas_gap", False):
            # Mark this cell as a gas gap so internal surface-to-surface radiation is computed across it.
            gas_gap[cell] = True
            # Emissivity of the solid wall on the left side of the gas gap (face `cell`).
            face_e[cell] = float(d.get("gas_face_emissivity_left", 0))
            # Emissivity of the solid wall on the right side of the gas gap (face `cell + 1`).
            face_e[cell + 1] = float(d.get("gas_face_emissivity_right", 0))
            # Absorptivity of the solid wall on the left side of the gas gap (face `cell`).
            face_a[cell] = float(d.get("gas_face_absorptivity_left", 0))
            # Absorptivity of the solid wall on the right side of the gas gap (face `cell + 1`).
            face_a[cell + 1] = float(d.get("gas_face_absorptivity_right", 0))

    # Second pass: adjust cell widths Δx_i so that any interior local contact resistances
    # fall exactly on cell faces, and store each resistance R''_c on `local_r[face_index]`.
    if any(d.get("resistances") for d in domains):
        # Track the 1-based global index of the first cell in the current section.
        domain_start = 1
        # Process each section (`d`) and its cell count (`count`).
        for d, count in zip(domains, n_each):
            # Section length in metres and nominal uniform cell width Δx_nom = L / count.
            length = float(d["length_mm"]) / 1000
            nominal_dx = length / count
            # Retrieve the list of local contact resistances in this section.
            res = d.get("resistances", [])
            if res:
                # Collect resistances that lie strictly in the interior of the section (not snapped to the ends).
                interior: list[tuple[float, float]] = []
                for r in res:
                    # Convert resistance position from mm (relative to section start) to metres.
                    position = float(r["distance_mm"]) / 1000
                    # Area-specific thermal contact resistance R''_c in m²·K/W.
                    val = float(r["value"])
                    if count == 1:
                        # A 1-cell section has no interior faces; snap the resistance to its closer boundary face.
                        if position <= length / 2:
                            # Attach to the left face of this 1-cell section.
                            local_r[domain_start] += val
                        else:
                            # Attach to the right face of this 1-cell section.
                            local_r[domain_start + 1] += val
                    elif position < nominal_dx / 2:
                        # Resistance is within half a nominal cell of the section's left boundary -> snap to left face.
                        local_r[domain_start] += val
                    elif length - position < nominal_dx / 2:
                        # Resistance is within half a nominal cell of the section's right boundary -> snap to right face.
                        local_r[domain_start + count] += val
                    else:
                        # Resistance lies in the interior of the section and will align with an interior cell face.
                        interior.append((position, val))

                if interior:
                    # Merge any interior resistances that lie within half a nominal cell width of each other
                    # so we never create zero-width cells between two nearly coincident resistances.
                    grouped: list[list[float]] = []
                    for position, val in interior:
                        # If within Δx_nom / 2 of the previous resistance group, add the resistances in series.
                        if grouped and position - grouped[-1][0] < nominal_dx / 2:
                            grouped[-1][1] += val
                        else:
                            # Start a new interior resistance group at `position`.
                            grouped.append([position, val])

                    # `cell` is the 1-based global cell index; `cell_domain` is the 1-based cell index within this section.
                    cell = domain_start
                    cell_domain = 1
                    # `aux_x` tracks the axial distance (m) from the start of the section to the previous resistance face.
                    aux_x = 0.0
                    num_groups = len(grouped)
                    # Subdivide the intervals between consecutive interior resistances into integer numbers of cells.
                    for g_idx, (position, val) in enumerate(grouped):
                        # Number of subsequent resistance groups that still need at least 1 cell each after this one.
                        remaining_groups_after = num_groups - 1 - g_idx
                        # Maximum number of cells we can allocate to the current interval `[aux_x, position]`.
                        max_cells_here = (count - 1 - remaining_groups_after) - (cell_domain - 1)
                        if max_cells_here < 1:
                            # If there are more resistance groups than interior faces, attach to the current face.
                            local_r[cell] += val
                            continue
                        # Ideal floating-point number of nominal cells spanning `[aux_x, position]`.
                        n_real = (position - aux_x) / nominal_dx
                        # Integer floor of `n_real`.
                        n_int = int(n_real // 1)
                        # Round to the nearest integer number of cells (`n_aux`).
                        n_aux = n_int + 1 if n_real - n_int > 0.5 else n_int
                        # Clamp `n_aux` to `[1, max_cells_here]` so at least 1 cell spans `[aux_x, position]`
                        # and at least 1 cell remains after the final resistance.
                        n_aux = max(1, min(n_aux, max_cells_here))
                        # Uniform width Δx of each of the `n_aux` cells before this resistance.
                        aux_dx1 = (position - aux_x) / n_aux
                        # Assign `aux_dx1` to those `n_aux` cells.
                        for _ in range(n_aux):
                            dx[cell] = aux_dx1
                            cell += 1
                            cell_domain += 1
                        # Place the contact resistance on the face immediately following those `n_aux` cells.
                        local_r[cell] += val
                        # Advance `aux_x` to the current resistance position.
                        aux_x = position

                    # Distribute the remaining section length `length - aux_x` evenly across the remaining cells.
                    if aux_x > 0.0 and cell_domain <= count:
                        # Width of each cell between the last interior resistance and the end of the section.
                        aux_dx = (length - aux_x) / (count - (cell_domain - 1))
                        # Assign `aux_dx` to all remaining cells in this section (`cell_domain .. count`).
                        for _ in range(cell_domain, count + 1):
                            dx[cell] = aux_dx
                            cell += 1
                            cell_domain += 1
            # Advance `domain_start` to the first cell of the next section.
            domain_start += count

    # Compute the cell-center coordinates `xc[1..n]` and cell-face coordinates `xf[1..n+1]` from `dx[1..n]`.
    xc[1] = dx[1] / 2  # First cell center is half of Δx_1 from the base (x = 0)
    xf[1] = 0.0  # Leftmost boundary face (face 1) sits at the origin x = 0
    for i in range(2, n + 1):
        # Distance between center i-1 and center i is 0.5 * (Δx_{i-1} + Δx_i).
        xc[i] = xc[i - 1] + 0.5 * (dx[i - 1] + dx[i])
        # Face i sits at the right edge of cell i-1: x_{f,i} = x_{f,i-1} + Δx_{i-1}.
        xf[i] = xf[i - 1] + dx[i - 1]
    # Rightmost boundary face (face n + 1) sits at the right edge of cell n (x = L).
    xf[n + 1] = xf[n] + dx[n]

    # Compute the dimensionless mesh Fourier number Fo_i = α_i * Δt / Δx_i² for each cell i = 1..n,
    # where α_i = k_i / (ρ_i * c_{p,i}) is the thermal diffusivity (m²/s).
    cell = 0
    for d, count in zip(domains, n_each):
        # Thermal diffusivity α = k / (ρ * c_p) in m²/s for this section.
        diffusivity = float(d["conductivity"]) / float(d["density"]) / float(d["thermal_capacity"])
        for _ in range(count):
            cell += 1
            # Mesh Fourier number Fo_i = α * Δt / Δx_i².
            fourier[cell] = dt * diffusivity / dx[cell] ** 2

    # Compute face cross-sectional areas `face_area[1..n+1]`, cell lateral perimeters `perimeter[1..n]`,
    # and exact cell volumes `volume[1..n]` according to the selected 3D fin geometry.
    g = config["geometry"]
    shape = g["shape"]
    # Total axial fin length L (m) across all sections.
    total_length = sum(float(d["length_mm"]) / 1000 for d in domains)
    if shape == "Rectangular":
        # Convert constant rectangular width W and thickness t from mm to m.
        width, thick = float(g["width_mm"]) / 1000, float(g["thickness_mm"]) / 1000
        for i in range(1, n + 1):
            # Constant cross-sectional area A_f = W * t (m²).
            face_area[i] = width * thick
            # Constant cross-sectional perimeter P = 2 * (W + t) (m).
            perimeter[i] = 2 * (width + thick)
            # Prismatic cell volume V_i = A_f * Δx_i (m³).
            volume[i] = face_area[i] * dx[i]
        # Rightmost boundary face area A_{f,n+1} = W * t (m²).
        face_area[n + 1] = width * thick
    elif shape == "Cylindrical":
        # Convert constant cylinder diameter D from mm to m.
        diameter = float(g["diameter_mm"]) / 1000
        for i in range(1, n + 1):
            # Circular cross-sectional area A_f = π * D² / 4 (m²).
            face_area[i] = PI * diameter**2 / 4
            # Circular perimeter (circumference) P = π * D (m).
            perimeter[i] = PI * diameter
            # Cylindrical cell volume V_i = A_f * Δx_i (m³).
            volume[i] = face_area[i] * dx[i]
        # Rightmost boundary face area A_{f,n+1} = π * D² / 4 (m²).
        face_area[n + 1] = PI * diameter**2 / 4
    elif shape == "Pyramidal":
        # Convert pyramidal base width W_base and base thickness t_base from mm to m.
        width = float(g["width_base_mm"]) / 1000
        thick = float(g["thickness_base_mm"]) / 1000
        # Linear taper rates dW/dx and dt/dx (m/m) along the fin length L.
        slope_w = (width - float(g["width_top_mm"]) / 1000) / total_length
        slope_t = (thick - float(g["thickness_top_mm"]) / 1000) / total_length
        # Slant-length factors sqrt(1 + (0.5 * slope)²) converting axial step dx into true inclined surface area.
        slant_w = math.sqrt(1.0 + (0.5 * slope_w) ** 2)
        slant_t = math.sqrt(1.0 + (0.5 * slope_t) ** 2)
        # Base face area A_{f,1} at x = 0.
        face_area[1] = width * thick
        for i in range(1, n + 1):
            # Width and thickness at the right face (face i + 1) of cell i.
            width_1 = width - slope_w * dx[i]
            thick_1 = thick - slope_t * dx[i]
            # Cross-sectional area at face i + 1.
            face_area[i + 1] = width_1 * thick_1
            # Effective lateral perimeter P_i so that P_i * dx[i] equals the exact lateral surface area of the frustum.
            perimeter[i] = (width + width_1) * slant_t + (thick + thick_1) * slant_w
            # Exact prismoidal volume V_i = (Δx_i / 6) * (A_left + 4*A_mid + A_right) for a rectangular frustum.
            volume[i] = (dx[i] / 6.0) * (
                2.0 * width * thick + width * thick_1 + width_1 * thick + 2.0 * width_1 * thick_1
            )
            # Advance (width, thick) to the right face of cell i for the next cell.
            width, thick = width_1, thick_1
    elif shape == "Conical":
        # Convert conical base diameter D_base from mm to m.
        diameter = float(g["diameter_base_mm"]) / 1000
        # Linear diameter taper rate -dD/dx (m/m) along the fin length L.
        slope = (diameter - float(g["diameter_top_mm"]) / 1000) / total_length
        # Slant factor sqrt(1 + (dR/dx)²) = sqrt(1 + (0.5 * dD/dx)²) for the lateral surface of a right circular cone.
        slant = math.sqrt(1.0 + (0.5 * slope) ** 2)
        # Base circumference P_1 = π * D_base at x = 0.
        p = PI * diameter
        # Base cross-sectional area A_{f,1} = π * D_base² / 4 at x = 0.
        face_area[1] = PI * diameter**2 / 4
        for i in range(1, n + 1):
            # Diameter and circumference at the right face (face i + 1) of cell i.
            diameter_1 = diameter - slope * dx[i]
            p_1 = PI * diameter_1
            # Cross-sectional area at face i + 1.
            face_area[i + 1] = PI * diameter_1**2 / 4
            # Effective lateral perimeter P_i = 0.5 * (P_left + P_right) * slant so P_i * dx[i] is the exact cone frustum lateral area.
            perimeter[i] = 0.5 * (p + p_1) * slant
            # Exact volume of a conical frustum: V_i = (Δx_i / 3) * (A_i + sqrt(A_i * A_{i+1}) + A_{i+1}).
            volume[i] = (dx[i] / 3.0) * (
                face_area[i] + math.sqrt(max(0.0, face_area[i] * face_area[i + 1])) + face_area[i + 1]
            )
            # Advance (diameter, p) to the right face of cell i for the next cell.
            diameter, p = diameter_1, p_1
    else:
        # Reject any unknown shape string.
        raise ValueError(f"Unknown fin shape: {shape}")
    # Compute each cell's mean cross-sectional area A_{c,i} as the average of its left and right face areas.
    for i in range(1, n + 1):
        cell_area[i] = 0.5 * (face_area[i] + face_area[i + 1])

    # Compute the total area-specific thermal resistance `face_r[1..n+1]` (m²·K/W) at every face:
    t = config["temperature"]
    # Left boundary face (face 1, x = 0): local contact resistance R''_1 plus half-cell conduction Δx_1 / (2 k_1).
    face_r[1] = local_r[1] + dx[1] / 2 / conductivity[1]
    if t["left_boundary"] == "HeatFlux":
        # When the left boundary is convective (`HeatFlux`), add the convective film resistance 1 / h_left in series.
        h_left = float(t["left_convection"])
        if h_left < 0:
            # Negative convection coefficients are physically invalid.
            raise ValueError("Left convection coefficient cannot be negative")
        if h_left > 0:
            # Add convective film resistance 1 / h_left (m²·K/W) to face 1.
            face_r[1] += 1.0 / h_left
    # Right boundary face (face n + 1, x = L): local contact resistance R''_{n+1} plus half-cell conduction Δx_n / (2 k_n).
    face_r[n + 1] = local_r[n + 1] + dx[n] / 2 / conductivity[n]
    if t["right_boundary"] == "HeatFlux":
        # When the right boundary is convective (`HeatFlux`), add the convective film resistance 1 / h_right in series.
        h_right = float(t["right_convection"])
        if h_right < 0:
            # Negative convection coefficients are physically invalid.
            raise ValueError("Right convection coefficient cannot be negative")
        if h_right > 0:
            # Add convective film resistance 1 / h_right (m²·K/W) to face n + 1.
            face_r[n + 1] += 1.0 / h_right
    # Interior faces i = 2..n: series sum of local contact resistance R''_i and the two adjacent half-cell conduction resistances.
    for i in range(2, n + 1):
        face_r[i] = local_r[i] + dx[i - 1] / 2 / conductivity[i - 1] + dx[i] / 2 / conductivity[i]

    # Package and return the populated `Grid` dataclass.
    return Grid(n, dx, xc, xf, conductivity, capacity, density, generated,
                fourier, local_r, face_r, face_area, face_e, face_a,
                cell_area, perimeter, volume, lateral_e, lateral_a, gas_gap)


def _transport_coefficients(
    config: dict[str, Any], gr: Grid, old: list[float]
) -> tuple[list[float], list[float], list[float], list[float]]:
    """Assemble the dimensionless finite-volume transport coefficients `(left, center, right, known)`
    for advancing cell temperatures from `old` ($T^m$) by one time step $\\Delta t$.

    For each cell $i = 1 \\dots N$, let $C_i = \\rho_i c_{p,i} V_i$ (J/K) be the cell's
    thermal capacitance. Dividing the cell energy balance by $C_i / \\Delta t$ gives:
    - `left[i]`   $= \\frac{\\Delta t A_{f,i}}{C_i R''_{f,i}}$ (coupling to left neighbour $T_{i-1}$)
    - `right[i]`  $= \\frac{\\Delta t A_{f,i+1}}{C_i R''_{f,i+1}}$ (coupling to right neighbour $T_{i+1}$)
    - `center[i]` $= \\text{left}[i] + \\text{right}[i] + \\text{lateral sink terms}$ (self-coupling of $T_i$)
    - `known[i]`  $= \\frac{\\Delta t}{C_i} (\\dot{q}_{\\text{gen},i} V_i + \\text{boundary/ambient/radiation sources})$
    """
    # Number of cells N and references to `controls` and `temperature` config dictionaries.
    n = gr.n
    c, t = config["controls"], config["temperature"]
    # Time step Δt in seconds.
    dt = float(c["time_step_s"])
    # Allocate 1-based coefficient vectors for cells 1..n.
    left, center, right, known = _zeros(n), _zeros(n), _zeros(n), _zeros(n)
    # 1. Axial conduction across cell faces and internal volumetric heat generation:
    for i in range(1, n + 1):
        # Dimensionless conductance coupling cell i to its left face i: Δt * A_{f,i} / (ρ_i c_{p,i} V_i R''_{f,i}).
        left[i] = dt * gr.face_area[i] / gr.cell_volume[i] / gr.density[i] / gr.thermal_capacity[i] / gr.face_resistance[i]
        # Dimensionless conductance coupling cell i to its right face i + 1: Δt * A_{f,i+1} / (ρ_i c_{p,i} V_i R''_{f,i+1}).
        right[i] = dt * gr.face_area[i + 1] / gr.cell_volume[i] / gr.density[i] / gr.thermal_capacity[i] / gr.face_resistance[i + 1]
        # Diagonal self-conductance is initially the sum of left and right face conductances.
        center[i] = left[i] + right[i]
        # Temperature increment (K) from internal volumetric heat generation: Δt * q̇_{gen,i} / (ρ_i c_{p,i}).
        known[i] = dt * gr.heat_generated[i] / gr.density[i] / gr.thermal_capacity[i]

    # 2. Left boundary condition at face 1 (cell 1, x = 0):
    # Since there is no "cell 0", `left[1]` either vanishes (`NoHeatFlux`) or multiplies a known
    # boundary/ambient temperature (`Temp` or `HeatFlux`) and moves into `known[1]`.
    if t["left_boundary"] == "NoHeatFlux":
        # Adiabatic left end: zero heat flux across face 1, so remove `left[1]` from `center[1]`.
        left[1] = 0.0
        center[1] = right[1]
    elif t["left_boundary"] == "HeatFlux":
        # Convective left end: if h_left > 0, heat flows from ambient air `left_air_temperature_k` through `face_resistance[1]`.
        if float(t["left_convection"]) > 0:
            # Add convective source term `left[1] * T_air,L` to `known[1]` and zero out `left[1]`.
            known[1] += left[1] * float(t["left_air_temperature_k"])
            left[1] = 0.0
        else:
            # If h_left == 0, there is zero convective heat transfer at face 1 (same as adiabatic for convection).
            left[1] = 0.0
            center[1] = right[1]
    else:
        # Imposed temperature (`Temp`) at x = 0: add `left[1] * T_left` to `known[1]` and zero out `left[1]`.
        known[1] += left[1] * float(t["left_temperature_k"])
        left[1] = 0.0

    # 3. Right boundary condition at face n + 1 (cell n, x = L):
    # Since there is no "cell n + 1", `right[n]` either vanishes (`NoHeatFlux`) or multiplies a known
    # boundary/ambient temperature (`Temp` or `HeatFlux`) and moves into `known[n]`.
    if t["right_boundary"] == "NoHeatFlux":
        # Adiabatic right end: zero heat flux across face n + 1, so remove `right[n]` from `center[n]`.
        right[n] = 0.0
        center[n] = left[n]
    elif t["right_boundary"] == "HeatFlux":
        # Convective right end: if h_right > 0, heat flows to/from ambient air `right_air_temperature_k`.
        if float(t["right_convection"]) > 0:
            # Add convective source term `right[n] * T_air,R` to `known[n]` and zero out `right[n]`.
            known[n] += right[n] * float(t["right_air_temperature_k"])
            right[n] = 0.0
        else:
            # If h_right == 0, zero convective flux across face n + 1.
            right[n] = 0.0
            center[n] = left[n]
    else:
        # Imposed temperature (`Temp`) at x = L: add `right[n] * T_right` to `known[n]` and zero out `right[n]`.
        known[n] += right[n] * float(t["right_temperature_k"])
        right[n] = 0.0

    # 4. Lateral convection along the fin perimeter (Newton's law of cooling: q_lat = h * P_i * Δx_i * (T_i - T_air)):
    if t["lateral_flux"]:
        # Lateral convection coefficient h (W/(m²·K)) and ambient air temperature T_air (K).
        h = float(t["lateral_convection"])
        air = float(t["lateral_air_temperature_k"])
        for i in range(1, n + 1):
            # Add lateral convection sink coefficient to `center[i]` and ambient source term to `known[i]`.
            center[i] += dt * gr.cell_perimeter[i] * gr.dx[i] * h / gr.cell_volume[i] / gr.density[i] / gr.thermal_capacity[i]
            known[i] += dt * gr.cell_perimeter[i] * gr.dx[i] * h * air / gr.cell_volume[i] / gr.density[i] / gr.thermal_capacity[i]

    # 5. Lateral thermal radiation along the fin perimeter:
    # Net radiative loss = P_i * Δx_i * σ * (ε_{lat,i} * T_i⁴ - α_{lat,i} * T_sur⁴),
    # semi-implicitly linearized as ε_{lat,i} * (T_i^old)³ * T_i in `center[i]` and α_{lat,i} * T_sur⁴ in `known[i]`.
    if t["lateral_radiation"]:
        # Fourth power of the lateral surroundings temperature T_sur⁴ (K⁴).
        surround4 = float(t["lateral_surround_k"]) ** 4
        for i in range(1, n + 1):
            # Absorbed surroundings irradiation added to `known[i]`.
            known[i] += dt * gr.cell_perimeter[i] * gr.dx[i] * SIGMA * gr.lateral_absorptivity[i] * surround4 / gr.cell_volume[i] / gr.thermal_capacity[i] / gr.density[i]
            # Emitted thermal radiation coefficient (proportional to old[i]³) added to `center[i]`.
            center[i] += dt * gr.cell_perimeter[i] * gr.dx[i] * SIGMA * gr.lateral_emissivity[i] * old[i] ** 3 / gr.cell_volume[i] / gr.thermal_capacity[i] / gr.density[i]

    # 6. End-face thermal radiation at the left boundary (face 1, when `left_boundary == "HeatFlux"` and `left_radiation` is True):
    if t["left_boundary"] == "HeatFlux" and t["left_radiation"]:
        # Absorbed irradiation on face 1 added to `known[1]`.
        known[1] += dt * SIGMA * gr.face_area[1] * gr.face_absorptivity[1] * float(t["left_surround_k"]) ** 4 / gr.cell_volume[1] / gr.thermal_capacity[1] / gr.density[1]
        # Linearized emission coefficient on face 1 added to `center[1]`.
        center[1] += dt * SIGMA * gr.face_area[1] * gr.face_emissivity[1] * old[1] ** 3 / gr.cell_volume[1] / gr.thermal_capacity[1] / gr.density[1]

    # 7. End-face thermal radiation at the right boundary (face n + 1, when `right_boundary == "HeatFlux"` and `right_radiation` is True):
    if t["right_boundary"] == "HeatFlux" and t["right_radiation"]:
        # Absorbed irradiation on face n + 1 added to `known[n]`.
        known[n] += dt * SIGMA * gr.face_area[n + 1] * gr.face_absorptivity[n + 1] * float(t["right_surround_k"]) ** 4 / gr.cell_volume[n] / gr.thermal_capacity[n] / gr.density[n]
        # Linearized emission coefficient on face n + 1 added to `center[n]`.
        center[n] += dt * SIGMA * gr.face_area[n + 1] * gr.face_emissivity[n + 1] * old[n] ** 3 / gr.cell_volume[n] / gr.thermal_capacity[n] / gr.density[n]

    # 8. Internal surface-to-surface radiation across any interior 1-cell gas gap (cell i, bounded by solid cells i - 1 and i + 1):
    for i in range(2, n):
        if gr.gas_gap[i]:
            # Net radiative heat transfer rate q_rad (W) from left solid cell (i - 1, face i) to right solid cell (i + 1, face i + 1):
            q_rad = SIGMA * gr.cell_area[i] * (
                gr.face_emissivity[i] * gr.face_absorptivity[i + 1] * old[i - 1] ** 4
                - gr.face_absorptivity[i] * gr.face_emissivity[i + 1] * old[i + 1] ** 4
            )
            # Subtract q_rad from the left solid cell (i - 1) and add q_rad to the right solid cell (i + 1),
            # normalized by each solid cell's thermal capacitance ρ * c_p * V.
            known[i - 1] -= dt * q_rad / gr.cell_volume[i - 1] / gr.thermal_capacity[i - 1] / gr.density[i - 1]
            known[i + 1] += dt * q_rad / gr.cell_volume[i + 1] / gr.thermal_capacity[i + 1] / gr.density[i + 1]
    # Return the four 1-based transport coefficient vectors.
    return left, center, right, known


def _step(config: dict[str, Any], gr: Grid, old: list[float]) -> list[float]:
    """Advance the cell temperature vector `old` ($T^m$) by one time step $\\Delta t$ to produce `result` ($T^{m+1}$)."""
    # Compute the finite-volume transport coefficients at the current time step.
    left, center, right, known = _transport_coefficients(config, gr, old)
    # Number of cells N and output temperature vector `result` (1-based, indices 1..n).
    n = gr.n
    result = _zeros(n)
    # Check whether the user selected Explicit (`exp`) or Implicit (`imp`) time integration.
    method = config["controls"]["method"].lower()
    if method == "exp":
        # Explicit Forward-Euler scheme:
        # T_i^{m+1} = known_i + left_i * T_{i-1}^m + (1 - center_i) * T_i^m + right_i * T_{i+1}^m
        for i in range(1, n + 1):
            # Diagonal weight in the explicit update is `1 - center[i]`.
            center[i] = 1 - center[i]
        # Update cell 1 (left boundary cell; `left[1]` is already incorporated into `known[1]`).
        result[1] = known[1] + center[1] * old[1] + right[1] * old[2]
        # Update interior cells i = 2..n-1.
        for i in range(2, n):
            result[i] = known[i] + left[i] * old[i - 1] + center[i] * old[i] + right[i] * old[i + 1]
        # Update cell n (right boundary cell; `right[n]` is already incorporated into `known[n]`).
        result[n] = known[n] + left[n] * old[n - 1] + center[n] * old[n]
    else:
        # Implicit Backward-Euler scheme:
        # (-left_i) * T_{i-1}^{m+1} + (1 + center_i) * T_i^{m+1} + (-right_i) * T_{i+1}^{m+1} = T_i^m + known_i
        # Solve this tridiagonal linear system in O(N) time using the Thomas algorithm (TDMA):
        for i in range(1, n + 1):
            # Add the previous time-step temperature T_i^m to the right-hand side vector `known[i]`.
            known[i] += old[i]
            # Sub-diagonal coefficient A_i = -left[i].
            left[i] = -left[i]
            # Main-diagonal coefficient B_i = 1 + center[i].
            center[i] = 1 + center[i]
            # Super-diagonal coefficient C_i = -right[i].
            right[i] = -right[i]
        # Forward elimination pass (eliminate sub-diagonal `left[i]` for rows i = 2..n):
        for i in range(2, n + 1):
            # Elimination multiplier f = -A_i / B_{i-1}.
            f = -left[i] / center[i - 1]
            # Zero out the sub-diagonal entry in row i.
            left[i] = 0.0
            # Update the main diagonal B_i <- B_i + f * C_{i-1}.
            center[i] += f * right[i - 1]
            # Update the right-hand side D_i <- D_i + f * D_{i-1}.
            known[i] += f * known[i - 1]
        # Back-substitution pass: solve for the last cell T_n^{m+1} first, then sweep backward i = n-1 .. 1.
        result[n] = known[n] / center[n]
        for aux in range(1, n):
            # Backward row index i = n - 1, n - 2, ..., 1.
            i = n - aux
            # Solve for T_i^{m+1} = (D_i - C_i * T_{i+1}^{m+1}) / B_i.
            result[i] = (known[i] - right[i] * result[i + 1]) / center[i]
    # Return the new 1-based cell temperature vector T^{m+1}.
    return result


def _heat_flux(config: dict[str, Any], gr: Grid, new: list[float], old: list[float]) -> list[float]:
    """Compute the cross-sectional heat transfer rate $q_i$ (W, positive in the $+x$ direction)
    across all $N+1$ faces (`1..n+1`) at a printed output time.
    """
    # Number of cells N and reference to the temperature configuration dictionary.
    n = gr.n
    t = config["temperature"]
    # Allocate 1-based face heat-rate array `flux` for faces 1..n+1 (in watts).
    flux = _zeros(n + 1)
    # Retrieve left and right boundary condition types.
    left, right = t["left_boundary"], t["right_boundary"]
    # 1. Left boundary face (face 1, x = 0):
    if left == "Temp":
        # Conductive heat rate entering at x = 0 from the imposed temperature `left_temperature_k`:
        # q_1 = -A_{f,1} * (T_1 - T_left) / R''_{f,1}.
        flux[1] = -1 / gr.face_resistance[1] * gr.face_area[1] * (new[1] - float(t["left_temperature_k"]))
    elif left == "HeatFlux":
        # Convective heat rate entering at x = 0 from `left_air_temperature_k`:
        if float(t["left_convection"]) > 0:
            flux[1] = -1 / gr.face_resistance[1] * gr.face_area[1] * (new[1] - float(t["left_air_temperature_k"]))
        # If left-end radiation is enabled, add net radiative heat rate entering face 1 in the +x direction.
        if t["left_radiation"]:
            flux[1] += SIGMA * gr.face_area[1] * (gr.face_absorptivity[1] * float(t["left_surround_k"]) ** 4 - gr.face_emissivity[1] * new[1] ** 4)
    # 2. Right boundary face (face n + 1, x = L):
    if right == "Temp":
        # Conductive heat rate leaving at x = L toward the imposed temperature `right_temperature_k`:
        # q_{n+1} = -A_{f,n+1} * (T_right - T_n) / R''_{f,n+1}.
        flux[n + 1] = -1 / gr.face_resistance[n + 1] * gr.face_area[n + 1] * (float(t["right_temperature_k"]) - new[n])
    elif right == "HeatFlux":
        # Convective heat rate leaving at x = L toward `right_air_temperature_k`:
        if float(t["right_convection"]) > 0:
            flux[n + 1] = -1 / gr.face_resistance[n + 1] * gr.face_area[n + 1] * (float(t["right_air_temperature_k"]) - new[n])
        # If right-end radiation is enabled, add net radiative heat rate leaving face n + 1 in the +x direction.
        if t["right_radiation"]:
            flux[n + 1] -= SIGMA * gr.face_area[n + 1] * (gr.face_absorptivity[n + 1] * float(t["right_surround_k"]) ** 4 - gr.face_emissivity[n + 1] * new[n] ** 4)
    # 3. Interior cell faces i = 2..n:
    for i in range(2, n + 1):
        # Fourier's law across face i: q_i = -A_{f,i} * (T_i - T_{i-1}) / R''_{f,i}.
        flux[i] = -1 / gr.face_resistance[i] * gr.face_area[i] * (new[i] - new[i - 1])
    # 4. Add radiative heat transfer across any interior 1-cell gas gap (cell i, bounded by faces i and i + 1):
    for i in range(2, n):
        if gr.gas_gap[i]:
            # Radiative heat rate q_rad (W) crossing the gas gap from cell i - 1 to cell i + 1.
            q_rad = SIGMA * gr.cell_area[i] * (
                gr.face_emissivity[i] * gr.face_absorptivity[i + 1] * new[i - 1] ** 4
                - gr.face_absorptivity[i] * gr.face_emissivity[i + 1] * new[i + 1] ** 4
            )
            # Include `q_rad` in the total heat flux reported at both bounding faces of the gas gap.
            flux[i] += q_rad
            flux[i + 1] += q_rad
    # Return the 1-based face heat-rate vector `flux` (indices 1..n+1).
    return flux


def simulate(config: dict[str, Any]) -> SimulationResult:
    """Execute the full transient numerical simulation and compute the steady-state analytical comparison."""
    # 1. Build the finite-volume grid, cell geometry, and face resistances.
    gr = build_grid(config)
    # Extract temperature and time-stepping control dictionaries.
    tcfg, controls = config["temperature"], config["controls"]
    # Total number of finite-volume cells N.
    n = gr.n
    # Allocate the 1-based initial temperature vector `new` (indices 1..n).
    new = _zeros(n)
    # Initialize every cell to `default_k` first (used by both `DEFAULT` and unlisted cells in `CELL` mode).
    for i in range(1, n + 1):
        new[i] = float(tcfg["default_k"])
    # If `DEFCELL` uniform initialization is active, overwrite all cells with `initial_uniform_k`.
    if tcfg["initial_type"] == "DEFCELL":
        for i in range(1, n + 1):
            new[i] = float(tcfg["initial_uniform_k"])
    # If `CELL` custom initialization is active, overwrite the specified cells with their individual temperatures.
    elif tcfg["initial_type"] == "CELL":
        for item in tcfg.get("initial_cells", []):
            # 1-based cell index to override.
            cell = int(item["cell"])
            # Validate that the cell index lies within `1..n`.
            if cell < 1 or cell > n:
                raise ValueError(f"Initial cell {cell} is outside 1..{n}")
            # Assign the custom initial temperature (K) to `new[cell]`.
            new[cell] = float(item["value_k"])

    # Initialize the output storage lists with the initial state at t = 0.0 s.
    times = [0.0]  # List of printed time stamps (s)
    temperatures = [new[1 : n + 1].copy()]  # List of 0-based cell temperature rows (K)
    flux_times: list[float] = []  # List of printed heat-flux time stamps (s, for t > 0)
    fluxes: list[list[float]] = []  # List of 0-based face heat-flux rows (W, for t > 0)
    # Current simulation clock `time` (s), next scheduled output time `print_time` (s),
    # integration step `dt` (s), and total simulation `duration` (s).
    time = 0.0
    print_time = float(controls["output_interval_s"])
    dt = float(controls["time_step_s"])
    duration = float(controls["duration_s"])
    # 2. Main time-marching loop: advance by `dt` until `time >= duration`.
    while time < duration:
        # Advance the simulation clock by one time step Δt.
        time += dt
        # Save the previous time step's temperatures in `old`.
        old = new.copy()
        # Compute the new cell temperatures at `time`.
        new = _step(config, gr, old)
        # Whenever `time` reaches or passes the next scheduled output time `print_time`, record the state.
        if time >= print_time:
            # Evaluate cross-sectional heat fluxes across all n + 1 faces.
            flux = _heat_flux(config, gr, new, old)
            # Append the current time and 0-based temperature/flux slices to the printed history.
            times.append(time)
            temperatures.append(new[1 : n + 1].copy())
            flux_times.append(time)
            fluxes.append(flux[1 : n + 2])
            # Schedule the next output print time by adding `output_interval_s`.
            print_time += float(controls["output_interval_s"])

    # 3. Check whether any printed temperature fell below `minimum_k` or exceeded `maximum_k`.
    warnings: list[str] = []
    min_k = float(tcfg.get("minimum_k") or 0.0)
    max_k = float(tcfg.get("maximum_k") or 0.0)
    # Flatten all finite printed temperatures to find the global minimum and maximum over the simulation.
    all_temps = [val for row in temperatures for val in row if math.isfinite(val)]
    if all_temps:
        observed_min = min(all_temps)
        observed_max = max(all_temps)
        # Warn if a positive `minimum_k` threshold was configured and the temperature dropped below it.
        if min_k > 0.0 and observed_min < min_k:
            warnings.append(
                f"Computed temperature reached {observed_min:.4g} K, below Minimum value ({min_k:.4g} K)."
            )
        # Warn if a valid `maximum_k` threshold was configured and the temperature exceeded it.
        if max_k > 0.0 and max_k > min_k and observed_max > max_k:
            warnings.append(
                f"Computed temperature reached {observed_max:.4g} K, above Maximum value ({max_k:.4g} K)."
            )

    # 4. Automatically compute the 1D steady-state analytical temperature and heat-flux profiles
    # from the exact same `config` and `gr` so every 2D chart can overlay the analytical solution.
    an_temps, an_fluxes = evaluate_analytical_from_numerical(config, gr)
    # Return the complete `SimulationResult` object.
    return SimulationResult(
        times,
        temperatures,
        flux_times,
        fluxes,
        gr,
        warnings,
        an_temps,
        an_fluxes,
    )
