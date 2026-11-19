# Fin Heat Transfer Simulation App

Interactive Streamlit application for 1D transient and steady-state heat transfer across fins and composite bars.

## Key Features
- **Numerical Solver and Steady Reference**: Simulates 1D transient conduction, convection, internal heat generation, contact resistances, gas gaps and radiation with Explicit or Implicit finite volume schemes. The analytical reference uses averaged geometry and material properties and omits radiation. An insulated fin without generation uses its initial energy to determine the equilibrium temperature.
- **14 Built-in Saved Cases**: Includes 14 ready-to-run physical benchmark cases (`finmodel/saved_cases.py`) accessible from the **Load saved cases** selector at the top of the app.
- **JSON Import & CSV/JSON Export**: Save or load full simulation configurations as JSON and export temperature, heat-flux, and grid tables as CSV.
- **Interactive 2D & 3D Visualization**: Select temperature histories at cell centres, heat rates at faces, and profiles at saved times. The interactive 3D fin uses a fixed temperature scale and one frame per saved time.

The solver retains the fixed-step Excel output schedule. Results show the requested duration, final computed time and last saved time separately. Explicit checks the assembled loss weight of each cell, including its boundaries and active heat losses. It warns when that weight exceeds the limit, continues for demonstration, and saves consecutive steps around the warning for a separate graph. If the run overflows, it keeps the results saved so far.

## Run Locally

```powershell
python -m streamlit run app.py
```

Or with a fresh virtual environment (Python 3.11+):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

## Deploy Online (GitHub + Streamlit Community Cloud)

1. Push this repository to GitHub.
2. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/) with your GitHub account.
3. Click **Create app** -> select your repository, branch (`main`), and main file path (`app.py`), then click **Deploy**.

## Run Tests

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q tests/test_physics_regression.py
```
