"""Reproducible engineering reference assemblies for StackLab 1D."""

from __future__ import annotations

from pathlib import Path

from .domain import (
    AnalysisCase, AssemblyConstraint, AssemblyPolicy, ContactPair, Dimension,
    Face, FaceRef, FunctionalRequirement, Outline, PartDefinition, PartInstance,
    Project, SketchVertex, Tolerance, VariationSource,
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


def stepped_pin_reference() -> Project:
    """Illustrative solution to a stepped pin, cradle, guard and datum sketch.

    The reference image has no dimensions; these values are editable examples,
    not measurements extracted from its pixels.
    """
    base_left = FaceRef("f-base", "f-base-left")
    base_right = FaceRef("f-base", "f-base-right")
    insert_left = FaceRef("f-insert", "f-insert-left")
    insert_inner = FaceRef("f-insert", "f-insert-inner")
    pin_left = FaceRef("f-pin", "f-pin-left")
    pin_head = FaceRef("f-pin", "f-pin-head")
    pin_stem = FaceRef("f-pin", "f-pin-stem")
    guard_left = FaceRef("f-guard", "f-guard-left")
    guard_right = FaceRef("f-guard", "f-guard-right")
    datum = FaceRef("f-datum", "f-datum-face")
    pairs = [
        _dimension("f-base-width", "Cradle outside width", base_left, base_right, 70, 0.20,
                   std=0.20 / 3),
        _dimension("f-insert-opening", "Insert inner right edge", insert_left, insert_inner,
                   50, 0.10, std=0.10 / 3),
        _dimension("f-pin-head-width", "Pin head width", pin_left, pin_head, 52, 0.15,
                   std=0.15 / 3),
        _dimension("f-guard-width", "Guard thickness", guard_left, guard_right, 5, 0.10,
                   std=0.10 / 3),
    ]
    def polygon(identifier, name, coords, color, bindings=None):
        bindings = bindings or {}
        return Outline(identifier, name,
                       [SketchVertex(x, y, bindings.get(x)) for x, y in coords],
                       True, color)
    base_outline = polygon("f-base-shape", "U-shaped cradle",
        [(0, 18), (8, 18), (8, 47), (66, 47), (66, 18), (70, 18), (70, 53), (0, 53)],
        "#d4ad00", {0: "f-base-left", 70: "f-base-right"})
    insert_outline = polygon("f-insert-shape", "U-shaped insert",
        [(0, 10), (8, 10), (8, 38), (50, 38), (50, 10), (55, 10), (55, 44), (0, 44)],
        "#6132aa", {0: "f-insert-left", 50: "f-insert-inner"})
    pin_outline = polygon("f-pin-shape", "Stepped pin",
        [(0, -25), (52, -25), (52, -7), (35, -7), (35, 32), (17, 32),
         (17, -7), (0, -7)],
        "#d44845", {0: "f-pin-left", 52: "f-pin-head", 35: "f-pin-stem"})
    guard_outline = polygon("f-guard-shape", "Upper guard",
        [(0, -30), (5, -30), (5, 0), (0, 0)], "#d4ad00",
        {0: "f-guard-left", 5: "f-guard-right"})
    definitions = [
        PartDefinition("f-base-def", "Yellow cradle", [
            Face("f-base-left", "Cradle left", 0, local_y=18),
            Face("f-base-right", "Cradle right", 70, local_y=18)], [base_outline]),
        PartDefinition("f-insert-def", "Purple insert", [
            Face("f-insert-left", "Insert left", 0, local_y=10),
            Face("f-insert-inner", "Insert inner edge", 50, "slot", 10)], [insert_outline]),
        PartDefinition("f-pin-def", "Red stepped pin", [
            Face("f-pin-left", "Head left", 0, local_y=-25),
            Face("f-pin-head", "Head right", 52, "head", -25),
            Face("f-pin-stem", "Stem right", 35, "slot", 32)], [pin_outline]),
        PartDefinition("f-guard-def", "Yellow upper guard", [
            Face("f-guard-left", "Guard left", 0, "head", -30),
            Face("f-guard-right", "Guard right", 5, local_y=-30)], [guard_outline]),
        PartDefinition("f-datum-def", "Right reference line", [
            Face("f-datum-face", "Reference", 0, "centerline")]),
    ]
    instances = [
        PartInstance("f-base", "Yellow cradle", "f-base-def"),
        PartInstance("f-insert", "Purple insert", "f-insert-def", 10),
        PartInstance("f-pin", "Red stepped pin", "f-pin-def", 20),
        PartInstance("f-guard", "Upper guard", "f-guard-def", 75),
        PartInstance("f-datum", "Right datum", "f-datum-def", 100),
    ]
    requirements = [
        FunctionalRequirement("f-head-gap", "Head-to-guard gap", pin_head, guard_left,
                              min_value=2, max_value=4, methods=["worst_case", "rss"]),
        FunctionalRequirement("f-upper-datum", "Upper guard-to-datum distance", guard_right,
                              datum, methods=["worst_case"]),
        FunctionalRequirement("f-lower-datum", "Cradle-to-datum distance", base_right,
                              datum, methods=["worst_case"]),
        FunctionalRequirement("f-stem-gap", "Stem-to-insert clearance", pin_stem,
                              insert_inner, methods=["worst_case"]),
    ]
    return Project(
        id="example-f", name="Stepped pin with two reference distances",
        definitions=definitions, instances=instances,
        dimensions=[*(pair[0] for pair in pairs),
            Dimension("f-stem-offset", "Head left to stem right", pin_left, pin_stem,
                      35, kind="basic")],
        sources=[pair[1] for pair in pairs],
        constraints=[
            AssemblyConstraint("f-ground", "Fix cradle left", "fixed_face",
                               first=base_left, value=0),
            AssemblyConstraint("f-insert-location", "Locate insert in cradle", "fixed_offset",
                               first=base_left, second=insert_left, value=10),
            AssemblyConstraint("f-pin-location", "Locate pin head", "fixed_offset",
                               first=base_left, second=pin_left, value=20),
            AssemblyConstraint("f-guard-location", "Guard beyond cradle", "fixed_offset",
                               first=base_right, second=guard_left, value=5),
            AssemblyConstraint("f-reference", "Fix right reference line", "fixed_face",
                               first=datum, value=100),
        ],
        contacts=[
            ContactPair("f-head-contact", pin_head, guard_left, "head", "Head cannot enter guard"),
            ContactPair("f-slot-contact", pin_stem, insert_inner, "slot", "Stem cannot enter insert side"),
        ],
        requirements=requirements,
        analysis_cases=[AnalysisCase("f-case", "Head clearance", "f-head-gap",
                                     ["worst_case", "rss"])],
        view={"part_y": {instance.id: 210 for instance in instances},
              "long_centerline": True},
    )


EXAMPLE_BUILDERS = {
    "A": fixed_chain,
    "B": floating_block,
    "C": coupled_float,
    "D": inconsistent_loop,
    "E": lambda: fixed_chain(statistical=True),
    "F": stepped_pin_reference,
}

EXAMPLE_FILENAMES = {
    "A": "a-fixed-chain.stack1d",
    "B": "b-floating-block.stack1d",
    "C": "c-coupled-floating.stack1d",
    "D": "d-inconsistent-loop.stack1d",
    "E": "e-statistical-chain.stack1d",
    "F": "f-stepped-pin-reference.stack1d",
}


def create_example(name: str) -> Project:
    key = name.strip().upper()
    try:
        return EXAMPLE_BUILDERS[key]()
    except KeyError as exc:
        raise ValueError(f"Unknown example '{name}'; choose A, B, C, D, E, or F") from exc


def write_example_projects(directory: str | Path) -> list[Path]:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for key, filename in EXAMPLE_FILENAMES.items():
        path = destination / filename
        save_project(path, create_example(key))
        files.append(path)
    return files
