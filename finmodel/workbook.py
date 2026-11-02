"""Read the numerical fin workbook's input sheets without executing its VBA.

The parser follows the labels and row/column choices in Module1.bas. In
particular, a domain is active only when ``<BeginDomain>`` and ``<EndDomain>``
are in column A. The similar blocks in other columns are examples, not inputs.

``load_numerical_workbook`` returns a plain dictionary with five sections:
``controls``, ``geometry``, ``domains``, ``temperature``, and ``graphs``.
Lengths and dimensions retain the workbook's millimetre units; the solver is
responsible for the VBA's conversion to metres. Inactive fields are also read
so a UI can switch a boundary or initialization method without losing values.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, BinaryIO

from openpyxl import load_workbook
from openpyxl.utils.datetime import to_excel
from openpyxl.worksheet.worksheet import Worksheet


WorkbookSource = str | Path | BinaryIO


class WorkbookInputError(ValueError):
    """An input sheet cannot be interpreted as the numerical VBA would expect."""


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _number(value: Any, *, address: str = "") -> float:
    # VBA assigns an empty Excel cell to a Double as zero.
    if value is None or value == "":
        return 0.0
    # openpyxl returns clock/date objects for cells with a date number format,
    # even when the underlying Excel number is an ordinary numeric input.
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
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        location = f" at {address}" if address else ""
        raise WorkbookInputError(f"Expected a number{location}; got {value!r}") from exc


def _integer(value: Any, *, address: str = "") -> int:
    # Assignment to a VBA Integer rounds half to even (as Python round does).
    return round(_number(value, address=address))


def _yes(value: Any) -> bool:
    # These are the five accepted spellings in CreateTemperature.
    return _text(value) in {"YES", "Yes", "yes", "Y", "y"}


def _find_label(
    sheet: Worksheet, label: str, start: int, end: int, *, required: bool = False
) -> int | None:
    for row in range(start, end + 1):
        if sheet.cell(row, 1).value == label:
            return row
    if required:
        raise WorkbookInputError(f"{sheet.title}: {label} not found in column A")
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
    row = _find_label(sheet, label, start, end, required=required)
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
    row = _find_label(sheet, label, start, end, required=required)
    if row is None:
        return 0.0
    cell = sheet.cell(row, column)
    return _number(cell.value, address=f"{sheet.title}!{cell.coordinate}")


def _controls(sheet: Worksheet) -> dict[str, Any]:
    method = _text(_value_by_label(sheet, "TimeDiscretisation:", 1, 20, required=True))
    if method not in {"imp", "IMP", "Imp", "exp", "EXP", "Exp"}:
        raise WorkbookInputError(f"Main: unknown TimeDiscretisation: {method!r}")
    return {
        "method": method.lower(),
        "duration_s": _number_by_label(sheet, "SimulationDuration:", 1, 20, required=True),
        "time_step_s": _number_by_label(sheet, "TimeStep:", 1, 20, required=True),
        "output_interval_s": _number_by_label(sheet, "DTPrint:", 1, 20, required=True),
    }


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
    row = _find_label(sheet, "Shape:", 1, 100, required=True)
    assert row is not None
    selected_column = None
    shape = ""
    for column in range(2, 10):
        value = sheet.cell(row, column).value
        if value is not None and value != "":
            selected_column = column
            shape = _text(value)
    if selected_column is None:
        raise WorkbookInputError("FinProperties: Shape: has no selection in B:I")
    if shape not in {"Rectangular", "Pyramidal", "Cylindrical", "Conical"}:
        raise WorkbookInputError(f"FinProperties: unknown shape {shape!r}")

    geometry: dict[str, Any] = {"shape": shape}
    geometry.update({key: 0.0 for key in _GEOMETRY_KEYS})
    column = selected_column + 1

    def dimension(offset: int) -> float:
        cell = sheet.cell(row + offset, column)
        return _number(cell.value, address=f"FinProperties!{cell.coordinate}")

    if shape == "Rectangular":
        geometry["width_mm"] = dimension(2)
        geometry["thickness_mm"] = dimension(3)
    elif shape == "Pyramidal":
        geometry["width_base_mm"] = dimension(2)
        geometry["thickness_base_mm"] = dimension(3)
        geometry["width_top_mm"] = dimension(4)
        geometry["thickness_top_mm"] = dimension(5)
    elif shape == "Cylindrical":
        geometry["diameter_mm"] = dimension(2)
    else:
        geometry["diameter_base_mm"] = dimension(2)
        geometry["diameter_top_mm"] = dimension(3)
    return geometry


def _domain_ranges(sheet: Worksheet) -> list[tuple[int, int]]:
    # CreateSystem counts only A1:A1000. CreateFin then pairs the nth Begin
    # marker with the nth End marker, searching column A for each.
    beginnings = [
        row for row in range(1, 1001) if sheet.cell(row, 1).value == "<BeginDomain>"
    ]
    endings = [
        row for row in range(1, 1001) if sheet.cell(row, 1).value == "<EndDomain>"
    ]
    if not beginnings or len(beginnings) != len(endings):
        raise WorkbookInputError(
            "FinProperties: column A must have matching <BeginDomain>/<EndDomain> markers"
        )
    ranges = list(zip(beginnings, endings))
    if any(begin >= end for begin, end in ranges):
        raise WorkbookInputError("FinProperties: a domain ends before it begins")
    return ranges


def _domain(sheet: Worksheet, begin: int, end: int) -> dict[str, Any]:
    start, stop = begin + 1, end - 1

    def num(label: str, *, required: bool = False, column: int = 2) -> float:
        return _number_by_label(sheet, label, start, stop, column, required=required)

    def has(label: str) -> bool:
        return _find_label(sheet, label, start, stop) is not None

    gas_gap = has("GasGap:")
    length_mm = num("Length(mm):", required=True)
    lateral_emissivity = num("LateralEmissivity:")
    lateral_absorptivity = num("LateralAbsorsivity:")
    left_emissivity = num("LeftEmissivity:") if has("LeftEmissivity:") else lateral_emissivity
    left_absorptivity = (
        num("LeftAbsorsivity:") if has("LeftAbsorsivity:") else lateral_absorptivity
    )
    right_emissivity = num("RightEmissivity:") if has("RightEmissivity:") else lateral_emissivity
    right_absorptivity = (
        num("RightAbsorsivity:") if has("RightAbsorsivity:") else lateral_absorptivity
    )
    resistance_row = _find_label(sheet, "LocalResistance:", start, stop)
    resistance_count = (
        _integer(sheet.cell(resistance_row, 2).value, address=f"FinProperties!B{resistance_row}")
        if resistance_row is not None
        else 0
    )
    if resistance_count < 0:
        raise WorkbookInputError("FinProperties: LocalResistance: count cannot be negative")
    if resistance_count and resistance_row is not None:
        first_row = resistance_row + 2
        if sheet.cell(first_row, 2).value is None or sheet.cell(first_row, 3).value is None:
            raise WorkbookInputError(
                f"FinProperties!B{first_row}: first declared resistance needs a distance and value"
            )
    resistances = []
    for index in range(resistance_count):
        assert resistance_row is not None
        row = resistance_row + 2 + index
        distance = _number(sheet.cell(row, 2).value, address=f"FinProperties!B{row}")
        value = _number(sheet.cell(row, 3).value, address=f"FinProperties!C{row}")
        if not 0 <= distance <= length_mm:
            raise WorkbookInputError(
                f"FinProperties!B{row}: resistance location is outside its domain"
            )
        if resistances and distance <= resistances[-1]["distance_mm"]:
            raise WorkbookInputError(
                f"FinProperties!B{row}: resistances must be in increasing order"
            )
        resistances.append({"distance_mm": distance, "value": value})

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
    def num(label: str, column: int = 2, *, required: bool = False) -> float:
        return _number_by_label(sheet, label, 1, 50, column, required=required)

    def raw(label: str, column: int = 2, *, required: bool = False) -> Any:
        return _value_by_label(sheet, label, 1, 50, column, required=required)

    left_boundary = _text(raw("LeftBoundary:", required=True))
    right_boundary = _text(raw("RightBoundary:", required=True))
    valid_boundary = {"Temp", "HeatFlux", "NoHeatFlux"}
    if left_boundary not in valid_boundary or right_boundary not in valid_boundary:
        raise WorkbookInputError("Temperature: boundary types must be Temp, HeatFlux, or NoHeatFlux")

    initial_type = _text(raw("InitType:")) or "DEFAULT"
    if initial_type not in {"DEFAULT", "CELL", "DEFCELL"}:
        raise WorkbookInputError(f"Temperature: invalid InitType: {initial_type!r}")
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

    return {
        "default_k": num("DefaultValue:", required=True),
        "minimum_k": num("MinimumValue:"),
        # VBA overwrites this from D3, then never applies it. Preserve the
        # displayed B-cell input for editable UI state; the solver ignores it.
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
    # VBA uses the row's last occupied column minus two and reads every cell
    # from C through that column. An interior blank therefore becomes zero.
    last = max(
        (column for column in range(1, sheet.max_column + 1) if sheet.cell(row, column).value is not None),
        default=0,
    )
    return [
        _integer(sheet.cell(row, column).value, address=f"Graph!{sheet.cell(row, column).coordinate}")
        for column in range(3, last + 1)
    ]


def load_numerical_workbook(source: WorkbookSource) -> dict[str, Any]:
    """Import input settings from an `.xlsm` or `.xlsx` numerical workbook.

    ``source`` can be a filesystem path or a binary file object (including a
    Streamlit upload). Cached formula values are read; macro code is never run
    and the workbook is never saved or modified.
    """
    workbook = load_workbook(source, read_only=False, data_only=True, keep_links=False)
    try:
        try:
            main = workbook["Main"]
            fin_sheet = workbook["FinProperties"]
            temperature = workbook["Temperature"]
            graph = workbook["Graph"]
        except KeyError as exc:
            raise WorkbookInputError(f"Required numerical workbook sheet is missing: {exc}") from exc
        domain_ranges = _domain_ranges(fin_sheet)
        return {
            "controls": _controls(main),
            "geometry": _geometry(fin_sheet),
            "domains": [_domain(fin_sheet, begin, end) for begin, end in domain_ranges],
            "temperature": _temperature(temperature),
            "graphs": {"cells": _graph_row(graph, 2), "percentages": _graph_row(graph, 3)},
        }
    finally:
        workbook.close()
