#!/usr/bin/env python3
# Align one scan to another for PointYoink. Memory-capped subprocess.
#   python3 align.py --base A.ply --moving B.ply --out result.json [--pairs pairs.json] [--auto] [--no-icp]
# pairs.json: {"pairs": [[[bx,by,bz],[mx,my,mz]], ...]} matching points in each scan's own mm coordinates
# (3 or more). The rough fit from the pairs (Kabsch) is refined with point-to-plane ICP; --auto instead
# finds the rough fit itself (FPFH features + RANSAC), which works when the scans overlap well.
# Output: {"matrix": 4x4 moving->base (mm), "fitness": overlap share 0..1, "rmse": mm, "pair_error_before", "pair_error_after"}
import sys, os, argparse, json, resource
MEM_CAP_GB = float(os.environ.get("POINTYOINK_MEM_CAP_GB", "8"))
try:
    cap = int(MEM_CAP_GB * 1024**3); resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
except Exception: pass
import numpy as np
from registration_metrics import (
    validate_landmark_pairs,
    kabsch_transform,
    landmark_residuals_mm,
)


def _dump_json(path, obj):
    """Write the result beside its final name and swap it in whole, so the parent never reads a half-written file."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w") as f: json.dump(obj, f)
    os.replace(tmp, path)

def emit(stage, **kw): print("STAGE %s %s" % (stage, json.dumps(kw)), flush=True)

def kabsch(P, Q):
    """Rigid transform taking points P onto Q (both Nx3): returns 4x4."""
    return kabsch_transform(P, Q)

def sample(path, n):
    import trimesh
    loaded = trimesh.load(path, process=False)
    m = loaded
    if isinstance(loaded, trimesh.Scene):
        geoms = [g for g in loaded.geometry.values()]
        meshes = [g for g in geoms if hasattr(g, "faces") and len(getattr(g, "faces", []))]
        clouds = [g for g in geoms if hasattr(g, "vertices") and len(getattr(g, "vertices", []))]
        if meshes:
            m = trimesh.util.concatenate(meshes)
        elif clouds:
            pts = np.concatenate([np.asarray(g.vertices, np.float64) for g in clouds], axis=0)
            return sample_points_with_normals(pts, n)
    if hasattr(m, "faces") and len(getattr(m, "faces", [])):
        if len(m.faces) > 800000:
            import fast_simplification
            v, f = fast_simplification.simplify(np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.int32), target_count=800000)
            m = trimesh.Trimesh(v, f, process=False)
        pts, fidx = trimesh.sample.sample_surface(m, n)
        return np.asarray(pts, np.float64), np.asarray(m.face_normals[fidx], np.float64)
    pts = np.asarray(getattr(m, "vertices", []), np.float64)
    return sample_points_with_normals(pts, n)

def sample_points_with_normals(pts, n):
    if pts.size == 0:
        raise ValueError("no points in " + str(pts))
    if len(pts) > n:
        idx = np.linspace(0, len(pts) - 1, n).astype(np.int64)
        pts = pts[idx]
    try:
        import open3d as o3d
        pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))
        pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=4.0, max_nn=30))
        nrm = np.asarray(pc.normals, np.float64)
        if len(nrm) == len(pts): return pts, nrm
    except Exception:
        pass
    nrm = np.zeros_like(pts); nrm[:, 2] = 1.0
    return pts, nrm

def cloud(pts, nrm, voxel):
    import open3d as o3d
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts)); pc.normals = o3d.utility.Vector3dVector(nrm)
    return pc.voxel_down_sample(voxel)

def pair_err(pairs, T):
    if not pairs: return None
    B = np.array([p[0] for p in pairs], float); M = np.array([p[1] for p in pairs], float)
    Mh = (M @ T[:3, :3].T) + T[:3, 3]; return float(np.sqrt(((Mh - B) ** 2).sum(1)).mean())

def _registration_metrics(T, M, B, final_corr, max_dist, icp_fitness=None):
    """Extra quality signals beyond one-sided fitness/RMSE: symmetric overlap, per-correspondence
    residual percentiles, normal agreement, and transform magnitude."""
    import open3d as o3d
    out = {}
    # Transform magnitude - pure numpy, always available
    try:
        out["translation_mm"] = round(float(np.linalg.norm(T[:3, 3])), 2)
        cos_a = float(np.clip((np.trace(T[:3, :3]) - 1.0) / 2.0, -1.0, 1.0))
        out["rotation_deg"] = round(float(np.degrees(np.arccos(cos_a))), 2)
    except Exception:
        out["translation_mm"] = None; out["rotation_deg"] = None
    # Symmetric fitness: build a temporary cloud from the transformed M points and evaluate B→T(M)
    try:
        mp_arr = np.asarray(M.points, dtype=np.float64)
        mp_t = (T[:3, :3] @ mp_arr.T).T + T[:3, 3]
        M_t = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(mp_t))
        ev = o3d.pipelines.registration.evaluate_registration(B, M_t, max_dist)
        rev = float(ev.fitness or 0.0)
        if icp_fitness is not None and (icp_fitness + rev) > 1e-9:
            out["sym_fitness"] = round(2.0 * icp_fitness * rev / (icp_fitness + rev), 3)
        else:
            out["sym_fitness"] = None
    except Exception:
        mp_t = None; out["sym_fitness"] = None
    # Per-correspondence residuals and normal agreement from the final ICP correspondence set
    if final_corr is None or not len(final_corr):
        out["median_residual_mm"] = None; out["p95_residual_mm"] = None; out["normal_agreement"] = None
        return out
    try:
        corr = np.asarray(final_corr, dtype=np.int64).reshape(-1, 2)
        if mp_t is None:
            mp_arr = np.asarray(M.points, dtype=np.float64)
            mp_t = (T[:3, :3] @ mp_arr.T).T + T[:3, 3]
        bp_arr = np.asarray(B.points, dtype=np.float64)
        vm = (corr[:, 0] >= 0) & (corr[:, 0] < len(mp_t))
        vb = (corr[:, 1] >= 0) & (corr[:, 1] < len(bp_arr))
        valid = vm & vb
        if valid.any():
            dists = np.linalg.norm(mp_t[corr[valid, 0]] - bp_arr[corr[valid, 1]], axis=1)
            out["median_residual_mm"] = round(float(np.median(dists)), 3)
            out["p95_residual_mm"] = round(float(np.percentile(dists, 95)), 3)
        else:
            out["median_residual_mm"] = None; out["p95_residual_mm"] = None
    except Exception:
        out["median_residual_mm"] = None; out["p95_residual_mm"] = None
    try:
        mn_arr = np.asarray(M.normals, dtype=np.float64); bn_arr = np.asarray(B.normals, dtype=np.float64)
        corr = np.asarray(final_corr, dtype=np.int64).reshape(-1, 2)
        vm = (corr[:, 0] >= 0) & (corr[:, 0] < len(mn_arr))
        vb = (corr[:, 1] >= 0) & (corr[:, 1] < len(bn_arr))
        valid = vm & vb
        if valid.any() and len(mn_arr) and len(bn_arr):
            mn_rot = (T[:3, :3] @ mn_arr[corr[valid, 0]].T).T
            dots = np.clip((mn_rot * bn_arr[corr[valid, 1]]).sum(1), -1.0, 1.0)
            out["normal_agreement"] = round(float(np.mean(dots)), 3)
        else:
            out["normal_agreement"] = None
    except Exception:
        out["normal_agreement"] = None
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--moving", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--pairs"); ap.add_argument("--auto", action="store_true"); ap.add_argument("--no-icp", action="store_true")
    ap.add_argument("--voxel", type=float, default=1.0, help="mm, working resolution for ICP")
    ap.add_argument("--max-dist", type=float, default=6.0, help="mm, ICP correspondence distance")
    ap.add_argument("--samples", type=int, default=250000, help="max points sampled from each scan")
    ap.add_argument("--auto-pairs", type=int, default=12, help="number of final Auto correspondences to return (0 disables markers)")
    a = ap.parse_args()
    pairs = json.load(open(a.pairs))["pairs"] if a.pairs else []
    T0 = np.eye(4)
    landmark_metrics = None
    if pairs:
        try:
            pairs = validate_landmark_pairs(pairs)
        except ValueError as e:
            msg = str(e)
            _dump_json(a.out, {"matrix": T0.tolist(), "fitness": None, "rmse": None, "reason_codes": ["invalid_landmarks"], "error": msg})
            emit("error", msg=msg); return 2
    if pairs and len(pairs) >= 3:
        T0 = kabsch([p[1] for p in pairs], [p[0] for p in pairs])
        residuals = landmark_residuals_mm(pairs, T0)
        landmark_metrics = {"landmark_residuals_mm": [float(x) for x in residuals], "landmark_max_residual_mm": float(np.max(residuals)) if len(residuals) else None}
        emit("pairs", n=len(pairs), error_mm=round(pair_err(pairs, T0), 2))
    elif not a.auto:
        emit("error", msg="need 3 or more point pairs, or --auto"); return 2
    emit("load", msg="reading both scans")
    bp, bn = sample(a.base, max(1000, int(a.samples))); mp, mn = sample(a.moving, max(1000, int(a.samples)))
    try:
        import open3d as o3d
    except Exception:
        _dump_json(a.out, {"matrix": T0.tolist(), "fitness": None, "rmse": None, "pair_error_before": pair_err(pairs, np.eye(4)), "pair_error_after": pair_err(pairs, T0),
                   "note": "Open3D not installed: rough fit from the points only"})
        emit("result", fitness=None, rmse=None, icp=False); return 0
    B = cloud(bp, bn, a.voxel); M = cloud(mp, mn, a.voxel)
    if a.auto and len(pairs) < 3:
        emit("auto", msg="looking for a rough fit by itself")
        fv = a.voxel * 3
        Bd, Md = B.voxel_down_sample(fv), M.voxel_down_sample(fv)
        fb = o3d.pipelines.registration.compute_fpfh_feature(Bd, o3d.geometry.KDTreeSearchParamHybrid(radius=fv * 5, max_nn=100))
        fm = o3d.pipelines.registration.compute_fpfh_feature(Md, o3d.geometry.KDTreeSearchParamHybrid(radius=fv * 5, max_nn=100))
        # FGR first: deterministic, faster than RANSAC, same FPFH features.
        # Falls back to RANSAC when FGR produces a weak or geometrically implausible result.
        global_method = "ransac"
        try:
            rfgr = o3d.pipelines.registration.registration_fgr_based_on_feature_matching(
                Md, Bd, fm, fb,
                o3d.pipelines.registration.FastGlobalRegistrationOption(maximum_correspondence_distance=fv * 1.5))
            Tfgr = np.asarray(rfgr.transformation)
            if (rfgr.fitness > 0.2 and np.isfinite(Tfgr).all()
                    and np.linalg.norm(Tfgr[:3, 3]) < 5000):
                T0 = Tfgr; global_method = "fgr"
                emit("auto_done", method="fgr", fitness=round(rfgr.fitness, 3), rmse=round(rfgr.inlier_rmse, 2))
        except Exception:
            pass
        if global_method == "ransac":
            r = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
                Md, Bd, fm, fb, True, fv * 1.5, o3d.pipelines.registration.TransformationEstimationPointToPoint(False), 3,
                [o3d.pipelines.registration.CorrespondenceCheckerBasedOnEdgeLength(0.9), o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(fv * 1.5)],
                o3d.pipelines.registration.RANSACConvergenceCriteria(400000, 0.999))
            T0 = np.asarray(r.transformation)
            emit("auto_done", method="ransac", fitness=round(r.fitness, 3), rmse=round(r.inlier_rmse, 2))
    T = T0; fit = rmse = None
    auto_pairs = []
    final_corr = None
    if not a.no_icp:
        # coarse pass on a light cloud (seconds), then a tight pass on the working cloud with few iterations
        emit("icp", msg="fine adjustment")
        Bc, Mc = B.voxel_down_sample(max(a.voxel * 2.5, 2.0)), M.voxel_down_sample(max(a.voxel * 2.5, 2.0))
        res = o3d.pipelines.registration.registration_icp(Mc, Bc, a.max_dist, T0, o3d.pipelines.registration.TransformationEstimationPointToPlane(),
                                                          o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=40))
        T = np.asarray(res.transformation); fit, rmse = float(res.fitness), float(res.inlier_rmse)
        final_corr = getattr(res, "correspondence_set", None)
        emit("icp2", msg="final touch")
        res2 = o3d.pipelines.registration.registration_icp(M, B, max(a.voxel * 1.5, 1.0), T, o3d.pipelines.registration.TransformationEstimationPointToPlane(),
                                                           o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=15))
        if res2.fitness > 0.2:
            T = np.asarray(res2.transformation); fit, rmse = float(res2.fitness), float(res2.inlier_rmse)
            final_corr = getattr(res2, "correspondence_set", None)
    # Keep a small, deterministic sample of the final source/target correspondences
    # so the UI can show what Auto actually used without bloating the result file.
    if final_corr is not None and len(final_corr):
        try:
            corr=np.asarray(final_corr, dtype=np.int64).reshape(-1, 2)
            pair_count=max(0, min(48, int(a.auto_pairs)))
            take=np.linspace(0, len(corr)-1, min(pair_count, len(corr))).astype(np.int64) if pair_count else []
            for j in take:
                mi, bi = int(corr[j, 0]), int(corr[j, 1])
                if 0 <= mi < len(M.points) and 0 <= bi < len(B.points):
                    mp0=np.asarray(M.points[mi], dtype=float); bp0=np.asarray(B.points[bi], dtype=float)
                    if np.isfinite(mp0).all() and np.isfinite(bp0).all():
                        auto_pairs.append([bp0.tolist(), mp0.tolist()])
        except Exception:
            auto_pairs=[]
    metrics = _registration_metrics(T, M, B, final_corr, max(a.voxel * 1.5, 1.0), icp_fitness=fit)
    out = {"matrix": T.tolist(), "fitness": fit, "rmse": rmse, "auto_pairs": auto_pairs,
           "pair_error_before": pair_err(pairs, np.eye(4)), "pair_error_after": pair_err(pairs, T),
           "units": "mm", "frame_convention": "T_base_moving", "reason_codes": []}
    out.update(metrics)
    if landmark_metrics:
        final_residuals = landmark_residuals_mm(pairs, T)
        out.update(landmark_metrics)
        out["landmark_residuals_after_mm"] = [float(x) for x in final_residuals]
        if len(final_residuals) and float(np.max(final_residuals)) > max(a.max_dist, 1.0):
            out["reason_codes"].append("inconsistent_landmarks")
    if fit is not None and fit < 0.3:
        out["reason_codes"].append("low_overlap")
    if rmse is not None and rmse > 1.5:
        out["reason_codes"].append("high_residual")
    sym = out.get("sym_fitness")
    if sym is not None and sym < 0.2:
        out["reason_codes"].append("low_sym_overlap")
    na = out.get("normal_agreement")
    if na is not None and na < 0.0:
        out["reason_codes"].append("poor_normal_agreement")
    t_mm = out.get("translation_mm"); r_deg = out.get("rotation_deg")
    if (t_mm is not None and t_mm > 2000) or (r_deg is not None and r_deg > 170):
        out["reason_codes"].append("suspect_transform")
    _dump_json(a.out, out); emit("result", fitness=(round(fit, 3) if fit is not None else None), rmse=(round(rmse, 2) if rmse is not None else None), icp=not a.no_icp)
    return 0

if __name__ == "__main__":
    try: sys.exit(main())
    except MemoryError: emit("error", msg="out of memory"); sys.exit(3)
    except Exception as e: emit("error", msg=str(e)); sys.exit(1)
