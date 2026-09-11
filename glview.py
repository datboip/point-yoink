#!/usr/bin/env python3
# GPU 3D view for PointYoink, embedded in the Tk window: the full mesh, smooth shading, on the
# graphics card (OpenGL through pyopengltk + PyOpenGL, fixed-function pipeline so it runs on
# anything with GL 1.5+). Same mouse language as meshview.MeshView (the software fallback):
# drag rotates, scroll zooms, right-drag pans, double-click resets; solid or wireframe.
# If a GL context cannot be created (no GLX, headless, VM), .failed becomes True and the app
# swaps in the software view instead.
import threading, time
import numpy as np
from pyopengltk import OpenGLFrame
from OpenGL import GL, GLU
import shade

MAX_FACES = 3_000_000                 # bound VRAM and load time; above this we decimate

class GLView(OpenGLFrame):
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.failed = False; self.ready = False; self.wire = False
        self.azim, self.elev, self.zoom, self.pan = -35.0, 30.0, 1.0, [0.0, 0.0]
        self.rot = self._default_rot()         # free rotation: a 4x4 the drag turns about the screen axes, no limits
        self._drag = None; self._gen = 0; self._pending = None; self._n = 0; self._vbo = None
        self.animate = 0
        self.tf = None                         # orientation transform of the loaded mesh (shade.load_oriented_tf)
        self.markers = []                      # [(xyz in view coords, (r,g,b))] drawn as dots
        self.layers = []                       # extra meshes drawn tinted: [{"vbo","n","colour"}]
        self.tint = None                       # (r,g,b) for the main mesh, None = default material
        self.on_pick = None                    # callback(world_xyz_mm, view_xyz) for a plain left click
        self._press_at = None
        self.bind("<ButtonPress-1>", self._press); self.bind("<B1-Motion>", self._rotate)
        self.bind("<ButtonPress-3>", self._press); self.bind("<B3-Motion>", self._pan)
        self.bind("<ButtonPress-2>", self._press); self.bind("<B2-Motion>", self._pan)
        self.bind("<ButtonRelease-1>", self._release); self.bind("<ButtonRelease-3>", self._release); self.bind("<ButtonRelease-2>", self._release)
        self.bind("<MouseWheel>", self._wheel); self.bind("<Button-4>", lambda e: self._wheel(e, 1)); self.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.bind("<Double-Button-1>", lambda e: self.reset())
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
            self.after(0, lambda: self._loaded(gen, res, on_ready))
        threading.Thread(target=work, daemon=True).start()
    def _loaded(self, gen, res, on_ready):
        if gen != self._gen: return
        if isinstance(res, Exception) or self.failed:
            (on_ready and on_ready(False)); return
        v, n, f, wire = res
        self.markers = []; self.clear_layers(draw=False); self.reset(draw=False)
        if self.ready: self._upload(v, n, f, wire)
        else: self._pending = (v, n, f, wire)
        (on_ready and on_ready(not self.failed))
    def _upload(self, v, n, f, wire):
        try:
            self.tkMakeCurrent()
            if self._vbo is not None: GL.glDeleteBuffers(5, self._vbo)   # (a numpy array: never test it for truth)
            self._vbo = GL.glGenBuffers(5)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[0]); GL.glBufferData(GL.GL_ARRAY_BUFFER, v.nbytes, v, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[1]); GL.glBufferData(GL.GL_ARRAY_BUFFER, n.nbytes, n, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._vbo[2]); GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, f.nbytes, f, GL.GL_STATIC_DRAW)
            self._n = int(f.size); self._nw = 0; self._zmax = float(v[:, 2].max()); self._src = (v, f); self._wire_gen = None
            if wire: self._upload_wire(*wire)
            self._display()
        except Exception as e:
            self.failed = True; self._err = e
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
            if gen == self._gen: self.after(0, lambda: (self.tkMakeCurrent(), self._upload_wire(*res), self.draw()))
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
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[0]); GL.glVertexPointer(3, GL.GL_FLOAT, 0, None)
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[1]); GL.glNormalPointer(GL.GL_FLOAT, 0, None)
                GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._vbo[2]); GL.glDrawElements(GL.GL_TRIANGLES, self._n, GL.GL_UNSIGNED_INT, None)
                GL.glDisableClientState(GL.GL_VERTEX_ARRAY); GL.glDisableClientState(GL.GL_NORMAL_ARRAY)
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
        if self.ready and not self.failed:
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
        if not self.ready or self.failed or not self._n or self.tf is None: return None
        try:
            self.tkMakeCurrent(); self.redraw()
            h = max(1, self.winfo_height()); yy = h - 1 - y
            z = float(GL.glReadPixels(x, yy, 1, 1, GL.GL_DEPTH_COMPONENT, GL.GL_FLOAT)[0][0])
            if z >= 0.9999: return None
            vx, vy, vz = GLU.gluUnProject(x, yy, z, self._mv_m, self._pj_m, self._vp)
            view = np.array([vx, vy, vz]); return shade.view_to_world(view, self.tf), view
        except Exception:
            return None
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
                if gen != self._gen: return
                if isinstance(res, Exception): (on_ready and on_ready(False)); return
                try:
                    self.tkMakeCurrent(); vbo = GL.glGenBuffers(3); v, n, f = res
                    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo[0]); GL.glBufferData(GL.GL_ARRAY_BUFFER, v.nbytes, v, GL.GL_STATIC_DRAW)
                    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo[1]); GL.glBufferData(GL.GL_ARRAY_BUFFER, n.nbytes, n, GL.GL_STATIC_DRAW)
                    GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, vbo[2]); GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, f.nbytes, f, GL.GL_STATIC_DRAW)
                    self.layers.append({"vbo": vbo, "n": int(f.size), "colour": tuple(colour)}); self.draw(); (on_ready and on_ready(True))
                except Exception:
                    (on_ready and on_ready(False))
            self.after(0, up)
        threading.Thread(target=work, daemon=True).start()
    def clear_layers(self, draw=True):
        try:
            if self.layers and self.ready: self.tkMakeCurrent()
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
