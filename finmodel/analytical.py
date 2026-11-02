"""Corrected analytical fin heat-transfer solver.

Compared with the legacy ``Fin - Analytical solution.xlsx`` sheet, this module
fixes two workbook bugs:
1. The base heat-flux parameter is ``M = theta_b * sqrt(h * P * k * A_c)`` with
   ``theta_b = T_b - T_inf`` outside the square root (fixing units, magnitude,
   and cooling-fin cases where ``theta_b < 0``).
2. By default, ``evaluate_analytical`` evaluates ``round(n_cells)`` cell-center
   points spanning the full fin length ``[0, L]`` rather than hardcoding 20
   columns regardless of ``n_cells``.

It also provides ``evaluate_analytical_from_numerical`` to compute the 1D
steady-state analytical temperature profile (at cell centers) and conductive
heat-flux profile (at cell faces) directly from a numerical simulation's
configuration and grid, requiring no additional user inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cosh, exp, sinh, sqrt
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree
from zipfile import ZipFile

if TYPE_CHECKING:
    from finmodel.numerical import Grid


@dataclass(frozen=True)
class AnalyticalInputs:
    """The nine numeric inputs in ``Sheet1`` of the analytical workbook."""

    width_mm: float  # C4
    thickness_mm: float  # C5
    length_mm: float  # C6
    n_cells: float  # C7
    conductivity_w_m_k: float  # C9
    convection_coefficient_w_m2_k: float  # C10
    base_temperature_k: float  # C12
    air_temperature_k: float  # C13
    top_temperature_k: float  # C14


@dataclass(frozen=True)
class AnalyticalResults:
    """All calculated scalars and profile rows of the analytical solution."""

    width_m: float  # E4
    thickness_m: float  # E5
    length_m: float  # E6
    area_mm2: float  # C15
    area_m2: float  # E15
    perimeter_mm: float  # C16
    perimeter_m: float  # E16
    theta_top_k: float  # C17
    theta_base_k: float  # C18
    m_per_m: float  # C19
    cosh_m_length: float  # C21
    sinh_m_length: float  # C22
    h_over_mk: float  # C23
    x_m: tuple[float, ...]
    x_over_length: tuple[float, ...]
    cosh_m_length_minus_x: tuple[float, ...]
    sinh_m_length_minus_x: tuple[float, ...]
    sinh_m_x: tuple[float, ...]
    infinite_temperature_k: tuple[float, ...]
    convective_tip_temperature_k: tuple[float, ...]
    insulated_tip_temperature_k: tuple[float, ...]
    imposed_tip_temperature_k: tuple[float, ...]
    infinite_base_heat_flux_w: float
    convective_tip_base_heat_flux_w: float
    insulated_tip_base_heat_flux_w: float
    imposed_tip_base_heat_flux_w: float


def evaluate_analytical(
    inputs: AnalyticalInputs, *, point_count: int | None = None
) -> AnalyticalResults:
    """Evaluate the corrected 1D steady-state analytical fin formulas.

    When ``point_count`` is omitted, ``round(inputs.n_cells)`` cell-center
    positions spanning ``(0, L)`` are evaluated.
    """

    if inputs.width_mm <= 0 or inputs.thickness_mm <= 0 or inputs.length_mm <= 0:
        raise ValueError("Fin width, thickness, and length must be positive")
    if inputs.n_cells < 1:
        raise ValueError("n_cells must be at least one")
    if inputs.conductivity_w_m_k <= 0:
        raise ValueError("Conductivity must be positive")
    if inputs.convection_coefficient_w_m2_k <= 0:
        raise ValueError("Convection coefficient must be positive")

    if point_count is None:
        point_count = max(1, round(inputs.n_cells))
    if point_count < 1:
        raise ValueError("point_count must be at least one")

    width_m = inputs.width_mm / 1000  # E4
    thickness_m = inputs.thickness_mm / 1000  # E5
    length_m = inputs.length_mm / 1000  # E6
    area_mm2 = inputs.width_mm * inputs.thickness_mm  # C15
    area_m2 = width_m * thickness_m  # E15
    perimeter_mm = 2 * (inputs.width_mm + inputs.thickness_mm)  # C16
    perimeter_m = 2 * (width_m + thickness_m)  # E16
    theta_top = inputs.top_temperature_k - inputs.air_temperature_k  # C17
    theta_base = inputs.base_temperature_k - inputs.air_temperature_k  # C18
    m = sqrt(
        inputs.convection_coefficient_w_m2_k
        * perimeter_m
        / inputs.conductivity_w_m_k
        / area_m2
    )  # C19
    cosh_m_length = cosh(m * length_m)  # C21
    sinh_m_length = sinh(m * length_m)  # C22
    h_over_mk = inputs.convection_coefficient_w_m2_k / m / inputs.conductivity_w_m_k  # C23

    first_x = length_m / inputs.n_cells / 2
    step = length_m / inputs.n_cells
    x_values = [first_x]
    for _ in range(1, point_count):
        x_values.append(x_values[-1] + step)

    x_over_length = tuple(x / length_m for x in x_values)
    cosh_l_minus_x = tuple(cosh(m * (length_m - x)) for x in x_values)
    sinh_l_minus_x = tuple(sinh(m * (length_m - x)) for x in x_values)
    sinh_m_x = tuple(sinh(m * x) for x in x_values)

    infinite_temperature = tuple(
        inputs.air_temperature_k + theta_base * exp(-m * x) for x in x_values
    )
    convective_tip_temperature = tuple(
        inputs.air_temperature_k
        + theta_base
        * (cosh_l_minus_x[i] + h_over_mk * sinh_l_minus_x[i])
        / (cosh_m_length + h_over_mk * sinh_m_length)
        for i in range(point_count)
    )
    insulated_tip_temperature = tuple(
        inputs.air_temperature_k + theta_base * value / cosh_m_length
        for value in cosh_l_minus_x
    )
    imposed_tip_temperature = tuple(
        inputs.air_temperature_k
        + (theta_top * sinh_m_x[i] + theta_base * sinh_l_minus_x[i])
        / sinh_m_length
        for i in range(point_count)
    )

    flux_scale = sqrt(
        inputs.convection_coefficient_w_m2_k
        * perimeter_m
        * inputs.conductivity_w_m_k
        * area_m2
    )
    infinite_base_flux = theta_base * flux_scale
    convective_tip_base_flux = (
        infinite_base_flux
        * (sinh_m_length + h_over_mk * cosh_m_length)
        / (cosh_m_length + h_over_mk * sinh_m_length)
    )
    insulated_tip_base_flux = infinite_base_flux * sinh_m_length / cosh_m_length
    imposed_tip_base_flux = (
        flux_scale
        * (theta_base * cosh_m_length - theta_top)
        / sinh_m_length
    )

    return AnalyticalResults(
        width_m=width_m,
        thickness_m=thickness_m,
        length_m=length_m,
        area_mm2=area_mm2,
        area_m2=area_m2,
        perimeter_mm=perimeter_mm,
        perimeter_m=perimeter_m,
        theta_top_k=theta_top,
        theta_base_k=theta_base,
        m_per_m=m,
        cosh_m_length=cosh_m_length,
        sinh_m_length=sinh_m_length,
        h_over_mk=h_over_mk,
        x_m=tuple(x_values),
        x_over_length=x_over_length,
        cosh_m_length_minus_x=cosh_l_minus_x,
        sinh_m_length_minus_x=sinh_l_minus_x,
        sinh_m_x=sinh_m_x,
        infinite_temperature_k=infinite_temperature,
        convective_tip_temperature_k=convective_tip_temperature,
        insulated_tip_temperature_k=insulated_tip_temperature,
        imposed_tip_temperature_k=imposed_tip_temperature,
        infinite_base_heat_flux_w=infinite_base_flux,
        convective_tip_base_heat_flux_w=convective_tip_base_flux,
        insulated_tip_base_heat_flux_w=insulated_tip_base_flux,
        imposed_tip_base_heat_flux_w=imposed_tip_base_flux,
    )


def evaluate_analytical_from_numerical(
    config: dict[str, Any], grid: Grid
) -> tuple[list[float], list[float]]:
    """Compute 1D steady-state analytical temperatures (at cell centers) and
    cross-sectional heat fluxes (at cell faces) from a numerical config & grid.
    """
    n = grid.n
    x_cells = grid.xc[1 : n + 1]
    x_faces = grid.xf[1 : n + 2]
    total_length = x_faces[-1]
    tcfg = config["temperature"]
    default_temp = float(tcfg.get("default_k") or 300.0)

    if total_length <= 0.0:
        return [default_temp] * n, [0.0] * (n + 1)

    area_c = sum(grid.cell_area[i] * grid.dx[i] for i in range(1, n + 1)) / total_length
    perimeter = sum(grid.cell_perimeter[i] * grid.dx[i] for i in range(1, n + 1)) / total_length
    if area_c <= 0.0:
        return [default_temp] * n, [0.0] * (n + 1)

    cond_resistance = sum(
        grid.dx[i] / grid.conductivity[i] for i in range(1, n + 1)
    ) + sum(grid.local_resistance[i] for i in range(2, n + 1))
    k_eff = total_length / cond_resistance if cond_resistance > 0.0 else float(grid.conductivity[1])

    has_lat = bool(tcfg.get("lateral_flux")) and float(tcfg.get("lateral_convection") or 0.0) > 0.0
    h_lat = float(tcfg["lateral_convection"]) if has_lat else 0.0
    t_inf = float(tcfg["lateral_air_temperature_k"]) if has_lat else default_temp
    q_gen = sum(grid.heat_generated[i] * grid.dx[i] for i in range(1, n + 1)) / total_length

    # Boundary condition coefficients:
    # Left (x = 0): alpha_L * T(0) + beta_L * q(0) = gamma_L
    left_type = tcfg["left_boundary"]
    r_left_local = grid.local_resistance[1]
    if left_type == "Temp":
        alpha_l, beta_l, gamma_l = 1.0, r_left_local / area_c, float(tcfg["left_temperature_k"])
    elif left_type == "HeatFlux" and float(tcfg.get("left_convection") or 0.0) > 0.0:
        h_left = float(tcfg["left_convection"])
        alpha_l, beta_l, gamma_l = 1.0, (r_left_local + 1.0 / h_left) / area_c, float(tcfg["left_air_temperature_k"])
    else:
        alpha_l, beta_l, gamma_l = 0.0, 1.0, 0.0

    # Right (x = L): alpha_R * T(L) + beta_R * q(L) = gamma_R
    right_type = tcfg["right_boundary"]
    r_right_local = grid.local_resistance[n + 1]
    if right_type == "Temp":
        alpha_r, beta_r, gamma_r = 1.0, -r_right_local / area_c, float(tcfg["right_temperature_k"])
    elif right_type == "HeatFlux" and float(tcfg.get("right_convection") or 0.0) > 0.0:
        h_right = float(tcfg["right_convection"])
        alpha_r, beta_r, gamma_r = 1.0, -(r_right_local + 1.0 / h_right) / area_c, float(tcfg["right_air_temperature_k"])
    else:
        alpha_r, beta_r, gamma_r = 0.0, 1.0, 0.0

    if h_lat > 0.0 and perimeter > 0.0:
        m = sqrt(h_lat * perimeter / (k_eff * area_c))
        mk_a = m * k_eff * area_c
        t_particular = t_inf + (q_gen * area_c) / (h_lat * perimeter)
        denom_scale = 1.0 + exp(-2.0 * m * total_length)

        def phi1(x: float) -> float:
            return exp(-m * x) * (1.0 + exp(-2.0 * m * (total_length - x))) / denom_scale

        def phi2(x: float) -> float:
            return exp(-m * (total_length - x)) * (1.0 - exp(-2.0 * m * x)) / denom_scale

        def psi1(x: float) -> float:
            return exp(-m * x) * (1.0 - exp(-2.0 * m * (total_length - x))) / denom_scale

        def psi2(x: float) -> float:
            return -exp(-m * (total_length - x)) * (1.0 + exp(-2.0 * m * x)) / denom_scale

        a11 = alpha_l * phi1(0.0) + beta_l * mk_a * psi1(0.0)
        a12 = alpha_l * phi2(0.0) + beta_l * mk_a * psi2(0.0)
        a21 = alpha_r * phi1(total_length) + beta_r * mk_a * psi1(total_length)
        a22 = alpha_r * phi2(total_length) + beta_r * mk_a * psi2(total_length)
        rhs1 = gamma_l - alpha_l * t_particular
        rhs2 = gamma_r - alpha_r * t_particular

        det = a11 * a22 - a12 * a21
        if abs(det) < 1e-30:
            c1, c2 = 0.0, 0.0
        else:
            c1 = (rhs1 * a22 - rhs2 * a12) / det
            c2 = (a11 * rhs2 - a21 * rhs1) / det

        temps = [t_particular + c1 * phi1(x) + c2 * phi2(x) for x in x_cells]
        fluxes = [mk_a * (c1 * psi1(x) + c2 * psi2(x)) for x in x_faces]
        return temps, fluxes

    # Pure 1D conduction (h_lat == 0): T(x) = -(q_gen / (2*k_eff)) * x^2 + C1 + C2 * x
    a11 = alpha_l
    a12 = -beta_l * k_eff * area_c
    a21 = alpha_r
    a22 = alpha_r * total_length - beta_r * k_eff * area_c
    rhs1 = gamma_l
    rhs2 = (
        gamma_r
        + alpha_r * (q_gen * total_length**2) / (2.0 * k_eff)
        - beta_r * q_gen * area_c * total_length
    )
    det = a11 * a22 - a12 * a21
    if abs(det) < 1e-30:
        c1, c2 = default_temp, 0.0
    else:
        c1 = (rhs1 * a22 - rhs2 * a12) / det
        c2 = (a11 * rhs2 - a21 * rhs1) / det

    temps = [-(q_gen / (2.0 * k_eff)) * x**2 + c1 + c2 * x for x in x_cells]
    fluxes = [q_gen * area_c * x - k_eff * area_c * c2 for x in x_faces]
    return temps, fluxes


def read_worksheet_cached_values(path: str | Path) -> dict[str, float]:
    """Read numeric values, including formula caches, from Sheet1 via OOXML."""

    with ZipFile(path) as workbook:
        sheet_xml = workbook.read("xl/worksheets/sheet1.xml")
    root = ElementTree.fromstring(sheet_xml)
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    values: dict[str, float] = {}
    for cell in root.iter(f"{namespace}c"):
        value = cell.find(f"{namespace}v")
        if value is not None and value.text is not None and cell.attrib.get("t") != "e":
            try:
                values[cell.attrib["r"]] = float(value.text)
            except ValueError:
                pass  # Ignore text cells; all model inputs and results are numeric.
    return values


def read_analytical_workbook_inputs(path: str | Path) -> AnalyticalInputs:
    """Load the input values directly from the supplied analytical workbook."""

    cells = read_worksheet_cached_values(path)
    return AnalyticalInputs(
        width_mm=cells["C4"],
        thickness_mm=cells["C5"],
        length_mm=cells["C6"],
        n_cells=cells["C7"],
        conductivity_w_m_k=cells["C9"],
        convection_coefficient_w_m2_k=cells["C10"],
        base_temperature_k=cells["C12"],
        air_temperature_k=cells["C13"],
        top_temperature_k=cells["C14"],
    )
