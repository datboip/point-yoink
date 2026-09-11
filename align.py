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

def emit(stage, **kw): print("STAGE %s %s" % (stage, json.dumps(kw)), flush=True)

def kabsch(P, Q):
    """Rigid transform taking points P onto Q (both Nx3): returns 4x4."""
    P = np.asarray(P, float); Q = np.asarray(Q, float); cp, cq = P.mean(0), Q.mean(0)
    H = (P - cp).T @ (Q - cq); U, S, Vt = np.linalg.svd(H); d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1, 1, d]) @ U.T; T = np.eye(4); T[:3, :3] = R; T[:3, 3] = cq - R @ cp; return T

def sample(path, n):
    import trimesh
    m = trimesh.load(path, force="mesh")
    if len(m.faces) > 800000:
        import fast_simplification
        v, f = fast_simplification.simplify(np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.int32), target_count=800000)
        m = trimesh.Trimesh(v, f, process=False)
    pts, fidx = trimesh.sample.sample_surface(m, n)
    return np.asarray(pts, np.float64), np.asarray(m.face_normals[fidx], np.float64)

def cloud(pts, nrm, voxel):
    import open3d as o3d
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts)); pc.normals = o3d.utility.Vector3dVector(nrm)
    return pc.voxel_down_sample(voxel)

def pair_err(pairs, T):
    if not pairs: return None
    B = np.array([p[0] for p in pairs], float); M = np.array([p[1] for p in pairs], float)
    Mh = (M @ T[:3, :3].T) + T[:3, 3]; return float(np.sqrt(((Mh - B) ** 2).sum(1)).mean())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--moving", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--pairs"); ap.add_argument("--auto", action="store_true"); ap.add_argument("--no-icp", action="store_true")
    ap.add_argument("--voxel", type=float, default=1.0, help="mm, working resolution for ICP")
    ap.add_argument("--max-dist", type=float, default=6.0, help="mm, ICP correspondence distance")
    a = ap.parse_args()
    pairs = json.load(open(a.pairs))["pairs"] if a.pairs else []
    T0 = np.eye(4)
    if pairs and len(pairs) >= 3:
        T0 = kabsch([p[1] for p in pairs], [p[0] for p in pairs]); emit("pairs", n=len(pairs), error_mm=round(pair_err(pairs, T0), 2))
    elif not a.auto:
        emit("error", msg="need 3 or more point pairs, or --auto"); return 2
    emit("load", msg="reading both scans")
    bp, bn = sample(a.base, 250000); mp, mn = sample(a.moving, 250000)
    try:
        import open3d as o3d
    except Exception:
        json.dump({"matrix": T0.tolist(), "fitness": None, "rmse": None, "pair_error_before": pair_err(pairs, np.eye(4)), "pair_error_after": pair_err(pairs, T0),
                   "note": "Open3D not installed: rough fit from the points only"}, open(a.out, "w"))
        emit("result", fitness=None, rmse=None, icp=False); return 0
    B = cloud(bp, bn, a.voxel); M = cloud(mp, mn, a.voxel)
    if a.auto and len(pairs) < 3:
        emit("auto", msg="looking for a rough fit by itself")
        fv = a.voxel * 3
        Bd, Md = B.voxel_down_sample(fv), M.voxel_down_sample(fv)
        fb = o3d.pipelines.registration.compute_fpfh_feature(Bd, o3d.geometry.KDTreeSearchParamHybrid(radius=fv * 5, max_nn=100))
        fm = o3d.pipelines.registration.compute_fpfh_feature(Md, o3d.geometry.KDTreeSearchParamHybrid(radius=fv * 5, max_nn=100))
        r = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
            Md, Bd, fm, fb, True, fv * 1.5, o3d.pipelines.registration.TransformationEstimationPointToPoint(False), 3,
            [o3d.pipelines.registration.CorrespondenceCheckerBasedOnEdgeLength(0.9), o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(fv * 1.5)],
            o3d.pipelines.registration.RANSACConvergenceCriteria(400000, 0.999))
        T0 = np.asarray(r.transformation); emit("auto_done", fitness=round(r.fitness, 3), rmse=round(r.inlier_rmse, 2))
    T = T0; fit = rmse = None
    if not a.no_icp:
        # coarse pass on a light cloud (seconds), then a tight pass on the working cloud with few iterations
        emit("icp", msg="fine adjustment")
        Bc, Mc = B.voxel_down_sample(max(a.voxel * 2.5, 2.0)), M.voxel_down_sample(max(a.voxel * 2.5, 2.0))
        res = o3d.pipelines.registration.registration_icp(Mc, Bc, a.max_dist, T0, o3d.pipelines.registration.TransformationEstimationPointToPlane(),
                                                          o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=40))
        T = np.asarray(res.transformation); fit, rmse = float(res.fitness), float(res.inlier_rmse)
        emit("icp2", msg="final touch")
        res2 = o3d.pipelines.registration.registration_icp(M, B, max(a.voxel * 1.5, 1.0), T, o3d.pipelines.registration.TransformationEstimationPointToPlane(),
                                                           o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=15))
        if res2.fitness > 0.2: T = np.asarray(res2.transformation); fit, rmse = float(res2.fitness), float(res2.inlier_rmse)
    out = {"matrix": T.tolist(), "fitness": fit, "rmse": rmse, "pair_error_before": pair_err(pairs, np.eye(4)), "pair_error_after": pair_err(pairs, T)}
    json.dump(out, open(a.out, "w")); emit("result", fitness=(round(fit, 3) if fit is not None else None), rmse=(round(rmse, 2) if rmse is not None else None), icp=not a.no_icp)
    return 0

if __name__ == "__main__":
    try: sys.exit(main())
    except MemoryError: emit("error", msg="out of memory"); sys.exit(3)
    except Exception as e: emit("error", msg=str(e)); sys.exit(1)
