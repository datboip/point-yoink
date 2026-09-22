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
import viewnav

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
        self.azim, self.elev, self.zoom, self.pan = -35.0, 30.0, 1.10, [0.0, 0.0]
        self.rot = self._default_rot()         # free rotation: a 4x4 the drag turns about the screen axes, no limits
        self._drag = None; self._gen = 0; self._pending = None; self._n = 0; self._vbo = None
        self._nav_hover = None
        self._nw = 0; self._src = None; self._wire_gen = None    # set again by _upload; must exist before the first upload (Wireframe clicked early)
        self._pvbo = None; self._pts_n = 0; self._pending_pts = None   # point-cloud view (Fused points): separate, additive path; never touches the mesh draw
        self._pcvbo = None; self._pts_v = None; self._pts_sel = None; self._pts_undo = []   # point editing: colour vbo, view-space points, selection mask, undo stack
        self.edit_session = None                                  # authoritative native-coordinate edit document
        self.on_points_change = None                                   # callback(kept_count) after an edit, for the editor UI
        self.on_brush_size = None                                      # callback(px) when scroll resizes the brush, so the palette slider tracks it
        self._depth_failed = False                                    # set when visible-only wanted depth but the read failed (app warns)
        self.edit_target = "points"    # "points" (clean cloud -> rebuild) or "mesh" (delete faces on the built model, no rebuild)
        self._medit_faces = None; self._mesh_face_sel = None; self._medit_undo = []; self._selfbo = None; self._sel_face_n = 0   # mesh face editing: faces, face selection, undo, overlay buffer
        self.visible_only = False      # True selects only visible surfaces; False selects through
        self._magic_degraded = False   # set True if Magic had to fall back to a plain ball (no scipy)
        self._depth_buf = None; self._depth_valid = False   # cached GL depth buffer for visible-only; invalidated on camera/geometry change
        self._keep_view = False        # set True before a load to keep the current camera (Mesh<->Points toggles in place)
        self.edit_tool = None          # None = orbit; "lasso"/"rect"/"brush"/"magic" = drag selects points instead of rotating
        self.edit_mode = "replace"     # replace / add / subtract (Shift adds, Ctrl subtracts)
        self._sel_path = None          # screen-space points of the in-progress lasso/rect/brush stroke
        self.brush_px = 24.0           # brush radius in screen pixels
        self.magic_thresh = 0.02       # magic-wand grow distance fallback (view units) when spacing is unknown
        self.magic_k = 3.0             # magic reach = this many median point-spacings (tunable in the palette)
        self._edit_orbit = False       # a left-drag that started in the empty margin: orbit, don't select
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
        self.bind("<ButtonPress-1>", self._press); self.bind("<B1-Motion>", self._rotate); self.bind("<Motion>", self._motion)
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
        if self._pending_pts is not None:
            p = self._pending_pts; self._pending_pts = None; self._upload_points(*p)
        elif self._pending is not None:
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
            GL.glEnable(GL.GL_POINT_SMOOTH)   # round points for the Fused-points view
            self.ready = True
            if self._pending_pts is not None: self._upload_points(*self._pending_pts); self._pending_pts = None
            elif self._pending is not None: self._upload(*self._pending); self._pending = None
        except Exception as e:
            self.failed = True; self._err = e
    # ---- loading ----
    def load(self, path, on_ready=None, max_faces=MAX_FACES):
        """Load (and orient, and smooth-normal) the mesh in a worker thread, then upload to the GPU."""
        self._gen += 1; gen = self._gen; self._n = 0
        def work():
            try:
                v, f, tf, nrm = shade.load_oriented_nrm(path, max_faces)   # verts+faces+transform+normals, all cached - the splash warms this exact entry
                if gen != self._gen: return                       # a newer load superseded this one: stop early
                self.tf = tf
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
        v, n, f, wire = res; self.edit_session = None
        self.markers = []; self.plane = None; self._ncol = 0; self._split_req = None; self.clear_layers(draw=False)
        if not self._keep_view: self.reset(draw=False)     # keep the camera when just toggling representation
        self._keep_view = False
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
            self._pts_n = 0                                              # leaving points mode: don't draw stale points over the mesh
            self.edit_target = "points"; self._medit_faces = None; self._sel_face_n = 0   # a freshly loaded mesh is view-only until begin_mesh_edit()
            self._depth_valid = False                                   # new geometry: the visible-only depth cache is stale
            if self._split is not None:                                 # index sets belong to the old vertices: drop them
                try: GL.glDeleteBuffers(2, [int(self._split[0]), int(self._split[3])])
                except Exception: pass
                self._split = None
            if wire: self._upload_wire(*wire)
            self._display()
            if self._split_req is not None and len(self._split_req[0]) == len(f): self.set_split(*self._split_req)
            (on_ready and on_ready(True))     # only now is view._src actually set - firing this any earlier is a race
        except Exception as e:
            self.failed = True; self._err = e
            (on_ready and on_ready(False))
    # ---- point cloud (Fused points) ----  additive: mesh state (_n) is zeroed so the mesh branch is skipped
    def load_points(self, path, on_ready=None, max_points=400000, tf=None):
        """Load a fused point cloud and show it as points. tf (from the scan's mesh) lines the points up
        exactly with the mesh, so flipping Mesh<->Points doesn't jump."""
        self._gen += 1; gen = self._gen; self._n = 0
        def work():
            try:
                import shade
                v, t = shade.load_points_tf(path, max_points, tf)
                if gen != self._gen: return
                self.tf = t
                res = np.ascontiguousarray(v, dtype=np.float32)
            except Exception as e:
                res = e
            try: self.after(0, lambda: self._points_loaded(gen, res, on_ready))
            except Exception: pass
        threading.Thread(target=work, daemon=True).start()
    def load_edit_document(self, path, project, node, source_key, kind, on_ready=None,
                           max_faces=5_000_000, max_points=6_000_000, tf=None):
        """Load full native geometry into an EditSession, then derive float32 renderer copies."""
        self._gen += 1; gen = self._gen; self._n = 0
        def work():
            try:
                import trimesh
                from edit_session import EditSession
                doc=EditSession.from_file(project, node, path, source_key, kind=kind,
                                          max_faces=max_faces, max_points=max_points)
                t=tf or shade._compute_tf(doc.vertices)
                vv=np.ascontiguousarray(shade.world_to_view(doc.vertices, t), dtype=np.float32)
                if kind=="mesh":
                    ff=np.ascontiguousarray(doc.faces, dtype=np.uint32)
                    nn=np.asarray(trimesh.Trimesh(vv, ff, process=False).vertex_normals, dtype=np.float32)
                    res=(doc, t, vv, nn, ff)
                else:
                    res=(doc, t, vv, None, None)
            except Exception as e:
                res=e
            try: self.after(0, lambda: self._edit_document_loaded(gen, res, on_ready))
            except Exception: pass
        threading.Thread(target=work, daemon=True).start()
    def _edit_document_loaded(self, gen, res, on_ready):
        if gen != self._gen or not self._alive(): return
        if isinstance(res, Exception) or self.failed:
            if on_ready: on_ready(False, res if isinstance(res, Exception) else self._err)
            return
        doc, tf, v, n, f=res; self.tf=tf
        self.markers=[]; self.plane=None; self._ncol=0; self._split_req=None; self.clear_layers(draw=False)
        if not self._keep_view: self.reset(draw=False)
        self._keep_view=False
        def uploaded(ok):
            if ok:
                self.edit_session=doc
                if doc.kind=="mesh": self.begin_mesh_edit(doc)
                else:
                    self.edit_target="points"; self._pts_sel=doc.selection.copy(); self._pts_undo=[]
                    self._pts_recolor(); self._display()
            if on_ready: on_ready(bool(ok), None if ok else self._err)
        if doc.kind=="mesh":
            if self.ready: self._upload(v, n, f, None, uploaded)
            else: self._pending=(v, n, f, None, uploaded)
        else:
            if self.ready: self._upload_points(v, uploaded)
            else: self._pending_pts=(v, uploaded)
    def _points_loaded(self, gen, res, on_ready):
        if gen != self._gen or not self._alive(): return
        if isinstance(res, Exception) or self.failed:
            (on_ready and on_ready(False)); return
        self.edit_session = None; self.markers = []; self.plane = None; self._ncol = 0; self._split_req = None
        self.clear_layers(draw=False)
        if not self._keep_view: self.reset(draw=False)     # keep the camera when just toggling Mesh<->Points
        self._keep_view = False
        if self.ready: self._upload_points(res, on_ready)
        else: self._pending_pts = (res, on_ready)
    def _upload_points(self, v, on_ready=None):
        if not self._mapped(): self._pending_pts = (v, on_ready); return
        try:
            self.tkMakeCurrent()
            # entering points mode: drop any mesh-edit state so selection/delete/save use the CLOUD, not
            # stale faces, and so _pts_recolor() below takes the point path not _mesh_recolor().
            self.edit_target = "points"; self._medit_faces = None; self._mesh_face_sel = None; self._medit_undo = []; self._sel_face_n = 0; self._depth_valid = False
            for _b in ("_pvbo", "_pcvbo"):
                if getattr(self, _b, None) is not None:
                    try: GL.glDeleteBuffers(1, [int(getattr(self, _b))])
                    except Exception: pass
                    setattr(self, _b, None)
            self._pvbo = int(GL.glGenBuffers(1))
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._pvbo); GL.glBufferData(GL.GL_ARRAY_BUFFER, v.nbytes, v, GL.GL_STATIC_DRAW)
            self._pts_v = v                                   # keep the view-space points for selection/edit
            self._pts_sel = np.zeros(len(v), dtype=bool)      # per-point selection mask
            self._pts_undo = []                               # stack of prior point arrays for Undo
            self._pcvbo = int(GL.glGenBuffers(1))             # per-point colour (blue, red where selected)
            self._pts_recolor()                               # fills _pcvbo from _pts_sel
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            self._pts_n = int(len(v)); self._n = 0            # points mode: mesh branch stays off
            self._zmax = float(v[:, 2].max()) if len(v) else 0.0; self._src = None
            self._display()
            (on_ready and on_ready(True))
        except Exception as e:
            self.failed = True; self._err = e
            (on_ready and on_ready(False))
    # ---- point editing (select / delete / undo) ----
    def _mesh_recolor(self):
        """Mesh editing: rebuild the red overlay from the face selection mask."""
        if self._pts_v is None or self._medit_faces is None: return
        if self._mesh_face_sel is None or len(self._mesh_face_sel) != len(self._medit_faces):
            if self._pts_sel is not None and len(self._pts_sel):
                self._mesh_face_sel = self._pts_sel[self._medit_faces].all(axis=1)
            else:
                self._mesh_face_sel = np.zeros(len(self._medit_faces), dtype=bool)
        self._pts_sel = np.zeros(len(self._pts_v), dtype=bool)
        if len(self._medit_faces) and self._mesh_face_sel.any():
            self._pts_sel[np.unique(self._medit_faces[self._mesh_face_sel])] = True
        if self.edit_session is not None and len(self.edit_session.selection)==len(self._pts_sel):
            self.edit_session.set_selection(self._pts_sel)
        try:
            sf = self._medit_faces[self._mesh_face_sel] if self._mesh_face_sel.any() else np.zeros((0, 3), dtype=np.uint32)
            sf = np.ascontiguousarray(sf, dtype=np.uint32)
            self.tkMakeCurrent()
            if self._selfbo is None: self._selfbo = GL.glGenBuffers(1)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._selfbo)
            GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, sf.nbytes, sf if sf.size else None, GL.GL_DYNAMIC_DRAW)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, 0)
            self._sel_face_n = int(sf.size)
        except Exception as e: self._err = e
        if callable(self.on_points_change):
            try: self.on_points_change(int(len(self._medit_faces)))
            except Exception: pass
    def begin_mesh_edit(self, session=None):
        """Turn the currently-loaded mesh (its displayed verts/faces) into an editable target: selection tools
        act on its vertices, Delete drops the faces you cover. WYSIWYG - you edit exactly what's on screen."""
        if session is not None:
            v=shade.world_to_view(session.vertices, self.tf).astype(np.float32); f=session.faces
        else:
            if not self._src: return False
            v, f = self._src
        self._pts_v = np.ascontiguousarray(v, dtype=np.float32)     # view-space verts: the selection tools project these
        self._pts_sel = session.selection.copy() if session is not None else np.zeros(len(self._pts_v), dtype=bool)
        self._medit_faces = np.ascontiguousarray(f, dtype=np.uint32).reshape(-1, 3)
        if session is not None and self._pts_sel is not None and len(self._pts_sel):
            self._mesh_face_sel = self._pts_sel[self._medit_faces].all(axis=1)
        else:
            self._mesh_face_sel = np.zeros(len(self._medit_faces), dtype=bool)
        self._medit_undo = []; self._pts_n = 0                      # _pts_n=0 so GL_POINTS isn't drawn; the mesh draws
        if session is not None: self.edit_session=session
        self.edit_target = "mesh"
        self._mesh_recolor()
        return True
    def mesh_world(self):
        """The edited mesh (verts in the scan's mm coords, faces) for saving. Drops now-unused verts."""
        if self._pts_v is None or self._medit_faces is None or self.tf is None: return None
        try:
            vw = shade.view_to_world(self._pts_v.astype(np.float64), self.tf)
            f = self._medit_faces.astype(np.int64)
            used = np.unique(f)
            remap = np.zeros(len(vw), dtype=np.int64); remap[used] = np.arange(len(used))
            return vw[used], remap[f]
        except Exception: return None
    def _pts_recolor(self):
        """Per-point colour VBO from _pts_sel: blue normally, red where selected."""
        if self.edit_target == "mesh": self._mesh_recolor(); return
        if self._pts_v is None or self._pcvbo is None: return
        if self.edit_session is not None and len(self.edit_session.selection)==len(self._pts_sel):
            self.edit_session.set_selection(self._pts_sel)
        col = np.empty((len(self._pts_v), 3), dtype=np.float32); col[:] = (0.36, 0.66, 1.0)
        if self._pts_sel is not None and self._pts_sel.any(): col[self._pts_sel] = (1.0, 0.28, 0.30)
        try:
            self.tkMakeCurrent()
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._pcvbo); GL.glBufferData(GL.GL_ARRAY_BUFFER, col.nbytes, col, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        except Exception: pass
        if callable(self.on_points_change):
            try: self.on_points_change(len(self._pts_v))    # live point/selection count for the editor
            except Exception: pass
    def _project_points(self, pts):
        """Screen (x,y) in Tk top-left coords for points, an in-front mask, and window depth [0,1]."""
        if pts is None or not len(pts) or getattr(self, "_mv_m", None) is None: return None
        try:
            pts = np.asarray(pts, dtype=np.float64)
            mv = np.asarray(self._mv_m, dtype=np.float64); pj = np.asarray(self._pj_m, dtype=np.float64)
            _, _, vw, vh = self._vp
            P = np.column_stack([pts, np.ones(len(pts))])
            def run(m, p):
                clip = (P @ m) @ p; w = clip[:, 3].copy(); w[np.abs(w) < 1e-12] = 1e-12
                ndc = clip[:, :3] / w[:, None]
                sx = (ndc[:, 0] * 0.5 + 0.5) * vw; sy = (0.5 - ndc[:, 1] * 0.5) * vh
                wz = ndc[:, 2] * 0.5 + 0.5                        # NDC z [-1,1] -> window depth [0,1]
                return np.column_stack([sx, sy]), (w > 0), wz
            scr, front, winz = run(mv, pj)
            # calibrate against gluProject on the first point; if the vectorised result is off, transpose
            try:
                p0 = pts[0]; gx, gy, _ = GLU.gluProject(float(p0[0]), float(p0[1]), float(p0[2]), self._mv_m, self._pj_m, self._vp)
                if abs(scr[0, 0] - gx) + abs(scr[0, 1] - (vh - gy)) > 4.0:
                    scr, front, winz = run(mv.T, pj.T)
            except Exception: pass
            return scr, front, winz
        except Exception:
            return None
    def _project_all(self):
        """Screen (x,y) in Tk top-left coords for every editable point or vertex."""
        return self._project_points(self._pts_v)
    def _capture_depth(self):
        """Read the window depth buffer once after the base geometry is drawn (before selection overlays),
        for visible-only selection. Row 0 is the bottom of the screen (GL convention)."""
        try:
            _, _, vw, vh = self._vp; vw = int(vw); vh = int(vh)
            buf = GL.glReadPixels(0, 0, vw, vh, GL.GL_DEPTH_COMPONENT, GL.GL_FLOAT)
            self._depth_buf = np.asarray(buf, dtype=np.float32).reshape(vh, vw); self._depth_valid = True
        except Exception:
            self._depth_buf = None; self._depth_valid = False
    def _visible_mask(self, scr, front, winz):
        """True where a point is the front-most surface at its pixel (not hidden behind the object). When
        visible-only is off, `front` (select-through). When it is ON but we can't read/compare depth, ABORT
        the selection (empty mask) rather than select through: 'only what I can see' must never silently
        grab hidden back geometry that Delete would then remove. _depth_failed flags it so the UI can warn."""
        if not self.visible_only: return front
        db = self._depth_buf
        if db is None:                                       # wanted visible-only but the depth read failed
            self._depth_failed = True; return np.zeros(len(front), dtype=bool)
        try:
            vh, vw = db.shape
            px = np.clip(scr[:, 0].astype(np.int32), 0, vw - 1)
            py = np.clip((vh - scr[:, 1]).astype(np.int32), 0, vh - 1)   # Tk top-left y -> GL bottom-left row
            nearest = db[py, px]
            # Perspective crushes the whole model into a tiny depth window near 1.0, so a fixed tolerance lets
            # the back through. Scale it to the model's own on-screen depth span instead.
            fw = winz[front]
            zr = float(fw.max() - fw.min()) if fw.size else 0.0
            tol = max(zr * 0.02, 1e-6)
            return front & (winz <= nearest + tol)                       # keep the front surface (winz ~= nearest); drop what's behind it
        except Exception:
            self._depth_failed = True; return np.zeros(len(front), dtype=bool)   # can't compare depth: abort, don't select through
    def set_visible_only(self, on):
        self.visible_only = bool(on); self._depth_valid = False; self.draw()
    @staticmethod
    def _in_poly(pts, poly):
        """Vectorised crossing-number point-in-polygon. pts: Nx2, poly: Mx2. Returns bool N."""
        x = pts[:, 0]; y = pts[:, 1]; inside = np.zeros(len(pts), dtype=bool)
        n = len(poly); j = n - 1
        for i in range(n):
            xi, yi = poly[i]; xj, yj = poly[j]
            cond = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-12) + xi)
            inside ^= cond; j = i
        return inside
    @staticmethod
    def _pt_in_tri2(p, tri):
        a, b, c = tri
        v0 = c - a; v1 = b - a; v2 = p - a
        den = v0[0] * v1[1] - v1[0] * v0[1]
        if abs(float(den)) < 1e-9: return False
        u = (v2[0] * v1[1] - v1[0] * v2[1]) / den
        v = (v0[0] * v2[1] - v2[0] * v0[1]) / den
        return u >= -1e-5 and v >= -1e-5 and (u + v) <= 1.0 + 1e-5
    def _mesh_sampled_surface_hit(self, polygon=None, circle=None, max_samples=220):
        pr = self._mesh_project_faces()
        if pr is None: return None
        (cscr, cfront, cwinz), (vscr, _vfront, vwinz) = pr
        faces = self._medit_faces
        tri_scr = vscr[faces]
        tri_z = vwinz[faces].mean(axis=1)
        if polygon is not None:
            poly = np.asarray(polygon, dtype=float)
            mn = poly.min(axis=0); mx = poly.max(axis=0)
            area_w = max(1.0, float(mx[0] - mn[0])); area_h = max(1.0, float(mx[1] - mn[1]))
            nx = max(3, min(18, int(area_w / 12) + 1)); ny = max(3, min(18, int(area_h / 12) + 1))
            xs = np.linspace(mn[0], mx[0], nx); ys = np.linspace(mn[1], mx[1], ny)
            samples = np.array([(x, y) for y in ys for x in xs], dtype=float)
            samples = samples[self._in_poly(samples, poly)]
            if len(samples) > max_samples:
                samples = samples[np.linspace(0, len(samples) - 1, max_samples).astype(int)]
        else:
            x, y, r = circle
            nx = ny = 11
            xs = np.linspace(x - r, x + r, nx); ys = np.linspace(y - r, y + r, ny)
            samples = np.array([(sx, sy) for sy in ys for sx in xs if (sx - x) ** 2 + (sy - y) ** 2 <= r ** 2], dtype=float)
            samples = np.vstack([samples, np.array([[x, y]], dtype=float)]) if len(samples) else np.array([[x, y]], dtype=float)
        out = np.zeros(len(faces), dtype=bool)
        if not len(samples): return out
        tb_min = tri_scr.min(axis=1); tb_max = tri_scr.max(axis=1)
        for sp in samples:
            cand = np.where((tb_min[:, 0] <= sp[0]) & (tb_max[:, 0] >= sp[0]) & (tb_min[:, 1] <= sp[1]) & (tb_max[:, 1] >= sp[1]) & cfront)[0]
            if not len(cand): continue
            hit = [int(i) for i in cand if self._pt_in_tri2(sp, tri_scr[int(i)])]
            if not hit: continue
            out[min(hit, key=lambda i: tri_z[i])] = True
        if out.any(): return np.ascontiguousarray(out)
        return None
    def _mesh_apply_face_hit(self, face_hit, mode):
        if self._mesh_face_sel is None or len(self._mesh_face_sel) != len(self._medit_faces):
            self._mesh_face_sel = np.zeros(len(self._medit_faces), dtype=bool)
        face_hit = np.asarray(face_hit, dtype=bool)
        if mode == "add": self._mesh_face_sel |= face_hit
        elif mode == "subtract": self._mesh_face_sel &= ~face_hit
        else: self._mesh_face_sel = np.ascontiguousarray(face_hit, dtype=bool)
        self._mesh_recolor(); self._display()
        return int(self._sel_face_n // 3)
    def _mesh_project_faces(self):
        if self._pts_v is None or self._medit_faces is None or not len(self._medit_faces): return None
        centres = self._pts_v[self._medit_faces].mean(axis=1)
        cpr = self._project_points(centres)
        vpr = self._project_points(self._pts_v)
        if cpr is None or vpr is None: return None
        return cpr, vpr
    def select_region(self, polygon, mode="replace", front_only=True):
        """Select points whose screen projection is inside polygon (list of (x,y) Tk coords). mode:
        replace / add / subtract. front_only drops points facing away/behind (w<=0)."""
        if self._pts_v is None or self._pts_sel is None: return 0
        poly = np.asarray(polygon, dtype=float)
        if self.edit_target == "mesh" and self._medit_faces is not None:
            pr = self._mesh_project_faces()
            if pr is None: return 0
            (cscr, cfront, cwinz), _vpr = pr
            import mesh_ops
            vis = self._visible_mask(cscr, cfront, cwinz) if front_only else None
            face_hit = mesh_ops.face_center_selection(cscr, poly, vis)
            return self._mesh_apply_face_hit(face_hit, mode)
        pr = self._project_all()
        if pr is None: return 0
        scr, front, winz = pr
        hit = self._in_poly(scr, poly)
        if front_only: hit &= self._visible_mask(scr, front, winz)   # visible-only drops points hidden behind the object
        if mode == "add": self._pts_sel |= hit
        elif mode == "subtract": self._pts_sel &= ~hit
        else: self._pts_sel = hit
        self._pts_recolor(); self._display()
        return int(self._pts_sel.sum())
    def clear_selection(self):
        if self.edit_target == "mesh" and self._mesh_face_sel is not None:
            self._mesh_face_sel[:] = False; self._mesh_recolor(); self._display(); return
        if self._pts_sel is not None: self._pts_sel[:] = False; self._pts_recolor(); self._display()
    def invert_selection(self):
        if self.edit_target == "mesh" and self._mesh_face_sel is not None:
            self._mesh_face_sel = ~self._mesh_face_sel; self._mesh_recolor(); self._display(); return
        if self._pts_sel is not None: self._pts_sel = ~self._pts_sel; self._pts_recolor(); self._display()
    def _face_graph(self):
        """The mesh's face adjacency, built once per faces array (about a second for a million faces) and reused
        by every Grow / Shrink / Connected / Magic click until the faces change."""
        f = self._medit_faces
        g = getattr(self, "_fgraph", None)
        if g is None or g[0] is not f:
            g = (f, mesh_ops.FaceGraph(f)); self._fgraph = g
        return g[1]
    def mesh_grow_selection(self):
        if self.edit_target != "mesh" or self._medit_faces is None or self._mesh_face_sel is None: return False
        import mesh_ops
        changed = mesh_ops.grow_face_selection(self._medit_faces, self._mesh_face_sel, graph=self._face_graph())
        if np.array_equal(changed, self._mesh_face_sel): return False
        self._mesh_face_sel = changed
        self._mesh_recolor(); self._display()
        return True
    def mesh_shrink_selection(self):
        if self.edit_target != "mesh" or self._medit_faces is None or self._mesh_face_sel is None: return False
        import mesh_ops
        changed = mesh_ops.shrink_face_selection(self._medit_faces, self._mesh_face_sel, graph=self._face_graph())
        if np.array_equal(changed, self._mesh_face_sel):
            changed = self._shrink_closed_face_selection()
            if changed is None or np.array_equal(changed, self._mesh_face_sel): return False
        self._mesh_face_sel = changed
        self._mesh_recolor(); self._display()
        return True
    def _shrink_closed_face_selection(self):
        if self._mesh_face_sel is None or not self._mesh_face_sel.any(): return None
        pr = self._mesh_project_faces()
        if pr is None: return None
        (cscr, cfront, cwinz), _vpr = pr
        mask = self._mesh_face_sel.copy()
        candidates = np.flatnonzero(mask & self._visible_mask(cscr, cfront, cwinz))
        if not len(candidates): candidates = np.flatnonzero(mask)
        if len(candidates) <= 1: return mask & False
        pts = cscr[candidates]
        centre = pts.mean(axis=0)
        dist = np.linalg.norm(pts - centre, axis=1)
        cut = float(np.quantile(dist, 0.72))
        remove = candidates[dist >= cut]
        if len(remove) == 0: remove = candidates[[int(np.argmax(dist))]]
        out = mask.copy(); out[remove] = False
        if not out.any(): out = mask.copy(); out[remove[:max(1, len(remove) - 1)]] = False
        return np.ascontiguousarray(out)
    def mesh_connected_selection(self):
        if self.edit_target != "mesh" or self._medit_faces is None or self._mesh_face_sel is None: return False
        import mesh_ops
        changed = mesh_ops.connected_face_selection(self._medit_faces, self._mesh_face_sel, graph=self._face_graph())
        if np.array_equal(changed, self._mesh_face_sel): return False
        self._mesh_face_sel = changed
        self._mesh_recolor(); self._display()
        return True
    def delete_selected(self):
        """Remove selected points (points mode) or the covered faces (mesh mode). Pushes undo first."""
        if self._pts_sel is None or not self._pts_sel.any(): return
        if self.edit_session is not None:
            if self.edit_target == "mesh": self._mesh_recolor()
            changed=self.edit_session.delete_selected(self._pts_sel)
            if changed: self._sync_edit_session()
            return changed
        if self.edit_target == "mesh":
            if self._medit_faces is None: return
            self._medit_undo.append(self._medit_faces.copy())
            if len(self._medit_undo) > 12: self._medit_undo.pop(0)
            drop = self._pts_sel[self._medit_faces].all(axis=1)      # a face goes only when all 3 of its verts are selected: clean cuts
            self._medit_faces = np.ascontiguousarray(self._medit_faces[~drop], dtype=np.uint32)
            self._pts_sel[:] = False; self._reupload_mesh_faces()
            return
        if self._pts_v is None: return
        self._pts_undo.append(self._pts_v.copy())
        if len(self._pts_undo) > 12: self._pts_undo.pop(0)
        keep = ~self._pts_sel
        self._pts_v = np.ascontiguousarray(self._pts_v[keep]); self._reupload_points()
    def _reupload_mesh_faces(self):
        """Re-push the mesh's index buffer after a face delete/undo and refresh the overlay."""
        try:
            self.tkMakeCurrent()
            f = np.ascontiguousarray(self._medit_faces, dtype=np.uint32)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._vbo[2]); GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, f.nbytes, f, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, 0)
            self._n = int(f.size); self._wire_gen = None            # wireframe cache is stale after a face change
            self._depth_valid = False                               # geometry changed: visible-only depth is stale
            if self._pts_sel is None or len(self._pts_sel) != len(self._pts_v):
                self._pts_sel = np.zeros(len(self._pts_v), dtype=bool)
            if self._mesh_face_sel is None or len(self._mesh_face_sel) != len(self._medit_faces):
                self._mesh_face_sel = np.zeros(len(self._medit_faces), dtype=bool)
            self._mesh_recolor(); self._display()
        except Exception as e:
            self._err = e
    def undo_points(self):
        if self.edit_session is not None:
            changed=self.edit_session.undo()
            if changed: self._sync_edit_session()
            return changed
        if self.edit_target == "mesh":
            if not self._medit_undo: return
            self._medit_faces = self._medit_undo.pop(); self._pts_sel[:] = False; self._reupload_mesh_faces(); return
        if not self._pts_undo: return
        self._pts_v = self._pts_undo.pop(); self._reupload_points(); return True
    def redo_points(self):
        if self.edit_session is None: return False
        changed=self.edit_session.redo()
        if changed: self._sync_edit_session()
        return changed
    def _sync_edit_session(self):
        doc=self.edit_session
        if doc is None: return
        if doc.kind=="mesh":
            self._medit_faces=np.ascontiguousarray(doc.faces, dtype=np.uint32)
            self._pts_sel=doc.selection.copy()
            self._mesh_face_sel = self._pts_sel[self._medit_faces].all(axis=1) if len(self._medit_faces) else np.zeros(0, dtype=bool)
            self._reupload_mesh_faces()
        else:
            self._pts_v=np.ascontiguousarray(shade.world_to_view(doc.vertices, self.tf), dtype=np.float32)
            self._pts_sel=doc.selection.copy(); self._reupload_points(keep_selection=True)
    def _reupload_points(self, keep_selection=False):
        """Re-push _pts_v after an edit and refresh selection/colours."""
        try:
            self.tkMakeCurrent()
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._pvbo); GL.glBufferData(GL.GL_ARRAY_BUFFER, self._pts_v.nbytes, self._pts_v, GL.GL_STATIC_DRAW)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            self._pts_n = int(len(self._pts_v))
            if not keep_selection: self._pts_sel = np.zeros(self._pts_n, dtype=bool)
            self._depth_valid = False                               # geometry changed: visible-only depth is stale
            self._pts_recolor(); self._display()
            if callable(self.on_points_change): self.on_points_change(self._pts_n)
        except Exception as e:
            self._err = e
    def points_world(self):
        """Current edited points back in the scan's own mm coordinates (for saving / re-meshing)."""
        if self._pts_v is None or self.tf is None: return None
        try: return shade.view_to_world(self._pts_v.astype(np.float64), self.tf)
        except Exception: return None
    def set_edit_tool(self, tool, mode="replace"):
        """tool: None (orbit) / 'lasso' / 'rect' / 'brush' / 'magic'. mode: replace/add/subtract."""
        self.edit_tool = tool; self.edit_mode = mode; self._sel_path = None
        # the pointer changes so you can tell which tool is live (arrow = orbit, a select cursor per tool)
        try: self.configure(cursor={"lasso":"pencil", "rect":"crosshair", "brush":"", "magic":"plus"}.get(tool, ""))
        except Exception: pass
        self.draw()
    def _over_content(self, x, y):
        """True if (x, y) is over the object's screen area (so a left-drag there selects); False out in the
        empty margins (so a left-drag there orbits instead - no tool switch needed)."""
        pr = self._project_all()
        if pr is None: return True
        scr, front, _winz = pr
        s = scr[front]
        if not len(s): return True
        m = 45.0                        # margin so you can lasso just outside the silhouette
        return (s[:, 0].min() - m) <= x <= (s[:, 0].max() + m) and (s[:, 1].min() - m) <= y <= (s[:, 1].max() + m)
    def _brush_at(self, x, y, mode):
        """Paint-select points or mesh faces within brush_px pixels of (x, y)."""
        if self._pts_v is None or self._pts_sel is None: return
        if self.edit_target == "mesh" and self._medit_faces is not None:
            pr = self._mesh_project_faces()
            if pr is None: return
            (cscr, cfront, cwinz), _vpr = pr
            import mesh_ops
            vis = self._visible_mask(cscr, cfront, cwinz)
            hit = mesh_ops.face_radius_selection(cscr, x, y, self.brush_px, vis)
            self._mesh_apply_face_hit(hit, "subtract" if mode == "subtract" else "add")
            return
        pr = self._project_all()
        if pr is None: return
        scr, front, winz = pr
        vis = self._visible_mask(scr, front, winz)
        hit = ((scr[:, 0] - x) ** 2 + (scr[:, 1] - y) ** 2 <= self.brush_px ** 2) & vis
        if mode == "subtract": self._pts_sel &= ~hit
        else: self._pts_sel |= hit
        self._pts_recolor(); self._display()
    def _magic_at(self, x, y, mode):
        """Region-grow from the clicked point or mesh face."""
        if self._pts_v is None or self._pts_sel is None: return
        if self.edit_target == "mesh" and self._medit_faces is not None:
            pr = self._mesh_project_faces()
            if pr is None: return
            (cscr, cfront, cwinz), (vscr, _vfront, _vwinz) = pr
            d2 = (cscr[:, 0] - x) ** 2 + (cscr[:, 1] - y) ** 2
            visible = self._visible_mask(cscr, cfront, cwinz)
            cand = np.where(visible & (d2 <= (self.brush_px * 2.0) ** 2))[0]
            if not len(cand):
                vert_d2 = (vscr[:, 0] - x) ** 2 + (vscr[:, 1] - y) ** 2
                face_d2 = vert_d2[self._medit_faces].min(axis=1)
                cand = np.where(visible & (face_d2 <= (self.brush_px * 2.0) ** 2))[0]
                d2 = face_d2
            if not len(cand): return
            seed = int(cand[np.argmin(d2[cand])])
            face_hit = self._mesh_magic_region(seed, visible)
            self._mesh_apply_face_hit(face_hit, "subtract" if mode == "subtract" else "add")
            return
        pr = self._project_all()
        if pr is None: return
        scr, front, winz = pr
        d2 = (scr[:, 0] - x) ** 2 + (scr[:, 1] - y) ** 2
        cand = np.where(self._visible_mask(scr, front, winz) & (d2 <= (self.brush_px * 1.5) ** 2))[0]   # seed from a visible point
        if not len(cand): return
        seed = int(cand[np.argmin(d2[cand])])
        grown = self._region_grow(seed)
        if self.visible_only:                                  # don't let the grow spill onto the hidden back side
            vis = self._visible_mask(scr, front, winz)
            grown = grown[vis[grown]]
        if mode == "subtract": self._pts_sel[grown] = False
        else: self._pts_sel[grown] = True
        self._pts_recolor(); self._display()
    def _mesh_magic_region(self, seed, visible):
        try:
            import mesh_ops
            faces = np.asarray(self._medit_faces, dtype=np.int64)
            pts = np.asarray(self._pts_v, dtype=np.float64)
            tri = pts[faces]
            normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            lens = np.linalg.norm(normals, axis=1)
            good = lens > 1e-12
            normals[good] /= lens[good, None]
            seed_n = normals[int(seed)]
            angle = min(80.0, max(20.0, 25.0 + float(getattr(self, "magic_k", 3.0)) * 8.0))
            allowed = good & (normals @ seed_n >= np.cos(np.deg2rad(angle)))
            if self.visible_only: allowed &= visible
            # flood fill over the allowed faces only: the seed's component in the graph restricted to `allowed`,
            # grown one hop at a time in numpy instead of a Python stack walk over a million faces
            if not allowed[int(seed)]: allowed[int(seed)] = True
            g = self._face_graph(); out = np.zeros(len(faces), dtype=bool); out[int(seed)] = True
            while True:
                nxt = g.neighbours_of(out) & allowed & ~out
                if not nxt.any(): break
                out |= nxt
            return np.ascontiguousarray(out)
        except Exception:
            out = np.zeros(len(self._medit_faces), dtype=bool); out[int(seed)] = True; return out
    def _region_grow(self, seed):
        """Connected points near the growing set, from a seed. The step size ADAPTS to the cloud's own point
        spacing (not a fixed constant), so Magic behaves the same on dense and sparse scans. KDTree BFS, capped."""
        pts = self._pts_v
        try:
            from scipy.spatial import cKDTree
        except Exception:
            # no scipy on this machine: fall back to a plain ball around the seed. That is NOT a connected-region
            # grow, so flag it - the app tells the user Magic is degraded here.
            self._magic_degraded = True
            d = np.linalg.norm(pts - pts[seed], axis=1)
            return np.where(d <= (self.magic_thresh or 0.02) * 8)[0]
        self._magic_degraded = False
        tree = cKDTree(pts); n = len(pts)
        # spacing = median distance to the nearest OTHER point (from a sample); a grow step spans a few of those
        samp = pts if n <= 4000 else pts[np.random.RandomState(0).choice(n, 4000, replace=False)]
        try:
            dd, _ = tree.query(samp, k=2); spacing = float(np.median(dd[:, 1])) if dd.ndim > 1 and dd.shape[1] > 1 else 0.0
        except Exception: spacing = 0.0
        thr = spacing * float(getattr(self, "magic_k", 3.0)) if spacing > 0 else float(self.magic_thresh)
        visited = np.zeros(n, dtype=bool); visited[seed] = True
        frontier = [seed]; cap = min(n, 300000)
        for _ in range(400):                                  # cap iterations so a runaway grow can't hang
            if not frontier: break
            nbrs = tree.query_ball_point(pts[frontier], thr)
            nxt = []
            for lst in nbrs:
                for j in lst:
                    if not visited[j]: visited[j] = True; nxt.append(j)
            frontier = nxt
            if visited.sum() >= cap: break
        return np.where(visited)[0]
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
        GL.glMatrixMode(GL.GL_PROJECTION); GL.glLoadIdentity(); GLU.gluPerspective(34.0, w / float(h), 0.05, 50.0)
        GL.glMatrixMode(GL.GL_MODELVIEW); GL.glLoadIdentity()
        GL.glTranslatef(self.pan[0] * 2.0, self.pan[1] * 2.0 - 0.05, -4.55 / self.zoom)   # +0.176 = home framing shifted up to match shade.render's 0.56 baseline
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_POSITION, (-0.5, 0.8, 1.0, 0.0)); GL.glLightfv(GL.GL_LIGHT1, GL.GL_POSITION, (0.8, -0.3, 0.4, 0.0))
        self._mult_rot()
        GL.glTranslatef(0, 0, -0.5 * getattr(self, "_zmax", 0.0))
        self._mv_m = GL.glGetDoublev(GL.GL_MODELVIEW_MATRIX); self._pj_m = GL.glGetDoublev(GL.GL_PROJECTION_MATRIX); self._vp = (0, 0, w, h)
        try:                                   # invalidate the visible-only depth cache when the camera moves
            _sig = (float(self.azim), float(self.elev), float(self.zoom), float(self.pan[0]), float(self.pan[1]), w, h, hash(self.rot.tobytes()))
            if _sig != getattr(self, "_depth_cam", None): self._depth_valid = False; self._depth_cam = _sig
        except Exception: self._depth_valid = False
        # floor grid
        GL.glDisable(GL.GL_LIGHTING); GL.glColor3f(26 / 255.0, 33 / 255.0, 48 / 255.0); GL.glBegin(GL.GL_LINES)
        for t in np.linspace(-1.1, 1.1, 11):
            GL.glVertex3f(t, -1.1, 0); GL.glVertex3f(t, 1.1, 0); GL.glVertex3f(-1.1, t, 0); GL.glVertex3f(1.1, t, 0)
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
            if self.edit_tool and self.visible_only and not self._depth_valid: self._capture_depth()   # base mesh depth, before the red overlay
            if self.edit_target == "mesh" and self._sel_face_n and self._selfbo is not None and self._vbo is not None:
                GL.glDisable(GL.GL_LIGHTING); GL.glEnable(GL.GL_POLYGON_OFFSET_FILL); GL.glPolygonOffset(-1.0, -1.0)
                GL.glColor3f(1.0, 0.28, 0.30)                # the faces that Delete will remove, painted red on top
                GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo[0]); GL.glVertexPointer(3, GL.GL_FLOAT, 0, None)
                GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._selfbo); GL.glDrawElements(GL.GL_TRIANGLES, self._sel_face_n, GL.GL_UNSIGNED_INT, None)
                GL.glDisableClientState(GL.GL_VERTEX_ARRAY); GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0); GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, 0)
                GL.glDisable(GL.GL_POLYGON_OFFSET_FILL); GL.glEnable(GL.GL_LIGHTING)
        if self._pts_n and self._pvbo is not None:      # Fused-points view (drawn instead of the mesh)
            GL.glDisable(GL.GL_LIGHTING); GL.glPointSize(2.0)
            GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._pvbo); GL.glVertexPointer(3, GL.GL_FLOAT, 0, None)
            if self._pcvbo is not None:                 # per-point colour (red = selected for editing)
                GL.glEnableClientState(GL.GL_COLOR_ARRAY)
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._pcvbo); GL.glColorPointer(3, GL.GL_FLOAT, 0, None)
            else:
                GL.glColor3f(0.36, 0.66, 1.0)
            GL.glDrawArrays(GL.GL_POINTS, 0, self._pts_n)
            if self._pcvbo is not None: GL.glDisableClientState(GL.GL_COLOR_ARRAY)
            GL.glDisableClientState(GL.GL_VERTEX_ARRAY); GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            GL.glPointSize(1.0); GL.glEnable(GL.GL_LIGHTING)
            if self.edit_tool and self.visible_only and not self._depth_valid: self._capture_depth()   # point-cloud depth (colour doesn't affect it)
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
        self._draw_view_nav(w, h)
        # selection stroke overlay (lasso / rect outline / brush ring), 2D screen space, top-left origin
        if self.edit_tool and self._sel_path:
            GL.glViewport(0, 0, w, h)
            GL.glDisable(GL.GL_LIGHTING); GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glMatrixMode(GL.GL_PROJECTION); GL.glLoadIdentity(); GL.glOrtho(0, w, h, 0, -1, 1)
            GL.glMatrixMode(GL.GL_MODELVIEW); GL.glLoadIdentity()
            GL.glColor3f(0.30, 1.0, 0.45); GL.glLineWidth(1.6)
            if self.edit_tool == "rect" and len(self._sel_path) >= 2:
                (x0, y0) = self._sel_path[0]; (x1, y1) = self._sel_path[-1]
                GL.glBegin(GL.GL_LINE_LOOP)
                for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)): GL.glVertex2f(px, py)
                GL.glEnd()
            elif self.edit_tool == "brush":
                cx, cy = self._sel_path[-1]
                GL.glBegin(GL.GL_LINE_LOOP)
                for a in np.linspace(0, 2 * np.pi, 28): GL.glVertex2f(cx + self.brush_px * np.cos(a), cy + self.brush_px * np.sin(a))
                GL.glEnd()
            elif len(self._sel_path) >= 2:                     # lasso
                GL.glBegin(GL.GL_LINE_STRIP)
                for px, py in self._sel_path: GL.glVertex2f(px, py)
                GL.glEnd()
            GL.glLineWidth(1.0); GL.glEnable(GL.GL_DEPTH_TEST)
    def draw(self, hi=False):
        if self.ready and not self.failed and self._alive() and self._mapped():
            try: self._display()
            except Exception as e: self.failed = True; self._err = e
    def set_wire(self, on):
        self.wire = bool(on)
        if self.wire: self._wire_data()
        self.draw()
    def reset(self, draw=True):
        self.azim, self.elev, self.zoom, self.pan = -35.0, 30.0, 1.10, [0.0, 0.0]; self.rot = self._default_rot()
        if draw: self.draw()
    def _animate_view_to(self, azim, elev, steps=8, ms=18):
        try:
            target_az, target_el = float(azim), float(elev)
            start_az, start_el = float(self.azim), float(self.elev)
            start_pan = list(self.pan)
            # shortest azimuth path
            da = ((target_az - start_az + 180.0) % 360.0) - 180.0
            if self._view_anim:
                try: self.after_cancel(self._view_anim)
                except Exception: pass
                self._view_anim = None
            def step(i=1):
                t = min(1.0, i / float(steps)); k = 1.0 - (1.0 - t) * (1.0 - t)
                self.azim = start_az + da * k; self.elev = start_el + (target_el - start_el) * k
                self.pan = [start_pan[0] * (1.0 - k), start_pan[1] * (1.0 - k)]
                self.rot = self._default_rot(); self.draw()
                if i < steps and self._alive(): self._view_anim = self.after(ms, lambda: step(i + 1))
                else: self._view_anim = None
            step()
        except Exception:
            self.azim = float(azim); self.elev = float(elev); self.pan = [0.0, 0.0]; self.rot = self._default_rot(); self.draw()
    def set_view(self, azim, elev, draw=True):
        """Move to a standard view (Top/Front/Right/…) - azimuth about up, elevation above the floor."""
        if draw: self._animate_view_to(azim, elev)
        else:
            self.azim = float(azim); self.elev = float(elev); self.pan = [0.0, 0.0]; self.rot = self._default_rot()

    def _draw_view_nav(self, w, h):
        try:
            # Draw the entire navigation cube as a small raster overlay. This keeps labels clipped
            # inside their faces and avoids ugly GL stroke text when the cube rotates.
            from PIL import Image
            # rasterise only the strip the cube and home button occupy (left edge, full height is wasted work at
            # 1688x1044), cached across frames that did not change rotation or hover, then blit that strip
            lo = viewnav.layout(w, h); x0, y0, size = lo["cube"]; sw = min(w, x0 + size + 40)
            key = (w, h, tuple(np.round(self.rot[:3, :3].ravel(), 3)), getattr(self, "_nav_hover", None))
            cache = getattr(self, "_nav_cache", None)
            if cache is None or cache[0] != key:
                img = viewnav.overlay_cached(w, h, self.rot[:3, :3], getattr(self, "_nav_hover", None)).crop((0, 0, sw, h))
                data = img.transpose(Image.FLIP_TOP_BOTTOM).tobytes("raw", "RGBA"); self._nav_cache = (key, sw, data)
            _, sw, data = self._nav_cache
            GL.glViewport(0, 0, w, h)
            GL.glMatrixMode(GL.GL_PROJECTION); GL.glLoadIdentity(); GL.glOrtho(0, w, 0, h, -1, 1)
            GL.glMatrixMode(GL.GL_MODELVIEW); GL.glLoadIdentity()
            GL.glDisable(GL.GL_LIGHTING); GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glEnable(GL.GL_BLEND); GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glRasterPos2f(0, 0)
            GL.glDrawPixels(sw, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)
            GL.glDisable(GL.GL_BLEND); GL.glEnable(GL.GL_DEPTH_TEST)
        except Exception:
            pass
    def _nav_hit(self, x, y):
        try: return viewnav.hit(self.rot[:3, :3], max(1, self.winfo_width()), max(1, self.winfo_height()), x, y)
        except Exception: return None
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
    def _press(self, e):
        self._nav_press = None
        if getattr(e, "num", 1) == 1:
            nav = self._nav_hit(e.x, e.y)
            if nav is not None:
                if nav.get("kind") == "home": self.reset(); self._drag = None; self._press_at = None; return
                if nav.get("view") is not None:
                    self._nav_press = (nav, e.x, e.y)
                    self._drag = (e.x, e.y); self._press_at = None; return
        _editable = self._pts_n or (self.edit_target == "mesh" and self._medit_faces is not None)
        if self.edit_tool and _editable and e.num == 1:  # LEFT button in edit mode (points or mesh faces)
            if not self._over_content(e.x, e.y):           # started out in the empty margin: orbit, don't select
                self._edit_orbit = True; self._drag = (e.x, e.y); self._press_at = (e.x, e.y); return
            self._edit_orbit = False
            self._sel_mode_now = "add" if (e.state & 0x0001) else ("subtract" if (e.state & 0x0004) else self.edit_mode)
            # a Replace stroke clears the old selection once at its start, then adds for the rest of the drag; lasso and box replace on their own
            if self.edit_tool in ("brush", "magic") and self._sel_mode_now == "replace":
                if self._pts_sel is not None: self._pts_sel[:] = False
                if self._mesh_face_sel is not None: self._mesh_face_sel[:] = False
                self._sel_mode_now = "add"
            if self.edit_tool == "magic":
                self._magic_at(e.x, e.y, self._sel_mode_now); self._sel_path = None; self.draw(); return
            self._sel_path = [(e.x, e.y)]
            if self.edit_tool == "brush": self._brush_at(e.x, e.y, self._sel_mode_now)
            self.draw(); return
        self._drag = (e.x, e.y); self._press_at = (e.x, e.y)
    def _release(self, e):
        if self.edit_tool and self._edit_orbit and e.num == 1:            # empty-margin orbit drag ended
            self._edit_orbit = False; self._drag = None; self._press_at = None; return
        if self.edit_tool and self._sel_path is not None and e.num == 1:  # finish the LEFT-button stroke
            path = self._sel_path; self._sel_path = None; m = getattr(self, "_sel_mode_now", "replace")
            if self.edit_tool == "lasso" and len(path) >= 3:
                self.select_region(path, mode=m)
            elif self.edit_tool == "rect" and len(path) >= 2:
                (x0, y0) = path[0]; (x1, y1) = path[-1]
                self.select_region([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], mode=m)
            else:
                self.draw()                                # brush already applied live
            return
        navp = getattr(self, "_nav_press", None)
        self._nav_press = None
        self._drag = None
        if navp is not None and e.num == 1:
            nav = viewnav.finish_click(navp, e.x, e.y)
            if nav is not None:
                self.set_view(*nav["view"])
            self._press_at = None; return
        if self.on_pick and self._press_at and abs(e.x - self._press_at[0]) <= 8 and abs(e.y - self._press_at[1]) <= 8 and e.num == 1:   # a click, not a drag
            r = self.pick(e.x, e.y)
            if r is not None:
                try: self.on_pick(*r)
                except Exception: pass
        self._press_at = None
    def _motion(self, e):
        nav = self._nav_hit(e.x, e.y)
        hv = nav.get("key") if nav and nav.get("kind") == "face" else None
        if hv != getattr(self, "_nav_hover", None):
            self._nav_hover = hv; self.draw()
        if self.edit_tool == "brush" and self._drag is None:
            self._sel_path = [(e.x, e.y)]
            self.draw()
    def _rotate(self, e):
        if self.edit_tool and self._edit_orbit and self._drag:            # left-drag from the empty margin: orbit
            dx, dy = e.x - self._drag[0], e.y - self._drag[1]; self._drag = (e.x, e.y)
            self.rot = self._axis_rot(dy * 0.5, 1, 0, 0) @ self._axis_rot(dx * 0.5, 0, 1, 0) @ self.rot; self.draw(); return
        if self.edit_tool and self._sel_path is not None:  # extend the stroke, don't orbit
            self._sel_path.append((e.x, e.y))
            if self.edit_tool == "brush": self._brush_at(e.x, e.y, getattr(self, "_sel_mode_now", "add"))
            self.draw(); return
        if not self._drag: return
        if getattr(self, "_nav_press", None) is not None:
            if not viewnav.drag_exceeded(self._nav_press, e.x, e.y): return
            self._nav_press = None
        if self._press_at and abs(e.x - self._press_at[0]) <= 8 and abs(e.y - self._press_at[1]) <= 8: return   # still within a click
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]; self._drag = (e.x, e.y)
        # Free rotation about the screen's own axes: EVERY angle is reachable and nothing ever locks.
        # This must stay free (not a clamped turntable) - the 3-point base-removal tool needs to orient
        # the mesh from any direction to place the cut, and clamping the pitch broke it.
        self.rot = self._axis_rot(dy * 0.5, 1, 0, 0) @ self._axis_rot(dx * 0.5, 0, 1, 0) @ self.rot; self.draw()
    def _pan(self, e):
        if not self._drag: return
        w, h = max(64, self.winfo_width()), max(64, self.winfo_height())
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]; self._drag = (e.x, e.y)
        if self.edit_tool:                 # while a select tool is active, right/middle-drag ORBITS so you can
            self.rot = self._axis_rot(dy * 0.5, 1, 0, 0) @ self._axis_rot(dx * 0.5, 0, 1, 0) @ self.rot; self.draw(); return   # check coverage from any angle; the red selection persists
        self.pan[0] += dx / (w * 0.9); self.pan[1] -= dy / (h * 0.9); self.draw()   # scale pointer movement to the viewport
    def _wheel(self, e, direction=None):
        d = direction if direction is not None else (1 if e.delta > 0 else -1)
        if self.edit_tool == "brush":                        # scroll sizes the brush, not the zoom
            self.brush_px = max(6.0, min(120.0, self.brush_px * (1.15 if d > 0 else 1 / 1.15)))
            if callable(self.on_brush_size):                 # keep the palette's slider/number in sync
                try: self.on_brush_size(self.brush_px)
                except Exception: pass
            if self._sel_path is None: self._sel_path = [(e.x, e.y)]   # show the ring where the cursor is
            else: self._sel_path[-1] = (e.x, e.y)
            self.draw(); return
        self.zoom = max(0.05, min(40.0, self.zoom * (1.12 if d > 0 else 1 / 1.12))); self.draw()
