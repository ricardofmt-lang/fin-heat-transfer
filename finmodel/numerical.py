"""Corrected numerical 1D fin heat-transfer solver.

Compared with the legacy XLSM ``Module1.Programa`` implementation, this module
fixes the following physical and computational bugs:
1. Uses ``math.pi`` instead of the mistyped ``3.1459`` for cylindrical and
   conical cross-sections, perimeters, and volumes.
2. Uses exact frustum volumes and slant lateral surface areas for pyramidal and
   conical fins instead of arithmetic end-area averages and projected lengths.
3. Removes the spurious ``1 +`` and ``+ 0.0001`` from the boundary convective
   resistance denominators, handling ``h = 0`` cleanly as zero convective flux.
4. Fixes internal gas-gap radiation by using face ``i`` (instead of ``i - 1``),
   dividing by the adjacent cells' thermal capacitances ``rho * cp * V``, and
   including the radiative flux in ``CrossSectionHeatFlux``.
5. Uses ``new[n] ** 4`` consistently for right-boundary radiation heat flux in
   both Explicit and Implicit modes.
6. Fixes local contact resistance grid generation for multiple resistances in a
   domain, resistances in domains after the first, resistances before the final
   cell of a domain (``cell_domain == count``), and resistances near domain
   boundaries without division by zero.
7. Checks computed temperatures against ``minimum_k`` and ``maximum_k`` when
   configured, recording warnings on ``SimulationResult.warnings``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from finmodel.analytical import evaluate_analytical_from_numerical


SIGMA = 5.670373e-8
PI = math.pi


def _zeros(n: int) -> list[float]:
    """A one-based array with entries 1..n."""
    return [0.0] * (n + 1)


@dataclass
class Grid:
    n: int
    dx: list[float]
    xc: list[float]
    xf: list[float]
    conductivity: list[float]
    thermal_capacity: list[float]
    density: list[float]
    heat_generated: list[float]
    fourier_number: list[float]
    local_resistance: list[float]
    face_resistance: list[float]
    face_area: list[float]
    face_emissivity: list[float]
    face_absorptivity: list[float]
    cell_area: list[float]
    cell_perimeter: list[float]
    cell_volume: list[float]
    lateral_emissivity: list[float]
    lateral_absorptivity: list[float]
    gas_gap: list[bool]


@dataclass
class SimulationResult:
    """Printed simulation results, with Python zero-based rows and cells."""

    times_s: list[float]
    temperature_k: list[list[float]]
    heat_flux_times_s: list[float]
    cross_section_heat_flux_w: list[list[float]]
    grid: Grid
    warnings: list[str] = field(default_factory=list)
    analytical_temperature_k: list[float] = field(default_factory=list)
    analytical_heat_flux_w: list[float] = field(default_factory=list)

    @property
    def cell_centers_m(self) -> list[float]:
        return self.grid.xc[1 : self.grid.n + 1]

    @property
    def face_positions_m(self) -> list[float]:
        return self.grid.xf[1 : self.grid.n + 2]

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "times_s": self.times_s,
            "temperature_k": self.temperature_k,
            "heat_flux_times_s": self.heat_flux_times_s,
            "cross_section_heat_flux_w": self.cross_section_heat_flux_w,
            "analytical_temperature_k": self.analytical_temperature_k,
            "analytical_heat_flux_w": self.analytical_heat_flux_w,
            "grid": {
                "n": self.grid.n,
                "dx_m": self.grid.dx[1 : self.grid.n + 1],
                "cell_centers_m": self.cell_centers_m,
                "face_positions_m": self.face_positions_m,
                "local_resistance_m2k_w": self.grid.local_resistance[1 : self.grid.n + 2],
                "face_total_resistance_m2k_w": self.grid.face_resistance[1 : self.grid.n + 2],
                "fourier_number": self.grid.fourier_number[1 : self.grid.n + 1],
                "face_area_m2": self.grid.face_area[1 : self.grid.n + 2],
                "cell_area_m2": self.grid.cell_area[1 : self.grid.n + 1],
                "cell_perimeter_m": self.grid.cell_perimeter[1 : self.grid.n + 1],
                "cell_volume_m3": self.grid.cell_volume[1 : self.grid.n + 1],
            },
        }
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        return payload


def _validate(config: dict[str, Any]) -> None:
    c = config["controls"]
    if c["method"] not in {"imp", "IMP", "Imp", "exp", "EXP", "Exp"}:
        raise ValueError("Time discretisation must be imp or exp")
    if c["time_step_s"] <= 0:
        raise ValueError("Time step must be positive so the time loop can finish")
    if c["output_interval_s"] == 0:
        raise ValueError("Output interval cannot be zero")
    domains = config["domains"]
    if not domains:
        raise ValueError("At least one active domain is required")
    cells = sum(1 if d.get("gas_gap", False) else int(d["n_cells"]) for d in domains)
    if cells < 1:
        raise ValueError("At least one cell is required")
    if cells == 1 and c["method"].lower() == "exp":
        raise ValueError("The explicit solver requires at least two cells")
    for i, d in enumerate(domains, 1):
        if d["length_mm"] == 0:
            raise ValueError(f"Domain {i} length cannot be zero")
        if not d.get("gas_gap", False) and (d["n_cells"] < 1 or d["n_cells"] != int(d["n_cells"])):
            raise ValueError(f"Domain {i} must have a positive integer cell count")
        for key in ("conductivity", "thermal_capacity", "density"):
            if d[key] == 0:
                raise ValueError(f"Domain {i} {key} cannot be zero")
        prev_distance: float | None = None
        for r in d.get("resistances", []):
            dist = float(r["distance_mm"])
            if not 0 <= dist <= float(d["length_mm"]):
                raise ValueError(f"Domain {i} resistance location is outside its domain")
            if prev_distance is not None and dist <= prev_distance:
                raise ValueError(f"Domain {i} resistances must be in increasing order")
            prev_distance = dist
    t = config["temperature"]
    for key in ("left_boundary", "right_boundary"):
        if t[key] not in {"Temp", "HeatFlux", "NoHeatFlux"}:
            raise ValueError(f"{key} must be Temp, HeatFlux, or NoHeatFlux")
    if t["initial_type"] not in {"DEFAULT", "DEFCELL", "CELL"}:
        raise ValueError("Initial type must be DEFAULT, DEFCELL, or CELL")
    if t["initial_type"] == "CELL" and not t.get("initial_cells"):
        raise ValueError("CELL initialization requires at least one cell temperature")


def build_grid(config: dict[str, Any]) -> Grid:
    """Build the 1D finite-volume grid, cell geometry, and face resistances."""
    _validate(config)
    domains = config["domains"]
    dt = float(config["controls"]["time_step_s"])
    n_each = [1 if d.get("gas_gap", False) else int(d["n_cells"]) for d in domains]
    n = sum(n_each)
    dx, xc, conductivity = _zeros(n), _zeros(n), _zeros(n)
    capacity, density, generated = _zeros(n), _zeros(n), _zeros(n)
    fourier = _zeros(n)
    lateral_e, lateral_a = _zeros(n), _zeros(n)
    gas_gap = [False] * (n + 1)
    xf, face_area, local_r = _zeros(n + 1), _zeros(n + 1), _zeros(n + 1)
    face_e, face_a, face_r = _zeros(n + 1), _zeros(n + 1), _zeros(n + 1)
    cell_area, perimeter, volume = _zeros(n), _zeros(n), _zeros(n)

    face_a[1] = float(domains[0].get("left_absorptivity", domains[0].get("lateral_absorptivity", 0)))
    face_e[1] = float(domains[0].get("left_emissivity", domains[0].get("lateral_emissivity", 0)))
    face_a[n + 1] = float(domains[-1].get("right_absorptivity", domains[-1].get("lateral_absorptivity", 0)))
    face_e[n + 1] = float(domains[-1].get("right_emissivity", domains[-1].get("lateral_emissivity", 0)))

    cell = 0
    for d, count in zip(domains, n_each):
        length = float(d["length_mm"]) / 1000
        for _ in range(count):
            cell += 1
            dx[cell] = length / count
            conductivity[cell] = float(d["conductivity"])
            capacity[cell] = float(d["thermal_capacity"])
            density[cell] = float(d["density"])
            generated[cell] = float(d.get("heat_generated", 0))
            lateral_e[cell] = float(d.get("lateral_emissivity", 0))
            lateral_a[cell] = float(d.get("lateral_absorptivity", 0))
        if d.get("gas_gap", False):
            gas_gap[cell] = True
            face_e[cell] = float(d.get("gas_face_emissivity_left", 0))
            face_e[cell + 1] = float(d.get("gas_face_emissivity_right", 0))
            face_a[cell] = float(d.get("gas_face_absorptivity_left", 0))
            face_a[cell + 1] = float(d.get("gas_face_absorptivity_right", 0))

    if any(d.get("resistances") for d in domains):
        domain_start = 1
        for d, count in zip(domains, n_each):
            length = float(d["length_mm"]) / 1000
            nominal_dx = length / count
            res = d.get("resistances", [])
            if res:
                interior: list[tuple[float, float]] = []
                for r in res:
                    position = float(r["distance_mm"]) / 1000
                    val = float(r["value"])
                    if count == 1:
                        if position <= length / 2:
                            local_r[domain_start] += val
                        else:
                            local_r[domain_start + 1] += val
                    elif position < nominal_dx / 2:
                        local_r[domain_start] += val
                    elif length - position < nominal_dx / 2:
                        local_r[domain_start + count] += val
                    else:
                        interior.append((position, val))

                if interior:
                    # Group interior resistances that fall within half a cell of each other.
                    grouped: list[list[ float ]] = []
                    for position, val in interior:
                        if grouped and position - grouped[-1][0] < nominal_dx / 2:
                            grouped[-1][1] += val
                        else:
                            grouped.append([position, val])

                    cell = domain_start
                    cell_domain = 1
                    aux_x = 0.0
                    num_groups = len(grouped)
                    for g_idx, (position, val) in enumerate(grouped):
                        remaining_groups_after = num_groups - 1 - g_idx
                        max_cells_here = (count - 1 - remaining_groups_after) - (cell_domain - 1)
                        if max_cells_here < 1:
                            # More distinct resistance groups than available interior faces;
                            # attach to the current face.
                            local_r[cell] += val
                            continue
                        n_real = (position - aux_x) / nominal_dx
                        n_int = int(n_real // 1)
                        n_aux = n_int + 1 if n_real - n_int > 0.5 else n_int
                        n_aux = max(1, min(n_aux, max_cells_here))
                        aux_dx1 = (position - aux_x) / n_aux
                        for _ in range(n_aux):
                            dx[cell] = aux_dx1
                            cell += 1
                            cell_domain += 1
                        local_r[cell] += val
                        aux_x = position

                    if aux_x > 0.0 and cell_domain <= count:
                        aux_dx = (length - aux_x) / (count - (cell_domain - 1))
                        for _ in range(cell_domain, count + 1):
                            dx[cell] = aux_dx
                            cell += 1
                            cell_domain += 1
            domain_start += count

    xc[1] = dx[1] / 2
    xf[1] = 0.0
    for i in range(2, n + 1):
        xc[i] = xc[i - 1] + 0.5 * (dx[i - 1] + dx[i])
        xf[i] = xf[i - 1] + dx[i - 1]
    xf[n + 1] = xf[n] + dx[n]
    cell = 0
    for d, count in zip(domains, n_each):
        diffusivity = float(d["conductivity"]) / float(d["density"]) / float(d["thermal_capacity"])
        for _ in range(count):
            cell += 1
            fourier[cell] = dt * diffusivity / dx[cell] ** 2

    g = config["geometry"]
    shape = g["shape"]
    total_length = sum(float(d["length_mm"]) / 1000 for d in domains)
    if shape == "Rectangular":
        width, thick = float(g["width_mm"]) / 1000, float(g["thickness_mm"]) / 1000
        for i in range(1, n + 1):
            face_area[i] = width * thick
            perimeter[i] = 2 * (width + thick)
            volume[i] = face_area[i] * dx[i]
        face_area[n + 1] = width * thick
    elif shape == "Cylindrical":
        diameter = float(g["diameter_mm"]) / 1000
        for i in range(1, n + 1):
            face_area[i] = PI * diameter**2 / 4
            perimeter[i] = PI * diameter
            volume[i] = face_area[i] * dx[i]
        face_area[n + 1] = PI * diameter**2 / 4
    elif shape == "Pyramidal":
        width = float(g["width_base_mm"]) / 1000
        thick = float(g["thickness_base_mm"]) / 1000
        slope_w = (width - float(g["width_top_mm"]) / 1000) / total_length
        slope_t = (thick - float(g["thickness_top_mm"]) / 1000) / total_length
        slant_w = math.sqrt(1.0 + (0.5 * slope_w) ** 2)
        slant_t = math.sqrt(1.0 + (0.5 * slope_t) ** 2)
        face_area[1] = width * thick
        for i in range(1, n + 1):
            width_1 = width - slope_w * dx[i]
            thick_1 = thick - slope_t * dx[i]
            face_area[i + 1] = width_1 * thick_1
            perimeter[i] = (width + width_1) * slant_t + (thick + thick_1) * slant_w
            volume[i] = (dx[i] / 6.0) * (
                2.0 * width * thick + width * thick_1 + width_1 * thick + 2.0 * width_1 * thick_1
            )
            width, thick = width_1, thick_1
    elif shape == "Conical":
        diameter = float(g["diameter_base_mm"]) / 1000
        slope = (diameter - float(g["diameter_top_mm"]) / 1000) / total_length
        slant = math.sqrt(1.0 + (0.5 * slope) ** 2)
        p = PI * diameter
        face_area[1] = PI * diameter**2 / 4
        for i in range(1, n + 1):
            diameter_1 = diameter - slope * dx[i]
            p_1 = PI * diameter_1
            face_area[i + 1] = PI * diameter_1**2 / 4
            perimeter[i] = 0.5 * (p + p_1) * slant
            volume[i] = (dx[i] / 3.0) * (
                face_area[i] + math.sqrt(max(0.0, face_area[i] * face_area[i + 1])) + face_area[i + 1]
            )
            diameter, p = diameter_1, p_1
    else:
        raise ValueError(f"Unknown fin shape: {shape}")
    for i in range(1, n + 1):
        cell_area[i] = 0.5 * (face_area[i] + face_area[i + 1])

    t = config["temperature"]
    face_r[1] = local_r[1] + dx[1] / 2 / conductivity[1]
    if t["left_boundary"] == "HeatFlux":
        h_left = float(t["left_convection"])
        if h_left < 0:
            raise ValueError("Left convection coefficient cannot be negative")
        if h_left > 0:
            face_r[1] += 1.0 / h_left
    face_r[n + 1] = local_r[n + 1] + dx[n] / 2 / conductivity[n]
    if t["right_boundary"] == "HeatFlux":
        h_right = float(t["right_convection"])
        if h_right < 0:
            raise ValueError("Right convection coefficient cannot be negative")
        if h_right > 0:
            face_r[n + 1] += 1.0 / h_right
    for i in range(2, n + 1):
        face_r[i] = local_r[i] + dx[i - 1] / 2 / conductivity[i - 1] + dx[i] / 2 / conductivity[i]

    return Grid(n, dx, xc, xf, conductivity, capacity, density, generated,
                fourier, local_r, face_r, face_area, face_e, face_a,
                cell_area, perimeter, volume, lateral_e, lateral_a, gas_gap)


def _transport_coefficients(config: dict[str, Any], gr: Grid, old: list[float]) -> tuple[list[float], list[float], list[float], list[float]]:
    """Compute transport and radiative coefficients for the current time step."""
    n = gr.n
    c, t = config["controls"], config["temperature"]
    dt = float(c["time_step_s"])
    left, center, right, known = _zeros(n), _zeros(n), _zeros(n), _zeros(n)
    for i in range(1, n + 1):
        left[i] = dt * gr.face_area[i] / gr.cell_volume[i] / gr.density[i] / gr.thermal_capacity[i] / gr.face_resistance[i]
        right[i] = dt * gr.face_area[i + 1] / gr.cell_volume[i] / gr.density[i] / gr.thermal_capacity[i] / gr.face_resistance[i + 1]
        center[i] = left[i] + right[i]
        known[i] = dt * gr.heat_generated[i] / gr.density[i] / gr.thermal_capacity[i]

    if t["left_boundary"] == "NoHeatFlux":
        left[1] = 0.0
        center[1] = right[1]
    elif t["left_boundary"] == "HeatFlux":
        if float(t["left_convection"]) > 0:
            known[1] += left[1] * float(t["left_air_temperature_k"])
            left[1] = 0.0
        else:
            left[1] = 0.0
            center[1] = right[1]
    else:
        known[1] += left[1] * float(t["left_temperature_k"])
        left[1] = 0.0

    if t["right_boundary"] == "NoHeatFlux":
        right[n] = 0.0
        center[n] = left[n]
    elif t["right_boundary"] == "HeatFlux":
        if float(t["right_convection"]) > 0:
            known[n] += right[n] * float(t["right_air_temperature_k"])
            right[n] = 0.0
        else:
            right[n] = 0.0
            center[n] = left[n]
    else:
        known[n] += right[n] * float(t["right_temperature_k"])
        right[n] = 0.0

    if t["lateral_flux"]:
        h = float(t["lateral_convection"])
        air = float(t["lateral_air_temperature_k"])
        for i in range(1, n + 1):
            center[i] += dt * gr.cell_perimeter[i] * gr.dx[i] * h / gr.cell_volume[i] / gr.density[i] / gr.thermal_capacity[i]
            known[i] += dt * gr.cell_perimeter[i] * gr.dx[i] * h * air / gr.cell_volume[i] / gr.density[i] / gr.thermal_capacity[i]

    if t["lateral_radiation"]:
        surround4 = float(t["lateral_surround_k"]) ** 4
        for i in range(1, n + 1):
            known[i] += dt * gr.cell_perimeter[i] * gr.dx[i] * SIGMA * gr.lateral_absorptivity[i] * surround4 / gr.cell_volume[i] / gr.thermal_capacity[i] / gr.density[i]
            center[i] += dt * gr.cell_perimeter[i] * gr.dx[i] * SIGMA * gr.lateral_emissivity[i] * old[i] ** 3 / gr.cell_volume[i] / gr.thermal_capacity[i] / gr.density[i]
    if t["left_boundary"] == "HeatFlux" and t["left_radiation"]:
        known[1] += dt * SIGMA * gr.face_area[1] * gr.face_absorptivity[1] * float(t["left_surround_k"]) ** 4 / gr.cell_volume[1] / gr.thermal_capacity[1] / gr.density[1]
        center[1] += dt * SIGMA * gr.face_area[1] * gr.face_emissivity[1] * old[1] ** 3 / gr.cell_volume[1] / gr.thermal_capacity[1] / gr.density[1]
    if t["right_boundary"] == "HeatFlux" and t["right_radiation"]:
        known[n] += dt * SIGMA * gr.face_area[n + 1] * gr.face_absorptivity[n + 1] * float(t["right_surround_k"]) ** 4 / gr.cell_volume[n] / gr.thermal_capacity[n] / gr.density[n]
        center[n] += dt * SIGMA * gr.face_area[n + 1] * gr.face_emissivity[n + 1] * old[n] ** 3 / gr.cell_volume[n] / gr.thermal_capacity[n] / gr.density[n]
    for i in range(2, n):
        if gr.gas_gap[i]:
            q_rad = SIGMA * gr.cell_area[i] * (
                gr.face_emissivity[i] * gr.face_absorptivity[i + 1] * old[i - 1] ** 4
                - gr.face_absorptivity[i] * gr.face_emissivity[i + 1] * old[i + 1] ** 4
            )
            known[i - 1] -= dt * q_rad / gr.cell_volume[i - 1] / gr.thermal_capacity[i - 1] / gr.density[i - 1]
            known[i + 1] += dt * q_rad / gr.cell_volume[i + 1] / gr.thermal_capacity[i + 1] / gr.density[i + 1]
    return left, center, right, known


def _step(config: dict[str, Any], gr: Grid, old: list[float]) -> list[float]:
    left, center, right, known = _transport_coefficients(config, gr, old)
    n = gr.n
    result = _zeros(n)
    method = config["controls"]["method"].lower()
    if method == "exp":
        for i in range(1, n + 1):
            center[i] = 1 - center[i]
        result[1] = known[1] + center[1] * old[1] + right[1] * old[2]
        for i in range(2, n):
            result[i] = known[i] + left[i] * old[i - 1] + center[i] * old[i] + right[i] * old[i + 1]
        result[n] = known[n] + left[n] * old[n - 1] + center[n] * old[n]
    else:
        for i in range(1, n + 1):
            known[i] += old[i]
            left[i] = -left[i]
            center[i] = 1 + center[i]
            right[i] = -right[i]
        for i in range(2, n + 1):
            f = -left[i] / center[i - 1]
            left[i] = 0.0
            center[i] += f * right[i - 1]
            known[i] += f * known[i - 1]
        result[n] = known[n] / center[n]
        for aux in range(1, n):
            i = n - aux
            result[i] = (known[i] - right[i] * result[i + 1]) / center[i]
    return result


def _heat_flux(config: dict[str, Any], gr: Grid, new: list[float], old: list[float]) -> list[float]:
    """Compute cross-sectional heat fluxes after each printed step."""
    n = gr.n
    t = config["temperature"]
    flux = _zeros(n + 1)
    left, right = t["left_boundary"], t["right_boundary"]
    if left == "Temp":
        flux[1] = -1 / gr.face_resistance[1] * gr.face_area[1] * (new[1] - float(t["left_temperature_k"]))
    elif left == "HeatFlux":
        if float(t["left_convection"]) > 0:
            flux[1] = -1 / gr.face_resistance[1] * gr.face_area[1] * (new[1] - float(t["left_air_temperature_k"]))
        if t["left_radiation"]:
            flux[1] += SIGMA * gr.face_area[1] * (gr.face_absorptivity[1] * float(t["left_surround_k"]) ** 4 - gr.face_emissivity[1] * new[1] ** 4)
    if right == "Temp":
        flux[n + 1] = -1 / gr.face_resistance[n + 1] * gr.face_area[n + 1] * (float(t["right_temperature_k"]) - new[n])
    elif right == "HeatFlux":
        if float(t["right_convection"]) > 0:
            flux[n + 1] = -1 / gr.face_resistance[n + 1] * gr.face_area[n + 1] * (float(t["right_air_temperature_k"]) - new[n])
        if t["right_radiation"]:
            flux[n + 1] -= SIGMA * gr.face_area[n + 1] * (gr.face_absorptivity[n + 1] * float(t["right_surround_k"]) ** 4 - gr.face_emissivity[n + 1] * new[n] ** 4)
    for i in range(2, n + 1):
        flux[i] = -1 / gr.face_resistance[i] * gr.face_area[i] * (new[i] - new[i - 1])
    for i in range(2, n):
        if gr.gas_gap[i]:
            q_rad = SIGMA * gr.cell_area[i] * (
                gr.face_emissivity[i] * gr.face_absorptivity[i + 1] * new[i - 1] ** 4
                - gr.face_absorptivity[i] * gr.face_emissivity[i + 1] * new[i + 1] ** 4
            )
            flux[i] += q_rad
            flux[i + 1] += q_rad
    return flux


def simulate(config: dict[str, Any]) -> SimulationResult:
    """Run the numerical time loop and retain the printed output steps."""
    gr = build_grid(config)
    tcfg, controls = config["temperature"], config["controls"]
    n = gr.n
    new = _zeros(n)
    for i in range(1, n + 1):
        new[i] = float(tcfg["default_k"])
    if tcfg["initial_type"] == "DEFCELL":
        for i in range(1, n + 1):
            new[i] = float(tcfg["initial_uniform_k"])
    elif tcfg["initial_type"] == "CELL":
        for item in tcfg.get("initial_cells", []):
            cell = int(item["cell"])
            if cell < 1 or cell > n:
                raise ValueError(f"Initial cell {cell} is outside 1..{n}")
            new[cell] = float(item["value_k"])

    times = [0.0]
    temperatures = [new[1 : n + 1].copy()]
    flux_times: list[float] = []
    fluxes: list[list[float]] = []
    time = 0.0
    print_time = float(controls["output_interval_s"])
    dt = float(controls["time_step_s"])
    duration = float(controls["duration_s"])
    while time < duration:
        time += dt
        old = new.copy()
        new = _step(config, gr, old)
        if time >= print_time:
            flux = _heat_flux(config, gr, new, old)
            times.append(time)
            temperatures.append(new[1 : n + 1].copy())
            flux_times.append(time)
            fluxes.append(flux[1 : n + 2])
            print_time += float(controls["output_interval_s"])

    warnings: list[str] = []
    min_k = float(tcfg.get("minimum_k") or 0.0)
    max_k = float(tcfg.get("maximum_k") or 0.0)
    all_temps = [val for row in temperatures for val in row if math.isfinite(val)]
    if all_temps:
        observed_min = min(all_temps)
        observed_max = max(all_temps)
        if min_k > 0.0 and observed_min < min_k:
            warnings.append(
                f"Computed temperature reached {observed_min:.4g} K, below Minimum value ({min_k:.4g} K)."
            )
        if max_k > 0.0 and max_k > min_k and observed_max > max_k:
            warnings.append(
                f"Computed temperature reached {observed_max:.4g} K, above Maximum value ({max_k:.4g} K)."
            )

    an_temps, an_fluxes = evaluate_analytical_from_numerical(config, gr)
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
