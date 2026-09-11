#!/usr/bin/env python3
# Compare two 3D models of the same scan (e.g. the scanner's One-tap Edit result vs our Process on PC
# build): size, counts, extent, pieces, and how far apart the surfaces are, plus side-by-side renders.
# MEMORY-SAFE: a hard address-space cap, meshes decimated before analysis, and distances measured with
# sampled surface points + a KD-tree (a few hundred MB at most). The naive trimesh proximity query on a
# 3M-face mesh used 58 GB and took the whole machine down on 2026-09-11; never do that again.
#   ~/pointyoink/venv/bin/python dev/compare.py A.ply B.ply [--out compare.png] [--labels scanner,PC]
import argparse, os, sys, time, resource
CAP_GB = float(os.environ.get("POINTYOINK_MEM_CAP_GB", "6"))
try:
    cap = int(CAP_GB * 1024**3); resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
except Exception: pass
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def load(p, max_faces=600000):
    import trimesh
    m = trimesh.load(p, force="mesh")
    n_orig = len(m.faces)
    if n_orig > max_faces:
        import fast_simplification
        v, f = fast_simplification.simplify(np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.int32), target_count=max_faces)
        m = trimesh.Trimesh(v, f, process=False)
    return m, n_orig

def surface_points(m, n):
    import trimesh
    pts, _ = trimesh.sample.sample_surface(m, n); return np.asarray(pts, np.float32)

def dist_stats(pa, pb):
    """Distance from each point of pa to the nearest sampled point of pb (mm). With ~1M points on pb the
    sampling error is well under the scanner's own resolution."""
    from scipy.spatial import cKDTree
    d, _ = cKDTree(pb).query(pa, k=1, workers=2)
    return {"mean": float(d.mean()), "median": float(np.median(d)), "p95": float(np.percentile(d, 95)), "max": float(d.max()),
            "over_1mm": float((d > 1.0).mean() * 100)}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--out", default=None); ap.add_argument("--labels", default="A,B")
    args = ap.parse_args(); la, lb = args.labels.split(",")
    t = time.time(); A, na = load(args.a); B, nb = load(args.b)
    def row(name, m, n_orig, p):
        ext = m.bounds[1] - m.bounds[0]
        print("%-8s %-52s %7.1f MB  %9d faces  extent %.0f x %.0f x %.0f mm  watertight=%s" % (
            name, os.path.basename(p), os.path.getsize(p) / 1048576, n_orig, ext[0], ext[1], ext[2], m.is_watertight))
    row(la, A, na, args.a); row(lb, B, nb, args.b)
    try:
        import trimesh
        ca = len(trimesh.graph.connected_components(A.face_adjacency, min_len=1)); cb = len(trimesh.graph.connected_components(B.face_adjacency, min_len=1))
        print("pieces   %s: %d   %s: %d   (more pieces = floating bits left in)" % (la, ca, lb, cb))
    except Exception as e:
        print("pieces: (skipped: %s)" % e)
    pa, pb = surface_points(A, 300000), surface_points(B, 1000000)
    d1 = dist_stats(pa, pb)
    pa2, pb2 = surface_points(B, 300000), surface_points(A, 1000000)
    d2 = dist_stats(pa2, pb2)
    for lab, d in ((la + " -> " + lb, d1), (lb + " -> " + la, d2)):
        print("surface distance %-18s mean %.3f  median %.3f  95%% %.3f  max %.1f mm   %.1f%% of the surface is >1 mm from the other" % (lab, d["mean"], d["median"], d["p95"], d["max"], d["over_1mm"]))
    print("(a big max or >1 mm share in ONE direction = that model has parts the other lacks: a base, floaters, or filled holes)")
    if args.out:
        import shade
        from PIL import Image, ImageDraw
        imgs = []
        for p, lab in ((args.a, la), (args.b, lb)):
            v, f = shade.load_oriented(p); im = shade.render(v, f, size=(700, 480)); ImageDraw.Draw(im).text((12, 10), lab, fill=(238, 241, 245)); imgs.append(im)
        out = Image.new("RGB", (1400, 480)); out.paste(imgs[0], (0, 0)); out.paste(imgs[1], (700, 0)); out.save(args.out); print("wrote", args.out)
    print("done in %.1fs, peak memory %.1f GB" % (time.time() - t, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576))

if __name__ == "__main__":
    try: main()
    except MemoryError:
        print("out of memory under the %.0f GB cap (raise POINTYOINK_MEM_CAP_GB, or lower max_faces)" % CAP_GB); sys.exit(3)
