"""Blank numerical form templates and input validation before running the solver.

In the Streamlit UI, a newly opened form uses `None` for numeric fields that
the student has not filled in yet, and empty strings `""` for dropdowns that
have not been selected yet. Before calling `simulate(config)`, the function
`prepare_numerical_config(config)` validates every active parameter required by
the selected fin shape, boundary condition types, radiation toggles, and
initialization mode, and converts any inactive/unused fields into `0.0` (the
same default value that VBA assigns when reading an empty Excel cell).
"""

from __future__ import annotations  # Postpone evaluation of type annotations

import copy  # Used to deep-copy the configuration dictionary before normalizing values
import math  # Used to verify that numeric inputs are finite (`math.isfinite`)
from typing import Any  # Generic type hint for configuration dictionaries


# Map each of the four supported fin geometries to the specific cross-sectional
# dimension keys (in millimetres) that must be entered by the student:
# - Rectangular: constant width W and thickness t along the fin length
# - Cylindrical: constant circular diameter D along the fin length
# - Pyramidal: rectangular cross-section tapering linearly from (W_base, t_base) to (W_top, t_top)
# - Conical: circular cross-section tapering linearly from D_base to D_top
_GEOMETRY_FIELDS = {
    "Rectangular": ("width_mm", "thickness_mm"),  # Constant rectangular cross-section
    "Cylindrical": ("diameter_mm",),  # Constant circular cross-section (pin fin)
    "Pyramidal": ("width_base_mm", "thickness_base_mm", "width_top_mm", "thickness_top_mm"),  # Tapered rectangular fin
    "Conical": ("diameter_base_mm", "diameter_top_mm"),  # Tapered circular (conical) fin
}

# Human-readable labels used in validation error messages for each geometry key.
_GEOMETRY_LABELS = {
    "width_mm": "Width (mm)",  # Rectangular fin width
    "thickness_mm": "Thickness (mm)",  # Rectangular fin thickness
    "diameter_mm": "Diameter (mm)",  # Cylindrical fin diameter
    "width_base_mm": "Base width (mm)",  # Pyramidal fin width at x = 0
    "thickness_base_mm": "Base thickness (mm)",  # Pyramidal fin thickness at x = 0
    "width_top_mm": "Top width (mm)",  # Pyramidal fin width at x = L
    "thickness_top_mm": "Top thickness (mm)",  # Pyramidal fin thickness at x = L
    "diameter_base_mm": "Base diameter (mm)",  # Conical fin diameter at x = 0
    "diameter_top_mm": "Top diameter (mm)",  # Conical fin diameter at x = L
}

# Tuple of all numeric keys stored in each axial section (`domain`) dictionary:
# - `length_mm`: axial length of the section (mm)
# - `conductivity`: thermal conductivity k (W/(m·K))
# - `thermal_capacity`: specific heat capacity c_p (J/(kg·K))
# - `density`: mass density ρ (kg/m³)
# - `heat_generated`: volumetric internal heat generation rate q̇_gen (W/m³)
# - surface emissivities (ε) and absorptivities (α) for lateral, end-face, and gas-gap radiation
_SECTION_NUMBERS = (
    "length_mm", "conductivity", "thermal_capacity", "density",
    "heat_generated", "lateral_emissivity", "lateral_absorptivity",
    "left_emissivity", "left_absorptivity", "right_emissivity",
    "right_absorptivity", "gas_face_emissivity_left",
    "gas_face_emissivity_right", "gas_face_absorptivity_left",
    "gas_face_absorptivity_right",
)

# Tuple of all scalar numeric keys stored in the `temperature` dictionary:
# - `default_k`: default initial temperature (K)
# - `minimum_k`, `maximum_k`: optional temperature warning thresholds (K)
# - left/right boundary imposed temperatures (K), convection coefficients h (W/(m²·K)),
#   ambient air temperatures T_∞ (K), and radiative surroundings temperatures T_sur (K)
# - `initial_uniform_k`: uniform initial temperature (K) when `initial_type == "DEFCELL"`
_TEMPERATURE_NUMBERS = (
    "default_k", "minimum_k", "maximum_k", "left_temperature_k",
    "left_convection", "left_air_temperature_k", "right_temperature_k",
    "right_convection", "right_air_temperature_k", "lateral_convection",
    "lateral_air_temperature_k", "lateral_surround_k", "left_surround_k",
    "right_surround_k", "initial_uniform_k",
)


def blank_section() -> dict[str, Any]:
    """Return a new axial fin section dictionary with all numeric inputs unset (`None`)."""
    # Initialize every numeric property to `None`, cell count to `None`, gas_gap to `False`,
    # and the list of local contact resistances to an empty list `[]`.
    return {
        **{key: None for key in _SECTION_NUMBERS},  # All continuous physical properties start blank
        "n_cells": None,  # Number of finite-volume cells in this section starts blank
        "gas_gap": False,  # Whether this section is a 1-cell radiative gas gap
        "resistances": [],  # List of `{distance_mm, value}` local contact resistances in this section
    }


def empty_numerical_config() -> dict[str, Any]:
    """Return a completely blank numerical configuration dictionary with one empty section.

    All structural keys, lists, and boolean checkboxes are initialized so the
    Streamlit UI can bind widgets to them immediately and JSON presets share the
    exact same dictionary schema.
    """
    # Construct and return the five top-level configuration groups.
    return {
        "controls": {
            "method": "",  # Time discretisation scheme: `"imp"` (Implicit) or `"exp"` (Explicit)
            "duration_s": None,  # Total physical time to simulate (s)
            "time_step_s": None,  # Numerical integration time step Δt (s)
            "output_interval_s": None,  # Time interval Δt_print between saved output states (s)
        },
        "geometry": {
            "shape": "",  # Cross-sectional geometry (`Rectangular`, `Cylindrical`, `Pyramidal`, `Conical`)
            **{key: None for key in _GEOMETRY_LABELS},  # All shape dimensions start as `None`
        },
        "domains": [blank_section()],  # Start with a single blank axial section
        "temperature": {
            **{key: None for key in _TEMPERATURE_NUMBERS},  # All scalar temperature/convection inputs start blank
            "left_boundary": "",  # Left boundary type (`Temp`, `HeatFlux`, or `NoHeatFlux`)
            "right_boundary": "",  # Right boundary type (`Temp`, `HeatFlux`, or `NoHeatFlux`)
            "initial_type": "",  # Initialization mode (`DEFAULT`, `DEFCELL`, or `CELL`)
            "lateral_flux": False,  # Toggle for lateral surface convection along the fin perimeter
            "lateral_radiation": False,  # Toggle for lateral surface thermal radiation
            "left_radiation": False,  # Toggle for thermal radiation at the left end face (when `HeatFlux`)
            "right_radiation": False,  # Toggle for thermal radiation at the right end face (when `HeatFlux`)
            "initial_cells": [],  # Per-cell initial temperature overrides `[{"cell": i, "value_k": T}]`
        },
        "graphs": {
            "cells": [],  # 1-based cell indices selected for temperature/flux vs. time graphs
            "percentages": [],  # Percentages (0..100) of simulation duration selected for spatial profiles
        },
    }


def _empty(value: Any) -> bool:
    """Return `True` if `value` is `None` or a whitespace-only string."""
    # Check for `None` or a string that becomes empty after stripping whitespace.
    return value is None or isinstance(value, str) and not value.strip()


def _number(value: Any, label: str, *, required: bool) -> float:
    """Validate and convert `value` to a finite `float`, or return `0.0` if optional and blank."""
    # Handle blank (`None` or `""`) inputs first.
    if _empty(value):
        # If the field is active/required for the chosen physical configuration, prompt the student.
        if required:
            # Raise a clear error naming the missing physical parameter.
            raise ValueError(f"Enter {label}.")
        # Inactive/optional blank fields default to 0.0, matching Excel/VBA blank-cell behavior.
        return 0.0
    # In Python, `bool` is a subclass of `int` (`isinstance(True, int)` is True), so reject booleans explicitly.
    if isinstance(value, bool):
        # Prevent `True`/`False` from being silently coerced into `1.0`/`0.0`.
        raise ValueError(f"{label} must be a number.")
    # Attempt to convert the value to a standard IEEE-754 floating-point number.
    try:
        # Convert numeric or numeric-string value to `float`.
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        # Raise a user-friendly validation message if conversion fails.
        raise ValueError(f"{label} must be a number.") from exc
    # Reject `NaN`, `+inf`, and `-inf` which would corrupt the linear algebra / time marching.
    if not math.isfinite(number):
        # Require a finite real number.
        raise ValueError(f"{label} must be a finite number.")
    # Return the validated floating-point value.
    return number


def _selection(value: Any, label: str, choices: set[str]) -> str:
    """Validate that a dropdown choice `value` is non-empty and belongs to `choices`."""
    # If the student has not picked an option yet, prompt them to choose one.
    if _empty(value):
        # Raise a validation error requesting a selection for `label`.
        raise ValueError(f"Choose {label}.")
    # Ensure the chosen value is a string and matches one of the allowed options in `choices`.
    if not isinstance(value, str) or value not in choices:
        # Raise an error if an unrecognized option string was provided.
        raise ValueError(f"Choose a valid {label}.")
    # Return the validated selection string.
    return value


def _object(config: dict[str, Any], key: str) -> dict[str, Any]:
    """Retrieve a required sub-dictionary `config[key]` or raise a `ValueError`."""
    # Look up `key` in the configuration dictionary.
    value = config.get(key)
    # Verify that the retrieved value is a dictionary.
    if not isinstance(value, dict):
        # Raise an error if the sub-dictionary is missing or not a `dict`.
        raise ValueError(f"Inputs JSON needs a {key} object.")
    # Return the validated sub-dictionary.
    return value


def _integer(value: Any, label: str) -> int:
    """Validate that `value` is a required whole number and return it as an `int`."""
    # First validate that `value` is a finite number.
    number = _number(value, label, required=True)
    # Check that the floating-point value has no fractional part (e.g. `10.0 == 10`).
    if number != int(number):
        # Reject non-integer cell counts or cell indices.
        raise ValueError(f"{label} must be a whole number.")
    # Return the validated integer.
    return int(number)


def prepare_numerical_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate all active numerical inputs and return a solver-ready deep copy of `config`.

    Which inputs are mandatory depends on the student's selections:
    - Only the dimensions belonging to the selected `shape` are required.
    - Only the boundary parameters belonging to the active `left_boundary` and
      `right_boundary` (`Temp`, `HeatFlux`, `NoHeatFlux`) are required.
    - Radiation surrounding temperatures $T_{\\text{sur}}$ are required only when
      the corresponding radiation checkbox is enabled.
    - Inactive numeric fields are filled with `0.0`.
    """
    # Ensure the top-level configuration is a dictionary.
    if not isinstance(config, dict):
        # Reject non-dictionary inputs.
        raise ValueError("Numerical inputs must be a JSON object.")
    # Work on a deep copy so the UI's session-state dictionary keeps its `None` placeholders for inactive fields.
    prepared = copy.deepcopy(config)
    # Extract and validate the four top-level sub-dictionaries.
    controls = _object(prepared, "controls")
    geometry = _object(prepared, "geometry")
    temperature = _object(prepared, "temperature")
    graphs = _object(prepared, "graphs")

    # Validate the time-discretisation scheme (`imp` for backward-Euler implicit, `exp` for forward-Euler explicit).
    controls["method"] = _selection(
        controls.get("method"), "time discretisation", {"imp", "IMP", "Imp", "exp", "EXP", "Exp"}
    )
    # Validate the three mandatory time-stepping parameters (duration, time step Δt, and print interval Δt_print).
    for key, label in (
        ("duration_s", "duration (s)"),
        ("time_step_s", "time step (s)"),
        ("output_interval_s", "output interval (s)"),
    ):
        # Convert each timing parameter to a finite float (raising an error if blank).
        controls[key] = _number(controls.get(key), label, required=True)

    # Validate the selected fin cross-sectional shape (`Rectangular`, `Cylindrical`, `Pyramidal`, or `Conical`).
    shape = _selection(geometry.get("shape"), "fin shape", set(_GEOMETRY_FIELDS))
    # Look up which dimension fields are required for this specific fin shape.
    active_dimensions = _GEOMETRY_FIELDS[shape]
    # Validate all geometry dimension fields: require active ones, default inactive ones to 0.0.
    for key, label in _GEOMETRY_LABELS.items():
        # Convert active dimensions with `required=True` and inactive dimensions with `required=False`.
        geometry[key] = _number(geometry.get(key), label, required=key in active_dimensions)

    # Retrieve the list of axial sections (`domains`) composing the fin.
    domains = prepared.get("domains")
    # Ensure at least one axial section is defined.
    if not isinstance(domains, list) or not domains:
        # Prompt the student to add at least one section if the list is empty.
        raise ValueError("Add at least one section.")
    # Validate each axial section (`domain`) in order (1-based index for user messages).
    for index, domain in enumerate(domains, 1):
        # Ensure the section entry is a dictionary.
        if not isinstance(domain, dict):
            # Raise an error if a section entry is malformed.
            raise ValueError(f"Section {index} must be an object.")
        # Coerce the `gas_gap` flag to a boolean (`True` if this section represents an air/gas gap).
        gas_gap = bool(domain.get("gas_gap", False))
        # Store the normalized boolean back into the section dictionary.
        domain["gas_gap"] = gas_gap
        # Validate all numeric material and radiative properties of the section.
        for key in _SECTION_NUMBERS:
            # Map core thermophysical property keys to human-readable labels with units.
            label = {
                "length_mm": "length (mm)",
                "conductivity": "conductivity (W/m K)",
                "thermal_capacity": "specific heat (J/kg K)",
                "density": "density (kg/m³)",
            }.get(key, key.replace("_", " "))
            # Length L, conductivity k, specific heat c_p, and density ρ are always required;
            # heat generation and radiation emissivities/absorptivities default to 0.0 if left blank.
            domain[key] = _number(
                domain.get(key), f"section {index} {label}",
                required=key in {"length_mm", "conductivity", "thermal_capacity", "density"},
            )
        # Gas-gap sections always use exactly 1 control volume (`n_cells = 1`);
        # solid sections require a positive whole number of cells entered by the student.
        domain["n_cells"] = 1 if gas_gap else _integer(domain.get("n_cells"), f"section {index} cells")
        # Retrieve the list of local contact resistances inside this section.
        resistances = domain.get("resistances", [])
        # Ensure `resistances` is a list.
        if not isinstance(resistances, list):
            # Raise an error if `resistances` is not a list.
            raise ValueError(f"Section {index} local resistances must be a list.")
        # Validate each `{distance_mm, value}` local contact resistance entry in this section.
        for resistance_index, resistance in enumerate(resistances, 1):
            # Ensure each resistance entry is a dictionary.
            if not isinstance(resistance, dict):
                # Raise an error if a resistance entry is not an object.
                raise ValueError(f"Section {index} local resistance {resistance_index} must be an object.")
            # Both the axial position `distance_mm` (mm from section start) and contact resistance `value` (m²·K/W) are required.
            for key, label in (("distance_mm", "distance (mm)"), ("value", "value (m² K/W)")):
                # Validate and convert each resistance field to a float.
                resistance[key] = _number(
                    resistance.get(key),
                    f"section {index} local resistance {resistance_index} {label}",
                    required=True,
                )
        # Store the validated list of local resistances back on the section dictionary.
        domain["resistances"] = resistances

    # Validate the boundary condition types at the left end (x = 0) and right end (x = L),
    # and the initial temperature distribution mode (`DEFAULT`, `DEFCELL`, or `CELL`).
    left = _selection(temperature.get("left_boundary"), "left boundary", {"Temp", "HeatFlux", "NoHeatFlux"})
    right = _selection(temperature.get("right_boundary"), "right boundary", {"Temp", "HeatFlux", "NoHeatFlux"})
    initial = _selection(temperature.get("initial_type"), "initialization", {"DEFAULT", "DEFCELL", "CELL"})
    # Store the validated boundary and initialization strings.
    temperature["left_boundary"] = left
    temperature["right_boundary"] = right
    temperature["initial_type"] = initial
    # Normalize the four convection/radiation boolean toggles.
    for key in ("lateral_flux", "lateral_radiation", "left_radiation", "right_radiation"):
        # Coerce each toggle to `True` or `False`.
        temperature[key] = bool(temperature.get(key, False))
    # Build a dictionary mapping only the currently active temperature/convection keys to their error labels.
    required_temperature = {}
    # `DEFAULT` initialization requires `default_k` (in `CELL` mode, `default_k` is also used for unlisted cells).
    if initial == "DEFAULT":
        # Require the default temperature (K).
        required_temperature["default_k"] = "default temperature (K)"
    # If the left boundary (x = 0) is an imposed temperature (Dirichlet BC), require `left_temperature_k`.
    if left == "Temp":
        # Require the imposed left-end temperature T_L (K).
        required_temperature["left_temperature_k"] = "left imposed temperature (K)"
    # If the left boundary (x = 0) is convective/radiative (`HeatFlux`, Robin BC), require h_L and T_∞,L.
    elif left == "HeatFlux":
        # Require left-end convection coefficient and air temperature.
        required_temperature.update({
            "left_convection": "left convection coefficient (W/m² K)",
            "left_air_temperature_k": "left air temperature (K)",
        })
        # If left-end surface radiation is also enabled, require the left surroundings temperature T_sur,L (K).
        if temperature["left_radiation"]:
            # Require left radiative surroundings temperature.
            required_temperature["left_surround_k"] = "left surroundings (K)"
    # If the right boundary (x = L) is an imposed temperature (Dirichlet BC), require `right_temperature_k`.
    if right == "Temp":
        # Require the imposed right-end temperature T_R (K).
        required_temperature["right_temperature_k"] = "right imposed temperature (K)"
    # If the right boundary (x = L) is convective/radiative (`HeatFlux`, Robin BC), require h_R and T_∞,R.
    elif right == "HeatFlux":
        # Require right-end convection coefficient and air temperature.
        required_temperature.update({
            "right_convection": "right convection coefficient (W/m² K)",
            "right_air_temperature_k": "right air temperature (K)",
        })
        # If right-end surface radiation is also enabled, require the right surroundings temperature T_sur,R (K).
        if temperature["right_radiation"]:
            # Require right radiative surroundings temperature.
            required_temperature["right_surround_k"] = "right surroundings (K)"
    # If lateral convection along the fin perimeter is enabled, require h_lat and T_∞,lat.
    if temperature["lateral_flux"]:
        # Require lateral convection coefficient and ambient air temperature.
        required_temperature.update({
            "lateral_convection": "lateral convection coefficient (W/m² K)",
            "lateral_air_temperature_k": "lateral air temperature (K)",
        })
    # If lateral radiation along the fin perimeter is enabled, require the lateral surroundings temperature T_sur,lat.
    if temperature["lateral_radiation"]:
        # Require lateral radiative surroundings temperature.
        required_temperature["lateral_surround_k"] = "lateral surroundings (K)"
    # If uniform cell initialization (`DEFCELL`) is selected, require `initial_uniform_k`.
    if initial == "DEFCELL":
        # Require uniform initial cell temperature.
        required_temperature["initial_uniform_k"] = "DEFCELL uniform temperature (K)"
    # Validate all scalar fields in `temperature`: require active ones, set inactive ones to 0.0.
    for key in _TEMPERATURE_NUMBERS:
        # Convert each field to float, enforcing `required=True` only when present in `required_temperature`.
        temperature[key] = _number(
            temperature.get(key),
            required_temperature.get(key, key.replace("_", " ")),
            required=key in required_temperature,
        )
    # Validate the list of per-cell initial temperature overrides (`initial_cells`).
    initial_cells = temperature.get("initial_cells", [])
    # Ensure `initial_cells` is a list.
    if not isinstance(initial_cells, list):
        # Raise an error if `initial_cells` is not a list.
        raise ValueError("Initial cell temperatures must be a list.")
    # When `initial_type == "CELL"`, at least one cell temperature override must be provided.
    if initial == "CELL" and not initial_cells:
        # Prompt the student to enter at least one `(cell, value_k)` row.
        raise ValueError("Enter at least one initial cell temperature for CELL initialization.")
    # Validate each `(cell, value_k)` dictionary in `initial_cells`.
    for index, entry in enumerate(initial_cells, 1):
        # Ensure each row is a dictionary object.
        if not isinstance(entry, dict):
            # Raise an error if a row is not a dictionary.
            raise ValueError(f"Initial cell temperature {index} must be an object.")
        # Validate that `cell` is an integer cell index and `value_k` is a finite temperature in kelvin.
        entry["cell"] = _integer(entry.get("cell"), f"initial cell {index} number")
        entry["value_k"] = _number(entry.get("value_k"), f"initial cell {index} temperature (K)", required=True)
    # Save the validated `initial_cells` list back into `temperature`.
    temperature["initial_cells"] = initial_cells

    # Validate the graph control lists (`cells` for time-series plots and `percentages` for spatial profiles).
    for key, label in (("cells", "graph cells"), ("percentages", "graph percentages")):
        # Retrieve the list of selected cell numbers or percentages.
        values = graphs.get(key, [])
        # Ensure the selection is a list.
        if not isinstance(values, list):
            # Raise an error if `cells` or `percentages` is not a list.
            raise ValueError(f"{label.capitalize()} must be a list.")
        # Convert every entry in the list to a validated float.
        graphs[key] = [_number(value, f"{label} entry {index}", required=True) for index, value in enumerate(values, 1)]

    # Return the fully validated, solver-ready configuration dictionary.
    return prepared
