"""Chart specifications for the numerical and analytical fin comparison.

Compared with the legacy XLSM ``CreateGraphs`` implementation, this module
fixes four charting bugs:
1. Replaces the hardcoded ``Round(y / 10, 0) * 10 +/- 10`` Y-axis bounds with
   scale-aware 1/2/5 tick bounds so small heat-flux curves and narrow
   temperature spans are not squashed into ``[-10, 10]``.
2. Computes overlaid chart Y-axis bounds strictly from the plotted series
   rather than scanning all columns/rows between the first and last selection.
3. Skips any ``0%`` initial row on the ``CrossSectionHeatFlux`` spatial overlap
   chart (regardless of its position in the percentage list) while preserving
   chart and axis titles when ``0%`` is the sole selection.
4. Always initializes ``top_px = 100`` even when ``cells`` is empty.

In addition, when ``result.analytical_temperature_k`` and
``result.analytical_heat_flux_w`` are present, every 2D chart (both time series
and spatial profiles, overlapped and individual, for both Temperature and
CrossSectionHeatFlux) includes the corresponding 1D steady-state analytical
solution for direct visual comparison.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil, floor, isfinite, log, log10
from typing import Any, Literal, Mapping

from finmodel.numerical import SimulationResult


PropertyName = Literal["Temperature", "CrossSectionHeatFlux"]
ChartKind = Literal["overlap_time", "overlap_space", "individual_time", "individual_space"]


@dataclass(frozen=True)
class ChartSeries:
    name: str
    x_values: tuple[float | None, ...]
    y_values: tuple[float | None, ...]
    cell_number: int | None = None
    percentage: int | None = None
    row_index: int | None = None
    time_s: float | None = None


@dataclass(frozen=True)
class ChartSpec:
    property_name: PropertyName
    kind: ChartKind
    title: str
    x_axis: str
    y_axis: str
    series: tuple[ChartSeries, ...]
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    left_px: int
    top_px: int
    width_px: int = 500
    height_px: int = 325
    chart_type: str = "xy_scatter_lines"
    source_first_column: int | None = None
    source_last_column: int | None = None
    selected_percentage: int | None = None
    selected_row_index: int | None = None
    selected_time_s: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly description for a plotting layer."""

        return asdict(self)


def _vba_controls(values: list[int | float], upper_bound: int, label: str) -> list[int]:
    """Validate the raw input number, then round to integer index."""

    selected = []
    for value in values:
        number = float(value)
        if not isfinite(number) or number < 0 or number > upper_bound:
            raise ValueError(f"{label} {value!r} is outside 0..{upper_bound}")
        selected.append(round(number))
    return selected


def _axis_bounds(values: list[float | None]) -> tuple[float, float]:
    """Choose scale-aware Y-axis bounds with 1/2/5 major tick padding."""

    numeric = [value for value in values if value is not None and isfinite(value)]
    if not numeric:
        return 0.0, 1.0
    low = min(numeric)
    high = max(numeric)
    span = high - low
    if span <= 0.0:
        pad = max(abs(low) * 0.1, 1.0) if low != 0.0 else 1.0
        return float(low - pad), float(high + pad)
    target = span / 6.0
    exponent = floor(log10(target))
    candidates = [
        multiplier * 10.0**power
        for power in range(exponent - 1, exponent + 2)
        for multiplier in (1, 2, 5)
    ]
    step = min(candidates, key=lambda value: abs(log(value / target)))
    y_min = floor(low / step + 1e-12) * step
    y_max = ceil(high / step - 1e-12) * step
    if (low - y_min) / span < 0.05 - 1e-10:
        y_min -= step
    if (y_max - high) / span < 0.05 - 1e-10:
        y_max += step
    return float(y_min), float(y_max)


def _excel_x_bounds(
    values: tuple[float | None, ...], *, target_intervals: int = 6
) -> tuple[float, float]:
    """Choose a 1/2/5 X-axis scale for positive time and spatial axes."""

    maximum = max((value for value in values if value is not None), default=0.0)
    if maximum <= 0:
        return 0.0, 1.0
    target = maximum / target_intervals
    exponent = floor(log10(target))
    candidates = [
        multiplier * 10.0**power
        for power in range(exponent - 1, exponent + 2)
        for multiplier in (1, 2, 5)
    ]
    step = min(candidates, key=lambda value: abs(log(value / target)))
    top = ceil(maximum / step - 1e-12) * step
    if (top - maximum) / top < 0.05 - 1e-10:
        top += step
    return 0.0, top


def _selected_row(percentage: int, last_row_index: int) -> int:
    return round(percentage / 100 * last_row_index)


def _time_label(time_s: float) -> str:
    return str(round(time_s))


def build_chart_specs(
    result: SimulationResult, graph_controls: Mapping[str, list[int | float]]
) -> list[ChartSpec]:
    """Describe every chart selected by the graph controls, including analytical curves."""

    n = result.grid.n
    times = result.times_s
    temperature = result.temperature_k
    flux_times = result.heat_flux_times_s
    flux = result.cross_section_heat_flux_w
    an_temp = list(getattr(result, "analytical_temperature_k", ()) or ())
    an_flux = list(getattr(result, "analytical_heat_flux_w", ()) or ())
    has_an_temp = len(an_temp) == n
    has_an_flux = len(an_flux) == n + 1

    if not times or len(temperature) != len(times) or any(len(row) != n for row in temperature):
        raise ValueError("Temperature rows must match the printed times and grid cells")
    if (
        len(flux) != len(times) - 1
        or len(flux_times) != len(flux)
        or any(len(row) != n + 1 for row in flux)
    ):
        raise ValueError("Heat-flux rows must follow the blank initial row and have n+1 faces")

    cells = _vba_controls(list(graph_controls.get("cells", [])), n, "Cell number")
    percentages = _vba_controls(list(graph_controls.get("percentages", [])), 100, "Percentage")
    selected_rows = [_selected_row(value, len(times) - 1) for value in percentages]

    charts: list[ChartSpec] = []
    for property_name, column_number in (
        ("Temperature", 1),
        ("CrossSectionHeatFlux", 2),
    ):
        is_flux = property_name == "CrossSectionHeatFlux"
        has_analytical = has_an_flux if is_flux else has_an_temp
        legend = property_name + (" (W)" if is_flux else " (K)")
        spatial_overlap_legend = "Conduction " + legend if is_flux else legend
        left_px = 100 + (column_number - 1) * 520
        top_px = 100
        position_values = result.face_positions_m if is_flux else result.cell_centers_m
        padding = 2 if is_flux else 1
        spatial_x: tuple[float | None, ...] = tuple(position_values) + (None,) * padding
        source_last_column = 4 + (n + 1 if is_flux else n) + (1 if is_flux else 0)
        time_x: tuple[float | None, ...] = (
            (None, *flux_times) if is_flux else tuple(times)
        )
        time_x_bounds = _excel_x_bounds(time_x)
        individual_flux_time_x_bounds = _excel_x_bounds(time_x, target_intervals=5)
        spatial_x_bounds = _excel_x_bounds(spatial_x)
        individual_flux_spatial_x_bounds = _excel_x_bounds(spatial_x, target_intervals=5)

        def time_y(cell: int) -> tuple[float | None, ...]:
            if cell == 0:
                return (None,) * len(times)
            if is_flux:
                return (None, *(row[cell - 1] for row in flux))
            return tuple(row[cell - 1] for row in temperature)

        def analytical_time_y(cell: int) -> tuple[float | None, ...]:
            if cell == 0 or not has_analytical:
                return (None,) * len(times)
            if is_flux:
                val = an_flux[cell - 1]
                return (None, *(val for _ in flux_times))
            val = an_temp[cell - 1]
            return tuple(val for _ in times)

        def space_y(row_index: int) -> tuple[float | None, ...]:
            if is_flux:
                data = (None,) * (n + 1) if row_index == 0 else tuple(flux[row_index - 1])
            else:
                data = tuple(temperature[row_index])
            return data + (None,) * padding

        def analytical_space_y() -> tuple[float | None, ...]:
            if not has_analytical:
                count = n + 1 if is_flux else n
                return (None,) * (count + padding)
            data = tuple(an_flux) if is_flux else tuple(an_temp)
            return data + (None,) * padding

        def row_time(row_index: int) -> float:
            if is_flux:
                return 0.0 if row_index == 0 else flux_times[row_index - 1]
            return times[row_index]

        def append(
            kind: ChartKind,
            title: str,
            x_axis: str,
            y_axis: str,
            series: tuple[ChartSeries, ...],
            bounds: tuple[float, float],
            *,
            spatial: bool = False,
            percentage: int | None = None,
            row_index: int | None = None,
        ) -> None:
            nonlocal top_px
            if is_flux and kind == "individual_time":
                selected_x_bounds = individual_flux_time_x_bounds
            elif is_flux and kind == "individual_space":
                selected_x_bounds = individual_flux_spatial_x_bounds
            else:
                selected_x_bounds = spatial_x_bounds if spatial else time_x_bounds
            charts.append(
                ChartSpec(
                    property_name=property_name,
                    kind=kind,
                    title=title,
                    x_axis=x_axis,
                    y_axis=y_axis,
                    series=series,
                    x_min=selected_x_bounds[0],
                    x_max=selected_x_bounds[1],
                    y_min=bounds[0],
                    y_max=bounds[1],
                    left_px=left_px,
                    top_px=top_px,
                    source_first_column=4 if spatial else None,
                    source_last_column=source_last_column if spatial else None,
                    selected_percentage=percentage,
                    selected_row_index=row_index,
                    selected_time_s=row_time(row_index) if row_index is not None else None,
                )
            )
            top_px += 350

        # 1. One chart containing all chosen time series (+ analytical).
        if cells:
            series_list: list[ChartSeries] = [
                ChartSeries(f" @Cell {cell}", time_x, time_y(cell), cell_number=cell)
                for cell in cells
            ]
            if has_analytical:
                series_list.extend(
                    ChartSeries(f"Analytical @Cell {cell}", time_x, analytical_time_y(cell), cell_number=cell)
                    for cell in cells
                )
            series = tuple(series_list)
            bound_values = [value for s in series for value in s.y_values]
            append(
                "overlap_time",
                property_name + " vs time in cell ",
                "Seconds",
                legend,
                series,
                _axis_bounds(bound_values),
            )

        # 2. One chart containing all chosen longitudinal profiles (+ analytical).
        if percentages:
            series_list = [
                ChartSeries(
                    " @Time " + _time_label(row_time(selected_rows[i])) + " seconds",
                    spatial_x,
                    space_y(selected_rows[i]),
                    percentage=percentages[i],
                    row_index=selected_rows[i],
                    time_s=row_time(selected_rows[i]),
                )
                for i in range(len(percentages))
                if not (is_flux and selected_rows[i] == 0)
            ]
            if has_analytical:
                series_list.append(
                    ChartSeries(
                        "Analytical",
                        spatial_x,
                        analytical_space_y(),
                    )
                )
            series = tuple(series_list)
            bound_values = [value for s in series for value in s.y_values]
            append(
                "overlap_space",
                spatial_overlap_legend + " vs cell ",
                "Distance to Fin´s Base (m)",
                spatial_overlap_legend,
                series,
                _axis_bounds(bound_values),
                spatial=True,
            )

        # 3. One time chart per selected cell (+ analytical).
        for cell in cells:
            indiv_time_series: list[ChartSeries] = [
                ChartSeries(legend, time_x, time_y(cell), cell_number=cell),
            ]
            if has_analytical:
                indiv_time_series.append(
                    ChartSeries("Analytical", time_x, analytical_time_y(cell), cell_number=cell)
                )
            series = tuple(indiv_time_series)
            append(
                "individual_time",
                property_name + " vs time in cell " + str(cell),
                "Seconds",
                legend,
                series,
                _axis_bounds([value for s in series for value in s.y_values]),
            )

        # 4. One spatial chart per selected percentage (+ analytical).
        for percentage, row_index in zip(percentages, selected_rows):
            selected_time = row_time(row_index)
            indiv_space_series: list[ChartSeries] = [
                ChartSeries(
                    property_name,
                    spatial_x,
                    space_y(row_index),
                    percentage=percentage,
                    row_index=row_index,
                    time_s=selected_time,
                ),
            ]
            if has_analytical:
                indiv_space_series.append(
                    ChartSeries(
                        "Analytical",
                        spatial_x,
                        analytical_space_y(),
                        percentage=percentage,
                        row_index=row_index,
                        time_s=selected_time,
                    )
                )
            series = tuple(indiv_space_series)
            append(
                "individual_space",
                legend + " vs cell at " + _time_label(selected_time) + " Seconds after start",
                "Distance to Fin´s Base (m)",
                legend,
                series,
                _axis_bounds([value for s in series for value in s.y_values]),
                spatial=True,
                percentage=percentage,
                row_index=row_index,
            )

    return charts
