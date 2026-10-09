"""Constrained 1D worst-case analysis with dimension-dependent assembly freedom.

Each feature has one shared x-coordinate. Manufacturing dimensions, movement
limits, containment and seating act on those coordinates in the same LP. This
preserves coupling and cancellation when a face is reused in several relations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import numpy as np
from scipy.optimize import linprog

from .model import Project

EPS = 1e-7  # millimetres; comparison tolerance, not a manufacturing allowance


@dataclass
class FitResult:
    id: str
    name: str
    mode: str
    clearance_min: float | None
    clearance_max: float | None
    shift_min: float | None = None
    shift_max: float | None = None

    @property
    def fit_risk(self) -> bool:
        return self.clearance_min is not None and self.clearance_min < -EPS


@dataclass
class Analysis:
    status: str
    message: str
    nominal: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    nominal_positions: dict[str, float] = field(default_factory=dict)
    minimum_positions: dict[str, float] = field(default_factory=dict)
    maximum_positions: dict[str, float] = field(default_factory=dict)
    fits: list[FitResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    meets_gap_limits: bool | None = None


@dataclass
class _Problem:
    ids: list[str]
    index: dict[str, int]
    ub: list[list[float]] = field(default_factory=list)
    ub_values: list[float] = field(default_factory=list)
    eq: list[list[float]] = field(default_factory=list)
    eq_values: list[float] = field(default_factory=list)
    ub_labels: list[str] = field(default_factory=list)
    eq_labels: list[str] = field(default_factory=list)

    def expression(self, coefficients: dict[str, float]) -> list[float]:
        row = [0.0] * len(self.ids)
        for point_id, coefficient in coefficients.items():
            row[self.index[point_id]] += coefficient
        return row

    def difference(self, start: str, end: str) -> list[float]:
        return self.expression({start: -1.0, end: 1.0})

    def interval(self, row: list[float], lower: float, upper: float, label: str) -> None:
        if lower == upper:
            self.eq.append(row)
            self.eq_values.append(lower)
            self.eq_labels.append(label)
        else:
            self.ub.extend([row, [-v for v in row]])
            self.ub_values.extend([upper, -lower])
            self.ub_labels.extend([label, label])

    def less_equal(self, row: list[float], value: float, label: str) -> None:
        self.ub.append(row)
        self.ub_values.append(value)
        self.ub_labels.append(label)

    def solve(self, objective: list[float] | np.ndarray, exclude: set[str] | None = None):
        excluded = exclude or set()
        ub = [(row, value) for row, value, label in zip(self.ub, self.ub_values, self.ub_labels) if label not in excluded]
        eq = [(row, value) for row, value, label in zip(self.eq, self.eq_values, self.eq_labels) if label not in excluded]
        return linprog(
            objective,
            A_ub=[row for row, _ in ub] or None,
            b_ub=[value for _, value in ub] or None,
            A_eq=[row for row, _ in eq] or None,
            b_eq=[value for _, value in eq] or None,
            bounds=[(None, None)] * len(self.ids),  # coordinates can be negative
            method="highs",
            options={"primal_feasibility_tolerance": 1e-9, "dual_feasibility_tolerance": 1e-9},
        )


def _problem(project: Project, *, nominal_sizes: bool = False, sizes_only: bool = False) -> _Problem:
    ids = [point.id for point in project.points]
    problem = _Problem(ids, {point_id: i for i, point_id in enumerate(ids)})
    problem.interval(problem.expression({project.datum: 1.0}), 0.0, 0.0, "Datum")
    for dimension in project.dimensions:
        if sizes_only and dimension.kind != "size":
            continue
        low, high = dimension.minimum, dimension.maximum
        if nominal_sizes and dimension.kind == "size":
            low = high = dimension.nominal
        problem.interval(problem.difference(dimension.start, dimension.end), low, high, dimension.id + " · " + dimension.name)
    if not sizes_only:
        for body in project.bodies:
            problem.less_equal(problem.difference(body.right, body.left), 0.0, "Orientation: " + body.id + " · " + body.name)
        for fit in project.fits:
            label = fit.id + " · " + fit.name
            # slot_left <= body_left <= body_right <= slot_right
            problem.less_equal(problem.difference(fit.body_left, fit.slot_left), 0.0, label)
            problem.less_equal(problem.difference(fit.body_right, fit.body_left), 0.0, label)
            problem.less_equal(problem.difference(fit.slot_right, fit.body_right), 0.0, label)
            if fit.mode == "left":
                problem.interval(problem.difference(fit.slot_left, fit.body_left), 0, 0, label)
            elif fit.mode == "right":
                problem.interval(problem.difference(fit.body_right, fit.slot_right), 0, 0, label)
            elif fit.mode == "centered":
                row = problem.expression({fit.body_left: 1, fit.body_right: 1, fit.slot_left: -1, fit.slot_right: -1})
                problem.interval(row, 0, 0, label)
    return problem


def _positions(problem: _Problem, result) -> dict[str, float]:
    return {point_id: float(result.x[i]) for i, point_id in enumerate(problem.ids)}


def _closest(problem: _Problem, project: Project, gap_value: float | None = None):
    """Choose a witness close to the sketch without changing any gap extremum."""
    n = len(problem.ids)
    ub = [row + [0.0] * n for row in problem.ub]
    rhs = list(problem.ub_values)
    eq = [row + [0.0] * n for row in problem.eq]
    eq_rhs = list(problem.eq_values)
    origin = project.point(project.datum).x
    for point in project.points:
        i = problem.index[point.id]
        target = point.x - origin
        row = [0.0] * (2 * n)
        row[i], row[n + i] = 1.0, -1.0
        ub.append(row)
        rhs.append(target)
        row = [0.0] * (2 * n)
        row[i], row[n + i] = -1.0, -1.0
        ub.append(row)
        rhs.append(-target)
    if gap_value is not None and project.gap:
        eq.append(problem.difference(project.gap.start, project.gap.end) + [0.0] * n)
        eq_rhs.append(gap_value)
    return linprog(
        [0.0] * n + [1.0] * n,
        A_ub=ub or None, b_ub=rhs or None,
        A_eq=eq or None, b_eq=eq_rhs or None,
        bounds=[(None, None)] * n + [(0.0, None)] * n,
        method="highs",
        options={"primal_feasibility_tolerance": 1e-9, "dual_feasibility_tolerance": 1e-9},
    )


def _bound(problem: _Problem, row: list[float]) -> tuple[float | None, float | None]:
    low, high = problem.solve(row), problem.solve([-v for v in row])
    return (float(low.fun) if low.success else None, -float(high.fun) if high.success else None)


def _conflicting_labels(problem: _Problem) -> list[str]:
    """Return a small irreducible inconsistent set, retaining the coordinate datum."""
    labels = list(dict.fromkeys(problem.eq_labels + problem.ub_labels))
    excluded: set[str] = set()
    for label in labels[:80]:
        if label == "Datum":
            continue
        result = problem.solve([0.0] * len(problem.ids), exclude=excluded | {label})
        if result.status == 2:
            excluded.add(label)
    return [label for label in labels if label not in excluded and label != "Datum"]


def analyze(project: Project) -> Analysis:
    try:
        project.validate()
    except ValueError as exc:
        return Analysis("invalid", str(exc))
    if not project.points:
        return Analysis("incomplete", "Draw a body or add points, then define dimensions and a gap.")
    if project.gap is None:
        return Analysis("incomplete", "Pick the two face points that define the functional gap.")
    problem = _problem(project)
    feasible = problem.solve([0.0] * len(problem.ids))
    if feasible.status == 2:
        return Analysis("infeasible", "No assembly can satisfy all these dimensions and fit conditions.", conflicts=_conflicting_labels(problem))
    if not feasible.success:
        return Analysis("error", "The numerical solver could not establish feasibility: " + feasible.message)

    row = problem.difference(project.gap.start, project.gap.end)
    low, high = problem.solve(row), problem.solve([-v for v in row])
    if low.status not in (0, 3) or high.status not in (0, 3):
        return Analysis("error", "The numerical solver could not determine the gap limits.")
    minimum = float(low.fun) if low.success else -math.inf
    maximum = -float(high.fun) if high.success else math.inf
    result = Analysis("ok", "Worst-case limits over feasible 1D assemblies.", minimum=minimum, maximum=maximum)
    for dimension in project.dimensions:
        if dimension.kind == "size" and not dimension.minimum <= dimension.nominal <= dimension.maximum:
            result.warnings.append(f"{dimension.name}: its nominal value is outside the declared tolerance interval; the reference uses that nominal value only as a design reference.")
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        result.status = "unbounded"
        result.message = "The gap is not fully located. Add a contact, placement range, dimension or fit between its parts."

    nominal_problem = _problem(project, nominal_sizes=True)
    reference = _closest(nominal_problem, project)
    if reference.success:
        result.nominal_positions = _positions(nominal_problem, reference)
        result.nominal = result.nominal_positions[project.gap.end] - result.nominal_positions[project.gap.start]
    else:
        result.warnings.append("Nominal sizes do not satisfy the assembly constraints; no nominal pose is shown.")

    for value, solution, attr in ((minimum, low, "minimum_positions"), (maximum, high, "maximum_positions")):
        if math.isfinite(value):
            witness = _closest(problem, project, value)
            setattr(result, attr, _positions(problem, witness if witness.success else solution))

    raw = _problem(project, sizes_only=True)
    raw_feasible = raw.solve([0.0] * len(raw.ids)).success
    for fit in project.fits:
        clearance = raw.expression({fit.slot_right: 1, fit.slot_left: -1, fit.body_right: -1, fit.body_left: 1})
        capacity_min, capacity_max = _bound(raw, clearance) if raw_feasible else (None, None)
        fit_result = FitResult(fit.id, fit.name, fit.mode, capacity_min, capacity_max)
        if result.nominal_positions:
            movement = nominal_problem.difference(fit.slot_left, fit.body_left)
            shift_low, shift_high = _bound(nominal_problem, movement)
            reference_offset = result.nominal_positions[fit.body_left] - result.nominal_positions[fit.slot_left]
            if shift_low is not None:
                fit_result.shift_min = shift_low - reference_offset
            if shift_high is not None:
                fit_result.shift_max = shift_high - reference_offset
        result.fits.append(fit_result)
        if capacity_min is None or capacity_max is None:
            result.warnings.append(f"{fit.name}: dimension the two widths to obtain a bounded size-clearance check.")
        elif capacity_min < -EPS:
            result.warnings.append(f"{fit.name}: some permitted size combinations interfere. Gap limits cover only combinations that can assemble.")
    if len(project.fits) > 1:
        result.warnings.append("Size-clearance checks apply to each fit separately; they do not certify all size combinations for multiple coupled fits.")
    if result.status == "ok":
        gap = project.gap
        result.meets_gap_limits = (
            (gap.minimum_allowed is None or minimum >= gap.minimum_allowed - EPS)
            and (gap.maximum_allowed is None or maximum <= gap.maximum_allowed + EPS)
        )
    return result
