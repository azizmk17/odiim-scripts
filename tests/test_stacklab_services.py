"""Application command and revision behavior at the project boundary."""

from threading import Event

import pytest

from stacklab.services import ProjectService, StaleAnalysis


def test_edit_undo_redo_tracks_dirty_state_and_revision():
    service = ProjectService()
    try:
        start = service.revision
        assert not service.dirty
        service.execute(lambda project: setattr(project, "name", "Changed assembly"), "Rename")
        assert service.project.name == "Changed assembly"
        assert service.dirty
        assert service.revision == start + 1
        assert service.can_undo
        assert service.undo()
        assert service.project.name == "Untitled assembly"
        assert not service.dirty
        assert service.can_redo
        assert service.redo()
        assert service.project.name == "Changed assembly"
        assert service.dirty
    finally:
        service.close()


def test_invalid_command_does_not_change_project_or_revision():
    service = ProjectService()
    try:
        start = service.revision
        def invalid(project):
            project.unit = "unrecognized"
        try:
            service.execute(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid engineering input was accepted")
        assert service.revision == start
        assert service.project.unit == "mm"
        assert not service.can_undo
    finally:
        service.close()


def test_background_result_from_old_revision_is_rejected(monkeypatch):
    started = Event()
    finish = Event()

    def slow_solver(*args, **kwargs):
        started.set()
        assert finish.wait(timeout=5)
        return object()

    monkeypatch.setattr("stacklab.solvers.analyze", slow_solver)
    service = ProjectService()
    try:
        future = service.submit_analysis("any", methods=("worst_case",))
        assert started.wait(timeout=5)
        service.execute(lambda project: setattr(project, "name", "New revision"))
        finish.set()
        with pytest.raises(StaleAnalysis):
            future.result(timeout=5)
    finally:
        finish.set()
        service.close()
