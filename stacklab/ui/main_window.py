"""StackLab 1D application window and model command wiring."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import sys
from typing import Callable

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QKeySequence, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stacklab.domain import (
    AnalysisCase, AssemblyConstraint, AssemblyPolicy, ContactPair, Correlation, Dimension, Face, FaceRef,
    FunctionalRequirement, Outline, PartDefinition, PartInstance, SketchDimension, SketchVertex, Tolerance,
    VariationSource, new_id,
)
from stacklab.services import ProjectService, StaleAnalysis
from stacklab.sketch_dimensions import (GeometryPick, measure_sketch_dimension,
                                        parallel_line_spacing, segment_points,
                                        set_sketch_dimension_value, vertex_point)

from .dialogs import (
    AnalysisSettingsDialog, ConstraintDialog, ContactDialog, CorrelationDialog, DimensionDialog,
    FaceDialog, InstanceDialog, OutlineDialog, PartDialog, PolicyDialog, RequirementDialog,
    SketchDimensionDialog, SourceDialog,
)
from .sketch import SCALE, TRACK, SketchView


class _AnalysisBridge(QObject):
    progress = Signal(float)
    completed = Signal(str, object)
    failed = Signal(str)


class MainWindow(QMainWindow):
    """Main workspace. All engineering edits pass through ProjectService."""

    def __init__(self, service: ProjectService, parent=None):
        super().__init__(parent)
        self.service = service
        self._results: dict[str, object] = {}
        self._selected: tuple[str, str] | None = None
        self._tool: str | None = None
        self._picked_faces: list[FaceRef] = []
        self._picked_geometry: list[GeometryPick | FaceRef] = []
        self._busy = False
        self._future = None
        self._fit_on_show = True
        self._pending_new_part_name: str | None = None
        self._point_instance_id: str | None = None
        self._settings = {"samples": 10000, "seed": 0, "sigma_level": 3.0}
        self._bridge = _AnalysisBridge(self)
        self._bridge.progress.connect(self._analysis_progress)
        self._bridge.completed.connect(self._analysis_complete)
        self._bridge.failed.connect(self._analysis_failed)
        self._unsubscribe = self.service.subscribe(self._on_service_event)
        self.resize(1500, 900)
        self.setMinimumSize(920, 600)
        self._create_actions()
        self._create_workspace()
        self._create_toolbar()
        self._create_status()
        self._refresh()

    def _action(self, text: str, callback: Callable, shortcut=None, tip: str = "", checkable=False) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        action.setCheckable(checkable)
        return action

    def _create_actions(self) -> None:
        self.new_action = self._action("New", self.new_project, QKeySequence.New, "Create a new assembly")
        self.open_action = self._action("Open", self.open_project, QKeySequence.Open, "Open a .stack1d project")
        self.example_action = self._action("Open Stepped Assembly Example", self.open_stepped_example,
                                            tip="Open the editable pin, U-shaped cradle, guard and datum example")
        self.save_action = self._action("Save", self.save_project, QKeySequence.Save, "Save the complete model")
        self.save_as_action = self._action("Save As…", self.save_as_project, QKeySequence.SaveAs)
        self.undo_action = self._action("Undo", self.undo, QKeySequence.Undo)
        self.redo_action = self._action("Redo", self.redo, QKeySequence.Redo)
        self.part_action = self._action("Create Part", self.create_part, tip="Sketch a new axial profile")
        self.draw_part_action = self._action("Draw Part", self.draw_new_part,
                                             tip="Click polygon vertices, then Enter or right-click to finish")
        self.draw_outline_action = self._action("Add Outline", lambda: self.draw_outline(True),
                                                tip="Draw another closed contour on a selected part")
        self.draw_path_action = self._action("Add Polyline", lambda: self.draw_outline(False),
                                             tip="Draw an open profile on a selected part")
        self.place_point_action = self._action("Place Point", self.place_point,
                                               tip="Click a feature to create a dimensionable point")
        self.face_action = self._action("Add Face", self.add_face, tip="Add an axial feature to a part")
        self.centerline_action = self._action("Centerline", self.add_centerline,
                                              tip="Add a fixed axial reference line for point-to-line dimensions")
        self.dimension_action = self._action("Dimension", lambda: self._set_tool(
            None if self._tool == "dimension" else "dimension"),
            tip="Click one line for length, two vertices for distance, or two parallel lines for spacing", checkable=True)
        self.constraint_action = self._action("Constraint", self.add_constraint, tip="Define grounding, alignment or bounded movement")
        self.contact_action = self._action("Contact", lambda: self._set_tool("contact"), tip="Select two compatible faces as a candidate contact", checkable=True)
        self.policy_action = self._action("Position Policy", self.add_policy, tip="Define how floating parts are positioned")
        self.correlation_action = self._action("Correlate", self.add_correlation,
                                               tip="Set correlation between two manufacturing sources")
        self.gap_action = self._action("Measure Gap", lambda: self._set_tool("gap"), tip="Select two faces for a functional requirement", checkable=True)
        self.analyze_action = self._action("Analyze", self.analyze_selected, "F5", "Run the selected gap analysis")
        self.cancel_action = self._action("Cancel Analysis", self.cancel_analysis, "Escape")
        self.export_action = self._action("Export Report", self.export_report, tip="Export the selected analysis")
        self.delete_action = self._action("Delete", self.delete_selected, QKeySequence.Delete)
        self.fit_action = self._action("Fit View", self._fit_view, "Ctrl+0")
        self.grid_action = self._action("Grid", self._toggle_grid, checkable=True)
        self.grid_action.setChecked(True)

        file_menu = self.menuBar().addMenu("&File")
        for action in (self.new_action, self.open_action, self.example_action,
                       self.save_action, self.save_as_action, self.export_action):
            file_menu.addAction(action)
        edit_menu = self.menuBar().addMenu("&Edit")
        for action in (self.undo_action, self.redo_action, self.delete_action):
            edit_menu.addAction(action)
        sketch_menu = self.menuBar().addMenu("&Sketch")
        for action in (self.part_action, self.draw_part_action, self.draw_outline_action,
                       self.draw_path_action, self.place_point_action, self.face_action,
                       self.centerline_action, self.dimension_action, self.constraint_action,
                       self.contact_action, self.policy_action, self.gap_action):
            sketch_menu.addAction(action)
        analysis_menu = self.menuBar().addMenu("&Analysis")
        analysis_menu.addAction(self.analyze_action)
        analysis_menu.addAction(self.cancel_action)
        analysis_menu.addAction(self.correlation_action)
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.fit_action)
        view_menu.addAction(self.grid_action)

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Model tools", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(toolbar)
        for action in (self.new_action, self.open_action, self.save_action):
            toolbar.addAction(action)
        toolbar.addSeparator()
        for action in (self.undo_action, self.redo_action):
            toolbar.addAction(action)
        toolbar.addSeparator()
        for action in (self.part_action, self.draw_part_action, self.draw_outline_action,
                       self.place_point_action, self.face_action, self.centerline_action, self.dimension_action,
                       self.constraint_action, self.contact_action, self.policy_action, self.gap_action):
            toolbar.addAction(action)
        toolbar.addSeparator()
        toolbar.addAction(self.analyze_action)
        toolbar.addAction(self.correlation_action)
        toolbar.addAction(self.export_action)

    def _create_workspace(self) -> None:
        splitter = QSplitter(Qt.Horizontal, self)
        self.setCentralWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 5, 8)
        title = QLabel("ASSEMBLY")
        title.setObjectName("sectionTitle")
        left_layout.addWidget(title)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        left_layout.addWidget(self.tree)
        splitter.addWidget(left)

        self.sketch = SketchView()
        self.sketch.face_clicked.connect(self._face_clicked)
        self.sketch.entity_clicked.connect(self._scene_selected)
        self.sketch.blank_clicked.connect(self._clear_selection)
        self.sketch.part_dragged.connect(self._part_dragged)
        self.sketch.edit_requested.connect(self._edit_entity)
        self.sketch.context_requested.connect(self._context_menu)
        self.sketch.outline_finished.connect(self._outline_finished)
        self.sketch.outline_cancelled.connect(self._sketch_cancelled)
        self.sketch.point_clicked.connect(self._point_clicked)
        self.sketch.geometry_clicked.connect(self._geometry_clicked)
        self.sketch.tool_cancelled.connect(lambda: self._set_tool(None))
        splitter.addWidget(self.sketch)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(7, 8, 8, 8)
        title = QLabel("PROPERTIES")
        title.setObjectName("sectionTitle")
        right_layout.addWidget(title)
        self.property_content = QWidget()
        self.property_form = QFormLayout(self.property_content)
        self.property_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        right_layout.addWidget(self.property_content)
        self.edit_button = QPushButton("Edit selected…")
        self.edit_button.clicked.connect(self.edit_selected)
        right_layout.addWidget(self.edit_button)
        self.delete_button = QPushButton("Delete selected")
        self.delete_button.clicked.connect(self.delete_selected)
        right_layout.addWidget(self.delete_button)
        right_layout.addStretch(1)
        splitter.addWidget(right)
        splitter.setSizes([250, 900, 275])

        self.bottom = QDockWidget("Engineering analysis", self)
        self.bottom.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea)
        tabs = QTabWidget()
        self.messages = self._readout(tabs, "Messages")
        self.constraint_status = self._readout(tabs, "Constraint status")
        self.chain_view = self._readout(tabs, "Dimensional chain")
        self.worst_view = self._readout(tabs, "Worst case")
        self.rss_view = self._readout(tabs, "RSS")
        mc_tab = QWidget()
        mc_layout = QVBoxLayout(mc_tab)
        self.mc_view = QTextEdit()
        self.mc_view.setReadOnly(True)
        self.mc_view.setPlaceholderText("Results appear after analysis.")
        self.histogram = QGraphicsView()
        self.histogram.setScene(QGraphicsScene(self.histogram))
        self.histogram.setMinimumHeight(100)
        mc_layout.addWidget(self.mc_view, 2)
        mc_layout.addWidget(self.histogram, 1)
        tabs.addTab(mc_tab, "Monte Carlo")
        contrib_tab = QWidget()
        contrib_layout = QVBoxLayout(contrib_tab)
        self.contributors = QTreeWidget()
        self.contributors.setColumnCount(4)
        self.contributors.setHeaderLabels(["Parameter", "Sensitivity", "Worst span", "Variance share"])
        self.contributor_chart = QGraphicsView()
        self.contributor_chart.setScene(QGraphicsScene(self.contributor_chart))
        self.contributor_chart.setMinimumHeight(100)
        contrib_layout.addWidget(self.contributors, 2)
        contrib_layout.addWidget(self.contributor_chart, 1)
        tabs.addTab(contrib_tab, "Contributors")
        self.bottom.setWidget(tabs)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.bottom)
        self.resizeDocks([self.bottom], [245], Qt.Vertical)

    @staticmethod
    def _readout(tabs: QTabWidget, name: str) -> QTextEdit:
        widget = QTextEdit()
        widget.setReadOnly(True)
        widget.setPlaceholderText("Results appear after analysis.")
        tabs.addTab(widget, name)
        return widget

    def _create_status(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self.status_label = QLabel("Ready")
        bar.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedWidth(140)
        self.progress.hide()
        bar.addPermanentWidget(self.progress)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancel_analysis)
        cancel.hide()
        bar.addPermanentWidget(cancel)
        self.cancel_button = cancel
        self._style()

    def _style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f6f8fb; color: #1e3249; font: 10pt 'Segoe UI'; }
            QTreeWidget, QTextEdit, QGraphicsView { background: #fff; border: 1px solid #d9e1ea; border-radius: 4px; }
            QTreeWidget::item { padding: 3px; }
            QTreeWidget::item:selected { background: #d9eafb; color: #174571; }
            QToolBar { background: #fff; border-bottom: 1px solid #d9e1ea; spacing: 3px; padding: 4px; }
            QToolButton { padding: 6px 8px; border-radius: 4px; }
            QToolButton:hover, QPushButton:hover { background: #e8f2fb; }
            QToolButton:checked { background: #cfe7fb; }
            QPushButton { background: #fff; border: 1px solid #cbd7e4; border-radius: 4px; padding: 6px; }
            #sectionTitle { color: #627991; font-size: 9pt; font-weight: 700; letter-spacing: 1px; }
            QTabWidget::pane { border: 1px solid #d9e1ea; }
            QTabBar::tab { background: #edf2f7; padding: 6px 12px; }
            QTabBar::tab:selected { background: #fff; color: #176399; }
        """)

    def _on_service_event(self, event: str, revision: int) -> None:
        if event in {"edit", "new", "open", "undo", "redo"}:
            self._results.clear()
            self._clear_results()
            if self._busy:
                self.status_label.setText("Model changed; previous calculation cancelled")
        if event in {"new", "open"}:
            self._fit_on_show = True
            self.sketch._manually_navigated = False
        self._refresh()
        if event in {"new", "open"} and self.isVisible():
            QTimer.singleShot(0, self._fit_view)

    def _refresh(self) -> None:
        project = self.service.project
        path = self.service.path
        dirty = " *" if self.service.dirty else ""
        self.setWindowTitle(f"{project.name}{dirty} — StackLab 1D" + (f"  [{path.name}]" if path else ""))
        self.undo_action.setEnabled(self.service.can_undo)
        self.redo_action.setEnabled(self.service.can_redo)
        self._build_tree()
        positions = None
        if hasattr(self.service, "nominal_geometry"):
            try:
                positions = self.service.nominal_geometry()
            except ValueError as exc:
                self.status_label.setText(str(exc))
        self.sketch.set_project(project, positions, self.service.presentation)
        self._show_properties()

    def _build_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        project = self.service.project
        root = self._item(self.tree, project.name, "project", project.id)
        definitions = self._item(root, "Part definitions", "group", "definitions")
        for definition in project.definitions:
            item = self._item(definitions, definition.name, "definition", definition.id)
            for face in definition.faces:
                self._item(item, f"{face.name}  x={face.local_x:g} {project.unit}", "definition_face", f"{definition.id}:{face.id}")
            for outline in definition.outlines:
                self._item(item, f"{outline.name}  ({len(outline.vertices)} vertices)",
                           "outline", f"{definition.id}:{outline.id}")
        instances = self._item(root, "Part instances", "group", "instances")
        for instance in project.instances:
            marker = "◌ " if not instance.visible else ""
            item = self._item(instances, marker + instance.name, "instance", instance.id)
            definition = next((d for d in project.definitions if d.id == instance.definition_id), None)
            if definition:
                for face in definition.faces:
                    self._item(item, face.name, "face", f"{instance.id}:{face.id}")
        for label, kind, sequence in (
            ("Dimensions", "dimension", project.dimensions),
            ("Sketch dimensions", "sketch_dimension", project.sketch_dimensions),
            ("Manufacturing sources", "source", project.sources),
            ("Source correlations", "correlation", project.correlations),
            ("Constraints", "constraint", project.constraints),
            ("Contacts", "contact", project.contacts),
            ("Position policies", "policy", project.policies),
            ("Functional requirements", "requirement", project.requirements),
        ):
            group = self._item(root, label, "group", kind)
            for entity in sequence:
                name = getattr(entity, "name", "") or (
                    f"{entity.first_source_id} ↔ {entity.second_source_id}  ρ={entity.rho:g}"
                    if kind == "correlation" else entity.id)
                self._item(group, name, kind, entity.id)
        root.setExpanded(True)
        instances.setExpanded(True)
        for child_index in range(root.childCount()):
            root.child(child_index).setExpanded(True)
        if self._selected:
            kind, identifier = self._selected
            self._select_tree(kind, ":".join(identifier.split(":")[-2:])
                              if kind == "outline" else identifier)
        self.tree.blockSignals(False)

    @staticmethod
    def _item(parent, text: str, kind: str, identifier: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text])
        item.setData(0, Qt.UserRole, (kind, identifier))
        item.setToolTip(0, identifier)
        return item

    def _select_tree(self, kind: str, identifier: str) -> None:
        def walk(parent):
            for index in range(parent.childCount()):
                item = parent.child(index)
                if item.data(0, Qt.UserRole) == (kind, identifier):
                    return item
                match = walk(item)
                if match:
                    return match
            return None
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            found = root if root.data(0, Qt.UserRole) == (kind, identifier) else walk(root)
            if found:
                old_block = self.tree.blockSignals(True)
                try:
                    self.tree.setCurrentItem(found)
                finally:
                    self.tree.blockSignals(old_block)
                break

    def _tree_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        data = item.data(0, Qt.UserRole)
        if data and data[0] != "group":
            self._select_entity(*data, update_tree=False)

    def _scene_selected(self, kind: str, identifier: str) -> None:
        self._select_entity(kind, identifier)

    def _select_entity(self, kind: str, identifier: str, *, update_tree=True) -> None:
        self._selected = (kind, identifier)
        if update_tree:
            tree_identifier = ":".join(identifier.split(":")[-2:]) if kind == "outline" else identifier
            self._select_tree(kind, tree_identifier)
        faces = {(face.instance_id, face.face_id) for face in self._picked_faces}
        self.sketch.set_highlights(self._selected, faces, self.sketch._chain_dimensions)
        self._show_properties()

    def _clear_selection(self) -> None:
        self._selected = None
        self.tree.clearSelection()
        self.sketch.set_highlights(None, {(f.instance_id, f.face_id) for f in self._picked_faces}, self.sketch._chain_dimensions)
        self._show_properties()

    def _show_properties(self) -> None:
        form = self.property_form
        while form.rowCount():
            form.removeRow(0)
        selection = self._selected
        if not selection:
            form.addRow(QLabel("Select an entity in the tree or sketch."))
            self.edit_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            return
        kind, identifier = selection
        entity = self._entity(kind, identifier)
        self.edit_button.setEnabled(kind not in {"group", "project"})
        self.delete_button.setEnabled(kind not in {"group", "project"})
        if entity is None:
            form.addRow(QLabel("The selected entity is no longer in the model."))
            return
        form.addRow("Type", QLabel(kind.replace("_", " ").title()))
        for field in fields(entity):
            value = getattr(entity, field.name)
            if isinstance(value, FaceRef):
                display = self._face_label(value)
            elif isinstance(value, Tolerance):
                display = f"{value.lower:+g} / {value.upper:+g}"
            elif isinstance(value, list):
                display = ", ".join(str(item) for item in value)
            else:
                display = str(value)
            label = QLabel(display)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(field.name.replace("_", " ").title(), label)

    def _entity(self, kind: str, identifier: str):
        project = self.service.project
        if kind == "project":
            return project
        if kind in {"face", "definition_face"}:
            owner_id, face_id = identifier.split(":", 1)
            if kind == "face":
                instance = next((i for i in project.instances if i.id == owner_id), None)
                owner_id = instance.definition_id if instance else ""
            definition = next((d for d in project.definitions if d.id == owner_id), None)
            return next((face for face in definition.faces if face.id == face_id), None) if definition else None
        if kind == "outline":
            owner_id, outline_id = identifier.split(":")[-2:]
            definition = next((d for d in project.definitions if d.id == owner_id), None)
            return next((item for item in definition.outlines if item.id == outline_id), None) if definition else None
        mapping = {"definition": project.definitions, "instance": project.instances,
                   "dimension": project.dimensions, "sketch_dimension": project.sketch_dimensions,
                   "constraint": project.constraints,
                   "contact": project.contacts, "policy": project.policies,
                   "requirement": project.requirements, "source": project.sources,
                   "correlation": project.correlations}
        return next((entity for entity in mapping.get(kind, []) if entity.id == identifier), None)

    def _face_label(self, ref: FaceRef) -> str:
        project = self.service.project
        instance = next((i for i in project.instances if i.id == ref.instance_id), None)
        if not instance:
            return f"{ref.instance_id}:{ref.face_id}"
        definition = next((d for d in project.definitions if d.id == instance.definition_id), None)
        face = next((f for f in definition.faces if f.id == ref.face_id), None) if definition else None
        return f"{instance.name} / {face.name if face else ref.face_id}"

    def _command(self, mutator: Callable, description: str) -> bool:
        try:
            self.service.execute(mutator, description)
            self.status_label.setText(description)
            return True
        except Exception as exc:
            self._error(description, exc)
            return False

    def _error(self, action: str, exc: Exception) -> None:
        message = str(exc) or exc.__class__.__name__
        self.messages.append(f"{action}: {message}")
        QMessageBox.warning(self, action, message)

    def _maybe_save(self) -> bool:
        if not self.service.dirty:
            return True
        choice = QMessageBox.question(self, "Unsaved changes", "Save changes to this assembly?",
                                      QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                                      QMessageBox.Save)
        if choice == QMessageBox.Cancel:
            return False
        if choice == QMessageBox.Save:
            return self.save_project()
        return True

    def new_project(self) -> None:
        if not self._maybe_save():
            return
        name, ok = QInputDialog.getText(self, "New assembly", "Project name:", text="Untitled assembly")
        if ok and name.strip():
            self.service.new_project(name.strip())
            self._selected = None
            self._set_tool(None)

    def open_project(self) -> None:
        if not self._maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open StackLab project", "", "StackLab projects (*.stack1d)")
        if path:
            try:
                self.service.open(path)
                self._selected = None
                self._set_tool(None)
                self.status_label.setText(f"Opened {Path(path).name}")
            except Exception as exc:
                self._error("Open project", exc)

    def open_stepped_example(self) -> None:
        if not self._maybe_save():
            return
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        path = root / "examples" / "f-stepped-pin-reference.stack1d"
        try:
            self.service.open(path)
            self._selected = None
            self._set_tool(None)
            self.sketch.fit_assembly()
            self.status_label.setText("Opened editable stepped assembly example")
        except Exception as exc:
            self._error("Open stepped example", exc)

    def save_project(self) -> bool:
        if self.service.path is None:
            return self.save_as_project()
        try:
            self.service.save()
            self.status_label.setText("Project saved")
            return True
        except Exception as exc:
            self._error("Save project", exc)
            return False

    def save_as_project(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Save StackLab project", "", "StackLab projects (*.stack1d)")
        if not path:
            return False
        if not path.lower().endswith(".stack1d"):
            path += ".stack1d"
        try:
            self.service.save(path)
            self.status_label.setText(f"Saved {Path(path).name}")
            return True
        except Exception as exc:
            self._error("Save project", exc)
            return False

    def undo(self) -> None:
        self.service.undo()

    def redo(self) -> None:
        self.service.redo()

    def create_part(self) -> None:
        dialog = PartDialog(self.service.project, self)
        if dialog.exec() != QDialog.Accepted:
            return
        name = dialog.name.text().strip()
        positions = dialog.face_positions()
        lane = dialog.lane.text().strip() or "default"
        origin = dialog.origin.value()
        definition_id = new_id("part")
        instance_id = new_id("instance")
        face_ids = [new_id("face") for _ in positions]
        dimension_ids = [new_id("dimension") for _ in positions[1:]]
        def change(project):
            faces = [Face(face_ids[i], "Left" if i == 0 else "Right" if i == len(positions)-1 else f"Face {i+1}", x, lane)
                     for i, x in enumerate(positions)]
            project.definitions.append(PartDefinition(definition_id, name, faces))
            project.instances.append(PartInstance(instance_id, name, definition_id, origin))
            for index in range(len(positions)-1):
                project.dimensions.append(Dimension(
                    dimension_ids[index], f"{name} segment {index+1}",
                    FaceRef(instance_id, face_ids[index]), FaceRef(instance_id, face_ids[index+1]),
                    positions[index+1]-positions[index], Tolerance(0, 0)))
        if self._command(change, f"Created part {name}"):
            self._select_entity("instance", instance_id)
            self.sketch.fit_assembly()

    def draw_new_part(self) -> None:
        name, ok = QInputDialog.getText(self, "Draw part", "Part name:",
                                        text=f"Sketched part {len(self.service.project.instances) + 1}")
        if not ok or not name.strip():
            return
        self._set_tool(None)
        self._pending_new_part_name = name.strip()
        self.sketch.begin_outline(None, closed=True)
        self.status_label.setText("Draw a closed part: click vertices; Enter, double-click or right-click to finish. Esc cancels.")

    def _choose_sketch_instance(self, title: str) -> PartInstance | None:
        instance = self._selected_instance()
        if instance and instance.visible:
            return instance
        instances = [item for item in self.service.project.instances if item.visible and
                     any(d.id == item.definition_id and d.faces for d in self.service.project.definitions)]
        if not instances:
            QMessageBox.information(self, title, "Create a visible part first.")
            return None
        choices = [f"{item.name} ({item.id})" for item in instances]
        choice, ok = QInputDialog.getItem(self, title, "Part:", choices, 0, False)
        return instances[choices.index(choice)] if ok else None

    def draw_outline(self, closed: bool = True) -> None:
        instance = self._choose_sketch_instance("Sketch outline")
        if instance is None:
            return
        self._set_tool(None)
        self.sketch.begin_outline(instance.id, closed=closed)
        self.status_label.setText("Click outline vertices; Enter, double-click or right-click to finish. Backspace removes a point; Esc cancels.")

    def _outline_finished(self, instance_id: str | None, points: list[QPointF], closed: bool) -> None:
        if instance_id is None:
            name = self._pending_new_part_name or "Sketched part"
            self._pending_new_part_name = None
            xs = [point.x() / SCALE for point in points]
            left_x, right_x = min(xs), max(xs)
            if right_x - left_x < 1e-6:
                QMessageBox.warning(self, "Draw part", "The part needs a nonzero axial width.")
                return
            row_y = 70 + sum(item.visible for item in self.service.project.instances) * TRACK
            vertices = [SketchVertex(point.x() / SCALE - left_x, (point.y() - row_y) / SCALE)
                        for point in points]
            definition_id, new_instance_id = new_id("part"), new_id("instance")
            left_id, right_id, dimension_id = new_id("face"), new_id("face"), new_id("dimension")
            left_vertex = next(vertex for vertex in vertices if abs(vertex.x) < 1e-9)
            right_vertex = next(vertex for vertex in vertices if abs(vertex.x - (right_x - left_x)) < 1e-9)
            for vertex in vertices:
                if abs(vertex.x) < 1e-9:
                    vertex.face_id = left_id
                elif abs(vertex.x - (right_x - left_x)) < 1e-9:
                    vertex.face_id = right_id
            outline = Outline(new_id("outline"), "Main outline", vertices, closed)
            def change(project):
                faces = [Face(left_id, "Left feature", 0, local_y=left_vertex.y),
                         Face(right_id, "Right feature", right_x - left_x, local_y=right_vertex.y)]
                project.definitions.append(PartDefinition(definition_id, name, faces, [outline]))
                project.instances.append(PartInstance(new_instance_id, name, definition_id, left_x))
                project.dimensions.append(Dimension(dimension_id, f"{name} width",
                    FaceRef(new_instance_id, left_id), FaceRef(new_instance_id, right_id),
                    right_x - left_x, Tolerance(0, 0)))
            if self._command(change, f"Sketched {name}"):
                self._select_entity("instance", new_instance_id)
                self.sketch.fit_assembly()
            return

        instance = next((item for item in self.service.project.instances if item.id == instance_id), None)
        if instance is None:
            return
        definition = next(d for d in self.service.project.definitions if d.id == instance.definition_id)
        row_y = self.sketch._part_rows.get(instance_id, 70.0)
        translation = self.sketch._translation(instance, definition)
        vertices = []
        for point in points:
            global_x = point.x() / SCALE
            face = min(definition.faces,
                       key=lambda item: abs(self.sketch._x(instance, item) / SCALE - global_x),
                       default=None)
            bound = face if face and abs(self.sketch._x(instance, face) / SCALE - global_x) <= 1.0 else None
            vertices.append(SketchVertex(face.local_x if bound else global_x - translation,
                                         (point.y() - row_y) / SCALE,
                                         bound.id if bound else None))
        outline_id = new_id("outline")
        colors = ("#b95755", "#6d57b3", "#d5a800", "#38878a", "#547aaf")
        outline = Outline(outline_id, f"Outline {len(definition.outlines) + 1}", vertices,
                          closed, colors[len(definition.outlines) % len(colors)])
        def change(project):
            next(item for item in project.definitions if item.id == definition.id).outlines.append(outline)
        if self._command(change, "Added sketched outline"):
            self._select_entity("outline", f"{instance.id}:{definition.id}:{outline_id}")

    def _sketch_cancelled(self) -> None:
        self._pending_new_part_name = None
        self._point_instance_id = None
        self.status_label.setText("Sketch cancelled")

    def place_point(self) -> None:
        instance = self._choose_sketch_instance("Place measurement point")
        if instance is None:
            return
        self._set_tool(None)
        self._point_instance_id = instance.id
        self.sketch.begin_pick_point()
        self.status_label.setText("Click the feature point. A basic dimension will tie its X to the part.")

    def _point_clicked(self, point: QPointF) -> None:
        instance_id = self._point_instance_id
        self._point_instance_id = None
        instance = next((item for item in self.service.project.instances if item.id == instance_id), None)
        if instance is None:
            return
        definition = next(d for d in self.service.project.definitions if d.id == instance.definition_id)
        row_y = self.sketch._part_rows.get(instance.id, 70.0)
        translation = self.sketch._translation(instance, definition)
        # A nearby sketched vertex is a more reliable target than the raw click.
        candidates = [(outline.id, index,
                       QPointF(self.sketch._vertex_x(instance, definition, vertex),
                               row_y + vertex.y * SCALE))
                      for outline in definition.outlines
                      for index, vertex in enumerate(outline.vertices)]
        snapped_vertex = None
        if candidates:
            nearest = min(candidates, key=lambda item: (item[2].x()-point.x())**2 +
                          (item[2].y()-point.y())**2)
            if (nearest[2].x()-point.x())**2 + (nearest[2].y()-point.y())**2 <= 14**2:
                snapped_vertex = (nearest[0], nearest[1])
                point = nearest[2]
                existing_outline = next(item for item in definition.outlines if item.id == nearest[0])
                existing_face_id = existing_outline.vertices[nearest[1]].face_id
                if existing_face_id:
                    self._select_entity("face", f"{instance.id}:{existing_face_id}")
                    self.status_label.setText("Selected existing feature point")
                    return
        x = point.x() / SCALE - translation
        y = (point.y() - row_y) / SCALE
        name, ok = QInputDialog.getText(self, "Feature point", "Point name:",
                                        text=f"Point {len(definition.faces) + 1}")
        if not ok or not name.strip():
            return
        anchor = min(definition.faces, key=lambda face: abs(face.local_x - x))
        face_id, dimension_id = new_id("face"), new_id("dimension")
        def change(project):
            owner = next(d for d in project.definitions if d.id == definition.id)
            owner.faces.append(Face(face_id, name.strip(), x, local_y=y))
            if snapped_vertex is not None:
                outline = next(item for item in owner.outlines if item.id == snapped_vertex[0])
                outline.vertices[snapped_vertex[1]].face_id = face_id
                outline.vertices[snapped_vertex[1]].x = x
            project.dimensions.append(Dimension(dimension_id, f"{name.strip()} location",
                FaceRef(instance.id, anchor.id), FaceRef(instance.id, face_id),
                x - anchor.local_x, kind="basic"))
        if self._command(change, f"Placed point {name.strip()}"):
            self._select_entity("face", f"{instance.id}:{face_id}")

    def _selected_instance(self) -> PartInstance | None:
        if not self._selected:
            return None
        kind, identifier = self._selected
        if kind == "instance":
            return self._entity(kind, identifier)
        if kind == "face":
            instance_id = identifier.split(":", 1)[0]
            return self._entity("instance", instance_id)
        if kind == "outline" and len(identifier.split(":")) == 3:
            return self._entity("instance", identifier.split(":", 1)[0])
        if kind in {"definition", "outline", "definition_face"}:
            definition_id = identifier.split(":", 1)[0]
            return next((instance for instance in self.service.project.instances
                         if instance.definition_id == definition_id), None)
        return None

    def add_face(self) -> None:
        project = self.service.project
        instance = self._selected_instance()
        if instance is None and project.instances:
            names = [i.name for i in project.instances]
            name, ok = QInputDialog.getItem(self, "Choose part", "Add face to:", names, 0, False)
            if not ok:
                return
            instance = project.instances[names.index(name)]
        if instance is None:
            QMessageBox.information(self, "Add face", "Create a part first.")
            return
        definition = next(d for d in project.definitions if d.id == instance.definition_id)
        last = max((f.local_x for f in definition.faces), default=0)
        dialog = FaceDialog(f"Face {len(definition.faces)+1}", last + 10, "default", self)
        if dialog.exec() != QDialog.Accepted:
            return
        face_id = new_id("face")
        definition_id = definition.id
        name, x, y, lane = (dialog.name.text().strip(), dialog.x.value(),
                            dialog.y.value(), dialog.lane.text().strip())
        # Explicit dimensions are added only through the Dimension tool, so an
        # arbitrary new face remains visibly underconstrained until dimensioned.
        def change(project):
            owner = next(d for d in project.definitions if d.id == definition_id)
            owner.faces.append(Face(face_id, name, x, lane, y))
        if self._command(change, f"Added face {name}"):
            self._select_entity("face", f"{instance.id}:{face_id}")

    def add_centerline(self) -> None:
        name, ok = QInputDialog.getText(self, "Axial centerline", "Reference name:", text="Centerline")
        if not ok or not name.strip():
            return
        x, ok = QInputDialog.getDouble(self, "Axial centerline", "Axial coordinate:", 0.0,
                                       -1_000_000, 1_000_000, 4)
        if not ok:
            return
        definition_id, instance_id, face_id = new_id("datum"), new_id("instance"), new_id("face")
        constraint_id = new_id("constraint")
        def change(project):
            project.definitions.append(PartDefinition(definition_id, name.strip(),
                                                      [Face(face_id, name.strip(), 0, "centerline")]))
            project.instances.append(PartInstance(instance_id, name.strip(), definition_id, x))
            project.constraints.append(AssemblyConstraint(constraint_id, f"Fix {name.strip()}",
                                                           "fixed_face", first=FaceRef(instance_id, face_id), value=x))
        if self._command(change, f"Added centerline {name.strip()}"):
            self._select_entity("face", f"{instance_id}:{face_id}")

    def _set_tool(self, tool: str | None) -> None:
        self.sketch.cancel_outline()
        self.sketch.cancel_pick_point()
        self._tool = tool
        self._picked_faces.clear()
        self._picked_geometry.clear()
        self.sketch.set_dimension_mode(tool == "dimension")
        for name, action in (("dimension", self.dimension_action), ("contact", self.contact_action), ("gap", self.gap_action)):
            action.setChecked(name == tool)
        if tool == "dimension":
            self.status_label.setText("Dimension: click a line, two vertices, two parallel lines, or a vertex and centerline")
        elif tool:
            self.status_label.setText(f"{tool.title()}: select the first face, then the second face")
        else:
            self.status_label.setText("Ready")
        self.sketch.set_highlights(self._selected, set(), self.sketch._chain_dimensions)

    def _face_clicked(self, reference: FaceRef) -> None:
        if self._tool is None:
            return
        if self._tool == "dimension":
            self._dimension_selection(reference)
            return
        if reference in self._picked_faces:
            self.status_label.setText("Choose a different second face")
            return
        self._picked_faces.append(reference)
        self.sketch.set_highlights(self._selected, {(f.instance_id, f.face_id) for f in self._picked_faces}, self.sketch._chain_dimensions)
        if len(self._picked_faces) < 2:
            self.status_label.setText("Select the second face")
            return
        first, second = self._picked_faces
        tool = self._tool
        self._set_tool(None)
        if tool == "dimension":
            self._create_dimension(first, second)
        elif tool == "contact":
            self._create_contact(first, second)
        elif tool == "gap":
            self._create_requirement(first, second)

    def _geometry_clicked(self, pick: GeometryPick) -> None:
        if self._tool == "dimension":
            self._dimension_selection(pick)

    def _dimension_selection(self, pick: GeometryPick | FaceRef) -> None:
        if not self._picked_geometry:
            if isinstance(pick, GeometryPick) and pick.kind == "segment":
                choice, ok = QInputDialog.getItem(
                    self, "Dimension line", "What should this line dimension measure?",
                    ["Length of this line", "Spacing to another line"], 0, False)
                if not ok:
                    self._set_tool(None)
                    return
                if choice == "Length of this line":
                    self._set_tool(None)
                    self._create_geometry_dimension([pick])
                    return
            self._picked_geometry.append(pick)
            if isinstance(pick, GeometryPick):
                self.sketch.set_dimension_picks([pick])
            self.status_label.setText("Select the second line, vertex, or reference face")
            return
        first = self._picked_geometry[0]
        if pick == first:
            self.status_label.setText("Choose a different second feature")
            return
        if isinstance(first, GeometryPick) and first.kind == "segment":
            compatible = isinstance(pick, GeometryPick) and pick.kind == "segment"
        else:
            compatible = isinstance(pick, FaceRef) or (isinstance(pick, GeometryPick) and pick.kind == "vertex")
        if not compatible:
            self.status_label.setText("Select the same kind of feature for this dimension")
            return
        self._set_tool(None)
        self._create_geometry_dimension([first, pick])

    def _create_geometry_dimension(self, picks: list[GeometryPick | FaceRef]) -> None:
        project = self.service.project
        definitions = {definition.id: definition for definition in project.definitions}
        if len(picks) == 1:
            segment = picks[0]
            definition = definitions[segment.definition_id]
            a, b = segment_points(definition, segment)
            if abs(a[1] - b[1]) < 1e-6 and abs(a[0] - b[0]) > 1e-9:
                outline = next(o for o in definition.outlines if o.id == segment.outline_id)
                second = GeometryPick("vertex", segment.instance_id, segment.definition_id,
                                      segment.outline_id, (segment.index + 1) % len(outline.vertices))
                first = GeometryPick("vertex", segment.instance_id, segment.definition_id,
                                     segment.outline_id, segment.index)
                self._create_axial_geometry_dimension([first, second])
            else:
                self._create_sketch_dimension("line_length", segment)
            return
        first, second = picks
        if isinstance(first, FaceRef) or isinstance(second, FaceRef):
            self._create_axial_geometry_dimension([first, second])
            return
        if first.kind == "vertex":
            a = vertex_point(definitions[first.definition_id], first)
            b = vertex_point(definitions[second.definition_id], second)
            if first.instance_id != second.instance_id or (abs(a[1] - b[1]) < 1e-6 and
                                                             abs(a[0] - b[0]) > 1e-9):
                self._create_axial_geometry_dimension([first, second])
            else:
                self._create_sketch_dimension("point_distance", first, second)
            return
        a = segment_points(definitions[first.definition_id], first)
        b = segment_points(definitions[second.definition_id], second)
        try:
            parallel_line_spacing(a, b)
        except ValueError as exc:
            self._error("Dimension between lines", exc)
            return
        vertical = abs(a[0][0] - a[1][0]) < 1e-6 and abs(b[0][0] - b[1][0]) < 1e-6
        if vertical and abs(a[0][0] - b[0][0]) > 1e-9:
            first_vertex = GeometryPick("vertex", first.instance_id, first.definition_id,
                                        first.outline_id, first.index)
            second_vertex = GeometryPick("vertex", second.instance_id, second.definition_id,
                                         second.outline_id, second.index)
            self._create_axial_geometry_dimension([first_vertex, second_vertex], vertical_lines=[first, second])
        else:
            self._create_sketch_dimension("line_spacing", first, second)

    def _create_sketch_dimension(self, kind: str, first: GeometryPick,
                                 second: GeometryPick | None = None) -> None:
        if second is not None and (first.instance_id != second.instance_id or
                                   first.definition_id != second.definition_id):
            self._error("Sketch dimension", ValueError(
                "2D sketch dimensions need two features on the same part instance"))
            return
        definition = next(d for d in self.service.project.definitions if d.id == first.definition_id)
        identifier = new_id("sketch-dimension")
        measure = SketchDimension(identifier, f"SD{len(self.service.project.sketch_dimensions) + 1}",
                                  first.definition_id, kind, first.outline_id, first.index,
                                  second.outline_id if second else None,
                                  second.index if second else None)
        measure.nominal = measure_sketch_dimension(definition, measure)
        dialog = SketchDimensionDialog(measure.name, measure.nominal, self.service.project.unit,
                                       parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        measure.name = dialog.name.text().strip()
        measure.driving = dialog.driving.isChecked()
        target = dialog.value.value()
        def change(project):
            owner = next(d for d in project.definitions if d.id == measure.definition_id)
            item = SketchDimension(**vars(measure))
            if item.driving:
                set_sketch_dimension_value(owner, item, target)
            else:
                item.nominal = target
            project.sketch_dimensions.append(item)
        if self._command(change, f"Added sketch dimension {measure.name}"):
            self._select_entity("sketch_dimension", identifier)

    def _create_axial_geometry_dimension(self, picks: list[GeometryPick | FaceRef],
                                          vertical_lines: list[GeometryPick] | None = None) -> None:
        staged = self.service.project.copy()
        preparations: list[tuple[str, str, int, Face | None, str]] = []
        instances = {instance.id: instance for instance in staged.instances}
        definitions = {definition.id: definition for definition in staged.definitions}

        def ensure(pick: GeometryPick | FaceRef) -> FaceRef:
            if isinstance(pick, FaceRef):
                return pick
            definition = definitions[pick.definition_id]
            outline = next(item for item in definition.outlines if item.id == pick.outline_id)
            vertex = outline.vertices[pick.index]
            if vertex.face_id is None:
                face = Face(new_id("face"), f"{outline.name} vertex {pick.index + 1}",
                            vertex.x, local_y=vertex.y)
                definition.faces.append(face)
                vertex.face_id = face.id
                preparations.append((definition.id, outline.id, pick.index, face, face.id))
            return FaceRef(pick.instance_id, vertex.face_id)

        refs = [ensure(pick) for pick in picks]
        if vertical_lines:
            for line, ref in zip(vertical_lines, refs):
                definition = definitions[line.definition_id]
                outline = next(item for item in definition.outlines if item.id == line.outline_id)
                end = (line.index + 1) % len(outline.vertices)
                vertex = outline.vertices[end]
                if vertex.face_id is None:
                    vertex.face_id = ref.face_id
                    preparations.append((definition.id, outline.id, end, None, ref.face_id))

        def x(reference: FaceRef) -> float:
            instance = instances[reference.instance_id]
            definition = definitions[instance.definition_id]
            face = next(item for item in definition.faces if item.id == reference.face_id)
            return instance.translation + face.local_x

        if refs[0] == refs[1]:
            self._error("Dimension", ValueError("Choose two different sketch features"))
            return
        if x(refs[0]) > x(refs[1]):
            refs.reverse()
        self._create_dimension(refs[0], refs[1], staged_project=staged,
                               pending_bindings=preparations)

    def _create_dimension(self, first: FaceRef, second: FaceRef, *, staged_project=None,
                          pending_bindings: list[tuple[str, str, int, Face | None, str]] | None = None) -> None:
        existing = next((dimension for dimension in self.service.project.dimensions
                         if (dimension.first, dimension.second) in {(first, second), (second, first)}), None)
        if existing:
            self._select_entity("dimension", existing.id)
            self._edit_dimension(existing)
            return
        model = staged_project or self.service.project
        dialog = DimensionDialog(model, first, second, parent=self)
        first_part = next((item for item in model.instances if item.id == first.instance_id), None)
        second_part = next((item for item in model.instances if item.id == second.instance_id), None)
        first_definition = next((item for item in model.definitions if first_part and item.id == first_part.definition_id), None)
        second_definition = next((item for item in model.definitions if second_part and item.id == second_part.definition_id), None)
        first_face = next((item for item in first_definition.faces if item.id == first.face_id), None) if first_definition else None
        second_face = next((item for item in second_definition.faces if item.id == second.face_id), None) if second_definition else None
        if first_face and second_face and first_part and second_part:
            dialog.nominal.setValue((second_part.translation + second_face.local_x) -
                                    (first_part.translation + first_face.local_x))
        if dialog.exec() != QDialog.Accepted:
            return
        dimension_id = new_id("dimension")
        data = self._dimension_values(dialog)
        source_id = (data["source_id"] or new_id("source")) if data["kind"] == "driving" else None
        def change(project):
            for definition_id, outline_id, index, face, face_id in pending_bindings or []:
                definition = next(item for item in project.definitions if item.id == definition_id)
                if face is not None:
                    definition.faces.append(Face(**vars(face)))
                outline = next(item for item in definition.outlines if item.id == outline_id)
                outline.vertices[index].face_id = face_id
            project.dimensions.append(Dimension(dimension_id, data["name"], data["first"], data["second"],
                                                data["nominal"], Tolerance(data["lower"], data["upper"]),
                                                data["kind"], source_id, data["coefficient"],
                                                display_style=data["display_style"], show_on_sketch=True))
            if source_id and data["source_id"] is None:
                lo, hi = sorted((data["lower"] / data["coefficient"],
                                 data["upper"] / data["coefficient"]))
                project.sources.append(VariationSource(source_id, data["name"], lo, hi,
                    data["distribution"], 0.0,
                    data["std"] / abs(data["coefficient"]) if data["std"] is not None else None,
                    data["sigma_level"]))
            if source_id:
                self._sync_source_dimensions(project, source_id)
        if self._command(change, f"Added dimension {data['name']}"):
            self._select_entity("dimension", dimension_id)

    @staticmethod
    def _dimension_values(dialog: DimensionDialog) -> dict:
        return {"name": dialog.name.text().strip(), "first": dialog.first.currentData(),
                "second": dialog.second.currentData(), "nominal": dialog.nominal.value(),
                "lower": dialog.lower.value(), "upper": dialog.upper.value(),
                "kind": dialog.kind.currentText(), "distribution": dialog.distribution.currentText(),
                "std": dialog.sigma.value() or None, "sigma_level": dialog.sigma_level.value(),
                "display_style": dialog.display.currentData(),
                "source_id": dialog.source_choice.currentData(),
                "coefficient": dialog.coefficient.value()}

    @staticmethod
    def _sync_source_dimensions(project, source_id: str) -> None:
        source = next((item for item in project.sources if item.id == source_id), None)
        if source is None:
            return
        for dimension in project.dimensions:
            if dimension.kind == "driving" and dimension.source_id == source_id:
                deviations = sorted((dimension.coefficient * source.lower,
                                     dimension.coefficient * source.upper))
                dimension.tolerance = Tolerance(*deviations)

    def add_constraint(self) -> None:
        if not self.service.project.instances:
            QMessageBox.information(self, "Constraint", "Create a part first.")
            return
        dialog = ConstraintDialog(self.service.project, parent=self)
        if self._selected and self._selected[0] == "face":
            instance_id, face_id = self._selected[1].split(":", 1)
            index = dialog.first.findData(FaceRef(instance_id, face_id))
            if index >= 0:
                dialog.first.setCurrentIndex(index)
                dialog.kind.setCurrentIndex(dialog.kind.findData("fixed_face"))
        if dialog.exec() != QDialog.Accepted:
            return
        identifier = new_id("constraint")
        def change(project):
            kind = dialog.kind.currentData()
            project.constraints.append(AssemblyConstraint(
                identifier, dialog.name.text().strip(), kind,
                None if kind in {"fixed_position", "bounded_translation"} else dialog.first.currentData(),
                None if kind in {"fixed_position", "bounded_translation", "fixed_face"} else dialog.second.currentData(),
                dialog.instance.currentData() if kind in {"fixed_position", "bounded_translation"} else None,
                dialog.value.value(), dialog.lower.value() if kind in {"bounded_translation", "clearance_joint"} else None,
                dialog.upper.value() if kind in {"bounded_translation", "clearance_joint"} else None))
        if self._command(change, "Added assembly constraint"):
            self._select_entity("constraint", identifier)

    def _create_contact(self, first: FaceRef, second: FaceRef) -> None:
        dialog = ContactDialog(self.service.project, first=first, second=second, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        identifier = new_id("contact")
        def change(project):
            project.contacts.append(ContactPair(identifier, dialog.first.currentData(), dialog.second.currentData(),
                                                dialog.lane.text().strip(), f"Contact {len(project.contacts)+1}"))
        if self._command(change, "Added candidate contact"):
            self._select_entity("contact", identifier)

    def add_policy(self) -> None:
        dialog = PolicyDialog(self.service.project, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        identifier = new_id("policy")
        def change(project):
            project.policies.append(AssemblyPolicy(identifier, dialog.name.text().strip(),
                                                   dialog.kind.currentData(), dialog.selected_contact_ids(),
                                                   dialog.selected_bounds()))
        if self._command(change, "Added positioning policy"):
            self._select_entity("policy", identifier)

    def add_correlation(self) -> None:
        if len(self.service.project.sources) < 2:
            QMessageBox.information(self, "Source correlation", "Create two driving manufacturing sources first.")
            return
        dialog = CorrelationDialog(self.service.project, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        identifier = new_id("correlation")
        def change(project):
            pair = {dialog.first.currentData(), dialog.second.currentData()}
            if any({item.first_source_id, item.second_source_id} == pair
                   for item in project.correlations):
                raise ValueError("These sources already have a correlation; edit the existing entry.")
            project.correlations.append(Correlation(dialog.first.currentData(),
                                                    dialog.second.currentData(), dialog.rho.value(), identifier))
        if self._command(change, "Added source correlation"):
            self._select_entity("correlation", identifier)

    def _create_requirement(self, first: FaceRef, second: FaceRef) -> None:
        dialog = RequirementDialog(self.service.project, first, second, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        identifier = new_id("requirement")
        def change(project):
            project.requirements.append(FunctionalRequirement(
                identifier, dialog.name.text().strip(), dialog.first.currentData(), dialog.second.currentData(),
                dialog.direction.currentData(),
                dialog.minimum.value() if dialog.minimum_enabled.isChecked() else None,
                dialog.maximum.value() if dialog.maximum_enabled.isChecked() else None,
                dialog.policy.currentData()))
        if self._command(change, "Added functional requirement"):
            self._select_entity("requirement", identifier)

    def _part_dragged(self, instance_id: str, new_translation: float) -> None:
        project = self.service.project
        if any((c.kind == "fixed_position" and c.instance_id == instance_id) or
               (c.kind == "fixed_face" and c.first is not None and c.first.instance_id == instance_id)
               for c in project.constraints):
            self.status_label.setText("Fixed parts cannot be dragged. Edit the fixed-position constraint.")
            return
        def change(model):
            instance = next(i for i in model.instances if i.id == instance_id)
            instance.translation = round(new_translation, 3)
        self._command(change, "Moved part initial position")

    def edit_selected(self) -> None:
        if self._selected:
            self._edit_entity(*self._selected)

    def _edit_entity(self, kind: str, identifier: str) -> None:
        entity = self._entity(kind, identifier)
        if entity is None:
            return
        if kind in {"face", "definition_face"}:
            self._edit_face(kind, identifier, entity)
        elif kind == "outline":
            self._edit_outline(identifier, entity)
        elif kind == "dimension":
            self._edit_dimension(entity)
        elif kind == "sketch_dimension":
            self._edit_sketch_dimension(entity)
        elif kind == "constraint":
            self._edit_constraint(entity)
        elif kind == "contact":
            self._edit_contact(entity)
        elif kind == "policy":
            self._edit_policy(entity)
        elif kind == "requirement":
            self._edit_requirement(entity)
        elif kind == "source":
            self._edit_source(entity)
        elif kind == "correlation":
            self._edit_correlation(entity)
        elif kind in {"instance", "definition"}:
            self._edit_part(kind, entity)

    def _edit_face(self, kind, identifier, face) -> None:
        dialog = FaceDialog(face.name, face.local_x, face.lane, self, y=face.local_y)
        if dialog.exec() != QDialog.Accepted:
            return
        target = identifier
        def change(project):
            selected = self._face_in(project, kind, target)
            selected.name = dialog.name.text().strip()
            selected.local_x = dialog.x.value()
            selected.local_y = dialog.y.value()
            selected.lane = dialog.lane.text().strip()
        self._command(change, "Edited face")

    def _edit_outline(self, identifier: str, outline: Outline) -> None:
        definition_id, outline_id = identifier.split(":")[-2:]
        definition = next(d for d in self.service.project.definitions if d.id == definition_id)
        dialog = OutlineDialog(definition, outline, self)
        if dialog.exec() != QDialog.Accepted:
            return
        vertices = dialog.parsed_vertices()
        def change(project):
            owner = next(d for d in project.definitions if d.id == definition_id)
            target = next(item for item in owner.outlines if item.id == outline_id)
            target.name = dialog.name.text().strip()
            target.vertices = vertices
            target.closed = dialog.closed.isChecked()
            target.color = dialog.color.text().strip()
        self._command(change, "Edited outline")

    @staticmethod
    def _face_in(project, kind, identifier):
        owner_id, face_id = identifier.split(":", 1)
        if kind == "face":
            owner_id = next(i.definition_id for i in project.instances if i.id == owner_id)
        definition = next(d for d in project.definitions if d.id == owner_id)
        return next(f for f in definition.faces if f.id == face_id)

    def _edit_dimension(self, dimension) -> None:
        dialog = DimensionDialog(self.service.project, dimension=dimension, parent=self)
        source = next((s for s in self.service.project.sources if s.id == dimension.source_id), None)
        if source:
            dialog.distribution.setCurrentText(source.distribution)
            dialog.sigma.setValue(source.std or 0)
            dialog.sigma_level.setValue(source.sigma_level or 3)
        if dialog.exec() != QDialog.Accepted:
            return
        values = self._dimension_values(dialog)
        identifier = dimension.id
        source_id = ((values["source_id"] or new_id("source"))
                     if values["kind"] == "driving" else None)
        def change(project):
            item = next(d for d in project.dimensions if d.id == identifier)
            old_source_id = item.source_id
            item.name, item.first, item.second = values["name"], values["first"], values["second"]
            item.nominal, item.tolerance = values["nominal"], Tolerance(values["lower"], values["upper"])
            item.kind = values["kind"]
            item.display_style = values["display_style"]
            item.coefficient = values["coefficient"]
            item.source_id = source_id if item.kind == "driving" else None
            if item.kind == "driving" and values["source_id"] is None:
                lo, hi = sorted((values["lower"] / values["coefficient"],
                                 values["upper"] / values["coefficient"]))
                project.sources.append(VariationSource(source_id, values["name"], lo, hi,
                    values["distribution"], 0.0,
                    values["std"] / abs(values["coefficient"]) if values["std"] is not None else None,
                    values["sigma_level"]))
            if source_id:
                self._sync_source_dimensions(project, source_id)
            if old_source_id and old_source_id != source_id:
                self._drop_unused_source(project, old_source_id)
        self._command(change, "Edited dimension")

    def _edit_sketch_dimension(self, dimension: SketchDimension) -> None:
        definition = next(d for d in self.service.project.definitions if d.id == dimension.definition_id)
        current = measure_sketch_dimension(definition, dimension)
        dialog = SketchDimensionDialog(dimension.name, current, self.service.project.unit,
                                       driving=dimension.driving, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        identifier = dimension.id
        target = dialog.value.value()
        def change(project):
            item = next(d for d in project.sketch_dimensions if d.id == identifier)
            item.name = dialog.name.text().strip()
            item.driving = dialog.driving.isChecked()
            owner = next(d for d in project.definitions if d.id == item.definition_id)
            if item.driving:
                set_sketch_dimension_value(owner, item, target)
            else:
                item.nominal = target
        self._command(change, "Edited sketch dimension")

    def _edit_source(self, source) -> None:
        dialog = SourceDialog(source, self)
        if dialog.exec() != QDialog.Accepted:
            return
        def change(project):
            item = next(s for s in project.sources if s.id == source.id)
            item.name = dialog.name.text().strip()
            item.lower, item.upper = dialog.lower.value(), dialog.upper.value()
            item.distribution = dialog.distribution.currentText()
            item.mean = dialog.mean.value()
            item.std = dialog.std.value() or None
            item.sigma_level = dialog.sigma_level.value()
            self._sync_source_dimensions(project, item.id)
        self._command(change, "Edited manufacturing source")

    def _edit_correlation(self, correlation) -> None:
        dialog = CorrelationDialog(self.service.project, correlation, self)
        if dialog.exec() != QDialog.Accepted:
            return
        def change(project):
            pair = {dialog.first.currentData(), dialog.second.currentData()}
            if any(item.id != correlation.id and
                   {item.first_source_id, item.second_source_id} == pair
                   for item in project.correlations):
                raise ValueError("These sources already have a correlation.")
            item = next(c for c in project.correlations if c.id == correlation.id)
            item.first_source_id, item.second_source_id = dialog.first.currentData(), dialog.second.currentData()
            item.rho = dialog.rho.value()
        self._command(change, "Edited source correlation")

    @staticmethod
    def _drop_unused_source(project, source_id: str) -> None:
        if any(d.kind == "driving" and d.source_id == source_id for d in project.dimensions):
            return
        project.sources = [source for source in project.sources if source.id != source_id]
        project.correlations = [relation for relation in project.correlations
                                if source_id not in {relation.first_source_id, relation.second_source_id}]

    def _edit_constraint(self, constraint) -> None:
        dialog = ConstraintDialog(self.service.project, constraint=constraint, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        def change(project):
            item = next(c for c in project.constraints if c.id == constraint.id)
            kind = dialog.kind.currentData()
            item.name, item.kind = dialog.name.text().strip(), kind
            item.instance_id = dialog.instance.currentData() if kind in {"fixed_position", "bounded_translation"} else None
            item.first = None if item.instance_id else dialog.first.currentData()
            item.second = None if item.instance_id or kind == "fixed_face" else dialog.second.currentData()
            item.value = dialog.value.value()
            item.lower = dialog.lower.value() if kind in {"bounded_translation", "clearance_joint"} else None
            item.upper = dialog.upper.value() if kind in {"bounded_translation", "clearance_joint"} else None
        self._command(change, "Edited constraint")

    def _edit_contact(self, contact) -> None:
        dialog = ContactDialog(self.service.project, contact=contact, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        def change(project):
            item = next(c for c in project.contacts if c.id == contact.id)
            item.first, item.second, item.lane = dialog.first.currentData(), dialog.second.currentData(), dialog.lane.text().strip()
        self._command(change, "Edited contact")

    def _edit_policy(self, policy) -> None:
        dialog = PolicyDialog(self.service.project, policy=policy, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        def change(project):
            item = next(p for p in project.policies if p.id == policy.id)
            item.name, item.kind, item.contact_ids = dialog.name.text().strip(), dialog.kind.currentData(), dialog.selected_contact_ids()
            item.bounds = dialog.selected_bounds()
        self._command(change, "Edited positioning policy")

    def _edit_requirement(self, requirement) -> None:
        dialog = RequirementDialog(self.service.project, requirement=requirement, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        def change(project):
            item = next(r for r in project.requirements if r.id == requirement.id)
            item.name, item.first, item.second = dialog.name.text().strip(), dialog.first.currentData(), dialog.second.currentData()
            item.direction = dialog.direction.currentData()
            item.min_value = dialog.minimum.value() if dialog.minimum_enabled.isChecked() else None
            item.max_value = dialog.maximum.value() if dialog.maximum_enabled.isChecked() else None
            item.policy_id = dialog.policy.currentData()
        self._command(change, "Edited requirement")

    def _edit_part(self, kind, entity) -> None:
        if kind == "instance":
            dialog = InstanceDialog(entity, self)
            if dialog.exec() != QDialog.Accepted:
                return
            def change(project):
                item = next(i for i in project.instances if i.id == entity.id)
                item.name = dialog.name.text().strip()
                item.translation = dialog.translation.value()
                item.visible = dialog.visible.isChecked()
            self._command(change, "Edited part instance")
            return
        name, ok = QInputDialog.getText(self, "Rename part", "Name:", text=entity.name)
        if not ok or not name.strip():
            return
        def change(project):
            items = project.instances if kind == "instance" else project.definitions
            next(item for item in items if item.id == entity.id).name = name.strip()
        self._command(change, "Renamed part")

    def delete_selected(self) -> None:
        if not self._selected or self._selected[0] in {"group", "project"}:
            return
        kind, identifier = self._selected
        if QMessageBox.question(self, "Delete entity", f"Delete this {kind.replace('_', ' ')} and dependent relations?",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        def change(project):
            self._remove_entity(project, kind, identifier)
        if self._command(change, f"Deleted {kind}"):
            self._selected = None
            self._refresh()

    @staticmethod
    def _remove_entity(project, kind: str, identifier: str) -> None:
        removed_instances: set[str] = set()
        removed_faces: set[tuple[str, str]] = set()
        if kind == "instance":
            removed_instances.add(identifier)
            project.instances = [i for i in project.instances if i.id != identifier]
            used_definitions = {i.definition_id for i in project.instances}
            project.definitions = [d for d in project.definitions if d.id in used_definitions]
        elif kind == "definition":
            removed_instances = {i.id for i in project.instances if i.definition_id == identifier}
            project.instances = [i for i in project.instances if i.id not in removed_instances]
            project.definitions = [d for d in project.definitions if d.id != identifier]
            project.sketch_dimensions = [d for d in project.sketch_dimensions if d.definition_id != identifier]
        elif kind in {"face", "definition_face"}:
            owner_id, face_id = identifier.split(":", 1)
            if kind == "face":
                owner_id = next(i.definition_id for i in project.instances if i.id == owner_id)
            removed_faces = {(i.id, face_id) for i in project.instances if i.definition_id == owner_id}
            definition = next(d for d in project.definitions if d.id == owner_id)
            definition.faces = [f for f in definition.faces if f.id != face_id]
            for outline in definition.outlines:
                for vertex in outline.vertices:
                    if vertex.face_id == face_id:
                        vertex.face_id = None
        elif kind == "outline":
            definition_id, outline_id = identifier.split(":")[-2:]
            definition = next(d for d in project.definitions if d.id == definition_id)
            definition.outlines = [item for item in definition.outlines if item.id != outline_id]
            project.sketch_dimensions = [d for d in project.sketch_dimensions
                                         if not (d.definition_id == definition_id and
                                                 outline_id in {d.first_outline_id, d.second_outline_id})]
        elif kind == "dimension":
            project.dimensions = [d for d in project.dimensions if d.id != identifier]
        elif kind == "sketch_dimension":
            project.sketch_dimensions = [d for d in project.sketch_dimensions if d.id != identifier]
        elif kind == "source":
            for dimension in project.dimensions:
                if dimension.source_id == identifier:
                    dimension.source_id = None
            project.sources = [source for source in project.sources if source.id != identifier]
            project.correlations = [relation for relation in project.correlations
                                    if identifier not in {relation.first_source_id, relation.second_source_id}]
        elif kind == "correlation":
            project.correlations = [relation for relation in project.correlations if relation.id != identifier]
        elif kind == "constraint":
            project.constraints = [c for c in project.constraints if c.id != identifier]
        elif kind == "contact":
            project.contacts = [c for c in project.contacts if c.id != identifier]
            for policy in project.policies:
                policy.contact_ids = [cid for cid in policy.contact_ids if cid != identifier]
        elif kind == "policy":
            project.policies = [p for p in project.policies if p.id != identifier]
            for requirement in project.requirements:
                if requirement.policy_id == identifier:
                    requirement.policy_id = None
        elif kind == "requirement":
            project.requirements = [r for r in project.requirements if r.id != identifier]
        if removed_instances or removed_faces:
            valid_definitions = {d.id for d in project.definitions}
            project.sketch_dimensions = [d for d in project.sketch_dimensions
                                         if d.definition_id in valid_definitions]
            def touches(ref):
                return ref is not None and (ref.instance_id in removed_instances or
                                            (ref.instance_id, ref.face_id) in removed_faces)
            project.dimensions = [d for d in project.dimensions if not touches(d.first) and not touches(d.second)]
            project.constraints = [c for c in project.constraints if c.instance_id not in removed_instances and
                                   not touches(c.first) and not touches(c.second)]
            deleted_contacts = {c.id for c in project.contacts if touches(c.first) or touches(c.second)}
            project.contacts = [c for c in project.contacts if c.id not in deleted_contacts]
            for policy in project.policies:
                policy.contact_ids = [cid for cid in policy.contact_ids if cid not in deleted_contacts]
                for instance_id in removed_instances:
                    policy.bounds.pop(instance_id, None)
            project.requirements = [r for r in project.requirements if not touches(r.first) and not touches(r.second)]
        if kind in {"dimension", "instance", "definition", "face", "definition_face"}:
            for source in list(project.sources):
                MainWindow._drop_unused_source(project, source.id)
        valid_requirements = {r.id for r in project.requirements}
        project.analysis_cases = [case for case in project.analysis_cases if case.requirement_id in valid_requirements]

    def _tree_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        kind, identifier = item.data(0, Qt.UserRole)
        self._select_entity(kind, identifier)
        self._context_menu(kind, identifier, self.tree.viewport().mapToGlobal(position))

    def _context_menu(self, kind: str, identifier: str, global_position) -> None:
        menu = QMenu(self)
        if kind not in {"group", "project"}:
            edit = menu.addAction("Edit…")
            edit.triggered.connect(lambda: self._edit_entity(kind, identifier))
            if kind == "instance":
                instance = self._entity(kind, identifier)
                toggle = menu.addAction("Show" if not instance.visible else "Hide")
                toggle.triggered.connect(lambda: self._toggle_instance(identifier))
            if kind == "requirement":
                run = menu.addAction("Analyze this gap")
                run.triggered.connect(lambda: self._analyze_requirement(identifier))
            menu.addSeparator()
            delete = menu.addAction("Delete…")
            delete.triggered.connect(self.delete_selected)
        if menu.actions():
            menu.exec(global_position)

    def _toggle_instance(self, identifier: str) -> None:
        def change(project):
            instance = next(i for i in project.instances if i.id == identifier)
            instance.visible = not instance.visible
        self._command(change, "Changed part visibility")

    def _fit_view(self) -> None:
        self.sketch.fit_assembly()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._fit_on_show:
            self._fit_on_show = False
            QTimer.singleShot(0, self._fit_view)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.isVisible() and not self.sketch._manually_navigated:
            QTimer.singleShot(0, self._fit_view)

    def _toggle_grid(self) -> None:
        self.sketch.grid_visible = self.grid_action.isChecked()

    def _selected_requirement(self):
        if self._selected and self._selected[0] == "requirement":
            return self._entity(*self._selected)
        if len(self.service.project.requirements) == 1:
            return self.service.project.requirements[0]
        choices = [requirement.name for requirement in self.service.project.requirements]
        if not choices:
            QMessageBox.information(self, "Analyze", "Create a functional gap requirement first.")
            return None
        choice, ok = QInputDialog.getItem(self, "Select gap", "Analyze requirement:", choices, 0, False)
        return self.service.project.requirements[choices.index(choice)] if ok else None

    def analyze_selected(self) -> None:
        requirement = self._selected_requirement()
        if requirement:
            self._analyze_requirement(requirement.id)

    def _analyze_requirement(self, requirement_id: str) -> None:
        if self._busy:
            QMessageBox.information(self, "Analysis running", "Cancel the current calculation first.")
            return
        requirement = next((r for r in self.service.project.requirements if r.id == requirement_id), None)
        if requirement is None:
            QMessageBox.warning(self, "Analyze", "The selected functional requirement no longer exists.")
            return
        analysis_case = next((case for case in self.service.project.analysis_cases
                              if case.requirement_id == requirement_id), None)
        selected = {
            "samples": analysis_case.samples if analysis_case else self._settings["samples"],
            "seed": analysis_case.seed if analysis_case else self._settings["seed"],
            "sigma_level": analysis_case.sigma_level if analysis_case else self._settings["sigma_level"],
        }
        methods = tuple(analysis_case.methods if analysis_case else requirement.methods)
        settings = AnalysisSettingsDialog(selected["samples"], selected["seed"],
                                          selected["sigma_level"], self, methods=methods)
        if settings.exec() != QDialog.Accepted:
            return
        self._settings = {"samples": settings.samples.value(), "seed": settings.seed.value(),
                          "sigma_level": settings.sigma.value()}
        methods = settings.methods()
        needs_save = (
            list(methods) != requirement.methods or analysis_case is None or
            list(methods) != analysis_case.methods or
            any(self._settings[key] != getattr(analysis_case, key) for key in self._settings)
        )
        if needs_save:
            case_id = analysis_case.id if analysis_case else new_id("case")
            def change(project):
                target = next(r for r in project.requirements if r.id == requirement_id)
                target.methods = list(methods)
                stored = next((case for case in project.analysis_cases if case.requirement_id == requirement_id), None)
                if stored is None:
                    stored = AnalysisCase(case_id, f"{target.name} analysis", requirement_id)
                    project.analysis_cases.append(stored)
                stored.methods = list(methods)
                stored.samples = self._settings["samples"]
                stored.seed = self._settings["seed"]
                stored.sigma_level = self._settings["sigma_level"]
            if not self._command(change, "Saved analysis settings"):
                return
        self._busy = True
        self.analyze_action.setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_button.show()
        self.status_label.setText("Analyzing assembly…")
        self.messages.append("Analysis started. The model remains editable while it runs.")
        future = self.service.submit_analysis(requirement_id, methods,
                                              progress=lambda fraction: self._bridge.progress.emit(float(fraction)),
                                              **self._settings)
        self._future = future
        def done(f):
            try:
                self._bridge.completed.emit(requirement_id, f.result())
            except Exception as exc:
                self._bridge.failed.emit(str(exc) or exc.__class__.__name__)
        future.add_done_callback(done)

    def cancel_analysis(self) -> None:
        if self.sketch._drawing_active:
            self.sketch.cancel_outline()
            self._pending_new_part_name = None
            return
        if self.sketch._picking_point:
            self.sketch.cancel_pick_point()
            self._point_instance_id = None
            self.status_label.setText("Point placement cancelled")
            return
        if self._busy:
            self.service.cancel_analysis()
            self.status_label.setText("Cancelling analysis…")

    def _analysis_progress(self, value: float) -> None:
        fraction = value / 100 if value > 1 else value
        self.progress.setValue(max(0, min(100, int(fraction * 100))))

    def _finish_busy(self) -> None:
        self._busy = False
        self.analyze_action.setEnabled(True)
        self.progress.hide()
        self.cancel_button.hide()
        self._future = None

    def _analysis_complete(self, requirement_id: str, result) -> None:
        self._finish_busy()
        self._results[requirement_id] = result
        self._select_entity("requirement", requirement_id)
        self._show_result(result)
        self.status_label.setText("Analysis complete")

    def _analysis_failed(self, message: str) -> None:
        self._finish_busy()
        self.status_label.setText("Analysis did not complete")
        self.messages.append(message)
        if "cancel" not in message.lower() and "changed" not in message.lower():
            QMessageBox.warning(self, "Analysis", message)

    def _clear_results(self) -> None:
        for widget in (self.chain_view, self.worst_view, self.rss_view, self.mc_view):
            widget.clear()
        self.contributors.clear()
        self.histogram.scene().clear()
        self.contributor_chart.scene().clear()
        self.sketch._chain_dimensions.clear()

    def _show_result(self, result) -> None:
        project = self.service.project
        requirement = next((r for r in project.requirements if r.id == result.requirement_id), None)
        title = requirement.name if requirement else result.requirement_id
        diagnostics = [f"{d.severity.upper()}: {d.message}" for d in result.diagnostics]
        self.messages.setPlainText("\n".join(diagnostics) if diagnostics else "Analysis completed without diagnostics.")
        self.constraint_status.setPlainText("\n".join(diagnostics) if diagnostics else "No model diagnostics were reported.")
        chain = result.chain
        chain_lines = [f"Requirement: {title}", f"Equation: {chain.equation or 'Unresolved'}",
                       f"Nominal: {self._fmt(chain.nominal)} {project.unit}",
                       f"Floating degrees of freedom: {chain.degrees_of_freedom}"]
        source_names = {s.id: s.name for s in project.sources}
        for term in chain.terms:
            chain_lines.append(f"{term.coefficient:+.4f} × {source_names.get(term.source_id, term.source_id)}")
        if chain.floating:
            chain_lines.append("The selected gap depends on an assembly position; review the policy and feasible envelope.")
        self.chain_view.setPlainText("\n".join(chain_lines))

        worst = result.worst_case
        if worst:
            lines = [f"{title} — exact possible range under {worst.policy_kind}",
                     f"Nominal: {self._fmt(worst.nominal)} {project.unit}",
                     f"Minimum: {worst.minimum:.4f} {project.unit}",
                     f"Maximum: {worst.maximum:.4f} {project.unit}",
                     f"Possible interference: {'Yes' if worst.possible_interference else 'No'}",
                     f"Acceptance: {self._pass_text(worst.meets_limits)}",
                     "", "Minimum-state part positions:"]
            lines.extend(f"  {self._instance_name(k)}: {v:.4f} {project.unit}"
                         for k, v in worst.minimum_state.translations.items())
            lines.append("Maximum-state part positions:")
            lines.extend(f"  {self._instance_name(k)}: {v:.4f} {project.unit}"
                         for k, v in worst.maximum_state.translations.items())
            if worst.minimum_state.active_contacts:
                lines.append("Active contacts at minimum: " + ", ".join(worst.minimum_state.active_contacts))
            if worst.maximum_state.active_contacts:
                lines.append("Active contacts at maximum: " + ", ".join(worst.maximum_state.active_contacts))
            lines.extend("Warning: " + warning for warning in worst.warnings)
            self.worst_view.setPlainText("\n".join(lines))
        else:
            self.worst_view.setPlainText("Worst-case analysis was not requested or could not be resolved.")

        rss = result.rss
        if rss:
            lines = [f"{title} — affine RSS", f"Mean: {rss.mean:.4f} {project.unit}",
                     f"Standard deviation: {rss.std:.4f} {project.unit}",
                     f"Variance: {rss.variance:.6f} {project.unit}²",
                     f"{rss.sigma_level:g}σ interval: {rss.lower:.4f} to {rss.upper:.4f} {project.unit}"]
            lines.extend("Warning: " + warning for warning in rss.warnings)
            self.rss_view.setPlainText("\n".join(lines))
        else:
            self.rss_view.setPlainText("RSS is unavailable for this model or was not requested; review diagnostics.")

        mc = result.monte_carlo
        if mc:
            lines = [f"{title} — Monte Carlo, seed {mc.seed}",
                     f"Valid / requested: {mc.valid_samples} / {mc.requested_samples}",
                     f"Infeasible assemblies: {mc.infeasible_samples}",
                     f"Observed minimum / maximum: {self._fmt(mc.minimum)} / {self._fmt(mc.maximum)} {project.unit}",
                     f"Mean / standard deviation: {self._fmt(mc.mean)} / {self._fmt(mc.std)} {project.unit}",
                     f"Conditional failure probability: {self._fmt(mc.conditional_failure_probability)}",
                     f"Overall failure probability: {self._fmt(mc.overall_failure_probability)}",
                     f"Outside specification: {self._fmt(mc.ppm_outside_spec)} PPM"]
            lines.extend(f"{key}: {value:.4f} {project.unit}" for key, value in mc.percentiles.items())
            lines.extend("Warning: " + warning for warning in mc.warnings)
            self.mc_view.setPlainText("\n".join(lines))
            self._draw_histogram(mc.histogram_edges, mc.histogram_counts)
        else:
            self.mc_view.setPlainText("Monte Carlo analysis was not requested or could not be resolved.")
            self.histogram.scene().clear()

        self.contributors.clear()
        for term in sorted(chain.terms, key=lambda item: -abs(item.coefficient)):
            source = next((s for s in project.sources if s.id == term.source_id), None)
            span = abs(term.coefficient) * (source.upper - source.lower) if source else None
            variance = rss.variance_contributions.get(term.source_id) if rss else None
            QTreeWidgetItem(self.contributors, [source_names.get(term.source_id, term.source_id),
                                               f"{term.coefficient:+.4f}", self._fmt(span),
                                               f"{variance*100:.1f}%" if variance is not None else "—"])
        self.contributors.resizeColumnToContents(0)
        self._draw_contributor_chart(chain.terms, project)
        highlighted = {identifier for term in chain.terms for identifier in term.dimension_ids}
        self.sketch.set_highlights(self._selected, set(), highlighted)

    def _draw_histogram(self, edges: list[float], counts: list[int]) -> None:
        scene = self.histogram.scene()
        scene.clear()
        if not counts or len(edges) != len(counts) + 1 or max(counts) <= 0:
            scene.addText("No valid sample distribution is available.")
            return
        width, height = 520.0, 120.0
        top, bottom = 10.0, 115.0
        maximum = max(counts)
        bar_width = width / len(counts)
        for index, count in enumerate(counts):
            bar_height = (bottom - top) * count / maximum
            scene.addRect(index * bar_width, bottom - bar_height, max(1, bar_width - 1), bar_height,
                          QPen(QColor("#287eb5"), 0), QBrush(QColor("#80bddf")))
        left_label = scene.addText(f"{edges[0]:.3f}")
        left_label.setPos(0, bottom + 2)
        right_label = scene.addText(f"{edges[-1]:.3f}")
        right_label.setPos(width - right_label.boundingRect().width(), bottom + 2)
        scene.setSceneRect(0, 0, width, 145)
        self.histogram.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)

    def _draw_contributor_chart(self, terms, project) -> None:
        scene = self.contributor_chart.scene()
        scene.clear()
        sources = {source.id: source for source in project.sources}
        bars = []
        for term in terms:
            source = sources.get(term.source_id)
            if source:
                bars.append((source.name, abs(term.coefficient) * (source.upper - source.lower), term.coefficient))
        bars.sort(key=lambda item: -item[1])
        if not bars:
            scene.addText("No independent manufacturing contributors resolved.")
            return
        maximum = max((item[1] for item in bars), default=0) or 1
        for index, (name, span, coefficient) in enumerate(bars[:12]):
            y = index * 24
            label = scene.addText(name)
            label.setPos(0, y)
            x = 170
            width = max(1, 310 * span / maximum)
            color = QColor("#287eb5" if coefficient >= 0 else "#d28b44")
            scene.addRect(x, y + 5, width, 14, QPen(color, 0), QBrush(color))
            value = scene.addText(f"{span:.3f}")
            value.setPos(x + width + 5, y)
        scene.setSceneRect(0, 0, 560, max(60, len(bars[:12]) * 24 + 3))
        self.contributor_chart.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)

    @staticmethod
    def _fmt(value) -> str:
        return "—" if value is None else f"{value:.4f}"

    @staticmethod
    def _pass_text(value) -> str:
        return "No acceptance limits" if value is None else ("Pass" if value else "Fail")

    def _instance_name(self, identifier: str) -> str:
        instance = self._entity("instance", identifier)
        return instance.name if instance else identifier

    def export_report(self) -> None:
        requirement = self._selected_requirement()
        if requirement is None:
            return
        result = self._results.get(requirement.id)
        if result is None:
            QMessageBox.information(self, "Export report", "Analyze this requirement before exporting a report.")
            return
        path, filter_name = QFileDialog.getSaveFileName(
            self, "Export engineering report", "", "PDF report (*.pdf);;CSV analysis (*.csv);;Excel workbook (*.xlsx)")
        if not path:
            return
        try:
            from stacklab.reporting import export_csv, export_pdf, export_xlsx
            if "CSV" in filter_name:
                path = path if path.lower().endswith(".csv") else path + ".csv"
                export_csv(path, self.service.project, [result])
            elif "Excel" in filter_name:
                path = path if path.lower().endswith(".xlsx") else path + ".xlsx"
                export_xlsx(path, self.service.project, [result])
            else:
                path = path if path.lower().endswith(".pdf") else path + ".pdf"
                export_pdf(path, self.service.project, [result], self.service.presentation)
            self.status_label.setText(f"Exported {Path(path).name}")
        except Exception as exc:
            self._error("Export report", exc)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._maybe_save():
            event.ignore()
            return
        self.service.cancel_analysis()
        self._unsubscribe()
        self.service.close()
        event.accept()
