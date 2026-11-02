"""Steady-state 1D analytical fin heat-transfer solver.

Pedagogical Overview
--------------------
A 1D extended surface (fin) of cross-sectional area $A_c$, perimeter $P$, and
effective thermal conductivity $k$ losing heat by convection (coefficient $h$)
to surrounding fluid at temperature $T_\\infty$ is governed at steady state by
the second-order linear ordinary differential equation (ODE):

    $-k A_c \\frac{d^2 T}{dx^2} + h P (T(x) - T_\\infty) = \\dot{q}_{\\text{gen}} A_c$

Defining the excess temperature $\\theta(x) = T(x) - T_\\infty$ and the fin
parameter $m = \\sqrt{\\frac{h P}{k A_c}}$ ($\\text{m}^{-1}$), the homogeneous
equation ($\\dot{q}_{\\text{gen}} = 0$) becomes:

    $\\frac{d^2 \\theta}{dx^2} - m^2 \\theta = 0$

When the fin base ($x = 0$) is held at $T(0) = T_b$ ($\\theta_b = T_b - T_\\infty$),
the four classic textbook boundary conditions at the fin tip ($x = L$) yield:
1. **Infinite fin** ($\\theta(x \\to \\infty) = 0$):
   $\\theta(x) = \\theta_b e^{-m x}$,  $q_b = M = \\theta_b \\sqrt{h P k A_c}$
2. **Convective tip** ($-k \\theta'(L) = h \\theta(L)$):
   $\\theta(x) = \\theta_b \\frac{\\cosh(m(L-x)) + (h/mk)\\sinh(m(L-x))}{\\cosh(mL) + (h/mk)\\sinh(mL)}$
3. **Insulated (adiabatic) tip** ($\\theta'(L) = 0$):
   $\\theta(x) = \\theta_b \\frac{\\cosh(m(L-x))}{\\cosh(mL)}$,  $q_b = M \\tanh(mL)$
4. **Imposed tip temperature** ($\\theta(L) = \\theta_L = T_L - T_\\infty$):
   $\\theta(x) = \\frac{\\theta_L \\sinh(mx) + \\theta_b \\sinh(m(L-x))}{\\sinh(mL)}$

In addition to `evaluate_analytical` (which evaluates all four classic textbook
profiles above), this module provides `evaluate_analytical_from_numerical`,
which automatically solves the general 2-point boundary-value problem directly
from a numerical simulation's configuration and grid (supporting arbitrary
left/right boundary combinations, volumetric heat generation $\\dot{q}_{\\text{gen}}$,
and pure 1D conduction when $h = 0$).
"""

from __future__ import annotations  # Postpone evaluation of type annotations

from dataclasses import dataclass  # Used to define immutable input/output containers
from math import cosh, exp, sinh, sqrt  # Hyperbolic, exponential, and square-root functions for exact ODE solutions
from pathlib import Path  # Cross-platform filesystem path handling
from typing import TYPE_CHECKING, Any  # Type annotations
from xml.etree import ElementTree  # XML parser for reading cached values from `.xlsx` files
from zipfile import ZipFile  # `.xlsx` workbooks are ZIP archives containing XML worksheets

# Import `Grid` only during static type checking to avoid a circular runtime import with `finmodel.numerical`.
if TYPE_CHECKING:
    from finmodel.numerical import Grid  # Finite-volume mesh structure


@dataclass(frozen=True)
class AnalyticalInputs:
    """Container for the nine scalar inputs on `Sheet1` of `Fin - Analytical solution.xlsx`."""

    width_mm: float  # Fin cross-sectional width W in millimetres (Excel cell C4)
    thickness_mm: float  # Fin cross-sectional thickness t in millimetres (Excel cell C5)
    length_mm: float  # Total axial fin length L in millimetres (Excel cell C6)
    n_cells: float  # Number of axial subdivisions used for evaluation spacing (Excel cell C7)
    conductivity_w_m_k: float  # Thermal conductivity k in W/(m·K) (Excel cell C9)
    convection_coefficient_w_m2_k: float  # Surface convection coefficient h in W/(m²·K) (Excel cell C10)
    base_temperature_k: float  # Imposed base temperature T_b at x = 0 in kelvin (Excel cell C12)
    air_temperature_k: float  # Ambient fluid temperature T_∞ in kelvin (Excel cell C13)
    top_temperature_k: float  # Imposed tip temperature T_L at x = L in kelvin (Excel cell C14)


@dataclass(frozen=True)
class AnalyticalResults:
    """Container for all derived geometric parameters, temperature profiles, and base heat fluxes."""

    width_m: float  # Fin width W converted to metres (Excel cell E4)
    thickness_m: float  # Fin thickness t converted to metres (Excel cell E5)
    length_m: float  # Fin length L converted to metres (Excel cell E6)
    area_mm2: float  # Cross-sectional area A_c = W * t in mm² (Excel cell C15)
    area_m2: float  # Cross-sectional area A_c = W * t in m² (Excel cell E15)
    perimeter_mm: float  # Cross-sectional perimeter P = 2*(W + t) in mm (Excel cell C16)
    perimeter_m: float  # Cross-sectional perimeter P = 2*(W + t) in m (Excel cell E16)
    theta_top_k: float  # Tip excess temperature θ_L = T_L - T_∞ in K (Excel cell C17)
    theta_base_k: float  # Base excess temperature θ_b = T_b - T_∞ in K (Excel cell C18)
    m_per_m: float  # Fin parameter m = sqrt(h * P / (k * A_c)) in m⁻¹ (Excel cell C19)
    cosh_m_length: float  # Dimensionless value cosh(m * L) (Excel cell C21)
    sinh_m_length: float  # Dimensionless value sinh(m * L) (Excel cell C22)
    h_over_mk: float  # Dimensionless Biot-like tip ratio h / (m * k) (Excel cell C23)
    x_m: tuple[float, ...]  # Cell-center axial coordinates x_i (m) along the fin
    x_over_length: tuple[float, ...]  # Dimensionless axial coordinates x_i / L
    cosh_m_length_minus_x: tuple[float, ...]  # Helper row cosh(m * (L - x_i))
    sinh_m_length_minus_x: tuple[float, ...]  # Helper row sinh(m * (L - x_i))
    sinh_m_x: tuple[float, ...]  # Helper row sinh(m * x_i)
    infinite_temperature_k: tuple[float, ...]  # Temperature profile T(x_i) for an infinitely long fin (K)
    convective_tip_temperature_k: tuple[float, ...]  # Temperature profile T(x_i) for a convective tip (K)
    insulated_tip_temperature_k: tuple[float, ...]  # Temperature profile T(x_i) for an adiabatic/insulated tip (K)
    imposed_tip_temperature_k: tuple[float, ...]  # Temperature profile T(x_i) for an imposed tip temperature (K)
    infinite_base_heat_flux_w: float  # Total heat transfer rate q_b (W) entering at x = 0 for an infinite fin
    convective_tip_base_heat_flux_w: float  # Total heat transfer rate q_b (W) at x = 0 for a convective tip
    insulated_tip_base_heat_flux_w: float  # Total heat transfer rate q_b (W) at x = 0 for an insulated tip
    imposed_tip_base_heat_flux_w: float  # Total heat transfer rate q_b (W) at x = 0 for an imposed tip temperature


def evaluate_analytical(
    inputs: AnalyticalInputs, *, point_count: int | None = None
) -> AnalyticalResults:
    """Evaluate the four classical 1D steady-state analytical fin solutions.

    By default (`point_count=None`), temperatures are evaluated at `round(inputs.n_cells)`
    equally spaced cell-center positions $x_i = (i - 1/2)\\Delta x$ across $[0, L]$.
    """
    # Verify that all geometric dimensions (width, thickness, length) are strictly positive.
    if inputs.width_mm <= 0 or inputs.thickness_mm <= 0 or inputs.length_mm <= 0:
        # Reject zero or negative geometry dimensions.
        raise ValueError("Fin width, thickness, and length must be positive")
    # Verify that the number of cells is at least 1 so Δx = L / n_cells is well-defined.
    if inputs.n_cells < 1:
        # Reject cell counts below 1.
        raise ValueError("n_cells must be at least one")
    # Verify that thermal conductivity k > 0 (required in the denominator of m = sqrt(hP / kA_c)).
    if inputs.conductivity_w_m_k <= 0:
        # Reject non-positive thermal conductivity.
        raise ValueError("Conductivity must be positive")
    # Verify that the convection coefficient h > 0 (required for hyperbolic fin formulas).
    if inputs.convection_coefficient_w_m2_k <= 0:
        # Reject non-positive convection coefficient.
        raise ValueError("Convection coefficient must be positive")

    # If the caller did not specify an explicit `point_count`, use `round(inputs.n_cells)`.
    if point_count is None:
        # Round `n_cells` to the nearest integer (at least 1).
        point_count = max(1, round(inputs.n_cells))
    # Ensure `point_count` is a positive integer.
    if point_count < 1:
        # Reject non-positive point counts.
        raise ValueError("point_count must be at least one")

    # Convert width W from millimetres to metres (1 m = 1000 mm).
    width_m = inputs.width_mm / 1000  # Excel cell E4
    # Convert thickness t from millimetres to metres.
    thickness_m = inputs.thickness_mm / 1000  # Excel cell E5
    # Convert total fin length L from millimetres to metres.
    length_m = inputs.length_mm / 1000  # Excel cell E6
    # Compute rectangular cross-sectional area A_c = W * t in mm².
    area_mm2 = inputs.width_mm * inputs.thickness_mm  # Excel cell C15
    # Compute rectangular cross-sectional area A_c = W * t in m².
    area_m2 = width_m * thickness_m  # Excel cell E15
    # Compute rectangular perimeter P = 2 * (W + t) in mm.
    perimeter_mm = 2 * (inputs.width_mm + inputs.thickness_mm)  # Excel cell C16
    # Compute rectangular perimeter P = 2 * (W + t) in m.
    perimeter_m = 2 * (width_m + thickness_m)  # Excel cell E16
    # Compute tip excess temperature θ_L = T_L - T_∞ (K).
    theta_top = inputs.top_temperature_k - inputs.air_temperature_k  # Excel cell C17
    # Compute base excess temperature θ_b = T_b - T_∞ (K).
    theta_base = inputs.base_temperature_k - inputs.air_temperature_k  # Excel cell C18
    # Compute the fin parameter m = sqrt((h * P) / (k * A_c)) in m⁻¹.
    m = sqrt(
        inputs.convection_coefficient_w_m2_k
        * perimeter_m
        / inputs.conductivity_w_m_k
        / area_m2
    )  # Excel cell C19
    # Precompute cosh(m * L) for the tip boundary condition denominators.
    cosh_m_length = cosh(m * length_m)  # Excel cell C21
    # Precompute sinh(m * L) for the tip boundary condition denominators.
    sinh_m_length = sinh(m * length_m)  # Excel cell C22
    # Precompute the dimensionless tip convection parameter h / (m * k).
    h_over_mk = inputs.convection_coefficient_w_m2_k / m / inputs.conductivity_w_m_k  # Excel cell C23

    # Position of the first cell center: half a cell width from the base (x_1 = Δx / 2).
    first_x = length_m / inputs.n_cells / 2
    # Axial spacing between consecutive cell centers: Δx = L / n_cells.
    step = length_m / inputs.n_cells
    # Initialize the list of axial evaluation coordinates with the first cell center x_1.
    x_values = [first_x]
    # Append the remaining cell-center coordinates by repeatedly adding `step` (matching Excel's I2:AB2 formula).
    for _ in range(1, point_count):
        # x_{i} = x_{i-1} + Δx.
        x_values.append(x_values[-1] + step)

    # Compute the dimensionless coordinate x / L at each cell center.
    x_over_length = tuple(x / length_m for x in x_values)
    # Precompute cosh(m * (L - x)) at each cell center.
    cosh_l_minus_x = tuple(cosh(m * (length_m - x)) for x in x_values)
    # Precompute sinh(m * (L - x)) at each cell center.
    sinh_l_minus_x = tuple(sinh(m * (length_m - x)) for x in x_values)
    # Precompute sinh(m * x) at each cell center.
    sinh_m_x = tuple(sinh(m * x) for x in x_values)

    # 1. Infinite-fin temperature distribution: T(x) = T_∞ + θ_b * exp(-m * x).
    infinite_temperature = tuple(
        inputs.air_temperature_k + theta_base * exp(-m * x) for x in x_values
    )
    # 2. Convective-tip temperature distribution:
    #    T(x) = T_∞ + θ_b * [cosh(m(L-x)) + (h/mk)*sinh(m(L-x))] / [cosh(mL) + (h/mk)*sinh(mL)].
    convective_tip_temperature = tuple(
        inputs.air_temperature_k
        + theta_base
        * (cosh_l_minus_x[i] + h_over_mk * sinh_l_minus_x[i])
        / (cosh_m_length + h_over_mk * sinh_m_length)
        for i in range(point_count)
    )
    # 3. Insulated-tip (adiabatic tip) temperature distribution:
    #    T(x) = T_∞ + θ_b * cosh(m(L-x)) / cosh(mL).
    insulated_tip_temperature = tuple(
        inputs.air_temperature_k + theta_base * value / cosh_m_length
        for value in cosh_l_minus_x
    )
    # 4. Imposed-tip-temperature distribution:
    #    T(x) = T_∞ + [θ_L * sinh(mx) + θ_b * sinh(m(L-x))] / sinh(mL).
    imposed_tip_temperature = tuple(
        inputs.air_temperature_k
        + (theta_top * sinh_m_x[i] + theta_base * sinh_l_minus_x[i])
        / sinh_m_length
        for i in range(point_count)
    )

    # Characteristic fin conductance factor sqrt(h * P * k * A_c) in W/K.
    flux_scale = sqrt(
        inputs.convection_coefficient_w_m2_k
        * perimeter_m
        * inputs.conductivity_w_m_k
        * area_m2
    )
    # Base heat rate for an infinite fin: M = θ_b * sqrt(h * P * k * A_c) (in W).
    infinite_base_flux = theta_base * flux_scale
    # Base heat rate for a convective tip: q_b = M * [sinh(mL) + (h/mk)*cosh(mL)] / [cosh(mL) + (h/mk)*sinh(mL)].
    convective_tip_base_flux = (
        infinite_base_flux
        * (sinh_m_length + h_over_mk * cosh_m_length)
        / (cosh_m_length + h_over_mk * sinh_m_length)
    )
    # Base heat rate for an insulated tip: q_b = M * tanh(mL) = M * sinh(mL) / cosh(mL).
    insulated_tip_base_flux = infinite_base_flux * sinh_m_length / cosh_m_length
    # Base heat rate for an imposed tip temperature: q_b = sqrt(hPkA_c) * (θ_b * cosh(mL) - θ_L) / sinh(mL).
    imposed_tip_base_flux = (
        flux_scale
        * (theta_base * cosh_m_length - theta_top)
        / sinh_m_length
    )

    # Package and return all scalar quantities, helper rows, temperature profiles, and base heat rates.
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
    """Compute the 1D steady-state analytical temperatures (at cell centers) and
    conductive heat rates (at cell faces) directly from a numerical configuration and grid.

    Pedagogical Method
    ------------------
    1. **Effective Geometry & Properties**:
       Computes the length-weighted mean cross-sectional area $A_c$, perimeter $P$,
       volumetric heat generation $\\dot{q}_{\\text{gen}}$, and harmonic/series effective
       thermal conductivity $k_{\\text{eff}} = L / (\\sum \\Delta x_i / k_i + \\sum R''_{c,i})$.
    2. **Unified Robin Boundary Conditions**:
       Both the left boundary ($x = 0$) and right boundary ($x = L$) are written in
       the general linear Robin form:
           Left ($x = 0$):  $\\alpha_L T(0) + \\beta_L q(0) = \\gamma_L$
           Right ($x = L$): $\\alpha_R T(L) + \\beta_R q(L) = \\gamma_R$
       where $q(x) = -k_{\\text{eff}} A_c T'(x)$ is the axial conduction heat transfer
       rate (W) in the $+x$ direction.
    3. **Overflow-Safe Exponential Basis ($h_{\\text{lat}} > 0$)**:
       Instead of raw $\\cosh(mx)$ and $\\sinh(mx)$ (which overflow IEEE-754 floats when
       $mL > 710$), uses scaled combinations of $e^{-mx}$ and $e^{-m(L-x)}$ that are
       algebraically identical to $\\cosh / \\sinh$ for normal $mL$ and remain bounded
       in $[-1, 1]$ for arbitrarily large $mL$.
    4. **Pure 1D Conduction ($h_{\\text{lat}} = 0$)**:
       When lateral convection is off, solves $-k_{\\text{eff}} A_c T''(x) = \\dot{q}_{\\text{gen}} A_c$
       directly via the exact quadratic profile $T(x) = -\\frac{\\dot{q}_{\\text{gen}}}{2 k_{\\text{eff}}} x^2 + C_1 + C_2 x$.
    """
    # Number of finite-volume cells in the numerical grid.
    n = grid.n
    # Extract the 1-based cell-center coordinates x_{c,1} .. x_{c,n} (m).
    x_cells = grid.xc[1 : n + 1]
    # Extract the 1-based cell-face coordinates x_{f,1} .. x_{f,n+1} (m).
    x_faces = grid.xf[1 : n + 2]
    # Total fin length L is the coordinate of the rightmost face x_{f,n+1}.
    total_length = x_faces[-1]
    # Retrieve the temperature/boundary configuration sub-dictionary.
    tcfg = config["temperature"]
    # Fallback reference temperature (K) if no ambient or boundary temperature is active.
    default_temp = float(tcfg.get("default_k") or 300.0)

    # Guard against degenerate zero-length grids.
    if total_length <= 0.0:
        # Return uniform default temperature and zero heat flux.
        return [default_temp] * n, [0.0] * (n + 1)

    # Compute length-weighted average cross-sectional area A_c = (1/L) * Σ A_{c,i} Δx_i (m²).
    area_c = sum(grid.cell_area[i] * grid.dx[i] for i in range(1, n + 1)) / total_length
    # Compute length-weighted average perimeter P = (1/L) * Σ P_i Δx_i (m).
    perimeter = sum(grid.cell_perimeter[i] * grid.dx[i] for i in range(1, n + 1)) / total_length
    # Guard against degenerate zero cross-sectional area.
    if area_c <= 0.0:
        # Return uniform default temperature and zero heat flux.
        return [default_temp] * n, [0.0] * (n + 1)

    # Total specific axial thermal resistance R''_tot = Σ (Δx_i / k_i) + Σ_{interior faces} R''_{local,i} (m²·K/W).
    cond_resistance = sum(
        grid.dx[i] / grid.conductivity[i] for i in range(1, n + 1)
    ) + sum(grid.local_resistance[i] for i in range(2, n + 1))
    # Effective series thermal conductivity k_eff = L / R''_tot (W/(m·K)).
    k_eff = total_length / cond_resistance if cond_resistance > 0.0 else float(grid.conductivity[1])

    # Check whether lateral convection is enabled and has a positive coefficient h_lat > 0.
    has_lat = bool(tcfg.get("lateral_flux")) and float(tcfg.get("lateral_convection") or 0.0) > 0.0
    # Lateral convection coefficient h_lat (W/(m²·K)), or 0.0 when lateral convection is disabled.
    h_lat = float(tcfg["lateral_convection"]) if has_lat else 0.0
    # Ambient fluid temperature T_∞ (K) for lateral convection.
    t_inf = float(tcfg["lateral_air_temperature_k"]) if has_lat else default_temp
    # Length-weighted average volumetric internal heat generation q̇_gen (W/m³).
    q_gen = sum(grid.heat_generated[i] * grid.dx[i] for i in range(1, n + 1)) / total_length

    # Formulate the left boundary condition at x = 0 in the unified linear form:
    #     alpha_L * T(0) + beta_L * q(0) = gamma_L
    # where q(0) is the conductive heat rate entering the fin at x = 0 in the +x direction.
    left_type = tcfg["left_boundary"]
    # Any local contact resistance placed right at the left boundary face (face 1).
    r_left_local = grid.local_resistance[1]
    if left_type == "Temp":
        # Imposed base temperature T_L with optional surface contact resistance R''_1:
        # T_L - T(0) = q(0) * (R''_1 / A_c)  ==>  1 * T(0) + (R''_1 / A_c) * q(0) = T_L.
        alpha_l, beta_l, gamma_l = 1.0, r_left_local / area_c, float(tcfg["left_temperature_k"])
    elif left_type == "HeatFlux" and float(tcfg.get("left_convection") or 0.0) > 0.0:
        # Convective left boundary: q(0) = (T_air,L - T(0)) / ((1/h_L + R''_1) / A_c).
        h_left = float(tcfg["left_convection"])
        # Rearranged into: 1 * T(0) + ((R''_1 + 1/h_L) / A_c) * q(0) = T_air,L.
        alpha_l, beta_l, gamma_l = 1.0, (r_left_local + 1.0 / h_left) / area_c, float(tcfg["left_air_temperature_k"])
    else:
        # Insulated left boundary (`NoHeatFlux` or `HeatFlux` with h_L = 0): q(0) = 0.
        alpha_l, beta_l, gamma_l = 0.0, 1.0, 0.0

    # Formulate the right boundary condition at x = L in the unified linear form:
    #     alpha_R * T(L) + beta_R * q(L) = gamma_R
    # where q(L) is the conductive heat rate leaving the fin at x = L in the +x direction.
    right_type = tcfg["right_boundary"]
    # Any local contact resistance placed right at the right boundary face (face n + 1).
    r_right_local = grid.local_resistance[n + 1]
    if right_type == "Temp":
        # Imposed tip temperature T_R with optional surface contact resistance R''_{n+1}:
        # T(L) - T_R = q(L) * (R''_{n+1} / A_c)  ==>  1 * T(L) - (R''_{n+1} / A_c) * q(L) = T_R.
        alpha_r, beta_r, gamma_r = 1.0, -r_right_local / area_c, float(tcfg["right_temperature_k"])
    elif right_type == "HeatFlux" and float(tcfg.get("right_convection") or 0.0) > 0.0:
        # Convective right boundary: q(L) = (T(L) - T_air,R) / ((1/h_R + R''_{n+1}) / A_c).
        h_right = float(tcfg["right_convection"])
        # Rearranged into: 1 * T(L) - ((R''_{n+1} + 1/h_R) / A_c) * q(L) = T_air,R.
        alpha_r, beta_r, gamma_r = 1.0, -(r_right_local + 1.0 / h_right) / area_c, float(tcfg["right_air_temperature_k"])
    else:
        # Insulated right boundary (`NoHeatFlux` or `HeatFlux` with h_R = 0): q(L) = 0.
        alpha_r, beta_r, gamma_r = 0.0, 1.0, 0.0

    # Branch 1: Lateral convection is active (h_lat > 0 and P > 0) -> Hyperbolic / exponential fin ODE.
    if h_lat > 0.0 and perimeter > 0.0:
        # Fin parameter m = sqrt((h_lat * P) / (k_eff * A_c)) in m⁻¹.
        m = sqrt(h_lat * perimeter / (k_eff * area_c))
        # Characteristic axial conductance factor m * k_eff * A_c = sqrt(h_lat * P * k_eff * A_c) (W/K).
        mk_a = m * k_eff * area_c
        # Particular solution of the inhomogeneous ODE when volumetric heat generation q̇_gen is present:
        # T_p = T_∞ + (q̇_gen * A_c) / (h_lat * P).
        t_particular = t_inf + (q_gen * area_c) / (h_lat * perimeter)
        # Scaling denominator 1 + exp(-2 m L) used to normalize cosh(m(L-x))/cosh(mL) without overflow.
        denom_scale = 1.0 + exp(-2.0 * m * total_length)

        def phi1(x: float) -> float:
            """First temperature basis function: cosh(m(L - x)) / cosh(mL), written via decaying exponentials."""
            # Evaluates exp(-mx) * (1 + exp(-2m(L - x))) / (1 + exp(-2mL)), which is always in [0, 1].
            return exp(-m * x) * (1.0 + exp(-2.0 * m * (total_length - x))) / denom_scale

        def phi2(x: float) -> float:
            """Second temperature basis function: sinh(mx) / cosh(mL), written via decaying exponentials."""
            # Evaluates exp(-m(L - x)) * (1 - exp(-2mx)) / (1 + exp(-2mL)), which is always in [0, 1].
            return exp(-m * (total_length - x)) * (1.0 - exp(-2.0 * m * x)) / denom_scale

        def psi1(x: float) -> float:
            """Negative scaled derivative -phi1'(x) / m = sinh(m(L - x)) / cosh(mL) for heat flux q(x)."""
            # Evaluates exp(-mx) * (1 - exp(-2m(L - x))) / (1 + exp(-2mL)).
            return exp(-m * x) * (1.0 - exp(-2.0 * m * (total_length - x))) / denom_scale

        def psi2(x: float) -> float:
            """Negative scaled derivative -phi2'(x) / m = -cosh(mx) / cosh(mL) for heat flux q(x)."""
            # Evaluates -exp(-m(L - x)) * (1 + exp(-2mx)) / (1 + exp(-2mL)).
            return -exp(-m * (total_length - x)) * (1.0 + exp(-2.0 * m * x)) / denom_scale

        # Assemble the 2x2 linear system [a11  a12; a21  a22] [C1; C2] = [rhs1; rhs2]
        # enforcing the left boundary condition at x = 0 and right boundary condition at x = L.
        a11 = alpha_l * phi1(0.0) + beta_l * mk_a * psi1(0.0)  # Left BC coefficient multiplying C1
        a12 = alpha_l * phi2(0.0) + beta_l * mk_a * psi2(0.0)  # Left BC coefficient multiplying C2
        a21 = alpha_r * phi1(total_length) + beta_r * mk_a * psi1(total_length)  # Right BC coefficient multiplying C1
        a22 = alpha_r * phi2(total_length) + beta_r * mk_a * psi2(total_length)  # Right BC coefficient multiplying C2
        # Subtract the constant particular solution contribution from both boundary right-hand sides.
        rhs1 = gamma_l - alpha_l * t_particular  # Left BC right-hand side
        rhs2 = gamma_r - alpha_r * t_particular  # Right BC right-hand side

        # Solve the 2x2 linear system by Cramer's rule: det = a11 * a22 - a12 * a21.
        det = a11 * a22 - a12 * a21
        if abs(det) < 1e-30:
            # If the system is singular, the homogeneous coefficients are zero (T(x) = T_particular).
            c1, c2 = 0.0, 0.0
        else:
            # Compute C1 and C2 via Cramer's rule.
            c1 = (rhs1 * a22 - rhs2 * a12) / det
            c2 = (a11 * rhs2 - a21 * rhs1) / det

        # Evaluate the analytical steady-state temperature T(x) at every cell center x_c.
        temps = [t_particular + c1 * phi1(x) + c2 * phi2(x) for x in x_cells]
        # Evaluate the analytical steady-state conductive heat rate q(x) = -k_eff A_c T'(x) at every cell face x_f.
        fluxes = [mk_a * (c1 * psi1(x) + c2 * psi2(x)) for x in x_faces]
        # Return the cell-center temperatures (K) and face heat fluxes (W).
        return temps, fluxes

    # Branch 2: Pure 1D conduction (h_lat == 0):
    # Governing ODE: -k_eff * T''(x) = q̇_gen
    # General solution:
    #   T(x) = -(q̇_gen / (2 * k_eff)) * x^2 + C1 + C2 * x
    #   q(x) = -k_eff * A_c * T'(x) = q̇_gen * A_c * x - k_eff * A_c * C2
    a11 = alpha_l  # At x = 0, T(0) = C1 and q(0) = -k_eff * A_c * C2
    a12 = -beta_l * k_eff * area_c  # Coefficient of C2 in the left boundary equation
    a21 = alpha_r  # Coefficient of C1 in the right boundary equation at x = L
    a22 = alpha_r * total_length - beta_r * k_eff * area_c  # Coefficient of C2 at x = L
    # Left boundary right-hand side at x = 0 (where x^2 = 0 and x = 0).
    rhs1 = gamma_l
    # Right boundary right-hand side at x = L (moving the known q̇_gen terms to the RHS).
    rhs2 = (
        gamma_r
        + alpha_r * (q_gen * total_length**2) / (2.0 * k_eff)
        - beta_r * q_gen * area_c * total_length
    )
    # Determinant of the 2x2 boundary system.
    det = a11 * a22 - a12 * a21
    if abs(det) < 1e-30:
        # When both ends have NoHeatFlux (Neumann-Neumann), T'(x) = 0 and T(x) = default_temp.
        c1, c2 = default_temp, 0.0
    else:
        # Solve for integration constants C1 (K) and C2 (K/m) via Cramer's rule.
        c1 = (rhs1 * a22 - rhs2 * a12) / det
        c2 = (a11 * rhs2 - a21 * rhs1) / det

    # Evaluate T(x) at each cell center x_c (m).
    temps = [-(q_gen / (2.0 * k_eff)) * x**2 + c1 + c2 * x for x in x_cells]
    # Evaluate q(x) = -k_eff * A_c * T'(x) at each cell face x_f (m).
    fluxes = [q_gen * area_c * x - k_eff * area_c * c2 for x in x_faces]
    # Return the cell-center temperatures (K) and face heat fluxes (W).
    return temps, fluxes


def read_worksheet_cached_values(path: str | Path) -> dict[str, float]:
    """Read all numeric cell values (including cached formula outputs) from `Sheet1` of an `.xlsx` file."""
    # Open the `.xlsx` archive as a ZIP file and read the raw XML of `Sheet1`.
    with ZipFile(path) as workbook:
        # `xl/worksheets/sheet1.xml` stores all cell coordinates, formulas, and cached values for Sheet1.
        sheet_xml = workbook.read("xl/worksheets/sheet1.xml")
    # Parse the worksheet XML string into an ElementTree DOM.
    root = ElementTree.fromstring(sheet_xml)
    # SpreadsheetML XML namespace required to locate `<c>` (cell) and `<v>` (value) tags.
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    # Dictionary mapping Excel cell references (e.g. `"C4"`, `"I10"`) to their floating-point values.
    values: dict[str, float] = {}
    # Iterate over every `<c>` cell element in the worksheet.
    for cell in root.iter(f"{namespace}c"):
        # Look up the `<v>` child tag holding the cell's numeric or cached formula value.
        value = cell.find(f"{namespace}v")
        # Skip empty cells and formula-error cells (`t="e"`).
        if value is not None and value.text is not None and cell.attrib.get("t") != "e":
            try:
                # Parse the cell's text as a float and store it under its A1 coordinate (`r` attribute).
                values[cell.attrib["r"]] = float(value.text)
            except ValueError:
                # Ignore string/label cells; only numeric inputs and results are needed.
                pass
    # Return the dictionary of cell-reference -> float value pairs.
    return values


def read_analytical_workbook_inputs(path: str | Path) -> AnalyticalInputs:
    """Load the nine scalar parameters of `AnalyticalInputs` from `Fin - Analytical solution.xlsx`."""
    # Read all numeric cells from `Sheet1` of the analytical workbook.
    cells = read_worksheet_cached_values(path)
    # Map the nine workbook input cells (`C4:C7`, `C9:C10`, `C12:C14`) to `AnalyticalInputs`.
    return AnalyticalInputs(
        width_mm=cells["C4"],  # Width W (mm)
        thickness_mm=cells["C5"],  # Thickness t (mm)
        length_mm=cells["C6"],  # Length L (mm)
        n_cells=cells["C7"],  # Number of cells N
        conductivity_w_m_k=cells["C9"],  # Thermal conductivity k (W/(m·K))
        convection_coefficient_w_m2_k=cells["C10"],  # Convection coefficient h (W/(m²·K))
        base_temperature_k=cells["C12"],  # Base temperature T_b (K)
        air_temperature_k=cells["C13"],  # Ambient air temperature T_∞ (K)
        top_temperature_k=cells["C14"],  # Imposed tip temperature T_L (K)
    )
