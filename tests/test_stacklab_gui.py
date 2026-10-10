"""Offscreen smoke checks for the connected desktop workflow."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from stacklab.services import ProjectService
from stacklab.examples import fixed_chain
from stacklab.domain import Face, FaceRef, FunctionalRequirement, new_id
from stacklab.ui import MainWindow
from stacklab.ui.dialogs import (
    AnalysisSettingsDialog, CorrelationDialog, DimensionDialog, PartDialog, SourceDialog,
)
from stacklab.solvers import analyze


def test_window_creates_model_part_and_undoes(monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = ProjectService()
    window = MainWindow(service)
    monkeypatch.setattr(PartDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Discard)
    try:
        assert "StackLab 1D" in window.windowTitle()
        window.create_part()
        assert len(service.project.instances) == 1
        assert len(service.project.definitions[0].faces) == 2
        assert len(service.project.dimensions) == 1
        assert window.sketch.scene().items()
        assert window.tree.topLevelItemCount() == 1
        monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Axis reference", True))
        monkeypatch.setattr(QInputDialog, "getDouble", lambda *args, **kwargs: (12.5, True))
        window.add_centerline()
        assert any(c.kind == "fixed_face" for c in service.project.constraints)
        assert any(face.lane == "centerline" for d in service.project.definitions for face in d.faces)
        service.undo()
        assert len(service.project.instances) == 1
        service.redo()
        assert len(service.project.instances) == 2
    finally:
        window.close()
        app.processEvents()


def test_analyze_action_persists_per_requirement_settings(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    project = fixed_chain(statistical=True)
    service = ProjectService(project)
    window = MainWindow(service)
    opened = []
    def choose_settings(dialog):
        opened.append((dialog.methods(), dialog.samples.value(), dialog.seed.value(), dialog.sigma.value()))
        if len(opened) > 1:
            return QDialog.Rejected
        dialog.monte_carlo.setChecked(True)
        dialog.samples.setValue(1200)
        dialog.seed.setValue(17)
        dialog.sigma.setValue(4.0)
        return QDialog.Accepted
    monkeypatch.setattr(AnalysisSettingsDialog, "exec", choose_settings)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    try:
        window._analyze_requirement(project.requirements[0].id)
        deadline = time.monotonic() + 15
        while window._busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()
        assert not window._busy
        assert project.requirements[0].id in window._results
        assert "Minimum" in window.worst_view.toPlainText()
        assert "Standard deviation" in window.rss_view.toPlainText()
        assert "Valid / requested" in window.mc_view.toPlainText()
        saved_case = service.project.analysis_cases[0]
        assert saved_case.requirement_id == project.requirements[0].id
        assert saved_case.methods == ["worst_case", "rss", "monte_carlo"]
        assert (saved_case.samples, saved_case.seed, saved_case.sigma_level) == (1200, 17, 4.0)
        assert service.project.requirements[0].methods == saved_case.methods
        window._analyze_requirement(project.requirements[0].id)
        assert opened[1] == (("worst_case", "rss", "monte_carlo"), 1200, 17, 4.0)
        second_id = new_id("requirement")
        def add_second(model):
            first = model.requirements[0]
            model.requirements.append(FunctionalRequirement(second_id, "Second gap", first.first,
                                                            first.second, methods=["worst_case"]))
        service.execute(add_second, "Add second gap")
        window._analyze_requirement(second_id)
        assert opened[2][0] == ("worst_case",)
        assert len(service.project.analysis_cases) == 1
        path = tmp_path / "settings.stack1d"
        service.save(path)
        reopened = ProjectService()
        reopened.open(path)
        assert reopened.project.analysis_cases[0].methods == saved_case.methods
        assert reopened.project.analysis_cases[0].seed == 17
        reopened.close()
    finally:
        window.close()
        app.processEvents()


def test_shared_source_and_correlation_controls_change_engineering_result(monkeypatch):
    app = QApplication.instance() or QApplication([])
    project = fixed_chain(statistical=True)
    project.definitions[0].faces.append(Face("extra", "Extra face", 20))
    instance_id = project.instances[0].id
    origin_id = project.definitions[0].faces[0].id
    first_source_id, second_source_id = project.sources[0].id, project.sources[1].id
    service = ProjectService(project)
    window = MainWindow(service)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Discard)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args[2]))

    def choose_shared_source(dialog):
        dialog.source_choice.setCurrentIndex(dialog.source_choice.findData(first_source_id))
        dialog.coefficient.setValue(2)
        dialog.nominal.setValue(20)
        return QDialog.Accepted

    monkeypatch.setattr(DimensionDialog, "exec", choose_shared_source)
    try:
        window._create_dimension(FaceRef(instance_id, origin_id), FaceRef(instance_id, "extra"))
        assert not warnings
        shared = next(d for d in service.project.dimensions if d.second.face_id == "extra")
        assert shared.source_id == first_source_id
        assert shared.coefficient == 2
        assert shared.tolerance.lower == -0.4
        assert len(service.project.sources) == 3

        def choose_correlation(dialog):
            dialog.first.setCurrentIndex(dialog.first.findData(first_source_id))
            dialog.second.setCurrentIndex(dialog.second.findData(second_source_id))
            dialog.rho.setValue(0.5)
            return QDialog.Accepted

        monkeypatch.setattr(CorrelationDialog, "exec", choose_correlation)
        window.add_correlation()
        assert not warnings
        assert len(service.project.correlations) == 1
        assert any(item.data(0, 0x0100) == ("correlation", service.project.correlations[0].id)
                   for group_index in range(window.tree.topLevelItem(0).childCount())
                   for item_index in range(window.tree.topLevelItem(0).child(group_index).childCount())
                   for item in [window.tree.topLevelItem(0).child(group_index).child(item_index)])
        result = analyze(service.project, project.requirements[0].id, methods=("rss",))
        independent = (.2 / 3) ** 2 + (.1 / 3) ** 2 + (.15 / 3) ** 2
        assert result.rss.variance < independent

        def edit_source(dialog):
            dialog.lower.setValue(-0.25)
            return QDialog.Accepted

        monkeypatch.setattr(SourceDialog, "exec", edit_source)
        window._edit_source(next(s for s in service.project.sources if s.id == first_source_id))
        assert not warnings
        assert shared.id in {d.id for d in service.project.dimensions}
        updated = next(d for d in service.project.dimensions if d.id == shared.id)
        assert updated.tolerance.lower == -0.5
    finally:
        window.close()
        app.processEvents()
