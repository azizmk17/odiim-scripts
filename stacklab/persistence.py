"""Portable, versioned StackLab 1D project storage.

The archive contains plain JSON. Engineering data and presentation settings live
in separate members so changing the sketch layout cannot change the model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from .domain import Project


FORMAT_NAME = "stacklab-1d"
SCHEMA_VERSION = 2
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 24 * 1024 * 1024
_CURRENT_MEMBERS = {"manifest.json", "engineering.json", "presentation.json"}


class ProjectFormatError(ValueError):
    """A project archive or its engineering data is malformed or unsupported."""


@dataclass(frozen=True)
class ProjectBundle:
    project: Project
    presentation: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def _json_bytes(data: Any) -> bytes:
    try:
        return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True,
                           allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProjectFormatError(f"Project data cannot be encoded as JSON: {exc}") from exc


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"Non-finite numeric value {value} is not allowed")


def _read_json(archive: ZipFile, name: str) -> Any:
    try:
        info = archive.getinfo(name)
        if info.file_size > MAX_MEMBER_BYTES:
            raise ProjectFormatError(f"{name} exceeds the project size limit")
        raw = archive.read(info)
        return json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite)
    except KeyError as exc:
        raise ProjectFormatError(f"Required file {name} is missing") from exc
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ProjectFormatError):
            raise
        raise ProjectFormatError(f"{name} is not valid UTF-8 JSON: {exc}") from exc


def _validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProjectFormatError("Engineering data must be a JSON object")
    for key in ("id", "name"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ProjectFormatError(f"Engineering data requires a non-empty {key}")
    for key in ("definitions", "instances", "dimensions", "sketch_dimensions", "sources", "constraints",
                "contacts", "requirements", "policies", "correlations"):
        if key in payload and not isinstance(payload[key], list):
            raise ProjectFormatError(f"Engineering field {key} must be a list")
    if "unit" in payload and not isinstance(payload["unit"], str):
        raise ProjectFormatError("Engineering field unit must be a string")
    return payload


def _project_from_payload(payload: Any) -> Project:
    validated = _validate_payload(payload)
    try:
        project = Project.from_dict(validated)
        if hasattr(project, "validate"):
            project.validate()
        return project
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise ProjectFormatError(f"Invalid engineering model: {exc}") from exc


def save_project(path: str | os.PathLike[str], project: Project,
                 presentation: Mapping[str, Any] | None = None) -> None:
    """Validate and atomically write a complete project archive."""
    destination = Path(path)
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Project folder does not exist: {destination.parent}")
    if not isinstance(project, Project):
        raise TypeError("project must be a StackLab Project")
    if hasattr(project, "validate"):
        project.validate()
    payload = project.to_dict()
    # Older in-memory projects may still carry a view field; the file keeps it
    # exclusively in presentation.json.
    legacy_view = payload.pop("view", {})
    payload = _validate_payload(payload)
    # Validate the same public decoding path used by a later reopen.
    _project_from_payload(payload)
    layout = dict(legacy_view if presentation is None else presentation)
    manifest = {
        "format": FORMAT_NAME,
        "schema_version": SCHEMA_VERSION,
        "saved_utc": datetime.now(timezone.utc).isoformat(),
    }
    entries = {
        "manifest.json": _json_bytes(manifest),
        "engineering.json": _json_bytes(payload),
        "presentation.json": _json_bytes(layout),
    }
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", suffix=".tmp",
                                         dir=destination.parent, delete=False) as handle:
            temp_path = Path(handle.name)
        with ZipFile(temp_path, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        with temp_path.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def load_project_bundle(path: str | os.PathLike[str]) -> ProjectBundle:
    """Load a supported schema version, migrating v1's combined JSON layout."""
    source = Path(path)
    try:
        if source.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ProjectFormatError("Project archive exceeds the size limit")
        with ZipFile(source) as archive:
            members = archive.namelist()
            if len(members) != len(set(members)):
                raise ProjectFormatError("Project archive contains duplicate file names")
            if any("/" in name or "\\" in name or name.startswith(".") for name in members):
                raise ProjectFormatError("Project archive contains an invalid file name")
            manifest = _read_json(archive, "manifest.json")
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_NAME:
                raise ProjectFormatError("Not a StackLab 1D project archive")
            version = manifest.get("schema_version")
            if version == 2:
                if set(members) != _CURRENT_MEMBERS:
                    raise ProjectFormatError("Version 2 archive has missing or unexpected files")
                engineering = _read_json(archive, "engineering.json")
                presentation = _read_json(archive, "presentation.json")
            elif version == 1:
                # Early archives combined model and view data in project.json.
                if set(members) != {"manifest.json", "project.json"}:
                    raise ProjectFormatError("Version 1 archive has missing or unexpected files")
                combined = _read_json(archive, "project.json")
                if not isinstance(combined, dict):
                    raise ProjectFormatError("Version 1 project.json must be an object")
                engineering = combined.get("engineering", combined.get("project"))
                presentation = combined.get("presentation", {})
            else:
                raise ProjectFormatError(f"Unsupported project schema version: {version!r}")
            if not isinstance(presentation, dict):
                raise ProjectFormatError("Presentation settings must be a JSON object")
            if isinstance(engineering, dict):
                engineering = dict(engineering)
                embedded_view = engineering.pop("view", {})
                if not isinstance(embedded_view, dict):
                    raise ProjectFormatError("Embedded presentation settings must be a JSON object")
                presentation = {**embedded_view, **presentation}
            project = _project_from_payload(engineering)
            return ProjectBundle(project, presentation, manifest)
    except (OSError, BadZipFile) as exc:
        raise ProjectFormatError(f"Cannot open project archive: {exc}") from exc


def load_project(path: str | os.PathLike[str]) -> Project:
    """Load only the engineering project; use load_project_bundle for view settings."""
    return load_project_bundle(path).project
