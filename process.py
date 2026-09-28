#!/usr/bin/env python3
# Memory-safe mesh processor for PointYoink. Runs as a SUBPROCESS with a hard
# address-space cap so a pathological mesh dies alone instead of OOM-ing the box.
#   python3 process.py <in.ply> <out.ply> [--decimate N] [--base-remove] [--isolate] [--clean]
# Order matters: DECIMATE FIRST (cheap), then topology ops on the small mesh.
import sys, os, argparse, resource, json

# --- hard memory cap: contain any blowup to this process ---
MEM_CAP_GB = float(os.environ.get("POINTYOINK_MEM_CAP_GB", "10"))
def _cap_memory():
    """Cap this worker's address space. Only from main(), so importing this module never caps the importer."""
    try:
        cap = int(MEM_CAP_GB * 1024**3)
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except Exception as e:
        print("MEMCAP_WARN %s" % e, flush=True)

def _threads():
    """Worker threads for Open3D: the app's OMP_NUM_THREADS cap, never every core. Extra threads spin at
    barriers on a busy machine (minutes instead of seconds) and each one reserves address space."""
    try: n = int(os.environ.get("OMP_NUM_THREADS") or 2)
    except ValueError: n = 2
    return max(1, min(n, os.cpu_count() or 1))

def sliver_mask(V, F, k, min_angle_deg=15.0):
    """Stretched triangles: longest edge over k times the mesh's median edge AND a smallest angle under
    min_angle_deg. The angle test keeps big but well-shaped faces (a flat, coarsely meshed area) that the
    edge test alone would cut out of good surface."""
    import numpy as np
    V = np.asarray(V, np.float64); F = np.asarray(F, np.int64)
    if not len(F): return np.zeros(0, bool), 0.0
    el = np.linalg.norm(V[F[:, [0, 1, 2]]] - V[F[:, [1, 2, 0]]], axis=2)   # edge i is opposite corner (i+2)%3
    med = float(np.median(el)) if el.size else 0.0
    if med <= 0: return np.zeros(len(F), bool), med
    a, b, c = el[:, 0], el[:, 1], el[:, 2]
    with np.errstate(invalid="ignore", divide="ignore"):
        cosines = np.stack([(b * b + c * c - a * a) / (2 * b * c), (c * c + a * a - b * b) / (2 * c * a), (a * a + b * b - c * c) / (2 * a * b)], 1)
    ang = np.degrees(np.arccos(np.clip(np.nan_to_num(cosines, nan=1.0), -1.0, 1.0)))
    return (el.max(axis=1) > med * float(k)) & (ang.min(axis=1) < float(min_angle_deg)), med

def trim_to_input(rebuilt_v, src_v, src_f, k=3.0):
    """Mask of rebuilt (Poisson) vertices to drop: farther than k times the input's median edge from the input
    surface (its vertices, edge midpoints and face centers). Poisson closes every opening with a skin; the skin
    over a real hole is far from any input point, while the rebuilt surface over real geometry sits on it."""
    import numpy as np
    V = np.asarray(src_v, np.float64); F = np.asarray(src_f, np.int64)
    if not len(F): return np.zeros(len(rebuilt_v), bool)
    mids = (V[F[:, [0, 1, 2]]] + V[F[:, [1, 2, 0]]]) * 0.5
    med = float(np.median(np.linalg.norm(V[F[:, [0, 1, 2]]] - V[F[:, [1, 2, 0]]], axis=2)))
    pts = np.vstack([V[np.unique(F)], V[F].mean(axis=1), mids.reshape(-1, 3)])   # loose vertices are not surface
    q = np.asarray(rebuilt_v, np.float64)
    try:
        from scipy.spatial import cKDTree
        d, _ = cKDTree(pts).query(q, k=1, workers=_threads())
    except ImportError:                    # SciPy is optional; Rebuild surface already needs Open3D
        import open3d as o3d
        d = np.asarray(o3d.geometry.PointCloud(o3d.utility.Vector3dVector(q)).compute_point_cloud_distance(
            o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))))
    return d > k * max(med, 1e-6)

def isolate(m, rate, emit_as="isolated"):
    """Keep pieces at least rate %% of the largest (100 = only the largest). Returns the mesh (same object)."""
    import numpy as np, trimesh
    comps = trimesh.graph.connected_components(m.face_adjacency, min_len=1)
    if len(comps) > 1:
        big = max(len(c) for c in comps)
        keep = [c for c in comps if len(c) >= big * min(100.0, max(0.0, rate)) / 100.0]
        mask = np.zeros(len(m.faces), dtype=bool)
        for c in keep: mask[c] = True
        m.update_faces(mask); m.remove_unreferenced_vertices()
        emit(emit_as, pieces=len(comps), kept=len(keep), faces=len(m.faces))
    else:
        emit(emit_as, pieces=len(comps), kept=len(comps), faces=len(m.faces))
    return m

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
    _cap_memory()
    ap = argparse.ArgumentParser()
    ap.add_argument("infile"); ap.add_argument("outfile", nargs="?")
    ap.add_argument("--info", action="store_true", help="print size, counts, pieces, open edges, non-manifold edges and triangle quality as JSON and exit")
    ap.add_argument("--fix-nonmanifold", action="store_true", help="remove non-manifold edges (opt-in: destructive, reports faces removed) and write result to outfile")
    ap.add_argument("--decimate", type=int, default=0, help="target face count (0=off)")
    ap.add_argument("--base-remove", action="store_true")
    ap.add_argument("--isolate", action="store_true")
    ap.add_argument("--plane-thresh", type=float, default=0, help="mm; 0=auto from bbox")
    ap.add_argument("--clean", action="store_true", help="dedupe, drop small pieces, optional hole fill, smooth, optional simplify")
    # The scanner's own editing knobs; defaults match its Mesh panel.
    ap.add_argument("--isolation-rate", type=float, default=100.0, help="drop pieces smaller than this %% of the largest one (100 = keep only the largest)")
    ap.add_argument("--fill-holes", action="store_true", help="fill holes (the scanner has this off by default)")
    ap.add_argument("--hole-size", type=float, default=0, help="largest hole to fill, in mm (0 = auto: ~4%% of the bbox diagonal, so real gaps close but an intentionally-open base does not)")
    ap.add_argument("--smooth-times", type=int, default=3, help="smoothing passes (scanner default 3; 0 = off)")
    ap.add_argument("--smooth-method", choices=["humphrey", "taubin", "laplacian"], default="humphrey",
                     help="humphrey (default, mild and stays close to the original shape), taubin (barely shrinks, a bit softer), laplacian (strongest, most shrinkage - can round off real detail)")
    ap.add_argument("--rebuild-surface", type=int, default=0, help="rebuild the surface from the points with Poisson reconstruction at this octree depth (0 = off; 9 = normal, 10 = more detail). Turns torn edges and streaks into one continuous skin; the skin Poisson grows over real openings is trimmed off by distance to the input. Needs Open3D")
    ap.add_argument("--slivers", type=float, default=0, help="remove stretched triangles: any face whose longest edge is more than this many times the mesh's median edge and whose smallest angle is under 15 degrees (0 = off; 4 is a good default). The small holes this opens are closed by the hole fill when it is on")
    ap.add_argument("--simplify-pct", type=float, default=0, help="keep this %% of faces after cleaning (0 = keep all; the scanner's Simplify uses 40)")
    ap.add_argument("--rebuild-points", action="store_true", help="reconstruct a mesh from a point cloud with ball-pivoting (keeps openings); runs under this process's memory cap")
    a = ap.parse_args()

    import numpy as np

    if a.rebuild_points:
        # Ball-pivoting on purpose over Poisson: it spans only where points are, so real openings stay open.
        # Runs here (subprocess) under RLIMIT_AS, so a pathological cloud dies alone.
        if not a.outfile: print("outfile required"); sys.exit(2)
        import open3d as o3d
        emit("load", file=os.path.basename(a.infile))
        pcd = o3d.io.read_point_cloud(a.infile)
        pts = np.asarray(pcd.points, dtype=np.float64)
        emit("loaded", points=len(pts))
        if len(pts) < 100: print("too few points to rebuild"); sys.exit(3)
        pcd.remove_duplicated_points()
        try: avg = float(np.mean(pcd.compute_nearest_neighbor_distance()))
        except Exception: avg = 0.0
        if not (avg > 0 and np.isfinite(avg)): avg = 0.3
        pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=avg * 3.0, max_nn=30))
        try: pcd.orient_normals_consistent_tangent_plane(20)
        except Exception: pass
        emit("normals", avg_spacing=round(avg, 4))
        radii = o3d.utility.DoubleVector([avg * 1.5, avg * 3.0, avg * 6.0])
        mesh = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(pcd, radii)
        mesh.remove_duplicated_vertices(); mesh.remove_duplicated_triangles()
        mesh.remove_degenerate_triangles(); mesh.remove_unreferenced_vertices()
        if len(mesh.triangles) == 0: print("ball-pivoting produced no faces"); sys.exit(4)
        # Ball-pivoting is on purpose here (it only spans where points are, unlike Poisson), but where the
        # ball can't quite bridge a thin gap it leaves spiky boundary triangles instead of just an open edge.
        # Clean those up the same way --clean does, so a Points-cloud rebuild is not a downgrade from it:
        try:
            V = np.asarray(mesh.vertices, dtype=np.float64); F = np.asarray(mesh.triangles, dtype=np.int64)
            el = np.linalg.norm(V[F[:, [0, 1, 2]]] - V[F[:, [1, 2, 0]]], axis=2)
            med = float(np.median(el)) if el.size else 0.0
            if med > 0:
                bad = el.max(axis=1) > med * 4.0
                if bad.any():
                    mesh.remove_triangles_by_mask(bad); mesh.remove_unreferenced_vertices()
                    emit("slivers", removed=int(bad.sum()), median_edge_mm=round(med, 3))
        except Exception as e:
            emit("warn", step="rebuild-points-slivers", err=str(e)[:200])
        try:
            lab, cnt, _ = mesh.cluster_connected_triangles(); lab = np.asarray(lab); cnt = np.asarray(cnt)
            if len(cnt) > 1:
                mesh.remove_triangles_by_mask(cnt[lab] < cnt.max()); mesh.remove_unreferenced_vertices()
                emit("isolated", pieces=len(cnt), kept=1)
        except Exception as e:
            emit("warn", step="rebuild-points-isolate", err=str(e)[:200])
        tmp = a.outfile + ".tmp.%d.ply" % os.getpid()
        ok = o3d.io.write_triangle_mesh(tmp, mesh)
        if not ok or not os.path.exists(tmp) or os.path.getsize(tmp) < 64:   # never replace a good file with a failed write
            try: os.remove(tmp)
            except Exception: pass
            print("ERROR could not write %s" % a.outfile, flush=True); return 3
        os.replace(tmp, a.outfile)
        emit("done", out=os.path.basename(a.outfile), faces=len(mesh.triangles), verts=len(mesh.vertices))
        return

    import trimesh
    emit("load", file=os.path.basename(a.infile))
    m = trimesh.load(a.infile, force="mesh")
    emit("loaded", verts=len(m.vertices), faces=len(m.faces))
    if a.info:
        ext = (m.bounds[1] - m.bounds[0]) if len(m.vertices) else np.zeros(3)
        try: pieces = len(trimesh.graph.connected_components(m.face_adjacency, min_len=1))
        except Exception: pieces = -1
        try: open_edges = int(len(trimesh.grouping.group_rows(m.edges_sorted, require_count=1)))
        except Exception: open_edges = -1
        nm_edges = -1
        try:
            ec = trimesh.grouping.group_rows(m.edges_sorted, require_count=1)
            # edges shared by 3+ faces: group all half-edges and count those with >2 occurrences
            all_e = m.edges_sorted.view(np.dtype((np.void, m.edges_sorted.dtype.itemsize * 2))).ravel()
            _, counts = np.unique(all_e, return_counts=True)
            nm_edges = int((counts > 2).sum())
        except Exception: pass
        q_p5 = q_p50 = q_p95 = None
        try:
            V = np.asarray(m.vertices, dtype=np.float64); F = np.asarray(m.faces, dtype=np.int64)
            if len(F):
                v0, v1, v2 = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
                a01 = v1 - v0; a02 = v2 - v0; a12 = v2 - v1
                l01 = np.linalg.norm(a01, axis=1); l02 = np.linalg.norm(a02, axis=1); l12 = np.linalg.norm(a12, axis=1)
                eps = 1e-12
                # minimum angle per face via law of cosines
                cos0 = np.clip(( a01 *  a02).sum(1) / np.maximum(l01 * l02, eps), -1, 1)
                cos1 = np.clip((-a01 *  a12).sum(1) / np.maximum(l01 * l12, eps), -1, 1)
                cos2 = np.clip(( a02 *  a12).sum(1) / np.maximum(l02 * l12, eps), -1, 1)  # noqa: E501 (sign: a02 and a12 share v2)
                min_angles = np.degrees(np.arccos(np.minimum(np.minimum(cos0, cos1), cos2)))
                q_p5 = round(float(np.percentile(min_angles, 5)), 1)
                q_p50 = round(float(np.percentile(min_angles, 50)), 1)
                q_p95 = round(float(np.percentile(min_angles, 95)), 1)
        except Exception: pass
        emit("info", verts=len(m.vertices), faces=len(m.faces), extent=[round(float(x), 1) for x in ext],
             pieces=pieces, open_edges=open_edges, nm_edges=nm_edges, watertight=bool(m.is_watertight),
             min_angle_p5=q_p5, min_angle_p50=q_p50, min_angle_p95=q_p95,
             mb=round(os.path.getsize(a.infile) / 1048576, 1))
        return
    if not a.outfile: print("outfile required unless --info"); sys.exit(2)
    if a.fix_nonmanifold:
        try:
            import open3d as o3d
            o3d_m = o3d.geometry.TriangleMesh(
                o3d.utility.Vector3dVector(np.asarray(m.vertices, np.float64)),
                o3d.utility.Vector3iVector(np.asarray(m.faces, np.int32)))
            faces_before = len(o3d_m.triangles)
            o3d_m.remove_non_manifold_edges()
            o3d_m.remove_unreferenced_vertices()
            faces_after = len(o3d_m.triangles)
            removed = faces_before - faces_after
            m = trimesh.Trimesh(np.asarray(o3d_m.vertices), np.asarray(o3d_m.triangles), process=False)
            emit("fix_nonmanifold", faces_removed=removed, faces=faces_after)
        except Exception as e:
            emit("warn", step="fix-nonmanifold", err=str(e)[:200])
        tmp = a.outfile + ".tmp.%d.ply" % os.getpid()
        import open3d as o3d
        ok = o3d.io.write_triangle_mesh(tmp, o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(np.asarray(m.vertices, np.float64)),
            o3d.utility.Vector3iVector(np.asarray(m.faces, np.int32))))
        if ok and os.path.exists(tmp) and os.path.getsize(tmp) > 64:
            os.replace(tmp, a.outfile)
            emit("done", out=os.path.basename(a.outfile), faces=len(m.faces), verts=len(m.vertices))
        else:
            try: os.remove(tmp)
            except Exception: pass
            emit("warn", step="fix-nonmanifold-write", err="could not write output")
        return

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
    # (default 100 = only the largest piece survives; a stray bit of turntable a base cut missed can be a
    # large, noisy chunk, not a small fleck, so a lenient threshold lets it straight through). Lower it for
    # a scan you know has more than one real piece.
    # Use connected_components + a face mask instead of .split() (which builds every
    # submesh and calls fill_holes) - faster, lighter, and no repair dependency.
    iso_rate = None
    if a.isolate or a.base_remove or a.clean:
        iso_rate = a.isolation_rate if (a.clean and not (a.isolate or a.base_remove)) else 100.0
        isolate(m, iso_rate)

    warnings = []   # requested steps that silently failed - reported at 'done' so the app never claims a clean run it didn't do
    if a.clean:
        if a.slivers > 0:
            # Stretched triangles: the scanner's fusion (or a slightly-off combine) leaves long thin faces
            # streaking off an edge. A clean scan is uniform, so a face with an edge several times the median
            # AND a needle-thin angle is a sliver, not surface. Dropped before the hole fill (when that is on).
            # Removing them can cut off small crumbs, so the piece filter runs again afterwards.
            try:
                bad, med = sliver_mask(m.vertices, m.faces, a.slivers)
                if med > 0:
                    if bad.any():
                        m.update_faces(~bad); m.remove_unreferenced_vertices()
                    emit("slivers", removed=int(bad.sum()), median_edge_mm=round(med, 3), faces=len(m.faces))
                    if bad.any() and iso_rate is not None:
                        isolate(m, iso_rate, "isolated_again")
            except Exception as e:
                warnings.append("sliver removal"); emit("warn", step="slivers", err=str(e)[:200])
        if a.rebuild_surface > 0:
            try:
                import open3d as o3d
                pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.array(m.vertices, dtype=np.float64)))
                pcd.normals = o3d.utility.Vector3dVector(np.array(m.vertex_normals, dtype=np.float64))
                rm, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=int(a.rebuild_surface), n_threads=_threads())
                # Poisson closes every opening with a skin. Its density is no guide (the skin over a round hole
                # is not the sparsest part), so trim by distance to the input surface instead: real openings stay
                # open and the extrapolated bits over gaps in the scan do not survive.
                rm.remove_vertices_by_mask(trim_to_input(np.asarray(rm.vertices), m.vertices, m.faces))
                rm.remove_degenerate_triangles(); rm.remove_unreferenced_vertices()
                lab, cnt, _ = rm.cluster_connected_triangles(); lab = np.asarray(lab); cnt = np.asarray(cnt)
                if len(cnt):
                    rm.remove_triangles_by_mask(cnt[lab] < 0.01 * cnt.max()); rm.remove_unreferenced_vertices()   # crumbs
                # No automatic hole fill here: the distance trim only opens where the input itself was open,
                # so every opening left is a real one. Fill small holes (when ticked) has the final say below.
                m = trimesh.Trimesh(np.asarray(rm.vertices), np.asarray(rm.triangles), process=False)
                emit("rebuilt", depth=int(a.rebuild_surface), faces=len(m.faces))
            except Exception as e:
                warnings.append("surface rebuild"); emit("warn", step="rebuild-surface", err=str(e)[:200])
        if a.fill_holes:
            # Runs AFTER rebuild-surface on purpose: Poisson re-derives the mesh from points and its own
            # density trim strips away low-confidence bridging (including a fill-holes patch made earlier),
            # so a real gap can reopen. This pass has the final say on real holes, using the user's size.
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
            # Save the pre-smoothing vertices and a length scale, so we can undo the runaways (below).
            V0 = np.asarray(m.vertices, dtype=np.float64).copy()
            try:
                F0 = np.asarray(m.faces)
                el = np.linalg.norm(V0[F0[:, [0, 1, 2]]] - V0[F0[:, [1, 2, 0]]], axis=2)
                med_edge = float(np.median(el)) if el.size else 0.0
            except Exception: med_edge = 0.0
            try:
                if a.smooth_method == "taubin": trimesh.smoothing.filter_taubin(m, iterations=int(a.smooth_times))
                elif a.smooth_method == "laplacian": trimesh.smoothing.filter_laplacian(m, iterations=int(a.smooth_times))
                else: trimesh.smoothing.filter_humphrey(m, iterations=int(a.smooth_times))
            except Exception as e:
                warnings.append("smoothing"); emit("warn", step="smooth", err=str(e)[:200])
            # Humphrey/Laplacian smoothing drags a few percent of vertices far off these open-boundary scan meshes. Pin those runaway vertices back to their pre-smoothing position: topology untouched, no faces removed, the rest keeps its smoothing
            try:
                V = np.asarray(m.vertices, dtype=np.float64)
                if med_edge > 0 and len(V) == len(V0):
                    bad = np.linalg.norm(V - V0, axis=1) > med_edge * 3.0   # a real smoothing step moves << one edge; 3x is a wide margin
                    if bad.any():
                        V[bad] = V0[bad]; m.vertices = V                    # pin the runaways back, keep every face
                        emit("desliver", reverted=int(bad.sum()), faces=len(m.faces))
            except Exception as e:
                emit("warn", step="desliver", err=str(e)[:200])
        if 0 < a.simplify_pct < 100:
            import fast_simplification
            v, f = fast_simplification.simplify(np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.int32), target_reduction=1.0 - a.simplify_pct / 100.0)
            m = trimesh.Trimesh(v, f, process=False)
        emit("cleaned", faces=len(m.faces))

    # write beside the target and replace only when complete, so a failed or interrupted export never
    # leaves a truncated file where a good one was (same rule as the fused output above)
    root, ext = os.path.splitext(a.outfile)
    tmp = "%s.tmp.%d%s" % (root, os.getpid(), ext or ".ply")
    try:
        m.export(tmp)
        if not os.path.exists(tmp) or os.path.getsize(tmp) < 64: raise OSError("empty output")
        os.replace(tmp, a.outfile)
    except Exception as e:
        try: os.remove(tmp)
        except Exception: pass
        print("ERROR could not write %s: %s" % (a.outfile, str(e)[:200]), flush=True); return 3
    emit("done", out=os.path.basename(a.outfile), faces=len(m.faces),
         mb=round(os.path.getsize(a.outfile) / 1048576, 1), warnings=warnings)

if __name__ == "__main__":
    sys.exit(main() or 0)
