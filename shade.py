#!/usr/bin/env python3
# Shaded previews of scan meshes for PointYoink: a tiny software rasterizer (numpy + PIL, no GPU,
# no matplotlib) that draws a mesh as a grey material on a dark grid floor, plus a wireframe
# variant. Used for the big preview and the list thumbnails. Renders are cached next to the
# app's other thumbnails and only redone when the mesh file changes.
#   python3 shade.py mesh.ply out.png [--wire] [--size 900x600]
import os, sys, time, hashlib, struct
from collections import OrderedDict
import numpy as np

BG = (10, 12, 16)
GRID = (26, 33, 48)
MATERIAL = np.array([190.0, 196.0, 206.0])
WIRE = (110, 170, 255)
WIRE_FILL = (14, 17, 24)
MAX_FACES = 40000

# ---- prepared-mesh cache ---------------------------------------------------
# The expensive step for EVERY render (still, interactive GL/software view, strip thumbnail) is the same:
# trimesh-parse the .ply, decimate to max_faces, orient it. That output (view-space vertices, faces,
# transform) depends only on the source file + max_faces, so we cache it: an in-memory LRU for the running
# app (reopening a scan is instant, no disk) backed by an on-disk .npz keyed on the source's mtime/size, so
# a fresh launch skips the parse+simplify too. Editing the mesh (Prepare / base-cut writes a new file, new
# mtime) misses and recomputes. This is what makes clicking Scan 1 -> Scan 2 -> Scan 1 stop re-loading.
MESH_CACHE = os.path.expanduser("~/.cache/pointyoink/mesh")
CACHE_STATS = {"mem": 0, "disk": 0, "compute": 0}   # the app logs deltas around a view open to PROVE reuse
_MEM = OrderedDict(); _MEM_MAX = 6

def _mesh_key(path, max_faces, tf):
    try:
        st = os.stat(path)
        h = hashlib.sha1()
        h.update(os.path.abspath(path).encode("utf-8", "replace"))
        h.update(b"|"); h.update(("%d|%d|%d" % (int(st.st_mtime), st.st_size, int(max_faces))).encode())
        if tf is not None:                          # a second mesh oriented to match the first: key on that transform too
            h.update(b"|tf|"); h.update(np.asarray(tf["mean"], np.float64).tobytes())
            h.update(np.asarray(tf["R"], np.float64).tobytes())
            h.update(struct.pack("<dd", float(tf["scale"]), float(tf["zshift"])))
        return h.hexdigest()
    except Exception:
        return None

def _cache_get(key):
    m = _MEM.get(key)
    if m is not None:
        _MEM.move_to_end(key); CACHE_STATS["mem"] += 1
        vv, f, tf = m; return vv.copy(), f.copy(), dict(tf)
    cpath = os.path.join(MESH_CACHE, key + ".npz")
    try:
        if os.path.exists(cpath):
            d = np.load(cpath, allow_pickle=False)
            vv = d["v"].astype(np.float32); f = d["f"].astype(np.int32)
            tf = {"mean": np.asarray(d["mean"], np.float64), "scale": float(d["scale"][0]),
                  "R": np.asarray(d["R"], np.float64), "zshift": float(d["zshift"][0])}
            _MEM[key] = (vv.copy(), f.copy(), dict(tf)); _MEM.move_to_end(key)
            while len(_MEM) > _MEM_MAX: _MEM.popitem(last=False)
            CACHE_STATS["disk"] += 1; return vv, f, tf
    except Exception:
        pass
    return None

def _cache_put(key, vv, f, tf):
    _MEM[key] = (vv.copy(), f.copy(), dict(tf)); _MEM.move_to_end(key)   # store copies so a caller mutating vv/f can't corrupt the cache
    while len(_MEM) > _MEM_MAX: _MEM.popitem(last=False)
    try:
        os.makedirs(MESH_CACHE, exist_ok=True)
        cpath = os.path.join(MESH_CACHE, key + ".npz")
        tmp = cpath + ".tmp.%d" % os.getpid()
        with open(tmp, "wb") as fh:                 # file object -> np.savez keeps the name (no .npz appended); atomic replace
            np.savez(fh, v=vv.astype(np.float32), f=f.astype(np.int32),
                     mean=np.asarray(tf["mean"], np.float64), scale=np.array([float(tf["scale"])], np.float64),
                     R=np.asarray(tf["R"], np.float64), zshift=np.array([float(tf["zshift"])], np.float64))
        os.replace(tmp, cpath)
        if key[-2:] == "00": _cache_prune()          # ~1/256 of writes: keep the folder from growing without bound
    except Exception:
        pass

def _cache_prune(cap=400):
    try:
        files = [os.path.join(MESH_CACHE, n) for n in os.listdir(MESH_CACHE) if n.endswith(".npz")]
        if len(files) <= cap: return
        files.sort(key=lambda p: os.path.getmtime(p))          # oldest first
        for p in files[:len(files) - cap]:
            try: os.remove(p)
            except Exception: pass
    except Exception:
        pass

def load_oriented(path, max_faces=MAX_FACES):
    """Mesh -> (vertices, faces) decimated, centred, unit-scaled, with the scan's table plane as the floor."""
    v, f, _ = load_oriented_tf(path, max_faces)
    return v, f

def load_oriented_tf(path, max_faces=MAX_FACES, tf=None):
    """Like load_oriented but also returns the transform, so a point picked in the view can be mapped back
    to the scan's own millimetre coordinates (view_to_world). Pass tf= to orient a second mesh exactly like
    the first (needed to draw two scans in one view).
    Cached: the parsed+decimated+oriented result is reused from memory or disk when the source file and
    max_faces are unchanged, so only the FIRST open of a scan pays the trimesh parse + simplify."""
    key = _mesh_key(path, max_faces, tf)
    if key is not None:
        got = _cache_get(key)
        if got is not None: return got
    CACHE_STATS["compute"] += 1
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
    if tf is None:
        # normalise to ~0.85 (not 1.0) so the model sits inside the ~1.1 grid with a margin, not overhanging it
        mean = v.mean(0); vc = v - mean; scale = float(np.abs(vc).max() + 1e-9) / 0.85; vc = vc / scale
        # scans lie on a table: the axis of least spread is "up"
        w, e = np.linalg.eigh(np.cov(vc.T)); up = e[:, 0]
        if up[2] < 0: up = -up
        a = np.cross(up, [0, 0, 1.0]); s = np.linalg.norm(a); R = np.eye(3)
        if s > 1e-6:
            a /= s; ang = np.arccos(np.clip(up[2], -1, 1))
            K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
            R = np.eye(3) + np.sin(ang) * K + (1 - np.cos(ang)) * K @ K
        vr = vc @ R.T
        # The object is now level, but its yaw (spin about "up") is still whatever the scanner
        # happened to record, so every scan starts facing a different way. Rotate about up (Z) so the
        # longest horizontal axis lies along X, then pick a deterministic front from the along-X skew,
        # so Reset / home looks the same for every model. R stays a rotation, so the view<->world
        # round-trip (cut plane, combine) is unaffected.
        try:
            _wv, _ev = np.linalg.eigh(np.cov(vr[:, :2].T))     # ascending; last col = longest in-plane axis
            major = _ev[:, -1]; theta = np.arctan2(major[1], major[0])
            cz, sz = np.cos(-theta), np.sin(-theta)
            R = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]]) @ R
            vr = vc @ R.T
            if float(np.mean(vr[:, 0] ** 3)) > 0:              # 180-about-Z ambiguity: heavier end always the same side
                R = np.diag([-1.0, -1.0, 1.0]) @ R; vr = vc @ R.T
        except Exception:
            pass
        zshift = float(vr[:, 2].min())
        tf = {"mean": mean.astype(np.float64), "scale": scale, "R": R, "zshift": zshift}
    vv = world_to_view(v, tf).astype(np.float32)
    if key is not None: _cache_put(key, vv, f, tf)
    return vv, f, tf

def world_to_view(p, tf):
    p = np.asarray(p, dtype=np.float64)
    out = ((p - tf["mean"]) / tf["scale"]) @ tf["R"].T
    out[..., 2] -= tf["zshift"]
    return out

def view_to_world(p, tf):
    p = np.array(p, dtype=np.float64, copy=True)
    p[..., 2] += tf["zshift"]
    return (p @ tf["R"]) * tf["scale"] + tf["mean"]

def render(v, f, size=(900, 600), wire=False, azim=-35.0, elev=30.0, zoom=0.95, pan=(0.0, 0.0), grid=True, gizmo=True):
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
        return np.stack([W / 2 + (x + pan[0]) * W * 0.42, H * 0.48 - (y + pan[1]) * H * 0.42], 1), q[:, 1]
    img = Image.new("RGB", size, BG); dr = ImageDraw.Draw(img)
    for x in (np.linspace(-1.1, 1.1, 11) if grid else []):
        p, _ = proj(np.array([[x, -1.1, 0], [x, 1.1, 0]])); dr.line([tuple(p[0]), tuple(p[1])], fill=GRID, width=1)
        p, _ = proj(np.array([[-1.1, x, 0], [1.1, x, 0]])); dr.line([tuple(p[0]), tuple(p[1])], fill=GRID, width=1)
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
    if gizmo:   # X red, Y green, Z blue: the mesh's own axes, turning with the view, bottom-left
        L = min(W, H) * 0.075; ox, oy = 18 + L, H - 18 - L
        axes = (np.array([[1, 0, 0]]), np.array([[0, 1, 0]]), np.array([[0, 0, 1]]))
        for a3, col, lab in zip(axes, ((255, 93, 108), (62, 207, 142), (90, 176, 255)), ("X", "Y", "Z")):
            q = (a3 @ Rz.T) @ Rx.T; dx, dy = float(q[0, 0]), float(q[0, 2])
            ex, ey = ox + dx * L, oy - dy * L
            dr.line([(ox, oy), (ex, ey)], fill=col, width=2)
            dr.text((ex + (4 if dx >= 0 else -10), ey - 6), lab, fill=col)
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
        img = render(v, f, size=size, wire=wire)
        _tmp = out + ".tmp.%d" % os.getpid(); img.save(_tmp, format="PNG"); os.replace(_tmp, out)   # format explicit: _tmp's extension isn't .png (atomic write, see __main__)
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
    _tmp = a.out + ".tmp.%d" % os.getpid()          # atomic write: two renderers (foreground + warmer) can
    render(v, f, size=(W, H), wire=a.wire).save(_tmp, format="PNG"); os.replace(_tmp, a.out)   # format explicit: _tmp's extension isn't .png; target the same PNG; never leave a half-written one
    print("%s: %d faces, load+decimate %.1fs, draw %.1fs" % (a.out, len(f), t1 - t0, time.time() - t1))
