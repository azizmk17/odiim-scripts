"""Geometry selection and measurements for the 2D presentation sketch.

These distances do not enter the axial tolerance solver. Horizontal selections
are converted to engineering ``Dimension`` entities by the UI instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot, sqrt

from .domain import PartDefinition, SketchDimension


@dataclass(frozen=True)
class GeometryPick:
    kind: str  # vertex or segment
    instance_id: str
    definition_id: str
    outline_id: str
    index: int


def outline_vertices(definition: PartDefinition, outline_id: str) -> list[tuple[float, float]]:
    outline = next(item for item in definition.outlines if item.id == outline_id)
    faces = {face.id: face for face in definition.faces}
    return [(faces[vertex.face_id].local_x if vertex.face_id in faces else vertex.x, vertex.y)
            for vertex in outline.vertices]


def vertex_point(definition: PartDefinition, pick: GeometryPick) -> tuple[float, float]:
    return outline_vertices(definition, pick.outline_id)[pick.index]


def segment_points(definition: PartDefinition, pick: GeometryPick) -> tuple[tuple[float, float], tuple[float, float]]:
    outline = next(item for item in definition.outlines if item.id == pick.outline_id)
    points = outline_vertices(definition, pick.outline_id)
    if pick.index < 0 or pick.index >= len(points) - (0 if outline.closed else 1):
        raise ValueError("Selected line is no longer in the outline")
    return points[pick.index], points[(pick.index + 1) % len(points)]


def parallel_line_spacing(first, second) -> float:
    (ax, ay), (bx, by) = first
    (cx, cy), (dx, dy) = second
    ux, uy = bx - ax, by - ay
    vx, vy = dx - cx, dy - cy
    first_length, second_length = hypot(ux, uy), hypot(vx, vy)
    if first_length < 1e-9 or second_length < 1e-9:
        raise ValueError("Select nonzero-length lines")
    if abs(ux * vy - uy * vx) > first_length * second_length * 1e-5:
        raise ValueError("Select two parallel lines to dimension their spacing")
    return abs(ux * (cy - ay) - uy * (cx - ax)) / first_length


def measure_sketch_dimension(definition: PartDefinition, dimension: SketchDimension) -> float:
    first = GeometryPick("segment" if dimension.kind != "point_distance" else "vertex",
                         "", definition.id, dimension.first_outline_id, dimension.first_index)
    if dimension.kind == "line_length":
        a, b = segment_points(definition, first)
        return hypot(b[0] - a[0], b[1] - a[1])
    if dimension.second_outline_id is None or dimension.second_index is None:
        raise ValueError("Sketch dimension needs a second selection")
    second = GeometryPick(first.kind, "", definition.id,
                          dimension.second_outline_id, dimension.second_index)
    if dimension.kind == "point_distance":
        a, b = vertex_point(definition, first), vertex_point(definition, second)
        return hypot(b[0] - a[0], b[1] - a[1])
    if dimension.kind == "line_spacing":
        return parallel_line_spacing(segment_points(definition, first), segment_points(definition, second))
    raise ValueError(f"Unknown sketch dimension kind: {dimension.kind}")


def set_sketch_dimension_value(definition: PartDefinition, dimension: SketchDimension,
                               target: float) -> None:
    """Move the selected sketch vertices to set one local geometric distance.

    An X-bound vertex is owned by the axial model. Its X cannot be changed by a
    sketch-only dimension; the user must edit the corresponding axial dimension.
    """
    if target < 0:
        raise ValueError("A sketch distance cannot be negative")
    outlines = {outline.id: outline for outline in definition.outlines}

    def move(outline_id: str, index: int, dx: float, dy: float) -> None:
        vertex = outlines[outline_id].vertices[index]
        if vertex.face_id is not None and abs(dx) > 1e-8:
            raise ValueError("This vertex is tied to an axial feature. Edit its axial dimension instead.")
        vertex.x += dx
        vertex.y += dy

    if dimension.kind in {"line_length", "point_distance"}:
        first_pick = GeometryPick("vertex", "", definition.id,
                                  dimension.first_outline_id, dimension.first_index)
        if dimension.kind == "line_length":
            outline = outlines[dimension.first_outline_id]
            second_outline_id = dimension.first_outline_id
            second_index = (dimension.first_index + 1) % len(outline.vertices)
        else:
            second_outline_id, second_index = dimension.second_outline_id, dimension.second_index
        second_pick = GeometryPick("vertex", "", definition.id, second_outline_id, second_index)
        a, b = vertex_point(definition, first_pick), vertex_point(definition, second_pick)
        length = hypot(b[0] - a[0], b[1] - a[1])
        if length < 1e-9:
            raise ValueError("Cannot resize a zero-length selection")
        if target < 1e-9 and dimension.kind == "line_length":
            raise ValueError("A line length must be greater than zero")
        endpoint = outlines[second_outline_id].vertices[second_index]
        if endpoint.face_id is not None:
            fixed_dx = b[0] - a[0]
            if target + 1e-9 < abs(fixed_dx):
                raise ValueError("The requested length is shorter than its fixed axial projection")
            dy = sqrt(max(0, target * target - fixed_dx * fixed_dx))
            dy *= 1 if b[1] >= a[1] else -1
            move(second_outline_id, second_index, 0, dy - (b[1] - a[1]))
        else:
            scale = target / length - 1
            move(second_outline_id, second_index, (b[0] - a[0]) * scale, (b[1] - a[1]) * scale)
    elif dimension.kind == "line_spacing":
        first = GeometryPick("segment", "", definition.id,
                             dimension.first_outline_id, dimension.first_index)
        second = GeometryPick("segment", "", definition.id,
                              dimension.second_outline_id, dimension.second_index)
        if first.outline_id == second.outline_id:
            outline = outlines[first.outline_id]
            n = len(outline.vertices)
            first_indices = {first.index, (first.index + 1) % n}
            second_indices = {second.index, (second.index + 1) % n}
            if first_indices & second_indices:
                raise ValueError("Choose non-adjacent lines for a driving spacing dimension")
        (ax, ay), (bx, by) = segment_points(definition, first)
        (cx, cy), _ = segment_points(definition, second)
        parallel_line_spacing(segment_points(definition, first), segment_points(definition, second))
        ux, uy = bx - ax, by - ay
        length = hypot(ux, uy)
        nx, ny = -uy / length, ux / length
        signed = (cx - ax) * nx + (cy - ay) * ny
        direction = 1 if signed >= 0 else -1
        delta = direction * target - signed
        outline = outlines[second.outline_id]
        move(second.outline_id, second.index, nx * delta, ny * delta)
        move(second.outline_id, (second.index + 1) % len(outline.vertices), nx * delta, ny * delta)
    else:
        raise ValueError(f"Unknown sketch dimension kind: {dimension.kind}")
    dimension.nominal = target
    measured = measure_sketch_dimension(definition, dimension)
    if abs(measured - target) > 1e-5 * max(1, target):
        raise ValueError("This geometry cannot satisfy the requested dimension")
