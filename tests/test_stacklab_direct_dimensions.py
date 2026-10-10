"""Direct sketch selections create dimensions without manually placing faces."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from stacklab.domain import (AssemblyConstraint, Face, FaceRef, Outline,
                             PartDefinition, PartInstance, Project, SketchDimension, SketchVertex)
from stacklab.persistence import load_project_bundle, save_project
from stacklab.services import ProjectService
from stacklab.sketch_dimensions import GeometryPick, measure_sketch_dimension, set_sketch_dimension_value
from stacklab.ui import MainWindow
from stacklab.ui.dialogs import DimensionDialog, SketchDimensionDialog
from stacklab.ui.sketch import SCALE


def rectangle_project():
    return Project(
        "direct-dimensions", "Direct sketch dimensions",
        definitions=[PartDefinition("def", "Block", [Face("left", "Left", 0)], [
            Outline("outline", "Rectangle", [SketchVertex(0, 0, "left"),
                SketchVertex(40, 0), SketchVertex(40, 20), SketchVertex(0, 20, "left")])])],
        instances=[PartInstance("instance", "Block", "def")],
        constraints=[AssemblyConstraint("fixed", "Ground left", "fixed_face",
                                        first=FaceRef("instance", "left"), value=0)],
    )


def pick(kind, index):
    return GeometryPick(kind, "instance", "def", "outline", index)


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = ProjectService(rectangle_project())
    window = MainWindow(service)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: pytest.fail("Unexpected warning"))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Discard)
    monkeypatch.setattr(DimensionDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(SketchDimensionDialog, "exec", lambda self: QDialog.Accepted)
    yield window
    window.close()
    app.processEvents()


def test_one_horizontal_line_creates_toleranced_axial_dimension(window, monkeypatch, tmp_path):
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Length of this line", True))
    window._set_tool("dimension")
    window.sketch.geometry_clicked.emit(pick("segment", 0))
    project = window.service.project
    assert len(project.dimensions) == 1
    assert project.dimensions[0].nominal == pytest.approx(40)
    assert project.definitions[0].outlines[0].vertices[1].face_id == project.dimensions[0].second.face_id
    assert project.dimensions[0].kind == "driving"
    assert project.dimensions[0].show_on_sketch
    path = tmp_path / "direct.stack1d"
    save_project(path, project)
    assert load_project_bundle(path).project.dimensions[0].nominal == 40
    assert load_project_bundle(path).project.dimensions[0].show_on_sketch


def test_clicking_rendered_line_uses_dimension_picker(window, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Length of this line", True))
    window.show()
    QApplication.processEvents()
    window.sketch.fit_assembly()
    window._set_tool("dimension")
    midpoint = window.sketch.mapFromScene(QPointF(20 * SCALE, 70))
    QTest.mouseClick(window.sketch.viewport(), Qt.LeftButton, pos=midpoint)
    assert len(window.service.project.dimensions) == 1


def test_two_vertical_lines_dimension_axial_spacing(window, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Spacing to another line", True))
    window._set_tool("dimension")
    window.sketch.geometry_clicked.emit(pick("segment", 1))
    window.sketch.geometry_clicked.emit(pick("segment", 3))
    project = window.service.project
    assert len(project.dimensions) == 1
    assert project.dimensions[0].nominal == pytest.approx(40)
    right = project.definitions[0].outlines[0].vertices
    assert right[1].face_id == right[2].face_id


def test_two_vertices_create_axial_dimension_without_place_point(window):
    window._set_tool("dimension")
    window.sketch.geometry_clicked.emit(pick("vertex", 0))
    window.sketch.geometry_clicked.emit(pick("vertex", 1))
    assert len(window.service.project.dimensions) == 1
    assert window.service.project.dimensions[0].nominal == pytest.approx(40)


def test_vertex_to_centerline_creates_axial_dimension(window):
    def add_centerline(project):
        project.definitions.append(PartDefinition("datum-def", "Datum", [
            Face("datum-face", "Datum", 0, "centerline")]))
        project.instances.append(PartInstance("datum-instance", "Datum", "datum-def", 80))
        project.constraints.append(AssemblyConstraint("datum-fix", "Fix datum", "fixed_face",
            first=FaceRef("datum-instance", "datum-face"), value=80))
    window.service.execute(add_centerline, "Add datum")
    window._set_tool("dimension")
    window.sketch.geometry_clicked.emit(pick("vertex", 1))
    window.sketch.face_clicked.emit(FaceRef("datum-instance", "datum-face"))
    assert len(window.service.project.dimensions) == 1
    assert window.service.project.dimensions[0].nominal == pytest.approx(40)


def test_two_horizontal_lines_drive_vertical_sketch_spacing(window, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Spacing to another line", True))
    window._set_tool("dimension")
    window.sketch.geometry_clicked.emit(pick("segment", 0))
    window.sketch.geometry_clicked.emit(pick("segment", 2))
    assert len(window.service.project.sketch_dimensions) == 1
    assert window.service.project.sketch_dimensions[0].nominal == pytest.approx(20)
    def edit(dialog):
        dialog.value.setValue(30)
        return QDialog.Accepted
    monkeypatch.setattr(SketchDimensionDialog, "exec", edit)
    window._edit_sketch_dimension(window.service.project.sketch_dimensions[0])
    vertices = window.service.project.definitions[0].outlines[0].vertices
    assert vertices[2].y == pytest.approx(30)
    assert vertices[3].y == pytest.approx(30)


def test_two_vertical_vertices_drive_sketch_height_and_roundtrip(window, monkeypatch, tmp_path):
    window._set_tool("dimension")
    window.sketch.geometry_clicked.emit(pick("vertex", 0))
    window.sketch.geometry_clicked.emit(pick("vertex", 3))
    project = window.service.project
    assert not project.dimensions
    assert len(project.sketch_dimensions) == 1
    assert project.sketch_dimensions[0].nominal == pytest.approx(20)
    assert any(item.data(0) == "sketch_dimension" for item in window.sketch.scene().items())

    def edit(dialog):
        dialog.value.setValue(30)
        return QDialog.Accepted
    monkeypatch.setattr(SketchDimensionDialog, "exec", edit)
    window._edit_sketch_dimension(project.sketch_dimensions[0])
    assert window.service.project.definitions[0].outlines[0].vertices[3].y == pytest.approx(30)
    path = tmp_path / "height.stack1d"
    save_project(path, window.service.project)
    restored = load_project_bundle(path).project
    assert restored.sketch_dimensions[0].nominal == pytest.approx(30)
    assert restored.definitions[0].outlines[0].vertices[3].y == pytest.approx(30)


def test_diagonal_length_can_change_while_endpoint_axial_x_stays_bound():
    definition = PartDefinition("diagonal", "Diagonal", [Face("end", "End X", 30)], [
        Outline("edge", "Edge", [SketchVertex(0, 0), SketchVertex(30, 40, "end")], False)])
    dimension = SketchDimension("length", "Length", "diagonal", "line_length", "edge", 0)
    set_sketch_dimension_value(definition, dimension, 65)
    assert definition.faces[0].local_x == 30
    assert definition.outlines[0].vertices[1].x == 30
    assert measure_sketch_dimension(definition, dimension) == pytest.approx(65)


def test_sketch_constraint_tool_fixes_point_then_makes_line_horizontal(window, monkeypatch):
    window.service.execute(lambda project: setattr(
        project.definitions[0].outlines[0].vertices[1], "y", 5), "Skew top")
    choices = iter([("Fix point", True), ("Horizontal line", True)])
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: next(choices))
    window.add_sketch_constraint()
    window.sketch.geometry_clicked.emit(pick("vertex", 0))
    window.add_sketch_constraint()
    window.sketch.geometry_clicked.emit(pick("segment", 0))
    assert len(window.service.project.sketch_constraints) == 2
    assert window.service.project.definitions[0].outlines[0].vertices[1].y == pytest.approx(0, abs=1e-6)
    assert any(item.data(0) == "sketch_constraint" for item in window.sketch.scene().items())


def test_circle_draw_and_direct_diameter_dimension(window, monkeypatch, tmp_path):
    window.show()
    QApplication.processEvents()
    window._select_entity("instance", "instance")
    window.sketch.fit_assembly()
    row = window.sketch._part_rows["instance"]
    window.draw_circle()
    for point in (QPointF(20 * SCALE, row + 40), QPointF(30 * SCALE, row + 40)):
        QTest.mouseClick(window.sketch.viewport(), Qt.LeftButton,
                         pos=window.sketch.mapFromScene(point))
    definition = window.service.project.definitions[0]
    assert len(definition.circles) == 1
    circle = definition.circles[0]
    assert circle.radius == pytest.approx(10, abs=0.5)

    def diameter_dialog(dialog):
        dialog.value.setValue(24)
        return QDialog.Accepted
    monkeypatch.setattr(SketchDimensionDialog, "exec", diameter_dialog)
    window._set_tool("dimension")
    window.sketch.geometry_clicked.emit(GeometryPick("circle", "instance", "def", circle.id, 0))
    assert window.service.project.definitions[0].circles[0].radius == pytest.approx(12)
    assert window.service.project.sketch_dimensions[0].kind == "circle_diameter"
    path = tmp_path / "circle.stack1d"
    save_project(path, window.service.project)
    restored = load_project_bundle(path).project
    assert restored.definitions[0].circles[0].radius == pytest.approx(12)


def test_two_nonparallel_lines_create_angle_dimension(window, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args, **kwargs: ("Spacing to another line", True))
    window._set_tool("dimension")
    window.sketch.geometry_clicked.emit(pick("segment", 0))
    window.sketch.geometry_clicked.emit(pick("segment", 1))
    dimension = window.service.project.sketch_dimensions[0]
    assert dimension.kind == "angle_between_lines"
    assert dimension.nominal == pytest.approx(90)
    window._edit_sketch_dimension(dimension)
    assert measure_sketch_dimension(window.service.project.definitions[0], dimension) == pytest.approx(90)


def test_angle_dimension_rotates_second_line():
    definition = PartDefinition("angled", "Angles", [], [
        Outline("first", "First", [SketchVertex(0, 0), SketchVertex(10, 0)], False),
        Outline("second", "Second", [SketchVertex(10, 0), SketchVertex(10, 10)], False),
    ])
    dimension = SketchDimension("angle", "Angle", "angled", "angle_between_lines",
                                "first", 0, "second", 0)
    set_sketch_dimension_value(definition, dimension, 60)
    assert measure_sketch_dimension(definition, dimension) == pytest.approx(60)
