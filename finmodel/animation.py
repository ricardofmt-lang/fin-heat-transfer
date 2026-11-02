"""Interactive 3D animated visualization of the fin's temperature evolution.

Pedagogical Overview
--------------------
In the 1D finite-volume method, each axial cell $i = 1 \\dots N$ has one spatially
uniform temperature $T_i(t)$ at any given time $t$, bounded by its left face
$x_{f,i}$ and right face $x_{f,i+1}$.

To represent this finite-volume discretisation faithfully in 3D:
1. Each cell $i$ is constructed as its own independent 3D prism/frustum with its
   own duplicate vertices at $x_{f,i}$ and $x_{f,i+1}$, all assigned the vertex
   intensity $T_i(t)$. Because neighbouring cells do not share vertices across
   their common face, Plotly renders sharp, piecewise-constant colour bands for
   each cell rather than smearing/interpolating colours across cell boundaries.
2. The cross-section at every face $x_{f,i}$ is evaluated at the true physical
   millimetre coordinates (`Rectangular`, `Pyramidal`, `Cylindrical`, or
   `Conical`), including any non-uniform cell widths caused by local contact
   resistances.
3. The colourbar range `[cmin, cmax]` is fixed across the entire simulation time
   span $t \\in [0, t_{\\text{end}}]$ so a given colour always corresponds to the
   same kelvin temperature as the student scrubs the time slider or clicks Play.
"""

from __future__ import annotations  # Postpone evaluation of type annotations

from math import cos, isfinite, pi, sin  # Trigonometric and finiteness helpers for circular cross-sections
from typing import Any, Mapping  # Type annotations

import plotly.graph_objects as go  # Plotly 3D mesh (`go.Mesh3d`) and animation (`go.Frame`) objects

from finmodel.numerical import SimulationResult  # Output container from the numerical solver


# Five-stop thermal colourscale mapping normalized temperature [0.0 .. 1.0]
# from cool blue (#2f6fab) through cyan (#4db5d0) and warm yellow (#ffe37a)
# to orange (#ff913e) and hot red (#c62828).
HEAT_COLORSCALE = [
    [0.0, "#2f6fab"],  # Minimum temperature (cool blue)
    [0.25, "#4db5d0"],  # Lower-quartile temperature (cyan)
    [0.5, "#ffe37a"],  # Midpoint temperature (warm yellow)
    [0.75, "#ff913e"],  # Upper-quartile temperature (orange)
    [1.0, "#c62828"],  # Maximum temperature (hot red)
]

# Number of polygon vertices used to approximate circular cross-sections
# for `Cylindrical` and `Conical` fins (24-gon = 15° per segment).
_ROUND_SEGMENTS = 24


def _cross_section(
    geometry: Mapping[str, Any], position_mm: float, nominal_length_mm: float
) -> list[tuple[float, float]]:
    """Return the counterclockwise `(y, z)` polygon vertices (in mm) of the fin's cross-section at `position_mm`."""
    # Read the selected fin shape (`Rectangular`, `Pyramidal`, `Cylindrical`, or `Conical`).
    shape = geometry["shape"]
    # Normalized axial coordinate x / L in [0, 1] used to linearly interpolate tapered shapes.
    fraction = position_mm / nominal_length_mm
    # Floating-point summation of cell widths `dx[i]` can place the final face a few ULPs
    # (~1e-16) beyond `nominal_length_mm`. Clamp tiny machine-epsilon overshoots to [0.0, 1.0]
    # so a pointed tip (`width_top = 0` or `diameter_top = 0`) never becomes slightly negative.
    if -1e-12 <= fraction <= 0:
        fraction = 0.0
    elif 1 <= fraction <= 1 + 1e-12:
        fraction = 1.0
    # Compute transverse dimensions at `position_mm` based on `shape`:
    if shape == "Rectangular":
        # Constant width W (mm) and thickness t (mm) along the entire fin length.
        width = float(geometry["width_mm"])
        thickness = float(geometry["thickness_mm"])
    elif shape == "Pyramidal":
        # Linearly interpolate width W(x) and thickness t(x) from base (fraction = 0) to tip (fraction = 1).
        width = float(geometry["width_base_mm"]) + fraction * (
            float(geometry["width_top_mm"]) - float(geometry["width_base_mm"])
        )
        thickness = float(geometry["thickness_base_mm"]) + fraction * (
            float(geometry["thickness_top_mm"]) - float(geometry["thickness_base_mm"])
        )
    elif shape in {"Cylindrical", "Conical"}:
        # Circular cross-sections: determine diameter D(x) (mm) at `position_mm`.
        if shape == "Cylindrical":
            # Constant diameter D (mm) for a cylindrical pin fin.
            diameter = float(geometry["diameter_mm"])
        else:
            # Linearly interpolate diameter D(x) from `diameter_base_mm` (x = 0) to `diameter_top_mm` (x = L).
            diameter = float(geometry["diameter_base_mm"]) + fraction * (
                float(geometry["diameter_top_mm"]) - float(geometry["diameter_base_mm"])
            )
        # Ensure the diameter is finite and non-negative.
        if not isfinite(diameter) or diameter < 0:
            raise ValueError("The 3D fin diameter must be nonnegative at every face")
        # Convert diameter D to radius R = D / 2 (mm).
        radius = diameter / 2
        # Return `_ROUND_SEGMENTS` (24) vertices equally spaced around the circle in the (y, z) plane.
        return [
            (radius * cos(2 * pi * vertex / _ROUND_SEGMENTS),
             radius * sin(2 * pi * vertex / _ROUND_SEGMENTS))
            for vertex in range(_ROUND_SEGMENTS)
        ]
    else:
        # Reject unknown shape strings.
        raise ValueError(f"Unknown fin shape: {shape}")
    # Ensure rectangular/pyramidal width and thickness are finite and non-negative.
    if not all(isfinite(value) and value >= 0 for value in (width, thickness)):
        raise ValueError("The 3D fin width and thickness must be nonnegative at every face")
    # Return the 4 corner vertices of the rectangle centered at (y, z) = (0, 0) in counterclockwise order.
    return [
        (-width / 2, -thickness / 2),  # Bottom-left corner
        (width / 2, -thickness / 2),  # Bottom-right corner
        (width / 2, thickness / 2),  # Top-right corner
        (-width / 2, thickness / 2),  # Top-left corner
    ]


def _mesh(
    face_positions_mm: list[float], geometry: Mapping[str, Any], nominal_length_mm: float
) -> tuple[list[float], list[float], list[float], list[int], list[int], list[int], int]:
    """Build the 3D triangle mesh coordinates `(x, y, z)` and triangle index lists `(i_tri, j_tri, k_tri)`."""
    # Vertex coordinate lists in millimetres.
    x: list[float] = []
    y: list[float] = []
    z: list[float] = []
    # Triangle vertex index lists (each triangle `m` connects vertices `i_tri[m]`, `j_tri[m]`, `k_tri[m]`).
    i_tri: list[int] = []
    j_tri: list[int] = []
    k_tri: list[int] = []
    # Number of vertices per cross-sectional ring (4 for rectangular/pyramidal, 24 for cylindrical/conical).
    ring_size = 0

    # Iterate over each finite-volume cell `0 .. n-1`, bounded by its `left` and `right` face positions (mm).
    for cell, (left, right) in enumerate(zip(face_positions_mm, face_positions_mm[1:])):
        # Compute the cross-sectional polygon rings at the cell's left and right faces.
        left_ring = _cross_section(geometry, left, nominal_length_mm)
        right_ring = _cross_section(geometry, right, nominal_length_mm)
        # Record `ring_size` from the first cell.
        if ring_size == 0:
            ring_size = len(left_ring)
        # Starting vertex index `base` for this cell's `2 * ring_size` vertices.
        base = len(x)
        # Append the `ring_size` vertices at `x = left` followed by the `ring_size` vertices at `x = right`.
        for face_position, ring in ((left, left_ring), (right, right_ring)):
            for yy, zz in ring:
                x.append(face_position)  # Axial coordinate x (mm)
                y.append(yy)  # Transverse width coordinate y (mm)
                z.append(zz)  # Transverse thickness coordinate z (mm)

        # Triangulate the lateral side panels connecting `left_ring` to `right_ring`:
        for side in range(ring_size):
            # Next vertex index around the perimeter ring (wrapping around from `ring_size - 1` to `0`).
            next_side = (side + 1) % ring_size
            # Each quad panel `(side, next_side)` between `left_ring` and `right_ring` is split into 2 triangles.
            i_tri.extend((base + side, base + next_side))
            j_tri.extend((base + next_side, base + ring_size + next_side))
            k_tri.extend((base + ring_size + side, base + ring_size + side))

        # Close the exposed left end cap at x = 0 (cell 0) and right end cap at x = L (last cell)
        # using fan triangulation from vertex 0 of the ring. Interior shared faces are intentionally
        # left open so adjacent cells remain visually seamless while keeping sharp colour steps.
        if cell == 0:
            # Fan-triangulate the base end cap at x = 0 (`cell == 0`).
            for side in range(1, ring_size - 1):
                i_tri.append(base)
                j_tri.append(base + side + 1)
                k_tri.append(base + side)
        if cell == len(face_positions_mm) - 2:
            # Fan-triangulate the tip end cap at x = L (`cell == n - 1`).
            for side in range(1, ring_size - 1):
                i_tri.append(base + ring_size)
                j_tri.append(base + ring_size + side)
                k_tri.append(base + ring_size + side + 1)

    # Return the vertex arrays, triangle connectivity arrays, and vertices-per-ring count.
    return x, y, z, i_tri, j_tri, k_tri, ring_size


def build_3d_fin_figure(result: SimulationResult, config: Mapping[str, Any]) -> go.Figure:
    """Create the interactive Plotly 3D animated fin figure (`go.Figure`) across all printed time steps."""
    # Extract cell count `n`, printed times `times` (s), and printed temperature matrix `temperatures` (K).
    n = result.grid.n
    times = result.times_s
    temperatures = result.temperature_k
    # Validate that `temperatures` has one row per printed time and `n` columns per row.
    if not times or len(temperatures) != len(times) or any(len(row) != n for row in temperatures):
        raise ValueError("Temperature rows must match printed times and grid cells")
    # Validate that the grid has `n + 1` face positions for `n` cells.
    if n < 1 or len(result.face_positions_m) != n + 1:
        raise ValueError("The numerical grid must contain one more face than cells")
    # Validate that all temperature values are finite numbers (no NaN or Inf from unstable explicit runs).
    if not all(isfinite(float(value)) for row in temperatures for value in row):
        raise ValueError("3D temperature colours require finite values")
    # Validate that all time stamps are finite numbers.
    if not all(isfinite(float(value)) for value in times):
        raise ValueError("3D animation times must be finite")

    # Convert face axial coordinates from metres to millimetres.
    positions_mm = [1000 * value for value in result.face_positions_m]
    # Verify that face coordinates are strictly increasing from base to tip.
    if any(right <= left for left, right in zip(positions_mm, positions_mm[1:])):
        raise ValueError("3D fin faces must advance from base to tip")
    # Compute total nominal fin length L (mm) by summing section lengths.
    nominal_length_mm = sum(float(section["length_mm"]) for section in config["domains"])
    if not isfinite(nominal_length_mm) or nominal_length_mm <= 0:
        raise ValueError("3D fin length must be positive")
    # Build the 3D triangle mesh for the fin geometry.
    x, y, z, i_tri, j_tri, k_tri, ring_size = _mesh(
        positions_mm, config["geometry"], nominal_length_mm
    )

    def intensities(row: list[float]) -> list[float]:
        """Repeat each cell's temperature `2 * ring_size` times (once for each vertex of that cell)."""
        # Every one of the `2 * ring_size` vertices belonging to cell `i` receives `row[i]`.
        return [float(value) for value in row for _ in range(2 * ring_size)]

    # Determine the global minimum and maximum temperatures across all cells and all printed times
    # so the colourbar scale remains constant throughout the animation.
    colour_min = min(value for row in temperatures for value in row)
    colour_max = max(value for row in temperatures for value in row)
    # If the entire simulation is isothermal (`colour_min == colour_max`), expand the range by ±0.5 K
    # so Plotly's colourbar does not divide by a zero span.
    if colour_min == colour_max:
        colour_min -= 0.5
        colour_max += 0.5

    # Shared `go.Mesh3d` keyword arguments used by both the initial trace and every animation frame.
    mesh_properties = dict(
        x=x, y=y, z=z, i=i_tri, j=j_tri, k=k_tri,  # 3D vertex coordinates and triangle indices
        intensitymode="vertex", colorscale=HEAT_COLORSCALE,  # Per-vertex temperature colouring
        cmin=colour_min, cmax=colour_max, showscale=True,  # Fixed global temperature scale
        colorbar=dict(title="Temperature (K)", thickness=18),  # Colourbar title and width
        flatshading=True,  # Crisp facet shading for clear 3D geometry perception
        lighting=dict(ambient=0.95, diffuse=0.45, specular=0.1, roughness=0.8),  # High ambient lighting so colours stay true to the colourbar
        hovertemplate="Distance: %{x:.2f} mm<br>Temperature: %{intensity:.2f} K<extra></extra>",  # Tooltip showing x (mm) and T (K)
        name="Fin",  # Trace name
    )

    def frame_mesh(row: list[float]) -> go.Mesh3d:
        """Construct a complete `go.Mesh3d` trace for a single printed temperature row."""
        # Supplying the full `Mesh3d` in each animation frame prevents Plotly from resetting
        # the 3D bounding box when the user drags the time slider.
        return go.Mesh3d(intensity=intensities(row), **mesh_properties)

    # Create the initial 3D mesh trace at `t = 0` (`temperatures[0]`).
    mesh = frame_mesh(temperatures[0])

    # Compute coordinate spans and padding for the 3D axes (in mm).
    x_span = max(x) - min(x)
    y_span = max(y) - min(y)
    z_span = max(z) - min(z)
    x_padding = 0.025 * x_span
    y_padding = 0.08 * y_span if y_span else 0.5
    z_padding = 0.08 * z_span if z_span else 0.5
    # Configure fixed 3D axis ranges and titles (in millimetres).
    scene_ranges = dict(
        xaxis=dict(title="Distance from base (mm)", range=[min(x) - x_padding, max(x) + x_padding], autorange=False),
        yaxis=dict(title="Width (mm)", range=[min(y) - y_padding, max(y) + y_padding], autorange=False),
        zaxis=dict(title="Thickness (mm)", range=[min(z) - z_padding, max(z) + z_padding], autorange=False),
    )
    # Slender fins (e.g. L = 100 mm, thickness = 2 mm) would look like a thin wire if drawn at 1:1:1
    # aspect ratio. We keep the true millimetre tick labels on all axes while scaling the visual box
    # aspect ratio so the cross-section is clearly visible in 3D.
    larger_cross_section = max(y_span, z_span, 1e-12)
    aspect_ratio = dict(
        x=3.2,  # Elongated axial display proportion
        y=max(0.18, 0.68 * y_span / larger_cross_section),  # Transverse width proportion
        z=max(0.18, 0.68 * z_span / larger_cross_section),  # Transverse thickness proportion
    )

    # Build one `go.Frame` per printed time step (`0 .. len(times) - 1`).
    frames = [
        go.Frame(
            name=str(index),  # Frame identifier matching the slider step argument
            data=[frame_mesh(row)],  # Updated 3D mesh with temperatures at time `times[index]`
            traces=[0],  # Update trace 0 in place
        )
        for index, row in enumerate(temperatures)
    ]
    # Build the interactive time-slider steps corresponding to each animation frame.
    steps = [
        dict(
            method="animate",  # Trigger Plotly animation to the selected frame
            label=f"{time:g}",  # Display time in seconds on the slider tick
            args=[[str(index)], {
                "mode": "immediate",
                "frame": {"duration": 0, "redraw": True},
                "transition": {"duration": 0},
            }],
        )
        for index, time in enumerate(times)
    ]
    # Assemble the complete `go.Figure` with initial trace, animation frames, Play/Pause buttons, and time slider.
    fig = go.Figure(data=[mesh], frames=frames)
    fig.update_layout(
        title="Fin temperature over time",  # Chart title
        height=650,  # Vertical height in pixels
        margin=dict(l=0, r=0, t=55, b=50),  # Compact margins around the 3D scene
        scene=dict(
            **scene_ranges,  # Fixed X, Y, Z axis ranges and titles
            aspectmode="manual",  # Use our readable 3D aspect ratio
            aspectratio=aspect_ratio,
            camera=dict(eye=dict(x=1.3, y=1.5, z=0.85)),  # Isometric-like initial camera angle
            uirevision=f"fin-3d-{n}-{min(x):g}-{max(x):g}-{y_span:g}-{z_span:g}",  # Preserve user camera rotation across slider moves
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
    # Return the interactive 3D Plotly figure.
    return fig
