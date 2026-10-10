"""Independent engineering reference cases for the StackLab numerical core."""
from __future__ import annotations

from math import sqrt

import pytest

from stacklab.compiler import ModelError, compile_project
from stacklab.domain import (
    AssemblyConstraint, AssemblyPolicy, ContactPair, Correlation, Dimension,
    Face, FaceRef, FunctionalRequirement, PartDefinition, PartInstance,
    Project, Tolerance, VariationSource,
)
from stacklab.solvers import analyze, solve_nominal_geometry


def fixed_chain(*, statistical: bool = False) -> Project:
    """H - S - B, with a known 1 mm nominal gap."""
    project = Project("fixed", "Fixed chain")
    project.definitions = [PartDefinition("profile", "Profile", [
        Face("origin", "Origin", 0), Face("housing", "Housing", 100),
        Face("spacer", "Spacer", 60), Face("end", "End", 99),
    ])]
    project.instances = [PartInstance("part", "Profile", "profile")]
    source_ids = ("source_h", "source_s", "source_b") if statistical else (None, None, None)
    project.dimensions = [
        Dimension("h", "Housing length", FaceRef("part", "origin"), FaceRef("part", "housing"),
                  100, Tolerance(-.2, .2), source_id=source_ids[0]),
        Dimension("s", "Spacer length", FaceRef("part", "origin"), FaceRef("part", "spacer"),
                  60, Tolerance(-.1, .1), source_id=source_ids[1]),
        Dimension("b", "Block length", FaceRef("part", "spacer"), FaceRef("part", "end"),
                  39, Tolerance(-.15, .15), source_id=source_ids[2]),
    ]
    if statistical:
        project.sources = [
            VariationSource("source_h", "Housing process", -.2, .2, std=.2/3),
            VariationSource("source_s", "Spacer process", -.1, .1, std=.1/3),
            VariationSource("source_b", "Block process", -.15, .15, std=.15/3),
        ]
    project.constraints = [AssemblyConstraint("ground", "Ground", "fixed_position", instance_id="part")]
    project.requirements = [FunctionalRequirement("gap", "Axial gap", FaceRef("part", "end"),
                                                   FaceRef("part", "housing"), min_value=.5)]
    return project


def floating_block() -> Project:
    project = Project("floating", "Floating block")
    project.definitions = [
        PartDefinition("housing", "Housing", [Face("hleft", "Left", 0), Face("hright", "Right", 100)]),
        PartDefinition("spacer", "Spacer", [Face("sleft", "Left", 0), Face("sright", "Right", 25)]),
        PartDefinition("block", "Block", [Face("bleft", "Left", 30), Face("bright", "Right", 70)]),
    ]
    project.instances = [PartInstance("h", "Housing", "housing"),
                         PartInstance("s", "Spacer", "spacer"),
                         PartInstance("b", "Block", "block")]
    project.dimensions = [
        Dimension("H", "Housing length", FaceRef("h", "hleft"), FaceRef("h", "hright"),
                  100, Tolerance(-.2, .2)),
        Dimension("S", "Spacer length", FaceRef("s", "sleft"), FaceRef("s", "sright"),
                  25, Tolerance(-.1, .1)),
        Dimension("L", "Block length", FaceRef("b", "bleft"), FaceRef("b", "bright"),
                  40, Tolerance(-.15, .15)),
    ]
    project.constraints = [
        AssemblyConstraint("ground", "Ground housing", "fixed_position", instance_id="h"),
        AssemblyConstraint("mount", "Mount spacer", "coincident", FaceRef("h", "hleft"), FaceRef("s", "sleft")),
    ]
    project.contacts = [
        ContactPair("left", FaceRef("s", "sright"), FaceRef("b", "bleft")),
        ContactPair("right", FaceRef("b", "bright"), FaceRef("h", "hright")),
    ]
    project.policies = [
        AssemblyPolicy("free", "Free envelope", "free"),
        AssemblyPolicy("left_policy", "Seated left", "left_contact", ["left"]),
        AssemblyPolicy("right_policy", "Seated right", "right_contact", ["right"]),
        AssemblyPolicy("centered", "Centered", "centered", ["left", "right"]),
    ]
    project.requirements = [FunctionalRequirement("gap", "Right gap", FaceRef("b", "bright"),
                                                   FaceRef("h", "hright"), policy_id="free")]
    return project


def test_fixed_chain_asymmetric_and_source_signs() -> None:
    project = fixed_chain()
    project.dimensions[1].tolerance = Tolerance(-.03, .12)
    result = analyze(project, "gap", methods=("worst_case",))
    assert result.chain.floating is False
    coefficients = {term.source_id: term.coefficient for term in result.chain.terms}
    assert coefficients == pytest.approx({"dim:h": 1, "dim:s": -1, "dim:b": -1})
    assert result.worst_case.nominal == pytest.approx(1)
    assert result.worst_case.minimum == pytest.approx(1 - .2 - .12 - .15)
    assert result.worst_case.maximum == pytest.approx(1 + .2 + .03 + .15)
    assert result.worst_case.meets_limits is True
    assert solve_nominal_geometry(project)[("part", "end")] == pytest.approx(99)


def test_floating_block_envelope_and_contact_policies() -> None:
    project = floating_block()
    free = analyze(project, "gap", methods=("worst_case",))
    assert free.chain.floating
    assert free.worst_case.nominal is None
    assert free.worst_case.minimum == pytest.approx(0)
    assert free.worst_case.maximum == pytest.approx(35.45)
    project.requirements[0].policy_id = "left_policy"
    left = analyze(project, "gap", methods=("worst_case",))
    assert left.chain.floating is False
    assert left.worst_case.nominal == pytest.approx(35)
    assert left.worst_case.minimum == pytest.approx(34.55)
    assert left.worst_case.maximum == pytest.approx(35.45)
    assert "left" in left.worst_case.minimum_state.active_contacts
    project.requirements[0].policy_id = "right_policy"
    right = analyze(project, "gap", methods=("worst_case",))
    assert right.worst_case.minimum == pytest.approx(0)
    assert right.worst_case.maximum == pytest.approx(0)


def test_inconsistent_loop_and_underconstraint_diagnostics() -> None:
    project = fixed_chain()
    project.dimensions.append(Dimension("loop", "Conflicting loop", FaceRef("part", "origin"),
                                        FaceRef("part", "end"), 120))
    with pytest.raises(ModelError) as error:
        compile_project(project)
    assert any(d.code == "infeasible_model" for d in error.value.diagnostics)
    project = fixed_chain()
    project.dimensions.pop()
    model = compile_project(project)
    assert any(d.code == "underconstrained_geometry" for d in model.diagnostics)


def test_rss_independent_and_correlated_variance() -> None:
    project = fixed_chain(statistical=True)
    result = analyze(project, "gap", methods=("rss",))
    expected_variance = (.2/3)**2 + (.1/3)**2 + (.15/3)**2
    assert result.rss.mean == pytest.approx(1)
    assert result.rss.variance == pytest.approx(expected_variance)
    assert result.rss.std == pytest.approx(sqrt(expected_variance))
    project.correlations = [Correlation("source_h", "source_s", .5)]
    correlated = analyze(project, "gap", methods=("rss",))
    assert correlated.rss.variance == pytest.approx(expected_variance - 2*.5*(.2/3)*(.1/3))
    assert sum(correlated.rss.variance_contributions.values()) == pytest.approx(correlated.rss.variance)


def test_rss_rejects_independent_sources_in_closed_driving_loop() -> None:
    project = fixed_chain(statistical=True)
    project.dimensions.append(Dimension("loop", "Closed loop", FaceRef("part", "origin"),
                                        FaceRef("part", "end"), 99))
    result = analyze(project, "gap", methods=("worst_case", "rss"))
    assert result.worst_case is not None
    assert result.rss is None
    assert any(d.code == "unsupported_analysis" and "double-count" in d.message
               for d in result.diagnostics)


def test_monte_carlo_seed_and_statistics() -> None:
    project = fixed_chain(statistical=True)
    first = analyze(project, "gap", methods=("monte_carlo",), samples=10000, seed=72).monte_carlo
    second = analyze(project, "gap", methods=("monte_carlo",), samples=10000, seed=72).monte_carlo
    assert first == second
    assert first.valid_samples == 10000
    assert first.infeasible_samples == 0
    assert first.mean == pytest.approx(1, abs=.005)
    expected_std = sqrt((.2/3)**2 + (.1/3)**2 + (.15/3)**2)
    assert first.std == pytest.approx(expected_std, abs=.005)


def test_project_serialization_roundtrip() -> None:
    project = floating_block()
    project.dimensions[0].display_style = "limits"
    restored = Project.from_dict(project.to_dict())
    assert restored.to_dict() == project.to_dict()
    result = analyze(restored, "gap", methods=("worst_case",))
    assert result.worst_case.maximum == pytest.approx(35.45)


def test_empty_project_and_arbitrary_fixed_face() -> None:
    assert solve_nominal_geometry(Project("empty", "New assembly")) == {}
    project = fixed_chain()
    project.constraints = [AssemblyConstraint("fix_face", "Fix end", "fixed_face",
                                              first=FaceRef("part", "end"), value=500)]
    positions = solve_nominal_geometry(project)
    assert positions[("part", "end")] == pytest.approx(500)
    assert positions[("part", "housing")] == pytest.approx(501)
    result = analyze(project, "gap", methods=("worst_case",))
    assert result.worst_case.nominal == pytest.approx(1)


def test_redudant_consistent_loop_and_multiple_requirements() -> None:
    project = fixed_chain()
    for dimension in project.dimensions:
        dimension.tolerance = Tolerance(0, 0)
    project.dimensions.append(Dimension("sum", "Reference loop", FaceRef("part", "origin"),
                                        FaceRef("part", "end"), 99))
    project.requirements.append(FunctionalRequirement("overall", "Overall length",
        FaceRef("part", "origin"), FaceRef("part", "end")))
    compile_project(project)  # consistent redundancy is valid
    first = analyze(project, "gap", methods=("worst_case",))
    second = analyze(project, "overall", methods=("worst_case",))
    assert first.worst_case.minimum == pytest.approx(1)
    assert second.worst_case.minimum == pytest.approx(99)
    assert second.worst_case.maximum == pytest.approx(99)


def test_coupled_floating_blocks_and_contact_states() -> None:
    project = Project("coupled", "Two moving blocks")
    project.definitions = [
        PartDefinition("hd", "Housing", [Face("hl", "Left", 0), Face("hr", "Right", 100)]),
        PartDefinition("ad", "Block A", [Face("al", "Left", 0), Face("ar", "Right", 30)]),
        PartDefinition("bd", "Block B", [Face("bl", "Left", 0), Face("br", "Right", 40)]),
    ]
    project.instances = [PartInstance("h", "Housing", "hd"), PartInstance("a", "A", "ad"),
                         PartInstance("b", "B", "bd")]
    project.dimensions = [
        Dimension("H", "H", FaceRef("h", "hl"), FaceRef("h", "hr"), 100),
        Dimension("A", "A", FaceRef("a", "al"), FaceRef("a", "ar"), 30),
        Dimension("B", "B", FaceRef("b", "bl"), FaceRef("b", "br"), 40),
    ]
    project.constraints = [AssemblyConstraint("ground", "Ground", "fixed_position", instance_id="h")]
    project.contacts = [
        ContactPair("left", FaceRef("h", "hl"), FaceRef("a", "al")),
        ContactPair("middle", FaceRef("a", "ar"), FaceRef("b", "bl")),
        ContactPair("right", FaceRef("b", "br"), FaceRef("h", "hr")),
    ]
    project.requirements = [FunctionalRequirement("gap", "Between blocks", FaceRef("a", "ar"),
                                                   FaceRef("b", "bl"))]
    result = analyze(project, "gap", methods=("worst_case",))
    assert result.worst_case.minimum == pytest.approx(0)
    assert result.worst_case.maximum == pytest.approx(30)
    assert "middle" in result.worst_case.minimum_state.active_contacts
    for state in (result.worst_case.minimum_state, result.worst_case.maximum_state):
        assert state.face_positions["a:ar"] <= state.face_positions["b:bl"] + 1e-8
        assert state.face_positions["b:br"] <= state.face_positions["h:hr"] + 1e-8


def test_infeasible_assemblies_are_counted_in_simulation() -> None:
    project = floating_block()
    project.dimensions[0].nominal = 65
    project.dimensions[0].source_id = "housing_process"
    project.dimensions[0].tolerance = Tolerance(-1, 1)
    project.sources = [VariationSource("housing_process", "Housing process", -1, 1,
                                       distribution="uniform")]
    project.dimensions[1].tolerance = Tolerance(0, 0)
    project.dimensions[2].tolerance = Tolerance(0, 0)
    project.requirements[0].policy_id = "left_policy"
    project.requirements[0].min_value = .25
    result = analyze(project, "gap", methods=("monte_carlo",), samples=800, seed=9)
    mc = result.monte_carlo
    assert 350 < mc.infeasible_samples < 450
    assert mc.valid_samples + mc.infeasible_samples == 800
    assert mc.minimum >= 0
    assert mc.overall_failure_probability > mc.conditional_failure_probability
    rss_result = analyze(project, "gap", methods=("rss",))
    assert rss_result.rss is None
    assert any(d.code == "unsupported_analysis" and "cannot assemble" in d.message
               for d in rss_result.diagnostics)


def test_centered_floating_block_exact_gap() -> None:
    project = floating_block()
    project.requirements[0].policy_id = "centered"
    result = analyze(project, "gap", methods=("worst_case",))
    assert result.chain.floating is False
    assert result.worst_case.nominal == pytest.approx(17.5)
    assert result.worst_case.minimum == pytest.approx(17.275)
    assert result.worst_case.maximum == pytest.approx(17.725)
    for dimension in project.dimensions:
        dimension.tolerance = Tolerance(0, 0)
    simulation = analyze(project, "gap", methods=("monte_carlo",), samples=8, seed=1)
    assert simulation.monte_carlo.valid_samples == 8
    assert simulation.monte_carlo.mean == pytest.approx(17.5)
    project.policies[-1].contact_ids = []
    unsupported = analyze(project, "gap", methods=("worst_case",))
    assert unsupported.worst_case is None
    assert any(d.code == "unsupported_analysis" for d in unsupported.diagnostics)
