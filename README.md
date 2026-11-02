# Fin Heat Transfer Simulation App

Interactive Streamlit application for 1D transient and steady-state heat transfer across fins and composite bars.

## Key Features
- **Unified Numerical & Analytical Solver**: Simulates 1D transient conduction, convection, internal heat generation, local contact resistances, gas gaps, and surface/lateral radiation using Explicit or Implicit finite-volume schemes (`finmodel/numerical.py`), while automatically computing and overlaying the exact 1D steady-state analytical solution (`finmodel/analytical.py`) on every 2D graph.
- **14 Built-in Saved Cases**: Includes 14 ready-to-run physical benchmark cases (`finmodel/saved_cases.py`) accessible from the **Load saved cases** selector at the top of the app.
- **JSON Import & CSV/JSON Export**: Save or load full simulation configurations as JSON and export temperature, heat-flux, and grid tables as CSV.
- **Interactive 2D & 3D Visualization**: Generates all 22 temperature and cross-sectional heat-flux graphs (with analytical comparison curves) plus an interactive 3D animated fin surface (`finmodel/animation.py`).

## Run Locally

```powershell
.\run.cmd
```

Or with a fresh virtual environment (Python 3.11+):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\run.cmd
```

## Deploy Online (GitHub + Streamlit Community Cloud)

1. Push this repository to GitHub.
2. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/) with your GitHub account.
3. Click **Create app** -> select your repository, branch (`main`), and main file path (`app.py`), then click **Deploy**.

## Run Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```
