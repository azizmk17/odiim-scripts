# StackLab 1D

StackLab 1D is a desktop tool for sketching axial assemblies and analyzing mechanical tolerance stack-ups. The sketch is connected to a parametric engineering model: dimensions control local part geometry, assembly constraints control part translations, and selected functional faces define the measured gap. The application uses PySide6 for the workspace and SciPy for linear assembly and worst-case optimization.

## Install and launch

Use Python 3.11 or newer. On Windows, double-click `run.bat`, or run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

On Linux or macOS, run `sh run.sh`. The GUI requires a graphical desktop. Project calculations and report export also run from the terminal:

If another environment such as `pyoccenv` is active, launch through `run.bat`; it clears inherited Qt DLL paths before starting StackLab. Or deactivate that environment and use `.\.venv\Scripts\python.exe` explicitly. The project uses its own Python 3.11 environment; `python main.py` may select a different interpreter.

```powershell
.\.venv\Scripts\python.exe main.py examples\a-fixed-chain.stack1d --analyze --methods worst_case
.\.venv\Scripts\python.exe main.py examples\e-statistical-chain.stack1d --analyze --methods worst_case,rss,monte_carlo --samples 30000 --seed 2026 --report report.pdf --csv report.csv --xlsx report.xlsx
```

`--requirement` selects one requirement ID; otherwise the CLI analyzes all requirements. `--sigma-level` sets the reported RSS interval. The CLI reports unsupported statistical assumptions as diagnostics instead of inventing results.

## Model an assembly

1. Use **Create Part** to add a component. **Add Face** places another axial face on its local profile. Reusable part definitions and individual instances appear in the assembly tree.
2. Use **Dimension**, then select two faces. Enter the signed nominal separation, lower and upper deviations, a dimension role, and a process distribution. Driving dimensions update model geometry. Choose **New independent source** or reuse an existing source with a signed coefficient to link manufacturing effects across dimensions. Edit a source in the assembly tree to update every linked tolerance. Use **Correlate** to set a correlation between two sources. Reference and derived dimensions are informational; basic dimensions constrain nominal geometry without manufacturing variation. Bilateral, unilateral, and limit annotation styles are available.
3. Use **Constraint** to fix any selected face at an axial coordinate, fix a part translation, align faces, set a fixed offset, or bound motion. The model can have several movable parts. **Centerline** creates a selectable axial datum marker; the same Dimension tool can dimension a face to this marker.
4. Use **Contact** to mark compatible interface faces that cannot penetrate. A contact candidate enforces `x(second) >= x(first)`; it does not become active merely because two lines overlap on screen. Choose a **Position Policy** such as free movement, left or right seating, or centering between two opposing contacts.
5. Use **Measure Gap**, then select the first and second functional faces. Set optional acceptance limits and select a positioning policy. **Analyze** computes the automatic dimensional chain and the selected methods. Selecting a result highlights its contributors on the sketch.
6. Edit a dimension or tolerance in the tree or properties panel, then analyze again. Undo and redo are available with Ctrl+Z and Ctrl+Y. Use the wheel to zoom, middle-drag to pan, and Ctrl+0 to fit the assembly. Save as a `.stack1d` project and export a PDF, CSV, or XLSX report after analysis.

The nominal assembly coordinate is `global face x = instance translation + local face x`. Pixels never determine engineering dimensions. Sketch lane offsets distinguish components visually; contact lanes determine whether faces are mechanically compatible.

## What the calculations mean

**Worst case** optimizes a functional gap over manufacturing deviation bounds and feasible assembly positions using linear programming. The reported minimum and maximum are the possible envelope for a free policy, or the extrema under the specified seating/centering policy. It reports manufacturing deviations, translations, and active contacts at each extreme. For the supplied fixed-chain example, `G = D1 − D2 − D3 = 1.00 mm`, with exact bounds **0.55–1.45 mm**.

The floating-block example has a 100 ±0.20 mm housing, a 25 ±0.10 mm spacer, and a 40 ±0.15 mm block. Total clearance is **34.55–35.45 mm**. Its right-side gap is 0–35.45 mm if the block is free, 34.55–35.45 mm when left-seated, 0 mm when right-seated, and 17.275–17.725 mm when centered between the opposing contacts. Free movement has no single nominal position or probability distribution.

**RSS** computes an exact affine variance with independent or correlated manufacturing sources when the selected gap has a unique linear chain and assembly feasibility does not truncate the configured distributions. Specify a process standard deviation or a limit-to-sigma mapping for normal sources; drawing limits alone do not imply ±3σ. Correlated contributions use covariance allocation and may be negative. If contact switching or feasibility makes the affine statistical model invalid, the app reports why RSS is unavailable.

**Monte Carlo** samples manufacturing sources with a fixed seed, solves the feasible assembly for each sample, and separates valid assemblies from infeasible ones. It reports observed spread, percentiles, histogram, conditional specification failure, overall failure including assembly failures, PPM, and a confidence interval. A free-floating assembly needs a statistical positioning policy before a unique probability can be assigned to its gap. The solver never assigns a random floating position without such a policy.

## Example projects

| File | Purpose | Reference check |
| --- | --- | --- |
| `examples/a-fixed-chain.stack1d` | Three-dimension fixed chain | nominal 1.00, worst case 0.55–1.45 mm |
| `examples/b-floating-block.stack1d` | Spacer, moving block, opposing contacts | total clearance 34.55–35.45 mm |
| `examples/c-coupled-floating.stack1d` | Two coupled moving blocks | left-seated rear gap 49.55–50.45 mm |
| `examples/d-inconsistent-loop.stack1d` | Conflicting driving dimensions | infeasibility diagnostic |
| `examples/e-statistical-chain.stack1d` | Explicit normal process standard deviations | analytical RSS variance checked against simulation |

The inconsistent-loop project is intentionally invalid for analysis; it can still be opened for inspection and repair.

## Project format and architecture

`.stack1d` is a versioned ZIP archive containing `engineering.json`, `presentation.json`, and `manifest.json`. Saves use a temporary file and atomic replacement. Version-1 project archives migrate on load. Malformed files produce a readable error. The engineering model is independent of sketch zoom, pan, and annotation layout.

`stacklab/domain.py` defines persistent entities. `stacklab/compiler.py` checks references, dimensions, connectivity, and feasibility, then creates numerical constraint matrices. `stacklab/solvers.py` generates the dimensional chain and runs worst-case, RSS, and Monte Carlo analysis. `stacklab/services.py` provides commands, undo/redo, revision tracking, cached results, and cancellable background analysis. `stacklab/ui/` contains the PySide6 sketch and workspace. `stacklab/persistence.py` and `stacklab/reporting.py` handle projects and exports.

## Validate and package

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build_windows.ps1
```

The Windows build creates `dist\StackLab1D\StackLab1D.exe`; distribute its whole directory. See [Windows packaging](docs/windows-packaging.md) for the build environment and bundled example location.

StackLab models one axial coordinate with rigid parts, linear dimensions, translations, and declared contacts. Rotation, elastic deformation, form errors, and full 2D/3D GD&T are outside this model. Horizontal/vertical/parallel/perpendicular sketch constraints require a two-dimensional geometry kernel and do not have independent meaning in a strict 1D analysis. Centerlines here are selectable axial datum planes. Centered placement is solved exactly for a single moving component between two ordered opposing contacts; more general coupled centering and closest-position worst-case requests are diagnosed when unsupported. Engineering results must be reviewed against the real contact and process assumptions of the design.
