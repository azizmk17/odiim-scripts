"""Project commands, revision tracking and background analysis orchestration.

The service contains no Qt objects. A desktop view can use its notifications and
futures without sharing mutable engineering models with a worker thread.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import json
import logging
from pathlib import Path
from threading import Event, RLock
from typing import Callable
from uuid import uuid4

from .domain import Project


class StaleAnalysis(RuntimeError):
    """The model changed before an analysis finished."""


class ProjectService:
    def __init__(self, project: Project | None = None) -> None:
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stacklab-analysis")
        self._cancel_event = Event()
        self._listeners: list[Callable[[str, int], None]] = []
        self.project = deepcopy(project) if project is not None else Project(id=uuid4().hex, name="Untitled assembly")
        self.presentation: dict = {}
        self.path: Path | None = None
        self.revision = 0
        self._undo: list[tuple[Project, dict, str]] = []
        self._redo: list[tuple[Project, dict, str]] = []
        self._cache: dict[tuple, object] = {}
        self._saved_fingerprint = self._fingerprint()

    def _fingerprint(self) -> str:
        return json.dumps({"engineering": self.project.to_dict(), "presentation": self.presentation},
                          sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def dirty(self) -> bool:
        with self._lock:
            return self._fingerprint() != self._saved_fingerprint

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def subscribe(self, callback: Callable[[str, int], None]) -> Callable[[], None]:
        """Subscribe to ``(event_name, revision)`` notifications."""
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None

    def _notify(self, event: str) -> None:
        for callback in tuple(self._listeners):
            try:
                callback(event, self.revision)
            except Exception:
                logging.exception("A StackLab project listener failed")

    def _invalidate(self, event: str) -> None:
        self._cancel_event.set()
        self._cancel_event = Event()
        self._cache.clear()
        self.revision += 1
        self._notify(event)

    def new_project(self, name: str = "Untitled assembly") -> Project:
        with self._lock:
            self.project = Project(id=uuid4().hex, name=name)
            self.presentation = {}
            self.path = None
            self._undo.clear()
            self._redo.clear()
            self._saved_fingerprint = self._fingerprint()
            self._invalidate("new")
            return self.project

    def open(self, path: str | Path) -> Project:
        from .persistence import load_project_bundle

        bundle = load_project_bundle(path)
        with self._lock:
            self.project = bundle.project
            self.presentation = dict(bundle.presentation)
            self.path = Path(path)
            self._undo.clear()
            self._redo.clear()
            self._saved_fingerprint = self._fingerprint()
            self._invalidate("open")
            return self.project

    def save(self, path: str | Path | None = None) -> Path:
        from .persistence import save_project

        with self._lock:
            destination = Path(path) if path is not None else self.path
            if destination is None:
                raise ValueError("Choose a .stack1d file before saving.")
            snapshot = deepcopy(self.project)
            presentation = deepcopy(self.presentation)
            revision = self.revision
        save_project(destination, snapshot, presentation)
        with self._lock:
            if self.revision == revision:
                self.path = destination
                self._saved_fingerprint = self._fingerprint()
                self._notify("save")
        return destination

    def execute(self, mutator: Callable[[Project], None], description: str = "Edit model") -> Project:
        with self._lock:
            candidate = deepcopy(self.project)
            mutator(candidate)
            # The domain validates references and numerical input. Underconstrained
            # designs remain editable; analysis reports their missing relations.
            errors = [issue for issue in candidate.validate() if issue.severity == "error"]
            if errors:
                raise ValueError("; ".join(issue.message for issue in errors))
            self._undo.append((deepcopy(self.project), deepcopy(self.presentation), description))
            self._undo = self._undo[-100:]
            self._redo.clear()
            self.project = candidate
            self._invalidate("edit")
            return self.project

    def set_presentation(self, values: dict) -> None:
        with self._lock:
            self._undo.append((deepcopy(self.project), deepcopy(self.presentation), "Edit view"))
            self._undo = self._undo[-100:]
            self._redo.clear()
            self.presentation = deepcopy(values)
            self._invalidate("presentation")

    def undo(self) -> bool:
        with self._lock:
            if not self._undo:
                return False
            project, presentation, description = self._undo.pop()
            self._redo.append((deepcopy(self.project), deepcopy(self.presentation), description))
            self.project, self.presentation = project, presentation
            self._invalidate("undo")
            return True

    def redo(self) -> bool:
        with self._lock:
            if not self._redo:
                return False
            project, presentation, description = self._redo.pop()
            self._undo.append((deepcopy(self.project), deepcopy(self.presentation), description))
            self.project, self.presentation = project, presentation
            self._invalidate("redo")
            return True

    def analyze(self, requirement_id: str, methods: tuple[str, ...] = ("worst_case", "rss"), *,
                samples: int = 10_000, seed: int = 0, sigma_level: float = 3.0,
                progress: Callable[[float], None] | None = None,
                cancelled: Callable[[], bool] | None = None):
        """Analyze an immutable snapshot and reject a result from an old revision."""
        from .solvers import analyze as solve

        methods = tuple(methods)
        with self._lock:
            revision = self.revision
            key = (revision, requirement_id, methods, samples, seed, sigma_level)
            if key in self._cache:
                return deepcopy(self._cache[key])
            snapshot = deepcopy(self.project)
            token = self._cancel_event

        def is_cancelled() -> bool:
            return token.is_set() or (cancelled is not None and cancelled())

        def report_progress(completed: int, total: int) -> None:
            if progress is not None and total > 0:
                progress(completed / total)

        result = solve(snapshot, requirement_id, methods=methods, samples=samples,
                       seed=seed, sigma_level=sigma_level,
                       progress=report_progress if progress is not None else None,
                       cancelled=is_cancelled)
        with self._lock:
            if self.revision != revision or is_cancelled():
                raise StaleAnalysis("The project changed before analysis finished; run analysis again.")
            self._cache[key] = deepcopy(result)
        return result

    def submit_analysis(self, requirement_id: str, methods: tuple[str, ...] = ("worst_case", "rss"), **settings) -> Future:
        return self._executor.submit(self.analyze, requirement_id, methods, **settings)

    def nominal_geometry(self) -> dict[tuple[str, str], float]:
        """Solve current driving dimensions and contacts into global face positions."""
        from .solvers import solve_nominal_geometry

        with self._lock:
            snapshot = deepcopy(self.project)
        return solve_nominal_geometry(snapshot)

    def cancel_analysis(self) -> None:
        with self._lock:
            self._cancel_event.set()
            self._cancel_event = Event()

    def close(self) -> None:
        self.cancel_analysis()
        self._executor.shutdown(wait=False, cancel_futures=True)
