"""Serializable one-dimensional geometry. Vertical coordinates are layout only."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math


@dataclass
class Point:
    id: str
    name: str
    x: float
    y: float


@dataclass
class Body:
    id: str
    name: str
    left: str
    right: str
    height: float = 6.0
    color: str = "#2563eb"
    hollow: bool = False


@dataclass
class Sketch:
    """A custom outline whose vertices are analytical x-features.

    Columns are explicit shared-x groups established while drawing vertical
    faces. Circle vertices are ordered left, center, right.
    """
    id: str
    name: str
    vertices: list[str]
    closed: bool = True
    color: str = "#2563eb"
    kind: str = "profile"  # profile, line, circle
    columns: list[list[str]] = field(default_factory=list)


@dataclass
class Dimension:
    id: str
    name: str
    start: str
    end: str
    nominal: float
    lower: float = -0.1
    upper: float = 0.1
    kind: str = "size"  # size: manufacturing; placement: movement; contact: zero separation
    annotation_y: float | None = None
    label_offset: float = 0.0

    @property
    def minimum(self) -> float:
        return self.nominal + self.lower

    @property
    def maximum(self) -> float:
        return self.nominal + self.upper


@dataclass
class Fit:
    id: str
    name: str
    slot_left: str
    slot_right: str
    body_left: str
    body_right: str
    mode: str = "free"  # free, left, right, centered


@dataclass
class Gap:
    start: str
    end: str
    name: str = "Functional gap"
    minimum_allowed: float | None = 0.0
    maximum_allowed: float | None = None


@dataclass
class Project:
    name: str = "Untitled stackup"
    points: list[Point] = field(default_factory=list)
    bodies: list[Body] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    fits: list[Fit] = field(default_factory=list)
    datum: str | None = None
    gap: Gap | None = None
    schema_version: int = 2
    sketches: list[Sketch] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        if not isinstance(data, dict) or data.get("schema_version") not in (1, 2):
            raise ValueError("This file is not a supported Odiim stackup project (schema 1 or 2).")
        try:
            project = cls(
                name=data.get("name", "Untitled stackup"),
                points=[Point(**item) for item in data.get("points", [])],
                bodies=[Body(**item) for item in data.get("bodies", [])],
                sketches=[Sketch(**item) for item in data.get("sketches", [])],
                dimensions=[Dimension(**item) for item in data.get("dimensions", [])],
                fits=[Fit(**item) for item in data.get("fits", [])],
                datum=data.get("datum"),
                gap=Gap(**data["gap"]) if data.get("gap") is not None else None,
            )
        except (TypeError, KeyError) as exc:
            raise ValueError("Malformed project data: " + str(exc)) from exc
        project.validate()
        return project

    def copy(self) -> "Project":
        return self.from_dict(self.to_dict())

    def next_id(self, prefix: str) -> str:
        ids = {item.id for group in (self.points, self.bodies, self.sketches, self.dimensions, self.fits) for item in group}
        number = 1
        while f"{prefix}{number}" in ids:
            number += 1
        return f"{prefix}{number}"

    def point(self, point_id: str) -> Point:
        return next(point for point in self.points if point.id == point_id)

    def add_sketch(self, coordinates: list[tuple[float, float]], *, name: str = "Custom part",
                   kind: str = "profile", closed: bool = True, color: str = "#2563eb",
                   reuse: list[str | None] | None = None) -> Sketch:
        """Create independent part features, or deliberately join line endpoints.

        Sketch distances supply a reference pose, never manufacturing bounds.
        Dimensions are added by the designer afterwards.
        """
        if kind not in ("profile", "line", "circle"):
            raise ValueError("Choose a profile, line or circle.")
        if len(coordinates) < (3 if kind == "profile" and closed else 2):
            raise ValueError("A closed profile needs at least three vertices; an open path needs two.")
        if kind in ("line", "circle") and len(coordinates) != 2:
            raise ValueError("Draw a line or circle with two clicks.")
        if any(len(pair) != 2 or any(isinstance(v, bool) or not isinstance(v, (float, int))
                                    or not math.isfinite(v) for v in pair) for pair in coordinates):
            raise ValueError("Sketch coordinates must be finite numbers.")
        if reuse is not None and len(reuse) != len(coordinates):
            raise ValueError("Endpoint references must match the sketch vertices.")
        trial = self.copy()
        refs = reuse or [None] * len(coordinates)
        if kind == "circle":
            (left, _), (right, _) = sorted(coordinates)
            y = sum(pair[1] for pair in coordinates) / 2
            if right - left <= 1e-8:
                raise ValueError("A circle needs a positive horizontal diameter.")
            coordinates = [(left, y), ((left + right) / 2, y), (right, y)]
            refs = [None] * 3
            closed = True
        elif kind == "line":
            closed = False
        vertices = []
        for index, ((x, y), ref) in enumerate(zip(coordinates, refs)):
            if ref is not None:
                if ref not in {p.id for p in trial.points}:
                    raise ValueError("The selected endpoint no longer exists.")
                vertices.append(ref)
            else:
                point_id = trial.next_id("P")
                feature = ["left", "center", "right"][index] if kind == "circle" else f"v{index + 1}"
                trial.points.append(Point(point_id, name + " " + feature, x, y))
                vertices.append(point_id)
        columns: dict[float, list[str]] = {}
        if kind != "circle":
            for point_id, (x, _) in zip(vertices, coordinates):
                columns.setdefault(round(x, 9), []).append(point_id)
        sketch = Sketch(trial.next_id("S"), name, vertices, closed, color, kind,
                        [group for group in columns.values() if len(group) > 1])
        trial.sketches.append(sketch)
        trial.datum = trial.datum or vertices[0]
        trial.validate()
        self.points, self.sketches, self.datum = trial.points, trial.sketches, trial.datum
        return sketch

    def aligned_points(self, point_id: str) -> set[str]:
        """All features belonging to the same explicitly drawn x-column."""
        linked = {point_id}
        while True:
            previous = len(linked)
            for sketch in self.sketches:
                for group in sketch.columns:
                    if linked.intersection(group):
                        linked.update(group)
            if len(linked) == previous:
                return linked

    def remove_point(self, point_id: str) -> None:
        self.points = [p for p in self.points if p.id != point_id]
        self.bodies = [b for b in self.bodies if point_id not in (b.left, b.right)]
        self.sketches = [s for s in self.sketches if point_id not in s.vertices]
        self.dimensions = [d for d in self.dimensions if point_id not in (d.start, d.end)]
        self.fits = [f for f in self.fits if point_id not in (f.slot_left, f.slot_right, f.body_left, f.body_right)]
        if self.gap and point_id in (self.gap.start, self.gap.end):
            self.gap = None
        if self.datum == point_id:
            self.datum = self.points[0].id if self.points else None

    def remove_sketch(self, sketch_id: str) -> None:
        sketch = next(s for s in self.sketches if s.id == sketch_id)
        self.sketches = [s for s in self.sketches if s.id != sketch_id]
        shared = {p for s in self.sketches for p in s.vertices}
        shared.update(p for b in self.bodies for p in (b.left, b.right))
        for point_id in sketch.vertices:
            if point_id not in shared:
                self.remove_point(point_id)

    def validate(self) -> None:
        if not isinstance(self.name, str):
            raise ValueError("The project name must be text.")
        if len(self.points) > 300 or len(self.dimensions) + len(self.fits) > 600:
            raise ValueError("This desktop app supports up to 300 points and 600 constraints.")
        objects = [item for group in (self.points, self.bodies, self.sketches, self.dimensions, self.fits) for item in group]
        ids = [item.id for item in objects]
        if any(not isinstance(item_id, str) or not item_id for item_id in ids) or len(set(ids)) != len(ids):
            raise ValueError("Every object must have a unique, non-empty ID.")
        if any(not isinstance(item.name, str) for item in objects):
            raise ValueError("Object names must be text.")
        point_ids = {point.id for point in self.points}

        def endpoints(*refs: str) -> None:
            if any(ref not in point_ids for ref in refs):
                raise ValueError("A constraint references a point that does not exist.")

        def finite(*values: float) -> None:
            if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
                raise ValueError("Coordinates and dimensions must be finite numbers.")

        if self.points and self.datum not in point_ids:
            raise ValueError("Select a valid datum point.")
        if not self.points and self.datum is not None:
            raise ValueError("An empty project cannot have a datum.")
        for point in self.points:
            finite(point.x, point.y)
        for body in self.bodies:
            endpoints(body.left, body.right)
            finite(body.height)
            if body.left == body.right or body.height <= 0:
                raise ValueError("A body needs two different faces and a positive drawing height.")
            if not isinstance(body.hollow, bool):
                raise ValueError("The body sketch style must be a boolean.")
            if not isinstance(body.color, str) or len(body.color) != 7 or not body.color.startswith("#"):
                raise ValueError("Body colors must use #RRGGBB notation.")
            try:
                int(body.color[1:], 16)
            except ValueError as exc:
                raise ValueError("Invalid body color.") from exc
        for sketch in self.sketches:
            if not isinstance(sketch.vertices, list) or not isinstance(sketch.columns, list):
                raise ValueError("Sketch vertices and shared-x groups must be lists.")
            endpoints(*sketch.vertices)
            if sketch.kind not in ("profile", "line", "circle") or not isinstance(sketch.closed, bool):
                raise ValueError("Unknown sketch type or closure flag.")
            required = 3 if sketch.closed or sketch.kind == "circle" else 2
            if len(sketch.vertices) < required or len(set(sketch.vertices)) != len(sketch.vertices):
                raise ValueError("A sketch needs distinct vertices and enough points for its outline.")
            if sketch.kind == "circle" and (len(sketch.vertices) != 3 or sketch.columns):
                raise ValueError("A circle needs its left, center and right features.")
            if sketch.kind == "line" and (len(sketch.vertices) != 2 or sketch.closed):
                raise ValueError("A line needs exactly two endpoints and must be open.")
            grouped: set[str] = set()
            for group in sketch.columns:
                if not isinstance(group, list) or len(group) < 2 or len(set(group)) != len(group):
                    raise ValueError("A shared-x group needs distinct features.")
                if not set(group) <= set(sketch.vertices) or grouped.intersection(group):
                    raise ValueError("Each sketch feature may belong to only one shared-x group.")
                grouped.update(group)
            if not isinstance(sketch.color, str) or len(sketch.color) != 7 or not sketch.color.startswith("#"):
                raise ValueError("Sketch colors must use #RRGGBB notation.")
            try:
                int(sketch.color[1:], 16)
            except ValueError as exc:
                raise ValueError("Invalid sketch color.") from exc
        for dimension in self.dimensions:
            endpoints(dimension.start, dimension.end)
            finite(dimension.nominal, dimension.lower, dimension.upper)
            finite(dimension.label_offset)
            if dimension.annotation_y is not None:
                finite(dimension.annotation_y)
            if dimension.start == dimension.end or dimension.lower > dimension.upper:
                raise ValueError("A dimension needs different points and an ordered tolerance interval.")
            if dimension.kind not in ("size", "placement", "contact"):
                raise ValueError("Unknown dimension kind.")
            if dimension.kind == "contact" and any(v != 0 for v in (dimension.nominal, dimension.lower, dimension.upper)):
                raise ValueError("A contact must have exactly zero separation.")
        for fit in self.fits:
            endpoints(fit.slot_left, fit.slot_right, fit.body_left, fit.body_right)
            if len({fit.slot_left, fit.slot_right, fit.body_left, fit.body_right}) != 4:
                raise ValueError("A fit needs four distinct face points. Use a contact to join shared faces.")
            if fit.mode not in ("free", "left", "right", "centered"):
                raise ValueError("Unknown fit mode.")
        if self.gap:
            if not isinstance(self.gap.name, str):
                raise ValueError("The gap name must be text.")
            endpoints(self.gap.start, self.gap.end)
            if self.gap.start == self.gap.end:
                raise ValueError("The gap needs two different points.")
            limits = [v for v in (self.gap.minimum_allowed, self.gap.maximum_allowed) if v is not None]
            finite(*limits)
            if len(limits) == 2 and limits[0] > limits[1]:
                raise ValueError("The minimum gap requirement cannot exceed the maximum.")
