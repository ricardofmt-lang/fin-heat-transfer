"""Independent checks for the student-facing fin model."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from finmodel.empty_inputs import prepare_numerical_config
from finmodel.numerical import SIGMA, simulate
from finmodel.saved_cases import get_saved_case_names, load_saved_case
from finmodel.visualization import build_chart_specs

APP = Path(__file__).resolve().parents[1] / "app.py"


def case(number):
    return load_saved_case(get_saved_case_names()[number - 1])


@pytest.mark.parametrize("method", ["exp", "imp"])
@pytest.mark.parametrize("right_bc", ["NoHeatFlux", "Temp", "HeatFlux"])
def test_one_cell_matches_an_independent_heat_balance(method, right_bc):
    cfg = case(1)
    cfg["domains"][0]["n_cells"] = 1
    cfg["temperature"].update(right_boundary=right_bc, right_temperature_k=320, right_convection=50)
    cfg["controls"].update(method=method, time_step_s=1, duration_s=10, output_interval_s=1)
    # Material and dimensions of Case 1; a fixed end is half a cell away.
    area, length, k = 0.3 * 0.01, 0.05, 300
    capacity = 3000 * 3000 * area * length
    g_left = area / (length / (2 * k))
    g_right, t_right = {
        "NoHeatFlux": (0, 0),
        "Temp": (g_left, 320),
        "HeatFlux": (area / (length / (2 * k) + 1 / 50), 300),
    }[right_bc]
    expected = 300.0
    result = simulate(cfg)
    for row in result.temperature_k[1:]:
        if method == "imp":
            expected = (capacity * expected + g_left * 400 + g_right * t_right) / (capacity + g_left + g_right)
        else:
            expected += (g_left * (400 - expected) + g_right * (t_right - expected)) / capacity
        assert row[0] == pytest.approx(expected)


@pytest.mark.parametrize("number,equilibrium", [(3, 470), (7, 355)])
def test_insulated_equilibrium_uses_initial_energy(number, equilibrium):
    result = simulate(case(number))
    assert result.analytical_temperature_k == pytest.approx([equilibrium] * result.grid.n)
    assert result.analytical_heat_flux_w == [0] * (result.grid.n + 1)


def test_isolated_composite_weights_initial_temperature_by_capacitance():
    cfg = case(3)
    section = dict(cfg["domains"][0], n_cells=1, length_mm=10)
    cfg["domains"] = [section, dict(section, length_mm=20)]
    cfg["temperature"]["initial_cells"] = [{"cell": 1, "value_k": 600}]
    result = simulate(cfg)
    assert result.analytical_temperature_k == pytest.approx([400, 400])


def test_insulated_generation_has_no_steady_reference():
    cfg = case(3)
    cfg["domains"][0]["heat_generated"] = 5e6
    result = simulate(cfg)
    assert result.analytical_temperature_k == result.analytical_heat_flux_w == []
    assert "No steady state" in result.analytical_note
    assert all(row[0] == row[-1] == 0 for row in result.cross_section_heat_flux_w)


def test_explicit_error_identifies_fixed_end_even_below_fourier_half():
    cfg = case(1)
    cfg["controls"].update(method="exp", time_step_s=0.08)
    alpha = 300 / (3000 * 3000)
    assert alpha * 0.08 / (0.05 / 20) ** 2 < 0.5
    with pytest.raises(ValueError, match=r"t=0.08 s.*cell 1.*b = 1.28"):
        simulate(cfg)


def test_radiating_end_reports_irradiation_linear_in_absorptivity():
    cfg = case(3)
    cfg["temperature"].update(initial_type="DEFAULT", right_boundary="HeatFlux",
                              right_convection=0, right_radiation=True, right_surround_k=400)
    cfg["domains"][0].update(right_absorptivity=0.5, right_emissivity=0)
    result = simulate(cfg)
    expected = -SIGMA * (0.3 * 0.01) * 0.5 * 400**4
    assert result.cross_section_heat_flux_w[-1][-1] == pytest.approx(expected)
    assert result.analytical_temperature_k == []


def test_saved_and_computed_times_are_distinct_and_face_n_plus_one_is_plotable():
    cfg = case(1)
    cfg["controls"].update(duration_s=0.25, time_step_s=0.1, output_interval_s=0.3)
    result = simulate(cfg)
    assert result.times_s == pytest.approx([0, 0.3])
    assert result.final_computed_time_s == pytest.approx(0.3)
    chart_specs = build_chart_specs(result, {"faces": [21], "percentages": [100]})
    end_face = next(spec for spec in chart_specs if spec.kind == "individual_time")
    assert end_face.title.endswith("face 21")
    assert end_face.series[0].y_values[-1] == result.cross_section_heat_flux_w[-1][-1]
    assert any("0.3 Seconds" in spec.title for spec in chart_specs)
    cfg["controls"]["output_interval_s"] = 20
    result = simulate(cfg)
    assert result.times_s == [0]
    assert result.final_computed_time_s == pytest.approx(0.3)


def test_initial_cell_overrides_require_a_default_for_unlisted_cells():
    cfg = case(3)
    cfg["temperature"]["default_k"] = None
    with pytest.raises(ValueError, match="default temperature .*unlisted cells"):
        prepare_numerical_config(cfg)


@pytest.mark.parametrize("fault, message", [
    ("lateral_h", "Lateral convection coefficient"),
    ("optical", "emissivity must be between 0 and 1"),
    ("end_gap", "gas gap must be between other sections"),
    ("kelvin", "must be above 0 K"),
])
def test_invalid_physics_is_rejected(fault, message):
    cfg = case(1)
    if fault == "lateral_h":
        cfg["temperature"].update(lateral_flux=True, lateral_convection=-1)
    elif fault == "optical":
        cfg["domains"][0]["lateral_emissivity"] = 2
    elif fault == "end_gap":
        cfg["domains"][0]["gas_gap"] = True
    else:
        cfg["temperature"]["left_temperature_k"] = -1
    with pytest.raises(ValueError, match=message):
        simulate(prepare_numerical_config(cfg))


def test_bad_plot_selection_preserves_the_tables_and_exports():
    app = AppTest.from_file(str(APP)).run()
    app.selectbox(key="saved_case").select(get_saved_case_names()[0]).run()
    run = lambda: next(b for b in app.button if b.label == "Run numerical simulation").click().run(timeout=30)
    next(w for w in app.text_input if w.label.startswith("Heat rate at faces")).set_value("22").run()
    run()
    assert not app.exception
    assert "Face number 22" in app.error[0].value
    assert len(app.get("download_button")) == 5


def test_one_cell_default_plots_include_both_faces():
    app = AppTest.from_file(str(APP)).run()
    app.selectbox(key="saved_case").select(get_saved_case_names()[0]).run()
    next(w for w in app.number_input if w.label == "Cells").set_value(1).run()
    next(b for b in app.button if b.label == "Use default graph selections").click().run()
    assert app.session_state["fin_config"]["graphs"]["cells"] == [1]
    assert app.session_state["fin_config"]["graphs"]["faces"] == [1, 2]
