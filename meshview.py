#!/usr/bin/env python3
# Embedded interactive 3D view for PointYoink: drag to rotate, scroll to zoom, right-drag to pan,
# double-click to reset, solid or wireframe. Pure software rendering through shade.py (numpy +
# PIL), so it works everywhere Tk does: a low-detail copy of the mesh is drawn while the mouse
# moves and the full-detail one a moment after it stops.
import os, threading, time, tkinter as tk
import numpy as np
import shade

LO_FACES, HI_FACES = 12000, 40000

class MeshView(tk.Label):
    def __init__(self, master, bg="#0a0c10", **kw):
        super().__init__(master, bg=bg, bd=0, highlightthickness=0, text="", fg="#98a2b3", **kw)
        self.lo = self.hi = None; self.path = None; self.wire = False; self._photo = None
        self.azim, self.elev, self.zoom, self.pan = -35.0, 30.0, 1.0, [0.0, 0.0]
        self._drag = None; self._hi_job = None; self._busy = False; self._gen = 0
        self.bind("<ButtonPress-1>", self._press); self.bind("<B1-Motion>", self._rotate)
        self.bind("<ButtonPress-3>", self._press); self.bind("<B3-Motion>", self._pan)
        self.bind("<ButtonPress-2>", self._press); self.bind("<B2-Motion>", self._pan)
        self.bind("<ButtonRelease-1>", self._release); self.bind("<ButtonRelease-3>", self._release); self.bind("<ButtonRelease-2>", self._release)
        self.bind("<MouseWheel>", self._wheel); self.bind("<Button-4>", lambda e: self._wheel(e, 1)); self.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.bind("<Double-Button-1>", lambda e: self.reset()); self.bind("<Configure>", lambda e: self._schedule_hi(120))
    # ---- loading ----
    def load(self, path, on_ready=None):
        """Decimate in a worker thread, then draw. on_ready(ok) is called on the Tk thread."""
        self.path = path; self.lo = self.hi = None; self._gen += 1; gen = self._gen
        self.configure(image="", text="loading 3D view…"); self._photo = None
        def work():
            try:
                hv, hf = shade.load_oriented(path, HI_FACES)
                try:
                    import fast_simplification
                    lv, lf = fast_simplification.simplify(hv, hf, target_count=LO_FACES)
                    lv, lf = np.asarray(lv, np.float32), np.asarray(lf, np.int32)
                except Exception:
                    lv, lf = hv, hf
                res = ((lv, lf), (hv, hf))
            except Exception as e:
                res = e
            self.after(0, lambda: self._loaded(gen, res, on_ready))
        threading.Thread(target=work, daemon=True).start()
    def _loaded(self, gen, res, on_ready):
        if gen != self._gen: return
        if isinstance(res, Exception):
            self.configure(text="could not load this mesh"); (on_ready and on_ready(False)); return
        self.lo, self.hi = res; self.configure(text=""); self.reset(draw=False); self.draw(hi=True); (on_ready and on_ready(True))
    # ---- drawing ----
    def draw(self, hi=False):
        data = self.hi if (hi and self.hi is not None) else self.lo
        if data is None: return
        w, h = max(64, self.winfo_width()), max(64, self.winfo_height())
        from PIL import ImageTk
        img = shade.render(data[0], data[1], size=(w, h), wire=self.wire, azim=self.azim, elev=self.elev, zoom=self.zoom, pan=tuple(self.pan))
        self._photo = ImageTk.PhotoImage(img); self.configure(image=self._photo)
    def _schedule_hi(self, ms=150):
        if self._hi_job: self.after_cancel(self._hi_job)
        self._hi_job = self.after(ms, lambda: (setattr(self, "_hi_job", None), self.draw(hi=True)))
    def set_wire(self, on):
        self.wire = bool(on); self.draw(hi=True)
    def reset(self, draw=True):
        self.azim, self.elev, self.zoom, self.pan = -35.0, 30.0, 1.0, [0.0, 0.0]
        if draw: self.draw(hi=True)
    def snapshot(self, path, size=(900, 600)):
        """Full-quality PNG of the current view (for thumbnails/sharing)."""
        if self.hi is None: return None
        shade.render(self.hi[0], self.hi[1], size=size, wire=self.wire, azim=self.azim, elev=self.elev, zoom=self.zoom, pan=tuple(self.pan)).save(path); return path
    # ---- mouse ----
    def _press(self, e): self._drag = (e.x, e.y)
    def _release(self, e): self._drag = None; self._schedule_hi(60)
    def _rotate(self, e):
        if not self._drag: return
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]; self._drag = (e.x, e.y)
        self.azim -= dx * 0.5; self.elev = max(-89, min(89, self.elev + dy * 0.5)); self._live()
    def _pan(self, e):
        if not self._drag: return
        w, h = max(64, self.winfo_width()), max(64, self.winfo_height())
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]; self._drag = (e.x, e.y)
        self.pan[0] += dx / (w * 0.42); self.pan[1] -= dy / (h * 0.42); self._live()
    def _wheel(self, e, direction=None):
        d = direction if direction is not None else (1 if e.delta > 0 else -1)
        self.zoom = max(0.2, min(8.0, self.zoom * (1.12 if d > 0 else 1 / 1.12))); self._live()
    def _live(self):
        if self._busy: return
        self._busy = True
        try: self.draw(hi=False)
        finally: self._busy = False
        self._schedule_hi(150)
