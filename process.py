#!/usr/bin/env python3
# Memory-safe mesh processor for PointYoink. Runs as a SUBPROCESS with a hard
# address-space cap so a pathological mesh dies alone instead of OOM-ing the box.
#   python3 process.py <in.ply> <out.ply> [--decimate N] [--base-remove] [--isolate] [--clean]
# Order matters: DECIMATE FIRST (cheap), then topology ops on the small mesh.
import sys, os, argparse, resource, json

# --- hard memory cap: contain any blowup to this process ---
MEM_CAP_GB = float(os.environ.get("POINTYOINK_MEM_CAP_GB", "10"))
try:
    cap = int(MEM_CAP_GB * 1024**3)
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
except Exception as e:
    print("MEMCAP_WARN %s" % e, flush=True)

def emit(stage, **kw):
    print("STAGE %s %s" % (stage, json.dumps(kw)), flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile"); ap.add_argument("outfile")
    ap.add_argument("--decimate", type=int, default=0, help="target face count (0=off)")
    ap.add_argument("--base-remove", action="store_true")
    ap.add_argument("--isolate", action="store_true")
    ap.add_argument("--plane-thresh", type=float, default=0, help="mm; 0=auto from bbox")
    ap.add_argument("--clean", action="store_true", help="dedupe, drop small pieces, optional hole fill, smooth, optional simplify")
    # The scanner's own editing knobs (see dev/design/device/SCANNER-EDIT-OPTIONS.md); defaults match its Mesh panel.
    ap.add_argument("--isolation-rate", type=float, default=15.0, help="drop pieces smaller than this %% of the largest one (100 = keep only the largest)")
    ap.add_argument("--fill-holes", action="store_true", help="fill small holes (the scanner has this off by default)")
    ap.add_argument("--smooth-times", type=int, default=3, help="smoothing passes (scanner default 3; 0 = off)")
    ap.add_argument("--simplify-pct", type=float, default=0, help="keep this %% of faces after cleaning (0 = keep all; the scanner's Simplify uses 40)")
    a = ap.parse_args()

    import numpy as np, trimesh
    emit("load", file=os.path.basename(a.infile))
    m = trimesh.load(a.infile, force="mesh")
    emit("loaded", verts=len(m.vertices), faces=len(m.faces))

    # 1) DECIMATE FIRST — turns the memory-heavy topology ops that follow into cheap ones
    if a.decimate and len(m.faces) > a.decimate:
        import fast_simplification
        v, f = fast_simplification.simplify(m.vertices, m.faces, target_count=a.decimate)
        m = trimesh.Trimesh(v, f, process=False)
        emit("decimated", faces=len(m.faces))

    if a.clean:   # tidy the topology before the connectivity pass
        try: m.merge_vertices()
        except Exception: pass
        try:
            m.update_faces(m.nondegenerate_faces()); m.update_faces(m.unique_faces()); m.remove_unreferenced_vertices()
        except Exception: pass
        emit("deduped", faces=len(m.faces))

    # 2) BASE REMOVAL — RANSAC the dominant plane (the table/turntable) and cut it away
    if a.base_remove:
        V = np.asarray(m.vertices)
        diag = float(np.linalg.norm(V.max(0) - V.min(0)))
        thresh = a.plane_thresh if a.plane_thresh > 0 else diag * 0.01  # ~1% of bbox diagonal
        rng = np.random.default_rng(0)
        best_inliers, best_plane = 0, None
        S = V[rng.choice(len(V), min(60000, len(V)), replace=False)]  # sample for speed
        for _ in range(200):
            p = S[rng.choice(len(S), 3, replace=False)]
            n = np.cross(p[1] - p[0], p[2] - p[0])
            ln = np.linalg.norm(n)
            if ln < 1e-9:
                continue
            n = n / ln; d = -n.dot(p[0])
            inl = int(np.sum(np.abs(S.dot(n) + d) < thresh))
            if inl > best_inliers:
                best_inliers, best_plane = inl, (n, d)
        if best_plane is not None:
            n, d = best_plane
            sd = V.dot(n) + d
            near = np.abs(sd) < thresh                       # vertices in the flat plane band
            # Remove ONLY the flat band (the table), plus a thin skirt just past it on the
            # non-object side. This severs the object from the table without slicing through
            # it; the object then survives whole as the largest connected component.
            obj_side = np.sign(np.median(sd[~near])) if np.any(~near) else 1.0
            drop_v = near | ((sd * obj_side) < -thresh)      # band + everything on the far side
            keep_f = ~drop_v[m.faces].any(axis=1)            # drop a face if ANY vertex is dropped
            emit("base_plane", inlier_pct=round(100*best_inliers/len(S), 1),
                 thresh_mm=round(thresh, 2), removed_faces=int((~keep_f).sum()))
            m.update_faces(keep_f); m.remove_unreferenced_vertices()

    # 3) ISOLATE — drop floating junk / table remnants. --isolate and --base-remove keep only the
    # largest connected piece; --clean keeps every piece at least --isolation-rate % of the largest
    # (the scanner's "Isolation rate", default 15%), so an object scanned in parts survives.
    # Use connected_components + a face mask instead of .split() (which builds every
    # submesh and calls fill_holes) — faster, lighter, and no repair dependency.
    if a.isolate or a.base_remove or a.clean:
        comps = trimesh.graph.connected_components(m.face_adjacency, min_len=1)
        if len(comps) > 1:
            big = max(len(c) for c in comps)
            rate = a.isolation_rate if (a.clean and not (a.isolate or a.base_remove)) else 100.0
            keep = [c for c in comps if len(c) >= big * min(100.0, max(0.0, rate)) / 100.0]
            mask = np.zeros(len(m.faces), dtype=bool)
            for c in keep: mask[c] = True
            m.update_faces(mask); m.remove_unreferenced_vertices()
            emit("isolated", pieces=len(comps), kept=len(keep), faces=len(m.faces))
        else:
            emit("isolated", pieces=len(comps), kept=len(comps), faces=len(m.faces))

    if a.clean:
        if a.fill_holes:
            try: m.fill_holes()
            except Exception: pass
        if a.smooth_times > 0:
            try: trimesh.smoothing.filter_humphrey(m, iterations=int(a.smooth_times))
            except Exception: pass
        if 0 < a.simplify_pct < 100:
            import fast_simplification
            v, f = fast_simplification.simplify(np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.int32), target_reduction=1.0 - a.simplify_pct / 100.0)
            m = trimesh.Trimesh(v, f, process=False)
        emit("cleaned", faces=len(m.faces))

    m.export(a.outfile)
    emit("done", out=os.path.basename(a.outfile), faces=len(m.faces),
         mb=round(os.path.getsize(a.outfile) / 1048576, 1))

if __name__ == "__main__":
    main()
