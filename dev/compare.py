#!/usr/bin/env python3
# Compare two 3D models of the same scan (e.g. the scanner's One-tap Edit result vs our Process on PC
# build): size, counts, extent, how far apart the surfaces are, and side-by-side shaded renders.
#   ~/pointyoink/venv/bin/python dev/compare.py A.ply B.ply [--out compare.png] [--samples 200000]
import argparse, os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def load(p):
    import trimesh
    m = trimesh.load(p, force="mesh"); return m

def dist_stats(a, b, n):
    """Distances from n random surface points of a to the surface of b (mm), summarised."""
    import trimesh
    pts, _ = trimesh.sample.sample_surface(a, n)
    q = trimesh.proximity.ProximityQuery(b)
    d = np.abs(q.signed_distance(pts)) if len(b.faces) < 400000 else np.linalg.norm(pts - q.on_surface(pts)[0], axis=1)
    return {"mean": float(d.mean()), "median": float(np.median(d)), "p95": float(np.percentile(d, 95)), "max": float(d.max())}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--out", default=None); ap.add_argument("--samples", type=int, default=100000)
    ap.add_argument("--labels", default="A,B")
    args = ap.parse_args(); la, lb = args.labels.split(",")
    t = time.time(); A = load(args.a); B = load(args.b)
    def row(name, m, p):
        ext = m.bounds[1] - m.bounds[0]
        print("%-8s %-50s %7.1f MB  %9d verts %9d faces  extent %.0f x %.0f x %.0f mm  watertight=%s" % (
            name, os.path.basename(p), os.path.getsize(p) / 1048576, len(m.vertices), len(m.faces), ext[0], ext[1], ext[2], m.is_watertight))
    row(la, A, args.a); row(lb, B, args.b)
    try:
        ca = len(A.split(only_watertight=False)); cb = len(B.split(only_watertight=False))
        print("pieces   %s: %d   %s: %d   (floating bits = pieces beyond the main one)" % (la, ca, lb, cb))
    except Exception as e:
        print("pieces: (skipped: %s)" % e)
    n = min(args.samples, 200000)
    d1 = dist_stats(A, B, n); d2 = dist_stats(B, A, n)
    print("surface distance %s -> %s (mm): mean %.3f  median %.3f  95%% %.3f  max %.2f" % (la, lb, d1["mean"], d1["median"], d1["p95"], d1["max"]))
    print("surface distance %s -> %s (mm): mean %.3f  median %.3f  95%% %.3f  max %.2f" % (lb, la, d2["mean"], d2["median"], d2["p95"], d2["max"]))
    print("(a large max with a small median means one model has parts the other does not: extra floaters, or a base that was removed)")
    if args.out:
        import shade
        from PIL import Image, ImageDraw
        imgs = []
        for p, lab in ((args.a, la), (args.b, lb)):
            v, f = shade.load_oriented(p); im = shade.render(v, f, size=(700, 480)); ImageDraw.Draw(im).text((12, 10), lab, fill=(238, 241, 245)); imgs.append(im)
        out = Image.new("RGB", (1400, 480)); out.paste(imgs[0], (0, 0)); out.paste(imgs[1], (700, 0)); out.save(args.out); print("wrote", args.out)
    print("done in %.1fs" % (time.time() - t))

if __name__ == "__main__":
    main()
