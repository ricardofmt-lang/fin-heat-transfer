"""Animated 3D view of the temperatures printed by the numerical solver.

The solver has one temperature per axial cell.  The mesh therefore has a
separate set of vertices for each cell, so colours change at cell boundaries
instead of being interpolated between neighbouring cells.  Its cross-section
is evaluated at the solver's actual face positions, including any spacing
changes caused by local thermal resistances.
"""

from __future__ import annotations

from math import cos, isfinite, pi, sin
from typing import Any, Mapping

import plotly.graph_objects as go

from finmodel.numerical import SimulationResult


HEAT_COLORSCALE = [
    [0.0, "#2f6fab"],
    [0.25, "#4db5d0"],
    [0.5, "#ffe37a"],
    [0.75, "#ff913e"],
    [1.0, "#c62828"],
]

_ROUND_SEGMENTS = 24


def _cross_section(
    geometry: Mapping[str, Any], position_mm: float, nominal_length_mm: float
) -> list[tuple[float, float]]:
    """Return counterclockwise (y, z) vertices at a fin face, in mm."""
    shape = geometry["shape"]
    fraction = position_mm / nominal_length_mm
    # Summing cell widths in the VBA grid can put the final face a few ULPs
    # beyond the nominal length. At a pointed tip that would otherwise turn
    # an exact zero width into a tiny negative value and suppress the view.
    if -1e-12 <= fraction <= 0:
        fraction = 0.0
    elif 1 <= fraction <= 1 + 1e-12:
        fraction = 1.0
    if shape == "Rectangular":
        width = float(geometry["width_mm"])
        thickness = float(geometry["thickness_mm"])
    elif shape == "Pyramidal":
        width = float(geometry["width_base_mm"]) + fraction * (
            float(geometry["width_top_mm"]) - float(geometry["width_base_mm"])
        )
        thickness = float(geometry["thickness_base_mm"]) + fraction * (
            float(geometry["thickness_top_mm"]) - float(geometry["thickness_base_mm"])
        )
    elif shape in {"Cylindrical", "Conical"}:
        if shape == "Cylindrical":
            diameter = float(geometry["diameter_mm"])
        else:
            diameter = float(geometry["diameter_base_mm"]) + fraction * (
                float(geometry["diameter_top_mm"]) - float(geometry["diameter_base_mm"])
            )
        if not isfinite(diameter) or diameter < 0:
            raise ValueError("The 3D fin diameter must be nonnegative at every face")
        radius = diameter / 2
        return [
            (radius * cos(2 * pi * vertex / _ROUND_SEGMENTS),
             radius * sin(2 * pi * vertex / _ROUND_SEGMENTS))
            for vertex in range(_ROUND_SEGMENTS)
        ]
    else:
        raise ValueError(f"Unknown fin shape: {shape}")
    if not all(isfinite(value) and value >= 0 for value in (width, thickness)):
        raise ValueError("The 3D fin width and thickness must be nonnegative at every face")
    return [
        (-width / 2, -thickness / 2),
        (width / 2, -thickness / 2),
        (width / 2, thickness / 2),
        (-width / 2, thickness / 2),
    ]


def _mesh(
    face_positions_mm: list[float], geometry: Mapping[str, Any], nominal_length_mm: float
) -> tuple[list[float], list[float], list[float], list[int], list[int], list[int], int]:
    x: list[float] = []
    y: list[float] = []
    z: list[float] = []
    i_tri: list[int] = []
    j_tri: list[int] = []
    k_tri: list[int] = []
    ring_size = 0

    for cell, (left, right) in enumerate(zip(face_positions_mm, face_positions_mm[1:])):
        left_ring = _cross_section(geometry, left, nominal_length_mm)
        right_ring = _cross_section(geometry, right, nominal_length_mm)
        if ring_size == 0:
            ring_size = len(left_ring)
        base = len(x)
        for face_position, ring in ((left, left_ring), (right, right_ring)):
            for yy, zz in ring:
                x.append(face_position)
                y.append(yy)
                z.append(zz)

        for side in range(ring_size):
            next_side = (side + 1) % ring_size
            # Two outward-facing triangles per axial side panel.
            i_tri.extend((base + side, base + next_side))
            j_tri.extend((base + next_side, base + ring_size + next_side))
            k_tri.extend((base + ring_size + side, base + ring_size + side))

        # The two exposed end faces close the solid.  Shared internal faces
        # are omitted so cells still form one fin while retaining sharp colour
        # changes at their boundaries.
        if cell == 0:
            for side in range(1, ring_size - 1):
                i_tri.append(base)
                j_tri.append(base + side + 1)
                k_tri.append(base + side)
        if cell == len(face_positions_mm) - 2:
            for side in range(1, ring_size - 1):
                i_tri.append(base + ring_size)
                j_tri.append(base + ring_size + side)
                k_tri.append(base + ring_size + side + 1)

    return x, y, z, i_tri, j_tri, k_tri, ring_size


def build_3d_fin_figure(result: SimulationResult, config: Mapping[str, Any]) -> go.Figure:
    """Create a rotatable, Play/Pause/slider animation of printed temperatures.

    All printed times are included, from the initial state through the last
    row.  Temperature colours use one range over the complete simulation, so
    their meaning stays fixed while the animation advances.  Coordinates and
    axes are in millimetres; the solver's temperatures remain in kelvin.
    """
    n = result.grid.n
    times = result.times_s
    temperatures = result.temperature_k
    if not times or len(temperatures) != len(times) or any(len(row) != n for row in temperatures):
        raise ValueError("Temperature rows must match printed times and grid cells")
    if n < 1 or len(result.face_positions_m) != n + 1:
        raise ValueError("The numerical grid must contain one more face than cells")
    if not all(isfinite(float(value)) for row in temperatures for value in row):
        raise ValueError("3D temperature colours require finite values")
    if not all(isfinite(float(value)) for value in times):
        raise ValueError("3D animation times must be finite")

    positions_mm = [1000 * value for value in result.face_positions_m]
    if any(right <= left for left, right in zip(positions_mm, positions_mm[1:])):
        raise ValueError("3D fin faces must advance from base to tip")
    nominal_length_mm = sum(float(section["length_mm"]) for section in config["domains"])
    if not isfinite(nominal_length_mm) or nominal_length_mm <= 0:
        raise ValueError("3D fin length must be positive")
    x, y, z, i_tri, j_tri, k_tri, ring_size = _mesh(
        positions_mm, config["geometry"], nominal_length_mm
    )

    def intensities(row: list[float]) -> list[float]:
        return [float(value) for value in row for _ in range(2 * ring_size)]

    colour_min = min(value for row in temperatures for value in row)
    colour_max = max(value for row in temperatures for value in row)
    if colour_min == colour_max:
        colour_min -= 0.5
        colour_max += 0.5

    mesh_properties = dict(
        x=x, y=y, z=z, i=i_tri, j=j_tri, k=k_tri,
        intensitymode="vertex", colorscale=HEAT_COLORSCALE,
        cmin=colour_min, cmax=colour_max, showscale=True,
        colorbar=dict(title="Temperature (K)", thickness=18),
        flatshading=True,
        lighting=dict(ambient=0.95, diffuse=0.45, specular=0.1, roughness=0.8),
        hovertemplate="Distance: %{x:.2f} mm<br>Temperature: %{intensity:.2f} K<extra></extra>",
        name="Fin",
    )

    def frame_mesh(row: list[float]) -> go.Mesh3d:
        # A complete Mesh3d in each frame prevents Plotly from re-autoscaling
        # a partial trace when the time slider requests a 3D redraw.
        return go.Mesh3d(intensity=intensities(row), **mesh_properties)

    mesh = frame_mesh(temperatures[0])

    x_span = max(x) - min(x)
    y_span = max(y) - min(y)
    z_span = max(z) - min(z)
    x_padding = 0.025 * x_span
    y_padding = 0.08 * y_span if y_span else 0.5
    z_padding = 0.08 * z_span if z_span else 0.5
    scene_ranges = dict(
        xaxis=dict(title="Distance from base (mm)", range=[min(x) - x_padding, max(x) + x_padding], autorange=False),
        yaxis=dict(title="Width (mm)", range=[min(y) - y_padding, max(y) + y_padding], autorange=False),
        zaxis=dict(title="Thickness (mm)", range=[min(z) - z_padding, max(z) + z_padding], autorange=False),
    )
    # Long, thin fins become an almost invisible line at physical display
    # scale.  Keep their numerical coordinates and tick labels unchanged, but
    # enlarge the transverse directions together for a readable 3D solid.
    larger_cross_section = max(y_span, z_span, 1e-12)
    aspect_ratio = dict(
        x=3.2,
        y=max(0.18, 0.68 * y_span / larger_cross_section),
        z=max(0.18, 0.68 * z_span / larger_cross_section),
    )

    frames = [
        go.Frame(
            name=str(index),
            data=[frame_mesh(row)],
            traces=[0],
        )
        for index, row in enumerate(temperatures)
    ]
    steps = [
        dict(
            method="animate",
            label=f"{time:g}",
            args=[[str(index)], {
                "mode": "immediate",
                "frame": {"duration": 0, "redraw": True},
                "transition": {"duration": 0},
            }],
        )
        for index, time in enumerate(times)
    ]
    fig = go.Figure(data=[mesh], frames=frames)
    fig.update_layout(
        title="Fin temperature over time",
        height=650,
        margin=dict(l=0, r=0, t=55, b=50),
        scene=dict(
            **scene_ranges,
            aspectmode="manual",
            aspectratio=aspect_ratio,
            camera=dict(eye=dict(x=1.3, y=1.5, z=0.85)),
            uirevision=f"fin-3d-{n}-{min(x):g}-{max(x):g}-{y_span:g}-{z_span:g}",
        ),
        annotations=[dict(
            x=0.0, y=1.02, xref="paper", yref="paper", xanchor="left",
            showarrow=False, text="Cross section enlarged for visibility; axes show actual mm",
        )],
        updatemenus=[dict(
            type="buttons",
            showactive=False,
            direction="left",
            x=0.0, y=-0.08,
            buttons=[
                dict(label="▶ Play", method="animate", args=[None, {
                    "fromcurrent": True,
                    "frame": {"duration": 180, "redraw": True},
                    "transition": {"duration": 0},
                }]),
                dict(label="❚❚ Pause", method="animate", args=[[None], {
                    "mode": "immediate",
                    "frame": {"duration": 0, "redraw": False},
                    "transition": {"duration": 0},
                }]),
            ],
        )],
        sliders=[dict(
            active=0,
            x=0.18, len=0.8,
            currentvalue=dict(prefix="Time: ", suffix=" s"),
            steps=steps,
        )],
    )
    return fig
