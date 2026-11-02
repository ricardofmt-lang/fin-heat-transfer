"""Blank numerical form data and the boundary between form data and the solver.

The workbook reader and the VBA translation use numbers (including zero for
unused cells).  A new form uses ``None`` for numbers that the user has not
entered.  ``prepare_numerical_config`` checks the active inputs before it
turns the remaining blanks into the same zeros the workbook reader produces.
"""

from __future__ import annotations

import copy
import math
from typing import Any


_GEOMETRY_FIELDS = {
    "Rectangular": ("width_mm", "thickness_mm"),
    "Cylindrical": ("diameter_mm",),
    "Pyramidal": ("width_base_mm", "thickness_base_mm", "width_top_mm", "thickness_top_mm"),
    "Conical": ("diameter_base_mm", "diameter_top_mm"),
}

_GEOMETRY_LABELS = {
    "width_mm": "Width (mm)",
    "thickness_mm": "Thickness (mm)",
    "diameter_mm": "Diameter (mm)",
    "width_base_mm": "Base width (mm)",
    "thickness_base_mm": "Base thickness (mm)",
    "width_top_mm": "Top width (mm)",
    "thickness_top_mm": "Top thickness (mm)",
    "diameter_base_mm": "Base diameter (mm)",
    "diameter_top_mm": "Top diameter (mm)",
}

_SECTION_NUMBERS = (
    "length_mm", "conductivity", "thermal_capacity", "density",
    "heat_generated", "lateral_emissivity", "lateral_absorptivity",
    "left_emissivity", "left_absorptivity", "right_emissivity",
    "right_absorptivity", "gas_face_emissivity_left",
    "gas_face_emissivity_right", "gas_face_absorptivity_left",
    "gas_face_absorptivity_right",
)

_TEMPERATURE_NUMBERS = (
    "default_k", "minimum_k", "maximum_k", "left_temperature_k",
    "left_convection", "left_air_temperature_k", "right_temperature_k",
    "right_convection", "right_air_temperature_k", "lateral_convection",
    "lateral_air_temperature_k", "lateral_surround_k", "left_surround_k",
    "right_surround_k", "initial_uniform_k",
)


def blank_section() -> dict[str, Any]:
    """Return a fresh, unfilled section with the solver's domain keys."""

    return {
        **{key: None for key in _SECTION_NUMBERS},
        "n_cells": None,
        "gas_gap": False,
        "resistances": [],
    }


def empty_numerical_config() -> dict[str, Any]:
    """Return a fresh form model with no chosen values and one empty section.

    Structural lists and unchecked toggles are present so that the UI can
    render and a later JSON preset can use the identical schema.
    """

    return {
        "controls": {
            "method": "",
            "duration_s": None,
            "time_step_s": None,
            "output_interval_s": None,
        },
        "geometry": {
            "shape": "",
            **{key: None for key in _GEOMETRY_LABELS},
        },
        "domains": [blank_section()],
        "temperature": {
            **{key: None for key in _TEMPERATURE_NUMBERS},
            "left_boundary": "",
            "right_boundary": "",
            "initial_type": "",
            "lateral_flux": False,
            "lateral_radiation": False,
            "left_radiation": False,
            "right_radiation": False,
            "initial_cells": [],
        },
        "graphs": {"cells": [], "percentages": []},
    }


def _empty(value: Any) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


def _number(value: Any, label: str, *, required: bool) -> float:
    if _empty(value):
        if required:
            raise ValueError(f"Enter {label}.")
        return 0.0
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number.")
    return number


def _selection(value: Any, label: str, choices: set[str]) -> str:
    if _empty(value):
        raise ValueError(f"Choose {label}.")
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"Choose a valid {label}.")
    return value


def _object(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Inputs JSON needs a {key} object.")
    return value


def _integer(value: Any, label: str) -> int:
    number = _number(value, label, required=True)
    if number != int(number):
        raise ValueError(f"{label} must be a whole number.")
    return int(number)


def prepare_numerical_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate entered numerical inputs and make a solver-ready deep copy.

    Required fields depend on the chosen shape, boundaries, radiation and
    initialization.  An unselected or inactive numeric field is zero, just
    as a blank Excel cell is when assigned to a VBA ``Double``.
    """

    if not isinstance(config, dict):
        raise ValueError("Numerical inputs must be a JSON object.")
    prepared = copy.deepcopy(config)
    controls = _object(prepared, "controls")
    geometry = _object(prepared, "geometry")
    temperature = _object(prepared, "temperature")
    graphs = _object(prepared, "graphs")

    controls["method"] = _selection(
        controls.get("method"), "time discretisation", {"imp", "IMP", "Imp", "exp", "EXP", "Exp"}
    )
    for key, label in (
        ("duration_s", "duration (s)"),
        ("time_step_s", "time step (s)"),
        ("output_interval_s", "output interval (s)"),
    ):
        controls[key] = _number(controls.get(key), label, required=True)

    shape = _selection(geometry.get("shape"), "fin shape", set(_GEOMETRY_FIELDS))
    active_dimensions = _GEOMETRY_FIELDS[shape]
    for key, label in _GEOMETRY_LABELS.items():
        geometry[key] = _number(geometry.get(key), label, required=key in active_dimensions)

    domains = prepared.get("domains")
    if not isinstance(domains, list) or not domains:
        raise ValueError("Add at least one section.")
    for index, domain in enumerate(domains, 1):
        if not isinstance(domain, dict):
            raise ValueError(f"Section {index} must be an object.")
        gas_gap = bool(domain.get("gas_gap", False))
        domain["gas_gap"] = gas_gap
        for key in _SECTION_NUMBERS:
            label = {
                "length_mm": "length (mm)",
                "conductivity": "conductivity (W/m K)",
                "thermal_capacity": "specific heat (J/kg K)",
                "density": "density (kg/m³)",
            }.get(key, key.replace("_", " "))
            domain[key] = _number(
                domain.get(key), f"section {index} {label}",
                required=key in {"length_mm", "conductivity", "thermal_capacity", "density"},
            )
        domain["n_cells"] = 1 if gas_gap else _integer(domain.get("n_cells"), f"section {index} cells")
        resistances = domain.get("resistances", [])
        if not isinstance(resistances, list):
            raise ValueError(f"Section {index} local resistances must be a list.")
        for resistance_index, resistance in enumerate(resistances, 1):
            if not isinstance(resistance, dict):
                raise ValueError(f"Section {index} local resistance {resistance_index} must be an object.")
            for key, label in (("distance_mm", "distance (mm)"), ("value", "value (m² K/W)")):
                resistance[key] = _number(
                    resistance.get(key),
                    f"section {index} local resistance {resistance_index} {label}",
                    required=True,
                )
        domain["resistances"] = resistances

    left = _selection(temperature.get("left_boundary"), "left boundary", {"Temp", "HeatFlux", "NoHeatFlux"})
    right = _selection(temperature.get("right_boundary"), "right boundary", {"Temp", "HeatFlux", "NoHeatFlux"})
    initial = _selection(temperature.get("initial_type"), "initialization", {"DEFAULT", "DEFCELL", "CELL"})
    temperature["left_boundary"] = left
    temperature["right_boundary"] = right
    temperature["initial_type"] = initial
    for key in ("lateral_flux", "lateral_radiation", "left_radiation", "right_radiation"):
        temperature[key] = bool(temperature.get(key, False))
    required_temperature = {}
    if initial == "DEFAULT":
        required_temperature["default_k"] = "default temperature (K)"
    if left == "Temp":
        required_temperature["left_temperature_k"] = "left imposed temperature (K)"
    elif left == "HeatFlux":
        required_temperature.update({
            "left_convection": "left convection coefficient (W/m² K)",
            "left_air_temperature_k": "left air temperature (K)",
        })
        if temperature["left_radiation"]:
            required_temperature["left_surround_k"] = "left surroundings (K)"
    if right == "Temp":
        required_temperature["right_temperature_k"] = "right imposed temperature (K)"
    elif right == "HeatFlux":
        required_temperature.update({
            "right_convection": "right convection coefficient (W/m² K)",
            "right_air_temperature_k": "right air temperature (K)",
        })
        if temperature["right_radiation"]:
            required_temperature["right_surround_k"] = "right surroundings (K)"
    if temperature["lateral_flux"]:
        required_temperature.update({
            "lateral_convection": "lateral convection coefficient (W/m² K)",
            "lateral_air_temperature_k": "lateral air temperature (K)",
        })
    if temperature["lateral_radiation"]:
        required_temperature["lateral_surround_k"] = "lateral surroundings (K)"
    if initial == "DEFCELL":
        required_temperature["initial_uniform_k"] = "DEFCELL uniform temperature (K)"
    for key in _TEMPERATURE_NUMBERS:
        temperature[key] = _number(
            temperature.get(key),
            required_temperature.get(key, key.replace("_", " ")),
            required=key in required_temperature,
        )
    initial_cells = temperature.get("initial_cells", [])
    if not isinstance(initial_cells, list):
        raise ValueError("Initial cell temperatures must be a list.")
    if initial == "CELL" and not initial_cells:
        raise ValueError("Enter at least one initial cell temperature for CELL initialization.")
    for index, entry in enumerate(initial_cells, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Initial cell temperature {index} must be an object.")
        entry["cell"] = _integer(entry.get("cell"), f"initial cell {index} number")
        entry["value_k"] = _number(entry.get("value_k"), f"initial cell {index} temperature (K)", required=True)
    temperature["initial_cells"] = initial_cells

    for key, label in (("cells", "graph cells"), ("percentages", "graph percentages")):
        values = graphs.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"{label.capitalize()} must be a list.")
        graphs[key] = [_number(value, f"{label} entry {index}", required=True) for index, value in enumerate(values, 1)]

    return prepared
