#!/usr/bin/env python3
# Interactive cut-plane base removal for PointYoink. Runs as a memory-capped
# subprocess. Shows two side profiles of the mesh with a cut-height slider; you
# set where to slice, Apply removes the base and keeps the object.
#   python3 cutplane.py <in.ply> <out.ply>
# stdout: CUT_DONE {json} | CUT_CANCELLED | CUT_ERROR ...
import sys, os, resource, json

MEM_CAP_GB = float(os.environ.get("POINTYOINK_MEM_CAP_GB", "10"))
try:
    cap = int(MEM_CAP_GB * 1024**3)
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
except Exception:
    pass

def ransac_normal(V, rng, return_points=False):
    diag = float(__import__("numpy").linalg.norm(V.max(0) - V.min(0)))
    import numpy as np
    thr = diag * 0.01
    S = V[rng.choice(len(V), min(60000, len(V)), replace=False)]
    best, normal = 0, np.array([0.0, 0.0, 1.0])
    support = np.empty((0, 3), dtype=float)
    for _ in range(200):
        p = S[rng.choice(len(S), 3, replace=False)]
        n = np.cross(p[1] - p[0], p[2] - p[0]); ln = np.linalg.norm(n)
        if ln < 1e-9:
            continue
        n = n / ln; d = -n.dot(p[0])
        mask = np.abs(S.dot(n) + d) < thr
        inl = int(np.sum(mask))
        if inl > best:
            best, normal = inl, n
            if return_points: support = S[mask]
    if return_points:
        # Keep real surface samples, spread across the winning plane. These are
        # review points from its inliers, not invented points projected onto it.
        if len(support):
            candidates = support[np.linspace(0, len(support)-1, min(4096, len(support)), dtype=int)]
            chosen = [int(np.argmin(np.sum((candidates-candidates.mean(0))**2, axis=1)))]
            distances = np.sum((candidates-candidates[chosen[0]])**2, axis=1)
            for _ in range(min(12, len(candidates))-1):
                j = int(np.argmax(distances))
                if distances[j] <= 1e-18: break
                chosen.append(j)
                distances = np.minimum(distances, np.sum((candidates-candidates[j])**2, axis=1))
            support = candidates[chosen].copy()
        return normal, support
    return normal

def main():
    if len(sys.argv) < 3:
        print("CUT_ERROR usage: cutplane.py <in> <out>", flush=True); return 2
    infile, outfile = sys.argv[1], sys.argv[2]
    given = None                                    # --plane nx,ny,nz,d,keep_above  (from the in-app cut view): no window, just cut
    if len(sys.argv) > 4 and sys.argv[3] == "--plane":
        v = sys.argv[4].split(","); given = ([float(v[0]), float(v[1]), float(v[2])], float(v[3]), v[4].lower() in ("1", "true", "yes"))
    import numpy as np, trimesh
    m_full = trimesh.load(infile, process=False)
    if isinstance(m_full, trimesh.Scene):
        m_full = m_full.dump(concatenate=True)
    m = m_full
    if len(getattr(m_full, "faces", [])) > 800000 and given is None:     # the interactive view gets a lighter copy; the saved cut is always the full mesh
        import fast_simplification
        v, f = fast_simplification.simplify(m_full.vertices, m_full.faces, target_count=800000)
        m = trimesh.Trimesh(v, f, process=False)
    V = np.asarray(m.vertices)
    rng = np.random.default_rng(0)

    # cut axis = dominant-plane normal (the table's normal); build 2 in-plane axes
    normal = np.array(given[0], float) if given else ransac_normal(V, rng)
    if given:
        state = {"cut": given[1], "keep_above": given[2], "apply": True}
        return finish(m_full, normal, state, outfile)
    a = np.array([1.0, 0, 0]) if abs(normal[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(normal, a); u /= np.linalg.norm(u)
    w = np.cross(normal, u)
    H = V.dot(normal); U = V.dot(u); W = V.dot(w)
    idx = rng.choice(len(V), min(20000, len(V)), replace=False)
    Hs, Us, Ws = H[idx], U[idx], W[idx]

    import matplotlib
    matplotlib.rcParams["toolbar"] = "None"          # drop the clunky nav toolbar
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.widgets import Slider, Button

    # PointYoink palette
    BG="#0d0f14"; CARD="#12161d"; TX="#eef1f5"; MUT="#8b95a7"; STROKE="#252b36"
    KEEP="#5ab0ff"; REMOVE="#ff5d6c"; CUT="#ffb020"; ACC="#1f6feb"; BTN="#1b212b"

    Hmin, Hmax = float(H.min()), float(H.max()); pad = (Hmax - Hmin) * 0.05
    # start the cut near the table (8th percentile of height) so the base is caught
    state = {"cut": float(np.percentile(H, 8)), "keep_above": True, "apply": False}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 6.6))
    fig.patch.set_facecolor(BG)
    plt.subplots_adjust(left=0.07, right=0.97, top=0.86, bottom=0.27, wspace=0.14)
    try: fig.canvas.manager.set_window_title("PointYoink - Base Removal")
    except Exception: pass
    lims = {id(ax1): (float(Us.min()), float(Us.max())), id(ax2): (float(Ws.min()), float(Ws.max()))}

    def style_ax(ax, title):
        ax.set_facecolor(CARD)
        for sp in ax.spines.values(): sp.set_color(STROKE)
        ax.tick_params(colors=MUT, labelsize=8)
        ax.grid(True, color=STROKE, lw=0.5, alpha=0.5)
        ax.set_title(title, color=MUT, fontsize=10, pad=8)

    def draw():
        ka = state["keep_above"]
        for ax, X, lbl in ((ax1, Us, "profile A"), (ax2, Ws, "profile B")):
            ax.clear(); style_ax(ax, lbl)
            rem = (Hs < state["cut"]) if ka else (Hs > state["cut"])
            ax.scatter(X[~rem], Hs[~rem], s=4, c=KEEP, linewidths=0, alpha=0.75)
            ax.scatter(X[rem], Hs[rem], s=4, c=REMOVE, linewidths=0, alpha=0.75)
            ax.axhline(state["cut"], color=CUT, lw=2.2)
            ax.set_xlim(*lims[id(ax)]); ax.set_ylim(Hmin - pad, Hmax + pad)
            ax.set_aspect("equal", adjustable="box")
        fig.suptitle("Base removal    set the line so RED is the base and BLUE is your object",
                     color=TX, fontsize=13, y=0.955)
        fig.canvas.draw_idle()

    sax = plt.axes([0.15, 0.145, 0.70, 0.035], facecolor=CARD)
    sl = Slider(sax, "cut", Hmin, Hmax, valinit=state["cut"], color=CUT)
    sl.label.set_color(MUT); sl.valtext.set_color(TX)
    try: sl.track.set_color(STROKE)          # darken the unfilled track
    except Exception: pass
    sl.on_changed(lambda v: (state.__setitem__("cut", float(v)), draw()))

    def mkbtn(x, w, label, cb, accent=False):
        b = Button(plt.axes([x, 0.04, w, 0.065]),
                   label, color=(ACC if accent else BTN), hovercolor=("#2f7ffb" if accent else STROKE))
        b.label.set_color("#ffffff" if accent else TX); b.label.set_fontsize(11)
        for sp in b.ax.spines.values(): sp.set_color(STROKE)
        b.on_clicked(cb); return b
    _b1 = mkbtn(0.15, 0.17, "Flip side",
                lambda e: (state.__setitem__("keep_above", not state["keep_above"]), draw()))
    _b2 = mkbtn(0.40, 0.20, "Apply cut",
                lambda e: (state.__setitem__("apply", True), plt.close(fig)), accent=True)
    _b3 = mkbtn(0.68, 0.17, "Cancel", lambda e: plt.close(fig))
    fig._pyk_btns = (_b1, _b2, _b3)   # keep refs alive

    draw()
    print("CUT_READY", flush=True)
    plt.show()

    if not state["apply"]:
        print("CUT_CANCELLED", flush=True); return 0

    return finish(m_full, normal, state, outfile)

def finish(m, normal, state, outfile):
    import numpy as np
    import trimesh
    normal = np.asarray(normal, dtype=float)
    cut = float(state["cut"])
    if normal.shape != (3,) or not np.isfinite(normal).all() or not np.isfinite(cut):
        raise ValueError("Cut plane must be finite.")
    norm = float(np.linalg.norm(normal))
    if norm < 1e-12:
        raise ValueError("Cut plane normal must be nonzero.")
    # Preserve retained portions of crossing triangles. No cap, filling, cleanup,
    # or disconnected-component removal is implied by a base cut.
    if isinstance(m, trimesh.Trimesh) and len(m.faces):
        origin = normal * (cut / (norm * norm))
        direction = normal / norm * (1 if state["keep_above"] else -1)
        # Use the face slicer directly: the capped wrapper imports optional
        # Shapely even when cap=False, which fresh installations may not have.
        vertices = np.asarray(m.vertices)
        faces = np.asarray(m.faces)
        dots = (vertices - origin).dot(direction)
        coplanar = np.all(np.abs(dots[faces]) <= trimesh.constants.tol.merge, axis=1)
        clipped_v, clipped_f, _ = trimesh.intersections.slice_faces_plane(
            vertices, faces[~coplanar], direction, origin)
        clipped = trimesh.Trimesh(clipped_v, clipped_f, process=False)
        if coplanar.any():
            # The lower-level slicer discards front-facing coplanar triangles.
            # Keep all original on-plane surfaces: the user retained this side.
            flat = trimesh.Trimesh(vertices.copy(), faces[coplanar], process=False)
            flat.remove_unreferenced_vertices()
            clipped = trimesh.util.concatenate([clipped, flat])
        m = clipped
    else:
        heights = np.asarray(m.vertices).dot(normal)
        keep = heights >= cut if state["keep_above"] else heights <= cut
        colors = getattr(m.visual, "vertex_colors", None)
        m = trimesh.points.PointCloud(np.asarray(m.vertices)[keep],
                                      colors=colors[keep] if colors is not None and len(colors) == len(keep) else None)
    tmp = "%s.tmp.%d.ply" % (outfile[:-4] if outfile.lower().endswith(".ply") else outfile, os.getpid())
    m.export(tmp, file_type="ply")
    if not os.path.exists(tmp) or os.path.getsize(tmp) < 64:
        try: os.remove(tmp)
        except Exception: pass
        print("CUT_ERROR could not write the result", flush=True); return 3
    os.replace(tmp, outfile)                          # never leave a half-written file where a good one was
    print("CUT_DONE " + json.dumps({"faces": len(getattr(m, "faces", [])), "points": len(m.vertices), "mb": round(os.path.getsize(outfile) / 1048576, 1),
          "plane": {"n": [float(x) for x in normal], "d": float(state["cut"]), "keep_above": bool(state["keep_above"])}}), flush=True)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("CUT_ERROR %s" % e, flush=True); sys.exit(1)
