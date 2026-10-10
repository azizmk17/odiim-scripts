"""Freeform outlines stay editable while axial feature points drive the solver."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from stacklab.examples import stepped_pin_reference
from stacklab.domain import PartInstance
from stacklab.persistence import load_project_bundle, save_project
from stacklab.services import ProjectService
from stacklab.solvers import analyze
from stacklab.ui import MainWindow


def test_stepped_pin_example_solves_four_different_gaps_and_roundtrips(tmp_path):
    project = stepped_pin_reference()
    assert not [issue for issue in project.validate() if issue.severity == "error"]
    assert all(definition.outlines for definition in project.definitions[:-1])
    expected = {
        "f-head-gap": (3, 2.65, 3.35),
        "f-upper-datum": (20, 19.7, 20.3),
        "f-lower-datum": (30, 29.8, 30.2),
        "f-stem-gap": (5, 4.9, 5.1),
    }
    for requirement_id, values in expected.items():
        result = analyze(project, requirement_id, methods=("worst_case",))
        assert result.worst_case is not None
        assert (result.worst_case.nominal, result.worst_case.minimum,
                result.worst_case.maximum) == pytest.approx(values)

    path = tmp_path / "stepped.stack1d"
    save_project(path, project)
    bundle = load_project_bundle(path)
    assert bundle.presentation["long_centerline"] is True
    assert bundle.project.definitions[2].outlines[0].vertices[1].face_id == "f-pin-head"
    assert analyze(bundle.project, "f-head-gap", methods=("worst_case",)).worst_case.maximum == pytest.approx(3.35)


def test_canvas_draws_concave_part_places_measurement_point_and_stretches(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    service = ProjectService()
    window = MainWindow(service)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Concave part", True)
                        if args[1] == "Draw part" else ("Inside corner", True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: pytest.fail("Unexpected warning"))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Discard)
    try:
        window.draw_new_part()
        for x, y in ((0, 0), (60, 0), (60, 30), (30, 15), (0, 30)):
            window.sketch.add_outline_point(QPointF(x, y))
        assert window.sketch.finish_outline()
        definition = service.project.definitions[0]
        assert len(definition.outlines[0].vertices) == 5
        assert definition.outlines[0].vertices[1].face_id == definition.faces[1].id
        assert any(item.data(0) == "outline" for item in window.sketch.scene().items())

        window.place_point()
        window.sketch.point_clicked.emit(QPointF(30, 15))
        assert len(service.project.definitions[0].faces) == 3
        assert service.project.definitions[0].faces[-1].local_x == pytest.approx(5)
        assert service.project.definitions[0].outlines[0].vertices[3].face_id == service.project.definitions[0].faces[-1].id
        assert any(d.kind == "basic" and d.second.face_id == service.project.definitions[0].faces[-1].id
                   for d in service.project.dimensions)
        window.place_point()
        window.sketch.point_clicked.emit(QPointF(30, 15))
        assert len(service.project.definitions[0].faces) == 3
        assert window._selected == ("face", f"{service.project.instances[0].id}:{service.project.definitions[0].faces[-1].id}")

        outline_id = definition.outlines[0].id
        def rendered_right():
            item = next(item for item in window.sketch.scene().items()
                        if item.data(0) == "outline" and item.data(1).endswith(outline_id))
            return item.path().boundingRect().right()
        before = rendered_right()
        width_id = next(d.id for d in service.project.dimensions if d.kind == "driving")
        service.execute(lambda model: setattr(next(d for d in model.dimensions if d.id == width_id),
                                              "nominal", 12), "Change width")
        assert rendered_right() - before == pytest.approx(12)

        window.draw_outline(False)
        for x, y in ((0, 50), (30, 60), (72, 50)):
            window.sketch.add_outline_point(QPointF(x, y))
        assert window.sketch.finish_outline()
        assert len(service.project.definitions[0].outlines) == 2
        assert service.project.definitions[0].outlines[-1].closed is False
        assert window._selected[1].count(":") == 2
        assert window._selected_instance().id == service.project.instances[0].id

        path = tmp_path / "drawn.stack1d"
        service.save(path)
        reopened = load_project_bundle(path).project
        assert reopened.definitions[0].outlines[0].vertices[1].face_id == reopened.definitions[0].faces[1].id
    finally:
        window.close()
        app.processEvents()


def test_stepped_example_opens_from_file_menu(monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = ProjectService()
    window = MainWindow(service)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: pytest.fail("Example could not open"))
    try:
        window.open_stepped_example()
        assert service.project.id == "example-f"
        assert service.presentation["long_centerline"] is True
        assert len([item for item in window.sketch.scene().items() if item.data(0) == "outline"]) == 4
    finally:
        window.close()
        app.processEvents()


def test_clicking_a_reused_outline_keeps_its_instance_selected():
    app = QApplication.instance() or QApplication([])
    project = stepped_pin_reference()
    project.instances.append(PartInstance("f-pin-copy", "Second pin", "f-pin-def", 24))
    service = ProjectService(project)
    window = MainWindow(service)
    try:
        window._scene_selected("outline", "f-pin-copy:f-pin-def:f-pin-shape")
        assert window._selected_instance().id == "f-pin-copy"
        assert window._entity(*window._selected).id == "f-pin-shape"
    finally:
        window.close()
        app.processEvents()
