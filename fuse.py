#!/usr/bin/env python3
# PC-side fusion for PointYoink: raw MIRACO depth frames -> fused mesh, on your PC.
# Memory-capped subprocess. Integrates frames one at a time into a TSDF voxel grid
# (bounded memory even for hundreds of frames), then extracts a triangle mesh.
#   python3 fuse.py --frames <cache dir> --calib <param/Pl.bin> --out mesh.ply [--voxel 0.3] [--gpu]
# Formats (verified against the scanner's own fuse.ply to 0.13 mm):
#   .dph  = WxH uint16 depth, depth_mm = raw * 0.1
#   .inf  = 16-byte header + 4x4 float64 camera->world pose (+ a second 4x4 we don't need)
#   Pl.bin = 10 float32: [_, fx, _, cx, _, fy, cy, _, _, 1]
#   camera frame = OpenCV frame with Y and Z flipped: p_scanner = diag(1,-1,-1) . p_opencv
import sys, os, argparse, resource, json, struct, glob

MEM_CAP_GB = float(os.environ.get("POINTYOINK_MEM_CAP_GB", "12"))

def apply_mem_cap():
    # CPU path only: CUDA reserves huge virtual address ranges, so RLIMIT_AS would
    # make cudaMalloc fail with a bogus "out of memory". VRAM is the GPU's own bound.
    try:
        cap = int(MEM_CAP_GB * 1024**3); resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except Exception: pass

def emit(stage, **kw): print("STAGE %s %s" % (stage, json.dumps(kw)), flush=True)

def read_calib(path):
    v = struct.unpack("<10f", open(path, "rb").read())
    return v[1], v[5], v[3], v[6]            # fx, fy, cx, cy

def read_pose(path):
    import numpy as np
    return np.array(struct.unpack_from("<16d", open(path, "rb").read(), 16)).reshape(4, 4)

def read_global_poses(path):
    """The scanner's globally registered poses (cache/global_register_pose.pose): int32 count, then per
    frame an int32 pass number, int32 frame number and a 4x4 float64 camera->world pose (mm). These are
    the poses the device itself fuses with; after a Resume Scan the second pass sits tens of mm away from
    its own per-frame odometry, so building from the .inf poses alone leaves ghost surfaces."""
    import numpy as np
    b = open(path, "rb").read(); n = struct.unpack_from("<i", b, 0)[0]; out = {}
    for i in range(n):
        off = 4 + i * 136
        if off + 136 > len(b): break
        seq, frame = struct.unpack_from("<ii", b, off)
        out[(seq, frame)] = np.array(struct.unpack_from("<16d", b, off + 8)).reshape(4, 4)
    return out

def frame_key(dph):
    import re
    m = re.search(r"frame_(\d+)_(\d+)\.dph$", dph)
    return (int(m.group(1)), int(m.group(2))) if m else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames"); ap.add_argument("--calib")
    ap.add_argument("--set", action="append", default=[], help="frames_dir,calib,transform.json[,plane.json] (repeat to fuse several aligned scans into one model; transform = 4x4 in mm, scan -> base; plane = the scan's saved base cut, everything on its far side is dropped)")
    ap.add_argument("--plane", default=None, help="base cut for a single --frames run: plane.json from Remove base")
    ap.add_argument("--out", required=True)
    ap.add_argument("--voxel", type=float, default=0.3, help="voxel size in mm (detail; smaller = finer, more RAM)")
    ap.add_argument("--width", type=int, default=800); ap.add_argument("--height", type=int, default=600)
    ap.add_argument("--depth-scale", type=float, default=0.1, help="mm per raw unit")
    ap.add_argument("--max-depth", type=float, default=600.0, help="mm; ignore farther pixels")
    ap.add_argument("--every", type=int, default=1, help="use every Nth frame")
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--poses", default="auto", help="global pose table: auto (use <frames>/global_register_pose.pose if present), none, or a path")
    ap.add_argument("--min-weight", type=float, default=1.0, help="GPU: drop voxels seen fewer than N times (raise to cut noise)")
    a = ap.parse_args()
    if not a.gpu: apply_mem_cap()

    import numpy as np, open3d as o3d
    # one or more frame sets: (frames dir, calib, 4x4 transform in mm or None)
    sets = []
    if a.set:
        for spec in a.set:
            parts = spec.split(","); T = None
            if len(parts) > 2 and parts[2]:
                T = np.array(json.load(open(parts[2]))["matrix"] if parts[2].endswith(".json") else json.loads(parts[2]), dtype=np.float64).reshape(4, 4)
            P = json.load(open(parts[3])) if len(parts) > 3 and parts[3] else None
            sets.append((parts[0], parts[1], T, P))
    elif a.frames and a.calib: sets.append((a.frames, a.calib, None, json.load(open(a.plane)) if a.plane else None))
    else: emit("error", msg="give --frames and --calib, or --set"); return 2
    fx, fy, cx, cy = read_calib(sets[0][1])
    emit("calib", fx=round(fx, 2), fy=round(fy, 2), cx=round(cx, 2), cy=round(cy, 2))
    jobs = []      # (dph, gp dict, T mm, calib tuple, plane)
    for frames, calib, T, P in sets:
        dphs = sorted(glob.glob(os.path.join(frames, "*.dph")))[::max(1, a.every)]
        if not dphs: emit("error", msg="no .dph frames in " + frames); return 2
        gp = {}
        pose_path = os.path.join(frames, "global_register_pose.pose") if a.poses == "auto" else a.poses
        if a.poses != "none" and os.path.exists(pose_path):
            try: gp = read_global_poses(pose_path)
            except Exception as e: emit("warn", msg="global poses unreadable: %r" % (e,)); gp = {}
        cal = read_calib(calib)
        emit("frames", count=len(dphs), global_poses=len(gp), set=os.path.basename(os.path.dirname(frames)))
        jobs += [(d, gp, T, cal, P) for d in dphs]
    dphs = [j[0] for j in jobs]

    W, H = a.width, a.height
    # depth in meters = raw * depth_scale(mm) / 1000  ->  Open3D divides by depth_scale
    o3d_depth_scale = 1000.0 / a.depth_scale
    depth_trunc_m = a.max_depth / 1000.0
    voxel_m = a.voxel / 1000.0
    F = np.diag([1.0, -1.0, -1.0, 1.0])       # OpenCV -> scanner camera frame

    intr = o3d.camera.PinholeCameraIntrinsic(W, H, fx, fy, cx, cy)
    dev = o3d.core.Device("CUDA:0") if (a.gpu and o3d.core.cuda.is_available()) else o3d.core.Device("CPU:0")
    emit("device", device=str(dev))

    use_t = a.gpu and o3d.core.cuda.is_available()
    if use_t:
        vbg = o3d.t.geometry.VoxelBlockGrid(attr_names=("tsdf", "weight"), attr_dtypes=(o3d.core.float32, o3d.core.float32),
                                            attr_channels=((1), (1)), voxel_size=voxel_m, block_resolution=16,
                                            block_count=100000, device=dev)
        K = o3d.core.Tensor(intr.intrinsic_matrix, o3d.core.float64)
    else:
        vol = o3d.pipelines.integration.ScalableTSDFVolume(
            voxel_length=voxel_m, sdf_trunc=voxel_m * 4,
            color_type=o3d.pipelines.integration.TSDFVolumeColorType.NoColor)

    n = 0; cur_cal = None; grid = None
    def cull_below_plane(d, pose_mm, cal, P):
        """Zero the depth pixels that sit on the removed side of this scan's base plane (plane in the scan's own mm frame)."""
        nonlocal grid
        fx, fy, cx, cy = cal
        if grid is None:
            u, v = np.meshgrid(np.arange(W, dtype=np.float32), np.arange(H, dtype=np.float32)); grid = (u, v)
        u, v = grid; z = d.astype(np.float32) * a.depth_scale          # mm, OpenCV camera frame
        X = (u - cx) * z / fx; Y = (v - cy) * z / fy
        M = pose_mm @ F                                                # camera(OpenCV) -> scan world, mm
        h = (M[0, 0] * X + M[0, 1] * Y + M[0, 2] * z + M[0, 3]) * P["n"][0] + (M[1, 0] * X + M[1, 1] * Y + M[1, 2] * z + M[1, 3]) * P["n"][1] \
            + (M[2, 0] * X + M[2, 1] * Y + M[2, 2] * z + M[2, 3]) * P["n"][2]
        margin = float(P.get("margin", 1.0))
        bad = (h < P["d"] + margin) if P.get("keep_above", True) else (h > P["d"] - margin)
        d[bad & (d > 0)] = 0
        return d
    for i, (dph, gp, T, cal, P) in enumerate(jobs):
        inf = dph[:-4] + ".inf"
        if not os.path.exists(inf): continue
        if cal != cur_cal:                        # each scan can carry its own calibration
            cur_cal = cal; fx, fy, cx, cy = cal
            if use_t: K = o3d.core.Tensor([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], o3d.core.float64)
            else: intr = o3d.camera.PinholeCameraIntrinsic(W, H, fx, fy, cx, cy)
        d = np.frombuffer(open(dph, "rb").read(), dtype=np.uint16).reshape(H, W).copy()
        d[d * a.depth_scale > a.max_depth] = 0
        pose = gp.get(frame_key(dph))            # camera->world (scanner frame), translation in mm
        if pose is None: pose = read_pose(inf)
        else: pose = pose.copy()
        if P is not None: d = cull_below_plane(d, pose, cal, P)      # drop the table before it ever reaches the volume
        if T is not None: pose = T @ pose         # this scan's frame -> the base scan's frame (mm)
        pose[:3, 3] /= 1000.0                     # -> meters, to match the depth units
        extr = np.linalg.inv(pose @ F)            # world->camera (OpenCV frame)
        if use_t:
            dimg = o3d.t.geometry.Image(o3d.core.Tensor(d)).to(dev)
            ext = o3d.core.Tensor(extr, o3d.core.float64)
            coords = vbg.compute_unique_block_coordinates(dimg, K, ext, o3d_depth_scale, depth_trunc_m)
            vbg.integrate(coords, dimg, K, ext, o3d_depth_scale, depth_trunc_m)
        else:
            dimg = o3d.geometry.Image(d)
            rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
                o3d.geometry.Image(np.zeros((H, W, 3), np.uint8)), dimg,
                depth_scale=o3d_depth_scale, depth_trunc=depth_trunc_m, convert_rgb_to_intensity=False)
            vol.integrate(rgbd, intr, extr)
        n += 1
        if n % 10 == 0 or n == len(dphs):
            emit("integrate", done=n, total=len(dphs))

    emit("extract")
    if use_t:
        mesh = vbg.extract_triangle_mesh(weight_threshold=a.min_weight).to_legacy()
    else:
        mesh = vol.extract_triangle_mesh()
    mesh.compute_vertex_normals()
    # back to mm for the rest of the pipeline
    mesh.scale(1000.0, center=(0, 0, 0))
    o3d.io.write_triangle_mesh(a.out, mesh, write_ascii=False)
    emit("done", verts=len(mesh.vertices), faces=len(mesh.triangles),
         mb=round(os.path.getsize(a.out) / 1048576, 1))
    return 0

if __name__ == "__main__":
    try: sys.exit(main())
    except MemoryError:
        emit("error", msg="out of memory - try a larger --voxel or --every 2"); sys.exit(3)
    except Exception as e:
        emit("error", msg=str(e)); sys.exit(1)
