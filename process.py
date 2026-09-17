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

def _smooth_fill_faces(m, old_fcount, iters=120):
    """Round flat, ear-clipped hole fills (the faces at/after old_fcount) into the surface. Splits only
    the fill patches' INTERIOR edges - the rim edges shared with the untouched original faces stay whole,
    so watertightness is preserved (no T-junction cracks) - then relaxes the new midpoints toward a smooth
    surface while every original vertex stays put."""
    import numpy as np, trimesh
    from collections import defaultdict
    V = [np.asarray(x, np.float64) for x in m.vertices]; n_orig_v = len(V)
    F = np.asarray(m.faces, np.int64); orig = F[:old_fcount]; fill = F[old_fcount:]
    if len(fill) == 0: return m
    ec = defaultdict(int)
    for a, b, c in fill:
        for x, y in ((a, b), (b, c), (c, a)): ec[(min(x, y), max(x, y))] += 1
    interior = {k for k, v in ec.items() if v == 2}      # edges shared by two fill faces: safe to split
    if not interior: return m
    mid = {}
    def mp(a, b):
        k = (min(a, b), max(a, b))
        if k not in interior: return None
        if k in mid: return mid[k]
        i = len(V); V.append((V[a] + V[b]) * 0.5); mid[k] = i; return i
    nf = []
    for a, b, c in fill:
        a, b, c = int(a), int(b), int(c)
        mab, mbc, mca = mp(a, b), mp(b, c), mp(c, a)
        present = [nm for nm, mm in (('ab', mab), ('bc', mbc), ('ca', mca)) if mm is not None]
        k = len(present)
        if k == 0: nf.append((a, b, c))
        elif k == 3: nf += [(a, mab, mca), (mab, b, mbc), (mca, mbc, c), (mab, mbc, mca)]
        elif k == 1:
            e = present[0]
            if e == 'ab': nf += [(a, mab, c), (mab, b, c)]
            elif e == 'bc': nf += [(b, mbc, a), (mbc, c, a)]
            else: nf += [(c, mca, b), (mca, a, b)]
        else:
            s = set(present)
            if s == {'ab', 'ca'}: nf += [(a, mab, mca), (mab, b, c), (mab, c, mca)]
            elif s == {'ab', 'bc'}: nf += [(b, mbc, mab), (a, mab, mbc), (a, mbc, c)]
            else: nf += [(c, mca, mbc), (a, b, mbc), (a, mbc, mca)]
    V = np.array(V, np.float64); fillf = np.array(nf, int)
    free = np.zeros(len(V), bool); free[n_orig_v:] = True     # only the new midpoints move
    e = np.vstack([fillf[:, [0, 1]], fillf[:, [1, 2]], fillf[:, [2, 0]]]); e = np.vstack([e, e[:, ::-1]])
    deg = np.bincount(e[:, 0], minlength=len(V)).astype(np.float64); deg[deg == 0] = 1.0
    for _ in range(iters):
        acc = np.zeros_like(V); np.add.at(acc, e[:, 0], V[e[:, 1]]); V[free] = (acc / deg[:, None])[free]
    return trimesh.Trimesh(V, np.vstack([orig, fillf]), process=False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile"); ap.add_argument("outfile", nargs="?")
    ap.add_argument("--info", action="store_true", help="print size, counts, pieces and open edges as JSON and exit")
    ap.add_argument("--decimate", type=int, default=0, help="target face count (0=off)")
    ap.add_argument("--base-remove", action="store_true")
    ap.add_argument("--isolate", action="store_true")
    ap.add_argument("--plane-thresh", type=float, default=0, help="mm; 0=auto from bbox")
    ap.add_argument("--clean", action="store_true", help="dedupe, drop small pieces, optional hole fill, smooth, optional simplify")
    # The scanner's own editing knobs (see dev/design/device/SCANNER-EDIT-OPTIONS.md); defaults match its Mesh panel.
    ap.add_argument("--isolation-rate", type=float, default=15.0, help="drop pieces smaller than this %% of the largest one (100 = keep only the largest)")
    ap.add_argument("--fill-holes", action="store_true", help="fill holes (the scanner has this off by default)")
    ap.add_argument("--hole-size", type=float, default=0, help="largest hole to fill, in mm (0 = auto: ~4%% of the bbox diagonal, so real gaps close but an intentionally-open base does not)")
    ap.add_argument("--smooth-times", type=int, default=3, help="smoothing passes (scanner default 3; 0 = off)")
    ap.add_argument("--simplify-pct", type=float, default=0, help="keep this %% of faces after cleaning (0 = keep all; the scanner's Simplify uses 40)")
    a = ap.parse_args()

    import numpy as np, trimesh
    emit("load", file=os.path.basename(a.infile))
    m = trimesh.load(a.infile, force="mesh")
    emit("loaded", verts=len(m.vertices), faces=len(m.faces))
    if a.info:
        ext = (m.bounds[1] - m.bounds[0]) if len(m.vertices) else np.zeros(3)
        try: pieces = len(trimesh.graph.connected_components(m.face_adjacency, min_len=1))
        except Exception: pieces = -1
        try: open_edges = int(len(trimesh.grouping.group_rows(m.edges_sorted, require_count=1)))
        except Exception: open_edges = -1
        emit("info", verts=len(m.vertices), faces=len(m.faces), extent=[round(float(x), 1) for x in ext], pieces=pieces,
             open_edges=open_edges, watertight=bool(m.is_watertight), mb=round(os.path.getsize(a.infile) / 1048576, 1))
        return
    if not a.outfile: print("outfile required unless --info"); sys.exit(2)

    # 1) DECIMATE FIRST - turns the memory-heavy topology ops that follow into cheap ones
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

    # 2) BASE REMOVAL - RANSAC the dominant plane (the table/turntable) and cut it away
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
            drop_frac = float((~keep_f).sum()) / max(1, len(m.faces))
            emit("base_plane", inlier_pct=round(100*best_inliers/len(S), 1),
                 thresh_mm=round(thresh, 2), removed_faces=int((~keep_f).sum()), drop_pct=round(100*drop_frac, 1))
            # Guardrail: the "dominant plane" is only the table if it cuts off a minority. When it would
            # take a big chunk (e.g. a flat face of the object itself, common after a manual Cut base has
            # already dropped the real table), it is slicing the part - skip it rather than remove half.
            if drop_frac > 0.30:
                emit("base_skip", drop_pct=round(100*drop_frac, 1),
                     msg="the biggest flat surface looks like part of the object (would remove %.0f%%); left it in place" % (100*drop_frac))
            else:
                m.update_faces(keep_f); m.remove_unreferenced_vertices()

    # 3) ISOLATE - drop floating junk / table remnants. --isolate and --base-remove keep only the
    # largest connected piece; --clean keeps every piece at least --isolation-rate % of the largest
    # (the scanner's "Isolation rate", default 15%), so an object scanned in parts survives.
    # Use connected_components + a face mask instead of .split() (which builds every
    # submesh and calls fill_holes) - faster, lighter, and no repair dependency.
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

    warnings = []   # requested steps that silently failed - reported at 'done' so the app never claims a clean run it didn't do
    if a.clean:
        if a.fill_holes:
            # trimesh.fill_holes() only closes gaps bounded by a few edges (measured: it left ~40k open
            # edges on a real scan untouched). Open3D fills up to a size threshold, so real holes close
            # while an intentionally-open base (much larger than the threshold) stays open. Fall back to
            # trimesh only if Open3D is unavailable.
            hole_mm = a.hole_size if a.hole_size > 0 else float(np.linalg.norm(m.bounds[1] - m.bounds[0])) * 0.04
            filled = False
            try:
                import open3d as o3d
                old_fcount = len(m.faces)
                tm = o3d.t.geometry.TriangleMesh.from_legacy(o3d.geometry.TriangleMesh(
                    o3d.utility.Vector3dVector(np.asarray(m.vertices, np.float64)),
                    o3d.utility.Vector3iVector(np.asarray(m.faces, np.int32))))
                lg = tm.fill_holes(hole_size=hole_mm).to_legacy()
                m = trimesh.Trimesh(np.asarray(lg.vertices), np.asarray(lg.triangles), process=False)
                # Open3D ear-clips holes flat with no new vertices, so a fill reads as a coarse web that
                # doesn't follow the surface. Round it: subdivide only the new fill faces' interior edges
                # (the shared rim stays put, so it's still watertight) and relax the new midpoints.
                if len(m.faces) > old_fcount:
                    try: m = _smooth_fill_faces(m, old_fcount)
                    except Exception: pass
                emit("filled", hole_mm=round(hole_mm, 1), faces=len(m.faces)); filled = True
            except Exception as e:
                emit("warn", step="fill-holes", err=str(e)[:200])
            if not filled:
                try:
                    m.fill_holes(); filled = True
                except Exception as e:
                    warnings.append("hole fill"); emit("warn", step="fill-holes-fallback", err=str(e)[:200])
        if a.smooth_times > 0:
            try: trimesh.smoothing.filter_humphrey(m, iterations=int(a.smooth_times))
            except Exception as e:
                warnings.append("smoothing"); emit("warn", step="smooth", err=str(e)[:200])
            # Humphrey/Laplacian smoothing is unstable on these open-boundary scan meshes: it drags a few
            # percent of vertices far off the surface, spawning long sliver triangles that render as
            # "shredded" garbage (measured max edge 97 vs a 0.27 median on a real scan; even 1 pass does it,
            # and pinning the boundary does not stop it). The slivers are a runaway artifact, not surface, so
            # drop any face with an edge far above the median. Guard so a mesh with no slivers is untouched.
            try:
                V = np.asarray(m.vertices); F = np.asarray(m.faces)
                if len(F):
                    emax = np.linalg.norm(V[F[:, [0, 1, 2]]] - V[F[:, [1, 2, 0]]], axis=2).max(axis=1)
                    keep = emax < float(np.median(emax)) * 8.0
                    if 0 < keep.sum() < len(F):
                        m = trimesh.Trimesh(V, F[keep], process=False); m.remove_unreferenced_vertices()
                        emit("desliver", dropped=int(len(F) - int(keep.sum())), faces=len(m.faces))
            except Exception: pass
        if 0 < a.simplify_pct < 100:
            import fast_simplification
            v, f = fast_simplification.simplify(np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.int32), target_reduction=1.0 - a.simplify_pct / 100.0)
            m = trimesh.Trimesh(v, f, process=False)
        emit("cleaned", faces=len(m.faces))

    m.export(a.outfile)
    emit("done", out=os.path.basename(a.outfile), faces=len(m.faces),
         mb=round(os.path.getsize(a.outfile) / 1048576, 1), warnings=warnings)

if __name__ == "__main__":
    main()
