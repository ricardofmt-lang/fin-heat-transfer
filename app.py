"""Streamlit web application for 1D transient and steady-state fin heat transfer.

Pedagogical Overview
--------------------
This file implements the interactive user interface that students use in the
browser:
1. **Top Loader Controls**: Students can either pick one of the 14 built-in
   benchmark cases from **Load saved cases**, upload a previously exported JSON
   configuration via **Load saved inputs JSON**, or enter parameters from scratch.
2. **Dynamic Input Form (`numerical_input_form`)**: Displays only the geometry
   dimensions, boundary condition parameters, radiation surroundings temperatures,
   and initialization fields that are physically active for the current selections.
3. **Unified Simulation & Results (`numerical_results`)**: Runs the transient
   finite-volume solver (`simulate`), which evaluates a simplified steady
   reference when available and overlays it as a dashed line, alongside the
   interactive 3D animated fin view, data tables, and CSV/JSON downloads.
"""

from __future__ import annotations  # Postpone evaluation of type annotations

import copy  # Used to copy configuration state before applying widget updates
import hashlib  # Used to hash uploaded JSON files so the same upload is not re-processed on every rerun
import json  # Used to parse uploaded JSON presets and serialize exported JSON files
import re  # Used to strip epoch suffixes (`__1`, `__2`, ...) from Streamlit widget keys
from itertools import zip_longest
from pathlib import Path  # Cross-platform filesystem path handling

import pandas as pd  # Used to build editable tables (`st.data_editor`) and CSV exports
import plotly.graph_objects as go  # Used to build interactive 2D line charts (`go.Figure`, `go.Scatter`)
import streamlit as st  # Streamlit web UI framework

from finmodel.animation import build_3d_fin_figure  # 3D animated fin mesh builder
from finmodel.animation_download import gif_download_html  # Browser-side GIF export
from finmodel.empty_inputs import blank_section, empty_numerical_config, prepare_numerical_config  # Form validation
from finmodel.numerical import SimulationResult, simulate  # Core 1D numerical + analytical solver
from finmodel.presets import export_numerical_preset, import_numerical_preset  # JSON preset converters
from finmodel.results_tables import output_frames  # Tabular DataFrame builders for results display
from finmodel.saved_cases import SAVED_CASES, get_saved_case_names, load_saved_case  # Pre-programmed teaching cases


# Directory containing `app.py`.
ROOT = Path(__file__).resolve().parent

# Set of base Streamlit widget keys belonging to the numerical input form.
# When a student loads a saved case, uploads a JSON file, or adds/removes a section,
# these keys are cleared and given a new epoch suffix so every widget refreshes cleanly.
NUMERICAL_WIDGET_KEYS = {
    "method", "duration", "step", "interval", "shape", "width", "thickness",
    "diameter", "width_base", "thick_base", "width_top", "thick_top",
    "diam_base", "diam_top", "default_k", "min_k", "max_k", "left_type",
    "left_temp", "left_h", "left_air", "left_rad", "left_sur",
    "right_type", "right_temp", "right_h", "right_air", "right_rad",
    "right_sur", "lateral_flux", "lateral_h", "lateral_air", "lateral_rad",
    "lateral_sur", "initial_type", "initial_uniform", "initial_cells",
    "graph_cells", "graph_faces", "graph_percent", "remove_section", "graph_defaults",
}

# Configure the browser tab title and wide two-column page layout.
st.set_page_config(page_title="Fin heat transfer", layout="wide")
# Schema version counter for `st.session_state.fin_config`.
NUMERICAL_UI_VERSION = 3


def _clear_numerical_widgets() -> None:
    """Clear cached widget values in `st.session_state` and increment the widget epoch counter."""
    # Iterate over a snapshot of all current keys in `st.session_state`.
    for key in list(st.session_state):
        # Strip any trailing `__<epoch>` suffix to recover the base widget key name.
        base = re.sub(r"__\d+$", "", key)
        # Delete the key if it is a top-level numerical widget or a section widget (`d0_length`, `d1_k`, etc.).
        if base in NUMERICAL_WIDGET_KEYS or re.fullmatch(r"d\d+_.+", base):
            del st.session_state[key]
    # Increment `numerical_widget_epoch` so `numerical_key()` generates fresh widget identities on the next rerun.
    st.session_state.numerical_widget_epoch = st.session_state.get("numerical_widget_epoch", 0) + 1


def numerical_key(key: str) -> str:
    """Return the versioned Streamlit widget key for `key` under the current `numerical_widget_epoch`."""
    # Analytical keys (if any) are returned unchanged.
    if key.startswith("analytical_"):
        return key
    # Read the current widget epoch (0 on initial page load, >0 after preset/section changes).
    epoch = st.session_state.get("numerical_widget_epoch", 0)
    # Use the bare `key` at epoch 0, or `f"{key}__{epoch}"` after a structural reload.
    return key if epoch == 0 else f"{key}__{epoch}"


def numeric(label: str, value: float | None, *, key: str, help: str | None = None) -> float | None:
    """Render a floating-point number input widget (`st.number_input`) that allows `None` when blank."""
    # Render the Streamlit number input with 12 significant digits (`%.12g`) to avoid rounding small resistances.
    entered = st.number_input(
        label,
        value=None if value is None else float(value),
        format="%.12g",
        key=numerical_key(key),
        help=help,
        placeholder="Enter a value",
    )
    # Return `None` if the field is empty, or its `float` value otherwise.
    return None if entered is None else float(entered)


def integer(label: str, value: int | None, *, key: str, min_value: int = 1) -> int | None:
    """Render an integer number input widget (`st.number_input` with `step=1`) that allows `None` when blank."""
    # Render the integer input widget with lower bound `min_value` (default 1 cell).
    entered = st.number_input(
        label,
        min_value=min_value,
        value=None if value is None else max(min_value, int(value)),
        step=1,
        key=numerical_key(key),
        placeholder="Enter a count",
    )
    # Return `None` if blank, or the integer value otherwise.
    return None if entered is None else int(entered)


def choose(label: str, options: list[str], value: str | None, *, key: str) -> str:
    """Render a dropdown selector (`st.selectbox`) that starts unselected (`index=None`) when `value` is empty."""
    # Find the 0-based index of `value` in `options`, or `None` if `value` is not in `options`.
    index = options.index(value) if value in options else None
    # Render the selectbox and return `""` when unselected.
    return st.selectbox(label, options, index=index, key=numerical_key(key), placeholder="Choose an option") or ""


def series_editor(label: str, values: list[dict], columns: list[str], key: str) -> list[dict]:
    """Render a dynamic add/delete table editor (`st.data_editor`) for resistances or initial cell temperatures."""
    # Convert the list of row dictionaries into a pandas DataFrame with the specified `columns`.
    frame = pd.DataFrame(values, columns=columns)
    # Render the interactive table editor with dynamic row addition/deletion (`num_rows="dynamic"`).
    edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, key=numerical_key(key), width="stretch")
    # Collect non-empty rows back into a list of float-valued dictionaries.
    records: list[dict] = []
    for row in edited.to_dict("records"):
        # Skip completely blank rows that the student added but has not filled in yet.
        if all(pd.isna(row.get(column)) for column in columns):
            continue
        # If a row is only partially filled (e.g. distance entered without resistance value), raise an error.
        if any(pd.isna(row.get(column)) for column in columns):
            raise ValueError(f"{label}: complete or delete each row")
        # Convert all columns in the completed row to `float` and append.
        records.append({column: float(row[column]) for column in columns})
    # Return the validated list of row dictionaries.
    return records


def numerical_input_form(config: dict) -> tuple[dict, str, int | None]:
    """Render the complete numerical configuration form and return `(updated_config, action, remove_index)`."""
    # Work on a deep copy of `config` so we can detect whether any widget value changed on this rerun.
    updated = copy.deepcopy(config)
    # Shorthand references to the `controls`, `geometry`, and `temperature` sub-dictionaries.
    c, g, t = updated["controls"], updated["geometry"], updated["temperature"]
    with st.container():
        # 1. Cross-sectional dimension inputs (conditioned on the chosen `shape`):
        if g["shape"] == "Rectangular":
            # Rectangular fin requires Width W (mm) and Thickness t (mm).
            a, b = st.columns(2)
            with a:
                g["width_mm"] = numeric("Width (mm)", g["width_mm"], key="width")
            with b:
                g["thickness_mm"] = numeric("Thickness (mm)", g["thickness_mm"], key="thickness")
        elif g["shape"] == "Cylindrical":
            # Cylindrical pin fin requires Diameter D (mm).
            g["diameter_mm"] = numeric("Diameter (mm)", g["diameter_mm"], key="diameter")
        elif g["shape"] == "Pyramidal":
            # Pyramidal fin requires Base width/thickness (at x = 0) and Top width/thickness (at x = L).
            a, b, d, e = st.columns(4)
            with a:
                g["width_base_mm"] = numeric("Base width (mm)", g["width_base_mm"], key="width_base")
            with b:
                g["thickness_base_mm"] = numeric("Base thickness (mm)", g["thickness_base_mm"], key="thick_base")
            with d:
                g["width_top_mm"] = numeric("Top width (mm)", g["width_top_mm"], key="width_top")
            with e:
                g["thickness_top_mm"] = numeric("Top thickness (mm)", g["thickness_top_mm"], key="thick_top")
        elif g["shape"] == "Conical":
            # Conical fin requires Base diameter (at x = 0) and Top diameter (at x = L).
            a, b = st.columns(2)
            with a:
                g["diameter_base_mm"] = numeric("Base diameter (mm)", g["diameter_base_mm"], key="diam_base")
            with b:
                g["diameter_top_mm"] = numeric("Top diameter (mm)", g["diameter_top_mm"], key="diam_top")
        else:
            # Prompt the student to pick a fin shape first when `shape` is blank.
            st.info("Choose a fin shape to show its dimensions.")

        # 2. Time-discretisation and simulation timing controls:
        st.subheader("Simulation")
        a, b, d, e = st.columns(4)
        with a:
            # Choose `"imp"` (Implicit backward-Euler) or `"exp"` (Explicit forward-Euler).
            c["method"] = choose("Time discretisation", ["imp", "exp"], c["method"], key="method")
        with b:
            # Total physical duration of the simulation (s).
            c["duration_s"] = numeric("Duration (s)", c["duration_s"], key="duration")
        with d:
            # Numerical integration time step Δt (s).
            c["time_step_s"] = numeric("Time step (s)", c["time_step_s"], key="step")
        with e:
            # Time interval Δt_print (s) between saved output rows.
            c["output_interval_s"] = numeric("Output interval (s)", c["output_interval_s"], key="interval")

        # 3. Axial sections (`domains`) and their thermophysical/radiative/resistance properties:
        st.subheader("Sections")
        for index, domain in enumerate(updated["domains"]):
            # Prefix `d0`, `d1`, ... for widget keys belonging to section `index`.
            k = f"d{index}"
            with st.expander(f"Section {index + 1}", expanded=index == 0):
                # Row 1: Section length (mm), gas-gap toggle, and cell count N_i.
                a, b, d, e = st.columns(4)
                with a:
                    domain["length_mm"] = numeric("Length (mm)", domain["length_mm"], key=f"{k}_length")
                with b:
                    domain["gas_gap"] = st.checkbox("Gas gap", value=bool(domain["gas_gap"]), key=numerical_key(f"{k}_gas"))
                with d:
                    if domain["gas_gap"]:
                        # Gas gaps always use 1 control volume, so lock the displayed count to 1.
                        domain["n_cells"] = 1
                        st.number_input("Cells", value=1, disabled=True, key=numerical_key(f"{k}_cells_fixed"))
                    else:
                        domain["n_cells"] = integer("Cells", domain["n_cells"], key=f"{k}_cells")
                with e:
                    if domain["gas_gap"]:
                        st.caption("Gas gaps always use 1 cell.")
                # Row 2: Conductivity k (W/m·K), specific heat c_p (J/kg·K), density ρ (kg/m³), and heat generation q̇_gen (W/m³).
                a, b, d, e = st.columns(4)
                with a:
                    domain["conductivity"] = numeric("Conductivity (W/m K)", domain["conductivity"], key=f"{k}_k")
                with b:
                    domain["thermal_capacity"] = numeric("Specific heat (J/kg K)", domain["thermal_capacity"], key=f"{k}_cp")
                with d:
                    domain["density"] = numeric("Density (kg/m³)", domain["density"], key=f"{k}_rho")
                with e:
                    domain["heat_generated"] = numeric("Heat generation (W/m³)", domain["heat_generated"], key=f"{k}_heat")
                # Row 3: Lateral and left-face radiative emissivities and absorptivities.
                a, b, d, e = st.columns(4)
                with a:
                    domain["lateral_emissivity"] = numeric("Lateral emissivity", domain["lateral_emissivity"], key=f"{k}_le")
                with b:
                    domain["lateral_absorptivity"] = numeric("Lateral absorptivity", domain["lateral_absorptivity"], key=f"{k}_la")
                with d:
                    domain["left_emissivity"] = numeric("Left emissivity", domain["left_emissivity"], key=f"{k}_left_e")
                with e:
                    domain["left_absorptivity"] = numeric("Left absorptivity", domain["left_absorptivity"], key=f"{k}_left_a")
                # Row 4: Right-face radiative emissivity and absorptivity.
                a, b = st.columns(2)
                with a:
                    domain["right_emissivity"] = numeric("Right emissivity", domain["right_emissivity"], key=f"{k}_right_e")
                with b:
                    domain["right_absorptivity"] = numeric("Right absorptivity", domain["right_absorptivity"], key=f"{k}_right_a")
                if domain["gas_gap"]:
                    # Row 5: Gas-gap bounding wall emissivities and absorptivities (shown only when `gas_gap` is checked).
                    st.markdown("Gas gap face properties")
                    a, b, d, e = st.columns(4)
                    with a:
                        domain["gas_face_emissivity_left"] = numeric("Left face emissivity", domain["gas_face_emissivity_left"], key=f"{k}_gfel")
                    with b:
                        domain["gas_face_emissivity_right"] = numeric("Right face emissivity", domain["gas_face_emissivity_right"], key=f"{k}_gfer")
                    with d:
                        domain["gas_face_absorptivity_left"] = numeric("Left face absorptivity", domain["gas_face_absorptivity_left"], key=f"{k}_gfal")
                    with e:
                        domain["gas_face_absorptivity_right"] = numeric("Right face absorptivity", domain["gas_face_absorptivity_right"], key=f"{k}_gfar")
                # Row 6: Dynamic table editor for local thermal contact resistances inside this section.
                st.markdown("Local thermal resistances")
                domain["resistances"] = series_editor(
                    "Local resistances", domain["resistances"], ["distance_mm", "value"], key=f"{k}_resistances"
                )
                st.caption("Distance is measured from the start of this section, in mm. Resistance is in m² K/W.")

        # Section management buttons (`Add section` and, when >1 section exists, `Remove section`):
        remove_index: int | None = None
        remove_clicked = False
        if len(updated["domains"]) > 1:
            a, b, d = st.columns([2, 1, 1])
            with a:
                remove_index = st.selectbox(
                    "Section to remove",
                    list(range(1, len(updated["domains"]) + 1)),
                    index=len(updated["domains"]) - 1,
                    key=numerical_key("remove_section"),
                )
            with b:
                remove_clicked = st.button("Remove section")
            with d:
                add_clicked = st.button("Add section")
        else:
            add_clicked = st.button("Add section")

        # 4. Temperature limits and boundary conditions (Left end, Right end, Lateral surface):
        st.subheader("Temperature and boundaries")
        a, b = st.columns(2)
        with a:
            t["minimum_k"] = numeric("Minimum value (K)", t["minimum_k"], key="min_k", help="Warns if computed temperatures fall below this value.")
        with b:
            t["maximum_k"] = numeric("Maximum value (K)", t["maximum_k"], key="max_k", help="Warns if computed temperatures exceed this value.")
        a, b, d = st.columns(3)
        with a:
            # Left boundary (x = 0): `Temp` (imposed T), `HeatFlux` (convection + optional radiation), or `NoHeatFlux` (insulated).
            st.markdown("**Left end**")
            t["left_boundary"] = choose("Left boundary", ["Temp", "HeatFlux", "NoHeatFlux"], t["left_boundary"], key="left_type")
            if t["left_boundary"] == "Temp":
                t["left_temperature_k"] = numeric("Left imposed temperature (K)", t["left_temperature_k"], key="left_temp")
            elif t["left_boundary"] == "HeatFlux":
                t["left_convection"] = numeric("Left convection coefficient (W/m² K)", t["left_convection"], key="left_h")
                t["left_air_temperature_k"] = numeric("Left air temperature (K)", t["left_air_temperature_k"], key="left_air")
                t["left_radiation"] = st.checkbox("Left radiation", value=bool(t["left_radiation"]), key=numerical_key("left_rad"))
                if t["left_radiation"]:
                    t["left_surround_k"] = numeric("Left surroundings (K)", t["left_surround_k"], key="left_sur")
        with b:
            # Right boundary (x = L): `Temp` (imposed T), `HeatFlux` (convection + optional radiation), or `NoHeatFlux` (insulated).
            st.markdown("**Right end**")
            t["right_boundary"] = choose("Right boundary", ["Temp", "HeatFlux", "NoHeatFlux"], t["right_boundary"], key="right_type")
            if t["right_boundary"] == "Temp":
                t["right_temperature_k"] = numeric("Right imposed temperature (K)", t["right_temperature_k"], key="right_temp")
            elif t["right_boundary"] == "HeatFlux":
                t["right_convection"] = numeric("Right convection coefficient (W/m² K)", t["right_convection"], key="right_h")
                t["right_air_temperature_k"] = numeric("Right air temperature (K)", t["right_air_temperature_k"], key="right_air")
                t["right_radiation"] = st.checkbox("Right radiation", value=bool(t["right_radiation"]), key=numerical_key("right_rad"))
                if t["right_radiation"]:
                    t["right_surround_k"] = numeric("Right surroundings (K)", t["right_surround_k"], key="right_sur")
        with d:
            # Lateral perimeter surface: optional lateral convection and optional lateral thermal radiation.
            st.markdown("**Lateral surface**")
            t["lateral_flux"] = st.checkbox("Compute lateral convection", value=bool(t["lateral_flux"]), key=numerical_key("lateral_flux"))
            if t["lateral_flux"]:
                t["lateral_convection"] = numeric("Lateral convection (W/m² K)", t["lateral_convection"], key="lateral_h")
                t["lateral_air_temperature_k"] = numeric("Lateral air temperature (K)", t["lateral_air_temperature_k"], key="lateral_air")
            t["lateral_radiation"] = st.checkbox("Lateral radiation", value=bool(t["lateral_radiation"]), key=numerical_key("lateral_rad"))
            if t["lateral_radiation"]:
                t["lateral_surround_k"] = numeric("Lateral surroundings (K)", t["lateral_surround_k"], key="lateral_sur")

        # 5. Initial temperature distribution at t = 0 s (`DEFAULT`, `DEFCELL`, or per-cell `CELL`):
        st.subheader("Initial distribution")
        t["initial_type"] = choose("Initialization", ["DEFAULT", "DEFCELL", "CELL"], t["initial_type"], key="initial_type")
        if t["initial_type"] in {"DEFAULT", "CELL"}:
            t["default_k"] = numeric("Default temperature (K)", t["default_k"], key="default_k")
        if t["initial_type"] == "DEFCELL":
            t["initial_uniform_k"] = numeric("Uniform initial temperature (K)", t["initial_uniform_k"], key="initial_uniform")
        if t["initial_type"] == "CELL":
            st.caption("Cells without a listed temperature use the default temperature.")
            t["initial_cells"] = series_editor("Initial cell temperatures", t["initial_cells"], ["cell", "value_k"], key="initial_cells")

        # 6. Graph selection controls (which cells to plot vs. time, and which time percentages to plot vs. x):
        st.subheader("Graph selections")
        st.caption("Temperature histories use cell centres, heat-rate histories use faces. Profile percentages select a saved row; 100% is the last saved row.")
        graph = updated["graphs"]
        cells_text = st.text_input("Time series at cells (comma separated)", value=", ".join(map(str, graph["cells"])), key=numerical_key("graph_cells"))
        faces_text = st.text_input(
            "Heat rate at faces (comma separated)", value=", ".join(map(str, graph.get("faces", []))),
            key=numerical_key("graph_faces"),
            help="Face 1 is the left end; face N+1 is the right end. Leave blank to reuse the cell indices.",
        )
        percent_text = st.text_input("Longitudinal profiles at percentages (comma separated)", value=", ".join(map(str, graph["percentages"])), key=numerical_key("graph_percent"))
        preset_clicked = st.button("Use default graph selections", key=numerical_key("graph_defaults"), help="Populate default cell indices and time percentages for the output plots.")
        run_clicked = st.button("Run numerical simulation", type="primary")

    # Determine which button action (if any) triggered this script rerun.
    action = "run" if run_clicked else "add" if add_clicked else "remove" if remove_clicked else "defaults" if preset_clicked else ""
    if action == "defaults":
        # Populate standard default graph selections.
        n = sum(1 if d["gas_gap"] else (d["n_cells"] or 0) for d in updated["domains"])
        graph["cells"] = [cell for cell in (1, 5, 10) if cell <= n]
        graph["faces"] = [1, n + 1] if n else []
        graph["percentages"] = [0, 10, 20, 50, 80, 100]
    else:
        # Parse comma-separated cell indices and time percentages entered by the student.
        graph["cells"] = [float(item.strip()) for item in cells_text.split(",") if item.strip()]
        if faces_text.strip():
            graph["faces"] = [float(item.strip()) for item in faces_text.split(",") if item.strip()]
        else:
            graph.pop("faces", None)
        graph["percentages"] = [float(item.strip()) for item in percent_text.split(",") if item.strip()]
    # Round cell numbers in `initial_cells` to integers.
    for item in t["initial_cells"]:
        item["cell"] = int(round(item["cell"]))
    # Return the updated configuration dictionary, button action string, and optional section index to remove.
    return updated, action, remove_index


def _plot(
    title: str,
    x_title: str,
    y_title: str,
    series: list[tuple[str, list[float | None], list[float | None]]],
    y_bounds: tuple[float, float] | None = None,
    x_bounds: tuple[float, float] | None = None,
    height_px: int = 350,
    key: str | None = None,
) -> None:
    """Render a 2D Plotly line chart, styling any `"Analytical"` series with a dashed line."""
    # Create an empty Plotly figure.
    fig = go.Figure()
    # Add each `(name, x, y)` curve as a `go.Scatter` line trace.
    for name, x, y in series:
        # Identify whether this curve is the 1D steady-state analytical reference (`"Analytical"` or `"Analytical @Cell ..."`).
        is_analytical = name == "Analytical" or name.startswith("Analytical ")
        # Render analytical curves with a dashed line (`dash="dash"`) so they remain visible when overlapped by the numerical curve.
        line = dict(dash="dash") if is_analytical else None
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=name, line=line))
    # Set chart title, axis titles, pixel height, and compact margins.
    fig.update_layout(title=title, xaxis_title=x_title, yaxis_title=y_title, height=height_px, margin=dict(l=20, r=20, t=50, b=20))
    # Apply explicit horizontal and vertical axis bounds if supplied.
    if x_bounds is not None:
        fig.update_xaxes(range=list(x_bounds))
    if y_bounds is not None:
        fig.update_yaxes(range=list(y_bounds))
    # Render the interactive Plotly chart in Streamlit.
    st.plotly_chart(fig, width="stretch", key=key)


def numerical_results(config: dict, result: SimulationResult) -> None:
    """Render the four output tabs (`Graphs`, `3D animation`, `Tables`, `Export`) for `result`."""
    # Display summary header with cell count and printed time step counts.
    st.subheader("Numerical results")
    st.caption(f"{result.grid.n} cells · {len(result.times_s)} printed temperature times · {len(result.heat_flux_times_s)} printed heat flux times")
    st.caption(
        f"Requested duration: {config['controls']['duration_s']:.12g} s · "
        f"Final computed time: {result.final_computed_time_s:.12g} s · "
        f"Last saved time: {result.times_s[-1]:.12g} s"
    )
    if len(result.times_s) == 1:
        st.warning("Only the initial state was saved. Reduce the output interval to save transient results.")
    # Display any temperature threshold warnings recorded during the simulation.
    for warning in getattr(result, "warnings", ()):
        st.warning(warning)
    # Create the four output presentation tabs.
    tab_charts, tab_3d, tab_tables, tab_export = st.tabs(["Graphs", "3D animation", "Tables", "Export"])
    with tab_charts:
        # Build all 2D chart specifications (including the overlaid analytical curves).
        from finmodel.visualization import build_chart_specs

        st.caption(result.analytical_note)
        if result.explicit_warning_cell is not None and len(result.explicit_preview_rows) >= 2:
            cell = result.explicit_warning_cell
            rows = result.explicit_preview_rows
            st.caption("These consecutive steps show the onset of possible instability, separately from the full run's scale.")
            _plot(
                f"Early explicit steps at cell {cell}", "Time (s)", "Temperature (K)",
                [(f"Cell {cell}", [result.times_s[i] for i in rows], [result.temperature_k[i][cell - 1] for i in rows])],
                key="explicit_stability_preview",
            )
        try:
            specs = build_chart_specs(result, config["graphs"])
        except ValueError as exc:
            st.error(f"Graph selection: {exc}. The simulation results are available in Tables and Export.")
            specs = None
        if specs == []:
            st.info("Select at least one cell, face or profile percentage to display graphs.")
        # Partition into left-column Temperature charts and right-column CrossSectionHeatFlux charts.
        temperature_specs = [spec for spec in specs or [] if spec.property_name == "Temperature"]
        flux_specs = [spec for spec in specs or [] if spec.property_name == "CrossSectionHeatFlux"]
        # Render paired Temperature (left) and CrossSectionHeatFlux (right) charts side by side.
        for index, (left, right) in enumerate(zip_longest(temperature_specs, flux_specs)):
            a, b = st.columns(2)
            if left is not None:
                with a:
                    _plot(left.title, left.x_axis, left.y_axis,
                          [(s.name, list(s.x_values), list(s.y_values)) for s in left.series],
                          y_bounds=(left.y_min, left.y_max),
                          x_bounds=(left.x_min, left.x_max),
                          height_px=left.height_px, key=f"temperature_chart_{index}")
            if right is not None:
                with b:
                    _plot(right.title, right.x_axis, right.y_axis,
                          [(s.name, list(s.x_values), list(s.y_values)) for s in right.series],
                          y_bounds=(right.y_min, right.y_max),
                          x_bounds=(right.x_min, right.x_max),
                          height_px=right.height_px, key=f"flux_chart_{index}")
    with tab_3d:
        # Render the rotatable 3D fin mesh with Play/Pause buttons and time slider.
        st.caption("Rotate the fin with the mouse. Use Play, Pause, or the time slider to inspect each printed temperature state.")
        try:
            if result.explicit_warning_cell is not None:
                raise ValueError("Use Graphs to inspect the explicit stability demonstration.")
            st.plotly_chart(build_3d_fin_figure(result, config), width="stretch", key="fin_3d")
            # Both controls use the same iframe to access the rendered Plotly chart.
            st.iframe(gif_download_html(), height=78)
        except ValueError as exc:
            st.warning(f"3D view unavailable: {exc}")
    # Build the `temperature`, `flux`, and `grid` pandas DataFrames for the Tables and Export tabs.
    temp, flux, grid = output_frames(result)
    with tab_tables:
        # Display the finite-volume grid table, printed temperature history, and printed face heat-flux history.
        st.markdown("**Grid**")
        st.dataframe(grid, hide_index=True, width="stretch")
        st.markdown("**Temperature (K)**")
        st.dataframe(temp, hide_index=True, width="stretch")
        st.markdown("**Cross section heat flux (W)**")
        st.dataframe(flux, hide_index=True, width="stretch")
    with tab_export:
        # Provide one-click CSV downloads for tables and JSON downloads for inputs and full results.
        st.download_button("Temperature CSV", temp.to_csv(index=False), file_name="temperature.csv", mime="text/csv")
        st.download_button("Cross section heat flux CSV", flux.to_csv(index=False), file_name="cross_section_heat_flux.csv", mime="text/csv")
        st.download_button("Grid CSV", grid.to_csv(index=False), file_name="grid.csv", mime="text/csv")
        st.download_button("Inputs JSON", json.dumps(export_numerical_preset(config), indent=2), file_name="fin_inputs.json", mime="application/json")
        st.download_button("Results JSON", json.dumps(result.as_dict(), indent=2), file_name="fin_results.json", mime="application/json")


def numerical_page() -> None:
    """Main page controller managing session state, saved cases, JSON uploads, form edits, and simulation runs."""
    # Initialize or migrate `st.session_state` on first load or when `NUMERICAL_UI_VERSION` changes.
    if st.session_state.get("numerical_ui_version") != NUMERICAL_UI_VERSION:
        had_previous_inputs = "fin_config" in st.session_state
        st.session_state.numerical_ui_version = NUMERICAL_UI_VERSION
        st.session_state.fin_config = empty_numerical_config()
        st.session_state.pop("fin_result", None)
        st.session_state.pop("fin_error", None)
        st.session_state.pop("json_upload", None)
        st.session_state.pop("loaded_json_hash", None)
        st.session_state.pop("saved_case", None)
        st.session_state.pop("loaded_saved_case", None)
        if had_previous_inputs:
            _clear_numerical_widgets()
        else:
            st.session_state.numerical_widget_epoch = 0
    elif "fin_config" not in st.session_state:
        st.session_state.fin_config = empty_numerical_config()

    # Page header and explanatory caption.
    st.title("Fin heat transfer: numerical solution")
    st.caption(
        "Enter numerical inputs, choose one of the saved cases, or load a saved JSON file, then run the simulation. "
        "The graphs include a simplified steady-state reference when one is available."
    )

    # Top Loader Option 1: Select one of the 14 pre-programmed teaching cases (`Case 1` .. `Case 14`).
    selected_case = st.selectbox(
        "Load saved cases",
        get_saved_case_names(),
        index=None,
        key="saved_case",
        placeholder="Choose a saved case",
    )
    # Whenever the user selects a different saved case, load its configuration and refresh all form widgets.
    if selected_case != st.session_state.get("loaded_saved_case"):
        st.session_state.loaded_saved_case = selected_case
        if selected_case is not None:
            st.session_state.fin_config = load_saved_case(selected_case)
            st.session_state.pop("fin_result", None)
            st.session_state.pop("fin_error", None)
            _clear_numerical_widgets()
            st.rerun()
    # Show the pedagogical description of the currently selected saved case.
    if selected_case in SAVED_CASES:
        st.info(SAVED_CASES[selected_case].description)

    # Top Loader Option 2: Upload a saved JSON configuration file.
    saved_inputs = st.file_uploader("Load saved inputs JSON", type=["json"], key="json_upload")
    if saved_inputs is not None:
        # Compute SHA-256 hash of the uploaded bytes so we only import when a new file is uploaded.
        digest = hashlib.sha256(saved_inputs.getvalue()).hexdigest()
        if st.session_state.get("loaded_json_hash") != digest:
            try:
                loaded = json.loads(saved_inputs.getvalue().decode("utf-8-sig"))
                st.session_state.fin_config = import_numerical_preset(loaded)
                st.session_state.loaded_json_hash = digest
                st.session_state.pop("saved_case", None)
                st.session_state.pop("loaded_saved_case", None)
                st.session_state.pop("fin_result", None)
                st.session_state.pop("fin_error", None)
                _clear_numerical_widgets()
                st.rerun()
            except (ValueError, UnicodeError, KeyError) as exc:
                st.error(str(exc))

    # Fin shape selector (`Rectangular`, `Cylindrical`, `Pyramidal`, or `Conical`).
    st.subheader("Fin geometry")
    shape = choose(
        "Shape",
        ["Rectangular", "Cylindrical", "Pyramidal", "Conical"],
        st.session_state.fin_config["geometry"]["shape"],
        key="shape",
    )
    # Clear stale results if the user switches the fin geometry shape.
    if shape != st.session_state.fin_config["geometry"]["shape"]:
        st.session_state.fin_config["geometry"]["shape"] = shape
        st.session_state.pop("fin_result", None)
        st.session_state.pop("fin_error", None)

    # Render the main input form and handle button actions (`add`, `remove`, `defaults`, `run`) or live edits.
    try:
        updated, action, remove_index = numerical_input_form(st.session_state.fin_config)
        if action == "add":
            # Append a new blank section to `domains` and refresh widget identities.
            updated["domains"].append(blank_section())
            st.session_state.fin_config = updated
            st.session_state.pop("fin_result", None)
            st.session_state.pop("fin_error", None)
            _clear_numerical_widgets()
            st.rerun()
        elif action == "remove" and remove_index is not None:
            # Remove the selected 1-based section index (`remove_index - 1`) and refresh widget identities.
            updated["domains"].pop(remove_index - 1)
            st.session_state.fin_config = updated
            st.session_state.pop("fin_result", None)
            st.session_state.pop("fin_error", None)
            _clear_numerical_widgets()
            st.rerun()
        elif action == "defaults":
            # Apply the default Excel graph selections (`cells = [1, 5, 10]`, `percentages = [0, 10, 20, 50, 80, 100]`).
            st.session_state.fin_config = updated
            st.session_state.pop("fin_result", None)
            st.session_state.pop("fin_error", None)
            _clear_numerical_widgets()
            st.rerun()
        elif action == "run":
            # Validate all active inputs and run the numerical + analytical simulation.
            prepared = prepare_numerical_config(updated)
            result = simulate(prepared)
            st.session_state.fin_config = updated
            st.session_state.fin_result = result
            st.session_state.pop("fin_error", None)
        elif updated != st.session_state.fin_config:
            # Any input change invalidates previously computed results until the student clicks Run again.
            st.session_state.fin_config = updated
            st.session_state.pop("fin_result", None)
            st.session_state.pop("fin_error", None)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        # Capture validation or numerical errors and display them cleanly to the student.
        st.session_state.fin_error = str(exc)
        st.session_state.pop("fin_result", None)

    # Display any active validation/solver error message.
    if "fin_error" in st.session_state:
        st.error(st.session_state.fin_error)
    # Display the simulation results tabs whenever a valid `fin_result` is present in session state.
    if "fin_result" in st.session_state:
        numerical_results(st.session_state.fin_config, st.session_state.fin_result)


# Execute the unified numerical + analytical page on every Streamlit script run.
numerical_page()
