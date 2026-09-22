# Fixture reference contracts for PointYoink.
# Public boundary uses millimetres and T_target_source homogeneous transforms.
import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict

import numpy as np

from registration_metrics import as_transform, validate_landmark_pairs, kabsch_transform, landmark_residuals_mm


def file_fingerprint(path, chunk=1024*1024):
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b: break
            size += len(b); h.update(b)
    return {"sha256": h.hexdigest(), "size": size, "mtime_ns": os.stat(path).st_mtime_ns}


@dataclass
class FixtureProfile:
    id: str
    reference_mesh: str
    reference_fingerprint: dict
    units: str = "mm"
    schema_version: int = 1
    landmarks: list | None = None
    registration_region: dict | None = None
    removable_region: dict | None = None
    contact_margin_mm: float = 1.0
    created_at: int = 0

    @classmethod
    def create(cls, fixture_id, reference_mesh, landmarks=None, **kw):
        if landmarks:
            validate_landmark_pairs([[p, p] for p in landmarks], min_pairs=3)
        return cls(
            id=str(fixture_id),
            reference_mesh=os.path.abspath(reference_mesh),
            reference_fingerprint=file_fingerprint(reference_mesh),
            landmarks=landmarks or [],
            created_at=int(time.time()),
            **kw,
        )

    def to_json(self):
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text):
        data = json.loads(text)
        if data.get("units") != "mm":
            raise ValueError("fixture profile units must be mm")
        return cls(**data)


def solve_scan_to_fixture_from_pairs(pairs):
    """Return T_F_S from [fixture_xyz, scan_xyz] manual pairs."""
    pairs = validate_landmark_pairs(pairs)
    fixture_pts = np.asarray([p[0] for p in pairs], dtype=float)
    scan_pts = np.asarray([p[1] for p in pairs], dtype=float)
    T_F_S = kabsch_transform(scan_pts, fixture_pts)
    residuals = landmark_residuals_mm(pairs, T_F_S)
    return {
        "matrix": T_F_S.tolist(),
        "landmark_residuals_mm": [float(x) for x in residuals],
        "max_landmark_residual_mm": float(np.max(residuals)) if len(residuals) else None,
        "units": "mm",
        "frame_convention": "T_fixture_scan",
    }


def compose_scan_to_scan_through_fixture(T_F_base_scan, T_F_moving_scan):
    """Compose fixture poses into current Combine contract: moving scan -> base scan.

    Column-vector convention: p_base = T_base_moving @ p_moving.
    """
    T_F_A = as_transform(T_F_base_scan, "T_F_base_scan")
    T_F_B = as_transform(T_F_moving_scan, "T_F_moving_scan")
    return (np.linalg.inv(T_F_A) @ T_F_B).tolist()


def transform_plane_native_to_target(plane_native, T_target_native):
    """Transform plane [nx,ny,nz,d] from native coords to target coords.

    Plane convention: n dot p + d = 0.
    """
    T = as_transform(T_target_native)
    plane = np.asarray(plane_native, dtype=float)
    if plane.shape != (4,) or not np.isfinite(plane).all():
        raise ValueError("plane must be [nx,ny,nz,d]")
    invT = np.linalg.inv(T)
    out = invT.T @ plane
    n = out[:3]
    l = np.linalg.norm(n)
    if l <= 1e-12:
        raise ValueError("plane normal is zero")
    out = out / l
    return out.tolist()
