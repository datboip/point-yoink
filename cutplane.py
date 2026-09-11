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

def ransac_normal(V, rng):
    diag = float(__import__("numpy").linalg.norm(V.max(0) - V.min(0)))
    import numpy as np
    thr = diag * 0.01
    S = V[rng.choice(len(V), min(60000, len(V)), replace=False)]
    best, normal = 0, np.array([0.0, 0.0, 1.0])
    for _ in range(200):
        p = S[rng.choice(len(S), 3, replace=False)]
        n = np.cross(p[1] - p[0], p[2] - p[0]); ln = np.linalg.norm(n)
        if ln < 1e-9:
            continue
        n = n / ln; d = -n.dot(p[0])
        inl = int(np.sum(np.abs(S.dot(n) + d) < thr))
        if inl > best:
            best, normal = inl, n
    return normal

def main():
    if len(sys.argv) < 3:
        print("CUT_ERROR usage: cutplane.py <in> <out>", flush=True); return 2
    infile, outfile = sys.argv[1], sys.argv[2]
    import numpy as np, trimesh
    m = trimesh.load(infile, force="mesh")
    if len(m.faces) > 800000:
        import fast_simplification
        v, f = fast_simplification.simplify(m.vertices, m.faces, target_count=800000)
        m = trimesh.Trimesh(v, f, process=False)
    V = np.asarray(m.vertices)
    rng = np.random.default_rng(0)

    # cut axis = dominant-plane normal (the table's normal); build 2 in-plane axes
    normal = ransac_normal(V, rng)
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

    # apply the cut to the (decimated) mesh, then keep the largest piece
    keepv = (H > state["cut"]) if state["keep_above"] else (H < state["cut"])
    keep_f = keepv[m.faces].all(axis=1)
    m.update_faces(keep_f); m.remove_unreferenced_vertices()
    comps = trimesh.graph.connected_components(m.face_adjacency, min_len=1)
    if len(comps) > 1:
        largest = max(comps, key=len)
        mask = np.zeros(len(m.faces), bool); mask[largest] = True
        m.update_faces(mask); m.remove_unreferenced_vertices()
    m.export(outfile)
    print("CUT_DONE " + json.dumps({"faces": len(m.faces), "mb": round(os.path.getsize(outfile) / 1048576, 1),
          "plane": {"n": [float(x) for x in normal], "d": float(state["cut"]), "keep_above": bool(state["keep_above"])}}), flush=True)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("CUT_ERROR %s" % e, flush=True); sys.exit(1)
