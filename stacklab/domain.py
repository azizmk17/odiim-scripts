"""Editable, presentation-independent StackLab 1D engineering model.

All coordinates and dimensions use ``Project.unit``.  Face coordinates are local
to a part instance; the assembly coordinate is local_x + instance translation.
Tolerance limits are signed deviations from a dimension's nominal value.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4
import copy


def new_id(prefix: str = "item") -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


@dataclass
class Face:
    id: str
    name: str
    local_x: float = 0.0
    lane: str = "default"
    local_y: float = 0.0  # sketch location; axial calculations use local_x only


@dataclass(frozen=True)
class FaceRef:
    instance_id: str
    face_id: str


@dataclass
class SketchVertex:
    x: float
    y: float
    face_id: str | None = None  # bind the drawn X coordinate to a solved face


@dataclass
class Outline:
    id: str
    name: str
    vertices: list[SketchVertex] = field(default_factory=list)
    closed: bool = True
    color: str = "#4f86b2"


@dataclass
class PartDefinition:
    id: str
    name: str
    faces: list[Face] = field(default_factory=list)
    outlines: list[Outline] = field(default_factory=list)


@dataclass
class PartInstance:
    id: str
    name: str
    definition_id: str
    translation: float = 0.0
    visible: bool = True


@dataclass
class Tolerance:
    lower: float = 0.0
    upper: float = 0.0


@dataclass
class Dimension:
    id: str
    name: str
    first: FaceRef
    second: FaceRef
    nominal: float
    tolerance: Tolerance = field(default_factory=Tolerance)
    kind: str = "driving"  # driving, reference, basic, derived
    source_id: str | None = None
    coefficient: float = 1.0
    display_style: str = "bilateral"  # bilateral, unilateral, limits


@dataclass
class Parameter:
    id: str
    name: str
    nominal: float = 0.0


@dataclass
class VariationSource:
    id: str
    name: str
    lower: float
    upper: float
    distribution: str = "normal"  # normal, uniform, fixed
    mean: float = 0.0
    std: float | None = None
    sigma_level: float | None = None


@dataclass
class Correlation:
    first_source_id: str
    second_source_id: str
    rho: float
    id: str = field(default_factory=lambda: new_id("correlation"))


@dataclass
class AssemblyConstraint:
    id: str
    name: str
    kind: str  # fixed_position, coincident, fixed_offset, bounded_translation,
               # unilateral_contact, clearance_joint, floating
    first: FaceRef | None = None
    second: FaceRef | None = None
    instance_id: str | None = None
    value: float = 0.0
    lower: float | None = None
    upper: float | None = None


@dataclass
class Joint:
    id: str
    name: str
    first: FaceRef
    second: FaceRef
    lower: float = 0.0
    upper: float = 0.0


@dataclass
class ContactPair:
    id: str
    first: FaceRef
    second: FaceRef
    lane: str = "default"
    name: str = ""


@dataclass
class Assembly:
    id: str
    name: str
    instance_ids: list[str] = field(default_factory=list)


@dataclass
class AssemblyPolicy:
    id: str
    name: str
    kind: str = "free"  # free, left_contact, right_contact, centered, closest, bounded
    contact_ids: list[str] = field(default_factory=list)
    bounds: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass
class FunctionalRequirement:
    id: str
    name: str
    first: FaceRef
    second: FaceRef
    direction: int = 1
    min_value: float | None = None
    max_value: float | None = None
    policy_id: str | None = None
    methods: list[str] = field(default_factory=lambda: ["worst_case", "rss"])


@dataclass
class AnalysisCase:
    id: str
    name: str
    requirement_id: str
    methods: list[str] = field(default_factory=lambda: ["worst_case", "rss"])
    samples: int = 10000
    seed: int = 0
    sigma_level: float = 3.0


@dataclass
class Diagnostic:
    code: str
    message: str
    entity_ids: list[str] = field(default_factory=list)
    severity: str = "error"


@dataclass
class ChainTerm:
    source_id: str
    coefficient: float
    dimension_ids: list[str] = field(default_factory=list)


@dataclass
class ChainResult:
    nominal: float | None = None
    terms: list[ChainTerm] = field(default_factory=list)
    equation: str = ""
    floating: bool = False
    degrees_of_freedom: int = 0


@dataclass
class ExtremeState:
    gap: float
    source_values: dict[str, float] = field(default_factory=dict)
    translations: dict[str, float] = field(default_factory=dict)
    face_positions: dict[str, float] = field(default_factory=dict)
    active_contacts: list[str] = field(default_factory=list)


@dataclass
class WorstCaseResult:
    nominal: float | None
    minimum: float
    maximum: float
    minimum_state: ExtremeState
    maximum_state: ExtremeState
    possible_interference: bool
    meets_limits: bool | None
    policy_kind: str = "free"
    warnings: list[str] = field(default_factory=list)


@dataclass
class RSSResult:
    mean: float
    variance: float
    std: float
    sigma_level: float
    lower: float
    upper: float
    variance_contributions: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class MonteCarloResult:
    requested_samples: int
    valid_samples: int
    infeasible_samples: int
    seed: int
    minimum: float | None
    maximum: float | None
    mean: float | None
    std: float | None
    percentiles: dict[str, float] = field(default_factory=dict)
    histogram_edges: list[float] = field(default_factory=list)
    histogram_counts: list[int] = field(default_factory=list)
    specification_failures: int = 0
    conditional_failure_probability: float | None = None
    overall_failure_probability: float | None = None
    ppm_outside_spec: float | None = None
    failure_probability_ci95: tuple[float, float] | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    requirement_id: str
    chain: ChainResult
    worst_case: WorstCaseResult | None = None
    rss: RSSResult | None = None
    monte_carlo: MonteCarloResult | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class Project:
    id: str
    name: str
    unit: str = "mm"
    definitions: list[PartDefinition] = field(default_factory=list)
    instances: list[PartInstance] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    sources: list[VariationSource] = field(default_factory=list)
    constraints: list[AssemblyConstraint] = field(default_factory=list)
    contacts: list[ContactPair] = field(default_factory=list)
    requirements: list[FunctionalRequirement] = field(default_factory=list)
    policies: list[AssemblyPolicy] = field(default_factory=list)
    correlations: list[Correlation] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    joints: list[Joint] = field(default_factory=list)
    assemblies: list[Assembly] = field(default_factory=list)
    analysis_cases: list[AnalysisCase] = field(default_factory=list)
    view: dict[str, Any] = field(default_factory=dict)

    def copy(self) -> Project:
        return copy.deepcopy(self)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Project:
        if not isinstance(value, dict):
            raise ValueError("Project data must be a JSON object")
        def ref(item: Any) -> FaceRef | None:
            if item is None:
                return None
            return FaceRef(**item)
        try:
            definitions = [PartDefinition(**{**d, "faces": [Face(**f) for f in d.get("faces", [])],
                "outlines": [Outline(**{**o, "vertices": [SketchVertex(**v) for v in o.get("vertices", [])]})
                             for o in d.get("outlines", [])]}) for d in value.get("definitions", [])]
            dimensions = [Dimension(**{**d, "first": ref(d["first"]), "second": ref(d["second"]),
                                       "tolerance": Tolerance(**d.get("tolerance", {}))}) for d in value.get("dimensions", [])]
            constraints = [AssemblyConstraint(**{**c, "first": ref(c.get("first")), "second": ref(c.get("second"))}) for c in value.get("constraints", [])]
            contacts = [ContactPair(**{**c, "first": ref(c["first"]), "second": ref(c["second"])}) for c in value.get("contacts", [])]
            requirements = [FunctionalRequirement(**{**r, "first": ref(r["first"]), "second": ref(r["second"])}) for r in value.get("requirements", [])]
            joints = [Joint(**{**j, "first": ref(j["first"]), "second": ref(j["second"])}) for j in value.get("joints", [])]
            policies = [AssemblyPolicy(**{**p, "bounds": {k: tuple(v) for k, v in p.get("bounds", {}).items()}}) for p in value.get("policies", [])]
            project = cls(
                id=value["id"], name=value["name"], unit=value.get("unit", "mm"), definitions=definitions,
                instances=[PartInstance(**i) for i in value.get("instances", [])], dimensions=dimensions,
                sources=[VariationSource(**s) for s in value.get("sources", [])], constraints=constraints,
                contacts=contacts, requirements=requirements, policies=policies,
                correlations=[Correlation(**c) for c in value.get("correlations", [])],
                parameters=[Parameter(**p) for p in value.get("parameters", [])], joints=joints,
                assemblies=[Assembly(**a) for a in value.get("assemblies", [])],
                analysis_cases=[AnalysisCase(**a) for a in value.get("analysis_cases", [])],
                view=value.get("view", {}),
            )
            errors = [d for d in project.validate() if d.severity == "error"]
            if errors:
                raise ValueError("; ".join(d.message for d in errors))
            return project
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Malformed project data: {exc}") from exc

    def validate(self) -> list[Diagnostic]:
        from .compiler import validate_project
        return validate_project(self)
