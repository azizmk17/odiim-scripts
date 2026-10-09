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
class Dimension:
    id: str
    name: str
    start: str
    end: str
    nominal: float
    lower: float = -0.1
    upper: float = 0.1
    kind: str = "size"  # size: manufacturing; placement: movement; contact: zero separation

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
    schema_version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("This file is not a supported Odiim stackup project (schema 1).")
        try:
            project = cls(
                name=data.get("name", "Untitled stackup"),
                points=[Point(**item) for item in data.get("points", [])],
                bodies=[Body(**item) for item in data.get("bodies", [])],
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
        ids = {item.id for group in (self.points, self.bodies, self.dimensions, self.fits) for item in group}
        number = 1
        while f"{prefix}{number}" in ids:
            number += 1
        return f"{prefix}{number}"

    def point(self, point_id: str) -> Point:
        return next(point for point in self.points if point.id == point_id)

    def remove_point(self, point_id: str) -> None:
        self.points = [p for p in self.points if p.id != point_id]
        self.bodies = [b for b in self.bodies if point_id not in (b.left, b.right)]
        self.dimensions = [d for d in self.dimensions if point_id not in (d.start, d.end)]
        self.fits = [f for f in self.fits if point_id not in (f.slot_left, f.slot_right, f.body_left, f.body_right)]
        if self.gap and point_id in (self.gap.start, self.gap.end):
            self.gap = None
        if self.datum == point_id:
            self.datum = self.points[0].id if self.points else None

    def validate(self) -> None:
        if not isinstance(self.name, str):
            raise ValueError("The project name must be text.")
        if len(self.points) > 300 or len(self.dimensions) + len(self.fits) > 600:
            raise ValueError("This desktop app supports up to 300 points and 600 constraints.")
        objects = [item for group in (self.points, self.bodies, self.dimensions, self.fits) for item in group]
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
        for dimension in self.dimensions:
            endpoints(dimension.start, dimension.end)
            finite(dimension.nominal, dimension.lower, dimension.upper)
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
