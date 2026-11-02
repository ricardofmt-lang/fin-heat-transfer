"""Parser for reading input configurations from `.xlsm` and `.xlsx` numerical fin workbooks.

Pedagogical Overview
--------------------
The original Excel/VBA application stores simulation inputs across four
worksheets (`Main`, `FinProperties`, `Temperature`, and `Graph`) and locates
each parameter by scanning column A for label strings such as `"Shape:"`,
`"<BeginDomain>"`, `"<EndDomain>"`, `"LeftBoundary:"`, etc.

This module replicates those exact lookup rules in pure Python using `openpyxl`
(without executing any VBA macros or modifying the workbook), returning the
five-section configuration dictionary (`controls`, `geometry`, `domains`,
`temperature`, `graphs`) consumed by `simulate()`.
"""

from __future__ import annotations  # Postpone evaluation of type annotations

from datetime import date, datetime, time, timedelta  # Used to convert Excel date-formatted cells back to floats
from pathlib import Path  # Cross-platform path handling
from typing import Any, BinaryIO  # Type annotations for file paths and binary streams

from openpyxl import load_workbook  # Pure-Python Excel `.xlsx`/`.xlsm` reader
from openpyxl.utils.datetime import to_excel  # Converts `datetime` objects back to Excel serial numbers
from openpyxl.worksheet.worksheet import Worksheet  # Type annotation for an openpyxl worksheet


# Union type accepting a string path, `Path` object, or in-memory binary stream (`BytesIO`).
WorkbookSource = str | Path | BinaryIO


class WorkbookInputError(ValueError):
    """Exception raised when an Excel worksheet is missing required labels or contains invalid inputs."""


def _text(value: Any) -> str:
    """Convert an Excel cell value to a string, returning `""` for empty (`None`) cells."""
    # Empty cells return `""`; all other values are converted via `str(value)`.
    return "" if value is None else str(value)


def _number(value: Any, *, address: str = "") -> float:
    """Convert an Excel cell value to a `float`, treating empty cells as `0.0` (matching VBA)."""
    # In VBA, reading an empty worksheet cell into a `Double` variable yields `0.0`.
    if value is None or value == "":
        return 0.0
    # If an Excel cell was accidentally given a Date/Time number format, `openpyxl` returns
    # a Python `datetime`/`date`/`time`/`timedelta` object; convert it back to its underlying Excel serial number.
    if isinstance(value, datetime):
        return float(to_excel(value))
    if isinstance(value, date):
        return float(to_excel(datetime.combine(value, time())))
    if isinstance(value, time):
        return (
            value.hour * 3600
            + value.minute * 60
            + value.second
            + value.microsecond / 1_000_000
        ) / 86400
    if isinstance(value, timedelta):
        return value.total_seconds() / 86400
    # Convert standard numeric or numeric-string cell contents to `float`.
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        # Include the worksheet cell address (e.g. `FinProperties!B10`) in the error message.
        location = f" at {address}" if address else ""
        raise WorkbookInputError(f"Expected a number{location}; got {value!r}") from exc


def _integer(value: Any, *, address: str = "") -> int:
    """Convert an Excel cell value to an integer using banker's rounding (half-to-even, matching VBA)."""
    # Round the floating-point value to the nearest integer.
    return round(_number(value, address=address))


def _yes(value: Any) -> bool:
    """Return `True` if a cell contains one of the five `"Yes"` spellings accepted by `CreateTemperature`."""
    # Check against the exact set `{"YES", "Yes", "yes", "Y", "y"}`.
    return _text(value) in {"YES", "Yes", "yes", "Y", "y"}


def _find_label(
    sheet: Worksheet, label: str, start: int, end: int, *, required: bool = False
) -> int | None:
    """Scan column A (`column=1`) from row `start` to `end` for `label` and return its 1-based row number."""
    # Search rows `start .. end` inclusive in column A.
    for row in range(start, end + 1):
        if sheet.cell(row, 1).value == label:
            return row
    # If the label is mandatory and was not found, raise a descriptive `WorkbookInputError`.
    if required:
        raise WorkbookInputError(f"{sheet.title}: {label} not found in column A")
    # Return `None` when an optional label is absent.
    return None


def _value_by_label(
    sheet: Worksheet,
    label: str,
    start: int,
    end: int,
    column: int = 2,
    *,
    required: bool = False,
) -> Any:
    """Find `label` in column A (`start..end`) and return the raw cell value in `column` (default column B)."""
    # Locate the row containing `label` in column A.
    row = _find_label(sheet, label, start, end, required=required)
    # Return `None` if optional and absent, or the value in `(row, column)`.
    return None if row is None else sheet.cell(row, column).value


def _number_by_label(
    sheet: Worksheet,
    label: str,
    start: int,
    end: int,
    column: int = 2,
    *,
    required: bool = False,
) -> float:
    """Find `label` in column A (`start..end`) and return the numeric float value in `column`."""
    # Locate the row containing `label` in column A.
    row = _find_label(sheet, label, start, end, required=required)
    # Optional absent labels evaluate to `0.0`.
    if row is None:
        return 0.0
    # Read the target cell and convert its contents to `float`.
    cell = sheet.cell(row, column)
    return _number(cell.value, address=f"{sheet.title}!{cell.coordinate}")


def _controls(sheet: Worksheet) -> dict[str, Any]:
    """Read time-stepping controls (`method`, `duration_s`, `time_step_s`, `output_interval_s`) from `Main`."""
    # Read the time discretisation string (`imp` or `exp`) from `Main`.
    method = _text(_value_by_label(sheet, "TimeDiscretisation:", 1, 20, required=True))
    # Verify that `method` uses one of the spellings accepted by the VBA macro.
    if method not in {"imp", "IMP", "Imp", "exp", "EXP", "Exp"}:
        raise WorkbookInputError(f"Main: unknown TimeDiscretisation: {method!r}")
    # Return the normalized `controls` dictionary.
    return {
        "method": method.lower(),  # `"imp"` or `"exp"`
        "duration_s": _number_by_label(sheet, "SimulationDuration:", 1, 20, required=True),  # Total time (s)
        "time_step_s": _number_by_label(sheet, "TimeStep:", 1, 20, required=True),  # Time step Δt (s)
        "output_interval_s": _number_by_label(sheet, "DTPrint:", 1, 20, required=True),  # Print interval Δt_print (s)
    }


# Tuple of all cross-sectional dimension keys stored in `config["geometry"]`.
_GEOMETRY_KEYS = (
    "width_mm",
    "thickness_mm",
    "width_base_mm",
    "thickness_base_mm",
    "width_top_mm",
    "thickness_top_mm",
    "diameter_mm",
    "diameter_base_mm",
    "diameter_top_mm",
)


def _geometry(sheet: Worksheet) -> dict[str, Any]:
    """Read the active fin shape and its millimetre dimensions from the `FinProperties` worksheet."""
    # Locate the `"Shape:"` row in column A of `FinProperties`.
    row = _find_label(sheet, "Shape:", 1, 100, required=True)
    assert row is not None
    # In `CreateFin`, the macro scans columns B through I (`2..9`) on the `"Shape:"` row
    # and uses the last non-empty column as the active shape selection.
    selected_column = None
    shape = ""
    for column in range(2, 10):
        value = sheet.cell(row, column).value
        if value is not None and value != "":
            selected_column = column
            shape = _text(value)
    # Ensure a shape name was found in columns B:I.
    if selected_column is None:
        raise WorkbookInputError("FinProperties: Shape: has no selection in B:I")
    # Ensure the shape name is one of the four supported geometries.
    if shape not in {"Rectangular", "Pyramidal", "Cylindrical", "Conical"}:
        raise WorkbookInputError(f"FinProperties: unknown shape {shape!r}")

    # Initialize all dimension keys to `0.0`, then populate the active shape's dimensions
    # from the column immediately to the right of `selected_column` (`selected_column + 1`).
    geometry: dict[str, Any] = {"shape": shape}
    geometry.update({key: 0.0 for key in _GEOMETRY_KEYS})
    column = selected_column + 1

    def dimension(offset: int) -> float:
        """Read a numeric dimension `offset` rows below `"Shape:"` in `selected_column + 1`."""
        cell = sheet.cell(row + offset, column)
        return _number(cell.value, address=f"FinProperties!{cell.coordinate}")

    # Populate the dimensions corresponding to `shape`:
    if shape == "Rectangular":
        geometry["width_mm"] = dimension(2)  # Width (mm)
        geometry["thickness_mm"] = dimension(3)  # Thickness (mm)
    elif shape == "Pyramidal":
        geometry["width_base_mm"] = dimension(2)  # Base width (mm)
        geometry["thickness_base_mm"] = dimension(3)  # Base thickness (mm)
        geometry["width_top_mm"] = dimension(4)  # Top width (mm)
        geometry["thickness_top_mm"] = dimension(5)  # Top thickness (mm)
    elif shape == "Cylindrical":
        geometry["diameter_mm"] = dimension(2)  # Cylinder diameter (mm)
    else:
        geometry["diameter_base_mm"] = dimension(2)  # Cone base diameter (mm)
        geometry["diameter_top_mm"] = dimension(3)  # Cone top diameter (mm)
    # Return the populated `geometry` dictionary.
    return geometry


def _domain_ranges(sheet: Worksheet) -> list[tuple[int, int]]:
    """Find all matching `(<BeginDomain>, <EndDomain>)` row pairs in column A of `FinProperties`."""
    # Only `<BeginDomain>` and `<EndDomain>` markers in column A (`column=1`, rows `1..1000`) are active;
    # template blocks placed in columns B/C/D are reference examples ignored by the macro.
    beginnings = [
        row for row in range(1, 1001) if sheet.cell(row, 1).value == "<BeginDomain>"
    ]
    endings = [
        row for row in range(1, 1001) if sheet.cell(row, 1).value == "<EndDomain>"
    ]
    # Every `<BeginDomain>` must have a matching `<EndDomain>`.
    if not beginnings or len(beginnings) != len(endings):
        raise WorkbookInputError(
            "FinProperties: column A must have matching <BeginDomain>/<EndDomain> markers"
        )
    # Pair the n-th `<BeginDomain>` with the n-th `<EndDomain>` and verify `begin < end`.
    ranges = list(zip(beginnings, endings))
    if any(begin >= end for begin, end in ranges):
        raise WorkbookInputError("FinProperties: a domain ends before it begins")
    # Return the list of `(begin_row, end_row)` tuples.
    return ranges


def _domain(sheet: Worksheet, begin: int, end: int) -> dict[str, Any]:
    """Read a single axial section (`domain`) between rows `begin` and `end` of `FinProperties`."""
    # Search range strictly inside the `<BeginDomain>` and `<EndDomain>` markers.
    start, stop = begin + 1, end - 1

    def num(label: str, *, required: bool = False, column: int = 2) -> float:
        """Read a numeric property for `label` within rows `start..stop`."""
        return _number_by_label(sheet, label, start, stop, column, required=required)

    def has(label: str) -> bool:
        """Return `True` if `label` appears in column A within rows `start..stop`."""
        return _find_label(sheet, label, start, stop) is not None

    # A section is a 1-cell radiative gas gap if `"GasGap:"` appears in column A inside this block.
    gas_gap = has("GasGap:")
    # Axial length of the section in millimetres.
    length_mm = num("Length(mm):", required=True)
    # Lateral surface emissivity and absorptivity.
    lateral_emissivity = num("LateralEmissivity:")
    lateral_absorptivity = num("LateralAbsorsivity:")
    # End-face emissivities and absorptivities default to the lateral values if not explicitly listed.
    left_emissivity = num("LeftEmissivity:") if has("LeftEmissivity:") else lateral_emissivity
    left_absorptivity = (
        num("LeftAbsorsivity:") if has("LeftAbsorsivity:") else lateral_absorptivity
    )
    right_emissivity = num("RightEmissivity:") if has("RightEmissivity:") else lateral_emissivity
    right_absorptivity = (
        num("RightAbsorsivity:") if has("RightAbsorsivity:") else lateral_absorptivity
    )
    # Read declared local contact resistances (`LocalResistance:` count in column B, followed by
    # a header row `"Distance" / "Value"` and `resistance_count` data rows in columns B and C).
    resistance_row = _find_label(sheet, "LocalResistance:", start, stop)
    resistance_count = (
        _integer(sheet.cell(resistance_row, 2).value, address=f"FinProperties!B{resistance_row}")
        if resistance_row is not None
        else 0
    )
    if resistance_count < 0:
        raise WorkbookInputError("FinProperties: LocalResistance: count cannot be negative")
    if resistance_count and resistance_row is not None:
        # The first resistance data row sits 2 rows below `"LocalResistance:"` (after the header row).
        first_row = resistance_row + 2
        if sheet.cell(first_row, 2).value is None or sheet.cell(first_row, 3).value is None:
            raise WorkbookInputError(
                f"FinProperties!B{first_row}: first declared resistance needs a distance and value"
            )
    # Parse each declared `(distance_mm, value)` contact resistance pair.
    resistances = []
    for index in range(resistance_count):
        assert resistance_row is not None
        row = resistance_row + 2 + index
        distance = _number(sheet.cell(row, 2).value, address=f"FinProperties!B{row}")
        value = _number(sheet.cell(row, 3).value, address=f"FinProperties!C{row}")
        # Ensure the resistance lies within `[0, length_mm]` of this section.
        if not 0 <= distance <= length_mm:
            raise WorkbookInputError(
                f"FinProperties!B{row}: resistance location is outside its domain"
            )
        # Ensure resistances are listed in strictly ascending order of distance.
        if resistances and distance <= resistances[-1]["distance_mm"]:
            raise WorkbookInputError(
                f"FinProperties!B{row}: resistances must be in increasing order"
            )
        resistances.append({"distance_mm": distance, "value": value})

    # Return the complete section dictionary.
    return {
        "length_mm": length_mm,
        "n_cells": 1 if gas_gap else _integer(num("Ncells:", required=True)),
        "conductivity": num("Conductivity:", required=True),
        "thermal_capacity": num("ThermalCapacity:", required=True),
        "density": num("Density:", required=True),
        "heat_generated": num("HeatGenerated:"),
        "lateral_emissivity": lateral_emissivity,
        "lateral_absorptivity": lateral_absorptivity,
        "left_emissivity": left_emissivity,
        "left_absorptivity": left_absorptivity,
        "right_emissivity": right_emissivity,
        "right_absorptivity": right_absorptivity,
        "gas_gap": gas_gap,
        "gas_face_emissivity_left": num("FaceEmissivity:") if gas_gap else 0.0,
        "gas_face_emissivity_right": num("FaceEmissivity:", column=3) if gas_gap else 0.0,
        "gas_face_absorptivity_left": num("FaceAbsorsivity:") if gas_gap else 0.0,
        "gas_face_absorptivity_right": num("FaceAbsorsivity:", column=3) if gas_gap else 0.0,
        "resistances": resistances,
    }


def _temperature(sheet: Worksheet) -> dict[str, Any]:
    """Read boundary conditions, radiation toggles, and initial temperatures from `Temperature`."""
    def num(label: str, column: int = 2, *, required: bool = False) -> float:
        """Read a numeric value for `label` in `Temperature` (rows `1..50`)."""
        return _number_by_label(sheet, label, 1, 50, column, required=required)

    def raw(label: str, column: int = 2, *, required: bool = False) -> Any:
        """Read a raw cell value for `label` in `Temperature` (rows `1..50`)."""
        return _value_by_label(sheet, label, 1, 50, column, required=required)

    # Read and validate left and right boundary condition types (`Temp`, `HeatFlux`, `NoHeatFlux`).
    left_boundary = _text(raw("LeftBoundary:", required=True))
    right_boundary = _text(raw("RightBoundary:", required=True))
    valid_boundary = {"Temp", "HeatFlux", "NoHeatFlux"}
    if left_boundary not in valid_boundary or right_boundary not in valid_boundary:
        raise WorkbookInputError("Temperature: boundary types must be Temp, HeatFlux, or NoHeatFlux")

    # Read and validate the temperature initialization mode (`DEFAULT`, `CELL`, or `DEFCELL`).
    initial_type = _text(raw("InitType:")) or "DEFAULT"
    if initial_type not in {"DEFAULT", "CELL", "DEFCELL"}:
        raise WorkbookInputError(f"Temperature: invalid InitType: {initial_type!r}")
    # Read per-cell initial temperature overrides from the `"CellNumber:"` and `"CellTemperature:"` rows
    # scanning rightward from column B (`column=2`) until the first blank cell.
    cells = []
    cell_number_row = _find_label(sheet, "CellNumber:", 1, 50)
    if cell_number_row is not None:
        column = 2
        while column <= sheet.max_column:
            cell = sheet.cell(cell_number_row, column)
            if cell.value is None or cell.value == "":
                break
            cells.append(
                {
                    "cell": _integer(cell.value, address=f"Temperature!{cell.coordinate}"),
                    "value_k": _number(
                        sheet.cell(cell_number_row + 1, column).value,
                        address=f"Temperature!{sheet.cell(cell_number_row + 1, column).coordinate}",
                    ),
                }
            )
            column += 1

    # Return the complete `temperature` configuration dictionary.
    return {
        "default_k": num("DefaultValue:", required=True),
        "minimum_k": num("MinimumValue:"),
        "maximum_k": num("MaximumValue:"),
        "left_boundary": left_boundary,
        "left_temperature_k": num("LeftTempValueIfImposed:"),
        "left_convection": num("LeftConvectionCoef/AirTemp:"),
        "left_air_temperature_k": num("LeftConvectionCoef/AirTemp:", 3),
        "right_boundary": right_boundary,
        "right_temperature_k": num("RightTempValueIfImposed:"),
        "right_convection": num("RightConvectionCoef/AirTemp:"),
        "right_air_temperature_k": num("RightConvectionCoef/AirTemp:", 3),
        "lateral_flux": _yes(raw("ComputeLateralHeatFlux:")),
        "lateral_convection": num("LateralConvectionCoeficient:"),
        "lateral_air_temperature_k": num("LateralAirTemperature:"),
        "lateral_radiation": _yes(raw("LateralRadiation:")),
        "lateral_surround_k": num("LateralRadiation:", 3),
        "left_radiation": _yes(raw("LeftRadiation:")),
        "left_surround_k": num("LeftRadiation:", 3),
        "right_radiation": _yes(raw("RightRadiation:")),
        "right_surround_k": num("RightRadiation:", 3),
        "initial_type": initial_type,
        "initial_uniform_k": num("DEFCELL:"),
        "initial_cells": cells,
    }


def _graph_row(sheet: Worksheet, row: int) -> list[int]:
    """Read a row of integer graph selections from `Graph` starting in column C (`column=3`)."""
    # Find the rightmost occupied column in `row`.
    last = max(
        (column for column in range(1, sheet.max_column + 1) if sheet.cell(row, column).value is not None),
        default=0,
    )
    # Read every cell from column C (`3`) through `last` (converting interior blanks to `0` as in VBA).
    return [
        _integer(sheet.cell(row, column).value, address=f"Graph!{sheet.cell(row, column).coordinate}")
        for column in range(3, last + 1)
    ]


def load_numerical_workbook(source: WorkbookSource) -> dict[str, Any]:
    """Load a complete numerical configuration dictionary from an `.xlsm` or `.xlsx` workbook."""
    # Open the workbook in data-only mode (`data_only=True` reads cached formula values without executing VBA).
    workbook = load_workbook(source, read_only=False, data_only=True, keep_links=False)
    try:
        try:
            # Locate the four input worksheets required by the numerical model.
            main = workbook["Main"]
            fin_sheet = workbook["FinProperties"]
            temperature = workbook["Temperature"]
            graph = workbook["Graph"]
        except KeyError as exc:
            raise WorkbookInputError(f"Required numerical workbook sheet is missing: {exc}") from exc
        # Locate all active `<BeginDomain>` / `<EndDomain>` blocks in column A of `FinProperties`.
        domain_ranges = _domain_ranges(fin_sheet)
        # Assemble and return the five-section configuration dictionary.
        return {
            "controls": _controls(main),
            "geometry": _geometry(fin_sheet),
            "domains": [_domain(fin_sheet, begin, end) for begin, end in domain_ranges],
            "temperature": _temperature(temperature),
            "graphs": {"cells": _graph_row(graph, 2), "percentages": _graph_row(graph, 3)},
        }
    finally:
        # Always close the workbook handle even if an exception occurs.
        workbook.close()
