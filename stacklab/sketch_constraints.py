"""Small 2D sketch constraint solver, independent of the axial stack solver."""

from __future__ import annotations

from math import acos, degrees, hypot

import numpy as np
from scipy.optimize import least_squares

from .domain import PartDefinition, Project, SketchConstraint, SketchDimension


def solve_project_sketches(project: Project) -> None:
    """Keep driving sketch dimensions and geometric relations satisfied.

    Bound vertex X coordinates belong to the axial model and are constants here.
    Other coordinates move as little as possible while satisfying relations.
    """
    for definition in project.definitions:
        constraints = [item for item in project.sketch_constraints if item.definition_id == definition.id]
        dimensions = [item for item in project.sketch_dimensions
                      if item.definition_id == definition.id and item.driving and
                      item.kind != "circle_diameter"]
        if constraints or dimensions:
            solve_definition_sketch(definition, constraints, dimensions)


def solve_definition_sketch(definition: PartDefinition, constraints: list[SketchConstraint],
                            dimensions: list[SketchDimension] | None = None) -> None:
    dimensions = dimensions or []
    outlines = {outline.id: outline for outline in definition.outlines}
    faces = {face.id: face for face in definition.faces}
    fixed: dict[tuple[str, int], tuple[float, float]] = {}
    for constraint in constraints:
        if constraint.kind != "fixed":
            continue
        key = (constraint.first_outline_id, constraint.first_index)
        location = (constraint.x, constraint.y)
        if key in fixed and (abs(fixed[key][0] - location[0]) > 1e-8 or
                             abs(fixed[key][1] - location[1]) > 1e-8):
            raise ValueError(f"Sketch '{definition.name}' has conflicting fixed points")
        fixed[key] = location
    for (outline_id, index), (x, y) in fixed.items():
        outlines[outline_id].vertices[index].x = x
        outlines[outline_id].vertices[index].y = y
    initial: list[float] = []
    variables: dict[tuple[str, int, str], int] = {}
    for outline in definition.outlines:
        for index, vertex in enumerate(outline.vertices):
            location = fixed.get((outline.id, index))
            if location is not None and vertex.face_id is not None and abs(
                faces[vertex.face_id].local_x - location[0]) > 1e-8:
                raise ValueError(f"Sketch '{definition.name}' has a fixed point conflicting with an axial feature")
            if vertex.face_id is None and location is None:
                variables[(outline.id, index, "x")] = len(initial)
                initial.append(vertex.x)
            if location is None:
                variables[(outline.id, index, "y")] = len(initial)
                initial.append(vertex.y)
    seed = np.asarray(initial, dtype=float)

    def point(values: np.ndarray, outline_id: str, index: int) -> tuple[float, float]:
        vertex = outlines[outline_id].vertices[index]
        if (outline_id, index) in fixed:
            return fixed[(outline_id, index)]
        x_index = variables.get((outline_id, index, "x"))
        x = float(values[x_index]) if x_index is not None else faces[vertex.face_id].local_x
        return x, float(values[variables[(outline_id, index, "y")]])

    def segment(values: np.ndarray, outline_id: str, index: int):
        outline = outlines[outline_id]
        return point(values, outline_id, index), point(values, outline_id,
                                                        (index + 1) % len(outline.vertices))

    def geometric_residuals(values: np.ndarray) -> list[float]:
        residuals: list[float] = []
        for constraint in constraints:
            kind = constraint.kind
            if kind in {"fixed", "coincident"}:
                a = point(values, constraint.first_outline_id, constraint.first_index)
                b = ((constraint.x, constraint.y) if kind == "fixed" else
                     point(values, constraint.second_outline_id, constraint.second_index))
                residuals.extend((a[0] - b[0], a[1] - b[1]))
                continue
            a, b = segment(values, constraint.first_outline_id, constraint.first_index)
            ux, uy = b[0] - a[0], b[1] - a[1]
            if kind == "horizontal":
                residuals.append(uy)
            elif kind == "vertical":
                residuals.append(ux)
            else:
                c, d = segment(values, constraint.second_outline_id, constraint.second_index)
                vx, vy = d[0] - c[0], d[1] - c[1]
                length = max(1e-8, hypot(ux, uy) * hypot(vx, vy))
                size = max(1, (hypot(ux, uy) + hypot(vx, vy)) / 2)
                value = ux * vy - uy * vx if kind == "parallel" else ux * vx + uy * vy
                residuals.append(value / length * size)
        for dimension in dimensions:
            kind = dimension.kind
            a, b = segment(values, dimension.first_outline_id, dimension.first_index) if kind != "point_distance" else (
                point(values, dimension.first_outline_id, dimension.first_index),
                point(values, dimension.second_outline_id, dimension.second_index))
            if kind == "line_length" or kind == "point_distance":
                measured = hypot(b[0] - a[0], b[1] - a[1])
            elif kind == "angle_between_lines":
                c, d = segment(values, dimension.second_outline_id, dimension.second_index)
                ux, uy = b[0] - a[0], b[1] - a[1]
                vx, vy = d[0] - c[0], d[1] - c[1]
                length = max(1e-8, hypot(ux, uy) * hypot(vx, vy))
                measured = degrees(acos(max(-1.0, min(1.0, (ux * vx + uy * vy) / length))))
            else:
                c, d = segment(values, dimension.second_outline_id, dimension.second_index)
                ux, uy = b[0] - a[0], b[1] - a[1]
                vx, vy = d[0] - c[0], d[1] - c[1]
                length = max(hypot(ux, uy), 1e-8)
                other_length = max(hypot(vx, vy), 1e-8)
                measured = abs(ux * (c[1] - a[1]) - uy * (c[0] - a[0])) / length
                # A line-to-line spacing remains meaningful only while parallel.
                residuals.append((ux * vy - uy * vx) / (length * other_length) *
                                 max(1, (length + other_length) / 2))
            residuals.append(measured - dimension.nominal)
        return residuals

    if not initial:
        if any(abs(value) > 1e-5 for value in geometric_residuals(seed)):
            raise ValueError(f"Sketch '{definition.name}' has conflicting fixed geometry")
        return
    current = geometric_residuals(seed)
    if not current or max(abs(value) for value in current) < 1e-6:
        return

    def objective(values: np.ndarray) -> np.ndarray:
        equations = geometric_residuals(values)
        return np.concatenate((np.asarray(equations, dtype=float) * 1000,
                               (values - seed) * 0.01))

    result = least_squares(objective, seed, max_nfev=300,
                           ftol=1e-11, xtol=1e-11, gtol=1e-11)
    remaining = geometric_residuals(result.x)
    if remaining and max(abs(value) for value in remaining) > 1e-4:
        raise ValueError(f"Sketch '{definition.name}' has conflicting geometric constraints or dimensions")
    for (outline_id, index, axis), variable_index in variables.items():
        vertex = outlines[outline_id].vertices[index]
        if axis == "x":
            vertex.x = float(result.x[variable_index])
        else:
            vertex.y = float(result.x[variable_index])
