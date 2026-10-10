"""Validate and compile an editable 1D assembly into a linear numerical model."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

import networkx as nx
import numpy as np
from scipy.optimize import linprog

from .domain import (
    AssemblyConstraint, ContactPair, Diagnostic, Dimension, FaceRef, Project,
    VariationSource,
)


class ModelError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = diagnostics
        super().__init__("; ".join(d.message for d in diagnostics if d.severity == "error"))


@dataclass(frozen=True)
class CompiledModel:
    project: Project
    variable_names: tuple[str, ...]
    indices: dict[str, int]
    source_ids: tuple[str, ...]
    sources: dict[str, VariationSource]
    source_dimensions: dict[str, tuple[str, ...]]
    a_eq: np.ndarray
    b_eq: np.ndarray
    a_ub: np.ndarray
    b_ub: np.ndarray
    bounds: tuple[tuple[float | None, float | None], ...]
    diagnostics: tuple[Diagnostic, ...]
    dependency_graph: nx.Graph
    geometry_dof: int
    assembly_dof: int

    def face_row(self, ref: FaceRef) -> np.ndarray:
        row = np.zeros(len(self.variable_names))
        row[self.indices[f"local:{ref.instance_id}:{ref.face_id}"]] = 1.0
        row[self.indices[f"translation:{ref.instance_id}"]] = 1.0
        return row

    def local_row(self, ref: FaceRef) -> np.ndarray:
        row = np.zeros(len(self.variable_names))
        row[self.indices[f"local:{ref.instance_id}:{ref.face_id}"]] = 1.0
        return row

    def gap_row(self, first: FaceRef, second: FaceRef, direction: int = 1) -> np.ndarray:
        return direction * (self.face_row(second) - self.face_row(first))

    def fixed_source_bounds(self, values: dict[str, float]) -> tuple[tuple[float | None, float | None], ...]:
        result = list(self.bounds)
        for source_id, value in values.items():
            result[self.indices[f"source:{source_id}"]] = (value, value)
        return tuple(result)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def validate_project(project: Project) -> list[Diagnostic]:
    """Structural validation only; incomplete sketches remain editable."""
    diagnostics: list[Diagnostic] = []
    def error(code: str, message: str, *ids: str) -> None:
        diagnostics.append(Diagnostic(code, message, list(ids)))
    if project.unit not in {"mm", "in"}:
        error("unit", f"Unsupported unit '{project.unit}'", project.id)
    entities = [project, *project.definitions, *project.instances, *project.dimensions,
                *project.sources, *project.constraints, *project.contacts, *project.requirements,
                *project.policies, *project.correlations, *project.parameters, *project.joints, *project.assemblies,
                *project.analysis_cases]
    entities.extend(face for part in project.definitions for face in part.faces)
    entities.extend(outline for part in project.definitions for outline in part.outlines)
    seen: set[str] = set()
    for entity in entities:
        if not isinstance(entity.id, str) or not entity.id:
            error("id", "Every entity requires a nonempty ID")
        elif entity.id in seen:
            error("duplicate_id", f"Duplicate entity ID '{entity.id}'", entity.id)
        else:
            seen.add(entity.id)
    definitions = {d.id: d for d in project.definitions}
    instances = {i.id: i for i in project.instances}
    sources = {s.id: s for s in project.sources}
    policies = {p.id: p for p in project.policies}
    contacts = {c.id: c for c in project.contacts}
    requirements = {r.id: r for r in project.requirements}
    def check_ref(ref: FaceRef | None, owner: str) -> None:
        if ref is None:
            error("missing_face", f"'{owner}' requires a face reference", owner)
        elif ref.instance_id not in instances:
            error("missing_instance", f"'{owner}' refers to missing instance '{ref.instance_id}'", owner)
        elif instances[ref.instance_id].definition_id not in definitions:
            error("missing_definition", f"Instance '{ref.instance_id}' has no valid definition", owner, ref.instance_id)
        elif ref.face_id not in {f.id for f in definitions[instances[ref.instance_id].definition_id].faces}:
            error("missing_face", f"'{owner}' refers to missing face '{ref.face_id}'", owner, ref.face_id)
    for definition in project.definitions:
        face_ids = {face.id for face in definition.faces}
        for face in definition.faces:
            if not _finite(face.local_x) or not _finite(face.local_y):
                error("coordinate", f"Face '{face.name}' has a nonfinite local coordinate", face.id)
        for outline in definition.outlines:
            if len(outline.vertices) < (3 if outline.closed else 2):
                error("outline", f"Outline '{outline.name}' needs at least {'three' if outline.closed else 'two'} vertices", outline.id)
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", outline.color):
                error("outline_color", f"Outline '{outline.name}' needs a six-digit hex color", outline.id)
            for vertex in outline.vertices:
                if not _finite(vertex.x) or not _finite(vertex.y):
                    error("outline_coordinate", f"Outline '{outline.name}' has a nonfinite vertex", outline.id)
                if vertex.face_id is not None and vertex.face_id not in face_ids:
                    error("outline_face", f"Outline '{outline.name}' binds to a missing face", outline.id)
    for instance in project.instances:
        if instance.definition_id not in definitions:
            error("missing_definition", f"Instance '{instance.name}' has no valid definition", instance.id)
        if not _finite(instance.translation):
            error("coordinate", f"Instance '{instance.name}' has a nonfinite translation", instance.id)
    for dimension in project.dimensions:
        check_ref(dimension.first, dimension.id)
        check_ref(dimension.second, dimension.id)
        if dimension.kind not in {"driving", "reference", "basic", "derived"}:
            error("dimension_kind", f"Dimension '{dimension.name}' has unsupported kind", dimension.id)
        if dimension.display_style not in {"bilateral", "unilateral", "limits"}:
            error("dimension_display", f"Dimension '{dimension.name}' has unsupported display style", dimension.id)
        if not all(_finite(v) for v in (dimension.nominal, dimension.tolerance.lower,
                                         dimension.tolerance.upper, dimension.coefficient)):
            error("dimension_value", f"Dimension '{dimension.name}' has a nonfinite value", dimension.id)
        elif dimension.tolerance.lower > dimension.tolerance.upper:
            error("tolerance", f"Dimension '{dimension.name}' lower deviation exceeds upper deviation", dimension.id)
        if dimension.source_id and dimension.source_id not in sources:
            error("missing_source", f"Dimension '{dimension.name}' refers to missing source", dimension.id)
        if dimension.first == dimension.second and dimension.kind == "driving" and abs(dimension.nominal) > 1e-9:
            error("self_dimension", f"Dimension '{dimension.name}' spans the same face but is nonzero", dimension.id)
    for source in project.sources:
        if source.distribution not in {"normal", "uniform", "fixed"}:
            error("distribution", f"Source '{source.name}' has unsupported distribution", source.id)
        if not all(_finite(v) for v in (source.lower, source.upper, source.mean)) or source.lower > source.upper:
            error("source_bounds", f"Source '{source.name}' has invalid deviation bounds", source.id)
        if source.std is not None and (not _finite(source.std) or source.std < 0):
            error("source_std", f"Source '{source.name}' has invalid process standard deviation", source.id)
        if source.sigma_level is not None and (not _finite(source.sigma_level) or source.sigma_level <= 0):
            error("source_sigma", f"Source '{source.name}' has invalid sigma mapping", source.id)
    constraint_kinds = {"fixed_position", "fixed_face", "coincident", "fixed_offset", "bounded_translation",
                        "unilateral_contact", "clearance_joint", "floating"}
    for constraint in project.constraints:
        if constraint.kind not in constraint_kinds:
            error("constraint_kind", f"Constraint '{constraint.name}' has unsupported kind", constraint.id)
        if constraint.kind in {"fixed_position", "bounded_translation", "floating"}:
            if constraint.instance_id not in instances:
                error("missing_instance", f"Constraint '{constraint.name}' needs a valid instance", constraint.id)
        elif constraint.kind == "fixed_face":
            check_ref(constraint.first, constraint.id)
        else:
            check_ref(constraint.first, constraint.id)
            check_ref(constraint.second, constraint.id)
        if constraint.kind in {"clearance_joint", "bounded_translation"} and (
            constraint.lower is None or constraint.upper is None or
            not _finite(constraint.lower) or not _finite(constraint.upper) or constraint.lower > constraint.upper
        ):
            error("constraint_bounds", f"Constraint '{constraint.name}' needs ordered finite bounds", constraint.id)
        if not _finite(constraint.value):
            error("constraint_value", f"Constraint '{constraint.name}' has a nonfinite value", constraint.id)
    for contact in project.contacts:
        check_ref(contact.first, contact.id)
        check_ref(contact.second, contact.id)
        if contact.first.instance_id in instances and contact.second.instance_id in instances:
            first_def = definitions.get(instances[contact.first.instance_id].definition_id)
            second_def = definitions.get(instances[contact.second.instance_id].definition_id)
            if first_def and second_def:
                first_face = next((f for f in first_def.faces if f.id == contact.first.face_id), None)
                second_face = next((f for f in second_def.faces if f.id == contact.second.face_id), None)
                if first_face and second_face and (first_face.lane != second_face.lane or first_face.lane != contact.lane):
                    error("contact_lane", f"Contact '{contact.name or contact.id}' has incompatible interface lanes", contact.id)
    for policy in project.policies:
        if policy.kind not in {"free", "left_contact", "right_contact", "centered", "closest", "bounded"}:
            error("policy_kind", f"Policy '{policy.name}' has unsupported kind", policy.id)
        for contact_id in policy.contact_ids:
            if contact_id not in contacts:
                error("missing_contact", f"Policy '{policy.name}' refers to missing contact", policy.id)
        for instance_id, bounds in policy.bounds.items():
            if instance_id not in instances or len(bounds) != 2 or not all(_finite(v) for v in bounds) or bounds[0] > bounds[1]:
                error("policy_bounds", f"Policy '{policy.name}' has invalid translation bounds", policy.id)
    for requirement in project.requirements:
        check_ref(requirement.first, requirement.id)
        check_ref(requirement.second, requirement.id)
        if requirement.direction not in {-1, 1}:
            error("direction", f"Requirement '{requirement.name}' direction must be +1 or -1", requirement.id)
        if requirement.policy_id and requirement.policy_id not in policies:
            error("missing_policy", f"Requirement '{requirement.name}' refers to missing policy", requirement.id)
        if requirement.min_value is not None and not _finite(requirement.min_value):
            error("requirement_limit", f"Requirement '{requirement.name}' minimum is invalid", requirement.id)
        if requirement.max_value is not None and not _finite(requirement.max_value):
            error("requirement_limit", f"Requirement '{requirement.name}' maximum is invalid", requirement.id)
        if (requirement.min_value is not None and requirement.max_value is not None and
                requirement.min_value > requirement.max_value):
            error("requirement_limit", f"Requirement '{requirement.name}' has reversed limits", requirement.id)
        if (not isinstance(requirement.methods, list) or not requirement.methods or
                any(method not in {"worst_case", "rss", "monte_carlo"}
                    for method in requirement.methods)):
            error("analysis_methods", f"Requirement '{requirement.name}' has invalid analysis methods", requirement.id)
    correlation_pairs: set[frozenset[str]] = set()
    for correlation in project.correlations:
        if correlation.first_source_id not in sources or correlation.second_source_id not in sources:
            error("missing_source", "Correlation refers to a missing explicit source")
        if correlation.first_source_id == correlation.second_source_id:
            error("correlation", "Correlation must refer to two different sources", correlation.id)
        pair = frozenset((correlation.first_source_id, correlation.second_source_id))
        if pair in correlation_pairs:
            error("correlation", "Duplicate source correlation pair", correlation.id)
        correlation_pairs.add(pair)
        if not _finite(correlation.rho) or not -1 <= correlation.rho <= 1:
            error("correlation", "Correlation coefficient must be between -1 and 1")
    for case in project.analysis_cases:
        if case.requirement_id not in requirements:
            error("missing_requirement", f"Analysis case '{case.name}' refers to missing requirement", case.id)
        if (not isinstance(case.methods, list) or not case.methods or
                any(method not in {"worst_case", "rss", "monte_carlo"} for method in case.methods) or
                not isinstance(case.samples, int) or isinstance(case.samples, bool) or case.samples < 1 or
                not isinstance(case.seed, int) or isinstance(case.seed, bool) or case.seed < 0 or
                not _finite(case.sigma_level) or case.sigma_level <= 0):
            error("analysis_settings", f"Analysis case '{case.name}' has invalid settings", case.id)
    return diagnostics


def compile_project(project: Project) -> CompiledModel:
    diagnostics = validate_project(project)
    if any(d.severity == "error" for d in diagnostics):
        raise ModelError(diagnostics)
    definitions = {d.id: d for d in project.definitions}
    instances = {i.id: i for i in project.instances}
    names: list[str] = []
    for instance in project.instances:
        for face in definitions[instance.definition_id].faces:
            names.append(f"local:{instance.id}:{face.id}")
    names.extend(f"translation:{i.id}" for i in project.instances)
    sources = {s.id: s for s in project.sources}
    source_dimensions: dict[str, list[str]] = {s.id: [] for s in project.sources}
    for dimension in project.dimensions:
        if dimension.kind == "driving":
            source_id = dimension.source_id or f"dim:{dimension.id}"
            if source_id not in sources:
                sources[source_id] = VariationSource(source_id, dimension.name,
                    dimension.tolerance.lower, dimension.tolerance.upper, "normal")
                source_dimensions[source_id] = []
            source_dimensions[source_id].append(dimension.id)
    names.extend(f"source:{s}" for s in sources)
    indices = {name: i for i, name in enumerate(names)}
    n = len(names)
    eq: list[np.ndarray] = []
    eq_rhs: list[float] = []
    ub: list[np.ndarray] = []
    ub_rhs: list[float] = []
    graph = nx.Graph()
    for instance in project.instances:
        definition = definitions[instance.definition_id]
        for face in definition.faces:
            graph.add_node((instance.id, face.id))
        if definition.faces:
            first_face = definition.faces[0]
            row = np.zeros(n)
            row[indices[f"local:{instance.id}:{first_face.id}"]] = 1
            eq.append(row)
            eq_rhs.append(first_face.local_x)
    def local(ref: FaceRef) -> np.ndarray:
        row = np.zeros(n)
        row[indices[f"local:{ref.instance_id}:{ref.face_id}"]] = 1
        return row
    def global_face(ref: FaceRef) -> np.ndarray:
        row = local(ref)
        row[indices[f"translation:{ref.instance_id}"]] = 1
        return row
    for dimension in project.dimensions:
        if dimension.kind not in {"driving", "basic"}:
            continue
        # Driving dimensions carry manufacturing variation. Basic dimensions
        # fix a theoretically exact nominal relation without adding a source.
        # Within one part this is local geometry; between parts it controls
        # global separation as an assembly datum.
        if dimension.first.instance_id == dimension.second.instance_id:
            row = local(dimension.second) - local(dimension.first)
            graph.add_edge((dimension.first.instance_id, dimension.first.face_id),
                           (dimension.second.instance_id, dimension.second.face_id),
                           dimension_id=dimension.id)
        else:
            row = global_face(dimension.second) - global_face(dimension.first)
        if dimension.kind == "driving":
            row[indices[f"source:{dimension.source_id or f'dim:{dimension.id}'}"]] -= dimension.coefficient
        eq.append(row)
        eq_rhs.append(dimension.nominal)
    for instance in project.instances:
        definition = definitions[instance.definition_id]
        if definition.faces:
            anchor = (instance.id, definition.faces[0].id)
            missing = [f.id for f in definition.faces if not nx.has_path(graph, anchor, (instance.id, f.id))]
            if missing:
                diagnostics.append(Diagnostic("underconstrained_geometry",
                    f"Instance '{instance.name}' has faces without a driving dimension path",
                    [instance.id, *missing], "warning"))
    for constraint in project.constraints:
        if constraint.kind == "floating":
            continue
        if constraint.kind in {"fixed_position", "bounded_translation"}:
            row = np.zeros(n)
            row[indices[f"translation:{constraint.instance_id}"]] = 1
        elif constraint.kind == "fixed_face":
            row = global_face(constraint.first)
        else:
            row = global_face(constraint.second) - global_face(constraint.first)
        if constraint.kind in {"fixed_position", "fixed_face", "coincident", "fixed_offset"}:
            eq.append(row)
            eq_rhs.append(0.0 if constraint.kind == "coincident" else constraint.value)
        elif constraint.kind == "unilateral_contact":
            ub.append(-row)
            ub_rhs.append(0.0)
        else:
            ub.extend((-row, row))
            ub_rhs.extend((-constraint.lower, constraint.upper))
    for joint in project.joints:
        row = global_face(joint.second) - global_face(joint.first)
        ub.extend((-row, row))
        ub_rhs.extend((-joint.lower, joint.upper))
    for contact in project.contacts:
        row = global_face(contact.second) - global_face(contact.first)
        ub.append(-row)
        ub_rhs.append(0.0)
    bounds: list[tuple[float | None, float | None]] = [(None, None)] * (n - len(sources))
    bounds.extend((source.lower, source.upper) for source in sources.values())
    ae = np.array(eq, dtype=float).reshape(-1, n)
    be = np.array(eq_rhs, dtype=float)
    au = np.array(ub, dtype=float).reshape(-1, n)
    bu = np.array(ub_rhs, dtype=float)
    if n:
        feasibility = linprog(np.zeros(n), A_ub=au if len(au) else None,
                              b_ub=bu if len(bu) else None, A_eq=ae if len(ae) else None,
                              b_eq=be if len(be) else None, bounds=bounds, method="highs")
        if feasibility.status == 2:
            diagnostics.append(Diagnostic("infeasible_model",
                "Driving dimensions and assembly constraints have no feasible solution",
                [d.id for d in project.dimensions if d.kind == "driving"] +
                [c.id for c in project.constraints]))
        elif feasibility.status not in {0, 3}:
            diagnostics.append(Diagnostic("solver_failure", feasibility.message))
    if any(d.severity == "error" for d in diagnostics):
        raise ModelError(diagnostics)
    # Equality rank counts nominal linear freedom before inequalities. Sources
    # are free manufacturing parameters, so remove their count from this DOF.
    rank = int(np.linalg.matrix_rank(ae, tol=1e-9)) if len(ae) else 0
    dof = max(0, n - rank - len(sources))
    geometry_dof = sum(1 for d in diagnostics if d.code == "underconstrained_geometry")
    assembly_dof = max(0, dof - geometry_dof)
    if len(ae) and sources:
        source_columns = [indices[f"source:{sid}"] for sid in sources]
        local_columns = [i for i in range(n) if i not in set(source_columns)]
        az = ae[:, local_columns]
        residual = np.eye(len(ae)) - az @ np.linalg.pinv(az)
        variable_columns = [j for j, source in enumerate(sources.values())
                            if source.upper - source.lower > 1e-10]
        if variable_columns and np.linalg.norm(residual @ ae[:, source_columns][:, variable_columns]) > 1e-8:
            diagnostics.append(Diagnostic("dependent_sources",
                "A closed driving loop couples manufacturing sources; individual chain coefficients may not be unique",
                [d.id for d in project.dimensions if d.kind == "driving"], "warning"))
    for array in (ae, be, au, bu):
        array.setflags(write=False)
    return CompiledModel(project, tuple(names), indices, tuple(sources), sources,
        {k: tuple(v) for k, v in source_dimensions.items()}, ae, be, au, bu,
        tuple(bounds), tuple(diagnostics), graph, geometry_dof, assembly_dof)
