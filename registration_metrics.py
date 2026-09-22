# Alignment diagnostics for PointYoink.
# Public contract: all coordinates and translations are millimetres.
import math
import numpy as np


def as_transform(T, name="transform"):
    T = np.asarray(T, dtype=float)
    if T.shape != (4, 4):
        raise ValueError(f"{name} must be 4x4")
    if not np.isfinite(T).all():
        raise ValueError(f"{name} contains non-finite values")
    if not np.allclose(T[3], [0, 0, 0, 1], atol=1e-7):
        raise ValueError(f"{name} must be a homogeneous rigid transform")
    R = T[:3, :3]
    if not np.allclose(R.T @ R, np.eye(3), atol=1e-5):
        raise ValueError(f"{name} rotation is not orthonormal")
    if np.linalg.det(R) < 0.0:
        raise ValueError(f"{name} rotation reflects the mesh")
    return T


def apply_transform(points, T):
    pts = np.asarray(points, dtype=float)
    T = as_transform(T)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("points must be Nx3")
    if not np.isfinite(pts).all():
        raise ValueError("points contain non-finite values")
    return pts @ T[:3, :3].T + T[:3, 3]


def _rank(points, tol_mm):
    pts = np.asarray(points, dtype=float)
    centered = pts - pts.mean(axis=0)
    s = np.linalg.svd(centered, compute_uv=False)
    return int(np.sum(s > tol_mm)), s


def validate_landmark_pairs(pairs, min_pairs=3, tol_mm=1e-4):
    """Validate manual base/moving landmark pairs.

    pairs are [base_xyz, moving_xyz] in each scan's native millimetres. Returns
    a normalized list and raises ValueError with an actionable reason otherwise.
    """
    if pairs is None:
        pairs = []
    if len(pairs) < min_pairs:
        raise ValueError(f"need at least {min_pairs} landmark pairs")
    base = []
    moving = []
    for i, pair in enumerate(pairs, 1):
        if len(pair) != 2:
            raise ValueError(f"pair {i} must contain base and moving points")
        b = np.asarray(pair[0], dtype=float)
        m = np.asarray(pair[1], dtype=float)
        if b.shape != (3,) or m.shape != (3,):
            raise ValueError(f"pair {i} points must be xyz triples")
        if not np.isfinite(b).all() or not np.isfinite(m).all():
            raise ValueError(f"pair {i} contains non-finite coordinates")
        base.append(b); moving.append(m)
    base = np.asarray(base, dtype=float)
    moving = np.asarray(moving, dtype=float)
    for label, pts in (("base", base), ("moving", moving)):
        d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=2)
        dup = np.argwhere((d <= tol_mm) & (d >= 0))
        if any(i < j for i, j in dup):
            raise ValueError(f"{label} landmarks include duplicate/coincident points")
        rank, s = _rank(pts, max(tol_mm, 1e-6))
        if rank < 2:
            raise ValueError(f"{label} landmarks are collinear or coincident")
    return [[base[i].tolist(), moving[i].tolist()] for i in range(len(base))]


def kabsch_transform(source, target):
    """Rigid transform mapping source points onto target points, in mm."""
    source = np.asarray(source, dtype=float)
    target = np.asarray(target, dtype=float)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise ValueError("source and target must be matching Nx3 arrays")
    if len(source) < 3:
        raise ValueError("need at least 3 points")
    if not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError("points contain non-finite values")
    cs, ct = source.mean(axis=0), target.mean(axis=0)
    H = (source - cs).T @ (target - ct)
    U, _S, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    T = np.eye(4, dtype=float)
    T[:3, :3] = R
    T[:3, 3] = ct - R @ cs
    return T


def landmark_residuals_mm(pairs, T_moving_to_base):
    pairs = validate_landmark_pairs(pairs)
    base = np.asarray([p[0] for p in pairs], dtype=float)
    moving = np.asarray([p[1] for p in pairs], dtype=float)
    fitted = apply_transform(moving, T_moving_to_base)
    return np.linalg.norm(fitted - base, axis=1)


def nearest_distances_mm(source, target, chunk=4096):
    source = np.asarray(source, dtype=float)
    target = np.asarray(target, dtype=float)
    if source.ndim != 2 or target.ndim != 2 or source.shape[1] != 3 or target.shape[1] != 3:
        raise ValueError("source and target must be Nx3")
    if len(source) == 0 or len(target) == 0:
        return np.full(len(source), np.inf, dtype=float)
    out = np.empty(len(source), dtype=float)
    for start in range(0, len(source), chunk):
        block = source[start:start+chunk]
        d2 = ((block[:, None, :] - target[None, :, :]) ** 2).sum(axis=2)
        out[start:start+len(block)] = np.sqrt(d2.min(axis=1))
    return out


def coverage_report(source_pts, target_pts, T_source_to_target, threshold_mm):
    if threshold_mm <= 0:
        raise ValueError("threshold_mm must be positive")
    moved = apply_transform(source_pts, T_source_to_target)
    target = np.asarray(target_pts, dtype=float)
    sdist = nearest_distances_mm(moved, target)
    inv = np.linalg.inv(as_transform(T_source_to_target))
    t_in_source = apply_transform(target, inv)
    tdist = nearest_distances_mm(t_in_source, np.asarray(source_pts, dtype=float))
    inliers = sdist[np.isfinite(sdist) & (sdist <= threshold_mm)]
    return {
        "threshold_mm": float(threshold_mm),
        "source_coverage": float(np.mean(sdist <= threshold_mm)) if len(sdist) else 0.0,
        "target_coverage": float(np.mean(tdist <= threshold_mm)) if len(tdist) else 0.0,
        "unmatched_fraction": float(np.mean(sdist > threshold_mm)) if len(sdist) else 1.0,
        "inlier_median_mm": float(np.median(inliers)) if len(inliers) else None,
        "inlier_p95_mm": float(np.percentile(inliers, 95)) if len(inliers) else None,
    }


def point_to_plane_constraint_rank(points, normals, characteristic_length_mm=None, tol=1e-7):
    """Return normalized point-to-plane Jacobian rank and weak directions.

    Translation columns are normals; rotation columns are cross(point, normal).
    Rotation is normalized by characteristic length so mm/radian units do not
    dominate the singular values.
    """
    pts = np.asarray(points, dtype=float)
    nrm = np.asarray(normals, dtype=float)
    if pts.shape != nrm.shape or pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError("points and normals must be matching Nx3 arrays")
    good = np.isfinite(pts).all(axis=1) & np.isfinite(nrm).all(axis=1)
    nlen = np.linalg.norm(nrm, axis=1)
    good &= nlen > 1e-9
    if not np.any(good):
        return {"rank": 0, "singular_values": [], "weak_directions": ["tx", "ty", "tz", "rx", "ry", "rz"]}
    pts = pts[good]
    nrm = nrm[good] / nlen[good, None]
    if characteristic_length_mm is None:
        span = np.ptp(pts, axis=0)
        characteristic_length_mm = max(float(np.linalg.norm(span)), 1.0)
    rot = np.cross(pts, nrm) / max(float(characteristic_length_mm), 1e-9)
    J = np.column_stack([nrm, rot])
    _u, s, vt = np.linalg.svd(J, full_matrices=False)
    rank = int(np.sum(s > max(float(s[0]) if len(s) else 1.0, 1.0) * tol))
    names = np.array(["tx", "ty", "tz", "rx", "ry", "rz"])
    weak = []
    if len(s):
        nullish = vt[rank:] if rank < vt.shape[0] else vt[-1:][s[-1:] <= max(s[0], 1.0) * tol]
        scores = np.abs(nullish).max(axis=0) if len(nullish) else np.zeros(6)
        weak = names[scores > 0.35].tolist()
    return {"rank": rank, "singular_values": [float(x) for x in s], "weak_directions": weak}


def alignment_summary(source_pts, target_pts, T_source_to_target, threshold_mm, landmarks=None):
    report = coverage_report(source_pts, target_pts, T_source_to_target, threshold_mm)
    reasons = []
    if report["source_coverage"] < 0.3 or report["target_coverage"] < 0.3:
        reasons.append("low_overlap")
    if report["inlier_p95_mm"] is not None and report["inlier_p95_mm"] > threshold_mm * 0.75:
        reasons.append("high_residual")
    if landmarks:
        res = landmark_residuals_mm(landmarks, T_source_to_target)
        report["landmark_residuals_mm"] = [float(x) for x in res]
        if len(res) and float(np.max(res)) > threshold_mm:
            reasons.append("inconsistent_landmarks")
    report["reason_codes"] = reasons
    report["ambiguous"] = False
    return report
