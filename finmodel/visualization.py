"""2D chart specifications and axis-scaling logic for numerical and analytical comparisons.

Pedagogical Overview
--------------------
For both **Temperature** ($T$, in kelvin, evaluated at the $N$ cell centers $x_{c,i}$)
and **CrossSectionHeatFlux** ($q$, in watts, evaluated at the $N+1$ cell faces $x_{f,i}$),
this module constructs four families of 2D plots:

1. `overlap_time` (**Time-Series Overlap**):
   Plots the transient evolution $T(x_{c,k}, t)$ or $q(x_{f,k}, t)$ versus time $t$
   at all student-selected cell numbers $k$, together with horizontal dashed
   `"Analytical @Cell k"` asymptote lines showing the $t \\to \\infty$ steady-state
   analytical value for each selected cell.
2. `overlap_space` (**Longitudinal Spatial Profile Overlap**):
   Plots the spatial distribution $T(x, t_p)$ or $q(x, t_p)$ along the fin length $x$
   at each student-selected percentage $p\\%$ of the simulation duration, overlaid
   with the dashed `"Analytical"` steady-state curve so students can watch the
   transient numerical profiles converge toward the analytical solution.
3. `individual_time` (**Individual Cell Time History**):
   One dedicated chart per selected cell $k$, comparing its numerical time history
   against its steady-state `"Analytical"` reference value.
4. `individual_space` (**Individual Time-Slice Spatial Profile**):
   One dedicated chart per selected time percentage $p\\%$, comparing the numerical
   spatial profile at that instant against the steady-state `"Analytical"` profile.
"""

from __future__ import annotations  # Postpone evaluation of type annotations

from dataclasses import asdict, dataclass  # Used to define immutable chart specifications
from math import ceil, floor, isfinite, log, log10  # Mathematical helpers for 1-2-5 major tick calculation
from typing import Any, Literal, Mapping  # Type annotations

from finmodel.numerical import SimulationResult  # Output container from the numerical solver


# Literal union types for the two physical quantities and four chart families.
PropertyName = Literal["Temperature", "CrossSectionHeatFlux"]
ChartKind = Literal["overlap_time", "overlap_space", "individual_time", "individual_space"]


@dataclass(frozen=True)
class ChartSeries:
    """A single plotted curve `(x_values, y_values)` inside a 2D chart."""

    name: str  # Legend label for this curve (e.g. `" @Cell 1"`, `" @Time 150 seconds"`, `"Analytical"`)
    x_values: tuple[float | None, ...]  # Horizontal axis coordinates (time in s, or distance in m)
    y_values: tuple[float | None, ...]  # Vertical axis values (temperature in K, or heat flux in W)
    cell_number: int | None = None  # 1-based cell/face index (for time-series curves)
    percentage: int | None = None  # Selected percentage 0..100 of simulation duration (for spatial profiles)
    row_index: int | None = None  # 0-based printed time row index (for spatial profiles)
    time_s: float | None = None  # Physical time in seconds corresponding to `row_index`


@dataclass(frozen=True)
class ChartSpec:
    """Complete layout, axis bounds, and series specification for a single 2D chart."""

    property_name: PropertyName  # `"Temperature"` or `"CrossSectionHeatFlux"`
    kind: ChartKind  # `"overlap_time"`, `"overlap_space"`, `"individual_time"`, or `"individual_space"`
    title: str  # Chart title displayed at the top of the plot
    x_axis: str  # Horizontal axis title (`"Seconds"` or `"Distance to Fin´s Base (m)"`)
    y_axis: str  # Vertical axis title (`"Temperature (K)"` or `"CrossSectionHeatFlux (W)"`)
    series: tuple[ChartSeries, ...]  # Tuple of numerical and analytical curves plotted on this chart
    x_min: float  # Lower bound of the horizontal axis
    x_max: float  # Upper bound of the horizontal axis
    y_min: float  # Lower bound of the vertical axis
    y_max: float  # Upper bound of the vertical axis
    left_px: int  # Horizontal pixel offset in the Excel worksheet layout (100 for Temperature, 620 for Flux)
    top_px: int  # Vertical pixel offset in the Excel worksheet layout (increments by 350 px per chart)
    width_px: int = 500  # Chart width in pixels
    height_px: int = 325  # Chart height in pixels
    chart_type: str = "xy_scatter_lines"  # Chart style identifier
    source_first_column: int | None = None  # First worksheet column index used for spatial series
    source_last_column: int | None = None  # Last worksheet column index used for spatial series
    selected_percentage: int | None = None  # Percentage 0..100 for `individual_space` charts
    selected_row_index: int | None = None  # Printed row index for `individual_space` charts
    selected_time_s: float | None = None  # Physical time (s) for `individual_space` charts

    def as_dict(self) -> dict[str, Any]:
        """Convert this `ChartSpec` (and its nested `ChartSeries`) into a JSON-serializable dictionary."""
        # Use `dataclasses.asdict` to recursively convert the dataclass hierarchy.
        return asdict(self)


def _vba_controls(values: list[int | float], upper_bound: int, label: str) -> list[int]:
    """Validate that every user-entered graph control lies in `0..upper_bound` and round to `int`."""
    # List of validated integer indices/percentages.
    selected = []
    for value in values:
        # Convert the raw entry to a float.
        number = float(value)
        # Verify that the number is finite and within the allowed range `[0, upper_bound]`.
        if not isfinite(number) or number < 0 or number > upper_bound:
            raise ValueError(f"{label} {value!r} is outside 0..{upper_bound}")
        # Round half-to-even (matching VBA's `Integer` assignment semantics) and append.
        selected.append(round(number))
    # Return the validated list of integers.
    return selected


def _axis_bounds(values: list[float | None]) -> tuple[float, float]:
    """Compute clean 1-2-5 major-tick Y-axis bounds `(y_min, y_max)` with at least 5% headroom."""
    # Filter out `None` placeholders and non-finite numbers.
    numeric = [value for value in values if value is not None and isfinite(value)]
    # If a chart has no finite data points (e.g. the blank t = 0 s initial heat-flux row), return `(0.0, 1.0)`.
    if not numeric:
        return 0.0, 1.0
    # Find the minimum and maximum plotted values across all curves on this chart.
    low = min(numeric)
    high = max(numeric)
    # Total vertical data span.
    span = high - low
    # If all plotted values are identical (`span == 0`), pad symmetrically by ±10% of `|low|` (or ±1.0).
    if span <= 0.0:
        pad = max(abs(low) * 0.1, 1.0) if low != 0.0 else 1.0
        return float(low - pad), float(high + pad)
    # Aim for approximately 6 major tick intervals across the vertical span.
    target = span / 6.0
    # Base-10 order of magnitude of the target tick step.
    exponent = floor(log10(target))
    # Generate standard 1-2-5 nice tick step candidates across neighboring powers of 10.
    candidates = [
        multiplier * 10.0**power
        for power in range(exponent - 1, exponent + 2)
        for multiplier in (1, 2, 5)
    ]
    # Pick the 1-2-5 candidate step closest in logarithmic ratio to `target`.
    step = min(candidates, key=lambda value: abs(log(value / target)))
    # Snap `y_min` down and `y_max` up to multiples of `step`.
    y_min = floor(low / step + 1e-12) * step
    y_max = ceil(high / step - 1e-12) * step
    # Add one extra tick step of headroom if the data approaches within 5% of either axis boundary.
    if (low - y_min) / span < 0.05 - 1e-10:
        y_min -= step
    if (y_max - high) / span < 0.05 - 1e-10:
        y_max += step
    # Return the computed `(y_min, y_max)` axis bounds.
    return float(y_min), float(y_max)


def _excel_x_bounds(
    values: tuple[float | None, ...], *, target_intervals: int = 6
) -> tuple[float, float]:
    """Compute a 1-2-5 horizontal axis range `(0.0, x_max)` for positive time or spatial coordinates."""
    # Find the largest non-null coordinate value on the X-axis.
    maximum = max((value for value in values if value is not None), default=0.0)
    # Default to `(0.0, 1.0)` if all coordinates are zero or null.
    if maximum <= 0:
        return 0.0, 1.0
    # Target step size for `target_intervals` major divisions across `[0, maximum]`.
    target = maximum / target_intervals
    # Base-10 order of magnitude of `target`.
    exponent = floor(log10(target))
    # Standard 1-2-5 tick step candidates.
    candidates = [
        multiplier * 10.0**power
        for power in range(exponent - 1, exponent + 2)
        for multiplier in (1, 2, 5)
    ]
    # Select the candidate step closest in log-ratio to `target`.
    step = min(candidates, key=lambda value: abs(log(value / target)))
    # Round `maximum` up to the next multiple of `step`.
    top = ceil(maximum / step - 1e-12) * step
    # Add one additional `step` if `maximum` is within 5% of `top` (matching Excel's chart auto-scaling).
    if (top - maximum) / top < 0.05 - 1e-10:
        top += step
    # Return `(0.0, top)`.
    return 0.0, top


def _selected_row(percentage: int, last_row_index: int) -> int:
    """Convert a percentage `0..100` of simulation duration into a 0-based printed row index."""
    # Round `(percentage / 100) * last_row_index` using standard half-to-even rounding.
    return round(percentage / 100 * last_row_index)


def _time_label(time_s: float, *, legacy: bool = False) -> str:
    """Use actual saved seconds, suppressing floating point display noise."""
    # Round `time_s` to the nearest integer second and convert to string.
    return str(round(time_s)) if legacy else f"{time_s:.12g}"


def build_chart_specs(
    result: SimulationResult, graph_controls: Mapping[str, list[int | float]], *, legacy_labels: bool = False
) -> list[ChartSpec]:
    """Build the ordered list of `ChartSpec` objects for all selected cells and time percentages."""
    # Extract grid size and printed numerical/analytical arrays from `result`.
    n = result.grid.n
    times = result.times_s
    temperature = result.temperature_k
    flux_times = result.heat_flux_times_s
    flux = result.cross_section_heat_flux_w
    an_temp = list(getattr(result, "analytical_temperature_k", ()) or ())
    an_flux = list(getattr(result, "analytical_heat_flux_w", ()) or ())
    # Check whether valid analytical temperature (`len == n`) and heat-flux (`len == n + 1`) profiles exist.
    has_an_temp = len(an_temp) == n
    has_an_flux = len(an_flux) == n + 1

    # Verify structural consistency of the temperature and heat-flux matrices.
    if not times or len(temperature) != len(times) or any(len(row) != n for row in temperature):
        raise ValueError("Temperature rows must match the printed times and grid cells")
    if (
        len(flux) != len(times) - 1
        or len(flux_times) != len(flux)
        or any(len(row) != n + 1 for row in flux)
    ):
        raise ValueError("Heat-flux rows must follow the blank initial row and have n+1 faces")

    # Validate the user-selected cell indices (`0..n`) and time percentages (`0..100`).
    temperature_cells = _vba_controls(list(graph_controls.get("cells", [])), n, "Cell number")
    flux_faces = _vba_controls(list(graph_controls.get("faces", temperature_cells)), n + 1, "Face number")
    percentages = _vba_controls(list(graph_controls.get("percentages", [])), 100, "Percentage")
    # Map each percentage to its corresponding 0-based row index in `times`.
    selected_rows = [_selected_row(value, len(times) - 1) for value in percentages]

    # Output list of `ChartSpec` objects (all Temperature charts first, followed by all CrossSectionHeatFlux charts).
    charts: list[ChartSpec] = []
    for property_name, column_number in (
        ("Temperature", 1),
        ("CrossSectionHeatFlux", 2),
    ):
        # Flag indicating whether we are building heat-flux charts (`True`) or temperature charts (`False`).
        is_flux = property_name == "CrossSectionHeatFlux"
        cells = flux_faces if is_flux else temperature_cells
        location = "Cell" if not is_flux or legacy_labels else "Face"
        # Whether an analytical comparison profile is available for this property.
        has_analytical = has_an_flux if is_flux else has_an_temp
        # Y-axis label with units (`"Temperature (K)"` or `"CrossSectionHeatFlux (W)"`).
        legend = property_name + (" (W)" if is_flux else " (K)")
        # Title/axis prefix used on the spatial overlap chart.
        spatial_overlap_legend = "Conduction " + legend if is_flux else legend
        # Horizontal and vertical pixel coordinates replicating the two-column Excel chart layout.
        left_px = 100 + (column_number - 1) * 520
        top_px = 100
        # Spatial X coordinates: `n + 1` face positions for heat flux, or `n` cell centers for temperature.
        position_values = result.face_positions_m if is_flux else result.cell_centers_m
        # Trailing `None` padding matching the Excel worksheet column range (`4 .. source_last_column`).
        padding = 2 if is_flux else 1
        spatial_x: tuple[float | None, ...] = tuple(position_values) + (None,) * padding
        source_last_column = 4 + (n + 1 if is_flux else n) + (1 if is_flux else 0)
        # Time X coordinates: for heat flux, row 0 (`t = 0`) is blank (`None`) in the worksheet.
        time_x: tuple[float | None, ...] = (
            (None, *flux_times) if is_flux else tuple(times)
        )
        # Precompute horizontal axis bounds for time and spatial charts.
        time_x_bounds = _excel_x_bounds(time_x)
        individual_flux_time_x_bounds = _excel_x_bounds(time_x, target_intervals=5)
        spatial_x_bounds = _excel_x_bounds(spatial_x)
        individual_flux_spatial_x_bounds = _excel_x_bounds(spatial_x, target_intervals=5)

        def time_y(cell: int) -> tuple[float | None, ...]:
            """Extract the numerical time history at 1-based `cell` (or `None`s if `cell == 0`)."""
            if cell == 0:
                return (None,) * len(times)
            if is_flux:
                # Prepend `None` for the blank t = 0 s initial heat-flux row.
                return (None, *(row[cell - 1] for row in flux))
            return tuple(row[cell - 1] for row in temperature)

        def analytical_time_y(cell: int) -> tuple[float | None, ...]:
            """Build the constant horizontal steady-state analytical reference line for `cell` vs. time."""
            if cell == 0 or not has_analytical:
                return (None,) * len(times)
            if is_flux:
                val = an_flux[cell - 1]
                return (None, *(val for _ in flux_times))
            val = an_temp[cell - 1]
            return tuple(val for _ in times)

        def space_y(row_index: int) -> tuple[float | None, ...]:
            """Extract the numerical spatial profile across the fin at printed `row_index`."""
            if is_flux:
                # Row 0 (t = 0 s) has no printed heat fluxes in the worksheet (`None`s).
                data = (None,) * (n + 1) if row_index == 0 else tuple(flux[row_index - 1])
            else:
                data = tuple(temperature[row_index])
            return data + (None,) * padding

        def analytical_space_y() -> tuple[float | None, ...]:
            """Extract the 1D steady-state analytical spatial profile across the fin."""
            if not has_analytical:
                count = n + 1 if is_flux else n
                return (None,) * (count + padding)
            data = tuple(an_flux) if is_flux else tuple(an_temp)
            return data + (None,) * padding

        def row_time(row_index: int) -> float:
            """Return the physical time in seconds corresponding to `row_index`."""
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
            """Append a `ChartSpec` to `charts` and advance the vertical position `top_px` by 350 px."""
            nonlocal top_px
            # Choose the appropriate horizontal X-axis range for this chart kind.
            if is_flux and kind == "individual_time":
                selected_x_bounds = individual_flux_time_x_bounds
            elif is_flux and kind == "individual_space":
                selected_x_bounds = individual_flux_spatial_x_bounds
            else:
                selected_x_bounds = spatial_x_bounds if spatial else time_x_bounds
            # Construct and append the `ChartSpec`.
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
            # Advance the vertical layout offset for the next chart in this column.
            top_px += 350

        # 1. `overlap_time`: One chart overlaying all selected cell time histories + analytical asymptotes.
        if cells:
            series_list: list[ChartSeries] = [
                ChartSeries(f" @{location} {cell}", time_x, time_y(cell), cell_number=cell)
                for cell in cells
            ]
            if has_analytical:
                # Append the steady-state analytical reference line for each selected cell.
                series_list.extend(
                    ChartSeries(f"Analytical @{location} {cell}", time_x, analytical_time_y(cell), cell_number=cell)
                    for cell in cells
                )
            series = tuple(series_list)
            bound_values = [value for s in series for value in s.y_values]
            append(
                "overlap_time",
                property_name + f" vs time in {location.lower()} ",
                "Seconds",
                legend,
                series,
                _axis_bounds(bound_values),
            )

        # 2. `overlap_space`: One chart overlaying all selected longitudinal profiles + analytical profile.
        if percentages:
            series_list = [
                ChartSeries(
                    " @Time " + _time_label(row_time(selected_rows[i]), legacy=legacy_labels) + " seconds",
                    spatial_x,
                    space_y(selected_rows[i]),
                    percentage=percentages[i],
                    row_index=selected_rows[i],
                    time_s=row_time(selected_rows[i]),
                )
                for i in range(len(percentages))
                # Skip row 0 (t = 0 s) on the heat-flux overlap chart because initial fluxes are blank.
                if not (is_flux and selected_rows[i] == 0)
            ]
            if has_analytical:
                # Append the 1D steady-state analytical spatial curve.
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

        # 3. `individual_time`: One dedicated time-history chart per selected cell + analytical reference.
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
                property_name + f" vs time in {location.lower()} " + str(cell),
                "Seconds",
                legend,
                series,
                _axis_bounds([value for s in series for value in s.y_values]),
            )

        # 4. `individual_space`: One dedicated longitudinal profile chart per selected percentage + analytical curve.
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
            # For the blank initial flux row (`is_flux and row_index == 0`), keep Y bounds at `(0.0, 1.0)`
            # when only the numerical blank series is considered, while still plotting the analytical curve.
            numerical_only_y = [value for value in indiv_space_series[0].y_values if value is not None]
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
            y_bounds = (
                (0.0, 1.0)
                if (is_flux and row_index == 0 and not numerical_only_y and not has_analytical)
                else _axis_bounds([value for s in series for value in s.y_values])
            )
            append(
                "individual_space",
                legend + " vs cell at " + _time_label(selected_time, legacy=legacy_labels) + " Seconds after start",
                "Distance to Fin´s Base (m)",
                legend,
                series,
                y_bounds,
                spatial=True,
                percentage=percentage,
                row_index=row_index,
            )

    # Return the complete list of 2D chart specifications.
    return charts
