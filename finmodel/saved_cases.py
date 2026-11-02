"""Predefined lecture cases from 'Lecture 13: Exercises with the Excel application'."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import math
from typing import Any

from finmodel.empty_inputs import blank_section, empty_numerical_config


@dataclass(frozen=True)
class SavedCase:
    name: str
    description: str
    config: dict[str, Any]


def _lecture_base_section(
    *,
    length_mm: float = 50.0,
    n_cells: int = 20,
    conductivity: float = 300.0,
    thermal_capacity: float = 3000.0,
    density: float = 3000.0,
    heat_generated: float = 0.0,
    gas_gap: bool = False,
    resistances: list[dict[str, float]] | None = None,
) -> dict[str, Any]:
    sec = blank_section()
    sec.update(
        length_mm=length_mm,
        gas_gap=gas_gap,
        n_cells=1 if gas_gap else n_cells,
        conductivity=conductivity,
        thermal_capacity=thermal_capacity,
        density=density,
        heat_generated=heat_generated,
        lateral_emissivity=0.8,
        lateral_absorptivity=0.9,
        right_emissivity=0.5,
        right_absorptivity=0.4,
        left_emissivity=0.5,
        left_absorptivity=0.4,
        gas_face_emissivity_left=0.8,
        gas_face_emissivity_right=0.8,
        gas_face_absorptivity_left=0.8,
        gas_face_absorptivity_right=0.8,
        resistances=list(resistances or []),
    )
    return sec


def _lecture_base_config() -> dict[str, Any]:
    """Base configuration matching the Slide 5 Excel screenshot defaults."""
    cfg = empty_numerical_config()
    cfg["controls"].update(
        method="imp",
        duration_s=198.0,
        time_step_s=0.3,
        output_interval_s=2.0,
    )
    cfg["geometry"].update(
        shape="Rectangular",
        width_mm=300.0,
        thickness_mm=10.0,
    )
    cfg["domains"] = [_lecture_base_section()]
    cfg["temperature"].update(
        default_k=300.0,
        minimum_k=173.0,
        maximum_k=2000.0,
        left_boundary="Temp",
        left_temperature_k=400.0,
        left_convection=50.0,
        left_air_temperature_k=500.0,
        left_radiation=False,
        left_surround_k=330.0,
        right_boundary="HeatFlux",
        right_temperature_k=350.0,
        right_convection=5000.0,
        right_air_temperature_k=300.0,
        right_radiation=False,
        right_surround_k=300.0,
        lateral_flux=False,
        lateral_convection=500.0,
        lateral_air_temperature_k=300.0,
        lateral_radiation=False,
        lateral_surround_k=273.0,
        initial_type="DEFAULT",
        initial_uniform_k=600.0,
        initial_cells=[
            {"cell": 9, "value_k": 2000.0},
            {"cell": 10, "value_k": 2000.0},
        ],
    )
    cfg["graphs"] = {
        "cells": [1, 10, 20],
        "percentages": [0, 10, 20, 50, 80, 100],
    }
    return cfg


def _build_cases() -> dict[str, SavedCase]:
    cases: list[SavedCase] = []

    # Case 1
    c1 = _lecture_base_config()
    cases.append(
        SavedCase(
            name="Case 1: 1D conduction with convective right end",
            description=(
                "Rectangular slab (300 mm × 10 mm, L = 50 mm, 20 cells, k = 300 W/m·K) "
                "with insulated lateral surfaces, left end at 400 K, right end convecting "
                "to 300 K air (h = 5000 W/m²·K), initially at 300 K. Steady-state heat flux ≈ 818.18 W."
            ),
            config=c1,
        )
    )

    # Case 2
    c2 = _lecture_base_config()
    c2["controls"].update(duration_s=200.0, time_step_s=0.5, output_interval_s=5.0)
    c2["temperature"].update(
        default_k=400.0,
        left_boundary="HeatFlux",
        left_convection=500.0,
        left_air_temperature_k=300.0,
        right_boundary="HeatFlux",
        right_convection=500.0,
        right_air_temperature_k=300.0,
        initial_type="DEFAULT",
    )
    cases.append(
        SavedCase(
            name="Case 2: Symmetric convective cooling of hot slab",
            description=(
                "Rectangular slab initially at 400 K with insulated lateral surfaces, "
                "cooled symmetrically at both left and right ends by convection "
                "(h = 500 W/m²·K, T_air = 300 K), producing opposite-sign heat fluxes at the two ends."
            ),
            config=c2,
        )
    )

    # Case 3
    c3 = _lecture_base_config()
    c3["controls"].update(duration_s=150.0, time_step_s=0.3, output_interval_s=3.0)
    c3["temperature"].update(
        left_boundary="NoHeatFlux",
        right_boundary="NoHeatFlux",
        initial_type="CELL",
        default_k=300.0,
        initial_cells=[
            {"cell": 9, "value_k": 2000.0},
            {"cell": 10, "value_k": 2000.0},
        ],
    )
    c3["graphs"]["cells"] = [1, 9, 10, 20]
    cases.append(
        SavedCase(
            name="Case 3: Fully insulated bar with central hot spot",
            description=(
                "Fully adiabatic bar (NoHeatFlux on both ends, no lateral losses) "
                "initialized at 300 K except cells 9 and 10 at 2000 K. Equilibrates to a "
                "uniform 470 K by 150 s."
            ),
            config=c3,
        )
    )

    # Case 4
    c4 = _lecture_base_config()
    c4["controls"].update(duration_s=150.0, time_step_s=0.3, output_interval_s=3.0)
    c4["domains"] = [
        _lecture_base_section(length_mm=50.0, n_cells=20, conductivity=600.0),
        _lecture_base_section(length_mm=50.0, n_cells=10, conductivity=300.0),
    ]
    c4["temperature"].update(
        left_boundary="Temp",
        left_temperature_k=400.0,
        right_boundary="HeatFlux",
        right_convection=5000.0,
        right_air_temperature_k=300.0,
        initial_type="CELL",
        default_k=300.0,
        initial_cells=[
            {"cell": 9, "value_k": 2000.0},
            {"cell": 10, "value_k": 2000.0},
        ],
    )
    c4["graphs"]["cells"] = [1, 10, 20, 30]
    cases.append(
        SavedCase(
            name="Case 4: Two-layer composite bar",
            description=(
                "Two-domain composite slab (Section 1: L = 50 mm, 20 cells, k = 600 W/m·K; "
                "Section 2: L = 50 mm, 10 cells, k = 300 W/m·K) with left end at 400 K and "
                "convective right end (h = 5000 W/m²·K, 300 K), showing the slope change at x = 0.05 m."
            ),
            config=c4,
        )
    )

    # Case 5
    c5 = copy.deepcopy(c4)
    c5["controls"].update(duration_s=600.0, time_step_s=0.3, output_interval_s=10.0)
    c5["domains"][1]["resistances"] = [{"distance_mm": 25.0, "value": 0.0001}]
    c5["graphs"]["cells"] = [1, 20, 25, 30]
    cases.append(
        SavedCase(
            name="Case 5: Two-layer bar with local contact resistance",
            description=(
                "Same two-domain composite bar as Case 4, plus a local thermal contact resistance "
                "of 0.0001 m²·K/W at 25 mm inside Section 2 (x = 0.075 m), simulated for 600 s with Δt = 0.3 s."
            ),
            config=c5,
        )
    )

    # Case 6
    c6 = _lecture_base_config()
    c6["controls"].update(duration_s=100.0, time_step_s=0.3, output_interval_s=2.0)
    c6["temperature"].update(
        left_boundary="Temp",
        left_temperature_k=400.0,
        right_boundary="HeatFlux",
        right_convection=5000.0,
        right_air_temperature_k=300.0,
        lateral_flux=True,
        lateral_convection=500.0,
        lateral_air_temperature_k=300.0,
        initial_type="DEFAULT",
        default_k=300.0,
    )
    cases.append(
        SavedCase(
            name="Case 6: Conventional convective fin",
            description=(
                "Conventional rectangular fin (300 mm × 10 mm, L = 50 mm, 20 cells, k = 300 W/m·K) "
                "with base at 400 K, lateral convection (h = 500 W/m²·K, T_air = 300 K), and "
                "convective tip (h = 5000 W/m²·K, T_air = 300 K)."
            ),
            config=c6,
        )
    )

    # Case 7: Fully insulated system with initial temperature gradient and local resistance
    s14 = _lecture_base_config()
    s14["controls"].update(duration_s=150.0, time_step_s=0.5, output_interval_s=3.0)
    s14["domains"] = [
        _lecture_base_section(
            length_mm=50.0,
            n_cells=10,
            conductivity=300.0,
            resistances=[{"distance_mm": 25.0, "value": 0.0001}],
        )
    ]
    s14["temperature"].update(
        left_boundary="NoHeatFlux",
        right_boundary="NoHeatFlux",
        lateral_flux=False,
        initial_type="CELL",
        default_k=300.0,
        initial_cells=[
            {"cell": i, "value_k": 400.0 - 10.0 * (i - 1)} for i in range(1, 11)
        ],
    )
    s14["graphs"]["cells"] = [1, 5, 6, 10]
    cases.append(
        SavedCase(
            name="Case 7: Fully insulated bar with initial gradient & local resistance",
            description=(
                "Fully insulated bar (10 cells) with an initial linear temperature gradient "
                "(400 K down to 310 K) and a midpoint contact resistance (0.0001 m²·K/W), "
                "illustrating equilibration and ∂²T/∂x² vs. heat flux."
            ),
            config=s14,
        )
    )

    # Case 8: Two layers separated by a gas gap with radiation
    s15 = _lecture_base_config()
    s15["controls"].update(duration_s=300.0, time_step_s=1.0, output_interval_s=10.0)
    s15["domains"] = [
        _lecture_base_section(length_mm=25.0, n_cells=10, conductivity=300.0),
        _lecture_base_section(
            length_mm=2.0,
            n_cells=1,
            conductivity=0.026,
            thermal_capacity=1005.0,
            density=1.2,
            gas_gap=True,
        ),
        _lecture_base_section(length_mm=25.0, n_cells=10, conductivity=300.0),
    ]
    s15["temperature"].update(
        left_boundary="Temp",
        left_temperature_k=600.0,
        right_boundary="NoHeatFlux",
        lateral_flux=False,
        lateral_radiation=True,
        lateral_surround_k=300.0,
        initial_type="DEFAULT",
        default_k=300.0,
    )
    s15["graphs"]["cells"] = [1, 10, 11, 12, 21]
    cases.append(
        SavedCase(
            name="Case 8: Two-layer system with gas gap and radiation",
            description=(
                "Two solid layers (25 mm each, k = 300 W/m·K) separated by a 2 mm air gas gap "
                "(k = 0.026 W/m·K, surface radiation across gap faces) with left end at 600 K, "
                "right end insulated, and lateral radiation to 300 K surroundings."
            ),
            config=s15,
        )
    )

    # Case 9: Two domains, one with internal heat generation and one without
    s16 = _lecture_base_config()
    s16["controls"].update(duration_s=400.0, time_step_s=1.0, output_interval_s=10.0)
    s16["domains"] = [
        _lecture_base_section(
            length_mm=50.0, n_cells=10, conductivity=300.0, heat_generated=5.0e6
        ),
        _lecture_base_section(
            length_mm=50.0, n_cells=10, conductivity=300.0, heat_generated=0.0
        ),
    ]
    s16["temperature"].update(
        left_boundary="NoHeatFlux",
        right_boundary="HeatFlux",
        right_convection=5000.0,
        right_air_temperature_k=300.0,
        lateral_flux=False,
        initial_type="DEFAULT",
        default_k=300.0,
    )
    s16["graphs"]["cells"] = [1, 10, 15, 20]
    cases.append(
        SavedCase(
            name="Case 9: Two-domain system with internal heat generation",
            description=(
                "Two domains (50 mm each): Section 1 generates 5×10⁶ W/m³ and Section 2 has no "
                "heat generation. Left end is insulated (NoHeatFlux) and right end convects "
                "to 300 K air (h = 5000 W/m²·K)."
            ),
            config=s16,
        )
    )

    # Case 10: Rectangular fin insulated at the top
    s17a = _lecture_base_config()
    s17a["controls"].update(duration_s=300.0, time_step_s=1.0, output_interval_s=10.0)
    s17a["domains"] = [_lecture_base_section(length_mm=100.0, n_cells=20, conductivity=300.0)]
    s17a["temperature"].update(
        left_boundary="Temp",
        left_temperature_k=400.0,
        right_boundary="NoHeatFlux",
        lateral_flux=True,
        lateral_convection=100.0,
        lateral_air_temperature_k=300.0,
        initial_type="DEFAULT",
        default_k=300.0,
    )
    cases.append(
        SavedCase(
            name="Case 10: Rectangular fin insulated at the tip",
            description=(
                "Rectangular fin (300 mm × 10 mm, L = 100 mm, 20 cells, k = 300 W/m·K) "
                "with base at 400 K, lateral convection (h = 100 W/m²·K, 300 K), and insulated tip."
            ),
            config=s17a,
        )
    )

    # Case 11: Circular (cylindrical) fin with same cross section as rectangular (A = 3000 mm²)
    s17b = copy.deepcopy(s17a)
    equiv_diameter_mm = round(math.sqrt(4.0 * 300.0 * 10.0 / math.pi), 6)
    s17b["geometry"].update(
        shape="Cylindrical",
        diameter_mm=equiv_diameter_mm,
        width_mm=None,
        thickness_mm=None,
    )
    cases.append(
        SavedCase(
            name="Case 11: Cylindrical fin (same cross-section as rectangular)",
            description=(
                f"Cylindrical fin with diameter D = {equiv_diameter_mm:.4f} mm (matching the "
                "3000 mm² cross-section of the 300 mm × 10 mm rectangular fin), L = 100 mm, "
                "base at 400 K, lateral convection (h = 100 W/m²·K), and insulated tip."
            ),
            config=s17b,
        )
    )

    # Case 12: Rectangular fin with lateral radiation and convective tip
    s17c = copy.deepcopy(s17a)
    s17c["temperature"].update(
        left_temperature_k=500.0,
        right_boundary="HeatFlux",
        right_convection=100.0,
        right_air_temperature_k=300.0,
        lateral_radiation=True,
        lateral_surround_k=300.0,
    )
    cases.append(
        SavedCase(
            name="Case 12: Rectangular fin with tip convection & lateral radiation",
            description=(
                "Rectangular fin (300 mm × 10 mm, L = 100 mm, base at 500 K) with both "
                "lateral convection (h = 100 W/m²·K, 300 K), tip convection (h = 100 W/m²·K), "
                "and lateral radiation (T_sur = 300 K)."
            ),
            config=s17c,
        )
    )

    # Case 13: Pyramidal fin
    s17d = copy.deepcopy(s17a)
    s17d["geometry"].update(
        shape="Pyramidal",
        width_base_mm=300.0,
        thickness_base_mm=10.0,
        width_top_mm=100.0,
        thickness_top_mm=4.0,
        width_mm=None,
        thickness_mm=None,
    )
    s17d["temperature"].update(
        right_boundary="HeatFlux",
        right_convection=100.0,
        right_air_temperature_k=300.0,
    )
    cases.append(
        SavedCase(
            name="Case 13: Pyramidal fin",
            description=(
                "Tapered pyramidal fin (base 300 mm × 10 mm tapering to tip 100 mm × 4 mm, "
                "L = 100 mm, 20 cells) with base at 400 K and lateral/tip convection (h = 100 W/m²·K)."
            ),
            config=s17d,
        )
    )

    # Case 14: Conical fin
    s17e = copy.deepcopy(s17a)
    s17e["geometry"].update(
        shape="Conical",
        diameter_base_mm=equiv_diameter_mm,
        diameter_top_mm=20.0,
        width_mm=None,
        thickness_mm=None,
    )
    s17e["temperature"].update(
        right_boundary="HeatFlux",
        right_convection=100.0,
        right_air_temperature_k=300.0,
    )
    cases.append(
        SavedCase(
            name="Case 14: Conical fin",
            description=(
                f"Tapered conical fin (base diameter {equiv_diameter_mm:.4f} mm tapering to "
                "tip diameter 20 mm, L = 100 mm, 20 cells) with base at 400 K and lateral/tip convection."
            ),
            config=s17e,
        )
    )

    return {case.name: case for case in cases}


SAVED_CASES: dict[str, SavedCase] = _build_cases()


def get_saved_case_names() -> list[str]:
    """Return the ordered list of saved lecture case names."""
    return list(SAVED_CASES.keys())


def load_saved_case(name: str) -> dict[str, Any]:
    """Return a deep copy of the internal numerical config for ``name``."""
    if name not in SAVED_CASES:
        raise KeyError(f"Unknown saved case: {name}")
    return copy.deepcopy(SAVED_CASES[name].config)
