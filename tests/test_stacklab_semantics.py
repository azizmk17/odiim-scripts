"""Dimension roles must affect geometry and variation differently."""

import pytest

from stacklab.domain import (
    AssemblyConstraint, Dimension, Face, FaceRef, FunctionalRequirement,
    PartDefinition, PartInstance, Project, Tolerance,
)
from stacklab.solvers import analyze, solve_nominal_geometry
from stacklab.examples import fixed_chain


def test_basic_dimension_fixes_geometry_without_manufacturing_variation():
    first, middle, last = (FaceRef("part", face) for face in ("a", "b", "c"))
    project = Project("roles", "Dimension roles")
    project.definitions = [PartDefinition("profile", "Profile", [
        Face("a", "A", 0), Face("b", "B", 7), Face("c", "C", 12),
    ])]
    project.instances = [PartInstance("part", "Profile", "profile")]
    project.dimensions = [
        Dimension("basic", "Exact nominal datum", first, middle, 10, kind="basic"),
        Dimension("driving", "Manufactured segment", middle, last, 3,
                  Tolerance(-0.1, 0.2)),
        Dimension("reference", "Informational check", first, last, 999,
                  kind="reference"),
    ]
    project.constraints = [AssemblyConstraint("fixed", "Fix A", "fixed_face", first=first)]
    project.requirements = [FunctionalRequirement("gap", "Overall", first, last)]

    positions = solve_nominal_geometry(project)
    assert positions[("part", "b")] == pytest.approx(10)
    assert positions[("part", "c")] == pytest.approx(13)
    result = analyze(project, "gap", methods=("worst_case",))
    assert result.chain.nominal == pytest.approx(13)
    assert {term.source_id for term in result.chain.terms} == {"dim:driving"}
    assert result.worst_case.minimum == pytest.approx(12.9)
    assert result.worst_case.maximum == pytest.approx(13.2)


def test_asymmetric_uniform_rss_mean_matches_sampled_process():
    project = fixed_chain(statistical=True)
    for source in project.sources:
        source.distribution = "uniform"
    project.sources[0].lower = -0.1
    project.sources[0].upper = 0.2
    result = analyze(project, project.requirements[0].id,
                     methods=("rss", "monte_carlo"), samples=30_000, seed=41)
    assert result.rss.mean == pytest.approx(1.05)
    assert result.monte_carlo.mean == pytest.approx(1.05, abs=0.003)
