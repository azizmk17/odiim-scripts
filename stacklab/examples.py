"""Reproducible engineering reference assemblies for StackLab 1D."""

from __future__ import annotations

from pathlib import Path

from .domain import (
    AnalysisCase, AssemblyConstraint, AssemblyPolicy, ContactPair, Dimension,
    Face, FaceRef, FunctionalRequirement, PartDefinition, PartInstance, Project,
    Tolerance, VariationSource,
)
from .persistence import save_project


def _dimension(identifier: str, name: str, first: FaceRef, second: FaceRef,
               nominal: float, tolerance: float, *, std: float | None = None) -> tuple[Dimension, VariationSource]:
    source_id = f"source-{identifier}"
    return (
        Dimension(identifier, name, first, second, nominal,
                  Tolerance(-tolerance, tolerance), source_id=source_id),
        VariationSource(source_id, name, -tolerance, tolerance,
                        distribution="normal", std=std),
    )


def fixed_chain(*, statistical: bool = False) -> Project:
    """Example A/E: G = 100 - 60 - 39 = 1; worst case [0.55, 1.45]."""
    prefix = "e" if statistical else "a"
    part_id, instance_id = f"{prefix}-part", f"{prefix}-instance"
    f0, f100, f60, f99 = (f"{prefix}-origin", f"{prefix}-overall",
                           f"{prefix}-branch", f"{prefix}-end")
    origin, overall, branch, end = (FaceRef(instance_id, item)
                                    for item in (f0, f100, f60, f99))
    dimensions_and_sources = [
        _dimension(f"{prefix}-d1", "D1 overall", origin, overall, 100.0, 0.20,
                   std=0.20 / 3 if statistical else None),
        _dimension(f"{prefix}-d2", "D2 branch", origin, branch, 60.0, 0.10,
                   std=0.10 / 3 if statistical else None),
        _dimension(f"{prefix}-d3", "D3 return", branch, end, 39.0, 0.15,
                   std=0.15 / 3 if statistical else None),
    ]
    requirement_id = f"{prefix}-gap"
    project = Project(
        id=f"example-{prefix}",
        name="Statistical affine chain" if statistical else "Fixed dimensional chain",
        definitions=[PartDefinition(part_id, "Dimensioned profile", [
            Face(f0, "Datum face", 0.0), Face(f100, "Overall face", 100.0),
            Face(f60, "Branch face", 60.0), Face(f99, "Return face", 99.0),
        ])],
        instances=[PartInstance(instance_id, "Profile", part_id)],
        dimensions=[pair[0] for pair in dimensions_and_sources],
        sources=[pair[1] for pair in dimensions_and_sources],
        constraints=[AssemblyConstraint(f"{prefix}-ground", "Fix datum face",
                                        "fixed_face", first=origin, value=0.0)],
        requirements=[FunctionalRequirement(
            requirement_id, "Functional gap", end, overall,
            min_value=0.0 if not statistical else 0.8,
            max_value=1.5 if not statistical else 1.2,
            methods=["worst_case", "rss", "monte_carlo"] if statistical else ["worst_case"],
        )],
        analysis_cases=[AnalysisCase(f"{prefix}-case", "Default analysis", requirement_id,
                                     methods=["worst_case", "rss", "monte_carlo"] if statistical
                                     else ["worst_case"], samples=30000, seed=2026)],
    )
    return project


def floating_block() -> Project:
    """Example B: housing H, left-mounted spacer S, movable block L."""
    housing_left = FaceRef("b-housing", "b-house-left")
    housing_right = FaceRef("b-housing", "b-house-right")
    spacer_left = FaceRef("b-spacer", "b-spacer-left")
    spacer_right = FaceRef("b-spacer", "b-spacer-right")
    block_left = FaceRef("b-block", "b-block-left")
    block_right = FaceRef("b-block", "b-block-right")
    pairs = [
        _dimension("b-h", "Housing internal length", housing_left, housing_right, 100.0, 0.20,
                   std=0.20 / 3),
        _dimension("b-s", "Spacer length", spacer_left, spacer_right, 25.0, 0.10,
                   std=0.10 / 3),
        _dimension("b-l", "Block length", block_left, block_right, 40.0, 0.15,
                   std=0.15 / 3),
    ]
    contacts = [
        ContactPair("b-left-contact", spacer_right, block_left, name="Spacer to block"),
        ContactPair("b-right-contact", block_right, housing_right, name="Block to right wall"),
    ]
    policies = [
        AssemblyPolicy("b-free", "Free floating", "free"),
        AssemblyPolicy("b-left", "Seated against spacer", "left_contact", ["b-left-contact"]),
        AssemblyPolicy("b-right", "Seated against right wall", "right_contact", ["b-right-contact"]),
        AssemblyPolicy("b-centered", "Centered within clearance", "centered",
                       ["b-left-contact", "b-right-contact"]),
    ]
    requirements = [
        FunctionalRequirement("b-clearance", "Total available clearance at spacer seat",
                              block_right, housing_right, min_value=34.0,
                              policy_id="b-left", methods=["worst_case"]),
        FunctionalRequirement("b-free-gap", "Right-side gap with free block",
                              block_right, housing_right, policy_id="b-free",
                              methods=["worst_case"]),
        FunctionalRequirement("b-right-gap", "Right-side gap at right seat",
                              block_right, housing_right, policy_id="b-right",
                              methods=["worst_case"]),
        FunctionalRequirement("b-centered-gap", "Right-side gap with centered block",
                              block_right, housing_right, policy_id="b-centered",
                              methods=["worst_case", "monte_carlo"]),
    ]
    return Project(
        id="example-b", name="Floating block and assembly shift",
        definitions=[
            PartDefinition("b-housing-def", "Housing", [Face("b-house-left", "Left wall", 0.0),
                                                         Face("b-house-right", "Right wall", 100.0)]),
            PartDefinition("b-spacer-def", "Spacer", [Face("b-spacer-left", "Left face", 0.0),
                                                       Face("b-spacer-right", "Right face", 25.0)]),
            PartDefinition("b-block-def", "Block", [Face("b-block-left", "Left face", 0.0),
                                                     Face("b-block-right", "Right face", 40.0)]),
        ],
        instances=[PartInstance("b-housing", "Housing", "b-housing-def"),
                   PartInstance("b-spacer", "Spacer", "b-spacer-def"),
                   PartInstance("b-block", "Sliding block", "b-block-def", translation=25.0)],
        dimensions=[pair[0] for pair in pairs],
        sources=[pair[1] for pair in pairs],
        constraints=[AssemblyConstraint("b-ground", "Fix housing left face", "fixed_face",
                                        first=housing_left, value=0.0),
                     AssemblyConstraint("b-spacer-mount", "Mount spacer at left wall",
                                        "coincident", first=housing_left, second=spacer_left)],
        contacts=contacts, policies=policies, requirements=requirements,
        analysis_cases=[AnalysisCase("b-case", "Seated clearance analysis", "b-clearance",
                                     methods=["worst_case"])],
    )


def coupled_float() -> Project:
    """Example C: two movable blocks sharing one finite housing clearance."""
    h0, h1 = FaceRef("c-house", "c-house-left"), FaceRef("c-house", "c-house-right")
    a0, a1 = FaceRef("c-first", "c-first-left"), FaceRef("c-first", "c-first-right")
    b0, b1 = FaceRef("c-second", "c-second-left"), FaceRef("c-second", "c-second-right")
    pairs = [
        _dimension("c-h", "Housing length", h0, h1, 100.0, 0.20),
        _dimension("c-a", "First block length", a0, a1, 30.0, 0.15),
        _dimension("c-b", "Second block length", b0, b1, 20.0, 0.10),
    ]
    contacts = [
        ContactPair("c-left", h0, a0, name="First block to left wall"),
        ContactPair("c-between", a1, b0, name="Blocks cannot overlap"),
        ContactPair("c-right", b1, h1, name="Second block to right wall"),
    ]
    return Project(
        id="example-c", name="Coupled floating components",
        definitions=[
            PartDefinition("c-house-def", "Housing", [Face("c-house-left", "Left", 0.0),
                                                       Face("c-house-right", "Right", 100.0)]),
            PartDefinition("c-first-def", "First block", [Face("c-first-left", "Left", 0.0),
                                                           Face("c-first-right", "Right", 30.0)]),
            PartDefinition("c-second-def", "Second block", [Face("c-second-left", "Left", 0.0),
                                                             Face("c-second-right", "Right", 20.0)]),
        ],
        instances=[PartInstance("c-house", "Housing", "c-house-def"),
                   PartInstance("c-first", "First block", "c-first-def", translation=10.0),
                   PartInstance("c-second", "Second block", "c-second-def", translation=60.0)],
        dimensions=[pair[0] for pair in pairs], sources=[pair[1] for pair in pairs],
        constraints=[AssemblyConstraint("c-ground", "Fix housing left face", "fixed_face",
                                        first=h0, value=0.0)],
        contacts=contacts,
        policies=[AssemblyPolicy("c-free", "Both blocks float", "free"),
                  AssemblyPolicy("c-seated", "Both blocks left-seated", "left_contact",
                                 ["c-left", "c-between"])],
        requirements=[FunctionalRequirement("c-interblock", "Interblock gap", a1, b0,
                                            policy_id="c-free", methods=["worst_case"]),
                      FunctionalRequirement("c-rear", "Rear clearance when left seated", b1, h1,
                                            policy_id="c-seated", methods=["worst_case"])],
    )


def inconsistent_loop() -> Project:
    """Example D: 10 + 10 conflicts with a 21 mm closing dimension."""
    ref_a, ref_b, ref_c = (FaceRef("d-part", face_id)
                           for face_id in ("d-a", "d-b", "d-c"))
    return Project(
        id="example-d", name="Inconsistent dimensional loop",
        definitions=[PartDefinition("d-definition", "Loop profile",
                                    [Face("d-a", "A", 0.0), Face("d-b", "B", 10.0),
                                     Face("d-c", "C", 20.0)])],
        instances=[PartInstance("d-part", "Loop profile", "d-definition")],
        dimensions=[Dimension("d-ab", "A to B", ref_a, ref_b, 10.0),
                    Dimension("d-bc", "B to C", ref_b, ref_c, 10.0),
                    Dimension("d-ac", "Conflicting A to C", ref_a, ref_c, 21.0)],
        constraints=[AssemblyConstraint("d-ground", "Fix loop origin", "fixed_face",
                                        first=ref_a, value=0.0)],
        requirements=[FunctionalRequirement("d-gap", "Loop closure", ref_a, ref_c,
                                            methods=["worst_case"])],
    )


EXAMPLE_BUILDERS = {
    "A": fixed_chain,
    "B": floating_block,
    "C": coupled_float,
    "D": inconsistent_loop,
    "E": lambda: fixed_chain(statistical=True),
}

EXAMPLE_FILENAMES = {
    "A": "a-fixed-chain.stack1d",
    "B": "b-floating-block.stack1d",
    "C": "c-coupled-floating.stack1d",
    "D": "d-inconsistent-loop.stack1d",
    "E": "e-statistical-chain.stack1d",
}


def create_example(name: str) -> Project:
    key = name.strip().upper()
    try:
        return EXAMPLE_BUILDERS[key]()
    except KeyError as exc:
        raise ValueError(f"Unknown example '{name}'; choose A, B, C, D, or E") from exc


def write_example_projects(directory: str | Path) -> list[Path]:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for key, filename in EXAMPLE_FILENAMES.items():
        path = destination / filename
        save_project(path, create_example(key))
        files.append(path)
    return files
