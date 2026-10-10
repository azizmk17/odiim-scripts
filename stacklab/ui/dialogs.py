"""Focused editors for axial model entities."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from stacklab.domain import FaceRef, Project, SketchVertex


def number(value: float = 0.0, *, low: float = -1e9, high: float = 1e9, decimals: int = 4) -> QDoubleSpinBox:
    widget = QDoubleSpinBox()
    widget.setDecimals(decimals)
    widget.setRange(low, high)
    widget.setSingleStep(0.1)
    widget.setValue(value)
    return widget


def face_options(project: Project) -> list[tuple[str, FaceRef]]:
    definitions = {definition.id: definition for definition in project.definitions}
    choices = []
    for instance in project.instances:
        definition = definitions.get(instance.definition_id)
        if definition is None:
            continue
        for face in definition.faces:
            choices.append((f"{instance.name} / {face.name}", FaceRef(instance.id, face.id)))
    return choices


def face_combo(project: Project, selected: FaceRef | None = None) -> QComboBox:
    combo = QComboBox()
    for label, reference in face_options(project):
        combo.addItem(label, reference)
        if selected == reference:
            combo.setCurrentIndex(combo.count() - 1)
    return combo


class FormDialog(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignRight)
        layout.addLayout(self.form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def reject_input(self, message: str) -> None:
        QMessageBox.warning(self, "Check input", message)


class PartDialog(FormDialog):
    def __init__(self, project: Project, parent=None):
        super().__init__("Create axial part", parent)
        self.name = QLineEdit(f"Part {len(project.instances) + 1}")
        self.origin = number(0)
        self.length = number(40, low=0.001)
        self.profile = QLineEdit("")
        self.profile.setPlaceholderText("Optional intermediate face positions, e.g. 12, 28")
        self.lane = QLineEdit("default")
        self.form.addRow("Name", self.name)
        self.form.addRow("Assembly position", self.origin)
        self.form.addRow("Length", self.length)
        self.form.addRow("Intermediate faces", self.profile)
        self.form.addRow("Interface lane", self.lane)

    def face_positions(self) -> list[float]:
        positions = [0.0]
        if self.profile.text().strip():
            positions.extend(float(token.strip()) for token in self.profile.text().split(","))
        positions.append(self.length.value())
        return sorted(set(positions))

    def accept(self) -> None:
        if not self.name.text().strip():
            return self.reject_input("Give the part a name.")
        try:
            positions = self.face_positions()
        except ValueError:
            return self.reject_input("Enter intermediate face positions as comma-separated numbers.")
        if len(positions) < 2 or any(x < 0 or x > self.length.value() for x in positions):
            return self.reject_input("Intermediate faces must lie between 0 and the part length.")
        super().accept()


class FaceDialog(FormDialog):
    def __init__(self, name: str = "Face", x: float = 0.0, lane: str = "default", parent=None,
                 *, y: float = 0.0):
        super().__init__("Edit axial face", parent)
        self.name = QLineEdit(name)
        self.x = number(x)
        self.y = number(y)
        self.lane = QLineEdit(lane)
        self.form.addRow("Name", self.name)
        self.form.addRow("Local X", self.x)
        self.form.addRow("Sketch Y", self.y)
        self.form.addRow("Interface lane", self.lane)

    def accept(self) -> None:
        if not self.name.text().strip():
            return self.reject_input("Give the face a name.")
        if not self.lane.text().strip():
            return self.reject_input("Give the face an interface lane.")
        super().accept()


class OutlineDialog(FormDialog):
    def __init__(self, definition, outline, parent=None):
        super().__init__("Edit sketched outline", parent)
        self.setMinimumWidth(480)
        self.name = QLineEdit(outline.name)
        self.closed = QCheckBox("Closed, filled region")
        self.closed.setChecked(outline.closed)
        self.color = QLineEdit(outline.color)
        self.vertices = QPlainTextEdit()
        self.vertices.setMinimumHeight(190)
        self.vertices.setPlainText("\n".join(
            f"{vertex.x:g}, {vertex.y:g}" + (f", {vertex.face_id}" if vertex.face_id else "")
            for vertex in outline.vertices))
        self._face_ids = {face.id for face in definition.faces}
        names = ", ".join(f"{face.name}={face.id}" for face in definition.faces)
        hint = QLabel("One X, Y vertex per line. Optional third value binds X to a face.\n" + names)
        hint.setWordWrap(True)
        self.form.addRow("Name", self.name)
        self.form.addRow("Region", self.closed)
        self.form.addRow("Color", self.color)
        self.form.addRow("Vertices", self.vertices)
        self.form.addRow(hint)

    def parsed_vertices(self) -> list[SketchVertex]:
        vertices = []
        for line in self.vertices.toPlainText().splitlines():
            if not line.strip():
                continue
            fields = [field.strip() for field in line.split(",")]
            if len(fields) not in {2, 3}:
                raise ValueError("Each vertex needs X, Y and optionally a face ID.")
            face_id = fields[2] if len(fields) == 3 and fields[2] else None
            if face_id is not None and face_id not in self._face_ids:
                raise ValueError(f"Unknown face ID: {face_id}")
            vertices.append(SketchVertex(float(fields[0]), float(fields[1]), face_id))
        return vertices

    def accept(self) -> None:
        if not self.name.text().strip():
            return self.reject_input("Give the outline a name.")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color.text().strip()):
            return self.reject_input("Use a six-digit hex color such as #4f86b2.")
        try:
            vertices = self.parsed_vertices()
        except ValueError as exc:
            return self.reject_input(str(exc))
        if len(vertices) < (3 if self.closed.isChecked() else 2):
            return self.reject_input("This outline needs more vertices.")
        super().accept()


class InstanceDialog(FormDialog):
    def __init__(self, instance, parent=None):
        super().__init__("Edit part instance", parent)
        self.name = QLineEdit(instance.name)
        self.translation = number(instance.translation)
        self.visible = QCheckBox("Show in sketch")
        self.visible.setChecked(instance.visible)
        self.form.addRow("Name", self.name)
        self.form.addRow("Initial axial position", self.translation)
        self.form.addRow("Visibility", self.visible)

    def accept(self) -> None:
        if not self.name.text().strip():
            return self.reject_input("Give the part a name.")
        super().accept()


class DimensionDialog(FormDialog):
    def __init__(self, project: Project, first: FaceRef | None = None, second: FaceRef | None = None, dimension=None, parent=None):
        super().__init__("Dimension between faces", parent)
        self.name = QLineEdit(dimension.name if dimension else f"D{len(project.dimensions) + 1}")
        self.first = face_combo(project, dimension.first if dimension else first)
        self.second = face_combo(project, dimension.second if dimension else second)
        self.nominal = number(dimension.nominal if dimension else 0.0)
        tolerance = dimension.tolerance if dimension else None
        self.lower = number(tolerance.lower if tolerance else -0.1)
        self.upper = number(tolerance.upper if tolerance else 0.1)
        self.kind = QComboBox()
        self.kind.addItems(["driving", "reference", "basic", "derived"])
        self.kind.setCurrentText(dimension.kind if dimension else "driving")
        self.display = QComboBox()
        self.display.addItem("Bilateral", "bilateral")
        self.display.addItem("Unilateral", "unilateral")
        self.display.addItem("Limits", "limits")
        if dimension:
            self.display.setCurrentIndex(max(0, self.display.findData(dimension.display_style)))
        self._sources = {source.id: source for source in project.sources}
        self.source_choice = QComboBox()
        self.source_choice.addItem("New independent source", None)
        for source in project.sources:
            self.source_choice.addItem(f"{source.name} ({source.id})", source.id)
        if dimension and dimension.source_id:
            self.source_choice.setCurrentIndex(max(0, self.source_choice.findData(dimension.source_id)))
        self.coefficient = number(dimension.coefficient if dimension else 1.0,
                                  low=-1000, high=1000)
        self.coefficient.setToolTip("Signed influence of this source on the driving dimension")
        self.distribution = QComboBox()
        self.distribution.addItems(["normal", "uniform", "fixed"])
        self.sigma = number(0.0, low=0)
        self.sigma.setSpecialValueText("Use explicit mapping")
        self.sigma_level = number(3.0, low=0.01)
        self.form.addRow("Name", self.name)
        self.form.addRow("First face", self.first)
        self.form.addRow("Second face", self.second)
        self.form.addRow("Nominal", self.nominal)
        self.form.addRow("Lower deviation", self.lower)
        self.form.addRow("Upper deviation", self.upper)
        self.form.addRow("Dimension role", self.kind)
        self.form.addRow("Display style", self.display)
        self.form.addRow("Manufacturing source", self.source_choice)
        self.form.addRow("Source coefficient", self.coefficient)
        self.form.addRow("Process distribution", self.distribution)
        self.form.addRow("Process σ (0 = map limits)", self.sigma)
        self.form.addRow("Limit σ level", self.sigma_level)
        self.form.addRow(QLabel("A reused source varies linked dimensions together; edit its process data in the assembly tree."))
        self.kind.currentIndexChanged.connect(self._update_source_fields)
        self.source_choice.currentIndexChanged.connect(self._update_source_fields)
        self.coefficient.valueChanged.connect(self._update_source_fields)
        self._update_source_fields()

    def _update_source_fields(self) -> None:
        driving = self.kind.currentText() == "driving"
        source = self._sources.get(self.source_choice.currentData()) if driving else None
        self.source_choice.setEnabled(driving)
        self.coefficient.setEnabled(driving)
        for widget in (self.lower, self.upper, self.distribution, self.sigma, self.sigma_level):
            widget.setEnabled(driving and source is None)
        if source is not None:
            deviations = (self.coefficient.value() * source.lower,
                          self.coefficient.value() * source.upper)
            self.lower.setValue(min(deviations))
            self.upper.setValue(max(deviations))
            self.distribution.setCurrentText(source.distribution)
            self.sigma.setValue(source.std or 0)
            self.sigma_level.setValue(source.sigma_level or 3)

    def accept(self) -> None:
        if not self.name.text().strip() or self.first.currentData() is None or self.second.currentData() is None:
            return self.reject_input("Name the dimension and choose two faces.")
        if self.first.currentData() == self.second.currentData():
            return self.reject_input("Choose two different faces.")
        if self.lower.value() > self.upper.value():
            return self.reject_input("Lower deviation must not exceed upper deviation.")
        if self.kind.currentText() == "driving" and abs(self.coefficient.value()) < 1e-9:
            return self.reject_input("A driving source coefficient must be nonzero.")
        super().accept()


class SketchDimensionDialog(FormDialog):
    """Edit a 2D drawing dimension without claiming it affects axial tolerance."""

    def __init__(self, name: str, value: float, unit: str, *, driving: bool = True,
                 parent=None):
        super().__init__("Sketch dimension", parent)
        self.name = QLineEdit(name)
        self.value = number(value, low=0)
        self.driving = QCheckBox("Drive selected sketch geometry")
        self.driving.setChecked(driving)
        self.form.addRow("Name", self.name)
        self.form.addRow(f"Distance ({unit})", self.value)
        self.form.addRow(self.driving)
        self.form.addRow(QLabel("2D sketch dimensions shape the drawing. Axial dimensions drive the 1D tolerance stack."))

    def accept(self) -> None:
        if not self.name.text().strip():
            return self.reject_input("Give the sketch dimension a name.")
        super().accept()


class SourceDialog(FormDialog):
    def __init__(self, source, parent=None):
        super().__init__("Manufacturing variation source", parent)
        self.name = QLineEdit(source.name)
        self.lower = number(source.lower)
        self.upper = number(source.upper)
        self.distribution = QComboBox()
        self.distribution.addItems(["normal", "uniform", "fixed"])
        self.distribution.setCurrentText(source.distribution)
        self.mean = number(source.mean)
        self.std = number(source.std or 0, low=0)
        self.sigma_level = number(source.sigma_level or 3, low=0.01)
        self.form.addRow("Name", self.name)
        self.form.addRow("Lower deviation", self.lower)
        self.form.addRow("Upper deviation", self.upper)
        self.form.addRow("Distribution", self.distribution)
        self.form.addRow("Process mean", self.mean)
        self.form.addRow("Process σ (0 = map limits)", self.std)
        self.form.addRow("Limit σ level", self.sigma_level)

    def accept(self) -> None:
        if not self.name.text().strip():
            return self.reject_input("Give the source a name.")
        if self.lower.value() > self.upper.value():
            return self.reject_input("Lower deviation must not exceed upper deviation.")
        super().accept()


class CorrelationDialog(FormDialog):
    def __init__(self, project: Project, correlation=None, parent=None):
        super().__init__("Source correlation", parent)
        self.first = QComboBox()
        self.second = QComboBox()
        for source in project.sources:
            for combo in (self.first, self.second):
                combo.addItem(source.name, source.id)
        if correlation:
            self.first.setCurrentIndex(max(0, self.first.findData(correlation.first_source_id)))
            self.second.setCurrentIndex(max(0, self.second.findData(correlation.second_source_id)))
        elif self.second.count() > 1:
            self.second.setCurrentIndex(1)
        self.rho = number(correlation.rho if correlation else 0.0, low=-1, high=1)
        self.form.addRow("First source", self.first)
        self.form.addRow("Second source", self.second)
        self.form.addRow("Correlation ρ", self.rho)
        self.form.addRow(QLabel("ρ = +1 means linked motion; ρ = −1 means opposite motion."))

    def accept(self) -> None:
        if self.first.currentData() is None or self.second.currentData() is None:
            return self.reject_input("Create two manufacturing sources first.")
        if self.first.currentData() == self.second.currentData():
            return self.reject_input("Choose two different sources.")
        super().accept()


class ConstraintDialog(FormDialog):
    KINDS = [
        ("Fixed part position", "fixed_position"),
        ("Fixed face at coordinate", "fixed_face"),
        ("Coincident faces", "coincident"),
        ("Fixed face offset", "fixed_offset"),
        ("Bounded part movement", "bounded_translation"),
        ("Unilateral contact", "unilateral_contact"),
        ("Clearance joint", "clearance_joint"),
    ]

    def __init__(self, project: Project, constraint=None, parent=None):
        super().__init__("Assembly constraint", parent)
        self.name = QLineEdit(constraint.name if constraint else f"Constraint {len(project.constraints) + 1}")
        self.kind = QComboBox()
        for label, value in self.KINDS:
            self.kind.addItem(label, value)
        if constraint:
            self.kind.setCurrentIndex(max(0, self.kind.findData(constraint.kind)))
        self.instance = QComboBox()
        for instance in project.instances:
            self.instance.addItem(instance.name, instance.id)
        if constraint and constraint.instance_id:
            self.instance.setCurrentIndex(max(0, self.instance.findData(constraint.instance_id)))
        self.first = face_combo(project, constraint.first if constraint else None)
        self.second = face_combo(project, constraint.second if constraint else None)
        self.value = number(constraint.value if constraint else 0)
        self.lower = number(constraint.lower if constraint and constraint.lower is not None else 0)
        self.upper = number(constraint.upper if constraint and constraint.upper is not None else 0)
        self.form.addRow("Name", self.name)
        self.form.addRow("Type", self.kind)
        self.form.addRow("Part", self.instance)
        self.form.addRow("First face", self.first)
        self.form.addRow("Second face", self.second)
        self.form.addRow("Offset / position", self.value)
        self.form.addRow("Lower movement", self.lower)
        self.form.addRow("Upper movement", self.upper)
        self.kind.currentIndexChanged.connect(self._update_fields)
        self._update_fields()

    def _update_fields(self) -> None:
        kind = self.kind.currentData()
        self.instance.setEnabled(kind in {"fixed_position", "bounded_translation"})
        self.first.setEnabled(kind not in {"fixed_position", "bounded_translation"})
        self.second.setEnabled(kind not in {"fixed_position", "bounded_translation", "fixed_face"})
        self.value.setEnabled(kind in {"fixed_position", "fixed_face", "fixed_offset"})
        self.lower.setEnabled(kind in {"bounded_translation", "clearance_joint"})
        self.upper.setEnabled(kind in {"bounded_translation", "clearance_joint"})

    def accept(self) -> None:
        if not self.name.text().strip():
            return self.reject_input("Give the constraint a name.")
        if self.kind.currentData() in {"bounded_translation", "clearance_joint"} and self.lower.value() > self.upper.value():
            return self.reject_input("Lower bound must not exceed upper bound.")
        if self.kind.currentData() not in {"fixed_position", "bounded_translation", "fixed_face"} and self.first.currentData() == self.second.currentData():
            return self.reject_input("Choose two different faces.")
        super().accept()


class ContactDialog(FormDialog):
    def __init__(self, project: Project, contact=None, first: FaceRef | None = None, second: FaceRef | None = None, parent=None):
        super().__init__("Candidate contact", parent)
        self.first = face_combo(project, contact.first if contact else first)
        self.second = face_combo(project, contact.second if contact else second)
        self.lane = QLineEdit(contact.lane if contact else "default")
        self.form.addRow("First face", self.first)
        self.form.addRow("Second face", self.second)
        self.form.addRow("Interface lane", self.lane)
        self.form.addRow(QLabel("A candidate may be open; analysis resolves active contact."))

    def accept(self) -> None:
        if self.first.currentData() is None or self.second.currentData() is None:
            return self.reject_input("Choose two faces.")
        if self.first.currentData() == self.second.currentData():
            return self.reject_input("Choose two different faces.")
        if not self.lane.text().strip():
            return self.reject_input("Specify a compatible interface lane.")
        super().accept()


class PolicyDialog(FormDialog):
    KINDS = [
        ("Free floating envelope", "free"),
        ("Left contact", "left_contact"),
        ("Right contact", "right_contact"),
        ("Centered", "centered"),
        ("Closest feasible", "closest"),
        ("User bounded", "bounded"),
    ]

    def __init__(self, project: Project, policy=None, parent=None):
        super().__init__("Assembly positioning policy", parent)
        self.name = QLineEdit(policy.name if policy else f"Policy {len(project.policies) + 1}")
        self.kind = QComboBox()
        for label, kind in self.KINDS:
            self.kind.addItem(label, kind)
        if policy:
            self.kind.setCurrentIndex(max(0, self.kind.findData(policy.kind)))
        self.left_contact = QComboBox()
        self.right_contact = QComboBox()
        for combo in (self.left_contact, self.right_contact):
            combo.addItem("Choose contact", None)
            for contact in project.contacts:
                combo.addItem(contact.name or contact.id, contact.id)
        if policy and policy.contact_ids:
            self.left_contact.setCurrentIndex(max(0, self.left_contact.findData(policy.contact_ids[0])))
            if len(policy.contact_ids) > 1:
                self.right_contact.setCurrentIndex(max(0, self.right_contact.findData(policy.contact_ids[1])))
        self.bounded_part = QComboBox()
        for instance in project.instances:
            self.bounded_part.addItem(instance.name, instance.id)
        bound_id, bounds = next(iter(policy.bounds.items()), (None, (0.0, 0.0))) if policy else (None, (0.0, 0.0))
        if bound_id:
            self.bounded_part.setCurrentIndex(max(0, self.bounded_part.findData(bound_id)))
        self.lower = number(bounds[0])
        self.upper = number(bounds[1])
        self.form.addRow("Name", self.name)
        self.form.addRow("Positioning rule", self.kind)
        self.form.addRow("Left / primary contact", self.left_contact)
        self.form.addRow("Right contact", self.right_contact)
        self.form.addRow("Bounded part", self.bounded_part)
        self.form.addRow("Lower displacement", self.lower)
        self.form.addRow("Upper displacement", self.upper)
        self.form.addRow(QLabel("For centering, choose the left opposing contact first and right opposing contact second."))
        self.kind.currentIndexChanged.connect(self._update_fields)
        self._update_fields()

    def selected_contact_ids(self) -> list[str]:
        kind = self.kind.currentData()
        if kind == "centered":
            return [self.left_contact.currentData(), self.right_contact.currentData()]
        if kind in {"left_contact", "right_contact"}:
            return [self.left_contact.currentData()]
        return []

    def selected_bounds(self) -> dict[str, tuple[float, float]]:
        if self.kind.currentData() == "bounded" and self.bounded_part.currentData() is not None:
            return {self.bounded_part.currentData(): (self.lower.value(), self.upper.value())}
        return {}

    def _update_fields(self) -> None:
        kind = self.kind.currentData()
        self.left_contact.setEnabled(kind in {"left_contact", "right_contact", "centered"})
        self.right_contact.setEnabled(kind == "centered")
        for widget in (self.bounded_part, self.lower, self.upper):
            widget.setEnabled(kind == "bounded")

    def accept(self) -> None:
        kind = self.kind.currentData()
        if not self.name.text().strip():
            return self.reject_input("Give the policy a name.")
        if kind in {"left_contact", "right_contact", "centered"} and self.left_contact.currentData() is None:
            return self.reject_input("Choose a primary contact.")
        if kind == "centered" and (self.right_contact.currentData() is None or
                                   self.right_contact.currentData() == self.left_contact.currentData()):
            return self.reject_input("Choose a different right opposing contact.")
        if kind == "bounded" and self.lower.value() > self.upper.value():
            return self.reject_input("Lower displacement must not exceed upper displacement.")
        super().accept()


class RequirementDialog(FormDialog):
    def __init__(self, project: Project, first: FaceRef | None = None, second: FaceRef | None = None, requirement=None, parent=None):
        super().__init__("Functional gap requirement", parent)
        self.name = QLineEdit(requirement.name if requirement else f"Gap {len(project.requirements) + 1}")
        self.first = face_combo(project, requirement.first if requirement else first)
        self.second = face_combo(project, requirement.second if requirement else second)
        self.direction = QComboBox()
        self.direction.addItem("Second − first", 1)
        self.direction.addItem("First − second", -1)
        if requirement and requirement.direction < 0:
            self.direction.setCurrentIndex(1)
        self.minimum_enabled = QCheckBox("Apply minimum")
        self.minimum_enabled.setChecked(requirement is not None and requirement.min_value is not None)
        self.minimum = number(requirement.min_value if requirement and requirement.min_value is not None else 0)
        self.maximum_enabled = QCheckBox("Apply maximum")
        self.maximum_enabled.setChecked(requirement is not None and requirement.max_value is not None)
        self.maximum = number(requirement.max_value if requirement and requirement.max_value is not None else 0)
        self.policy = QComboBox()
        self.policy.addItem("No policy / possible envelope", None)
        for policy in project.policies:
            self.policy.addItem(policy.name, policy.id)
        if requirement and requirement.policy_id:
            self.policy.setCurrentIndex(max(0, self.policy.findData(requirement.policy_id)))
        self.form.addRow("Name", self.name)
        self.form.addRow("First face", self.first)
        self.form.addRow("Second face", self.second)
        self.form.addRow("Measurement", self.direction)
        self.form.addRow(self.minimum_enabled, self.minimum)
        self.form.addRow(self.maximum_enabled, self.maximum)
        self.form.addRow("Assembly policy", self.policy)

    def accept(self) -> None:
        if not self.name.text().strip() or self.first.currentData() is None or self.second.currentData() is None:
            return self.reject_input("Name the requirement and choose two faces.")
        if self.first.currentData() == self.second.currentData():
            return self.reject_input("Choose two different faces.")
        if self.minimum_enabled.isChecked() and self.maximum_enabled.isChecked() and self.minimum.value() > self.maximum.value():
            return self.reject_input("Minimum acceptance must not exceed maximum acceptance.")
        super().accept()


class AnalysisSettingsDialog(FormDialog):
    def __init__(self, samples: int = 10000, seed: int = 0, sigma: float = 3.0,
                 parent=None, methods: tuple[str, ...] = ("worst_case", "rss")):
        super().__init__("Analysis settings", parent)
        self.worst_case = QCheckBox("Exact possible envelope")
        self.worst_case.setChecked("worst_case" in methods)
        self.rss = QCheckBox("Affine RSS when applicable")
        self.rss.setChecked("rss" in methods)
        self.monte_carlo = QCheckBox("Monte Carlo")
        self.monte_carlo.setChecked("monte_carlo" in methods)
        self.samples = QSpinBox()
        self.samples.setRange(100, 1_000_000)
        self.samples.setValue(samples)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(seed)
        self.sigma = number(sigma, low=0.01)
        self.form.addRow("Worst case", self.worst_case)
        self.form.addRow("Statistical", self.rss)
        self.form.addRow("Simulation", self.monte_carlo)
        self.form.addRow("Samples", self.samples)
        self.form.addRow("Random seed", self.seed)
        self.form.addRow("Reported σ level", self.sigma)

    def methods(self) -> tuple[str, ...]:
        return tuple(name for name, checked in (
            ("worst_case", self.worst_case.isChecked()),
            ("rss", self.rss.isChecked()),
            ("monte_carlo", self.monte_carlo.isChecked()),
        ) if checked)

    def accept(self) -> None:
        if not self.methods():
            return self.reject_input("Choose at least one analysis method.")
        super().accept()
