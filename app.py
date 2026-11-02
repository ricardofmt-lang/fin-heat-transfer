"""Local Streamlit front end for the supplied fin workbooks."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from finmodel.animation import build_3d_fin_figure
from finmodel.empty_inputs import blank_section, empty_numerical_config, prepare_numerical_config
from finmodel.numerical import SimulationResult, simulate
from finmodel.presets import export_numerical_preset, import_numerical_preset
from finmodel.results_tables import output_frames
from finmodel.saved_cases import SAVED_CASES, get_saved_case_names, load_saved_case


ROOT = Path(__file__).resolve().parent

NUMERICAL_WIDGET_KEYS = {
    "method", "duration", "step", "interval", "shape", "width", "thickness",
    "diameter", "width_base", "thick_base", "width_top", "thick_top",
    "diam_base", "diam_top", "default_k", "min_k", "max_k", "left_type",
    "left_temp", "left_h", "left_air", "left_rad", "left_sur",
    "right_type", "right_temp", "right_h", "right_air", "right_rad",
    "right_sur", "lateral_flux", "lateral_h", "lateral_air", "lateral_rad",
    "lateral_sur", "initial_type", "initial_uniform", "initial_cells",
    "graph_cells", "graph_percent", "remove_section", "graph_defaults",
}

st.set_page_config(page_title="Fin heat transfer", layout="wide")
NUMERICAL_UI_VERSION = 3


def _clear_numerical_widgets() -> None:
    """Rehydrate widgets from a newly imported or structurally changed model."""
    for key in list(st.session_state):
        base = re.sub(r"__\d+$", "", key)
        if base in NUMERICAL_WIDGET_KEYS or re.fullmatch(r"d\d+_.+", base):
            del st.session_state[key]
    st.session_state.numerical_widget_epoch = st.session_state.get("numerical_widget_epoch", 0) + 1


def numerical_key(key: str) -> str:
    """Give new inputs fresh widget identities after a preset or section change."""
    if key.startswith("analytical_"):
        return key
    epoch = st.session_state.get("numerical_widget_epoch", 0)
    return key if epoch == 0 else f"{key}__{epoch}"


def numeric(label: str, value: float | None, *, key: str, help: str | None = None) -> float | None:
    entered = st.number_input(
        label,
        value=None if value is None else float(value),
        format="%.12g",
        key=numerical_key(key),
        help=help,
        placeholder="Enter a value",
    )
    return None if entered is None else float(entered)


def integer(label: str, value: int | None, *, key: str, min_value: int = 1) -> int | None:
    entered = st.number_input(
        label,
        min_value=min_value,
        value=None if value is None else max(min_value, int(value)),
        step=1,
        key=numerical_key(key),
        placeholder="Enter a count",
    )
    return None if entered is None else int(entered)


def choose(label: str, options: list[str], value: str | None, *, key: str) -> str:
    index = options.index(value) if value in options else None
    return st.selectbox(label, options, index=index, key=numerical_key(key), placeholder="Choose an option") or ""


def series_editor(label: str, values: list[dict], columns: list[str], key: str) -> list[dict]:
    frame = pd.DataFrame(values, columns=columns)
    edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, key=numerical_key(key), width="stretch")
    records: list[dict] = []
    for row in edited.to_dict("records"):
        if all(pd.isna(row.get(column)) for column in columns):
            continue
        if any(pd.isna(row.get(column)) for column in columns):
            raise ValueError(f"{label}: complete or delete each row")
        records.append({column: float(row[column]) for column in columns})
    return records


def numerical_input_form(config: dict) -> tuple[dict, str, int | None]:
    updated = copy.deepcopy(config)
    c, g, t = updated["controls"], updated["geometry"], updated["temperature"]
    with st.container():
        if g["shape"] == "Rectangular":
            a, b = st.columns(2)
            with a:
                g["width_mm"] = numeric("Width (mm)", g["width_mm"], key="width")
            with b:
                g["thickness_mm"] = numeric("Thickness (mm)", g["thickness_mm"], key="thickness")
        elif g["shape"] == "Cylindrical":
            g["diameter_mm"] = numeric("Diameter (mm)", g["diameter_mm"], key="diameter")
        elif g["shape"] == "Pyramidal":
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
            a, b = st.columns(2)
            with a:
                g["diameter_base_mm"] = numeric("Base diameter (mm)", g["diameter_base_mm"], key="diam_base")
            with b:
                g["diameter_top_mm"] = numeric("Top diameter (mm)", g["diameter_top_mm"], key="diam_top")
        else:
            st.info("Choose a fin shape to show its dimensions.")

        st.subheader("Simulation")
        a, b, d, e = st.columns(4)
        with a:
            c["method"] = choose("Time discretisation", ["imp", "exp"], c["method"], key="method")
        with b:
            c["duration_s"] = numeric("Duration (s)", c["duration_s"], key="duration")
        with d:
            c["time_step_s"] = numeric("Time step (s)", c["time_step_s"], key="step")
        with e:
            c["output_interval_s"] = numeric("Output interval (s)", c["output_interval_s"], key="interval")

        st.subheader("Sections")
        for index, domain in enumerate(updated["domains"]):
            k = f"d{index}"
            with st.expander(f"Section {index + 1}", expanded=index == 0):
                a, b, d, e = st.columns(4)
                with a:
                    domain["length_mm"] = numeric("Length (mm)", domain["length_mm"], key=f"{k}_length")
                with b:
                    domain["gas_gap"] = st.checkbox("Gas gap", value=bool(domain["gas_gap"]), key=numerical_key(f"{k}_gas"))
                with d:
                    domain["n_cells"] = integer("Cells", domain["n_cells"], key=f"{k}_cells")
                with e:
                    st.caption("Gas gaps use one cell, as in Excel.")
                a, b, d, e = st.columns(4)
                with a:
                    domain["conductivity"] = numeric("Conductivity (W/m K)", domain["conductivity"], key=f"{k}_k")
                with b:
                    domain["thermal_capacity"] = numeric("Specific heat (J/kg K)", domain["thermal_capacity"], key=f"{k}_cp")
                with d:
                    domain["density"] = numeric("Density (kg/m³)", domain["density"], key=f"{k}_rho")
                with e:
                    domain["heat_generated"] = numeric("Heat generation (W/m³)", domain["heat_generated"], key=f"{k}_heat")
                a, b, d, e = st.columns(4)
                with a:
                    domain["lateral_emissivity"] = numeric("Lateral emissivity", domain["lateral_emissivity"], key=f"{k}_le")
                with b:
                    domain["lateral_absorptivity"] = numeric("Lateral absorptivity", domain["lateral_absorptivity"], key=f"{k}_la")
                with d:
                    domain["left_emissivity"] = numeric("Left emissivity", domain["left_emissivity"], key=f"{k}_left_e")
                with e:
                    domain["left_absorptivity"] = numeric("Left absorptivity", domain["left_absorptivity"], key=f"{k}_left_a")
                a, b = st.columns(2)
                with a:
                    domain["right_emissivity"] = numeric("Right emissivity", domain["right_emissivity"], key=f"{k}_right_e")
                with b:
                    domain["right_absorptivity"] = numeric("Right absorptivity", domain["right_absorptivity"], key=f"{k}_right_a")
                st.markdown("Gas gap face properties (used when Gas gap is checked)")
                a, b, d, e = st.columns(4)
                with a:
                    domain["gas_face_emissivity_left"] = numeric("Left face emissivity", domain["gas_face_emissivity_left"], key=f"{k}_gfel")
                with b:
                    domain["gas_face_emissivity_right"] = numeric("Right face emissivity", domain["gas_face_emissivity_right"], key=f"{k}_gfer")
                with d:
                    domain["gas_face_absorptivity_left"] = numeric("Left face absorptivity", domain["gas_face_absorptivity_left"], key=f"{k}_gfal")
                with e:
                    domain["gas_face_absorptivity_right"] = numeric("Right face absorptivity", domain["gas_face_absorptivity_right"], key=f"{k}_gfar")
                st.markdown("Local thermal resistances")
                domain["resistances"] = series_editor(
                    "Local resistances", domain["resistances"], ["distance_mm", "value"], key=f"{k}_resistances"
                )
                st.caption("Distance is measured from the start of this section, in mm. Resistance is in m² K/W.")

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

        st.subheader("Temperature and boundaries")
        a, b = st.columns(2)
        with a:
            t["minimum_k"] = numeric("Minimum value (K)", t["minimum_k"], key="min_k", help="Warns if computed temperatures fall below this value.")
        with b:
            t["maximum_k"] = numeric("Maximum value (K)", t["maximum_k"], key="max_k", help="Warns if computed temperatures exceed this value.")
        a, b, d = st.columns(3)
        with a:
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
            st.markdown("**Lateral surface**")
            t["lateral_flux"] = st.checkbox("Compute lateral convection", value=bool(t["lateral_flux"]), key=numerical_key("lateral_flux"))
            if t["lateral_flux"]:
                t["lateral_convection"] = numeric("Lateral convection (W/m² K)", t["lateral_convection"], key="lateral_h")
                t["lateral_air_temperature_k"] = numeric("Lateral air temperature (K)", t["lateral_air_temperature_k"], key="lateral_air")
            t["lateral_radiation"] = st.checkbox("Lateral radiation", value=bool(t["lateral_radiation"]), key=numerical_key("lateral_rad"))
            if t["lateral_radiation"]:
                t["lateral_surround_k"] = numeric("Lateral surroundings (K)", t["lateral_surround_k"], key="lateral_sur")

        st.subheader("Initial distribution")
        t["initial_type"] = choose("Initialization", ["DEFAULT", "DEFCELL", "CELL"], t["initial_type"], key="initial_type")
        if t["initial_type"] in {"DEFAULT", "CELL"}:
            t["default_k"] = numeric("Default temperature (K)", t["default_k"], key="default_k")
        if t["initial_type"] == "DEFCELL":
            t["initial_uniform_k"] = numeric("Uniform initial temperature (K)", t["initial_uniform_k"], key="initial_uniform")
        if t["initial_type"] == "CELL":
            st.caption("Cells without a listed temperature use the default temperature.")
            t["initial_cells"] = series_editor("Initial cell temperatures", t["initial_cells"], ["cell", "value_k"], key="initial_cells")

        st.subheader("Graph selections")
        st.caption("Cell indices use 1 for the first cell. Profile positions are percentages of the printed simulation times.")
        graph = updated["graphs"]
        cells_text = st.text_input("Time series at cells (comma separated)", value=", ".join(map(str, graph["cells"])), key=numerical_key("graph_cells"))
        percent_text = st.text_input("Longitudinal profiles at percentages (comma separated)", value=", ".join(map(str, graph["percentages"])), key=numerical_key("graph_percent"))
        preset_clicked = st.button("Use Excel graph defaults", key=numerical_key("graph_defaults"), help="Select the cells and profile percentages in the supplied Excel workbook.")
        run_clicked = st.button("Run numerical simulation", type="primary")
    action = "run" if run_clicked else "add" if add_clicked else "remove" if remove_clicked else "defaults" if preset_clicked else ""
    if action == "defaults":
        graph["cells"] = [1, 5, 10]
        graph["percentages"] = [0, 10, 20, 50, 80, 100]
    else:
        graph["cells"] = [float(item.strip()) for item in cells_text.split(",") if item.strip()]
        graph["percentages"] = [float(item.strip()) for item in percent_text.split(",") if item.strip()]
    for item in t["initial_cells"]:
        item["cell"] = int(round(item["cell"]))
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
    fig = go.Figure()
    for name, x, y in series:
        is_analytical = name == "Analytical" or name.startswith("Analytical ")
        line = dict(dash="dash") if is_analytical else None
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=name, line=line))
    fig.update_layout(title=title, xaxis_title=x_title, yaxis_title=y_title, height=height_px, margin=dict(l=20, r=20, t=50, b=20))
    if x_bounds is not None:
        fig.update_xaxes(range=list(x_bounds))
    if y_bounds is not None:
        fig.update_yaxes(range=list(y_bounds))
    st.plotly_chart(fig, width="stretch", key=key)


def numerical_results(config: dict, result: SimulationResult) -> None:
    st.subheader("Numerical results")
    st.caption(f"{result.grid.n} cells · {len(result.times_s)} printed temperature times · {len(result.heat_flux_times_s)} printed heat flux times")
    for warning in getattr(result, "warnings", ()):
        st.warning(warning)
    tab_charts, tab_3d, tab_tables, tab_export = st.tabs(["Graphs", "3D animation", "Tables", "Export"])
    with tab_charts:
        from finmodel.visualization import build_chart_specs

        specs = build_chart_specs(result, config["graphs"])
        if not specs:
            st.info("Select at least one cell or profile percentage to display graphs.")
        temperature_specs = [spec for spec in specs if spec.property_name == "Temperature"]
        flux_specs = [spec for spec in specs if spec.property_name == "CrossSectionHeatFlux"]
        for index, (left, right) in enumerate(zip(temperature_specs, flux_specs)):
            a, b = st.columns(2)
            with a:
                _plot(left.title, left.x_axis, left.y_axis,
                      [(s.name, list(s.x_values), list(s.y_values)) for s in left.series],
                      y_bounds=(left.y_min, left.y_max),
                      x_bounds=(left.x_min, left.x_max),
                      height_px=left.height_px, key=f"temperature_chart_{index}")
            with b:
                _plot(right.title, right.x_axis, right.y_axis,
                      [(s.name, list(s.x_values), list(s.y_values)) for s in right.series],
                      y_bounds=(right.y_min, right.y_max),
                      x_bounds=(right.x_min, right.x_max),
                      height_px=right.height_px, key=f"flux_chart_{index}")
    with tab_3d:
        st.caption("Rotate the fin with the mouse. Use Play, Pause, or the time slider to inspect each printed temperature state.")
        try:
            st.plotly_chart(build_3d_fin_figure(result, config), width="stretch", key="fin_3d")
        except ValueError as exc:
            st.warning(f"3D view unavailable: {exc}")
    temp, flux, grid = output_frames(result)
    with tab_tables:
        st.markdown("**Grid**")
        st.dataframe(grid, hide_index=True, width="stretch")
        st.markdown("**Temperature (K)**")
        st.dataframe(temp, hide_index=True, width="stretch")
        st.markdown("**Cross section heat flux (W)**")
        st.dataframe(flux, hide_index=True, width="stretch")
    with tab_export:
        st.download_button("Temperature CSV", temp.to_csv(index=False), file_name="temperature.csv", mime="text/csv")
        st.download_button("Cross section heat flux CSV", flux.to_csv(index=False), file_name="cross_section_heat_flux.csv", mime="text/csv")
        st.download_button("Grid CSV", grid.to_csv(index=False), file_name="grid.csv", mime="text/csv")
        st.download_button("Inputs JSON", json.dumps(export_numerical_preset(config), indent=2), file_name="fin_inputs.json", mime="application/json")
        st.download_button("Results JSON", json.dumps(result.as_dict(), indent=2), file_name="fin_results.json", mime="application/json")


def numerical_page() -> None:
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
    st.title("Fin heat transfer: numerical solution")
    st.caption(
        "Enter numerical inputs, choose one of the saved cases, or load a saved JSON file, then run the simulation. "
        "The 1D steady-state analytical solution is automatically calculated from the same inputs and plotted on every 2D graph."
    )
    selected_case = st.selectbox(
        "Load saved cases",
        get_saved_case_names(),
        index=None,
        key="saved_case",
        placeholder="Choose a saved case",
    )
    if selected_case != st.session_state.get("loaded_saved_case"):
        st.session_state.loaded_saved_case = selected_case
        if selected_case is not None:
            st.session_state.fin_config = load_saved_case(selected_case)
            st.session_state.pop("fin_result", None)
            st.session_state.pop("fin_error", None)
            _clear_numerical_widgets()
            st.rerun()
    if selected_case in SAVED_CASES:
        st.info(SAVED_CASES[selected_case].description)
    saved_inputs = st.file_uploader("Load saved inputs JSON", type=["json"], key="json_upload")
    if saved_inputs is not None:
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
    st.subheader("Fin geometry")
    shape = choose(
        "Shape",
        ["Rectangular", "Cylindrical", "Pyramidal", "Conical"],
        st.session_state.fin_config["geometry"]["shape"],
        key="shape",
    )
    if shape != st.session_state.fin_config["geometry"]["shape"]:
        st.session_state.fin_config["geometry"]["shape"] = shape
        st.session_state.pop("fin_result", None)
        st.session_state.pop("fin_error", None)
    try:
        updated, action, remove_index = numerical_input_form(st.session_state.fin_config)
        if action == "add":
            updated["domains"].append(blank_section())
            st.session_state.fin_config = updated
            st.session_state.pop("fin_result", None)
            st.session_state.pop("fin_error", None)
            _clear_numerical_widgets()
            st.rerun()
        elif action == "remove" and remove_index is not None:
            updated["domains"].pop(remove_index - 1)
            st.session_state.fin_config = updated
            st.session_state.pop("fin_result", None)
            st.session_state.pop("fin_error", None)
            _clear_numerical_widgets()
            st.rerun()
        elif action == "defaults":
            st.session_state.fin_config = updated
            st.session_state.pop("fin_result", None)
            st.session_state.pop("fin_error", None)
            _clear_numerical_widgets()
            st.rerun()
        elif action == "run":
            prepared = prepare_numerical_config(updated)
            result = simulate(prepared)
            st.session_state.fin_config = updated
            st.session_state.fin_result = result
            st.session_state.pop("fin_error", None)
        elif updated != st.session_state.fin_config:
            st.session_state.fin_config = updated
            st.session_state.pop("fin_result", None)
            st.session_state.pop("fin_error", None)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        st.session_state.fin_error = str(exc)
        st.session_state.pop("fin_result", None)
    if "fin_error" in st.session_state:
        st.error(st.session_state.fin_error)
    if "fin_result" in st.session_state:
        numerical_results(st.session_state.fin_config, st.session_state.fin_result)


numerical_page()
