"""Atomic project storage and portable HTML/CSV reports."""

from __future__ import annotations

import csv
from html import escape
import json
import math
import os
from pathlib import Path
import tempfile

from .model import Project
from .solver import Analysis


def load_project(path: str | Path) -> Project:
    path = Path(path)
    if path.stat().st_size > 5_000_000:
        raise ValueError("The project file exceeds the 5 MB limit.")
    return Project.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _atomic_text(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix=".odiim-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save_project(project: Project, path: str | Path) -> None:
    project.validate()
    _atomic_text(path, json.dumps(project.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def number(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "Unavailable"
    if not math.isfinite(value):
        return "Unbounded"
    if abs(value) < 0.5 * 10 ** -digits:
        value = 0.0
    return f"{value:.{digits}f}"


def export_csv(project: Project, analysis: Analysis, path: str | Path) -> None:
    import io
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["Project", project.name, "Units", "mm", "Status", analysis.status])
    writer.writerow(["Gap", project.gap.name if project.gap else "", "Reference", number(analysis.nominal),
                     "Minimum", number(analysis.minimum), "Maximum", number(analysis.maximum)])
    writer.writerow([])
    writer.writerow(["ID", "Name", "Type", "From", "To", "Nominal", "Lower deviation", "Upper deviation", "At minimum gap", "At maximum gap"])
    for dimension in project.dimensions:
        actual = lambda coords: number(coords[dimension.end] - coords[dimension.start]) if coords else "Unavailable"
        writer.writerow([dimension.id, dimension.name, dimension.kind, project.point(dimension.start).name,
                         project.point(dimension.end).name, dimension.nominal, dimension.lower, dimension.upper,
                         actual(analysis.minimum_positions), actual(analysis.maximum_positions)])
    writer.writerow([])
    writer.writerow(["Fit", "Mode", "Size clearance minimum", "Size clearance maximum", "Nominal-size shift minimum", "Nominal-size shift maximum"])
    for fit in analysis.fits:
        writer.writerow([fit.name, fit.mode, number(fit.clearance_min), number(fit.clearance_max), number(fit.shift_min), number(fit.shift_max)])
    for message in analysis.warnings + analysis.conflicts:
        writer.writerow(["Note", message])
    # Excel must not interpret project labels as formulas.
    text = stream.getvalue()
    rows = list(csv.reader(io.StringIO(text)))
    cleaned = io.StringIO(newline="")
    writer = csv.writer(cleaned)
    for row in rows:
        writer.writerow(["'" + value if value.lstrip().startswith(("=", "+", "@")) else value for value in row])
    _atomic_text(path, "\ufeff" + cleaned.getvalue())


def export_html(project: Project, analysis: Analysis, path: str | Path) -> None:
    from .drawing import svg_sketch
    table_rows = []
    for dimension in project.dimensions:
        actual = lambda coords: number(coords[dimension.end] - coords[dimension.start]) if coords else "Unavailable"
        table_rows.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in
            [dimension.name, dimension.kind, f"{project.point(dimension.start).name} → {project.point(dimension.end).name}",
             number(dimension.nominal), f"{dimension.lower:+.3f} / {dimension.upper:+.3f}",
             actual(analysis.minimum_positions), actual(analysis.maximum_positions)]) + "</tr>")
    fit_rows = []
    for fit in analysis.fits:
        fit_rows.append("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in
            [fit.name, fit.mode, number(fit.clearance_min), number(fit.clearance_max),
             number(fit.shift_min), number(fit.shift_max), "Some sizes interfere" if fit.fit_risk else "No negative size clearance found"]) + "</tr>")
    notes = "".join(f"<li>{escape(note)}</li>" for note in analysis.warnings + analysis.conflicts)
    sketches = "".join(f"<h3>{name}</h3>{svg_sketch(project, coords, label=name)}" for name, coords in
        [("Reference pose at nominal sizes", analysis.nominal_positions), ("Minimum-gap assembly", analysis.minimum_positions),
         ("Maximum-gap assembly", analysis.maximum_positions)] if coords)
    requirement = ""
    if project.gap:
        requirement = f"Requirement: {number(project.gap.minimum_allowed) if project.gap.minimum_allowed is not None else 'no lower limit'} to {number(project.gap.maximum_allowed) if project.gap.maximum_allowed is not None else 'no upper limit'} mm."
    text = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>{escape(project.name)} — Odiim Stackup</title>
<style>body{{font:15px system-ui,sans-serif;color:#1e293b;max-width:1120px;margin:40px auto;padding:0 24px}}h1{{margin-bottom:6px}}p{{line-height:1.5}}.cards{{display:flex;gap:16px;margin:24px 0}}.card{{background:#f1f5f9;padding:20px;flex:1;border-radius:12px}}.card b{{display:block;font-size:27px;margin-top:8px}}table{{border-collapse:collapse;width:100%;margin:20px 0;font-size:13px}}th,td{{text-align:left;border-bottom:1px solid #e2e8f0;padding:10px}}th{{background:#f1f5f9}}svg{{width:100%;border:1px solid #e2e8f0;border-radius:10px}}.note{{color:#64748b}}li{{margin:8px 0}}@media print{{body{{margin:0}}svg,table,.cards{{break-inside:avoid}}}}</style>
<h1>{escape(project.name)}</h1><p class="note">Odiim 1D Stackup · millimetres · constrained worst-case analysis</p>
<p><b>{escape(analysis.status.upper())}</b> · {escape(analysis.message)}</p><p>{escape(requirement)}</p>
<div class="cards"><div class="card">Reference gap<b>{number(analysis.nominal)} mm</b></div><div class="card">Minimum gap<b>{number(analysis.minimum)} mm</b></div><div class="card">Maximum gap<b>{number(analysis.maximum)} mm</b></div></div>
<p>Gap meets its limits for feasible assemblies: {escape(str(analysis.meets_gap_limits))}. This does not certify that every manufactured size combination can assemble.</p>
{sketches}<h2>Dimensions and extreme witnesses</h2><table><tr><th>Name</th><th>Kind</th><th>From → to</th><th>Nominal</th><th>Deviations</th><th>At min gap</th><th>At max gap</th></tr>{''.join(table_rows)}</table>
<h2>Assembly freedom and size-clearance checks</h2><table><tr><th>Fit</th><th>Mode</th><th>Clearance min</th><th>Clearance max</th><th>Shift min</th><th>Shift max</th><th>Size check</th></tr>{''.join(fit_rows)}</table>
<p>Shift values use nominal manufacturing sizes and are measured from the chosen reference pose, relative to the slot's left face. Size clearance is slot width minus body width before the fit constraints filter out incompatible sizes. Separate fits are audited independently.</p>
<ul>{notes}</ul><h2>Model</h2><p>For each dimension: nominal + lower deviation ≤ x(to) − x(from) ≤ nominal + upper deviation. Free fits enforce containment. Seated fits additionally impose face contact. Centered fits impose equal left and right gaps. The objective minimizes or maximizes x(gap end) − x(gap start).</p>
<p class="note">The sketch's vertical layout does not enter the calculation. This model covers 1D translation, linear size variation and stated contacts. Rotations, angular tolerances, form, elastic deformation and 2D/3D GD&amp;T are outside its scope. No statistical distribution or RSS result is assumed.</p></html>'''
    _atomic_text(path, text)
