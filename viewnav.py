# Shared 3D view navigation widget math for PointYoink.
# The renderer owns the camera. This helper only draws an orientation cube, hit-tests
# its visible face polygons, and maps the clicked face back to a camera preset.
import math
import numpy as np

FACES = (
    ("right",  "Right",  (90.0, 0.0),  (-1,  0,  0), ((-1, 1,-1),(-1,-1,-1),(-1,-1, 1),(-1, 1, 1))),
    ("left",   "Left",   (-90.0,0.0),  ( 1,  0,  0), (( 1,-1,-1),( 1, 1,-1),( 1, 1, 1),( 1,-1, 1))),
    ("back",   "Back",   (180.0,0.0),  ( 0,  1,  0), ((-1, 1,-1),( 1, 1,-1),( 1, 1, 1),(-1, 1, 1))),
    ("front",  "Front",  (0.0,  0.0),  ( 0, -1,  0), (( 1,-1,-1),(-1,-1,-1),(-1,-1, 1),( 1,-1, 1))),
    ("top",    "Top",    (0.0, 90.0),  ( 0,  0,  1), ((-1,-1, 1),( 1,-1, 1),( 1, 1, 1),(-1, 1, 1))),
    ("bottom", "Bottom", (0.0,-90.0),  ( 0,  0, -1), ((-1, 1,-1),( 1, 1,-1),( 1,-1,-1),(-1,-1,-1))),
)
FACE_BY_KEY = {f[0]: f for f in FACES}

LABEL_AXES = {
    # Face decals: U is text left-to-right on that face, V is text top-to-bottom on that face.
    "front":  (( 1, 0, 0), (0, 0,-1)),
    "back":   ((-1, 0, 0), (0, 0,-1)),
    "right":  (( 0,-1, 0), (0, 0,-1)),
    "left":   (( 0, 1, 0), (0, 0,-1)),
    "top":    (( 1, 0, 0), (0,-1, 0)),
    "bottom": (( 1, 0, 0), (0, 1, 0)),
}
COLORS = {
    "right":  (0.25, 0.27, 0.30),
    "left":   (0.19, 0.20, 0.23),
    "back":   (0.20, 0.21, 0.24),
    "front":  (0.25, 0.26, 0.29),
    "top":    (0.29, 0.30, 0.33),
    "bottom": (0.15, 0.16, 0.18),
}
HOVER = (0.24, 0.50, 0.84)
EDGE = (0.72, 0.78, 0.88)
TEXT = (0.88, 0.90, 0.93)
AXIS = {"x": (0.95, 0.48, 0.48), "y": (0.42, 0.86, 0.48), "z": (0.55, 0.55, 0.90)}

def axis_rot(deg, x, y, z):
    a = math.radians(deg); c = math.cos(a); s = math.sin(a)
    n = np.array([x, y, z], dtype=float); n /= (np.linalg.norm(n) + 1e-12)
    K = np.array([[0, -n[2], n[1]], [n[2], 0, -n[0]], [-n[1], n[0], 0]], dtype=float)
    R = np.eye(3, dtype=float) + s * K + (1.0 - c) * (K @ K)
    return R

def rot_from_az_el(azim, elev):
    return axis_rot(float(elev) - 90.0, 1, 0, 0) @ axis_rot(float(azim), 0, 0, 1)

def layout(width, height):
    size = max(66, min(112, int(min(width, height) * 0.155)))
    margin = 14
    cube_y = max(margin + 50, height - margin - size)
    return {
        "cube": (margin, cube_y, size),
        "home": (9, 9, 30, 30),
    }

def _project(points, R, cx, cy, scale):
    pts = np.asarray(points, dtype=float) @ R.T
    xy = np.column_stack((cx + pts[:,0] * scale, cy - pts[:,1] * scale))
    return xy, pts[:,2]

def cube_vertices(rot, width, height):
    R = np.asarray(rot, dtype=float)[:3, :3]
    lo = layout(width, height); x, y, size = lo["cube"]
    cx = x + size * 0.50; cy = y + size * 0.52; scale = size * 0.34
    verts = np.array([[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]], dtype=float)
    xy, z = _project(verts, R, cx, cy, scale)
    return lo, [(tuple(xy[i]), float(z[i]), tuple(verts[i])) for i in range(len(verts))]

def axis_origin(rot, width, height):
    lo, verts = cube_vertices(rot, width, height)
    # Glue the triad to one physical cube corner, not a screen-chosen corner.
    # This is the visible front-left-bottom slicer-style corner for the default view.
    for xy, _z, v3 in verts:
        if tuple(v3) == (-1.0, -1.0, -1.0):
            return tuple(xy)
    return tuple(verts[0][0])

def axis_segments(rot, width, height):
    R = np.asarray(rot, dtype=float)[:3, :3]
    lo = layout(width, height); x, y, size = lo["cube"]
    cx = x + size * 0.50; cy = y + size * 0.52; scale = size * 0.34
    # Draw the colored triad on the three real cube edges meeting at one fixed physical corner.
    # This keeps the axes glued to the box instead of behaving like a free-floating global gizmo.
    corner = np.array([-1.0, -1.0, -1.0], dtype=float)
    edge_dirs = {"x": np.array([1.0, 0.0, 0.0]),
                 "y": np.array([0.0, 1.0, 0.0]),
                 "z": np.array([0.0, 0.0, 1.0])}
    out = {}
    for key, vec in edge_dirs.items():
        length = {"x": 2.18, "y": 2.18, "z": 2.58}[key]
        pts = np.array([corner, corner + vec * length], dtype=float)
        xy, _z = _project(pts, R, cx, cy, scale)
        out[key] = (tuple(xy[0]), tuple(xy[1]))
    return lo, out

def regions(rot, width, height):
    R = np.asarray(rot, dtype=float)[:3, :3]
    lo = layout(width, height); x, y, size = lo["cube"]
    cx = x + size * 0.50; cy = y + size * 0.52; scale = size * 0.34
    out = []
    for key, label, view, normal, verts in FACES:
        xy, z = _project(verts, R, cx, cy, scale)
        nz = float((np.asarray(normal, dtype=float) @ R.T)[2])
        if nz <= 0.04:
            continue
        center3 = np.mean(np.asarray(verts, dtype=float), axis=0)
        u3, v3 = (np.asarray(a, dtype=float) for a in LABEL_AXES[key])
        u3 = u3 / (np.linalg.norm(u3) + 1e-9); v3 = v3 / (np.linalg.norm(v3) + 1e-9)
        # Fixed cube-local decal size. This is the key: labels are printed on the cube,
        # so their physical size does not change just because the face projects narrower.
        width3 = min(1.92, max(1.28, 0.36 * len(label) + 0.58))
        height3 = 0.86
        q3 = np.array([center3 - u3 * width3/2 - v3 * height3/2,
                       center3 + u3 * width3/2 - v3 * height3/2,
                       center3 + u3 * width3/2 + v3 * height3/2,
                       center3 - u3 * width3/2 + v3 * height3/2], dtype=float)
        q2, _qz = _project(q3, R, cx, cy, scale)
        out.append({"kind":"face", "key":key, "label":label, "view":view, "poly":[tuple(p) for p in xy], "z":float(np.mean(z)), "normal_z":nz, "label_quad":[tuple(p) for p in q2]})
    out.sort(key=lambda r: r["z"])
    return lo, out

def point_in_poly(x, y, poly):
    inside = False; j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]; xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside

def hit(rot, width, height, x, y):
    lo, regs = regions(rot, width, height)
    hx, hy, hw, hh = lo["home"]
    if hx <= x <= hx + hw and hy <= y <= hy + hh:
        return {"kind":"home", "label":"Fit"}
    for r in reversed(regs):
        if point_in_poly(x, y, r["poly"]):
            return r
    return None

def finish_click(nav_press, x, y, threshold=4):
    """Return the stored nav action only if release stayed within click threshold.

    This lets users drag/orbit starting on the view cube without snapping to a face;
    only a true click activates a preset.
    """
    if nav_press is None:
        return None
    nav, x0, y0 = nav_press
    return nav if abs(float(x) - float(x0)) <= threshold and abs(float(y) - float(y0)) <= threshold else None

def drag_exceeded(nav_press, x, y, threshold=4):
    """True once a view-cube press has become a drag, not a click."""
    if nav_press is None:
        return False
    _nav, x0, y0 = nav_press
    return abs(float(x) - float(x0)) > threshold or abs(float(y) - float(y0)) > threshold

def _rgb(c, scale=255):
    return tuple(int(max(0, min(1, v)) * scale) for v in c)



# Final slicer-style overlay path. This overrides the earlier helper above.
def _quad_affine_coeffs(src, dst):
    """PIL affine coeffs mapping output pixels back into source pixels."""
    src = np.asarray(src[:3], dtype=float); dst = np.asarray(dst[:3], dtype=float)
    A = np.array([[dst[0,0], dst[0,1], 1, 0, 0, 0], [0, 0, 0, dst[0,0], dst[0,1], 1],
                  [dst[1,0], dst[1,1], 1, 0, 0, 0], [0, 0, 0, dst[1,0], dst[1,1], 1],
                  [dst[2,0], dst[2,1], 1, 0, 0, 0], [0, 0, 0, dst[2,0], dst[2,1], 1]], dtype=float)
    B = np.array([src[0,0], src[0,1], src[1,0], src[1,1], src[2,0], src[2,1]], dtype=float)
    return tuple(np.linalg.solve(A, B))

def _ordered_face_axes(poly):
    pts = np.asarray(poly, dtype=float)
    c = pts.mean(axis=0)
    edges = []
    for i in range(4):
        v = pts[(i + 1) % 4] - pts[i]
        edges.append((float(np.linalg.norm(v)), v))
    edges.sort(key=lambda e: e[0], reverse=True)
    u = edges[0][1]
    # Pick a non-parallel shorter edge as vertical axis.
    v = max((e[1] for e in edges[2:]), key=lambda q: abs(float(np.cross(u, q)))) if len(edges) > 2 else np.array([-u[1], u[0]])
    if np.linalg.norm(u) < 1e-6: u = np.array([1.0, 0.0])
    if np.linalg.norm(v) < 1e-6: v = np.array([0.0, 1.0])
    # Keep text readable: left-to-right and top-to-bottom on screen, but still skewed to face.
    if u[0] < 0: u = -u
    if v[1] < 0: v = -v
    return c, u, v, min(edges[0][0], edges[2][0] if len(edges) > 2 else edges[0][0])


def _draw_axis_arrow(d, a, b, color, label, font, width=4, alpha=255, label_text=True):
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    v = b - a; n = float(np.linalg.norm(v))
    if n < 2.0:
        return
    u = v / n
    p = np.array([-u[1], u[0]])
    col = _rgb(color) + (int(alpha),)
    d.line((a[0], a[1], b[0], b[1]), fill=col, width=int(width))
    head = min(10.0, max(6.0, n * 0.32))
    wing = head * 0.55
    p1 = b - u * head + p * wing
    p2 = b - u * head - p * wing
    d.polygon([tuple(b), tuple(p1), tuple(p2)], fill=col)
    if label_text:
        tp = b + u * 7.0
        d.text((tp[0], tp[1]), label, font=font, fill=col, anchor="mm")

_FONTS = {}
def _font(px):
    """One ImageFont per size for the life of the process: loading from disk per frame is what made drags stutter."""
    f = _FONTS.get(px)
    if f is None:
        from PIL import ImageFont
        try: f = ImageFont.truetype("DejaVuSans-Bold.ttf", px)
        except Exception: f = ImageFont.load_default()
        _FONTS[px] = f
    return f

def _face_text_layer(size, text, poly, fill, quad=None):
    from PIL import Image, ImageDraw
    W, H = size
    pts = np.asarray(poly, dtype=float)
    label = text.title()
    font = _font(62)
    # Fixed texture tile. It is warped onto a fixed cube-local quad from regions().
    src_w, src_h = 260, 92
    src = Image.new("RGBA", (src_w, src_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(src)
    bbox = sd.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # If a longer label needs help, fit it inside the texture once. This does not depend on view angle.
    if tw > src_w - 12:
        font = _font(50)
        bbox = sd.textbbox((0, 0), label, font=font); tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    sd.text(((src_w - tw) * 0.5 - bbox[0], (src_h - th) * 0.5 - bbox[1]), label, font=font, fill=fill)
    dst = np.asarray(quad if quad is not None else pts, dtype=float)
    # warp into a box just around the face, not the whole window: the affine is the expensive part
    x0 = int(max(0, np.floor(min(dst[:, 0].min(), pts[:, 0].min())) - 2)); y0 = int(max(0, np.floor(min(dst[:, 1].min(), pts[:, 1].min())) - 2))
    x1 = int(min(W, np.ceil(max(dst[:, 0].max(), pts[:, 0].max())) + 2)); y1 = int(min(H, np.ceil(max(dst[:, 1].max(), pts[:, 1].max())) + 2))
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    coeffs = _quad_affine_coeffs([(0,0), (src_w,0), (src_w,src_h)], dst[:3] - [x0, y0])
    small = src.transform((bw, bh), Image.Transform.AFFINE, coeffs, resample=Image.Resampling.BICUBIC)
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).polygon([(float(px) - x0, float(py) - y0) for px, py in pts], fill=255)
    small.putalpha(Image.composite(small.getchannel("A"), Image.new("L", (bw, bh), 0), mask))
    layer = Image.new("RGBA", size, (0, 0, 0, 0)); layer.paste(small, (x0, y0))
    return layer

def _draw_home_svg_icon(d, cx, cy, scale, color):
    # Same geometry as assets/icons/svg/home.svg: viewBox 0 0 24 24,
    # path d="M2.5 10.5 12 2.5l9.5 8M5 8.5V21h5v-7h4v7h5V8.5"
    def pt(x, y): return (cx + (float(x) - 12.0) * scale, cy + (float(y) - 12.0) * scale)
    d.line([pt(2.5, 10.5), pt(12, 2.5), pt(21.5, 10.5)], fill=color, width=max(1, int(round(1.65 * scale))), joint="curve")
    d.line([pt(5, 8.5), pt(5, 21), pt(10, 21), pt(10, 14), pt(14, 14), pt(14, 21), pt(19, 21), pt(19, 8.5)], fill=color, width=max(1, int(round(1.65 * scale))), joint="curve")

_OVERLAY_CACHE = {}
def overlay_cached(width, height, rot, hover=None, show_home=True):
    """draw_overlay_image() memoised on what it depends on (rotation to 3 decimals, hover, size): a redraw
    that did not turn the model reuses the last image instead of rasterising the cube again."""
    R = np.asarray(rot, dtype=float)
    key = (int(width), int(height), tuple(np.round(R.ravel(), 3)), hover, bool(show_home))
    img = _OVERLAY_CACHE.get(key)
    if img is None:
        if len(_OVERLAY_CACHE) > 8: _OVERLAY_CACHE.clear()
        img = draw_overlay_image(width, height, R, hover, show_home); _OVERLAY_CACHE[key] = img
    return img

def draw_overlay_image(width, height, rot, hover=None, show_home=True):
    from PIL import Image, ImageDraw
    width, height = int(width), int(height)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    lo, regs = regions(rot, width, height)
    _lo_axes, axes = axis_segments(rot, width, height)
    axis_font = _font(12)
    for r in regs:
        c = HOVER if hover == r["key"] else COLORS.get(r["key"], COLORS["front"])
        d.polygon(r["poly"], fill=_rgb(c)+(172,), outline=_rgb(EDGE)+(255,))
    # Draw one crisp triad over the translucent cube, then labels last. This matches slicer
    # view cubes better than hiding the axes behind the faces or double-painting them.
    for key in ("x", "y", "z"):
        a, b = axes[key]
        _draw_axis_arrow(d, a, b, AXIS[key], key, axis_font)
    for r in regs:
        img.alpha_composite(_face_text_layer((width, height), r["label"], r["poly"], _rgb(TEXT)+(255,), r.get("label_quad")))
    if show_home:
        hx, hy, hw, hh = lo["home"]; cx=hx+hw*.5; cy=hy+hh*.5
        d.ellipse((hx,hy,hx+hw,hy+hh), fill=(15,20,30,235), outline=(55,67,88,255), width=1)
        _draw_home_svg_icon(d, cx, cy, 0.78, (214,230,255,255))
    return img

def draw_pil(img, rot, hover=None, show_home=True):
    base = img.convert("RGBA")
    base.alpha_composite(draw_overlay_image(base.size[0], base.size[1], rot, hover, show_home=show_home))
    return base.convert(img.mode) if img.mode != "RGBA" else base
