#!/usr/bin/env python3
# Shaded previews of scan meshes for PointYoink: a tiny software rasterizer (numpy + PIL, no GPU,
# no matplotlib) that draws a mesh as a grey material on a dark grid floor, plus a wireframe
# variant. Used for the big preview and the list thumbnails. Renders are cached next to the
# app's other thumbnails and only redone when the mesh file changes.
#   python3 shade.py mesh.ply out.png [--wire] [--size 900x600]
import os, sys, time
import numpy as np

BG = (10, 12, 16)
GRID = (26, 33, 48)
MATERIAL = np.array([190.0, 196.0, 206.0])
WIRE = (110, 170, 255)
WIRE_FILL = (14, 17, 24)
MAX_FACES = 40000

def load_oriented(path, max_faces=MAX_FACES):
    """Mesh -> (vertices, faces) decimated, centred, unit-scaled, with the scan's table plane as the floor."""
    import trimesh
    m = trimesh.load(path, force="mesh")
    v = np.asarray(m.vertices, dtype=np.float32); f = np.asarray(m.faces, dtype=np.int32)
    if len(f) > max_faces:
        try:
            import fast_simplification
            v, f = fast_simplification.simplify(v, f, target_count=max_faces)
            v = np.asarray(v, dtype=np.float32); f = np.asarray(f, dtype=np.int32)
        except Exception:
            f = f[np.random.RandomState(0).choice(len(f), max_faces, replace=False)]   # crude fallback
    v = v - v.mean(0); v /= (np.abs(v).max() + 1e-9)
    # scans lie on a table: the axis of least spread is "up"
    w, e = np.linalg.eigh(np.cov(v.T)); up = e[:, 0]
    if up[2] < 0: up = -up
    a = np.cross(up, [0, 0, 1.0]); s = np.linalg.norm(a)
    if s > 1e-6:
        a /= s; ang = np.arccos(np.clip(up[2], -1, 1))
        K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
        v = v @ (np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * K @ K).T
    v[:, 2] -= v[:, 2].min()
    return v.astype(np.float32), f

def render(v, f, size=(900, 600), wire=False, azim=-35.0, elev=30.0, zoom=1.0, pan=(0.0, 0.0), grid=True):
    """Draw the mesh with flat shading (painter's algorithm) on a grid floor. Returns a PIL image.
    zoom scales the view, pan shifts it in screen fractions; both are what the live viewer drives."""
    from PIL import Image, ImageDraw
    W, H = size
    az, el = np.radians(azim), np.radians(elev)
    Rz = np.array([[np.cos(az), -np.sin(az), 0], [np.sin(az), np.cos(az), 0], [0, 0, 1]])
    Rx = np.array([[1, 0, 0], [0, np.cos(el), -np.sin(el)], [0, np.sin(el), np.cos(el)]])
    def proj(p):
        q = (p @ Rz.T) @ Rx.T; d = 3.2 + q[:, 1]
        x = q[:, 0] / d * 2.6 * zoom; y = q[:, 2] / d * 2.6 * zoom
        return np.stack([W / 2 + (x + pan[0]) * W * 0.42, H * 0.62 - (y + pan[1]) * H * 0.42], 1), q[:, 1]
    img = Image.new("RGB", size, BG); dr = ImageDraw.Draw(img)
    for x in (np.linspace(-1.5, 1.5, 13) if grid else []):
        p, _ = proj(np.array([[x, -1.5, 0], [x, 1.5, 0]])); dr.line([tuple(p[0]), tuple(p[1])], fill=GRID, width=1)
        p, _ = proj(np.array([[-1.5, x, 0], [1.5, x, 0]])); dr.line([tuple(p[0]), tuple(p[1])], fill=GRID, width=1)
    P, depth = proj(v)
    n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]); n /= np.linalg.norm(n, axis=1)[:, None] + 1e-9
    light = np.array([-0.4, -0.6, 0.7]); light /= np.linalg.norm(light)
    shade = np.clip(0.30 + 0.62 * np.clip(n @ light, 0, 1) + 0.12 * np.clip(-n @ np.array([0, 1, 0]), 0, 1), 0, 1)
    cols = (MATERIAL[None, :] * shade[:, None]).astype(int)
    order = np.argsort(-depth[f].mean(1))            # far to near
    pts = P[f]                                         # (F,3,2)
    for i in order:
        t = [tuple(x) for x in pts[i]]
        if wire: dr.polygon(t, fill=WIRE_FILL, outline=WIRE)
        else: dr.polygon(t, fill=tuple(cols[i]))
    return img

def preview_path(cache_dir, key, wire=False):
    return os.path.join(cache_dir, "%s__%s.png" % (key, "wire" if wire else "shaded"))

def ensure_preview(mesh_path, cache_dir, key, wire=False, size=(900, 600), thumb=None):
    """Render (or reuse) the shaded preview for a mesh. Returns the PNG path, or None on failure.
    thumb=(w,h) also writes a small '<key>__thumb.png' from the same render."""
    try:
        os.makedirs(cache_dir, exist_ok=True)
        out = preview_path(cache_dir, key, wire)
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(mesh_path): return out
        v, f = load_oriented(mesh_path)
        img = render(v, f, size=size, wire=wire); img.save(out)
        if thumb and not wire:
            from PIL import Image
            t = img.resize(thumb, Image.LANCZOS); t.save(os.path.join(cache_dir, key + "__thumb.png"))
        return out
    except Exception:
        return None

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("mesh"); ap.add_argument("out")
    ap.add_argument("--wire", action="store_true"); ap.add_argument("--size", default="900x600")
    a = ap.parse_args(); W, H = (int(x) for x in a.size.split("x"))
    t0 = time.time(); v, f = load_oriented(a.mesh); t1 = time.time()
    render(v, f, size=(W, H), wire=a.wire).save(a.out)
    print("%s: %d faces, load+decimate %.1fs, draw %.1fs" % (a.out, len(f), t1 - t0, time.time() - t1))
