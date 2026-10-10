"""Linear worst-case, affine RSS, and feasible-assembly Monte Carlo analysis."""
from __future__ import annotations

from math import sqrt
from itertools import product
from typing import Callable, Iterable

import numpy as np
from scipy.linalg import null_space
from scipy.optimize import linprog

from .compiler import CompiledModel, ModelError, compile_project
from .domain import (
    AnalysisResult, AssemblyPolicy, ChainResult, ChainTerm, Diagnostic,
    ExtremeState, FaceRef, FunctionalRequirement, MonteCarloResult, Project, RSSResult,
    VariationSource, WorstCaseResult,
)


EPS = 1e-8


class UnsupportedAnalysis(ValueError):
    pass


class SimulationCancelled(RuntimeError):
    pass


def _matrices(model: CompiledModel, policy: AssemblyPolicy | None):
    eq = list(model.a_eq)
    eq_rhs = list(model.b_eq)
    ub = list(model.a_ub)
    ub_rhs = list(model.b_ub)
    if policy:
        if policy.kind in {"left_contact", "right_contact"}:
            if not policy.contact_ids:
                raise UnsupportedAnalysis(f"Policy '{policy.name}' needs an explicit contact pair")
            contacts = {c.id: c for c in model.project.contacts}
            for contact_id in policy.contact_ids:
                contact = contacts[contact_id]
                eq.append(model.gap_row(contact.first, contact.second))
                eq_rhs.append(0.0)
        if policy.kind == "centered" and policy.contact_ids:
            if len(policy.contact_ids) != 2:
                raise UnsupportedAnalysis("Centered policy needs exactly two opposing contact pairs")
            contacts = {c.id: c for c in model.project.contacts}
            left, right = (contacts[contact_id] for contact_id in policy.contact_ids)
            if left.second.instance_id != right.first.instance_id:
                raise UnsupportedAnalysis(
                    "Centered contacts must bound one moving part: left.second and right.first")
            # Equal available clearances seat the moving part at the midpoint.
            eq.append(model.gap_row(left.first, left.second) -
                      model.gap_row(right.first, right.second))
            eq_rhs.append(0.0)
        if policy.kind == "bounded":
            for instance_id, (lower, upper) in policy.bounds.items():
                row = np.zeros(len(model.variable_names))
                row[model.indices[f"translation:{instance_id}"]] = 1
                ub.extend((-row, row))
                ub_rhs.extend((-lower, upper))
    n = len(model.variable_names)
    return (np.array(eq, dtype=float).reshape(-1, n), np.array(eq_rhs),
            np.array(ub, dtype=float).reshape(-1, n), np.array(ub_rhs))


def _lp(model: CompiledModel, objective: np.ndarray, policy: AssemblyPolicy | None,
        source_values: dict[str, float] | None = None,
        additional_eq: tuple[np.ndarray, float] | None = None):
    ae, be, au, bu = _matrices(model, policy)
    if additional_eq is not None:
        ae = np.vstack((ae, additional_eq[0]))
        be = np.append(be, additional_eq[1])
    bounds = model.fixed_source_bounds(source_values) if source_values is not None else model.bounds
    return linprog(objective, A_ub=au if len(au) else None, b_ub=bu if len(bu) else None,
                   A_eq=ae if len(ae) else None, b_eq=be if len(be) else None,
                   bounds=bounds, method="highs")


def _require_success(response, action: str) -> np.ndarray:
    if response.status == 2:
        raise ModelError([Diagnostic("infeasible_assembly", f"No feasible assembly for {action}")])
    if response.status == 3:
        raise UnsupportedAnalysis(f"{action} is unbounded; add a ground or translation limit")
    if not response.success:
        raise ModelError([Diagnostic("solver_failure", f"Numerical solver failed for {action}: {response.message}")])
    return response.x


def _policy(project: Project, requirement: FunctionalRequirement) -> AssemblyPolicy | None:
    return next((p for p in project.policies if p.id == requirement.policy_id), None)


def _state(model: CompiledModel, requirement: FunctionalRequirement, vector: np.ndarray) -> ExtremeState:
    gap = float(model.gap_row(requirement.first, requirement.second, requirement.direction) @ vector)
    sources = {sid: float(vector[model.indices[f"source:{sid}"]]) for sid in model.source_ids}
    translations = {i.id: float(vector[model.indices[f"translation:{i.id}"]]) for i in model.project.instances}
    refs = {ref for contact in model.project.contacts for ref in (contact.first, contact.second)}
    refs.update((requirement.first, requirement.second))
    face_positions = {f"{ref.instance_id}:{ref.face_id}": float(model.face_row(ref) @ vector) for ref in refs}
    active = [contact.id for contact in model.project.contacts
              if abs(model.gap_row(contact.first, contact.second) @ vector) <= 1e-6]
    return ExtremeState(gap, sources, translations, face_positions, active)


def _source_zero(model: CompiledModel) -> dict[str, float]:
    return {sid: 0.0 for sid in model.source_ids}


def _fixed_gap(model: CompiledModel, row: np.ndarray, policy: AssemblyPolicy | None,
               source_values: dict[str, float]) -> tuple[float, float]:
    lo = _lp(model, row, policy, source_values)
    hi = _lp(model, -row, policy, source_values)
    low_vector = _require_success(lo, "minimum nominal gap")
    high_vector = _require_success(hi, "maximum nominal gap")
    return float(row @ low_vector), float(row @ high_vector)


def _select_l1(model: CompiledModel, policy: AssemblyPolicy, source_values: dict[str, float],
               target: dict[str, float]) -> np.ndarray:
    """Pick a feasible position closest to requested instance translations."""
    ae, be, au, bu = _matrices(model, policy)
    n = len(model.variable_names)
    ids = list(target)
    k = len(ids)
    objective = np.r_[np.zeros(n), np.ones(k)]
    eq = np.hstack((ae, np.zeros((len(ae), k))))
    inequalities = [np.hstack((au, np.zeros((len(au), k))))] if len(au) else []
    rhs = [bu] if len(au) else []
    for j, instance_id in enumerate(ids):
        upper_row = np.zeros(n + k)
        lower_row = np.zeros(n + k)
        upper_row[model.indices[f"translation:{instance_id}"]] = 1
        upper_row[n + j] = -1
        lower_row[model.indices[f"translation:{instance_id}"]] = -1
        lower_row[n + j] = -1
        inequalities.append(np.vstack((upper_row, lower_row)))
        # The second row is -translation - aux <= -target.
        rhs.append(np.array((target[instance_id], -target[instance_id])))
    response = linprog(objective,
        A_eq=eq if len(eq) else None, b_eq=be if len(eq) else None,
        A_ub=np.vstack(inequalities) if inequalities else None,
        b_ub=np.concatenate(rhs) if rhs else None,
        bounds=[*model.fixed_source_bounds(source_values), *[(0, None)] * k], method="highs")
    return _require_success(response, f"{policy.kind} assembly position")[:n]


def solve_nominal_geometry(project: Project) -> dict[tuple[str, str], float]:
    """Return global face coordinates at zero manufacturing deviation.

    Among feasible nominal configurations, choose one closest to the editable
    sketch's initial local coordinates and instance translations. This selection
    is for display only; analyses do not treat it as a physical contact policy.
    """
    if not project.instances:
        return {}
    model = compile_project(project)
    n = len(model.variable_names)
    targets: list[tuple[int, float]] = []
    definitions = {d.id: d for d in project.definitions}
    for instance in project.instances:
        for face in definitions[instance.definition_id].faces:
            targets.append((model.indices[f"local:{instance.id}:{face.id}"], face.local_x))
        targets.append((model.indices[f"translation:{instance.id}"], instance.translation))
    k = len(targets)
    ae, be, au, bu = _matrices(model, None)
    inequalities = [np.hstack((au, np.zeros((len(au), k))))] if len(au) else []
    rhs = [bu] if len(au) else []
    for j, (variable_index, target) in enumerate(targets):
        upper = np.zeros(n + k)
        lower = np.zeros(n + k)
        upper[variable_index], upper[n + j] = 1, -1
        lower[variable_index], lower[n + j] = -1, -1
        inequalities.append(np.vstack((upper, lower)))
        rhs.append(np.array((target, -target)))
    response = linprog(np.r_[np.zeros(n), np.ones(k)],
        A_eq=np.hstack((ae, np.zeros((len(ae), k)))) if len(ae) else None,
        b_eq=be if len(ae) else None,
        A_ub=np.vstack(inequalities) if inequalities else None,
        b_ub=np.concatenate(rhs) if rhs else None,
        bounds=[*model.fixed_source_bounds(_source_zero(model)), *[(0, None)] * k],
        method="highs")
    vector = _require_success(response, "nominal sketch geometry")[:n]
    return {(instance.id, face.id): float(model.face_row(FaceRef(instance.id, face.id)) @ vector)
        for instance in project.instances for face in definitions[instance.definition_id].faces}


def _selected_vector(model: CompiledModel, requirement: FunctionalRequirement,
                     policy: AssemblyPolicy | None, source_values: dict[str, float]) -> np.ndarray | None:
    row = model.gap_row(requirement.first, requirement.second, requirement.direction)
    if policy and policy.kind in {"closest", "centered"}:
        if policy.kind == "closest":
            target = {i.id: i.translation for i in model.project.instances}
        else:
            target = {}
            for instance in model.project.instances:
                translation = np.zeros(len(model.variable_names))
                translation[model.indices[f"translation:{instance.id}"]] = 1
                lo = _lp(model, translation, policy, source_values)
                hi = _lp(model, -translation, policy, source_values)
                low_vec = _require_success(lo, f"centered position of '{instance.name}'")
                high_vec = _require_success(hi, f"centered position of '{instance.name}'")
                target[instance.id] = (translation @ low_vec + translation @ high_vec) / 2
        return _select_l1(model, policy, source_values, target)
    lo = _lp(model, row, policy, source_values)
    hi = _lp(model, -row, policy, source_values)
    low_vec = _require_success(lo, "selected assembly position")
    high_vec = _require_success(hi, "selected assembly position")
    if abs(row @ low_vec - row @ high_vec) > 1e-7:
        return None
    return low_vec


def automatic_chain(model: CompiledModel, requirement: FunctionalRequirement,
                    policy: AssemblyPolicy | None = None) -> ChainResult:
    """Derive the exact affine equation if the measurement is fixed by equalities.

    Graph traversal identifies dimension dependencies for highlighting. Coefficients
    come from the full constraint matrix, including shared sources and redundancy.
    """
    row = model.gap_row(requirement.first, requirement.second, requirement.direction)
    ae, be, _, _ = _matrices(model, policy)
    source_indices = [model.indices[f"source:{sid}"] for sid in model.source_ids]
    geometry_indices = [i for i in range(len(row)) if i not in set(source_indices)]
    if not len(ae):
        return ChainResult(floating=True, degrees_of_freedom=model.assembly_dof)
    az = ae[:, geometry_indices]
    target = row[geometry_indices]
    weights, *_ = np.linalg.lstsq(az.T, target, rcond=None)
    residual = np.linalg.norm(az.T @ weights - target)
    if residual > 1e-7 * (1 + np.linalg.norm(target)):
        return ChainResult(floating=True, degrees_of_freedom=model.assembly_dof,
                           equation="Gap depends on a feasible assembly position")
    nominal = float(weights @ be)
    if abs(nominal) < 1e-10:
        nominal = 0.0
    coefficient = row[source_indices] - weights @ ae[:, source_indices]
    terms = [ChainTerm(sid, float(coefficient[j]), list(model.source_dimensions.get(sid, ())))
             for j, sid in enumerate(model.source_ids) if abs(coefficient[j]) > 1e-10]
    equation = f"G = {nominal:.6g}"
    for term in terms:
        equation += f" {'+' if term.coefficient >= 0 else '-'} {abs(term.coefficient):.6g}·Δ({term.source_id})"
    if any(d.code == "dependent_sources" for d in model.diagnostics):
        equation += "  [valid on feasible coupled sources; attribution is not unique]"
    return ChainResult(nominal, terms, equation, False, model.assembly_dof)


def worst_case(model: CompiledModel, requirement: FunctionalRequirement,
               policy: AssemblyPolicy | None = None) -> WorstCaseResult:
    kind = policy.kind if policy else "free"
    centered_affine = (kind == "centered" and policy is not None and
                       len(policy.contact_ids) == 2 and model.assembly_dof == 1)
    if kind in {"centered", "closest"} and model.assembly_dof and not centered_affine:
        raise UnsupportedAnalysis(
            f"Exact worst-case under '{kind}' for movable coupled components is unsupported; "
            "use a contact policy or inspect the free assembly envelope")
    row = model.gap_row(requirement.first, requirement.second, requirement.direction)
    min_vec = _require_success(_lp(model, row, policy), "worst-case minimum")
    max_vec = _require_success(_lp(model, -row, policy), "worst-case maximum")
    minimum_state = _state(model, requirement, min_vec)
    maximum_state = _state(model, requirement, max_vec)
    try:
        nominal_lo, nominal_hi = _fixed_gap(model, row, policy, _source_zero(model))
        nominal = nominal_lo if abs(nominal_lo - nominal_hi) <= 1e-7 else None
    except (ModelError, UnsupportedAnalysis):
        nominal = None
    meets = None
    if requirement.min_value is not None or requirement.max_value is not None:
        meets = ((requirement.min_value is None or minimum_state.gap >= requirement.min_value - EPS)
                 and (requirement.max_value is None or maximum_state.gap <= requirement.max_value + EPS))
    warnings = []
    if nominal is None:
        warnings.append("The nominal assembly does not determine a unique gap; use a positioning policy.")
    return WorstCaseResult(nominal, minimum_state.gap, maximum_state.gap,
        minimum_state, maximum_state, minimum_state.gap < -EPS, meets, kind, warnings)


def _source_std(source: VariationSource) -> float:
    if source.distribution == "fixed" or source.lower == source.upper:
        return 0.0
    if source.distribution == "uniform":
        return (source.upper - source.lower) / sqrt(12)
    if source.std is not None:
        return source.std
    if source.sigma_level is not None:
        return max(abs(source.lower - source.mean), abs(source.upper - source.mean)) / source.sigma_level
    raise UnsupportedAnalysis(
        f"Source '{source.name}' needs an explicit standard deviation or sigma mapping for RSS")


def _source_mean(source: VariationSource) -> float:
    # A uniform process's support fixes its mean, regardless of the optional
    # normal-process mean field stored on the editable source.
    if source.distribution == "uniform":
        return (source.lower + source.upper) / 2
    return source.mean


def _covariance(model: CompiledModel) -> np.ndarray:
    ids = model.source_ids
    standard_deviations = np.array([_source_std(model.sources[sid]) for sid in ids])
    correlation = np.eye(len(ids))
    for item in model.project.correlations:
        i, j = ids.index(item.first_source_id), ids.index(item.second_source_id)
        correlation[i, j] = correlation[j, i] = item.rho
    if len(ids) and np.linalg.eigvalsh(correlation).min() < -1e-8:
        raise UnsupportedAnalysis("Source correlation matrix is not positive semidefinite")
    return np.outer(standard_deviations, standard_deviations) * correlation


def _verify_statistical_source_support(model: CompiledModel, policy: AssemblyPolicy | None,
                                       covariance: np.ndarray) -> list[str]:
    """Reject source distributions that contradict driving-loop equalities."""
    ae, be, au, _ = _matrices(model, policy)
    ids = model.source_ids
    if not ids:
        return []
    source_indices = [model.indices[f"source:{sid}"] for sid in ids]
    geometry_indices = [i for i in range(len(model.variable_names)) if i not in set(source_indices)]
    az = ae[:, geometry_indices]
    ass = ae[:, source_indices]
    means = np.array([model.sources[sid].mean for sid in ids])
    projection = az @ np.linalg.pinv(az) if len(ae) else np.zeros((0, 0))
    residual = np.eye(len(ae)) - projection
    incompatible_mean = residual @ (be - ass @ means)
    incompatible_variance = residual @ ass @ covariance @ ass.T @ residual.T
    if np.linalg.norm(incompatible_mean) > 1e-7 or np.linalg.norm(incompatible_variance) > 1e-9:
        raise UnsupportedAnalysis(
            "Driving-loop equalities restrict manufacturing sources beyond the declared covariance; "
            "RSS would double-count or admit impossible combinations")
    feasibility = _lp(model, np.zeros(len(model.variable_names)), policy,
                      dict(zip(ids, map(float, means))))
    if feasibility.status == 2:
        raise UnsupportedAnalysis("Mean manufacturing values cannot form a feasible assembly")
    _require_success(feasibility, "mean assembly for RSS")
    if len(au):
        varying_normal = [sid for sid in ids if model.sources[sid].distribution == "normal" and
                          _source_std(model.sources[sid]) > 0]
        if varying_normal:
            raise UnsupportedAnalysis(
                "Unbounded normal process tails may violate assembly inequalities; "
                "RSS cannot describe the distribution of successfully assembled products. "
                "Use Monte Carlo to count infeasible samples")
        varying = [sid for sid in ids if model.sources[sid].lower < model.sources[sid].upper]
        if len(varying) > 12:
            raise UnsupportedAnalysis(
                "More than 12 bounded sources with assembly inequalities cannot be certified for RSS")
        fixed_values = {sid: model.sources[sid].mean for sid in ids}
        for corner in product(*[(model.sources[sid].lower, model.sources[sid].upper) for sid in varying]):
            values = {**fixed_values, **dict(zip(varying, corner))}
            response = _lp(model, np.zeros(len(model.variable_names)), policy, values)
            if response.status == 2:
                raise UnsupportedAnalysis(
                    "Some manufacturing values within declared bounds cannot assemble; "
                    "RSS must not ignore those infeasible outcomes")
            _require_success(response, "RSS feasible-support check")
    return []


def rss(model: CompiledModel, requirement: FunctionalRequirement,
        policy: AssemblyPolicy | None = None, sigma_level: float = 3.0) -> RSSResult:
    if sigma_level <= 0:
        raise ValueError("Sigma level must be positive")
    chain = automatic_chain(model, requirement, policy)
    if chain.floating:
        raise UnsupportedAnalysis(
            "RSS needs a unique affine gap. Select a seating contact or use Monte Carlo with a positioning policy")
    ids = model.source_ids
    coefficients = np.array([next((t.coefficient for t in chain.terms if t.source_id == sid), 0.0) for sid in ids])
    covariance = _covariance(model)
    warnings = _verify_statistical_source_support(model, policy, covariance)
    variance = float(coefficients @ covariance @ coefficients)
    mean = float(chain.nominal + sum(coefficients[j] * _source_mean(model.sources[sid])
                                     for j, sid in enumerate(ids)))
    # Euler allocation includes correlations; individual allocations may be negative.
    allocation = coefficients * (covariance @ coefficients)
    contributions = {sid: float(allocation[j]) for j, sid in enumerate(ids) if abs(allocation[j]) > 1e-14}
    std = sqrt(max(0.0, variance))
    if model.project.correlations:
        warnings.append("Variance contributions use covariance allocation and can be negative.")
    return RSSResult(mean, variance, std, sigma_level, mean - sigma_level * std,
                     mean + sigma_level * std, contributions, warnings)


def _samples(model: CompiledModel, count: int, rng: np.random.Generator) -> np.ndarray:
    ids = model.source_ids
    if not ids:
        return np.zeros((count, 0))
    values = np.empty((count, len(ids)))
    if model.project.correlations:
        if any(model.sources[sid].distribution != "normal" for sid in ids
               if model.sources[sid].lower != model.sources[sid].upper):
            raise UnsupportedAnalysis("Correlated Monte Carlo currently requires normal sources")
        covariance = _covariance(model)
        means = np.array([model.sources[sid].mean for sid in ids])
        values[:] = rng.multivariate_normal(means, covariance, count, check_valid="raise")
        return values
    for j, sid in enumerate(ids):
        source = model.sources[sid]
        if source.distribution == "fixed" or source.lower == source.upper:
            values[:, j] = source.mean
        elif source.distribution == "uniform":
            values[:, j] = rng.uniform(source.lower, source.upper, count)
        else:
            values[:, j] = rng.normal(source.mean, _source_std(source), count)
    return values


def _vectorized_safe(model: CompiledModel, policy: AssemblyPolicy | None,
                     chain: ChainResult) -> bool:
    if chain.floating or policy and policy.kind in {"centered", "closest"}:
        return False
    ae, be, au, _ = _matrices(model, policy)
    if len(au):
        return False
    source_indices = [model.indices[f"source:{sid}"] for sid in model.source_ids]
    geometry_indices = [i for i in range(len(model.variable_names)) if i not in set(source_indices)]
    az = ae[:, geometry_indices]
    if not len(ae):
        return True
    projection = az @ np.linalg.pinv(az)
    dependent_rhs = np.column_stack((be, ae[:, source_indices]))
    return np.linalg.norm((np.eye(len(ae)) - projection) @ dependent_rhs) < 1e-7


def _wilson(failures: int, count: int) -> tuple[float, float] | None:
    if count == 0:
        return None
    z = 1.959963984540054
    p = failures / count
    denominator = 1 + z*z/count
    center = (p + z*z/(2*count)) / denominator
    half = z * sqrt((p*(1-p) + z*z/(4*count))/count) / denominator
    return max(0.0, center-half), min(1.0, center+half)


def monte_carlo(model: CompiledModel, requirement: FunctionalRequirement,
                policy: AssemblyPolicy | None = None, *, samples: int = 10000, seed: int = 0,
                progress: Callable[[int, int], None] | None = None,
                cancelled: Callable[[], bool] | None = None) -> MonteCarloResult:
    if samples <= 0:
        raise ValueError("Monte Carlo sample count must be positive")
    rng = np.random.default_rng(seed)
    source_samples = _samples(model, samples, rng)
    chain = automatic_chain(model, requirement, policy)
    gaps: np.ndarray
    infeasible = 0
    if _vectorized_safe(model, policy, chain):
        coefficients = np.array([next((t.coefficient for t in chain.terms if t.source_id == sid), 0.0)
                                 for sid in model.source_ids])
        gaps = float(chain.nominal) + source_samples @ coefficients
        if cancelled and cancelled():
            raise SimulationCancelled("Simulation was cancelled")
        if progress:
            progress(samples, samples)
    else:
        row = model.gap_row(requirement.first, requirement.second, requirement.direction)
        accepted: list[float] = []
        for index, sample in enumerate(source_samples):
            if cancelled and cancelled():
                raise SimulationCancelled("Simulation was cancelled")
            source_values = dict(zip(model.source_ids, map(float, sample)))
            try:
                vector = _selected_vector(model, requirement, policy, source_values)
                if vector is None:
                    raise UnsupportedAnalysis(
                        "Free floating assembly has no statistical positioning model; select contact, centered, or closest")
            except ModelError as error:
                if any(d.code == "infeasible_assembly" for d in error.diagnostics):
                    infeasible += 1
                    if progress and (index % 100 == 0 or index + 1 == samples):
                        progress(index + 1, samples)
                    continue
                raise
            accepted.append(float(row @ vector))
            if progress and (index % 100 == 0 or index + 1 == samples):
                progress(index + 1, samples)
        gaps = np.array(accepted, dtype=float)
    valid = len(gaps)
    failures = 0
    if requirement.min_value is not None:
        failures += int(np.count_nonzero(gaps < requirement.min_value))
    if requirement.max_value is not None:
        failures += int(np.count_nonzero(gaps > requirement.max_value))
    if valid:
        percentiles = {str(p): float(np.percentile(gaps, p)) for p in (1, 5, 50, 95, 99)}
        hist, edges = np.histogram(gaps, bins=min(40, max(1, int(sqrt(valid)))))
        minimum, maximum = float(np.min(gaps)), float(np.max(gaps))
        mean, std = float(np.mean(gaps)), float(np.std(gaps, ddof=1)) if valid > 1 else 0.0
    else:
        percentiles, hist, edges = {}, [], []
        minimum = maximum = mean = std = None
    conditional = failures / valid if valid else None
    overall_failures = failures + infeasible
    overall = overall_failures / samples
    return MonteCarloResult(samples, valid, infeasible, seed, minimum, maximum, mean, std,
        percentiles, list(map(float, edges)), list(map(int, hist)), failures,
        conditional, overall, conditional * 1e6 if conditional is not None else None,
        _wilson(overall_failures, samples))


def analyze(project: Project, requirement_id: str, *,
            methods: Iterable[str] = ("worst_case", "rss"), samples: int = 10000,
            seed: int = 0, sigma_level: float = 3.0,
            progress: Callable[[int, int], None] | None = None,
            cancelled: Callable[[], bool] | None = None) -> AnalysisResult:
    """Analyze one functional requirement; unsupported methods return diagnostics."""
    requirement = next((r for r in project.requirements if r.id == requirement_id), None)
    if requirement is None:
        raise ValueError(f"Unknown functional requirement '{requirement_id}'")
    model = compile_project(project)
    policy = _policy(project, requirement)
    diagnostics = list(model.diagnostics)
    try:
        chain = automatic_chain(model, requirement, policy)
    except UnsupportedAnalysis as exc:
        chain = ChainResult(floating=True, degrees_of_freedom=model.assembly_dof,
                            equation="Policy cannot define a valid dimensional chain")
        diagnostics.append(Diagnostic("unsupported_policy", str(exc), [requirement.id], "warning"))
    result = AnalysisResult(requirement.id, chain, diagnostics=diagnostics)
    requested = set(methods)
    unknown = requested - {"worst_case", "rss", "monte_carlo"}
    if unknown:
        raise ValueError(f"Unknown analysis methods: {', '.join(sorted(unknown))}")
    for method in ("worst_case", "rss", "monte_carlo"):
        if method not in requested:
            continue
        try:
            if method == "worst_case":
                result.worst_case = worst_case(model, requirement, policy)
            elif method == "rss":
                result.rss = rss(model, requirement, policy, sigma_level)
            else:
                result.monte_carlo = monte_carlo(model, requirement, policy,
                    samples=samples, seed=seed, progress=progress, cancelled=cancelled)
        except UnsupportedAnalysis as exc:
            result.diagnostics.append(Diagnostic("unsupported_analysis", f"{method}: {exc}",
                                                 [requirement.id], "warning"))
    return result
