#!/usr/bin/env python3
"""Validated temporary output and recoverable publication for editor saves."""
import json
import os
import tempfile

import numpy as np


def _write_json_atomic(path, payload):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise


def write_mesh_temp(snapshot, target):
    import trimesh

    folder = os.path.dirname(target) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(target) + ".tmp-", suffix=".ply", dir=folder)
    os.close(fd)
    try:
        kwargs = {}
        if snapshot.vertex_colors is not None:
            kwargs["vertex_colors"] = snapshot.vertex_colors
        if snapshot.face_colors is not None:
            kwargs["face_colors"] = snapshot.face_colors
        mesh = trimesh.Trimesh(
            vertices=snapshot.vertices,
            faces=snapshot.faces,
            process=False,
            **kwargs,
        )
        mesh.export(tmp)
        validate_mesh(tmp, len(snapshot.vertices), len(snapshot.faces))
        return tmp
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise


def validate_mesh(path, expected_vertices=None, expected_faces=None):
    import trimesh

    mesh = trimesh.load(path, force="mesh", process=False)
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    if not len(vertices) or not len(faces):
        raise ValueError("saved model is empty")
    if not np.isfinite(vertices).all():
        raise ValueError("saved model contains invalid coordinates")
    if expected_vertices is not None and len(vertices) != int(expected_vertices):
        raise ValueError("saved vertex count changed during validation")
    if expected_faces is not None and len(faces) != int(expected_faces):
        raise ValueError("saved face count changed during validation")
    if faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError("saved model contains invalid face indices")
    return True


def publish_temp(temp_path, target, archive_path, journal_path, journal_payload):
    payload = dict(journal_payload)
    payload.update(target=target, archive=archive_path, temporary=temp_path, state="ready")
    _write_json_atomic(journal_path, payload)
    archive_tmp = None
    try:
        if os.path.exists(target):
            if not archive_path:
                raise RuntimeError("the destination appeared without a reserved archive path")
            import shutil

            folder = os.path.dirname(archive_path) or "."
            os.makedirs(folder, exist_ok=True)
            fd, archive_tmp = tempfile.mkstemp(
                prefix=os.path.basename(archive_path) + ".tmp-", suffix=".ply", dir=folder
            )
            os.close(fd)
            shutil.copy2(target, archive_tmp)
            validate_mesh(archive_tmp)
            os.replace(archive_tmp, archive_path)
            archive_tmp = None
        payload["state"] = "publishing"
        _write_json_atomic(journal_path, payload)
        os.replace(temp_path, target)
        payload["state"] = "published"
        _write_json_atomic(journal_path, payload)
        return True
    except Exception:
        if archive_tmp:
            try:
                os.remove(archive_tmp)
            except Exception:
                pass
        raise


def finish_journal(journal_path):
    try:
        os.remove(journal_path)
    except FileNotFoundError:
        pass
