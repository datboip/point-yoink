#!/usr/bin/env python3
# GPU 3D view for PointYoink, embedded in the Tk window: the full mesh, smooth shading, on the
# graphics card (OpenGL through pyopengltk + PyOpenGL, fixed-function pipeline so it runs on
# anything with GL 1.5+). Same mouse language as meshview.MeshView (the software fallback):
# drag rotates, scroll zooms, right-drag pans, double-click resets; solid or wireframe.
# If a GL context cannot be created (no GLX, headless, VM), .failed becomes True and the app
# swaps in the software view instead.
import threading, time, ctypes, ctypes.util
import numpy as np
from pyopengltk import OpenGLFrame
from OpenGL import GL, GLU, GLX
import shade

MAX_FACES = 3_000_000                 # bound VRAM and load time; above this we decimate

# pyopengltk creates the GL context on its OWN Xlib connection; an X error there (seen on this
# NVIDIA/GNOME desktop: GLXBadDrawable from glXMakeContextCurrent, inside the app only, while the
# same widget alone works) goes to Xlib's default handler, which prints and _exit()s the whole
# program. Install a recording handler for the duration of context creation so it becomes a
# normal failure (.failed -> the app swaps in the software view) instead of killing the app.
_x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
class _XErrorEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("display", ctypes.c_void_p), ("resourceid", ctypes.c_ulong),
                ("serial", ctypes.c_ulong), ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte), ("minor_code", ctypes.c_ubyte)]
_XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))
_x11.XSetErrorHandler.argtypes = [_XErrorHandler]; _x11.XSetErrorHandler.restype = _XErrorHandler
_x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]; _x11.XSync.restype = ctypes.c_int
_xerrors = []
@_XErrorHandler
def _record_xerror(_disp, ev):
    try: _xerrors.append((ev.contents.error_code, ev.contents.request_code, ev.contents.minor_code))
    except Exception: _xerrors.append((-1, -1, -1))
    return 0

class GLView(OpenGLFrame):
    def tkCreateContext(self):
        # Tk synthesizes <Map> for child windows right after queuing XMapWindow, without a server
        # round-trip, and pyopengltk then uses this window's XID on its OWN X connection. If Tk's
        # CreateWindow/MapWindow are still sitting unflushed in its output buffer, the server has
        # never heard of the drawable and glXMakeContextCurrent fails with GLXBadDrawable - which
        # is what happened inside the app (bigger buffer, consistently) but not in a tiny test
        # window. winfo_rootx() is a round-trip (XTranslateCoordinates) on Tk's connection, so
        # everything queued before it is on the server before the GLX request goes out.
        # (winfo_rootx is NOT a round-trip for child windows - Tk answers from cache; XQueryPointer is.)
        try: self.winfo_pointerxy()
        except Exception: pass
        del _xerrors[:]
        prev = _x11.XSetErrorHandler(_record_xerror)
        try:
            super().tkCreateContext()
            try: _x11.XSync(ctypes.cast(self._OpenGLFrame__window, ctypes.c_void_p), 0)   # deliver any pending error now
            except Exception: pass
        finally:
            try: _x11.XSetErrorHandler(prev)
            except Exception: pass
        if _xerrors:
            e = _xerrors[0]
            raise RuntimeError("X error %d during GL context creation (request %d.%d)" % e)
        try:
            if not GLX.glXGetCurrentContext(): raise RuntimeError("no current GL context after creation")
        except RuntimeError: raise
        except Exception: pass
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        # Create the X window now, at construction, rather than in the same idle cycle that maps it
        # (see tkCreateContext): by the time the view is shown, the server has long known the XID.
        try: self.winfo_id()
        except Exception: pass
        self.failed = False; self.ready = False; self.wire = False
        self.azim, self.elev, self.zoom, self.pan = -35.0, 30.0, 1.0, [0.0, 0.0]
        self.rot = self._default_rot()         # free rotation: a 4x4 the drag turns about the screen axes, no limits
        self._drag = None; self._gen = 0; self._pending = None; self._n = 0; self._vbo = None
        self._nw = 0; self._src = None; self._wire_gen = None    # set again by _upload; must exist before the first upload (Wireframe clicked early)
        self.animate = 0
        self.tf = None                         # orientation transform of the loaded mesh (shade.load_oriented_tf)
        self.markers = []                      # [(xyz in view coords, (r,g,b))] drawn as dots
        self.layers = []                       # extra meshes drawn tinted: [{"vbo","n","colour"}]
        self.tint = None                       # (r,g,b) for the main mesh, None = default material
        self.on_pick = None                    # callback(world_xyz_mm, view_xyz) for a plain left click
        self._press_at = None
        self._cvbo = None; self._ncol = 0      # optional per-vertex colours (set_colors)
        self._split = None                     # optional (ibo_a, n_a, colour_a, ibo_b, n_b, colour_b): the mesh drawn as two parts
        self._split_req = None                 # (mask, colour_keep, colour_gone) to (re)apply after an upload
        self.plane = None                      # optional translucent quad: (centre_view_xyz, normal_view_xyz, half_size)
        self.bind("<ButtonPress-1>", self._press); self.bind("<B1-Motion>", self._rotate)
        self.bind("<ButtonPress-3>", self._press); self.bind("<B3-Motion>", self._pan)
        self.bind("<ButtonPress-2>", self._press); self.bind("<B2-Motion>", self._pan)
        self.bind("<ButtonRelease-1>", self._release); self.bind("<ButtonRelease-3>", self._release); self.bind("<ButtonRelease-2>", self._release)
        self.bind("<MouseWheel>", self._wheel); self.bind("<Button-4>", lambda e: self._wheel(e, 1)); self.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.bind("<Double-Button-1>", lambda e: self.reset())
        # pyopengltk only switches GL context while the widget is on screen. A hidden view must never touch GL
        # (its calls would land in another view's context and wreck its buffers), so uploads wait for <Map>.
        self.bind("<Map>", self._on_map, add="+")
    def _mapped(self):
        try: return bool(self.winfo_ismapped())
        except Exception: return False
    def _on_map(self, e=None):
        if not self.ready or self.failed: return
        if self._pending is not None:
            p = self._pending; self._pending = None; self._upload(*p)
        elif self._split_req is not None and self._split is None and getattr(self, "_src", None) is not None:
            self.set_split(*self._split_req)
        else:
            self.draw()
    @staticmethod
    def _axis_rot(deg, x, y, z):
        a = np.radians(deg); c, s_ = np.cos(a), np.sin(a); n = np.array([x, y, z], float); n /= np.linalg.norm(n)
        K = np.array([[0, -n[2], n[1]], [n[2], 0, -n[0]], [-n[1], n[0], 0]]); R = np.eye(4); R[:3, :3] = np.eye(3) + s_ * K + (1 - c) * K @ K; return R
    def _default_rot(self):
        return self._axis_rot(self.elev - 90.0, 1, 0, 0) @ self._axis_rot(self.azim, 0, 0, 1)
    def _mult_rot(self):
        GL.glMultMatrixf(np.ascontiguousarray(self.rot.T, dtype=np.float32))     # GL wants column-major
    # ---- context ----
    def tkMap(self, evt):
        try: super().tkMap(evt)
        except Exception as e:
            self.failed = True; self._err = e
    def initgl(self):
        try:
            GL.glClearColor(10 / 255.0, 12 / 255.0, 16 / 255.0, 1.0)
            GL.glEnable(GL.GL_DEPTH_TEST); GL.glEnable(GL.GL_NORMALIZE)
            GL.glEnable(GL.GL_LIGHTING); GL.glEnable(GL.GL_LIGHT0); GL.glEnable(GL.GL_LIGHT1)
            GL.glLightfv(GL.GL_LIGHT0, GL.GL_DIFFUSE, (0.85, 0.87, 0.9, 1.0)); GL.glLightfv(GL.GL_LIGHT0, GL.GL_SPECULAR, (0.25, 0.25, 0.25, 1.0))
            GL.glLightfv(GL.GL_LIGHT1, GL.GL_DIFFUSE, (0.30, 0.32, 0.36, 1.0)); GL.glLightfv(GL.GL_LIGHT1, GL.GL_SPECULAR, (0, 0, 0, 1))
            GL.glLightModelfv(GL.GL_LIGHT_MODEL_AMBIENT, (0.22, 0.23, 0.25, 1.0))
            GL.glMaterialfv(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE, (0.74, 0.76, 0.80, 1.0))
            GL.glMaterialfv(GL.GL_FRONT_AND_BACK, GL.GL_SPECULAR, (0.18, 0.18, 0.18, 1.0)); GL.glMaterialf(GL.GL_FRONT_AND_BACK, GL.GL_SHININESS, 28.0)
            GL.glLightModeli(GL.GL_LIGHT_MODEL_TWO_SIDE, 1)
            self.ready = True
            if self._pending is not None: self._upload(*self._pending); self._pending = None
        except Exception as e:
            self.failed = True; self._err = e
    # ---- loading ----
    def load(self, path, on_ready=None, max_faces=MAX_FACES):
        """Load (and orient, and smooth-normal) the mesh in a worker thread, then upload to the GPU."""
        self._gen += 1; gen = self._gen; self._n = 0
        def work():
            try:
                v, f, tf = shade.load_oriented_tf(path, max_faces)
                if gen != self._gen: return                       # a newer load superseded this one: stop early
                self.tf = tf
                import trimesh
                nrm = np.asarray(trimesh.Trimesh(v, f, process=False).vertex_normals, dtype=np.float32)
                if gen != self._gen: return
                # wireframe on the full mesh is a solid blob: it is drawn from a decimated copy, made lazily
                # (see _wire_data) so the solid view shows sooner
                res = (np.ascontiguousarray(v, dtype=np.float32), nrm, np.ascontiguousarray(f, dtype=np.uint32), None)
            except Exception as e:
                res = e
            try: self.after(0, lambda: self._loaded(gen, res, on_ready))
            except Exception: pass                                 # the widget (or the app) is gone
        threading.Thread(target=work, daemon=True).start()
    def _alive(self):
        try: return bool(self.winfo_exists())
        except Exception: return False
    def _loaded(self, gen, res, on_ready):
        if gen != self._gen or not self._alive(): return       # a newer load, or the window holding this view was closed
        if isinstance(res, Exception) or self.failed:
            (on_ready and on_ready(False)); return
        v, n, f, wire = res
        self.markers = []; self.plane = None; self._ncol = 0; self._split_req = None; self.clear_layers(draw=False); self.reset(draw=False)
        if self.ready: self._upload(v, n, f, wire, on_ready)
        else: self._pending = (v, n, f, wire, on_ready)   # on_ready fires later, from _upload(), once it actually runs (initgl() or _on_map())
    def _upload(self, v, n, f, wire, on_ready=None):
        if not self._mapped(): self._pending = (v, n, f, wire, on_ready); return       # done on <Map>
        try:
            self.tkMakeCurrent()
            if self._vbo is not None: GL.glDeleteBuffers(5, self._vbo)   # (a numpy array: never test it for truth)
            self._vbo = GL.glGenBuffers(5)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[0]); GL.glBufferData(GL.GL_ARRAY_BUFFER, v.nbytes, v, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[1]); GL.glBufferData(GL.GL_ARRAY_BUFFER, n.nbytes, n, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._vbo[2]); GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, f.nbytes, f, GL.GL_STATIC_DRAW)
            self._n = int(f.size); self._nw = 0; self._zmax = float(v[:, 2].max()); self._src = (v, f); self._wire_gen = None
            if self._split is not None:                                 # index sets belong to the old vertices: drop them
                try: GL.glDeleteBuffers(2, [int(self._split[0]), int(self._split[3])])
                except Exception: pass
                self._split = None
            if wire: self._upload_wire(*wire)
            self._display()
            if self._split_req is not None and len(self._split_req[0]) == len(f): self.set_split(*self._split_req)
            (on_ready and on_ready(True))     # only now is view._src actually set - firing this any earlier is a race (found 2026-09-14)
        except Exception as e:
            self.failed = True; self._err = e
            (on_ready and on_ready(False))
    def _upload_wire(self, wv, wf):
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[3]); GL.glBufferData(GL.GL_ARRAY_BUFFER, wv.nbytes, wv, GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._vbo[4]); GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, wf.nbytes, wf, GL.GL_STATIC_DRAW)
        self._nw = int(wf.size)
    def _wire_data(self):
        """Decimated copy for wireframe, made in a thread the first time it is needed."""
        if self._nw or getattr(self, "_wire_gen", None) == self._gen or not getattr(self, "_src", None): return
        self._wire_gen = gen = self._gen; v, f = self._src
        def work():
            try:
                import fast_simplification
                wv, wf = fast_simplification.simplify(v, f, target_count=min(len(f), 80000))
                res = (np.ascontiguousarray(wv, dtype=np.float32), np.ascontiguousarray(wf, dtype=np.uint32))
            except Exception:
                res = (v, np.ascontiguousarray(f[::max(1, len(f) // 80000)], dtype=np.uint32))
            if gen == self._gen:
                try: self.after(0, lambda: self._alive() and self._mapped() and (self.tkMakeCurrent(), self._upload_wire(*res), self.draw()))
                except Exception: pass
        threading.Thread(target=work, daemon=True).start()
    # ---- drawing ----
    def redraw(self):
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        GL.glViewport(0, 0, w, h)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glMatrixMode(GL.GL_PROJECTION); GL.glLoadIdentity(); GLU.gluPerspective(32.0, w / float(h), 0.05, 50.0)
        GL.glMatrixMode(GL.GL_MODELVIEW); GL.glLoadIdentity()
        GL.glTranslatef(self.pan[0] * 2.0, self.pan[1] * 2.0, -4.2 / self.zoom)
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_POSITION, (-0.5, 0.8, 1.0, 0.0)); GL.glLightfv(GL.GL_LIGHT1, GL.GL_POSITION, (0.8, -0.3, 0.4, 0.0))
        self._mult_rot()
        GL.glTranslatef(0, 0, -0.5 * getattr(self, "_zmax", 0.0))
        self._mv_m = GL.glGetDoublev(GL.GL_MODELVIEW_MATRIX); self._pj_m = GL.glGetDoublev(GL.GL_PROJECTION_MATRIX); self._vp = (0, 0, w, h)
        # floor grid
        GL.glDisable(GL.GL_LIGHTING); GL.glColor3f(26 / 255.0, 33 / 255.0, 48 / 255.0); GL.glBegin(GL.GL_LINES)
        for t in np.linspace(-1.5, 1.5, 13):
            GL.glVertex3f(t, -1.5, 0); GL.glVertex3f(t, 1.5, 0); GL.glVertex3f(-1.5, t, 0); GL.glVertex3f(1.5, t, 0)
        GL.glEnd()
        if self._n:
            if self.wire and self._nw:
                GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE); GL.glColor3f(110 / 255.0, 170 / 255.0, 1.0); GL.glLineWidth(1.0)
                GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[3]); GL.glVertexPointer(3, GL.GL_FLOAT, 0, None)
                GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._vbo[4]); GL.glDrawElements(GL.GL_TRIANGLES, self._nw, GL.GL_UNSIGNED_INT, None)
                GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
            else:
                GL.glEnable(GL.GL_LIGHTING); GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
                GL.glMaterialfv(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE, (self.tint + (1.0,)) if self.tint else (0.74, 0.76, 0.80, 1.0))
                GL.glEnableClientState(GL.GL_VERTEX_ARRAY); GL.glEnableClientState(GL.GL_NORMAL_ARRAY)
                use_col = self._cvbo is not None and self._ncol
                if use_col:
                    GL.glEnable(GL.GL_COLOR_MATERIAL); GL.glColorMaterial(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE)
                    GL.glEnableClientState(GL.GL_COLOR_ARRAY); GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._cvbo); GL.glColorPointer(3, GL.GL_FLOAT, 0, None)
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[0]); GL.glVertexPointer(3, GL.GL_FLOAT, 0, None)
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[1]); GL.glNormalPointer(GL.GL_FLOAT, 0, None)
                if self._split is not None:
                    ia, na, ca, ib, nb, cb = self._split
                    for ibo, cnt, col in ((ia, na, ca), (ib, nb, cb)):
                        if not cnt: continue
                        GL.glMaterialfv(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE, col + (1.0,))
                        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibo); GL.glDrawElements(GL.GL_TRIANGLES, cnt, GL.GL_UNSIGNED_INT, None)
                else:
                    GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._vbo[2]); GL.glDrawElements(GL.GL_TRIANGLES, self._n, GL.GL_UNSIGNED_INT, None)
                GL.glDisableClientState(GL.GL_VERTEX_ARRAY); GL.glDisableClientState(GL.GL_NORMAL_ARRAY)
                if use_col: GL.glDisableClientState(GL.GL_COLOR_ARRAY); GL.glDisable(GL.GL_COLOR_MATERIAL)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0); GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, 0)
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
        for L in self.layers:                  # tinted overlays (another scan, for alignment checks)
            GL.glEnable(GL.GL_LIGHTING); GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
            GL.glMaterialfv(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE, L["colour"] + (1.0,))
            GL.glEnableClientState(GL.GL_VERTEX_ARRAY); GL.glEnableClientState(GL.GL_NORMAL_ARRAY)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, L["vbo"][0]); GL.glVertexPointer(3, GL.GL_FLOAT, 0, None)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, L["vbo"][1]); GL.glNormalPointer(GL.GL_FLOAT, 0, None)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, L["vbo"][2]); GL.glDrawElements(GL.GL_TRIANGLES, L["n"], GL.GL_UNSIGNED_INT, None)
            GL.glDisableClientState(GL.GL_VERTEX_ARRAY); GL.glDisableClientState(GL.GL_NORMAL_ARRAY)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0); GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, 0)
        GL.glMaterialfv(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE, (0.74, 0.76, 0.80, 1.0))
        if self.plane is not None:             # the cut plane: a translucent amber square with its normal
            c, nrm, hs = self.plane; nrm = np.asarray(nrm, float); nrm /= (np.linalg.norm(nrm) + 1e-9)
            a = np.array([1.0, 0, 0]) if abs(nrm[0]) < 0.9 else np.array([0, 1.0, 0]); u = np.cross(nrm, a); u /= np.linalg.norm(u); v = np.cross(nrm, u)
            GL.glDisable(GL.GL_LIGHTING); GL.glEnable(GL.GL_BLEND); GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA); GL.glDepthMask(GL.GL_FALSE)
            GL.glColor4f(1.0, 0.69, 0.13, 0.16); GL.glBegin(GL.GL_QUADS)
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                q = np.asarray(c) + u * (sx * hs) + v * (sy * hs); GL.glVertex3f(*q)
            GL.glEnd(); GL.glDepthMask(GL.GL_TRUE); GL.glDisable(GL.GL_BLEND)
            GL.glColor3f(1.0, 0.69, 0.13); GL.glLineWidth(2.0); GL.glBegin(GL.GL_LINES); GL.glVertex3f(*c); GL.glVertex3f(*(np.asarray(c) + nrm * hs * 0.4)); GL.glEnd(); GL.glLineWidth(1.0)
        if self.markers:                       # numbered pick points, always on top
            GL.glDisable(GL.GL_LIGHTING); GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glPointSize(18.0); GL.glBegin(GL.GL_POINTS)                     # dark rim
            for xyz, col in self.markers: GL.glColor3f(0.04, 0.05, 0.07); GL.glVertex3f(*xyz)
            GL.glEnd(); GL.glPointSize(12.0); GL.glBegin(GL.GL_POINTS)          # the colour
            for xyz, col in self.markers: GL.glColor3f(*col); GL.glVertex3f(*xyz)
            GL.glEnd(); GL.glPointSize(1.0); GL.glEnable(GL.GL_DEPTH_TEST)
        # axis gizmo, bottom-left, rotating with the view: X red, Y green, Z blue
        GL.glDisable(GL.GL_LIGHTING); GL.glDisable(GL.GL_DEPTH_TEST)
        g = int(min(w, h) * 0.22); GL.glViewport(10, 10, g, g)
        GL.glMatrixMode(GL.GL_PROJECTION); GL.glLoadIdentity(); GL.glOrtho(-1.3, 1.3, -1.3, 1.3, -2, 2)
        GL.glMatrixMode(GL.GL_MODELVIEW); GL.glLoadIdentity(); self._mult_rot()
        GL.glLineWidth(2.0); GL.glBegin(GL.GL_LINES)
        for col, ax in (((1.0, 0.36, 0.42), (1, 0, 0)), ((0.24, 0.81, 0.56), (0, 1, 0)), ((0.35, 0.69, 1.0), (0, 0, 1))):
            GL.glColor3f(*col); GL.glVertex3f(0, 0, 0); GL.glVertex3f(*ax)
        GL.glEnd(); GL.glLineWidth(1.0); GL.glEnable(GL.GL_DEPTH_TEST)
    def draw(self, hi=False):
        if self.ready and not self.failed and self._alive() and self._mapped():
            try: self._display()
            except Exception as e: self.failed = True; self._err = e
    def set_wire(self, on):
        self.wire = bool(on)
        if self.wire: self._wire_data()
        self.draw()
    def reset(self, draw=True):
        self.azim, self.elev, self.zoom, self.pan = -35.0, 30.0, 1.0, [0.0, 0.0]; self.rot = self._default_rot()
        if draw: self.draw()
    def snapshot(self, path, size=None):
        """PNG of the current view read back from the GPU."""
        try:
            from PIL import Image
            if not self._mapped(): return None
            self.tkMakeCurrent(); w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
            self.redraw(); GL.glReadBuffer(GL.GL_BACK)
            data = GL.glReadPixels(0, 0, w, h, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
            img = Image.frombytes("RGB", (w, h), data).transpose(Image.FLIP_TOP_BOTTOM)
            if size: img = img.resize(size, Image.LANCZOS)
            img.save(path); return path
        except Exception:
            return None
    # ---- picking and overlays ----
    def pick(self, x, y):
        """The 3D point under window pixel (x, y): (world_xyz_mm, view_xyz), or None off the mesh."""
        if not self.ready or self.failed or not self._n or self.tf is None or not self._mapped(): return None
        try:
            self.tkMakeCurrent(); self.redraw()
            h = max(1, self.winfo_height()); yy = h - 1 - y
            z = float(GL.glReadPixels(x, yy, 1, 1, GL.GL_DEPTH_COMPONENT, GL.GL_FLOAT)[0][0])
            if z >= 0.9999: return None
            vx, vy, vz = GLU.gluUnProject(x, yy, z, self._mv_m, self._pj_m, self._vp)
            view = np.array([vx, vy, vz]); return shade.view_to_world(view, self.tf), view
        except Exception:
            return None
    def set_colors(self, rgb):
        """Per-vertex colours (Nx3 float32, 0..1) for the main mesh; None goes back to the plain material."""
        if not self._mapped(): return
        try:
            self.tkMakeCurrent()
            if rgb is None:
                if self._cvbo is not None: GL.glDeleteBuffers(1, [int(self._cvbo)])
                self._cvbo = None; self._ncol = 0; self.draw(); return
            rgb = np.ascontiguousarray(rgb, dtype=np.float32)
            if self._cvbo is None: self._cvbo = int(GL.glGenBuffers(1))
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._cvbo); GL.glBufferData(GL.GL_ARRAY_BUFFER, rgb.nbytes, rgb, GL.GL_DYNAMIC_DRAW)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0); self._ncol = len(rgb); self.draw()
        except Exception as e:
            self._err = e
    def set_split(self, keep_face_mask, colour_keep=(0.74, 0.76, 0.80), colour_gone=(1.0, 0.36, 0.42)):
        """Draw the mesh as two parts with plain materials (no colour array): faces where the mask is True in
        colour_keep, the rest in colour_gone. None goes back to one part."""
        self._split_req = None if keep_face_mask is None else (np.asarray(keep_face_mask, bool), tuple(colour_keep), tuple(colour_gone))
        if not self.ready or self.failed or not self._mapped(): return   # applied by _upload / <Map> once the view is on screen
        try:
            self.tkMakeCurrent()
            if self._split is not None:
                GL.glDeleteBuffers(2, [int(self._split[0]), int(self._split[3])]); self._split = None
            if keep_face_mask is None or getattr(self, "_src", None) is None: self.draw(); return
            f = np.asarray(self._src[1]); m = np.asarray(keep_face_mask, bool)
            if len(m) != len(f) or (f.size and int(f.max()) >= len(self._src[0])):
                self._err = "split mismatch: mask %d faces %d verts %d" % (len(m), len(f), len(self._src[0])); self.draw(); return
            fa = np.ascontiguousarray(f[m], dtype=np.uint32); fb = np.ascontiguousarray(f[~m], dtype=np.uint32)
            ibos = GL.glGenBuffers(2)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibos[0]); GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, max(4, fa.nbytes), fa if fa.size else None, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibos[1]); GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, max(4, fb.nbytes), fb if fb.size else None, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, 0)
            self._split = (int(ibos[0]), int(fa.size), tuple(colour_keep), int(ibos[1]), int(fb.size), tuple(colour_gone)); self.draw()
        except Exception as e:
            self._err = e
    def add_layer(self, path, matrix=None, colour=(1.0, 0.55, 0.25), on_ready=None):
        """Draw another mesh in this view, tinted, optionally moved by a 4x4 (in mm, world coords) first."""
        gen = self._gen
        def work():
            try:
                import trimesh
                v, f, _ = shade.load_oriented_tf(path, MAX_FACES // 2, tf={"mean": np.zeros(3), "scale": 1.0, "R": np.eye(3), "zshift": 0.0})
                if matrix is not None:
                    M = np.asarray(matrix, dtype=np.float64); v = (v @ M[:3, :3].T) + M[:3, 3]
                vv = np.ascontiguousarray(shade.world_to_view(v, self.tf), dtype=np.float32) if self.tf else np.ascontiguousarray(v, dtype=np.float32)
                nrm = np.asarray(trimesh.Trimesh(vv, f, process=False).vertex_normals, dtype=np.float32)
                res = (vv, nrm, np.ascontiguousarray(f, dtype=np.uint32))
            except Exception as e:
                res = e
            def up():
                if gen != self._gen or not self._alive() or not self._mapped(): return
                if isinstance(res, Exception): (on_ready and on_ready(False)); return
                try:
                    self.tkMakeCurrent(); vbo = GL.glGenBuffers(3); v, n, f = res
                    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo[0]); GL.glBufferData(GL.GL_ARRAY_BUFFER, v.nbytes, v, GL.GL_STATIC_DRAW)
                    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo[1]); GL.glBufferData(GL.GL_ARRAY_BUFFER, n.nbytes, n, GL.GL_STATIC_DRAW)
                    GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, vbo[2]); GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, f.nbytes, f, GL.GL_STATIC_DRAW)
                    self.layers.append({"vbo": vbo, "n": int(f.size), "colour": tuple(colour)}); self.draw(); (on_ready and on_ready(True))
                except Exception:
                    (on_ready and on_ready(False))
            try: self.after(0, up)
            except Exception: pass
        threading.Thread(target=work, daemon=True).start()
    def clear_layers(self, draw=True):
        try:
            if self.layers and self.ready and self._mapped(): self.tkMakeCurrent()
            elif self.layers: self.layers = []; return
            for L in self.layers: GL.glDeleteBuffers(3, L["vbo"])
        except Exception: pass
        self.layers = []
        if draw: self.draw()
    # ---- mouse ----
    def _press(self, e): self._drag = (e.x, e.y); self._press_at = (e.x, e.y)
    def _release(self, e):
        self._drag = None
        if self.on_pick and self._press_at and abs(e.x - self._press_at[0]) <= 8 and abs(e.y - self._press_at[1]) <= 8 and e.num == 1:   # a click, not a drag
            r = self.pick(e.x, e.y)
            if r is not None:
                try: self.on_pick(*r)
                except Exception: pass
        self._press_at = None
    def _rotate(self, e):
        if not self._drag: return
        if self._press_at and abs(e.x - self._press_at[0]) <= 8 and abs(e.y - self._press_at[1]) <= 8: return   # still within a click
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]; self._drag = (e.x, e.y)
        # turn about the screen's own axes, so any orientation is reachable and nothing ever locks
        self.rot = self._axis_rot(dy * 0.5, 1, 0, 0) @ self._axis_rot(dx * 0.5, 0, 1, 0) @ self.rot; self.draw()
    def _pan(self, e):
        if not self._drag: return
        w, h = max(64, self.winfo_width()), max(64, self.winfo_height())
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]; self._drag = (e.x, e.y)
        self.pan[0] += dx / (w * 0.5); self.pan[1] -= dy / (h * 0.5); self.draw()
    def _wheel(self, e, direction=None):
        d = direction if direction is not None else (1 if e.delta > 0 else -1)
        self.zoom = max(0.2, min(8.0, self.zoom * (1.12 if d > 0 else 1 / 1.12))); self.draw()
