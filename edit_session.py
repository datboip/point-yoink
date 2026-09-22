#!/usr/bin/env python3
"""Authoritative geometry and bounded history for one editing session."""
from dataclasses import dataclass
import hashlib
import os

import numpy as np


SCHEMA_VERSION = 1
DEFAULT_HISTORY_BYTES = 256 * 1024 * 1024


class EditSessionError(RuntimeError):
    pass


class EditSizeError(EditSessionError):
    pass


class HistoryLimitError(EditSessionError):
    pass


def source_signature(path):
    st = os.stat(path)
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return {
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "sha256": h.hexdigest(),
    }


def signature_matches(path, signature):
    try:
        st = os.stat(path)
        if int(st.st_size) != int(signature["size"]):
            return False
        if int(st.st_mtime_ns) == int(signature["mtime_ns"]):
            return True
        return source_signature(path)["sha256"] == signature["sha256"]
    except Exception:
        return False


def _ply_element_count(path, element):
    try:
        with open(path, "rb") as fh:
            header = fh.read(256 * 1024).split(b"end_header", 1)[0]
        match = (b"element " + element.encode("ascii") + b" ")
        for line in header.splitlines():
            if line.startswith(match):
                return int(line.split()[-1])
    except Exception:
        pass
    return None


def _visual_attributes(obj, vertex_count, face_count):
    vertex_colors = None
    face_colors = None
    visual = getattr(obj, "visual", None)
    if visual is not None and bool(getattr(visual, "defined", False)):
        try:
            colors = np.asarray(visual.vertex_colors)
            if len(colors) == vertex_count:
                vertex_colors = np.ascontiguousarray(colors.copy())
        except Exception:
            pass
        try:
            colors = np.asarray(visual.face_colors)
            if face_count is not None and len(colors) == face_count:
                face_colors = np.ascontiguousarray(colors.copy())
        except Exception:
            pass
    return vertex_colors, face_colors


@dataclass
class GeometrySnapshot:
    project: str
    node: str
    source_path: str
    source_key: str
    source_signature: dict
    kind: str
    vertices: np.ndarray
    faces: object
    vertex_colors: object
    face_colors: object
    geometry_revision: int
    topology_revision: int


@dataclass
class _DeleteCommand:
    kind: str
    indices: np.ndarray
    removed_vertices: object
    removed_faces: object
    removed_vertex_colors: object
    removed_face_colors: object
    selected_indices: np.ndarray
    before_geometry_revision: int
    after_geometry_revision: int
    before_topology_revision: int
    after_topology_revision: int

    @property
    def byte_size(self):
        total = 0
        for value in (
            self.indices,
            self.removed_vertices,
            self.removed_faces,
            self.removed_vertex_colors,
            self.removed_face_colors,
            self.selected_indices,
        ):
            if value is not None:
                total += int(value.nbytes)
        return total


@dataclass
class _VertexCommand:
    indices: np.ndarray
    before_vertices: np.ndarray
    after_vertices: np.ndarray
    before_geometry_revision: int
    after_geometry_revision: int
    before_topology_revision: int
    after_topology_revision: int

    @property
    def byte_size(self):
        return int(self.indices.nbytes + self.before_vertices.nbytes + self.after_vertices.nbytes)


class EditSession:
    def __init__(
        self,
        project,
        node,
        source_path,
        source_key,
        kind,
        vertices,
        faces=None,
        vertex_colors=None,
        face_colors=None,
        signature=None,
        history_bytes=DEFAULT_HISTORY_BYTES,
    ):
        self.schema_version = SCHEMA_VERSION
        self.project = str(project)
        self.node = str(node)
        self.source_path = os.path.abspath(source_path)
        self.source_key = str(source_key or "")
        self.source_signature = signature or source_signature(source_path)
        self.kind = kind
        self.vertices = np.ascontiguousarray(vertices, dtype=np.float64)
        self.faces = None if faces is None else np.ascontiguousarray(faces, dtype=np.int64)
        self.vertex_colors = None if vertex_colors is None else np.ascontiguousarray(vertex_colors.copy())
        self.face_colors = None if face_colors is None else np.ascontiguousarray(face_colors.copy())
        self.selection = np.zeros(len(self.vertices), dtype=bool)
        self.geometry_revision = 0
        self.topology_revision = 0
        self.saved_geometry_revision = 0
        self._revision_sequence = 0
        self._topology_sequence = 0
        self.history_cursor = 0
        self._history = []
        self._history_bytes = 0
        self.history_budget = max(1, int(history_bytes))

    @classmethod
    def from_file(
        cls,
        project,
        node,
        source_path,
        source_key,
        kind="mesh",
        max_faces=5_000_000,
        max_points=6_000_000,
        history_bytes=DEFAULT_HISTORY_BYTES,
    ):
        import trimesh

        signature = source_signature(source_path)
        if kind == "mesh":
            count = _ply_element_count(source_path, "face")
            if count is not None and count > int(max_faces):
                raise EditSizeError(
                    "This model has {:,} faces. The editor limit is {:,}, so it was not opened for editing.".format(
                        count, int(max_faces)
                    )
                )
            obj = trimesh.load(source_path, force="mesh", process=False)
            vertices = np.asarray(obj.vertices, dtype=np.float64)
            faces = np.asarray(obj.faces, dtype=np.int64).reshape(-1, 3)
            if len(faces) > int(max_faces):
                raise EditSizeError(
                    "This model has {:,} faces. The editor limit is {:,}, so it was not opened for editing.".format(
                        len(faces), int(max_faces)
                    )
                )
            vertex_colors, face_colors = _visual_attributes(obj, len(vertices), len(faces))
            return cls(
                project,
                node,
                source_path,
                source_key,
                kind,
                vertices,
                faces,
                vertex_colors,
                face_colors,
                signature,
                history_bytes,
            )

        count = _ply_element_count(source_path, "vertex")
        if count is not None and count > int(max_points):
            raise EditSizeError(
                "This point set has {:,} points. The editor limit is {:,}, so it was not opened for editing.".format(
                    count, int(max_points)
                )
            )
        obj = trimesh.load(source_path, process=False)
        vertices = np.asarray(getattr(obj, "vertices", np.zeros((0, 3))), dtype=np.float64)
        if len(vertices) == 0:
            obj = trimesh.load(source_path, force="mesh", process=False)
            vertices = np.asarray(obj.vertices, dtype=np.float64)
        if len(vertices) > int(max_points):
            raise EditSizeError(
                "This point set has {:,} points. The editor limit is {:,}, so it was not opened for editing.".format(
                    len(vertices), int(max_points)
                )
            )
        vertex_colors, _ = _visual_attributes(obj, len(vertices), None)
        return cls(
            project,
            node,
            source_path,
            source_key,
            "points",
            vertices,
            None,
            vertex_colors,
            None,
            signature,
            history_bytes,
        )

    @property
    def dirty(self):
        return self.geometry_revision != self.saved_geometry_revision

    @property
    def can_undo(self):
        return self.history_cursor > 0

    @property
    def can_redo(self):
        return self.history_cursor < len(self._history)

    @property
    def count(self):
        return len(self.faces) if self.kind == "mesh" else len(self.vertices)

    @property
    def history_bytes(self):
        return self._history_bytes

    def set_selection(self, selection):
        selection = np.asarray(selection, dtype=bool)
        if len(selection) != len(self.vertices):
            raise EditSessionError("selection does not match the current vertices")
        self.selection = np.ascontiguousarray(selection.copy())

    def _drop_redo(self):
        if self.history_cursor >= len(self._history):
            return
        for command in self._history[self.history_cursor :]:
            self._history_bytes -= command.byte_size
        del self._history[self.history_cursor :]

    def _append(self, command):
        if command.byte_size > self.history_budget:
            raise HistoryLimitError(
                "This edit needs {} MB of undo memory, over the {} MB limit.".format(
                    max(1, command.byte_size // (1024 * 1024)),
                    max(1, self.history_budget // (1024 * 1024)),
                )
            )
        self._drop_redo()
        self._history.append(command)
        self.history_cursor += 1
        self._history_bytes += command.byte_size
        while self._history_bytes > self.history_budget and self.history_cursor > 1:
            old = self._history.pop(0)
            self._history_bytes -= old.byte_size
            self.history_cursor -= 1

    def delete_selected(self, selection=None):
        if selection is not None:
            self.set_selection(selection)
        selected = np.flatnonzero(self.selection).astype(np.int64)
        if not len(selected):
            return False
        before_geometry = self.geometry_revision
        before_topology = self.topology_revision
        self._revision_sequence += 1; self._topology_sequence += 1
        after_geometry = self._revision_sequence
        after_topology = self._topology_sequence

        if self.kind == "mesh":
            drop = np.flatnonzero(self.selection[self.faces].all(axis=1)).astype(np.int64)
            if not len(drop):
                return False
            command = _DeleteCommand(
                "mesh",
                drop,
                None,
                self.faces[drop].copy(),
                None,
                None if self.face_colors is None else self.face_colors[drop].copy(),
                selected,
                before_geometry,
                after_geometry,
                before_topology,
                after_topology,
            )
            self._append(command)
            keep = np.ones(len(self.faces), dtype=bool)
            keep[drop] = False
            self.faces = np.ascontiguousarray(self.faces[keep])
            if self.face_colors is not None:
                self.face_colors = np.ascontiguousarray(self.face_colors[keep])
        else:
            command = _DeleteCommand(
                "points",
                selected,
                self.vertices[selected].copy(),
                None,
                None if self.vertex_colors is None else self.vertex_colors[selected].copy(),
                None,
                selected,
                before_geometry,
                after_geometry,
                before_topology,
                after_topology,
            )
            self._append(command)
            keep = np.ones(len(self.vertices), dtype=bool)
            keep[selected] = False
            self.vertices = np.ascontiguousarray(self.vertices[keep])
            if self.vertex_colors is not None:
                self.vertex_colors = np.ascontiguousarray(self.vertex_colors[keep])
            self.selection = np.zeros(len(self.vertices), dtype=bool)

        if self.kind == "mesh":
            self.selection[:] = False
        self.geometry_revision = after_geometry
        self.topology_revision = after_topology
        return True

    def replace_vertices(self, vertices):
        """Apply a same-topology geometry change with exact undo and redo."""
        vertices = np.asarray(vertices, dtype=np.float64)
        if vertices.shape != self.vertices.shape:
            raise EditSessionError("replacement vertices do not match the current geometry")
        if not np.isfinite(vertices).all():
            raise EditSessionError("replacement vertices contain invalid coordinates")
        changed = np.flatnonzero(np.any(vertices != self.vertices, axis=1)).astype(np.int64)
        if not len(changed):
            return False
        self._revision_sequence += 1
        command = _VertexCommand(
            changed,
            self.vertices[changed].copy(),
            vertices[changed].copy(),
            self.geometry_revision,
            self._revision_sequence,
            self.topology_revision,
            self.topology_revision,
        )
        self._append(command)
        self.vertices[changed] = command.after_vertices
        self.geometry_revision = command.after_geometry_revision
        return True

    @staticmethod
    def _restore_rows(current, removed, indices):
        total = len(current) + len(indices)
        keep = np.ones(total, dtype=bool)
        keep[indices] = False
        out = np.empty((total,) + current.shape[1:], dtype=current.dtype)
        out[keep] = current
        out[indices] = removed
        return np.ascontiguousarray(out)

    def undo(self):
        if not self.can_undo:
            return False
        command = self._history[self.history_cursor - 1]
        if isinstance(command, _VertexCommand):
            self.vertices[command.indices] = command.before_vertices
            self.geometry_revision = command.before_geometry_revision
            self.topology_revision = command.before_topology_revision
            self.history_cursor -= 1
            return True
        if command.kind == "mesh":
            self.faces = self._restore_rows(self.faces, command.removed_faces, command.indices)
            if command.removed_face_colors is not None:
                current = self.face_colors
                self.face_colors = self._restore_rows(current, command.removed_face_colors, command.indices)
            self.selection[:] = False
            self.selection[command.selected_indices] = True
        else:
            self.vertices = self._restore_rows(self.vertices, command.removed_vertices, command.indices)
            if command.removed_vertex_colors is not None:
                current = self.vertex_colors
                self.vertex_colors = self._restore_rows(current, command.removed_vertex_colors, command.indices)
            self.selection = np.zeros(len(self.vertices), dtype=bool)
            self.selection[command.selected_indices] = True
        self.geometry_revision = command.before_geometry_revision
        self.topology_revision = command.before_topology_revision
        self.history_cursor -= 1
        return True

    def redo(self):
        if not self.can_redo:
            return False
        command = self._history[self.history_cursor]
        if isinstance(command, _VertexCommand):
            self.vertices[command.indices] = command.after_vertices
            self.geometry_revision = command.after_geometry_revision
            self.topology_revision = command.after_topology_revision
            self.history_cursor += 1
            return True
        keep = np.ones(self.count, dtype=bool)
        keep[command.indices] = False
        if command.kind == "mesh":
            self.faces = np.ascontiguousarray(self.faces[keep])
            if self.face_colors is not None:
                self.face_colors = np.ascontiguousarray(self.face_colors[keep])
            self.selection[:] = False
        else:
            self.vertices = np.ascontiguousarray(self.vertices[keep])
            if self.vertex_colors is not None:
                self.vertex_colors = np.ascontiguousarray(self.vertex_colors[keep])
            self.selection = np.zeros(len(self.vertices), dtype=bool)
        self.geometry_revision = command.after_geometry_revision
        self.topology_revision = command.after_topology_revision
        self.history_cursor += 1
        return True

    def mark_saved(self, revision=None):
        revision = self.geometry_revision if revision is None else int(revision)
        if revision == self.geometry_revision:
            self.saved_geometry_revision = revision

    def snapshot(self, compact=True):
        vertices = self.vertices
        faces = self.faces
        vertex_colors = self.vertex_colors
        face_colors = self.face_colors
        if self.kind == "mesh" and compact:
            used = np.unique(faces)
            remap = np.zeros(len(vertices), dtype=np.int64)
            remap[used] = np.arange(len(used), dtype=np.int64)
            vertices = vertices[used]
            faces = remap[faces]
            if vertex_colors is not None:
                vertex_colors = vertex_colors[used]
        return GeometrySnapshot(
            self.project,
            self.node,
            self.source_path,
            self.source_key,
            dict(self.source_signature),
            self.kind,
            np.ascontiguousarray(vertices.copy(), dtype=np.float64),
            None if faces is None else np.ascontiguousarray(faces.copy(), dtype=np.int64),
            None if vertex_colors is None else np.ascontiguousarray(vertex_colors.copy()),
            None if face_colors is None else np.ascontiguousarray(face_colors.copy()),
            self.geometry_revision,
            self.topology_revision,
        )
