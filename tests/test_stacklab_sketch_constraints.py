"""Geometric sketch relations remain persistent and reject contradictions."""

import pytest

from stacklab.domain import (Outline, PartDefinition, PartInstance, Project,
                             SketchConstraint, SketchVertex)
from stacklab.persistence import load_project_bundle, save_project
from stacklab.services import ProjectService


def lines_project():
    return Project("geometry", "Geometry", definitions=[PartDefinition("def", "Part", [], [
        Outline("one", "First", [SketchVertex(0, 0), SketchVertex(10, 2)], False),
        Outline("two", "Second", [SketchVertex(5, 0), SketchVertex(7, 10)], False),
    ])], instances=[PartInstance("inst", "Part", "def")])


def constraint(identifier, kind, first_outline, first_index=0, second_outline=None,
               second_index=None, x=None, y=None):
    return SketchConstraint(identifier, identifier, "def", kind, first_outline, first_index,
                            second_outline, second_index, x, y)


def test_horizontal_vertical_and_fixed_point_are_solved_and_saved(tmp_path):
    service = ProjectService(lines_project())
    service.execute(lambda p: p.sketch_constraints.extend([
        constraint("anchor", "fixed", "one", x=0, y=0),
        constraint("horizontal", "horizontal", "one"),
        constraint("vertical", "vertical", "two"),
    ]))
    first, second = service.project.definitions[0].outlines
    assert first.vertices[0].x == pytest.approx(0)
    assert first.vertices[0].y == pytest.approx(0)
    assert first.vertices[1].y == pytest.approx(0, abs=1e-6)
    assert second.vertices[0].x == pytest.approx(second.vertices[1].x)
    path = tmp_path / "constrained.stack1d"
    save_project(path, service.project)
    assert len(load_project_bundle(path).project.sketch_constraints) == 3


def test_coincident_parallel_and_perpendicular_relations():
    service = ProjectService(lines_project())
    service.execute(lambda p: p.sketch_constraints.extend([
        constraint("anchor", "fixed", "one", x=0, y=0),
        constraint("touch", "coincident", "one", 1, "two", 0),
        constraint("square", "perpendicular", "one", 0, "two", 0),
    ]))
    first, second = service.project.definitions[0].outlines
    a, b = first.vertices
    c, d = second.vertices
    assert (a.x, a.y) == pytest.approx((0, 0))
    assert (b.x, b.y) == pytest.approx((c.x, c.y))
    assert (b.x - a.x) * (d.x - c.x) + (b.y - a.y) * (d.y - c.y) == pytest.approx(0, abs=1e-4)

    parallel = ProjectService(lines_project())
    parallel.execute(lambda p: p.sketch_constraints.append(
        constraint("parallel", "parallel", "one", 0, "two", 0)))
    first, second = parallel.project.definitions[0].outlines
    a, b = first.vertices
    c, d = second.vertices
    cross = (b.x - a.x) * (d.y - c.y) - (b.y - a.y) * (d.x - c.x)
    assert cross == pytest.approx(0, abs=1e-4)


def test_conflicting_constraints_reject_edit_without_changing_model():
    service = ProjectService(lines_project())
    before = service.project.to_dict()
    with pytest.raises(ValueError, match="conflicting"):
        service.execute(lambda p: p.sketch_constraints.extend([
            constraint("left", "fixed", "one", 0, x=0, y=0),
            constraint("right", "fixed", "one", 1, x=10, y=2),
            constraint("horizontal", "horizontal", "one"),
        ]))
    assert service.project.to_dict() == before
