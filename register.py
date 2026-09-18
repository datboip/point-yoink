#!/usr/bin/env python3
# Global registration for a raw MIRACO scan: what the scanner does when it fuses, and what a scan that was
# never fused on the device is missing. The per-frame .inf poses are live tracking and drift over a long pass.
# Here the scan is fused in short fragments with those poses (drift is small inside a fragment), the fragments
# are registered to each other with ICP (neighbours, plus any pair that overlaps in space: the loop closures),
# all fragment poses are optimised together (pose graph), and a per-frame pose table is written in the
# scanner's own format so fuse.py picks it up. Memory-capped subprocess; GPU when available.
#   python3 register.py --frames <cache dir> --calib <param/Pl.bin> [--fragment 30] [--every 1] [--gpu]
# Output: <cache dir>/pointyoink_register_pose.pose (never overwrites the scanner's global_register_pose.pose)
import sys, os, argparse, json, glob, struct, time, resource
MEM_CAP_GB = float(os.environ.get("POINTYOINK_MEM_CAP_GB", "12"))
def apply_mem_cap():
    try:
        cap = int(MEM_CAP_GB * 1024**3); resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except Exception: pass
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fuse import read_calib, read_pose, frame_key, read_global_poses

def emit(stage, **kw): print("STAGE %s %s" % (stage, json.dumps(kw)), flush=True)

def frame_size(frames, dphs):
    try:
        sp = json.load(open(os.path.join(os.path.dirname(os.path.abspath(frames)), "property.rvproj"))).get("scan_param", {})
        w, h = int(sp.get("depth_width") or 0), int(sp.get("depth_height") or 0)
        if w and h: return w, h
    except Exception: pass
    px = os.path.getsize(dphs[0]) // 2
    for w, h in ((800, 600), (400, 300), (640, 480)):
        if w * h == px: return w, h
    return 800, 600

def write_pose_table(path, entries):
    """entries: [(pass, frame, 4x4 mm)] in the scanner's layout: int32 count; per entry int32 pass, int32 frame, 16 float64."""
    with open(path, "wb") as f:
        f.write(struct.pack("<i", len(entries)))
        for ps, fr, M in entries:
            f.write(struct.pack("<ii", ps, fr)); f.write(struct.pack("<16d", *np.asarray(M, np.float64).reshape(-1)))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True); ap.add_argument("--calib", required=True)
    ap.add_argument("--fragment", type=int, default=30, help="frames per fragment")
    ap.add_argument("--every", type=int, default=1); ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--voxel", type=float, default=1.0, help="mm, fragment fusion and ICP resolution")
    ap.add_argument("--depth-scale", type=float, default=0.1); ap.add_argument("--max-depth", type=float, default=600.0)
    ap.add_argument("--loop-dist", type=float, default=80.0, help="mm, fragments whose centres are closer than this are tried as loop closures")
    ap.add_argument("--out", default=None)
    ap.add_argument("--loops", action="store_true", help="also try loop closures (off by default: measured against the scanner's own table, loop closures on a rim-shaped part slid the scan 40 mm off; neighbour refinement within 2 mm gave a small gain)")
    ap.add_argument("--no-loops", action="store_true", help="(default) neighbours only")
    ap.add_argument("--loop-fitness", type=float, default=0.3, help="accept a loop closure only above this overlap share")
    ap.add_argument("--loop-max-shift", type=float, default=1e9, help="mm: reject a loop closure whose fit moves the fragment further than this from where the tracking put it")
    ap.add_argument("--loop-pref", type=float, default=0.5, help="how much the optimiser trusts loop closures against the tracking chain (0..1)")
    ap.add_argument("--edge-inverse", action="store_true", help="diagnostic: store inverse transforms on the edges")
    ap.add_argument("--chain-only", action="store_true", help="diagnostic: compose the neighbour fits by hand, no graph optimisation")
    ap.add_argument("--adj-max-shift", type=float, default=2.0, help="mm: a neighbour fit that moves further than this from the tracking is not trusted (the tracking is kept)")
    a = ap.parse_args()
    import open3d as o3d
    if not (a.gpu and o3d.core.cuda.is_available()): apply_mem_cap()   # the CPU path is capped even when --gpu was asked for
    t0 = time.time()
    dphs = sorted(glob.glob(os.path.join(a.frames, "*.dph")))[::max(1, a.every)]
    dphs = [d for d in dphs if os.path.exists(d[:-4] + ".inf")]
    if len(dphs) < 2 * a.fragment: emit("error", msg="too few frames to register (%d)" % len(dphs)); return 2
    W, H = frame_size(a.frames, dphs)
    fx, fy, cx, cy = read_calib(a.calib)
    if W != 800: k = W / 800.0; fx, fy, cx, cy = fx * k, fy * k, cx * k, cy * k
    F = np.diag([1.0, -1.0, -1.0, 1.0])
    intr = o3d.camera.PinholeCameraIntrinsic(W, H, fx, fy, cx, cy)
    use_t = a.gpu and o3d.core.cuda.is_available(); dev = o3d.core.Device("CUDA:0") if use_t else o3d.core.Device("CPU:0")
    K = o3d.core.Tensor(intr.intrinsic_matrix, o3d.core.float64); vox_m = a.voxel / 1000.0
    odo = [read_pose(d[:-4] + ".inf") for d in dphs]                      # camera->world, mm (live tracking)
    for M in odo: M[:3, 3] /= 1000.0                                       # metres inside
    emit("frames", count=len(dphs), size="%dx%d" % (W, H), device=str(dev))
    # ---- fragments: fuse each with its own odometry, relative to its first frame ----
    frags = []       # (first index, cloud (metres, in fragment frame), pose of first frame in world)
    n_frag = (len(dphs) + a.fragment - 1) // a.fragment
    for fi in range(n_frag):
        idx = list(range(fi * a.fragment, min(len(dphs), (fi + 1) * a.fragment)))
        base = odo[idx[0]]; base_inv = np.linalg.inv(base)
        if use_t:
            vbg = o3d.t.geometry.VoxelBlockGrid(attr_names=("tsdf", "weight"), attr_dtypes=(o3d.core.float32, o3d.core.float32), attr_channels=((1), (1)),
                                                voxel_size=vox_m, block_resolution=8, block_count=20000, device=dev)
        else:
            vol = o3d.pipelines.integration.ScalableTSDFVolume(voxel_length=vox_m, sdf_trunc=vox_m * 4, color_type=o3d.pipelines.integration.TSDFVolumeColorType.NoColor)
        for i in idx:
            raw = np.frombuffer(open(dphs[i], "rb").read(), dtype=np.uint16)
            if raw.size != W * H: continue
            d = raw.reshape(H, W).copy(); d[d * a.depth_scale > a.max_depth] = 0
            pose = base_inv @ odo[i]                                       # this frame in the fragment's frame
            extr = np.linalg.inv(pose @ F)
            if use_t:
                dimg = o3d.t.geometry.Image(o3d.core.Tensor(d)).to(dev); ext = o3d.core.Tensor(extr, o3d.core.float64)
                coords = vbg.compute_unique_block_coordinates(dimg, K, ext, 1000.0 / a.depth_scale, a.max_depth / 1000.0, 4.0)
                vbg.integrate(coords, dimg, K, ext, 1000.0 / a.depth_scale, a.max_depth / 1000.0, 4.0)
            else:
                rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(o3d.geometry.Image(np.zeros((H, W, 3), np.uint8)), o3d.geometry.Image(d),
                                                                          depth_scale=1000.0 / a.depth_scale, depth_trunc=a.max_depth / 1000.0, convert_rgb_to_intensity=False)
                vol.integrate(rgbd, intr, extr)
        pc = (vbg.extract_point_cloud(weight_threshold=2.0).to_legacy() if use_t else vol.extract_point_cloud())
        pc = pc.voxel_down_sample(vox_m)
        if not pc.has_normals(): pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=vox_m * 4, max_nn=30))
        frags.append((idx[0], pc, base))
        if fi % 5 == 0 or fi == n_frag - 1: emit("fragments", done=fi + 1, total=n_frag)
    # ---- pose graph: nodes = fragments at their odometry pose; edges = ICP between pairs ----
    pg = o3d.pipelines.registration.PoseGraph()
    for _, _, base in frags: pg.nodes.append(o3d.pipelines.registration.PoseGraphNode(base.copy()))
    centres = [ (base @ np.r_[np.asarray(pc.points).mean(0), 1.0])[:3] for _, pc, base in frags ]
    def icp(i, j, init):
        src, tgt = frags[i][1], frags[j][1]
        res = o3d.pipelines.registration.registration_icp(src, tgt, vox_m * 6, init, o3d.pipelines.registration.TransformationEstimationPointToPlane(),
                                                          o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=30))
        res = o3d.pipelines.registration.registration_icp(src, tgt, vox_m * 2, res.transformation, o3d.pipelines.registration.TransformationEstimationPointToPlane(),
                                                          o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=20))
        info = o3d.pipelines.registration.get_information_matrix_from_point_clouds(src, tgt, vox_m * 2, res.transformation)
        return res, info
    n_edges = 0; n_loops = 0; tried = 0; adj_T = {}
    for i in range(len(frags)):
        for j in range(i + 1, len(frags)):
            adjacent = (j == i + 1)
            if not adjacent and ((not a.loops) or np.linalg.norm(centres[i] - centres[j]) * 1000.0 > a.loop_dist): continue
            init = np.linalg.inv(frags[j][2]) @ frags[i][2]                # i in j's frame, from odometry
            tried += 1
            res, info = icp(i, j, init)
            fitT = np.asarray(res.transformation)
            if adjacent:
                shift_adj = np.linalg.norm((fitT @ np.linalg.inv(init))[:3, 3]) * 1000.0
                if shift_adj > a.adj_max_shift: fitT = init                        # a fit that slid: keep the tracking
                adj_T[(i, j)] = fitT
            T = np.linalg.inv(fitT) if a.edge_inverse else fitT
            if adjacent:
                pg.edges.append(o3d.pipelines.registration.PoseGraphEdge(i, j, T, info, uncertain=False)); n_edges += 1
            elif res.fitness > a.loop_fitness and res.inlier_rmse < vox_m * 1.5:
                shift = np.linalg.norm((np.asarray(res.transformation) @ np.linalg.inv(init))[:3, 3]) * 1000.0    # mm the fit moved away from the tracking
                if shift <= a.loop_max_shift:
                    pg.edges.append(o3d.pipelines.registration.PoseGraphEdge(i, j, T, info, uncertain=True)); n_edges += 1; n_loops += 1
        if i % 5 == 0: emit("register", done=i + 1, total=len(frags), loops=n_loops)
    emit("graph", nodes=len(frags), edges=n_edges, loops=n_loops, tried=tried)
    opt = o3d.pipelines.registration.GlobalOptimizationOption(max_correspondence_distance=vox_m * 2, edge_prune_threshold=0.25, preference_loop_closure=a.loop_pref, reference_node=0)
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    if a.chain_only:                                   # node_j = node_i @ inv(T_ij): walk the neighbour fits from fragment 0
        cur = np.asarray(pg.nodes[0].pose).copy()
        for i in range(len(frags) - 1):
            cur = cur @ np.linalg.inv(adj_T[(i, i + 1)]); pg.nodes[i + 1].pose = cur
    else:
        o3d.pipelines.registration.global_optimization(pg, o3d.pipelines.registration.GlobalOptimizationLevenbergMarquardt(),
                                                       o3d.pipelines.registration.GlobalOptimizationConvergenceCriteria(), opt)
    # ---- per-frame poses: optimised fragment pose x the frame's odometry relative to its fragment ----
    entries = []; moved = []
    for fi, (first, pc, base) in enumerate(frags):
        newbase = np.asarray(pg.nodes[fi].pose); base_inv = np.linalg.inv(base)
        moved.append(np.linalg.norm(newbase[:3, 3] - base[:3, 3]) * 1000.0)
        for i in range(first, min(len(dphs), first + a.fragment)):
            M = newbase @ (base_inv @ odo[i]); M = M.copy(); M[:3, 3] *= 1000.0
            key = frame_key(dphs[i]) or (0, i); entries.append((key[0], key[1], M))
    out = a.out or os.path.join(a.frames, "pointyoink_register_pose.pose")
    write_pose_table(out, entries)
    emit("done", out=os.path.basename(out), frames=len(entries), fragments=len(frags), loops=n_loops,
         moved_median_mm=round(float(np.median(moved)), 2), moved_max_mm=round(float(np.max(moved)), 2), secs=round(time.time() - t0, 1))
    return 0

if __name__ == "__main__":
    try: sys.exit(main())
    except MemoryError: emit("error", msg="out of memory"); sys.exit(3)
    except Exception as e: emit("error", msg=str(e)); sys.exit(1)
