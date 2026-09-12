#!/usr/bin/env python3
# PointYoink - pull 3D scans off a Revopoint MIRACO over USB (Linux).
# MIT licensed. See LICENSE.
# Unofficial. Not affiliated with or endorsed by Revopoint.
# "Revopoint" and "MIRACO" are trademarks of their respective owners.
import os, re, json, time, glob, shutil, threading, subprocess, queue
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image

APP = "PointYoink"; VERSION = "0.9.9-pre"
GITHUB = "https://github.com/datboip/pointyoink"
HOME = os.path.expanduser("~")
MOUNT = os.path.join(HOME, "revopoint-mtp")
PROJECTS = os.path.join(MOUNT, "Internal shared storage", "Projects")
SCREENSHOTS = os.path.join(MOUNT, "Internal shared storage", "Screenshots")
THUMBS = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), "pointyoink", "thumbs")   # private, per user
CFG_DIR = os.path.join(HOME, ".config", "pointyoink"); CFG = os.path.join(CFG_DIR, "config.json")
# tests and scratch runs point POINTYOINK_CONFIG somewhere else so they never overwrite real settings
if os.environ.get("POINTYOINK_CONFIG"):
    CFG = os.environ["POINTYOINK_CONFIG"]; CFG_DIR = os.path.dirname(CFG) or CFG_DIR
HERE = os.path.dirname(os.path.abspath(__file__)); ICON = os.path.join(HERE, "icon.png")
DEFAULT_DEST = os.path.join(HOME, "revopoint-scans-models")
VID = "2207"
for d in (THUMBS, CFG_DIR): os.makedirs(d, exist_ok=True)

# palette
BG="#0e1117"; CARD="#171b23"; CARD2="#1d222c"; STROKE="#2a3140"; SELB="#22304a"
AC="#4aa3ff"; AC_H="#3b8fe6"; OK="#3ecf8e"; WARN="#ffb454"; DANGER="#ff6b6b"
TX="#eef1f5"; MUT="#98a2b3"

CHANGELOG = """0.8.0
  - Process on PC: rebuild a scan's mesh on your computer from the raw depth
    frames (GPU when available). Skips the scanner's slow on-device fusion and
    matches its output to about 0.2 mm. Uses frames already on disk from a full
    import, otherwise pulls just what it needs. Works on unfused scans too.
    Needs Open3D (optional install; the app tells you if it is missing).
  - Settings: Process on PC detail (voxel size, 0.4 mm = scanner).

0.7.0
  - Remove base: an interactive cut-plane tool to slice the table/turntable off
    a scan (keeps a cleaned copy). Plus optional mesh cleanup on import.
  - Captures tab: browse and pull the scanner's screenshots AND screen
    recordings, in their own place instead of the project list.
  - A Tools row groups View in 3D and Remove base.
  - A bottom status bar shows activity so the app never feels frozen.
  - Heavy mesh work runs in a memory-capped process so it can't crash your PC.
  - "Imported" now requires a real model file; partial export/zip failures are
    reported instead of silently passing; safer device mount cleanup.

0.6.2
  - "Imported" now reflects what is actually on disk - the badge clears if you
    delete the files, and updates live.
  - Project cards rebalanced so the size no longer gets cut off.

0.6.1
  - Export ZIP now shows an estimated size next to each option, so you can pick
    one that fits (e.g. under an upload limit) before zipping.

0.6.0
  - Cleaner file layout: imports land flat with clear unique names
    (Project_<scan>.ply / .stl / .png), not buried in nested folders.
  - Export ZIP now asks what to include (STL / OBJ / GLB / all models /
    everything) and packs files flat, so unzipping is ready to use. It can
    make STLs on the fly even if you did not export them at import.

0.5.2
  - Fix the square outline around the "View in 3D" button.

0.5.1
  - 3D viewer opens already-drawn (no black flash while it loads).
  - Preview image scales to fit the window at any size.
  - Project cards on three lines so nothing is cut off.
  - Exported STL/OBJ/GLB are named per project + scan, so they stay unique
    when you gather them in one folder.

0.5.0
  - View in 3D: open a scan's mesh in an interactive window (drag to rotate,
    scroll to zoom). Works straight from the device or a local copy.

0.4.0
  - Export ZIP button: bundle the selected project(s) into a .zip in your save
    folder (raw frames skipped), for archiving or moving to another machine.

0.3.2
  - Fix a crash that stopped the project list from showing whenever the
    scanner had projects on it (an undefined name in the list renderer).
  - HiDPI: read the GNOME desktop scale (and POINTYOINK_SCALE) so the window
    is sized correctly on scaled displays.

0.3.1
  - Fix tiny window on HiDPI laptops (auto-detect display scale) and add a
    UI scale setting.
  - Smarter re-import: detects when a project changed on the device.
  - Themed dialogs, animated conversion progress.

0.3.0
  - Rounded UI built on CustomTkinter, app icon, splash screen, cleaner spacing.
  - Auto-connects when the scanner is in File Transfer mode.
  - Optional STL / OBJ export of the meshes on import.
  - Rename a project (keeps the original ID as reference) and remember
    what's already been imported across sessions.
  - Bigger About with supported devices and an inline changelog.
  - Preview/Files tabs, per-project size, import summary.
  - Settings, Cancel and Retry, built-in error log, MIT licensed.

0.2.0
  - Project list with thumbnails and the scanner's own scan renders.
  - Models-only vs full import, imported badges, destination picker.

0.1.0
  - First working build: mount the MIRACO over MTP, pick projects, pull them.
  - Models-only skips the raw depth frames for a big speed-up."""

HELP = """POINTYOINK - HELP

WHAT IT DOES
PointYoink copies your finished 3D scans off a Revopoint MIRACO / MIRACO Pro
onto Linux, over the USB cable. No Windows, no Revo Scan, no cloud account.

HOW TO USE
1. Plug the scanner into this PC with the USB-C cable.
2. On the MIRACO screen tap  "File Transfer"  (Share to PC -> USB Cable).
3. Click  Connect. Your projects appear on the left.
4. Tick the projects you want; click one to preview its scans.
5. Pick a "Save to" folder and click  Import selected.

MODELS ONLY vs FULL
- "Models only" (default) copies just the finished meshes + point clouds
  (.ply) and skips the thousands of raw depth frames. This is ~1000x faster
  (~19 MB/s vs ~20 KB/s).
- Turn it OFF only if you want the raw frames to re-process a scan later in
  Revo Scan on another machine.

WHAT YOU GET
- fuse_mesh.ply = the finished 3D MESH (with faces) - what you print/render.
- fuse.ply      = the fused POINT CLOUD (points only).
Both are standard .ply - open them in Blender, MeshLab, or CloudCompare.
The "Files" tab lists every model file and its size before you import.

TROUBLESHOOTING
- "Not connected" won't go away: make sure you tapped FILE TRANSFER on the
  scanner (not just plugged it in). The scanner shows a USB mode popup.
- USB connect fails / hangs: unplug and replug the cable, re-tap File Transfer,
  then click USB again. PointYoink clears stale connections automatically.
- Previews blank: the scanner is still waking up - click the project again.
- Import slow with "Models only" OFF: that's the raw frames; expected.

PRIVACY & LEGAL
Everything happens locally over USB - nothing is uploaded anywhere.
PointYoink is UNOFFICIAL and not affiliated with or endorsed by Revopoint.
It only reads files off your own device.

fuse_mesh.ply = finished mesh (faces).   fuse.ply = point cloud (points)."""

import traceback as _tb, datetime as _dt
LOGFILE = os.path.join(CFG_DIR, "pointyoink.log")

def log_line(msg):
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOGFILE, "a") as f:
            f.write("[%s] %s\n" % (stamp, msg))
    except Exception:
        pass

def log_error(kind, exc):
    log_line("%s: %s\n%s" % (kind, exc, "".join(_tb.format_exception(type(exc), exc, exc.__traceback__)).rstrip()))

import sys as _sys
def _excepthook(t, v, tb):
    log_line("UNCAUGHT: %s\n%s" % (v, "".join(_tb.format_exception(t, v, tb)).rstrip()))
    _sys.__excepthook__(t, v, tb)
_sys.excepthook = _excepthook

# ---------------- config ----------------
def load_cfg():
    try: return json.load(open(CFG))
    except Exception: return {}
def save_cfg(c):
    try: json.dump(c, open(CFG, "w"), indent=2)
    except Exception: pass

# ---------------- device / mount ----------------
def usb_state():
    dev = None
    for d in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            if open(d).read().strip() == VID: dev = os.path.dirname(d); break
        except Exception: pass
    if not dev: return "absent", None
    classes = []
    for f in glob.glob(dev + "/*/bInterfaceClass"):
        try: classes.append(open(f).read().strip())
        except Exception: pass
    serial = None
    try: serial = open(os.path.join(dev, "serial")).read().strip()
    except Exception: pass
    return ("mtp" if "06" in classes else "adb"), serial

def quick_mounted():
    try:
        return subprocess.run(["ls", os.path.join(MOUNT, "Internal shared storage")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6).returncode == 0
    except Exception: return False

def do_mount():
    if quick_mounted(): return True, "already mounted"
    # Clear our OWN mountpoint gracefully first (don't blanket-kill MTP for other
    # devices the user may have connected). Release any gvfs claim on the device,
    # lazily unmount our path, and only then kill a jmtpfs still holding OUR mount.
    # GNOME auto-mounts the scanner through gvfs the moment it enters File Transfer mode, which
    # makes it "busy" for jmtpfs. Unmount exactly those gvfs MTP mounts (a glob does nothing here).
    try:
        lst=subprocess.run(["gio","mount","-l"], capture_output=True, text=True, timeout=10).stdout
        for m in re.findall(r"(mtp://[^\s/]+/)", lst):
            subprocess.run(["gio","mount","-u",m], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except Exception: pass
    subprocess.run(["fusermount","-uz",MOUNT], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill","-9","-f","jmtpfs .*%s" % os.path.basename(MOUNT)], stderr=subprocess.DEVNULL)
    if not os.path.isdir(MOUNT):
        try: os.makedirs(MOUNT, exist_ok=True)
        except FileExistsError: pass
    time.sleep(1.5)
    try: r = subprocess.run(["jmtpfs",MOUNT], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired: return False, "jmtpfs timed out -- unplug/replug & re-tap File Transfer"
    time.sleep(2)
    if quick_mounted(): return True, "mounted"
    err=(r.stderr or r.stdout or "").strip()
    # libmtp's raw panics are noise to a user; say what it actually means
    if any(k in err for k in ("device is busy", "Can't open device", "MtpErrorCantOpenDevice", "Unable to open")):
        return False, ("The scanner isn't available over USB right now. If it's in PC mode or on a Model "
                       "screen, tap File Transfer on the scanner, then click USB.")
    return False, (err or "mount failed - replug USB & re-tap File Transfer")

def list_projects():
    out = []
    if not quick_mounted(): return out
    try: names = sorted(os.listdir(PROJECTS), reverse=True)
    except Exception: return out
    for name in names:
        pdir = os.path.join(PROJECTS, name)
        if not os.path.isdir(pdir): continue
        info = {"name": name, "meshes": None, "clouds": None, "date": None, "nodes": None, "thumb": None, "edit_time": None}
        revo = os.path.join(pdir, name + ".revo")
        if os.path.exists(revo):
            try:
                d = json.load(open(revo))
                info["meshes"]=d.get("model_mesh_count"); info["clouds"]=d.get("model_pointcloud_count")
                info["nodes"]=len(d.get("nodes", []))
                et=d.get("edit_time")
                if et:
                    info["date"]=time.strftime("%Y-%m-%d %H:%M", time.localtime(int(et)))
                    try: info["edit_time"]=int(et)
                    except Exception: info["edit_time"]=None
            except Exception: pass
        tp = os.path.join(THUMBS, name + "__thumb.png")
        if not os.path.exists(tp):
            try:
                for node in sorted(os.listdir(os.path.join(pdir,"data"))):
                    prev=os.path.join(pdir,"data",node,"preview.png")
                    if os.path.exists(prev): os.makedirs(THUMBS, exist_ok=True); shutil.copyfile(prev, tp); break
            except Exception: pass
        if os.path.exists(tp): info["thumb"]=tp
        out.append(info)
    return out

def list_screenshots():
    """Device screenshots (Internal shared storage/Screenshots), newest first."""
    out=[]
    if not quick_mounted(): return out
    try:
        for f in sorted(os.listdir(SCREENSHOTS), reverse=True):
            if f.lower().endswith((".png", ".jpg", ".jpeg")):
                out.append((f, os.path.join(SCREENSHOTS, f)))
    except Exception: pass
    return out

def list_recordings():
    """Screen recordings (videos) anywhere on the device except the big Projects tree."""
    out=[]; exts=(".mp4",".mkv",".webm",".mov",".avi",".m4v")
    if not quick_mounted(): return out
    root=os.path.join(MOUNT, "Internal shared storage")
    try:
        for entry in os.listdir(root):
            if entry=="Projects": continue    # skip the huge scan tree
            sub=os.path.join(root, entry)
            if os.path.isdir(sub):
                try:
                    for f in sorted(os.listdir(sub), reverse=True):
                        if f.lower().endswith(exts): out.append((f, os.path.join(sub, f)))
                except Exception: pass
            elif entry.lower().endswith(exts):
                out.append((entry, sub))
    except Exception: pass
    return out

SKIP_LOCAL = {"range", "wifi", "device-screenshots"}
def list_local_projects(dest):
    """Projects already on this PC (USB or WiFi imports), so the list works with no scanner attached."""
    out=[]
    try: names=sorted(os.listdir(dest), reverse=True)
    except Exception: return out
    for name in names:
        pdir=os.path.join(dest, name)
        if name.startswith(".") or name in SKIP_LOCAL or name.endswith("_glb") or not os.path.isdir(pdir): continue
        def node_of(path):   # <name>_<node>[_pcfused|_clean].ply -> node
            n=os.path.basename(path)[len(name)+1:-4]
            for suf in ("_pcfused","_clean"):
                if n.endswith(suf): n=n[:-len(suf)]
            return n
        flat=[x for x in glob.glob(os.path.join(pdir, name+"_*.ply")) if not x.endswith("_cloud.ply")]
        nested=glob.glob(os.path.join(pdir, "data", "*", "fuse_mesh.ply"))
        clouds=glob.glob(os.path.join(pdir, name+"_*_cloud.ply")) or glob.glob(os.path.join(pdir, "data", "*", "fuse.ply"))
        mesh_nodes=set(node_of(x) for x in flat) | set(os.path.basename(os.path.dirname(x)) for x in nested)
        # meshes the SCANNER made (One-tap Edit / Mesh there): the plain <name>_<node>.ply or data/<node>/fuse_mesh.ply, not our _pcfused/_clean builds
        dev_meshed=set(node_of(x) for x in flat if not (x.endswith("_pcfused.ply") or x.endswith("_clean.ply"))) | set(os.path.basename(os.path.dirname(x)) for x in nested)
        nodes=mesh_nodes | set(os.path.basename(d) for d in glob.glob(os.path.join(pdir, "data", "*")) if os.path.isdir(d))
        if not (flat or nested or clouds or os.path.exists(os.path.join(pdir, name+".revo"))): continue
        nodes.discard("combined")
        info={"name":name, "local":True, "meshes":len(mesh_nodes - {"combined"}), "clouds":len(clouds), "nodes":len(nodes) or None, "date":None, "thumb":None, "edit_time":None,
              "combined": os.path.exists(os.path.join(pdir, name+"_combined_pcfused.ply")), "prepared": bool(glob.glob(os.path.join(pdir, name+"_*_clean.ply"))),
              "dev_meshed": len(dev_meshed - {"combined"})}
        try:
            d=json.load(open(os.path.join(pdir, name+".revo"))); et=d.get("edit_time")
            if et: info["date"]=time.strftime("%Y-%m-%d %H:%M", time.localtime(int(et))); info["edit_time"]=int(et)
        except Exception:
            try: info["date"]=time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(pdir)))
            except Exception: pass
        tp=os.path.join(THUMBS, name+"__thumb.png")
        if not os.path.exists(tp):
            for src in sorted(glob.glob(os.path.join(pdir, name+"_*.png"))) + sorted(glob.glob(os.path.join(pdir, "data", "*", "preview.png"))):
                try: os.makedirs(THUMBS, exist_ok=True); shutil.copyfile(src, tp); break
                except Exception: pass
        if os.path.exists(tp): info["thumb"]=tp
        out.append(info)
    return out

def project_model_size(name, local=None):
    total=0; files=[]
    plys=glob.glob(os.path.join(PROJECTS,name,"data","*","*.ply"))
    if not plys and local:
        plys=glob.glob(os.path.join(local, "*.ply")) or glob.glob(os.path.join(local, "data", "*", "*.ply"))
    for ply in plys:
        try:
            sz=os.path.getsize(ply); total+=sz
            files.append((os.path.basename(os.path.dirname(ply)), os.path.basename(ply), sz))
        except Exception: pass
    return total, files

def gather_gallery(name, local=None):
    paths=[]
    try: nodes=sorted(os.listdir(os.path.join(PROJECTS,name,"data")))
    except Exception:
        nodes=[]
    if not nodes and local:      # project only on this PC: flat <name>_<node>.png or nested previews
        for png in sorted(glob.glob(os.path.join(local, name+"_*.png"))): paths.append((os.path.basename(png)[len(name)+1:-4], png))
        if not paths:
            for pv in sorted(glob.glob(os.path.join(local, "data", "*", "preview.png"))): paths.append((os.path.basename(os.path.dirname(pv)), pv))
        _combined_tile(name, local, paths)
        return paths
    for node in nodes:
        prev=os.path.join(PROJECTS,name,"data",node,"preview.png")
        lp=os.path.join(THUMBS,"%s__%s.png"%(name,node))
        if not os.path.exists(lp):
            if os.path.exists(prev):
                try: shutil.copyfile(prev, lp)
                except Exception: continue
            else: continue
        paths.append((node, lp))
    if local: _combined_tile(name, local, paths)       # also when the scanner is connected (the combined model lives on this PC)
    return paths

def _combined_tile(name, local, paths):
    """The model built from all lined-up scans gets its own tile (rendered here, cached under THUMBS)."""
    comb=os.path.join(local, name+"_combined_pcfused.ply")
    if not os.path.exists(comb): return
    tp=os.path.join(THUMBS, "%s__combined__card.png" % name)
    if not os.path.exists(tp) or os.path.getmtime(tp)<os.path.getmtime(comb):
        try:
            import shade; os.makedirs(THUMBS, exist_ok=True)
            v,f=shade.load_oriented(comb, 150000); shade.render(v, f, size=(330, 210), grid=False, gizmo=False).save(tp)
        except Exception as e: log_error("combined tile", e); return
    paths.append(("combined", tp))

def human_count(n):
    n=int(n or 0)
    return ("%.1fM" % (n/1e6)) if n>=1e6 else (("%.0fk" % (n/1e3)) if n>=1e4 else "{:,}".format(n))
def human(n):
    for u in ("B","KB","MB","GB"):
        if n<1024: return "%.0f %s"%(n,u) if u=="B" else "%.1f %s"%(n,u)
        n/=1024
    return "%.1f TB"%n

def cimg(path, w):
    im=Image.open(path); r=w/im.width; return ctk.CTkImage(light_image=im, dark_image=im, size=(w, int(im.height*r)))

# ---- side-panel "inspector" building blocks ----
DIM="#5a6474"; DIM2="#414b5a"; CHIP="#232a36"; CHIP_TX="#c8d0db"
def hairline(master, padx=16):
    """1px separator between inspector rows (instead of bordered buttons)."""
    tk.Frame(master, bg=STROKE, height=1, bd=0, highlightthickness=0).pack(fill="x", padx=padx)   # a 1px CTkFrame draws nothing
def group_label(master, text, top=14):
    """Small-caps group header inside a side-panel section."""
    ctk.CTkLabel(master, text=text.upper(), text_color=MUT, font=ctk.CTkFont(size=10, weight="bold"), anchor="w").pack(fill="x", padx=16, pady=(top,2))

class ActionRow(ctk.CTkFrame):
    """Full-width inspector row: a leading icon (or a checkbox), a bold title and a one-line muted subtitle.
    Acts like a button: hover tint, click runs the command, configure(state=...) dims and disables it."""
    def __init__(self, master, title, sub, icon="", command=None, icon_color=None, check=None, **kw):
        super().__init__(master, fg_color="transparent", corner_radius=10, **kw)
        self._cmd=command; self._state="normal"; self._icol=icon_color or TX; self._var=check
        self.grid_columnconfigure(1, weight=1)
        if check is not None:
            self.lead=ctk.CTkCheckBox(self, text="", width=24, height=24, checkbox_width=20, checkbox_height=20, corner_radius=6,
                                      variable=check, onvalue=True, offvalue=False, fg_color=AC, hover_color=AC_H, border_color=DIM,
                                      command=self._checked)
            self.lead.grid(row=0,column=0, rowspan=2, padx=(12,4), pady=8)
        else:
            self.lead=ctk.CTkLabel(self, text=icon, width=28, text_color=self._icol, font=ctk.CTkFont(size=16))
            self.lead.grid(row=0,column=0, rowspan=2, padx=(10,4), pady=8)
        self.ti=ctk.CTkLabel(self, text=title, text_color=TX, anchor="w", height=18, font=ctk.CTkFont(size=12, weight="bold"))
        self.ti.grid(row=0,column=1, sticky="ew", padx=(0,12), pady=(8,0))
        self.su=ctk.CTkLabel(self, text=sub, text_color=MUT, anchor="w", height=15, font=ctk.CTkFont(size=10))
        self.su.grid(row=1,column=1, sticky="ew", padx=(0,12), pady=(1,8))
        for w in (self, self.ti, self.su) + (() if check is not None else (self.lead,)):
            w.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter); self.bind("<Leave>", self._leave)
    def _inside(self, e):
        try: w=self.winfo_containing(e.x_root, e.y_root)
        except Exception: return False
        return w is not None and (str(w)==str(self) or str(w).startswith(str(self)+"."))
    def bind(self, sequence=None, command=None, add=True):
        # hover bindings (ours and tooltips) cover the whole row: children included, and a move between
        # the row's own children does not count as leaving it
        if sequence=="<Leave>":
            cmd=command
            def guarded(e):
                if not self._inside(e): cmd(e)
            command=guarded
        super().bind(sequence, command, add)
        if sequence in ("<Enter>","<Leave>") and getattr(self, "su", None) is not None:
            for w in (self.lead, self.ti, self.su):
                try: w.bind(sequence, command, add)
                except Exception: pass
    def _enter(self, _=None):
        if self._state=="normal": super().configure(fg_color=CARD2)
    def _leave(self, _=None): super().configure(fg_color="transparent")
    def _click(self, _=None):
        if self._state!="normal": return
        if self._var is not None: self._var.set(not self._var.get())
        if self._cmd: self._cmd()
    def _checked(self):
        if self._cmd: self._cmd()
    def configure(self, require_redraw=False, **kw):
        if "state" in kw:
            self._state=kw.pop("state"); dim=(self._state=="disabled")
            self.ti.configure(text_color=(DIM if dim else TX)); self.su.configure(text_color=(DIM2 if dim else MUT))
            if self._var is None: self.lead.configure(text_color=(DIM if dim else self._icol))
            else: self.lead.configure(state=self._state)
            if dim: super().configure(fg_color="transparent")
        if "command" in kw: self._cmd=kw.pop("command")
        if "text" in kw: self.ti.configure(text=kw.pop("text"))
        if kw: super().configure(require_redraw=require_redraw, **kw)
    def cget(self, attribute_name):
        if attribute_name=="state": return self._state
        return super().cget(attribute_name)

ROW="transparent"            # project list row at rest (selected rows use SELB)
MODE_LABEL={"Projects":"Import","Local":"Projects","Captures":"Captures","Process":"Prepare","Live":"Live view"}
MODE_KEY={v:k for k,v in MODE_LABEL.items()}
HEADER_KEY={"Import":"Projects","Projects":"Local","Captures":"Captures","Prepare":"Process","Live view":"Live"}   # header tab label -> page key

class TabStrip(ctk.CTkFrame):
    """Text tabs with an accent underline (the mock's style). add() returns the tab's content frame, or
    None for a bare strip (content=False, used for the header modes). Extra controls can be packed into
    .bar (side='right'). set() shows a tab; the command gets the tab name when a user clicks."""
    def __init__(self, master, command=None, content=True, base=BG, size=13, line=True, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self._cmd=command; self._tabs={}; self._cur=None; self._base=base; self._size=size
        self.bar=ctk.CTkFrame(self, fg_color="transparent"); self.bar.grid(row=0,column=0, sticky="ew")
        self.body=None
        if content:
            self.grid_columnconfigure(0, weight=1); self.grid_rowconfigure(2, weight=1)
            if line: tk.Frame(self, bg=STROKE, height=1, bd=0, highlightthickness=0).grid(row=1,column=0, sticky="ew")
            self.body=ctk.CTkFrame(self, fg_color="transparent"); self.body.grid(row=2,column=0, sticky="nsew")
            self.body.grid_columnconfigure(0, weight=1); self.body.grid_rowconfigure(0, weight=1)
    def add(self, name, tag=None, icon=None):
        cell=ctk.CTkFrame(self.bar, fg_color="transparent"); cell.pack(side="left", padx=(0,4))
        f=ctk.CTkFont(size=self._size, weight="bold"); text=((icon+"  ") if icon else "")+name
        b=ctk.CTkButton(cell, text=text, width=f.measure(text)+22, height=30, corner_radius=6, fg_color="transparent",
                        hover_color=CARD2, text_color=MUT, font=f, command=lambda n=name: self.set(n, True))
        b.grid(row=0,column=0, padx=(4,0))
        if tag:
            ctk.CTkLabel(cell, text=tag, text_color=CHIP_TX, fg_color=CHIP, corner_radius=6, height=20, width=1,
                         font=ctk.CTkFont(size=10)).grid(row=0,column=1, padx=(4,6), ipadx=6)
        ul=tk.Frame(cell, bg=self._base, height=2, bd=0, highlightthickness=0)
        ul.grid(row=1,column=0,columnspan=2, sticky="ew", padx=(4,4), pady=(3,0))
        frame=None
        if self.body is not None:
            frame=ctk.CTkFrame(self.body, fg_color="transparent"); frame.grid(row=0,column=0, sticky="nsew"); frame.grid_remove()
        self._tabs[name]={"btn":b,"ul":ul,"frame":frame}
        if self._cur is None: self.set(name)
        return frame
    def set(self, name, fire=False):
        if name not in self._tabs: return
        self._cur=name
        for n,t in self._tabs.items():
            on=(n==name)
            t["btn"].configure(text_color=(TX if on else MUT)); t["ul"].configure(bg=(AC if on else self._base))
            if t["frame"] is not None:
                if on: t["frame"].grid()
                else: t["frame"].grid_remove()
        if fire and self._cmd: self._cmd(name)
    def get(self): return self._cur
    def tab(self, name): return self._tabs[name]["frame"]

class _ModeSwitch:
    """Adapter so the header tab strip still answers to mode_sw.set()/get() with the internal mode names."""
    def __init__(self, strip): self.strip=strip
    def set(self, name): self.strip.set(MODE_LABEL.get(name, name))
    def get(self): return MODE_KEY.get(self.strip.get(), self.strip.get())

class SplitButton(ctk.CTkFrame):
    """The one filled button in the window: a primary action plus a chevron with related actions."""
    def __init__(self, master, text, command, items, **kw):
        super().__init__(master, fg_color=AC, corner_radius=8, **kw)
        self._items=items
        self.main=ctk.CTkButton(self, text=text, height=40, corner_radius=8, fg_color=AC, hover_color=AC_H, text_color="#04121f",
                                font=ctk.CTkFont(size=13, weight="bold"), command=command)
        self.main.grid(row=0,column=0)
        tk.Frame(self, bg="#2c7ccc", width=1, bd=0, highlightthickness=0).grid(row=0,column=1, sticky="ns", pady=9)
        self.more=ctk.CTkButton(self, text="▾", width=34, height=40, corner_radius=8, fg_color=AC, hover_color=AC_H, text_color="#04121f",
                                font=ctk.CTkFont(size=13, weight="bold"), command=self._menu)
        self.more.grid(row=0,column=2)
    def _menu(self):
        m=tk.Menu(self, tearoff=0, bg=CARD2, fg=TX, activebackground=SELB, activeforeground=TX, bd=0, relief="flat",
                  font=("TkDefaultFont", 10), activeborderwidth=0)
        for label,cmd in self._items: m.add_command(label=label, command=cmd)
        try: m.tk_popup(self.winfo_rootx(), self.winfo_rooty()-len(self._items)*30-8)
        finally: m.grab_release()
    def configure(self, require_redraw=False, **kw):
        if "text" in kw: self.main.configure(text=kw.pop("text"))
        if "state" in kw:
            st=kw.pop("state"); self.main.configure(state=st); self.more.configure(state=st)
        if kw: super().configure(require_redraw=require_redraw, **kw)

# ---- empty states: a faint ring backsplash, a line illustration, a headline, one line, up to two buttons ----
ES_BG="#0a0c10"; ES_RING="#1e2634"; ES_LINE="#3a4556"; ES_MESH="#2a3140"
ES_COPY={   # kind -> (headline, one line of explanation)
    "captures": ("No captures yet", "Screenshots come over USB only. WiFi sends just the project you share."),
    "projects": ("No projects yet", "Connect over USB for all of them, or share one over WiFi."),
    "preview":  ("Nothing to preview", "Pick a project: its scans show here as a 3D model you can turn and zoom."),
    "live":     ("Live view is not connected", "Turn on the scanner's WiFi, then find it on your network."),
}
def _mix(a, b, t):
    """Blend two #rrggbb colours (t=0 -> a, t=1 -> b)."""
    a=[int(a[i:i+2],16) for i in (1,3,5)]; b=[int(b[i:i+2],16) for i in (1,3,5)]
    return "#%02x%02x%02x" % tuple(int(round(x+(y-x)*t)) for x,y in zip(a,b))
def _rrect(cv, x0, y0, x1, y1, r, tag, color=ES_LINE, width=3):
    """Outline-only rounded rectangle drawn with four arcs and four lines."""
    for box,start in (((x0,y0,x0+2*r,y0+2*r),90), ((x1-2*r,y0,x1,y0+2*r),0), ((x1-2*r,y1-2*r,x1,y1),270), ((x0,y1-2*r,x0+2*r,y1),180)):
        cv.create_arc(*box, start=start, extent=90, style="arc", outline=color, width=width, tags=tag)
    for seg in ((x0+r,y0,x1-r,y0), (x1,y0+r,x1,y1-r), (x0+r,y1,x1-r,y1), (x0,y0+r,x0,y1-r)):
        cv.create_line(*seg, fill=color, width=width, tags=tag)
def _illustration(cv, kind, cx, cy, s, tag):
    """Simple line drawing for an empty state, in a 130x110 box (times s) centred on (cx, cy).
    Muted 3 px strokes with one accent detail."""
    ox,oy=cx-65*s, cy-55*s
    def p(x,y): return (ox+x*s, oy+y*s)
    w=max(2, round(3*s)); L=dict(fill=ES_LINE, width=w, capstyle="round", joinstyle="round", tags=tag)
    if kind=="captures":            # scanner (screen side) with a USB cable running down to a plug
        _rrect(cv, *p(20,4), *p(110,60), 9*s, tag, width=w); _rrect(cv, *p(30,13), *p(100,51), 4*s, tag, width=w)
        cv.create_line(*p(74,17), *p(90,17), **L); cv.create_line(*p(74,25), *p(84,25), **L)   # a couple of UI lines on the screen
        cv.create_line(*p(65,60), *p(65,67), *p(72,73), *p(72,80), smooth=True, **L)
        _rrect(cv, *p(63,80), *p(81,98), 3*s, tag, width=w)
        cv.create_rectangle(*p(68,98), *p(76,108), fill=AC, outline="", tags=tag)                  # accent: the plug's tip
    elif kind=="projects":          # scanner outline with a small radio wave above its corner
        _rrect(cv, *p(16,36), *p(96,92), 9*s, tag, width=w); _rrect(cv, *p(26,45), *p(86,83), 4*s, tag, width=w)
        cv.create_line(*p(40,53), *p(72,53), **L)
        for r in (11, 21):
            cv.create_arc(*p(100-r,34-r), *p(100+r,34+r), start=35, extent=110, style="arc", outline=ES_LINE, width=w, tags=tag)
        cv.create_oval(*p(96,30), *p(104,38), fill=AC, outline="", tags=tag)                       # accent: the wave's origin
    elif kind=="preview":           # isometric cube with light mesh lines and a lit front vertex
        T,UR,LR,B,LL,UL,C=p(65,9),p(105,32),p(105,78),p(65,101),p(25,78),p(25,32),p(65,55)
        m=lambda a,b: ((a[0]+b[0])/2, (a[1]+b[1])/2)
        thin=dict(fill=ES_MESH, width=1, tags=tag)
        cv.create_line(*m(T,UL), *m(UR,C), **thin); cv.create_line(*m(T,UR), *m(UL,C), **thin)  # top face
        cv.create_line(*m(UL,LL), *m(C,B), **thin); cv.create_line(*m(UL,C), *m(LL,B), **thin)  # left face
        cv.create_line(*m(UR,LR), *m(C,B), **thin); cv.create_line(*m(UR,C), *m(LR,B), **thin)  # right face
        cv.create_line(*T, *UR, *LR, *B, *LL, *UL, *T, **L)
        for v in (UL, UR, B): cv.create_line(*C, *v, **L)
        r=4.5*s; cv.create_oval(C[0]-r, C[1]-r, C[0]+r, C[1]+r, fill=AC, outline="", tags=tag)   # accent: the front vertex
    elif kind=="live":              # camera lens: outer barrel, inner ring, an accent iris and a highlight
        for r in (46, 32):
            cv.create_oval(*p(65-r,55-r), *p(65+r,55+r), outline=ES_LINE, width=w, tags=tag)
        cv.create_arc(*p(45,35), *p(85,75), start=40, extent=250, style="arc", outline=AC, width=w, tags=tag)  # accent: the iris
        cv.create_oval(*p(48,36), *p(56,44), fill=ES_LINE, outline="", tags=tag)
def draw_empty_state(cv, kind, buttons=(), scale=1.0, tag="empty"):
    """Draw an empty state on a canvas: a ring backsplash that fades toward the edges, the illustration,
    a headline, one line of text and the given CTkButtons (placed as canvas windows). Everything is
    vertically centred in the canvas; call again on <Configure> to re-centre. Existing items with the
    tag are replaced, so it is safe to call repeatedly."""
    cv.delete(tag)
    W=max(40, cv.winfo_width()); H=max(40, cv.winfo_height()); s=scale
    head,line=ES_COPY[kind]
    fh=ctk.CTkFont(size=18, weight="bold"); fl=ctk.CTkFont(size=13)
    k=s*min(1.5, max(1.0, 1+(min(W,H)/s-420)/700))          # a bigger drawing (and wider rings) in a big area
    illus=110*k; gap1=22*s; hh=fh.metrics("linespace"); gap2=8*s; gap3=20*s
    bw=[b.winfo_reqwidth() for b in buttons]; bh=max([b.winfo_reqheight() for b in buttons] or [0])
    stack=bool(buttons) and (sum(bw)+12*s*(len(buttons)-1) > W-24)     # narrow column: one button under the other
    btn_h=(bh*len(buttons)+8*s*(len(buttons)-1)) if stack else bh
    # the text wraps inside the width it has; measure it before laying the block out
    tw=int(min(W-32, 460*s)); tid=cv.create_text(0,0, text=line, fill=MUT, font=fl, width=tw, justify="center", anchor="n", tags=tag)
    x0,y0,x1,y1=cv.bbox(tid); th=y1-y0
    total=illus+gap1+hh+gap2+th+(gap3+btn_h if buttons else 0)
    if total+16 > H: illus=0; gap1=0; total=hh+gap2+th+(gap3+btn_h if buttons else 0)   # short area: drop the drawing
    cx=W/2; y=max(8, (H-total)*0.46)
    ccx,ccy=(cx, y+illus/2) if illus else (cx, y+hh/2)
    # backsplash: concentric rings around the drawing, fading to the background at the edges
    rmax=max(((cx-x)**2+(ccy-yy)**2)**0.5 for x in (0,W) for yy in (0,H)); step=26*k; r=step*0.9
    while r<rmax:
        col=_mix(ES_BG, ES_RING, max(0.0, 1-r/rmax)**1.8)
        if col!=ES_BG: cv.create_oval(ccx-r, ccy-r, ccx+r, ccy+r, outline=col, width=1, tags=tag)
        r+=step
    if illus: _illustration(cv, kind, ccx, ccy, k, tag); y+=illus+gap1
    cv.create_text(cx, y, text=head, fill=TX, font=fh, anchor="n", tags=tag); y+=hh+gap2
    cv.coords(tid, cx, y); cv.tag_raise(tid); y+=th
    if buttons:
        y+=gap3
        if stack:
            for b,w in zip(buttons,bw): cv.create_window(cx, y, window=b, anchor="n", tags=tag); y+=bh+8*s
        else:
            x=cx-(sum(bw)+12*s*(len(buttons)-1))/2
            for b,w in zip(buttons,bw): cv.create_window(x, y, window=b, anchor="nw", tags=tag); x+=w+12*s

class EmptyState(ctk.CTkFrame):
    """An empty-state panel (see draw_empty_state) that fills whatever cell it is gridded into and
    redraws itself centred whenever that cell changes size. .buttons holds the CTkButtons in order."""
    def __init__(self, master, kind, buttons=(), scale=1.0, **kw):
        super().__init__(master, fg_color=ES_BG, corner_radius=0, **kw)
        self.kind=kind; self.scale=scale
        self.cv=tk.Canvas(self, bg=ES_BG, highlightthickness=0, bd=0); self.cv.pack(fill="both", expand=True)
        self.buttons=[]
        for i,(text,cmd) in enumerate(buttons):
            if i==0: b=ctk.CTkButton(self.cv, text=text, width=150, height=34, corner_radius=8, fg_color="transparent", border_width=1,
                                     border_color=AC, hover_color=CARD2, text_color=AC, font=ctk.CTkFont(size=13, weight="bold"), command=cmd)
            else: b=ctk.CTkButton(self.cv, text=text, width=130, height=34, corner_radius=8, fg_color="transparent",
                                  hover_color=CARD2, text_color=MUT, font=ctk.CTkFont(size=13), command=cmd)
            self.buttons.append(b)
        self.pack_propagate(False)
        self.cv.bind("<Configure>", lambda e: self.redraw())
    def redraw(self):
        try: draw_empty_state(self.cv, self.kind, self.buttons, self.scale)
        except Exception as e: log_error("empty-state", e)

def _kfmt(n):
    n=int(n or 0)
    if n>=1000000: return "%.1fM"%(n/1e6)
    if n>=1000: return "%dK"%(n//1000)
    return str(n)

def _render_mesh_png(path, out, mode="solid", size=(900,600)):
    """Off-screen render of a mesh to a PNG through shade.py (numpy + PIL): decimate, put the table
    plane on the floor, grey shaded material or blue wireframe, dark grid. Raises on any problem; the
    caller falls back to the scanner's preview."""
    import shade
    if os.path.getsize(path) > 1200*1024*1024: raise ValueError("mesh too large for a preview render")
    v,f=shade.load_oriented(path, 40000 if mode=="solid" else 30000)
    shade.render(v, f, size=size, wire=(mode=="wire")).save(out)

WORDMARK="Ubuntu"   # clean lowercase 'i' (the default bold font renders it like 'I')

def _has_imagetk():
    try:
        from PIL import ImageTk; return True
    except Exception: return False
def _has_trimesh():
    try:
        import trimesh; return True
    except Exception: return False

def _has_open3d():
    """Open3D is a ~400MB optional dep for Process on PC. Probe in a subprocess so the
    GUI process never loads it (it would stay resident in the app's memory)."""
    try:
        return subprocess.run([_sys.executable, "-c", "import open3d"], capture_output=True, timeout=60).returncode==0
    except Exception: return False

def _ply_counts(path):
    """Read vertex/face counts from a PLY header only (fast, no full load)."""
    v=f=0
    try:
        with open(path,"rb") as fh:
            for _ in range(80):
                line=fh.readline()
                if not line: break
                if line.startswith(b"element vertex"): v=int(line.split()[-1])
                elif line.startswith(b"element face"): f=int(line.split()[-1])
                elif line.strip()==b"end_header": break
    except Exception:
        pass
    return v,f

def _desktop_scale():
    """Best guess at the desktop UI scale so the app matches other windows.
    Priority: POINTYOINK_SCALE env > GNOME monitors.xml <scale> > None (caller falls back)."""
    env=os.environ.get("POINTYOINK_SCALE")
    if env:
        try:
            v=float(env)
            if 0.5<=v<=4: return v
        except Exception: pass
    try:
        import xml.etree.ElementTree as ET
        mx=os.path.expanduser("~/.config/monitors.xml")
        if os.path.exists(mx):
            scales=[float(s.text) for s in ET.parse(mx).getroot().iter("scale") if s.text]
            if scales:
                # the primary/most common scale
                return max(set(scales), key=scales.count)
    except Exception: pass
    return None

# ---------------- app ----------------
ctk.set_appearance_mode("dark"); ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        # per-project records keyed by ORIGINAL id: {label, imported_to, imported_at}
        self.records = self.cfg.get("records", {})
        # --- UI scaling: honor a saved override, else auto-detect HiDPI so it isn't tiny on laptops ---
        try:
            # priority: saved override > desktop scale (env / GNOME monitors.xml) > DPI heuristic > 1.0
            scale=self.cfg.get("ui_scale")
            if not scale:
                scale=_desktop_scale()
            if not scale:
                ppi=self.winfo_fpixels("1i") or 96.0   # note: XWayland reports a synthetic 96, so this rarely fires
                scale=max(1.0, min(2.5, round(ppi/96.0*20)/20)) if ppi>110 else 1.0
            scale=float(scale)
            if abs(scale-1.0)>0.02:
                ctk.set_widget_scaling(scale); ctk.set_window_scaling(scale)
            self._ui_scale=scale
        except Exception as e:
            self._ui_scale=1.0; log_error("ui-scale", e)
        # window size: default, but never bigger than the screen (keeps it usable on small/scaled displays)
        try:
            sw=self.winfo_screenwidth(); sh=self.winfo_screenheight()
            dw=min(1090, int(sw*0.92)); dh=min(1070, int(sh*0.90))   # tall enough for preview + renders + tools
        except Exception:
            dw,dh=1090,1070
        self.title("%s  %s" % (APP, VERSION))
        self.geometry(self.cfg.get("geometry", "%dx%d"%(dw,dh)))
        self.minsize(min(1024,dw), min(600,dh))
        self.configure(fg_color=BG)
        try:
            if os.path.exists(ICON):
                from PIL import ImageTk
                base=Image.open(ICON).convert("RGBA")
                self._iconimgs=[ImageTk.PhotoImage(base.resize((s,s), Image.LANCZOS)) for s in (256,128,64,48,32)]
                self.iconphoto(True, *self._iconimgs)
        except Exception as e: log_error("iconphoto", e)
        self.imgs={}             # image refs (needed by the splash, which runs first)
        self.withdraw()          # hide main window while the splash shows
        self._splash=None
        self._show_splash()

        self.q=queue.Queue(); self.pull_sel={}; self.projects=[]; self.projects_sig=None
        self.selected=None; self.gallery_cache={}; self.size_cache={}
        self.rows={}; self.serial=None
        self.pulling=False; self.cancel=False; self.listing=False; self.listed=False; self.proc=None
        self._mounting=False; self.auto_tried=False; self._wifi=None; self.listed_src=None; self._listing_src=None
        self.report_callback_exception = self._on_tk_error
        log_line("PointYoink %s started" % VERSION)

        self.grid_columnconfigure(0, weight=1); self.grid_rowconfigure(2, weight=1, minsize=300)
        self.search=ctk.StringVar(); self.shade_mode="solid"; self._film_sel=None; self._film_cells={}
        self._shade_lock=threading.Lock(); self._shade_want=None; self._shade_running=False; self._shade_key=None
        self._shade_failed=set(); self._mesh_stats={}
        self._header(); self._statusbar(); self._body(); self._build_options(); self._actions(); self._bottombar()
        self.search.trace_add("write", lambda *a: self._search_changed())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_loop(); self.drain_loop(); self._pulse()
        self.after(9000, self._close_splash)   # safety fallback; the setup checks normally close it
        self.after(2500, self._wifi_recover)   # offer a stranded WiFi transfer, if any

    # ---- splash + animation ----
    def _pointer_monitor(self):
        """Return (x,y,w,h) of the monitor the cursor is on (multi-monitor aware)."""
        try: px,py=self.winfo_pointerx(), self.winfo_pointery()
        except Exception: px,py=0,0
        try:
            out=subprocess.run(["xrandr","--listmonitors"], capture_output=True, text=True, timeout=3).stdout
            best=None
            for m in re.finditer(r"(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)", out):
                w,h,x,y=map(int,m.groups())
                if best is None: best=(x,y,w,h)
                if x<=px<x+w and y<=py<y+h: return (x,y,w,h)
            if best: return best
        except Exception: pass
        return (0,0,self.winfo_screenwidth(), self.winfo_screenheight())

    def _make_splash_bg(self, w, h):
        import numpy as np
        from PIL import Image, ImageTk
        cx, cy = w/2, h*0.30
        yy, xx = np.mgrid[0:h, 0:w]
        d = np.sqrt((xx-cx)**2 + (yy-cy)**2)
        t = np.clip(1 - d/(w*0.9), 0, 1)**1.5          # soft radial glow behind the logo
        r=(13+t*22).astype(np.uint8); g=(16+t*24).astype(np.uint8); b=(22+t*33).astype(np.uint8)
        return ImageTk.PhotoImage(Image.fromarray(np.dstack([r,g,b]), "RGB"))

    def _show_splash(self):
        try:
            W,H=480,480
            sp=ctk.CTkToplevel(self); sp.overrideredirect(True); sp.configure(fg_color=CARD)
            try: sp.wm_attributes("-type","splash")
            except Exception: pass
            mx,my,mw,mh=self._pointer_monitor()
            sp.geometry("%dx%d+%d+%d"%(W,H, mx+(mw-W)//2, my+(mh-H)//2))
            try: sp.attributes("-alpha",0.0); sp.attributes("-topmost",True)
            except Exception: pass
            # everything is drawn on ONE canvas so text/logo overlay the gradient with true transparency
            from PIL import Image, ImageTk
            cv=tk.Canvas(sp, width=W, height=H, highlightthickness=0, bd=0, bg=CARD); cv.pack(fill="both", expand=True)
            self._sp_cv=cv
            try:
                self.imgs["splashbg"]=self._make_splash_bg(W,H)
                cv.create_image(0,0, image=self.imgs["splashbg"], anchor="nw")
            except Exception as e: log_error("splashbg", e)
            cv.create_rectangle(0,0,W,3, fill=AC, outline="")                      # accent hairline
            if os.path.exists(ICON):
                try:
                    self.imgs["splash"]=ImageTk.PhotoImage(Image.open(ICON).convert("RGBA").resize((158,158), Image.LANCZOS))
                    cv.create_image(W//2, int(H*0.30), image=self.imgs["splash"], anchor="center")
                except Exception as e: log_error("splash-logo", e)
            cv.create_text(W//2, int(H*0.555), text=APP, fill=TX, font=(WORDMARK, 30, "bold"))
            cv.create_text(W//2, int(H*0.635), text="Y O I N K   Y O U R   S C A N S   O F F ,   O N   L I N U X",
                           fill=MUT, font=(WORDMARK, 8))
            px0, py, pw, ph = W//2-125, int(H*0.80), 250, 5
            cv.create_rectangle(px0, py, px0+pw, py+ph, fill="#0c0f15", outline="")
            self._sp_fill=cv.create_rectangle(px0, py, px0+1, py+ph, fill=AC, outline="")
            self._sp_px0, self._sp_pw, self._sp_py, self._sp_ph = px0, pw, py, ph
            self._sp_status=cv.create_text(W//2, int(H*0.865), text="checking your setup…", fill=MUT, font=(WORDMARK, 10))
            cv.create_text(W-22, H-20, text="v"+VERSION, fill=STROKE, font=(WORDMARK, 9), anchor="e")
            self._checklist=[
                {"pkg":"jmtpfs","fn":lambda:bool(shutil.which("jmtpfs")),"req":True,"ok":None},
                {"pkg":"rsync","fn":lambda:bool(shutil.which("rsync")),"req":True,"ok":None},
                {"pkg":"fuse","fn":lambda:bool(shutil.which("fusermount")),"req":True,"ok":None},
                {"pkg":"python3-pil.imagetk","fn":_has_imagetk,"req":True,"ok":None},
                {"pkg":"python3-trimesh","fn":_has_trimesh,"req":False,"ok":None},
                {"pkg":"xdg-utils","fn":lambda:bool(shutil.which("xdg-open")),"req":False,"ok":None},
            ]
            self._splash=sp; self._splash_a=0.0; self._missing=None
            self._splash_fade(0.12); self.after(400, lambda: self._run_checks(0))
        except Exception as e:
            log_error("splash", e); self.deiconify()
    def _splash_fade(self, d):
        sp=self._splash
        if not sp: return
        self._splash_a=max(0.0, min(1.0, self._splash_a+d))
        try: sp.attributes("-alpha", self._splash_a)
        except Exception: pass
        if 0.0 < self._splash_a < 1.0: self.after(22, lambda: self._splash_fade(d))
        elif self._splash_a<=0.0:
            try: sp.destroy()
            except Exception: pass
            self._splash=None; self.deiconify()
    def _run_checks(self, i):
        if not self._splash: return
        cv=self._sp_cv
        def setbar(frac):
            try: cv.coords(self._sp_fill, self._sp_px0, self._sp_py, self._sp_px0+int(self._sp_pw*frac), self._sp_py+self._sp_ph)
            except Exception: pass
        def setstatus(txt,col):
            try: cv.itemconfigure(self._sp_status, text=txt, fill=col)
            except Exception: pass
        if i>=len(self._checklist):
            setbar(1.0)
            req=[c for c in self._checklist if c["ok"] is False and c["req"]]
            opt=[c for c in self._checklist if c["ok"] is False and not c["req"]]
            if req:
                self._missing=req
                setstatus("missing: "+", ".join(c["pkg"] for c in req), WARN)
                self.after(1700, self._close_splash)
            else:
                setstatus("everything's here" if not opt else "ready (some optional tools missing)", OK)
                self.after(750, self._close_splash)
            return
        c=self._checklist[i]
        setstatus("checking "+c["pkg"]+" …", MUT)
        try: c["ok"]=bool(c["fn"]())
        except Exception: c["ok"]=False
        setbar((i+1)/len(self._checklist))
        self.after(300, lambda: self._run_checks(i+1))
    def _close_splash(self):
        if self._splash:
            self.deiconify()                                  # reveal the app BEHIND the still-topmost splash
            self.update_idletasks()
            self.after(140, lambda: self._splash_fade(-0.12)) # let it paint, then dissolve the splash over it
        else:
            self.deiconify()
        if getattr(self,"_missing",None):
            miss=self._missing; self._missing=None
            pkgs=" ".join(c["pkg"] for c in miss)
            self.after(500, lambda: self._alert("Missing tools",
                "Some required tools aren't installed:\n  "+", ".join(c["pkg"] for c in miss)+
                "\n\nInstall them with:\n  sudo apt install "+pkgs))
    # ---- activity (spinner + status text) in the bottom bar; device state lives in the device bar ----
    def _bottombar(self):
        left=self._barleft
        self._spin=ctk.CTkLabel(left, text="●", text_color=OK, font=ctk.CTkFont(size=13), width=18)
        self._spin.pack(side="left", padx=(22,4))
        self._status=ctk.CTkLabel(left, text="Ready", text_color=MUT, anchor="w", font=ctk.CTkFont(size=12))
        self._status.pack(side="left")
        self._status_msg=""

    def set_status(self, msg=""):
        """Set a transient bottom-bar message (pass '' to clear back to Ready/busy)."""
        self._status_msg=msg or ""

    _SPINNER=["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    def _pulse(self):
        busy = (self._mounting or self.listing or self.pulling
                or getattr(self,"_basing",False) or getattr(self,"_loader",None) is not None)
        self._pt=getattr(self,"_pt",0)+1
        try:
            if busy:
                self.dot.configure(text_color=(AC if self._pt%2 else "#2b5c8a"))
                self._spin.configure(text=self._SPINNER[self._pt % len(self._SPINNER)], text_color=AC)
                msg = self._status_msg or ("Connecting to the scanner…" if self._mounting else
                      "Reading projects off the scanner…" if self.listing else
                      "Importing…" if self.pulling else "Working…")
                self._status.configure(text=msg, text_color=TX)
            else:
                self._spin.configure(text="●", text_color=OK)
                self._status.configure(text=(self._status_msg or "Ready"),
                                       text_color=(TX if self._status_msg else MUT))
        except Exception: pass
        self.after(200, self._pulse)

    # ---- header: logo, mode tabs, settings ----
    def _header(self):
        h=ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=56); h.grid(row=0,column=0, sticky="ew"); h.grid_propagate(False)
        h.grid_columnconfigure(2, weight=1)
        if os.path.exists(ICON):
            try:
                self.imgs["logo"]=cimg(ICON,32)
                ctk.CTkLabel(h, image=self.imgs["logo"], text="").grid(row=0,column=0, padx=(18,8))
            except Exception: pass
        wm=ctk.CTkFrame(h, fg_color="transparent"); wm.grid(row=0,column=1, sticky="w")
        ctk.CTkLabel(wm, text="Point", font=ctk.CTkFont(family=WORDMARK, size=20, weight="bold"), text_color=TX).pack(side="left")
        ctk.CTkLabel(wm, text="Yoink", font=ctk.CTkFont(family=WORDMARK, size=20, weight="bold"), text_color=AC).pack(side="left")
        ctk.CTkLabel(wm, text="v"+VERSION, font=ctk.CTkFont(size=10), text_color=DIM).pack(side="left", padx=(6,0), pady=(6,0))
        self._modes=TabStrip(h, command=lambda lab: self._set_mode(HEADER_KEY.get(lab, lab)), content=False, base=CARD, size=13)
        self._modes.grid(row=0,column=2, sticky="w", padx=(26,0), pady=(8,0))
        self._modes.add("Import", icon="⬇"); self._modes.add("Projects", icon="▤"); self._modes.add("Captures", icon="▣")
        self._modes.add("Live view", tag="Planned", icon="◉")
        self.mode_sw=_ModeSwitch(self._modes)
        btns=ctk.CTkFrame(h, fg_color="transparent"); btns.grid(row=0,column=3, sticky="e", padx=(0,14)); self._hbtns=btns
        mb=ctk.CTkButton(btns, text="≡", width=36, height=30, corner_radius=6, fg_color="transparent", hover_color=CARD2, text_color=MUT,
                         font=ctk.CTkFont(size=18, weight="bold"), command=self._app_menu); mb.pack(side="left", padx=2); self._menu_btn=mb
        self._tip(mb, "Settings, how it works, help, about")
        tk.Frame(h, bg=STROKE, height=1, bd=0, highlightthickness=0).place(x=0, rely=1.0, y=-1, relwidth=1.0)

    def _app_menu(self):
        """The app menu under the ≡ button: a small dark popup that closes on click or focus loss."""
        old=getattr(self, "_menu_pop", None)
        if old is not None and old.winfo_exists(): old.destroy(); self._menu_pop=None; return
        m=ctk.CTkToplevel(self); m.overrideredirect(True); m.configure(fg_color=CARD2); self._menu_pop=m
        try: m.attributes("-topmost", True)
        except Exception: pass
        box=ctk.CTkFrame(m, fg_color=CARD2, corner_radius=10, border_width=1, border_color=STROKE); box.pack(fill="both", expand=True)
        def item(text, cmd, sep=False):
            if sep: tk.Frame(box, bg=STROKE, height=1, bd=0, highlightthickness=0).pack(fill="x", padx=8, pady=4)
            ctk.CTkButton(box, text=text, anchor="w", width=220, height=32, corner_radius=6, fg_color="transparent", hover_color=STROKE, text_color=TX,
                          font=ctk.CTkFont(size=12), command=lambda: (m.destroy(), cmd())).pack(fill="x", padx=6, pady=1)
        item("⚙  Settings…", self.dlg_settings)
        item("✦  How this works (the five steps)", self._howto_dialog, sep=True)
        item("?  Help", self.dlg_help)
        item("▤  Open error log", self.dlg_logs)
        item("i  About "+APP, self.dlg_about, sep=True)
        item("↗  PointYoink on GitHub", lambda: subprocess.Popen(["xdg-open", GITHUB]))
        self.update_idletasks()
        x=self._menu_btn.winfo_rootx()+self._menu_btn.winfo_width()-236; y=self._menu_btn.winfo_rooty()+self._menu_btn.winfo_height()+4
        m.geometry("+%d+%d" % (max(0, x), y)); m.after(50, lambda: (m.focus_force(), m.bind("<FocusOut>", lambda e: m.winfo_exists() and m.destroy())))
    # ---- device bar: scanner, state, connection controls ----
    def _statusbar(self):
        d=ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=54); d.grid(row=1,column=0, sticky="ew"); d.grid_propagate(False)
        d.grid_columnconfigure(3, weight=1)
        cv=tk.Canvas(d, width=44, height=30, bg=CARD, highlightthickness=0, bd=0); cv.grid(row=0,column=0, padx=(18,10), pady=12)
        cv.create_rectangle(3,4,41,27, outline=MUT, width=2); cv.create_rectangle(8,9,26,22, fill="#0a0c10", outline=STROKE)
        cv.create_oval(30,11,37,18, outline=MUT, width=2)
        ctk.CTkLabel(d, text="MIRACO", font=ctk.CTkFont(size=13, weight="bold"), text_color=TX).grid(row=0,column=1, padx=(0,16))
        self.dot=ctk.CTkLabel(d, text="●", text_color=WARN, font=ctk.CTkFont(size=14), width=16); self.dot.grid(row=0,column=2, padx=(0,6))
        self.banner=ctk.CTkLabel(d, text="…", text_color=TX, anchor="w", justify="left", font=ctk.CTkFont(size=12))
        self.banner.grid(row=0,column=3, sticky="ew", padx=(0,12))
        self.banner.bind("<Configure>", self._wrap_banner)
        def vsep(col): tk.Frame(d, bg=STROKE, width=1, bd=0, highlightthickness=0).grid(row=0,column=col, sticky="ns", pady=13)
        vsep(4)
        self.wifi_btn=ctk.CTkButton(d, text="\U0001F4F6  WiFi", width=96, height=32, corner_radius=8, fg_color="transparent", border_width=1,
                                    border_color=STROKE, hover_color=CARD2, text_color=TX, font=ctk.CTkFont(size=12, weight="bold"), command=self.on_wifi)
        self.wifi_btn.grid(row=0,column=5, padx=(12,4))
        self._tip(self.wifi_btn, "Receive a project over WiFi, no cable: the scanner's Share to PC > Wi-Fi sends it straight to PointYoink.")
        self.action_btn=ctk.CTkButton(d, text="\U0001F50C  USB", width=96, height=32, corner_radius=8, fg_color="transparent", border_width=1,
                                      border_color=STROKE, hover_color=CARD2, text_color=TX, font=ctk.CTkFont(size=12, weight="bold"), command=self.on_mount)
        self.action_btn.grid(row=0,column=6, padx=(4,12))
        self._tip(self.action_btn, "Connect to the scanner over the USB-C cable (it must be in File Transfer mode) and list its projects.")
        vsep(7)
        rb=ctk.CTkButton(d, text="↻  Refresh", width=96, height=32, corner_radius=8, fg_color="transparent", hover_color=CARD2,
                         text_color=TX, font=ctk.CTkFont(size=12, weight="bold"), command=self.on_refresh)
        rb.grid(row=0,column=8, padx=(12,18)); self._tip(rb, "Read the project list again (scanner or this PC).")
        tk.Frame(d, bg=STROKE, height=1, bd=0, highlightthickness=0).place(x=0, rely=1.0, y=-1, relwidth=1.0)
    def _wrap_banner(self, e):
        try:
            wl=max(160, e.width-8)
            if abs(wl-int(self.banner.cget("wraplength") or 0))>6: self.banner.configure(wraplength=wl)
        except Exception: pass
    def on_refresh(self):
        self.listed=False; self.projects_sig=None; self.gallery_cache={}
        self.set_status("Refreshing the project list…"); self.start_listing()

    # ---- body: three columns (list | preview | import options) plus the other modes ----
    def _body(self):
        body=ctk.CTkFrame(self, fg_color="transparent"); body.grid(row=2,column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1); body.grid_rowconfigure(0, weight=1)
        # top-level modes (header tabs): Import (projects), Captures, Process, Live view. Only one is shown.
        self.mode_frames={}
        for m in ("Projects","Captures","Process","Live"):
            f=ctk.CTkFrame(body, fg_color=("transparent" if m in ("Projects","Process") else CARD), corner_radius=(0 if m=="Projects" else 14))
            if m in ("Captures","Live"): f.grid(row=0,column=0, sticky="nsew", padx=16, pady=12)
            else: f.grid(row=0,column=0, sticky="nsew")
            f.grid_remove(); self.mode_frames[m]=f
        self.mode_frames["Projects"].grid()
        pm=self.mode_frames["Projects"]
        pm.grid_columnconfigure(2, weight=1); pm.grid_rowconfigure(0, weight=1)

        # -- left: On your scanner --
        left=ctk.CTkFrame(pm, fg_color="transparent", width=300); left.grid(row=0,column=0, sticky="nsew")
        left.grid_propagate(False); left.grid_rowconfigure(2, weight=1); left.grid_columnconfigure(0, weight=1)
        lh=ctk.CTkFrame(left, fg_color="transparent"); lh.grid(row=0,column=0, sticky="ew", padx=(18,10), pady=(14,8))
        self.page="import"
        self.list_title=ctk.CTkLabel(lh, text="On the scanner", font=ctk.CTkFont(size=15,weight="bold"), text_color=TX, anchor="w"); self.list_title.pack(side="left")
        b1=ctk.CTkButton(lh, text="none", width=44, height=22, corner_radius=6, fg_color="transparent", hover_color=CARD2, text_color=MUT,
                      font=ctk.CTkFont(size=10), command=self.select_none); b1.pack(side="right")
        b2=ctk.CTkButton(lh, text="all", width=36, height=22, corner_radius=6, fg_color="transparent", hover_color=CARD2, text_color=MUT,
                      font=ctk.CTkFont(size=10), command=self.select_all); b2.pack(side="right")
        self.list_selbtns=[b1, b2]
        se=ctk.CTkEntry(left, placeholder_text="⌕  Search projects…", height=34, corner_radius=8,
                        fg_color="#0d0f14", border_color=STROKE, text_color=TX, placeholder_text_color=MUT, font=ctk.CTkFont(size=12))
        se.grid(row=1,column=0, sticky="ew", padx=18, pady=(0,6))
        se.bind("<KeyRelease>", lambda e: self.search.set(se.get()))
        self.llist=ctk.CTkScrollableFrame(left, fg_color="transparent"); self.llist.grid(row=2,column=0, sticky="nsew", padx=(8,2), pady=0)
        self.llist.grid_columnconfigure(0, weight=1)
        self.list_empty=None   # the "No projects yet" panel, created by render_list; kept as tall as the list's visible area
        self.llist._parent_canvas.bind("<Configure>", lambda e: self._fit_empty("list_empty", self.llist), add="+")
        self.sel_lbl=ctk.CTkLabel(left, text="No projects selected", text_color=MUT, anchor="w", font=ctk.CTkFont(size=12))
        self.sel_lbl.grid(row=3,column=0, sticky="ew", padx=18, pady=(8,12))
        tk.Frame(pm, bg=STROKE, width=1, bd=0, highlightthickness=0).grid(row=0,column=1, sticky="ns")

        # -- centre: title, tabs (3D preview | Files), preview, scan strip --
        centre=ctk.CTkFrame(pm, fg_color="transparent"); centre.grid(row=0,column=2, sticky="nsew", padx=14)
        centre.grid_columnconfigure(0, weight=1); centre.grid_rowconfigure(1, weight=1)
        tb=ctk.CTkFrame(centre, fg_color="transparent"); tb.grid(row=0,column=0, sticky="ew", pady=(12,2)); tb.grid_columnconfigure(0, weight=1)
        self.proj_empty=ctk.CTkLabel(tb, text="Pick a project on the left", text_color=MUT, font=ctk.CTkFont(size=18, weight="bold"), anchor="w")
        self.proj_empty.grid(row=0,column=0, sticky="w", pady=(4,10))
        # projbar/detail/chips names kept: select_project drives them; detail is the plain-text fallback and stays un-gridded.
        self.projbar=ctk.CTkFrame(tb, fg_color="transparent"); self.projbar.grid(row=0,column=0, sticky="ew"); self.projbar.grid_remove()
        nr=ctk.CTkFrame(self.projbar, fg_color="transparent"); nr.pack(fill="x")
        self.hdr_name=ctk.CTkLabel(nr, text="", text_color=TX, anchor="w", justify="left", font=ctk.CTkFont(size=19, weight="bold"), wraplength=360)
        self.hdr_name.pack(side="left")
        rn=ctk.CTkButton(nr, text="✎", width=28, height=26, corner_radius=6, fg_color="transparent", hover_color=CARD2, text_color=MUT,
                         font=ctk.CTkFont(size=14), command=lambda: self.selected and self.rename_project(self.selected)); rn.pack(side="left", padx=(6,0))
        self._tip(rn, "Rename this project (the scanner's id is kept as a reference)")
        ir=ctk.CTkFrame(self.projbar, fg_color="transparent"); ir.pack(fill="x", pady=(2,6))
        self.hdr_id=ctk.CTkLabel(ir, text="", text_color=MUT, anchor="w", font=ctk.CTkFont(size=12)); self.hdr_id.pack(side="left", padx=(0,10))
        self.chips=ctk.CTkFrame(ir, fg_color="transparent"); self.chips.pack(side="left")
        self.hdr_date=ctk.CTkLabel(ir, text="", text_color=DIM, anchor="w", font=ctk.CTkFont(size=11)); self.hdr_date.pack(side="left", padx=(10,0))
        self.detail=ctk.CTkLabel(self.projbar, text="", text_color=TX, anchor="w", justify="left", font=ctk.CTkFont(size=12), wraplength=360)
        self.next_strip=ctk.CTkFrame(self.projbar, fg_color="#0f1a2b", corner_radius=12, border_width=1, border_color="#1f3a5f")   # NEXT: shown on the Projects page only
        self.tabs=TabStrip(centre, base=BG, size=13); self.tabs.grid(row=1,column=0, sticky="nsew")
        pv=self.tabs.add("3D preview"); fl=self.tabs.add("Files")
        ctl=ctk.CTkFrame(self.tabs.bar, fg_color="transparent"); ctl.pack(side="right", pady=(0,4))
        self.view_btn=ctk.CTkButton(ctl, text="⟳  View in 3D", width=98, height=30, corner_radius=8, fg_color="transparent", border_width=1,
                                    border_color=STROKE, hover_color=CARD2, text_color=TX, font=ctk.CTkFont(size=12), command=self.on_view_3d)
        self._tip(self.view_btn, "Open this scan in the interactive viewer: drag to rotate, scroll to zoom.")
        self.shade_sw=ctk.CTkSegmentedButton(ctl, values=["Solid","Wireframe"], command=self._shade_mode_changed, height=30, corner_radius=8,
                                             fg_color=CARD2, selected_color=SELB, selected_hover_color=SELB, unselected_color=CARD2, unselected_hover_color=STROKE,
                                             text_color=TX, font=ctk.CTkFont(size=11))
        self.shade_sw.pack(side="right"); self.shade_sw.set("Solid")
        # preview box: the rendered PNG (or the scanner's preview) with a hint line at the bottom
        pv.grid_columnconfigure(0, weight=1); pv.grid_rowconfigure(0, weight=1, minsize=120)
        bigwrap=ctk.CTkFrame(pv, fg_color="#0a0c10", corner_radius=12, height=120, border_width=1, border_color=STROKE)
        bigwrap.grid(row=0,column=0, sticky="nsew", pady=(10,8)); bigwrap.grid_propagate(False)
        bigwrap.grid_columnconfigure(0, weight=1); bigwrap.grid_rowconfigure(0, weight=1)
        self.big=ctk.CTkLabel(bigwrap, text="Select a project to preview its scans", fg_color="transparent", text_color=MUT)
        self.big.grid(row=0,column=0, sticky="nsew", padx=12, pady=12)
        self.big.bind("<Configure>", self._on_big_resize)
        # interactive 3D: the GPU view (glview.py, full mesh) when OpenGL works in this window, else the
        # software renderer (meshview.py). Same mouse language either way.
        self._mv_wrap=bigwrap; self.mv=self._make_mv(); self._mv_key=None; self._mv_want=None
        self.big_hint=ctk.CTkLabel(bigwrap, text="", text_color=MUT, font=ctk.CTkFont(size=11), fg_color="#0a0c10", corner_radius=6)
        self.big_hint.place(relx=0.5, rely=1.0, y=-10, anchor="s")
        # loading overlay: a spinning ring + the current step, centred on the preview while it works
        self.big_loader=ctk.CTkFrame(bigwrap, fg_color="#11151c", corner_radius=14, border_width=1, border_color=STROKE)
        self._spin_cv=tk.Canvas(self.big_loader, width=44, height=44, bg="#11151c", highlightthickness=0); self._spin_cv.pack(padx=18, pady=(16,6))
        self.big_loader_lbl=ctk.CTkLabel(self.big_loader, text="", text_color=TX, font=ctk.CTkFont(size=12)); self.big_loader_lbl.pack(padx=22, pady=(0,16))
        self._spin_job=None; self._spin_ang=0
        self.renders_lbl=ctk.CTkLabel(bigwrap, text="", text_color=MUT, font=ctk.CTkFont(size=11), fg_color="#0a0c10", corner_radius=6)
        # nothing selected: an empty state sits over the box (inset so the rounded border stays visible); select_project hides it
        self.big_empty=self._empty_state(bigwrap, "preview"); self.big_empty.grid(row=0,column=0, sticky="nsew", padx=6, pady=6)
        self.film=ctk.CTkScrollableFrame(pv, orientation="horizontal", fg_color="transparent", height=128)
        self.film.grid(row=1,column=0, sticky="ew"); self.film.grid_remove()
        self.film.bind("<Configure>", lambda e: self.after(80, self._film_fit))
        # Files tab: the project's model files (what an import copies) above the save folder on this PC
        fl.grid_columnconfigure(0, weight=1); fl.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(fl, text="Model files in this project", text_color=MUT, font=ctk.CTkFont(size=11), anchor="w").grid(row=0,column=0, sticky="ew", pady=(10,2))
        self.files_box=ctk.CTkTextbox(fl, fg_color="#0a0c10", text_color=TX, corner_radius=10, height=150, font=ctk.CTkFont(family="monospace", size=11))
        self.files_box.grid(row=1,column=0, sticky="ew")
        self._folder_tab=fl

        # -- right: Import options (always visible, scrolls) --
        tk.Frame(pm, bg=STROKE, width=1, bd=0, highlightthickness=0).grid(row=0,column=3, sticky="ns")
        self.side=ctk.CTkFrame(pm, fg_color="transparent", width=278); self.side.grid(row=0,column=4, sticky="nsew")
        self.side.grid_propagate(False); self.side.grid_columnconfigure(0, weight=1); self.side.grid_rowconfigure(0, weight=1)
        self.opts=ctk.CTkScrollableFrame(self.side, fg_color="transparent"); self.opts.grid(row=0,column=0, sticky="nsew", padx=(6,0))
        self.projpanel=ctk.CTkScrollableFrame(self.side, fg_color="transparent"); self.projpanel.grid(row=0,column=0, sticky="nsew", padx=(6,0)); self.projpanel.grid_remove()
        self.rail_btns={}; self.rail_bars={}

        # -- Process mode: the selected project's scans, each with its versions and the tools --
        self._build_process_page(self.mode_frames["Process"])

        # Captures mode: device screenshots AND screen recordings, out of the project list
        sc=self.mode_frames["Captures"]
        sc.grid_columnconfigure(0, weight=1); sc.grid_rowconfigure(1, weight=1)
        sctop=ctk.CTkFrame(sc, fg_color="transparent"); sctop.grid(row=0,column=0, sticky="ew", padx=10, pady=(10,4))
        self.shots_lbl=ctk.CTkLabel(sctop, text="Screenshots & recordings on the device", text_color=MUT,
                                    font=ctk.CTkFont(size=12)); self.shots_lbl.pack(side="left")
        ctk.CTkButton(sctop, text="⤓ Pull all", width=96, height=30, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE,
                      hover_color=CARD2, text_color=TX, command=self.pull_screenshots).pack(side="right", padx=4)
        ctk.CTkButton(sctop, text="↻ Refresh", width=96, height=30, corner_radius=8, fg_color=CARD2,
                      hover_color=STROKE, text_color=TX, command=self.refresh_screenshots).pack(side="right", padx=4)
        self.shots=ctk.CTkScrollableFrame(sc, fg_color="#0a0c10", corner_radius=10)
        self.shots.grid(row=1,column=0, sticky="nsew", padx=10, pady=(0,10))
        for c in range(4): self.shots.grid_columnconfigure(c, weight=1)
        self._shots_items=[]
        # no scanner yet: the empty state fills the visible area (the scrollable frame only grows with content)
        self.shots_empty=self._empty_state(self.shots, "captures")
        self.shots_empty.grid(row=0,column=0,columnspan=4, sticky="nsew")
        self.shots._parent_canvas.bind("<Configure>", lambda e: self._fit_empty("shots_empty", self.shots), add="+")
        # Live tab: two live sources. MIRACO streams pose + IMU over WiFi (TCP 9999, 120 Hz);
        # a tethered RANGE streams its cameras over USB (range.py).
        lv=self.mode_frames["Live"]
        lv.grid_columnconfigure(0, weight=1); lv.grid_rowconfigure(1, weight=1)
        bar=ctk.CTkFrame(lv, fg_color="transparent"); bar.grid(row=0,column=0, sticky="ew", padx=10, pady=(10,4))
        ctk.CTkLabel(bar, text="Source", text_color=MUT, font=ctk.CTkFont(size=12)).pack(side="left")
        self.live_src=ctk.CTkSegmentedButton(bar, values=["MIRACO  (WiFi)", "RANGE  (USB)"], command=self._live_src_changed, height=30, corner_radius=15,
                                             fg_color=CARD2, selected_color=AC, selected_hover_color=AC_H, unselected_color=CARD2, unselected_hover_color=STROKE,
                                             text_color=TX, font=ctk.CTkFont(size=12))
        self.live_src.pack(side="left", padx=10); self.live_src.set("MIRACO  (WiFi)")
        # -- MIRACO source --
        mf=ctk.CTkFrame(lv, fg_color="transparent"); mf.grid(row=1,column=0, sticky="nsew"); self.live_miraco=mf
        mf.grid_columnconfigure(0, weight=1); mf.grid_rowconfigure(1, weight=1)
        top=ctk.CTkFrame(mf, fg_color="transparent"); top.grid(row=0,column=0,columnspan=2, sticky="ew", padx=10, pady=(0,4))
        ctk.CTkLabel(top, text="Scanner IP", text_color=MUT, font=ctk.CTkFont(size=12)).pack(side="left")
        self.live_ip=ctk.StringVar(value=self.cfg.get("scanner_ip",""))
        ctk.CTkEntry(top, textvariable=self.live_ip, width=140, fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=10).pack(side="left", padx=8)
        fb=ctk.CTkButton(top, text="Find", width=64, height=30, corner_radius=15, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=self.live_find); fb.pack(side="left", padx=2)
        self._tip(fb, "Scan your local network for the scanner (it answers on port 9999 whenever its WiFi is on).")
        self.live_btn=ctk.CTkButton(top, text="▶ Connect", width=110, height=30, corner_radius=15, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=self.live_toggle)
        self.live_btn.pack(side="right")
        self.live_rate=ctk.CTkLabel(top, text="", text_color=MUT, font=ctk.CTkFont(size=11)); self.live_rate.pack(side="right", padx=12)
        self.live_cv=tk.Canvas(mf, bg="#0a0c10", highlightthickness=0); self.live_cv.grid(row=1,column=0, sticky="nsew", padx=(10,4), pady=(0,10))
        # backsplash + "Find the scanner" on the empty canvas; cleared when a stream starts (see _live_empty)
        self.live_find_btn=ctk.CTkButton(self.live_cv, text="Find the scanner", width=150, height=34, corner_radius=8, fg_color="transparent",
                                         border_width=1, border_color=AC, hover_color=CARD2, text_color=AC, font=ctk.CTkFont(size=13, weight="bold"),
                                         command=self.live_find)
        self._tip(self.live_find_btn, "Scan your local network for the scanner (it answers on port 9999 whenever its WiFi is on).")
        self.live_cv.bind("<Configure>", lambda e: self._live_empty())
        side=ctk.CTkFrame(mf, fg_color=CARD2, corner_radius=10, width=200); side.grid(row=1,column=1, sticky="ns", padx=(4,10), pady=(0,10)); side.grid_propagate(False)
        self.live_txt=ctk.CTkLabel(side, text="not connected\n\nHit Find, then Connect.", text_color=MUT, justify="left", anchor="nw", font=ctk.CTkFont(family="monospace", size=11))
        self.live_txt.pack(fill="both", expand=True, padx=12, pady=12)
        self._live_on=False; self._live_last=None; self._live_n=0; self._live_t=time.time(); self._live_trail=[]
        # -- RANGE source --
        rf=ctk.CTkFrame(lv, fg_color="transparent"); rf.grid(row=1,column=0, sticky="nsew"); rf.grid_remove(); self.live_range=rf
        rf.grid_columnconfigure(0, weight=1); rf.grid_rowconfigure(1, weight=1)
        rtop=ctk.CTkFrame(rf, fg_color="transparent"); rtop.grid(row=0,column=0, sticky="ew", padx=10, pady=(0,4))
        self.range_status=ctk.CTkLabel(rtop, text="Not connected - plug the RANGE into a direct USB port (not a hub), then Connect",
                                       text_color=MUT, font=ctk.CTkFont(size=12), anchor="w"); self.range_status.pack(side="left", fill="x", expand=True)
        self.range_btn=ctk.CTkButton(rtop, text="▶ Connect", width=120, height=30, corner_radius=15, fg_color=AC, hover_color=AC_H,
                                     text_color="#04121f", command=self.range_toggle); self.range_btn.pack(side="right")
        self.range_cap=ctk.CTkButton(rtop, text="⬇ Capture", width=100, height=30, corner_radius=15, fg_color=CARD2, hover_color=STROKE,
                                     text_color=TX, command=self.range_capture); self.range_cap.pack(side="right", padx=6)
        self._tip(self.range_cap, "Grab the current depth frame as a point cloud (.ply) plus a color snapshot into your save folder, ready for View in 3D and export.")
        self.range_view=ctk.CTkSegmentedButton(rtop, values=["All", "Depth", "IR L", "IR R", "Color", "Combined"], command=lambda v: self._range_layout(), height=30, corner_radius=15,
                                               fg_color=CARD2, selected_color=STROKE, selected_hover_color=STROKE, unselected_color=CARD2, unselected_hover_color=STROKE,
                                               text_color=TX, font=ctk.CTkFont(size=12))
        self.range_view.pack(side="right", padx=6); self.range_view.set("All")   # (segmented buttons can't take a tooltip)
        self.range_rot=int(self.cfg.get("range_rot", 90))   # the sensors are mounted sideways; 90 makes the view upright
        rb=ctk.CTkButton(rtop, text="↻ %d°" % self.range_rot, width=64, height=30, corner_radius=15, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=self._range_rotate)
        rb.pack(side="right", padx=2); self.range_rot_btn=rb
        self._tip(rb, "Rotate the live views (and captured clouds) in 90° steps to match how you're holding the scanner.")
        grid=ctk.CTkFrame(rf, fg_color="transparent"); grid.grid(row=1,column=0, sticky="nsew", padx=10, pady=(0,4)); self.range_grid=grid
        for c in (0,1): grid.grid_columnconfigure(c, weight=1, uniform="rg")
        for r in (0,1): grid.grid_rowconfigure(r, weight=1, uniform="rg")
        self.range_tiles={}
        for i,(key,cap) in enumerate((("Depth","Depth"),("IR L","IR left"),("IR R","IR right"),("Color","Color"))):
            cell=ctk.CTkFrame(grid, fg_color="#0a0c10", corner_radius=12); cell.grid(row=i//2, column=i%2, sticky="nsew", padx=3, pady=3)
            cell.grid_propagate(False); cell.grid_columnconfigure(0, weight=1); cell.grid_rowconfigure(1, weight=1)
            ctk.CTkLabel(cell, text=cap, text_color=MUT, font=ctk.CTkFont(size=11), anchor="w").grid(row=0,column=0, sticky="ew", padx=10, pady=(6,0))
            lab=ctk.CTkLabel(cell, text=""); lab.grid(row=1,column=0, sticky="nsew"); self.range_tiles[key]=lab
        self.range_single=ctk.CTkLabel(rf, text="", fg_color="#0a0c10", corner_radius=12); self.range_single.grid(row=1,column=0, sticky="nsew", padx=10, pady=(0,4)); self.range_single.grid_remove()
        self.range_info=ctk.CTkLabel(rf, text="The RANGE draws 5V/1A: hub ports (500 mA) make it reset when the projector fires. It also reboots itself whenever the stream stops (that's normal).",
                                     text_color=MUT, font=ctk.CTkFont(size=11), anchor="w"); self.range_info.grid(row=2,column=0, sticky="ew", padx=14, pady=(0,10))
        self._range=None; self._range_stream=None; self._range_color=None; self._range_intr=None; self._range_on=False; self._range_busy=False

    # ---- import options (right column) + the save folder browser (Files tab) ----
    def _opt(self, parent, kind, title, sub, var, value=None, command=None, tip=None):
        """One option row: a radio or checkbox with a bold title and a muted one-line hint under it."""
        row=ctk.CTkFrame(parent, fg_color="transparent"); row.pack(fill="x", padx=6, pady=(6,2))
        f=ctk.CTkFont(size=13, weight="bold")
        if kind=="radio":
            w=ctk.CTkRadioButton(row, text=title, variable=var, value=value, fg_color=AC, hover_color=AC_H, border_color=DIM, text_color=TX,
                                 font=f, radiobutton_width=20, radiobutton_height=20, border_width_unchecked=2, border_width_checked=6, command=command)
        else:
            w=ctk.CTkCheckBox(row, text=title, variable=var, onvalue=True, offvalue=False, fg_color=AC, hover_color=AC_H, border_color=DIM,
                              text_color=TX, font=f, checkbox_width=20, checkbox_height=20, corner_radius=5, command=command)
        w.pack(anchor="w")
        if sub: ctk.CTkLabel(row, text=sub, text_color=MUT, font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w", padx=(30,0))
        if tip: self._tip(w, tip)
        return w
    # ---- empty states ----
    def _empty_state(self, parent, kind):
        """The empty-state panel for one area (captures / projects / preview), with its buttons wired to the
        real handlers. Grid it with sticky='nsew'; it centres its content and re-centres on resize."""
        btns={"captures": [("Connect over USB", self.on_mount), ("Share over WiFi", self.on_wifi)],
              "projects": [("Connect over USB", self.on_mount), ("Share over WiFi", self.on_wifi)],
              "preview":  []}[kind]
        es=EmptyState(parent, kind, btns, scale=self._ui_scale)
        tips={"captures": "Plug in the USB-C cable and tap File Transfer on the scanner first.",
              "projects": "USB lists every project on the scanner (it must be in File Transfer mode)."}
        if es.buttons and kind in tips: self._tip(es.buttons[0], tips[kind])
        if len(es.buttons)>1: self._tip(es.buttons[1], "No cable: the scanner's Share to PC > Wi-Fi sends one project straight here.")
        return es
    def _fit_empty(self, attr, sf):
        """Keep the empty-state frame stored as self.<attr> as tall as the scrollable frame's visible area
        (a scrollable frame only grows with its content, so the panel would otherwise sit in a strip)."""
        w=getattr(self, attr, None)
        try:
            if w is None or not w.winfo_exists() or not w.winfo_manager(): return
            h=sf._parent_canvas.winfo_height()
            if h>1 and abs(h-w.winfo_height())>2: w.configure(height=h)
        except Exception: pass
    def _live_empty(self):
        """Backsplash on the Live canvas until a stream arrives (the trail drawing takes over from there)."""
        try:
            if not self._live_on and self._live_last is None: draw_empty_state(self.live_cv, "live", [self.live_find_btn], self._ui_scale)
            else: self.live_cv.delete("empty")
        except Exception as e: log_error("live-empty", e)

    def _hr(self, parent, pady=(12,6)):
        tk.Frame(parent, bg=STROKE, height=1, bd=0, highlightthickness=0).pack(fill="x", padx=6, pady=pady)
    def _title(self, parent, text, size=13, pady=(0,4)):
        ctk.CTkLabel(parent, text=text, text_color=TX, font=ctk.CTkFont(size=size, weight="bold"), anchor="w").pack(fill="x", padx=6, pady=pady)
    def _build_options(self):
        self.models_only=ctk.BooleanVar(value=self.cfg.get("models_only",True))
        self.auto_open=ctk.BooleanVar(value=self.cfg.get("auto_open",True))
        self.cleanup=ctk.BooleanVar(value=self.cfg.get("cleanup",False))
        self.fuse_voxel=ctk.DoubleVar(value=float(self.cfg.get("fuse_voxel",0.4)))
        self._ensure_clean_vars()
        self.exp_stl=ctk.BooleanVar(value=self.cfg.get("exp_stl",False))
        self.exp_obj=ctk.BooleanVar(value=self.cfg.get("exp_obj",False))
        self.exp_glb=ctk.BooleanVar(value=self.cfg.get("exp_glb",False))
        self.dest=ctk.StringVar(value=self.cfg.get("dest",DEFAULT_DEST))
        op=self.opts
        self._title(op, "Import options", size=15, pady=(14,6))
        self._opt(op, "radio", "Finished models", "Skip raw frames", self.models_only, True, command=self.update_summary,
                  tip="Copies only the finished meshes and point clouds (.ply) and skips the thousands of raw depth frames. Much faster and smaller.")
        self._opt(op, "radio", "Full project", "Includes raw capture data", self.models_only, False, command=self.update_summary,
                  tip="Copies everything, raw depth frames included, so the scan can be re-processed later. Slow over USB.")
        self._hr(op)
        self._title(op, "Also export as")
        self._opt(op, "check", "STL", None, self.exp_stl, tip="For 3D printing")
        self._opt(op, "check", "OBJ", None, self.exp_obj, tip="For editing")
        self._opt(op, "check", "GLB", None, self.exp_glb, tip="For the web and editing")
        ctk.CTkLabel(op, text="Original PLY files are kept", text_color=MUT, font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", padx=6, pady=(4,0))
        self._hr(op)
        # editing is an action with a result, not an import option: it lives on the Process page
        ctk.CTkLabel(op, text="After importing", text_color=TX, font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(fill="x", padx=16, pady=(4,2))
        eb=ctk.CTkButton(op, text="▤  Open the Projects page…", height=32, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE,
                         hover_color=CARD2, text_color=TX, anchor="w", command=lambda: self._set_mode("Local"))
        eb.pack(fill="x", padx=14, pady=(0,4))
        self._tip(eb, "Everything on this PC lives on the Projects page: build 3D models from raw data, line up scans, prepare, export.")
        self._hr(op)
        self._title(op, "Destination")
        dr=ctk.CTkFrame(op, fg_color="transparent"); dr.pack(fill="x", padx=6, pady=(2,0)); dr.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(dr, textvariable=self.dest, fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=8, height=36).grid(row=0,column=0, sticky="ew")
        bb=ctk.CTkButton(dr, text="\U0001F4C1", width=40, height=36, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE,
                         hover_color=CARD2, text_color=TX, command=self.browse); bb.grid(row=0,column=1, padx=(6,0)); self._tip(bb, "Choose the save folder")
        self._opt(op, "check", "Open folder when done", None, self.auto_open)
        # save folder browser: in the Files tab under the project's file list
        fp=self._folder_tab; self.sections={"import":op, "folder":fp, "project":self.projbar, "edit":self.mode_frames["Process"]}
        from tkinter import ttk
        st=ttk.Style(self); st.theme_use("clam")
        st.configure("PY.Treeview", background="#0a0c10", fieldbackground="#0a0c10", foreground=TX, borderwidth=0, relief="flat", rowheight=24, font=("TkDefaultFont", 10))
        st.layout("PY.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("PY.Treeview.Heading", background=CARD2, foreground=MUT, borderwidth=0, font=("TkDefaultFont", 9, "bold"))
        st.map("PY.Treeview", background=[("selected", SELB)], foreground=[("selected", TX)])
        fh=ctk.CTkFrame(fp, fg_color="transparent"); fh.grid(row=2,column=0, sticky="ew", pady=(12,4))
        ctk.CTkLabel(fh, text="Save folder on this PC", text_color=MUT, font=ctk.CTkFont(size=11), anchor="w").pack(side="left")
        self.folder_lbl=ctk.CTkLabel(fh, text="", text_color=DIM, anchor="w", font=ctk.CTkFont(size=11)); self.folder_lbl.pack(side="left", fill="x", expand=True, padx=10)
        ob=ctk.CTkButton(fh, text="open", width=50, height=24, corner_radius=6, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2,
                         text_color=TX, font=ctk.CTkFont(size=10), command=self.open_folder); ob.pack(side="right", padx=(4,0))
        self._tip(ob, "Open the save folder in your file manager")
        ctk.CTkButton(fh, text="↻", width=28, height=24, corner_radius=6, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2,
                      text_color=TX, command=self.refresh_folder).pack(side="right")
        tw=ctk.CTkFrame(fp, fg_color="#0a0c10", corner_radius=10); tw.grid(row=3,column=0, sticky="nsew", pady=(0,10))
        tw.grid_columnconfigure(0, weight=1); tw.grid_rowconfigure(0, weight=1); self._tree_wrap=tw
        self.ftree=ttk.Treeview(tw, style="PY.Treeview", columns=("size",), height=6, selectmode="browse")
        self.ftree.heading("#0", text="name", anchor="w"); self.ftree.heading("size", text="size", anchor="e")
        self.ftree.column("#0", width=190, stretch=True); self.ftree.column("size", width=70, anchor="e", stretch=False)
        self.ftree.grid(row=0,column=0, sticky="nsew", padx=4, pady=4)
        self.ftree.bind("<<TreeviewOpen>>", self._folder_expand); self.ftree.bind("<Double-1>", self._folder_open)
        self.ftree.tag_configure("dir", foreground=AC); self.ftree.tag_configure("mesh", foreground=OK)
        tw.bind("<Configure>", self._fit_folder)
        self.side_mode=None; self._folder_loaded=False

    def set_side(self, key, open_only=False):
        """Kept for callers (dev/render.py, older paths). The side panel is now the always-visible Import options
        column: 'folder' opens the Files tab, 'edit' the Process tab, anything else is a no-op."""
        if key=="folder":
            self._set_mode("Projects"); self.tabs.set("Files"); self._fit_folder(); self.refresh_folder()
        elif key=="edit": self._set_mode("Local")
        elif key in ("project","import"): self._set_mode("Projects")
        self.side_mode=key if key in self.sections else None; self.cfg["side"]=self.side_mode or "none"
    def _set_mode(self, m):
        """Import and Projects share one page (list | preview | right column); the page just changes what it shows:
        Import = what is on the scanner with the import options, Projects = what is on this PC with the project panel."""
        if m not in self.mode_frames and m!="Local": m=MODE_KEY.get(m, m)      # a label was passed
        if m=="Local": self.page="projects"; target=self.mode_frames["Projects"]
        elif m=="Projects": self.page="import"; target=self.mode_frames["Projects"]
        elif m in self.mode_frames: target=self.mode_frames[m]
        else: return
        for k,f in self.mode_frames.items():
            if f is target: f.grid()
            else: f.grid_remove()
        self._modes.set("Projects" if m in ("Local","Process") else MODE_LABEL.get(m, m))
        if m in ("Projects","Local"): self._apply_page()
        if m=="Projects" and self.tabs.get()=="Files" and not self._folder_loaded: self._folder_loaded=True; self.refresh_folder()
    def _page_filter(self, projs):
        if self.page=="projects": return [p for p in projs if p.get("local") or self.is_imported(p["name"])]
        return [p for p in projs if not p.get("local")]
    def _apply_page(self):
        imp=(self.page=="import")
        self.list_title.configure(text="On the scanner" if imp else "On this PC")
        for b in self.list_selbtns:
            if imp: b.pack(side="right")
            else: b.pack_forget()
        if imp: self.projpanel.grid_remove(); self.opts.grid(); self.next_strip.pack_forget()
        else: self.opts.grid_remove(); self.projpanel.grid()
        self._bottom_refresh()
        self.projects_sig=None; self.render_list(getattr(self, "all_projects", self.projects))
        if self.selected and self.selected not in {p["name"] for p in self.projects}: self._clear_selection()
        if not imp:
            self._panel_refresh()
            if not self.cfg.get("seen_howto") and self.projects and os.environ.get("POINTYOINK_NO_HOWTO")!="1": self.after(900, self._howto_dialog)
    def _clear_selection(self):
        """Nothing selected on this page: the centre goes back to its empty state."""
        self.selected=None; self._film_sel=None; self._film_cells={}
        try:
            self.next_strip.pack_forget(); self.projbar.grid_remove(); self.film.grid_remove(); self.proj_empty.grid()
            self._mv_key=None; self.mv.grid_remove(); self.big.grid(); self.big_empty.grid(); self.big_empty.lift()
        except Exception as e: log_error("clear selection", e)
    def _bottom_refresh(self):
        if getattr(self, "pulling", False): return
        if self.page=="import": self.import_btn.grid(row=0,column=3, padx=(6,20), pady=(12,4))
        else: self.import_btn.grid_remove()
    def _fit_folder(self, _=None):
        """Tree rows from the space it has, so the folder view fills the Files tab."""
        try:
            rows=max(4, min(40, (self._tree_wrap.winfo_height()-12)//24))
            if rows!=int(self.ftree.cget("height")): self.ftree.configure(height=rows)
        except Exception: pass
    def refresh_folder(self):
        root=self.dest.get() or DEFAULT_DEST
        self.ftree.delete(*self.ftree.get_children()); self._ftree_paths={}
        self._folder_fill("", root)
        try:
            n=sum(len(fs) for _,_,fs in os.walk(root)); total=sum(os.path.getsize(os.path.join(r,f)) for r,_,fs in os.walk(root) for f in fs)
            self.folder_lbl.configure(text="%d files · %s" % (n, human(total)))
        except Exception: self.folder_lbl.configure(text="")
    def _folder_fill(self, parent, path):
        try: names=sorted(os.listdir(path), key=lambda x: (not os.path.isdir(os.path.join(path,x)), x.lower()))
        except Exception: return
        for name in names:
            if name.startswith("."): continue
            full=os.path.join(path, name)
            try: mt=time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(full)))
            except Exception: mt=""
            if os.path.isdir(full):
                node=self.ftree.insert(parent, "end", text="  "+name, values=("",), tags=("dir",), open=False)
                self.ftree.insert(node, "end", text="…")          # placeholder; filled on expand
                self.ftree.set(node, "size", ""); self._ftree_paths=getattr(self, "_ftree_paths", {}); self._ftree_paths[node]=full
            else:
                try: sz=human(os.path.getsize(full))
                except Exception: sz=""
                tag=("mesh",) if name.lower().endswith((".ply",".stl",".obj",".glb")) else ()
                node=self.ftree.insert(parent, "end", text="  "+name, values=(sz,), tags=tag)
                self._ftree_paths=getattr(self, "_ftree_paths", {}); self._ftree_paths[node]=full
    def _folder_expand(self, e=None):
        node=self.ftree.focus(); kids=self.ftree.get_children(node)
        if len(kids)==1 and self.ftree.item(kids[0], "text")=="…":
            self.ftree.delete(kids[0]); self._folder_fill(node, self._ftree_paths.get(node, ""))
    def _folder_open(self, e=None):
        node=self.ftree.focus(); path=getattr(self, "_ftree_paths", {}).get(node)
        if path and os.path.isfile(path): subprocess.Popen(["xdg-open", path])

    # ---- bottom bar: selection summary + activity on the left, actions on the right ----
    def _actions(self):
        a=ctk.CTkFrame(self, fg_color=CARD, corner_radius=0); a.grid(row=3,column=0, sticky="ew")
        tk.Frame(a, bg=STROKE, height=1, bd=0, highlightthickness=0).place(x=0, y=0, relwidth=1.0)
        a.grid_columnconfigure(0, weight=1)
        a.grid_rowconfigure(1, minsize=6); a.grid_rowconfigure(2, minsize=12)   # reserve progress space so the window never jumps
        self._barleft=ctk.CTkFrame(a, fg_color="transparent"); self._barleft.grid(row=0,column=0, sticky="w", padx=(20,0), pady=(12,4))
        self.summary=ctk.CTkLabel(self._barleft, text="No projects selected", text_color=TX, anchor="w", font=ctk.CTkFont(size=13))
        self.summary.pack(side="left")
        self.progress=ctk.CTkProgressBar(a, height=6, corner_radius=3, progress_color=AC); self.progress.set(0)
        self.progline=ctk.CTkLabel(a, text="", text_color=MUT, anchor="w", font=ctk.CTkFont(size=11))
        def outlined(text, cmd, w=130):
            return ctk.CTkButton(a, text=text, width=w, height=40, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE,
                                 hover_color=CARD2, text_color=TX, font=ctk.CTkFont(size=13), command=cmd)
        self.open_btn=outlined("\U0001F4C2  Open folder", self.open_folder); self.open_btn.grid(row=0,column=1, padx=6, pady=(12,4))
        self.zip_btn=outlined("\U0001F5DC  Export ZIP", self.on_export_zip); self.zip_btn.grid(row=0,column=2, padx=6, pady=(12,4))
        self.cancel_btn=ctk.CTkButton(a, text="Cancel", width=150, height=40, corner_radius=8,
                                      fg_color="#3a2530", hover_color=DANGER, text_color=TX, command=self.on_cancel)
        self.import_btn=SplitButton(a, "Import selected", self.on_pull,
                                    [("Import selected", self.on_pull), ("Export ZIP of selected", self.on_export_zip),
                                     ("Select all projects", self.select_all), ("Open save folder", self.open_folder)])
        self.import_btn.grid(row=0,column=3, padx=(6,20), pady=(12,4))

    # ---- dialogs ----
    def _top(self, title, w=560, h=440, key=None):
        key=key or title
        if not hasattr(self,"_dialogs"): self._dialogs={}
        ex=self._dialogs.get(key)
        if ex is not None:
            try:
                if ex.winfo_exists():
                    ex.deiconify(); ex.lift(); ex.focus_force(); return None
            except Exception: pass
        t=ctk.CTkToplevel(self); t.title(title); t.geometry("%dx%d"%(w,h)); t.configure(fg_color=BG)
        t.transient(self); t.after(60, t.lift)
        self._dialogs[key]=t
        t.protocol("WM_DELETE_WINDOW", lambda: (self._dialogs.pop(key,None), t.destroy()))
        return t

    def _tip(self, widget, text):
        """Lightweight hover tooltip for a widget."""
        st={"win":None, "job":None}
        def show(_=None):
            if st["win"] or not text: return
            try:
                # build hidden, position, then show: mapping first flashes a black sliver at 0,0
                tw=tk.Toplevel(widget); tw.withdraw(); tw.wm_overrideredirect(True); tw.configure(bg="#0b0e13")
                try: tw.wm_attributes("-type","tooltip"); tw.attributes("-topmost",True)
                except Exception: pass
                f=ctk.CTkFrame(tw, fg_color="#0b0e13", corner_radius=8, border_width=1, border_color=STROKE)
                f.pack()
                ctk.CTkLabel(f, text=text, text_color=TX, font=ctk.CTkFont(size=11), justify="left",
                             wraplength=300).pack(padx=10, pady=7)
                tw.update_idletasks()
                x=widget.winfo_rootx()+14
                y=widget.winfo_rooty()-tw.winfo_reqheight()-8          # above the widget
                if y < 0: y=widget.winfo_rooty()+widget.winfo_height()+6   # fall back below if no room
                tw.wm_geometry("+%d+%d"%(x,y)); tw.deiconify()
                st["win"]=tw
            except Exception: pass
        def arm(_=None):
            cancel(); st["job"]=widget.after(400, show)       # only after the pointer rests on it
        def cancel():
            if st["job"]:
                try: widget.after_cancel(st["job"])
                except Exception: pass
                st["job"]=None
        def hide(_=None):
            cancel()
            if st["win"]:
                try: st["win"].destroy()
                except Exception: pass
                st["win"]=None
        widget.bind("<Enter>", arm); widget.bind("<Leave>", hide); widget.bind("<ButtonPress>", hide)

    def _modal(self, title, message, buttons):
        """Dark-themed modal. buttons: list of (label, value, accent). Returns chosen value."""
        dlg=ctk.CTkToplevel(self); dlg.title(title); dlg.configure(fg_color=BG); dlg.resizable(False,False)
        try: dlg.transient(self)
        except Exception: pass
        w,h=440,200
        try:
            self.update_idletasks()
            if self.winfo_width()>100:
                x=self.winfo_rootx()+(self.winfo_width()-w)//2; y=self.winfo_rooty()+(self.winfo_height()-h)//3
            else:
                x=(self.winfo_screenwidth()-w)//2; y=(self.winfo_screenheight()-h)//2
            dlg.geometry("%dx%d+%d+%d"%(w,h,x,y))
        except Exception: pass
        res={"v":None}
        card=ctk.CTkFrame(dlg, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(family=WORDMARK, size=15, weight="bold"), text_color=TX).pack(anchor="w", padx=18, pady=(16,4))
        ctk.CTkLabel(card, text=message, font=ctk.CTkFont(size=12), text_color=MUT, justify="left", wraplength=w-64).pack(anchor="w", padx=18, pady=(0,8))
        row=ctk.CTkFrame(card, fg_color="transparent"); row.pack(side="bottom", fill="x", padx=14, pady=(0,14))
        def choose(v): res["v"]=v; dlg.destroy()
        for label,val,accent in buttons:
            ctk.CTkButton(row, text=label, width=96, height=34, corner_radius=17,
                          fg_color=(AC if accent else CARD2), hover_color=(AC_H if accent else STROKE),
                          text_color=("#04121f" if accent else TX), command=lambda v=val: choose(v)).pack(side="right", padx=6)
        try:
            dlg.grab_set(); dlg.wait_window()
        except Exception: pass
        return res["v"]
    def _confirm(self, title, message):
        return self._modal(title, message, [("Yes",True,True),("No",False,False)]) is True
    def _alert(self, title, message):
        self._modal(title, message, [("OK",True,True)])
    def dlg_text(self, title, body):
        t=self._top(title)
        if t is None: return
        box=ctk.CTkTextbox(t, fg_color=CARD, text_color=TX, corner_radius=12, wrap="word"); box.pack(fill="both", expand=True, padx=16, pady=16)
        box.insert("1.0", body); box.configure(state="disabled")
    def dlg_help(self):
        t=self._top("How to use "+APP, 780, 700)
        if t is None: return
        head=ctk.CTkFrame(t, fg_color="transparent"); head.pack(fill="x", padx=22, pady=(18,6))
        ctk.CTkButton(head, text="The five steps…", width=130, height=30, corner_radius=15, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=self._howto_dialog).pack(side="right")
        if os.path.exists(ICON):
            try: self.imgs["helpico"]=cimg(ICON,46); ctk.CTkLabel(head, image=self.imgs["helpico"], text="").pack(side="left", padx=(0,12))
            except Exception: pass
        hc=ctk.CTkFrame(head, fg_color="transparent"); hc.pack(side="left", anchor="w")
        ctk.CTkLabel(hc, text="How to use PointYoink", font=ctk.CTkFont(size=19,weight="bold"), text_color=TX).pack(anchor="w")
        ctk.CTkLabel(hc, text="Scans off the MIRACO, onto Linux, into a model you can use", text_color=MUT, font=ctk.CTkFont(size=12)).pack(anchor="w")
        sc=ctk.CTkScrollableFrame(t, fg_color="transparent"); sc.pack(fill="both", expand=True, padx=16, pady=6)
        def card(title, rows, accent=AC):
            f=ctk.CTkFrame(sc, fg_color=CARD, corner_radius=14); f.pack(fill="x", padx=6, pady=7)
            ctk.CTkLabel(f, text=title, font=ctk.CTkFont(size=14,weight="bold"), text_color=accent).pack(anchor="w", padx=16, pady=(12,6))
            for r in rows:
                ctk.CTkLabel(f, text=r, font=ctk.CTkFont(size=12), text_color=TX, justify="left",
                             anchor="w", wraplength=690).pack(anchor="w", padx=16, pady=1)
            ctk.CTkFrame(f, fg_color="transparent", height=6).pack()
        card("Two pages", [
            "Import  -  the scanner. What is on it, the import options, one Import button.",
            "Projects  -  this PC. Everything you imported, the 3D view, and a NEXT bar that says what to do now.",
        ])
        card("Getting a project in", [
            "USB:  plug in a USB-C data cable, tap File Transfer on the scanner, and every project is listed. Tick, Import.",
            "WiFi:  click WiFi here, a 4-digit code shows; on the scanner choose Share to PC > Wi-Fi and type it. One project arrives, faster than the cable.",
            "Finished models  is quick.  Full project  also brings the raw frames, which Build and Combine need.",
        ])
        # the scanner's own screens for the two ways in
        shots=[("scanner-share-icon", "Share: the icon top right of a project"), ("scanner-wifi-code", "Wi-Fi: type the code PointYoink shows"), ("scanner-usb-tab", "USB: File Transfer")]
        adir=os.path.join(HERE, "assets", "device")
        if all(os.path.exists(os.path.join(adir, n+".png")) for n,_ in shots):
            f=ctk.CTkFrame(sc, fg_color=CARD, corner_radius=14); f.pack(fill="x", padx=6, pady=7)
            ctk.CTkLabel(f, text="On the scanner", font=ctk.CTkFont(size=14,weight="bold"), text_color=AC).pack(anchor="w", padx=16, pady=(12,6))
            row=ctk.CTkFrame(f, fg_color="transparent"); row.pack(fill="x", padx=10, pady=(0,10))
            for n,capt in shots:
                cell=ctk.CTkFrame(row, fg_color="#0a0c10", corner_radius=10); cell.pack(side="left", padx=6, pady=2, expand=True, fill="x")
                try:
                    self.imgs["help_"+n]=cimg(os.path.join(adir, n+".png"), 210); ctk.CTkLabel(cell, image=self.imgs["help_"+n], text="").pack(padx=6, pady=(6,2))
                except Exception: pass
                ctk.CTkLabel(cell, text=capt, text_color=MUT, font=ctk.CTkFont(size=10), wraplength=200).pack(pady=(0,6))
        card("The five steps (the NEXT bar walks you through them)", [
            "1  Build  -  raw frames become a 3D model. One-tap Edit on the scanner does it too; Build here when that did not turn out right.",
            "2  Cut base  -  drag one line above the table on each scan. The cut is remembered and applied when combining.",
            "3  Combine  -  scanned each side separately? Click matching spots on two scans at a time, Keep, then build one model from all the frames.",
            "4  Prepare  -  remove floating pieces, smooth, fill holes, reduce triangles. Before and after, Keep or Discard.",
            "5  Export  -  version, format and folder together, with the model's size and a mesh check.",
            "Originals are never changed: every step saves a new version, and you pick which one counts.",
        ], accent=OK)
        card("What you get", [
            "<project>_<scan>.ply  or  data/<scan>/fuse_mesh.ply   -   the scanner's finished model.",
            "<project>_<scan>_pcfused.ply   -   a model built on this PC.       <project>_<scan>_clean.ply   -   the prepared or base-cut version.",
            "<project>_combined_pcfused.ply   -   one model from all the scans you lined up.",
            "All standard .ply for Blender, MeshLab or CloudCompare; STL, OBJ and GLB from Export.",
        ])
        card("Trouble?", [
            "Nothing detected over USB:  tap File Transfer on the scanner, and try another USB-C cable, some only charge.",
            "WiFi says transfer failed:  the PC and the scanner must be on the same network, and port 9706 must be open.",
            "Build needs Open3D:  pip3 install --user --break-system-packages open3d   (a graphics card makes it seconds per scan).",
            "Point picking is greyed:  the graphics-card 3D view is off (Settings > 3D view). Auto still works.",
            "Anything else:  open the error log below and file an issue.",
        ], accent=WARN)
        row=ctk.CTkFrame(t, fg_color="transparent"); row.pack(fill="x", padx=22, pady=(2,16))
        ctk.CTkButton(row, text="Open error log", corner_radius=16, fg_color=CARD2, hover_color=STROKE,
                      text_color=TX, command=self.dlg_logs).pack(side="left")
        ctk.CTkButton(row, text="GitHub ↗", corner_radius=16, fg_color=AC, hover_color=AC_H,
                      text_color="#04121f", command=lambda: subprocess.Popen(["xdg-open",GITHUB])).pack(side="right")

    def dlg_about(self):
        t=self._top("About "+APP, 640, 680)
        if t is None: return
        if os.path.exists(ICON):
            try: self.imgs["about"]=cimg(ICON,88); ctk.CTkLabel(t, image=self.imgs["about"], text="").pack(pady=(22,6))
            except Exception: pass
        ctk.CTkLabel(t, text=APP+"  "+VERSION, font=ctk.CTkFont(family=WORDMARK, size=22,weight="bold"), text_color=TX).pack()
        ctk.CTkLabel(t, text="Yoink your 3D scans off a Revopoint scanner - on Linux, over USB.",
                     text_color=MUT, font=ctk.CTkFont(size=13)).pack(pady=(4,0))
        info=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); info.pack(fill="x", padx=24, pady=(14,8))
        info.grid_columnconfigure(1, weight=1)
        rows=[("Works with", "Revopoint MIRACO  ·  MIRACO Pro\nany Revopoint scanner with USB “File Transfer” (MTP)"),
              ("Needs", "Linux  ·  jmtpfs  ·  rsync"),
              ("Output", "standard .ply meshes & point clouds\nopen in Blender, MeshLab, or CloudCompare")]
        for i,(k,v) in enumerate(rows):
            ctk.CTkLabel(info, text=k, text_color=AC, font=ctk.CTkFont(size=11,weight="bold"),
                         anchor="ne", width=92).grid(row=i,column=0, sticky="ne", padx=(16,12), pady=(14 if i==0 else 4, 4 if i<2 else 14))
            ctk.CTkLabel(info, text=v, text_color=TX, font=ctk.CTkFont(size=12), justify="left",
                         anchor="w").grid(row=i,column=1, sticky="w", pady=(14 if i==0 else 4, 4 if i<2 else 14))
        ctk.CTkLabel(t, text="MIT licensed · free and open source", text_color=OK, font=ctk.CTkFont(size=12,weight="bold")).pack()
        ctk.CTkLabel(t, text="Unofficial. Not affiliated with or endorsed by Revopoint.\n"
                     "“Revopoint” and “MIRACO” are trademarks of their owners.",
                     text_color=MUT, font=ctk.CTkFont(size=11), justify="center").pack(pady=(6,0))
        ctk.CTkButton(t, text="GitHub  ↗", corner_radius=18, fg_color=AC, hover_color=AC_H, text_color="#04121f",
                      command=lambda: subprocess.Popen(["xdg-open",GITHUB])).pack(pady=12)
        ctk.CTkLabel(t, text="Changelog", text_color=MUT, font=ctk.CTkFont(size=12,weight="bold"), anchor="w").pack(fill="x", padx=24)
        box=ctk.CTkTextbox(t, fg_color=CARD, text_color=TX, corner_radius=12, wrap="word", height=150)
        box.pack(fill="both", expand=True, padx=24, pady=(4,20)); box.insert("1.0", CHANGELOG); box.configure(state="disabled")
    def dlg_settings(self):
        t=self._top("Settings", 560, 610)
        if t is None: return
        ctk.CTkLabel(t, text="Default save folder", text_color=TX, anchor="w").pack(fill="x", padx=20, pady=(20,4))
        dv=ctk.StringVar(value=self.dest.get()); row=ctk.CTkFrame(t, fg_color="transparent"); row.pack(fill="x", padx=20)
        ctk.CTkEntry(row, textvariable=dv, fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=10).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Browse", width=84, corner_radius=14, fg_color=CARD2, hover_color=STROKE, text_color=TX,
                      command=lambda: dv.set(filedialog.askdirectory(initialdir=dv.get() or HOME) or dv.get())).pack(side="left", padx=6)
        mo=ctk.BooleanVar(value=self.models_only.get()); ao=ctk.BooleanVar(value=self.auto_open.get())
        ctk.CTkCheckBox(t, text="Models only by default", variable=mo, fg_color=AC, hover_color=AC_H, text_color=TX).pack(anchor="w", padx=20, pady=(16,4))
        ctk.CTkCheckBox(t, text="Open folder when import finishes", variable=ao, fg_color=AC, hover_color=AC_H, text_color=TX).pack(anchor="w", padx=20)
        fr=ctk.CTkFrame(t, fg_color="transparent"); fr.pack(fill="x", padx=20, pady=(14,0))
        ctk.CTkLabel(fr, text="Build detail (voxel, mm)", text_color=TX).pack(side="left")
        fv=ctk.StringVar(value=str(self.fuse_voxel.get()))
        ctk.CTkEntry(fr, textvariable=fv, width=64, fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=10).pack(side="left", padx=8)
        ctk.CTkLabel(fr, text="0.4 = match scanner  ·  0.3 finer  ·  0.2 max", text_color=MUT, font=ctk.CTkFont(size=10)).pack(side="left")
        wr=ctk.CTkFrame(t, fg_color="transparent"); wr.pack(fill="x", padx=20, pady=(14,0))
        ctk.CTkLabel(wr, text="WiFi share code", text_color=TX).pack(side="left")
        wv=ctk.StringVar(value=str(self.cfg.get("wifi_code","")))
        ctk.CTkEntry(wr, textvariable=wv, width=64, fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=10).pack(side="left", padx=8)
        ctk.CTkLabel(wr, text="4 digits you'll always use, or leave blank for a fresh random one each time", text_color=MUT, font=ctk.CTkFont(size=10)).pack(side="left")
        gr=ctk.CTkFrame(t, fg_color="transparent"); gr.pack(fill="x", padx=20, pady=(14,0))
        ctk.CTkLabel(gr, text="3D view", text_color=TX).pack(side="left")
        glv=ctk.StringVar(value={"software":"Software view"}.get(self.cfg.get("gl_view","auto"), "Graphics card when available"))
        ctk.CTkOptionMenu(gr, variable=glv, values=["Graphics card when available","Software view"], width=230, fg_color="#0d0f14", button_color=CARD2,
                          button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=10).pack(side="left", padx=8)
        ctk.CTkLabel(gr, text="any OpenGL graphics works; the software view is the fallback", text_color=MUT, font=ctk.CTkFont(size=10)).pack(side="left")
        pr=ctk.CTkFrame(t, fg_color="transparent"); pr.pack(fill="x", padx=20, pady=(10,0))
        ctk.CTkLabel(pr, text="Build 3D models on", text_color=TX).pack(side="left")
        fdv=ctk.StringVar(value={"cpu":"CPU only"}.get(self.cfg.get("fuse_device","auto"), "NVIDIA GPU when available"))
        ctk.CTkOptionMenu(pr, variable=fdv, values=["NVIDIA GPU when available","CPU only"], width=230, fg_color="#0d0f14", button_color=CARD2,
                          button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=10).pack(side="left", padx=8)
        ctk.CTkLabel(pr, text="GPU: seconds per scan (needs ~2 GB VRAM) · CPU: minutes", text_color=MUT, font=ctk.CTkFont(size=10)).pack(side="left")
        # UI scale (for HiDPI / tiny-window fix)
        sr=ctk.CTkFrame(t, fg_color="transparent"); sr.pack(fill="x", padx=20, pady=(18,0))
        cur=getattr(self,"_ui_scale",1.0)
        sv=ctk.DoubleVar(value=cur)
        lab=ctk.CTkLabel(sr, text="UI scale: %.2fx"%cur, text_color=TX); lab.pack(side="left")
        ctk.CTkLabel(sr, text="(raise this if the window is tiny; applies next launch)", text_color=MUT, font=ctk.CTkFont(size=10)).pack(side="left", padx=(8,0))
        sl=ctk.CTkSlider(t, from_=0.8, to=2.5, number_of_steps=34, variable=sv,
                         command=lambda v: lab.configure(text="UI scale: %.2fx"%float(v)))
        sl.pack(fill="x", padx=20, pady=(4,0))
        def save():
            self.dest.set(dv.get()); self.models_only.set(mo.get()); self.auto_open.set(ao.get())
            try: self.fuse_voxel.set(max(0.1, min(2.0, float(fv.get()))))
            except Exception: pass
            code="".join(ch for ch in wv.get() if ch.isdigit())[:4]
            self.cfg["wifi_code"]=code.zfill(4) if code else ""
            self.cfg["gl_view"]="software" if glv.get().startswith("Software") else "auto"
            self.cfg["fuse_device"]="cpu" if fdv.get().startswith("CPU") else "auto"
            self.cfg["ui_scale"]=round(float(sv.get()),2); self._persist(); t.destroy()
            if abs(float(sv.get())-cur)>0.02:
                self._alert("UI scale changed", "The new UI scale takes effect next time you open PointYoink.")
        ctk.CTkButton(t, text="Save", corner_radius=18, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=save).pack(pady=20)

    def _on_tk_error(self, exc, val, tb):
        log_line("UI error: %s\n%s" % (val, "".join(_tb.format_exception(exc, val, tb)).rstrip()))
        try: self.set_banner("Something went wrong - see Help > Error log.", WARN)
        except Exception: pass

    def dlg_logs(self):
        t=self._top("Error log", 720, 520)
        if t is None: return
        ctk.CTkLabel(t, text="If something breaks, copy this into a GitHub issue.",
                     text_color=MUT).pack(anchor="w", padx=20, pady=(16,4))
        box=ctk.CTkTextbox(t, fg_color=CARD, text_color=TX, corner_radius=12, wrap="none",
                           font=ctk.CTkFont(family="monospace", size=11))
        box.pack(fill="both", expand=True, padx=20, pady=6)
        try: content=open(LOGFILE).read().replace(HOME, "~")     # no home paths in what gets pasted into issues
        except Exception: content=""
        box.insert("1.0", content or "No errors logged. Nice.")
        box.configure(state="disabled")
        row=ctk.CTkFrame(t, fg_color="transparent"); row.pack(fill="x", padx=20, pady=(4,16))
        def copy():
            try:
                self.clipboard_clear(); self.clipboard_append(content); self.set_banner("Log copied to clipboard.", OK)
            except Exception: pass
        def clear():
            try: open(LOGFILE,"w").close()
            except Exception: pass
            box.configure(state="normal"); box.delete("1.0","end"); box.insert("1.0","No errors logged. Nice."); box.configure(state="disabled")
        ctk.CTkButton(row, text="Copy", corner_radius=16, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=copy).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Open log file", corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX,
                      command=lambda: subprocess.Popen(["xdg-open", LOGFILE])).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Clear", corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=clear).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Report on GitHub", corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX,
                      command=lambda: subprocess.Popen(["xdg-open", GITHUB+"/issues/new"])).pack(side="right", padx=4)

    def _persist(self):
        self.cfg.update(dest=self.dest.get(), models_only=self.models_only.get(),
                        auto_open=self.auto_open.get(), geometry=self.geometry(),
                        exp_stl=self.exp_stl.get(), exp_obj=self.exp_obj.get(), exp_glb=self.exp_glb.get(),
                        fuse_voxel=round(float(self.fuse_voxel.get() or 0.4),2),
                        clean_isolation=self._num(self.clean_iso,15,0,100), clean_fill_holes=self.clean_holes.get(),
                        clean_smooth_times=int(self._num(self.clean_smooth,3,0,50)), clean_keep_pct=self._num(self.clean_keep,100,1,100),
                        clean_do_iso=self.clean_do_iso.get(), clean_do_smooth=self.clean_do_smooth.get(), clean_do_keep=self.clean_do_keep.get(), clean_do_base=self.clean_do_base.get(),
                        scanner_ip=self.live_ip.get().strip(),
                        cleanup=self.cleanup.get(), side=self.cfg.get("side","project"),
                        gl_view=self.cfg.get("gl_view","auto"), fuse_device=self.cfg.get("fuse_device","auto"),
                        records=self.records); save_cfg(self.cfg)

    # per-project records (rename + imported memory), keyed by ORIGINAL id
    def disp(self, name):
        return (self.records.get(name,{}).get("label") or name)
    def is_imported(self, name):
        # "imported" must mean a real model actually landed - not just a non-empty folder
        # left behind by a failed/partial transfer. Require at least one mesh/point-cloud .ply.
        d=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        if not os.path.isdir(d): return False
        try:
            for pat in (os.path.join(d, "*.ply"), os.path.join(d, "data", "*", "*.ply")):   # flat, then nested
                if any(os.path.getsize(f) > 1024 for f in glob.glob(pat)): return True
        except Exception: pass
        return False
    def _proj(self, name):
        return next((x for x in self.projects if x["name"]==name), None)
    def changed(self, name):
        """True if the device project has new scans/meshes or a newer edit time since it was imported."""
        rec=self.records.get(name,{})
        if not rec.get("imported_at"): return False
        sig=rec.get("sig"); p=self._proj(name)
        if not sig or not p: return False
        return ((p.get("edit_time") or 0) > (sig.get("edit_time") or 0)
                or (p.get("nodes") or 0) > (sig.get("nodes") or 0)
                or (p.get("meshes") or 0) > (sig.get("meshes") or 0))
    def rename_project(self, name):
        d=ctk.CTkInputDialog(title="Rename project",
                             text="Friendly name for:\n%s\n\n(the original ID is kept as the folder name /\nreference - clear the box to reset)"%name)
        val=d.get_input()
        if val is None: return
        self.records.setdefault(name,{})["label"]=(val.strip() or None)
        self._persist(); self.projects_sig=None  # force re-render
    def on_close(self):
        try:
            if self._wifi: self._wifi.stop()
        except Exception: pass
        try:   # projector off while still streaming; the streams die with us and the RANGE reboots (normal)
            if getattr(self, "_range_on", False) and self._range: self._range.projector(False)
        except Exception: pass
        self._persist(); self.destroy()

    # ---- helpers ----
    def browse(self):
        d=filedialog.askdirectory(initialdir=self.dest.get() or HOME)
        if d: self.dest.set(d)
    def set_banner(self, text, color): self.banner.configure(text=text); self.dot.configure(text_color=color)
    def select_all(self):
        for v in self.pull_sel.values(): v.set(True)
        self.update_summary()
    def select_none(self):
        for v in self.pull_sel.values(): v.set(False)
        self.update_summary()
    def update_summary(self):
        sel=[n for n,v in self.pull_sel.items() if v.get()]; n=len(sel); s="" if n==1 else "s"
        self.sel_lbl.configure(text=("%d project%s selected"%(n,s)) if n else "No projects selected")
        if not sel:
            self.summary.configure(text="No projects selected"); self.import_btn.configure(text="Import selected"); return
        known=[self.size_cache.get(x) for x in sel]; tot=sum(z for z in known if z); miss=sum(1 for z in known if not z)
        est=" · %s%s"%(human(tot), "+" if miss else "") if tot else ""
        note="" if self.models_only.get() else " · full, with raw frames"
        self.summary.configure(text="%d project%s%s%s"%(n,s,est,note))
        self.import_btn.configure(text="Import %d project%s"%(n,s))
    def _search_changed(self):
        self.projects_sig=None
        if self.projects: self.render_list(self.projects)

    # ---- polling ----
    def refresh_loop(self):
        if not self.pulling and not self._wifi:
            st,serial=usb_state(); self.serial=serial; mounted=quick_mounted()
            if not mounted and self.listed_src!="local": self.listed=False; self.start_listing()   # show what's on this PC
            if st=="absent":
                self.set_banner("Scanner not detected - plug in the USB-C cable, or use WiFi.", WARN)
                self.action_btn.configure(text="🔌  USB", state="normal"); self.auto_tried=False
            elif st=="adb":
                self.set_banner("MIRACO detected · Not connected - tap “File Transfer” on the scanner", WARN)
                self.action_btn.configure(text="🔌  USB", state="normal"); self.auto_tried=False
            elif st=="mtp" and not mounted:
                self.action_btn.configure(text="🔌  USB", state="normal")
                if self._mounting:
                    self.set_banner("Connecting…", AC)
                elif not self.auto_tried:
                    self.auto_tried=True; self.set_banner("MIRACO detected - connecting…", AC); self.on_mount()
                else:
                    self.set_banner("MIRACO detected · Not connected - click USB →", AC)
            elif mounted:
                self.action_btn.configure(text="🔌  Rescan", state="normal")
                if self.listed_src!="device": self.listed=False
                if self.listed:
                    self.set_banner("Connected - tick scans to import, click one to preview.", OK)
                    if self.projects: self.render_list(self.projects)   # refresh badges if files changed on disk (cheap no-op otherwise)
                else: self.set_banner("Reading projects off the scanner… (MTP is slow)", AC); self.start_listing()
        self.after(1500, self.refresh_loop)
    def start_listing(self):
        if self.listing: return
        self.listing=True; dest=self.dest.get() or DEFAULT_DEST; self._listing_src="device" if quick_mounted() else "local"
        def work():
            dev=list_projects() if self._listing_src=="device" else []
            names={p["name"] for p in dev}; local=list_local_projects(dest); lmap={p["name"]: p for p in local}
            for p in dev:                                  # a project that is also on this PC keeps what the PC knows about it
                lp=lmap.get(p["name"])
                if lp:
                    for k in ("combined", "prepared", "dev_meshed"): p[k]=lp.get(k)
                    p["on_pc"]=True
            self.q.put(("projects", dev+[p for p in local if p["name"] not in names]))
        threading.Thread(target=work, daemon=True).start()
    def on_mount(self):
        if self._mounting: return
        st,_=usb_state()
        if st!="mtp": self.set_banner("Tap “File Transfer” on the MIRACO first.", WARN); return
        self._mounting=True; self._shots_loaded=False; self.set_banner("Connecting…", AC)
        threading.Thread(target=lambda: self.q.put(("mounted", *do_mount())), daemon=True).start()

    # ---- list ----
    def render_list(self, projs):
        # include imported/changed state and the search text so the list re-renders when files or the filter change
        q=(self.search.get() or "").strip().lower()
        self.all_projects=projs; projs=self._page_filter(projs)
        sig=json.dumps([q, self.page]+[[p, self.is_imported(p["name"]), self.changed(p["name"])] for p in projs])
        if sig==self.projects_sig: return
        self.projects_sig=sig; self.projects=projs
        for w in self.llist.winfo_children(): w.destroy()
        old=self.pull_sel; self.pull_sel={}; self.rows={}
        if not projs:
            if self.page=="projects":
                self.list_empty=ctk.CTkFrame(self.llist, fg_color="transparent"); self.list_empty.grid(row=0,column=0, sticky="nsew")
                ctk.CTkLabel(self.list_empty, text="Nothing on this PC yet", text_color=TX, font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(60,4))
                ctk.CTkLabel(self.list_empty, text="Import a project from the scanner first.\nIt shows up here with everything you make from it.", text_color=MUT, font=ctk.CTkFont(size=12), justify="center").pack()
                ctk.CTkButton(self.list_empty, text="⬇  Go to Import", width=140, height=32, corner_radius=16, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=lambda: self._set_mode("Projects")).pack(pady=14)
            else:
                self.list_empty=self._empty_state(self.llist, "projects"); self.list_empty.grid(row=0,column=0, sticky="nsew")
                self._fit_empty("list_empty", self.llist)
            # nothing to preview either: cover the preview box (and park the 3D view so it cannot draw over the panel)
            self._mv_key=None; self.mv.grid_remove(); self.big.grid(); self.big_empty.grid(); self.big_empty.lift()
        else: self.list_empty=None
        shown=0
        for p in projs:
            name=p["name"]; var=old.get(name)
            if var is None:
                var=ctk.BooleanVar(value=False); var.trace_add("write", lambda *a: self.update_summary())
            self.pull_sel[name]=var
            if q and q not in (self.disp(name)+" "+name).lower(): continue
            card=ctk.CTkFrame(self.llist, fg_color=(SELB if name==self.selected else ROW), corner_radius=10, border_width=0)
            card.grid(row=2*shown, column=0, sticky="ew", pady=(2,0), padx=4); card.grid_columnconfigure(2, weight=1)
            self.rows[name]=card
            if self.page=="import":
                ctk.CTkCheckBox(card, text="", width=24, checkbox_width=20, checkbox_height=20, corner_radius=5, border_color=DIM,
                                variable=var, fg_color=AC, hover_color=AC_H).grid(row=0,column=0, padx=(8,0), pady=10)
            tbox=ctk.CTkFrame(card, fg_color="#0a0c10", corner_radius=8, width=60, height=50); tbox.grid(row=0,column=1, padx=(2,2), pady=8); tbox.grid_propagate(False)
            tbox.grid_columnconfigure(0, weight=1); tbox.grid_rowconfigure(0, weight=1)
            if p.get("thumb"):
                try: self.imgs["row_"+name]=cimg(p["thumb"],54); ctk.CTkLabel(tbox, image=self.imgs["row_"+name], text="").grid(row=0,column=0)
                except Exception: ctk.CTkLabel(tbox, text="-", text_color=MUT).grid(row=0,column=0)
            else: ctk.CTkLabel(tbox, text="-", text_color=MUT).grid(row=0,column=0)
            txt=ctk.CTkFrame(card, fg_color="transparent"); txt.grid(row=0,column=2, sticky="ew", padx=(4,8), pady=6)
            ctk.CTkLabel(txt, text=self.disp(name), text_color=TX, font=ctk.CTkFont(size=12,weight="bold"),
                         anchor="w", justify="left", wraplength=150).pack(anchor="w", fill="x")
            if self.records.get(name,{}).get("label"):
                ctk.CTkLabel(txt, text=name, text_color=MUT, font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w", fill="x")
            l2=" · ".join([x for x in [human(self.size_cache[name]) if self.size_cache.get(name) else "", (p.get("date") or "")[:10]] if x])
            if l2: ctk.CTkLabel(txt, text=l2, text_color=MUT, font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w", fill="x")
            parts=[]
            if p.get("nodes"): parts.append("%d scan%s"%(p["nodes"], "" if p["nodes"]==1 else "s"))
            ml=ctk.CTkFrame(txt, fg_color="transparent"); ml.pack(anchor="w", fill="x", pady=(2,0))
            badges=[]
            if p.get("local"): badges.append(("on this PC", AC, "#15304d"))
            elif self.is_imported(name): badges.append(("↑ updated", WARN, "#3d2f14") if self.changed(name) else ("✓ Imported", OK, "#173a2a"))
            else: badges.append(("on the scanner", MUT, CARD2))
            if p.get("nodes") and (p.get("local") or p.get("on_pc")):
                dm=p.get("dev_meshed") or 0
                if not dm: badges.append(("raw only", WARN, "#3d2f14"))
                elif dm<p["nodes"]: badges.append(("partly scanner-edited", WARN, "#3d2f14"))
                else: badges.append(("scanner-edited", OK, "#173a2a"))
            if p.get("combined"): badges.append(("⧉ combined", OK, "#173a2a"))
            if p.get("prepared"): badges.append(("✦ prepared", OK, "#173a2a"))
            b0=badges[0]
            ctk.CTkLabel(ml, text=b0[0], text_color=b0[1], fg_color=b0[2], corner_radius=6, width=1, height=18, font=ctk.CTkFont(size=10)).pack(side="left", padx=(0,6), ipadx=6)
            if parts: ctk.CTkLabel(ml, text=" · ".join(parts), text_color=MUT, font=ctk.CTkFont(size=10)).pack(side="left")
            ml2=None
            if len(badges)>1:               # what has been made from it, on its own row so nothing clips
                ml2=ctk.CTkFrame(txt, fg_color="transparent"); ml2.pack(anchor="w", fill="x", pady=(3,0))
                for badge in badges[1:]:
                    ctk.CTkLabel(ml2, text=badge[0], text_color=badge[1], fg_color=badge[2], corner_radius=6, width=1, height=18, font=ctk.CTkFont(size=10)).pack(side="left", padx=(0,6), ipadx=6)
            for w in [card, tbox, txt, ml] + ([ml2]+ml2.winfo_children() if ml2 else []) + tbox.winfo_children() + txt.winfo_children() + ml.winfo_children():
                w.bind("<Button-1>", lambda e,n=name: self.select_project(n))
            tk.Frame(self.llist, bg=STROKE, height=1, bd=0, highlightthickness=0).grid(row=2*shown+1, column=0, sticky="ew", padx=14, pady=(2,0))
            shown+=1
        if projs and q and not shown:
            ctk.CTkLabel(self.llist, text="No project matches “%s”."%self.search.get().strip(), text_color=MUT,
                         font=ctk.CTkFont(size=12)).grid(row=0,column=0, sticky="w", padx=12, pady=16)
        threading.Thread(target=self._compute_sizes, args=([p["name"] for p in projs], self.dest.get() or DEFAULT_DEST), daemon=True).start()
        self.update_summary()
        if projs and not self.selected: self.select_project(projs[0]["name"])
    def _compute_sizes(self, names, dest):
        for n in names:
            if n not in self.size_cache:
                sz,_=project_model_size(n, os.path.join(dest, n)); self.size_cache[n]=sz; self.q.put(("sizes",None))
    def select_project(self, name):
        self.selected=name
        for n,card in self.rows.items():
            card.configure(fg_color=(SELB if n==name else ROW))
        p=next((x for x in self.projects if x["name"]==name), None)
        if not p: return
        self.big_empty.grid_remove()
        self._film_sel=None; self._film_cells={}
        if p.get("thumb"): self._set_big_image(p["thumb"])
        else: self._big_src=None; self.big.configure(image=None, text="No preview for this project yet")
        self.big_hint.configure(text="")
        counts=[]
        if p.get("nodes"): counts.append("%d scan%s" % (p["nodes"], "" if p["nodes"]==1 else "s"))
        if p.get("meshes"): counts.append("%d 3D model%s" % (p["meshes"], "" if p["meshes"]==1 else "s"))
        if p.get("clouds") and p.get("clouds")!=p.get("meshes"): counts.append("%d cloud%s" % (p["clouds"], "" if p["clouds"]==1 else "s"))
        where="on this PC" if p.get("local") else ("imported" if self.is_imported(name) else "on the scanner")
        self.detail.configure(text="%s\n%s\n%s · %s" % (self.disp(name), ("edited "+p["date"]) if p.get("date") else "", " · ".join(counts), where))
        self._fill_header(p, name, counts)
        self.proj_empty.grid_remove(); self.projbar.grid(); self.film.grid()
        self.renders_lbl.configure(text=""); self.renders_lbl.place(relx=1.0, rely=0.0, x=-12, y=10, anchor="ne")
        if p.get("meshes") or p.get("nodes"): self.tools.grid()   # Process on PC works on unfused scans too
        else: self.tools.grid_remove()
        self._proc_refresh()
        for w in self.film.winfo_children(): w.destroy()
        if name in self.gallery_cache: self.render_gallery(name, self.gallery_cache[name])
        else:
            ctk.CTkLabel(self.film, text="loading scan renders…", text_color=MUT).pack(side="left", padx=8, pady=40)
            local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
            threading.Thread(target=lambda n=name, l=local: self.q.put(("gallery",n,gather_gallery(n, l))), daemon=True).start()
        self.files_box.configure(state="normal"); self.files_box.delete("1.0","end")
        self.files_box.insert("end","computing model files…\n"); self.files_box.configure(state="disabled")
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        threading.Thread(target=lambda n=name, l=local: self.q.put(("files",n,project_model_size(n, l))), daemon=True).start()
        self._request_shaded(name, None)
    def _fill_header(self, p, name, counts):
        """Title block for the selected project: name, id, state chip, edited date."""
        self.hdr_name.configure(text=self.disp(name))
        self.hdr_id.configure(text=name if self.records.get(name,{}).get("label") else " · ".join(counts))
        self.hdr_date.configure(text=("edited "+p["date"]) if p.get("date") else "")
        if p.get("local"): chip=("on this PC", AC, "#15304d")
        elif self.is_imported(name): chip=("updated", WARN, "#3d2f14") if self.changed(name) else ("imported", OK, "#173a2a")
        else: chip=("on the scanner", MUT, CHIP)
        for w in self.chips.winfo_children(): w.destroy()
        ctk.CTkLabel(self.chips, text=chip[0], text_color=chip[1], fg_color=chip[2], corner_radius=6, width=1, height=20,
                     font=ctk.CTkFont(size=10)).pack(side="left", ipadx=6)
    def render_gallery(self, name, items):
        if self.selected!=name: return
        for w in self.film.winfo_children(): w.destroy()
        self._film_cells={}
        if not items:
            self.film.grid_remove(); return
        self.film.grid()
        for i,(node,path) in enumerate(items):
            try:
                self.imgs["g_"+name+node]=cimg(path,100)
                cell=ctk.CTkFrame(self.film, fg_color="#0a0c10", corner_radius=10, border_width=2, border_color=(AC if node==self._film_sel else STROKE))
                cell.pack(side="left", padx=(0,10), pady=(6,4))
                im=ctk.CTkLabel(cell, image=self.imgs["g_"+name+node], text=""); im.pack(padx=8, pady=(8,2))
                cap=ctk.CTkLabel(cell, text=("Combined" if node=="combined" else "Scan %02d"%(1+[n for n,_ in items if n!="combined"].index(node))), text_color=(AC if node=="combined" else MUT), font=ctk.CTkFont(size=11)); cap.pack(pady=(0,6))
                for w in (cell, im, cap): w.bind("<Button-1>", lambda e,nd=node,pp=path: self._pick_scan(name, nd, pp))
                self._film_cells[node]=cell
            except Exception: pass
        self.after(120, self._film_fit)
    def _film_fit(self):
        """Show the strip's scrollbar only when the thumbnails do not fit."""
        try:
            need=sum(w.winfo_reqwidth()+10 for w in self.film.winfo_children()) > self.film._parent_canvas.winfo_width()
            if need: self.film._scrollbar.grid()
            else: self.film._scrollbar.grid_remove()
        except Exception: pass
    def _pick_scan(self, name, node, path):
        self._film_sel=node; self._mark_scan(node)
        self._set_big_image(path); self._request_shaded(name, node)
        if self.page=="projects": self._panel_refresh()
    def _mark_scan(self, node):
        for nd,cell in self._film_cells.items():
            try: cell.configure(border_color=(AC if nd==node else STROKE))
            except Exception: pass
    def _enlarge(self, path):
        self._set_big_image(path)

    def _set_big_image(self, path):
        """Show a preview that scales to fill the box and re-fits on window resize."""
        try:
            self._big_src=Image.open(path).convert("RGBA")
            new=ctk.CTkImage(light_image=self._big_src, dark_image=self._big_src, size=(320,240))
            try: self.big._label.configure(image="")      # drop a stale image name first (Tk refuses any configure while one is dead)
            except Exception: pass
            self.big.configure(image=new, text=""); self.imgs["big"]=new
            self._fit_big()
        except Exception as e:
            log_error("preview-image", e); self._big_src=None
            try: self.big._label.configure(image=""); self.big.configure(image=None, text="(preview unavailable)")
            except Exception: pass
    def _on_big_resize(self, e):
        if getattr(self,"_fit_job",None):
            try: self.after_cancel(self._fit_job)
            except Exception: pass
        self._fit_job=self.after(60, self._fit_big)
    def _fit_big(self, _=None):
        src=getattr(self,"_big_src",None)
        if src is None or "big" not in self.imgs: return
        try:
            bw=max(60, self.big.master.winfo_width()-28); bh=max(60, self.big.master.winfo_height()-28)
            iw,ih=src.size
            scale=min(bw/iw, bh/ih)
            scale=min(scale, 2.2)   # cap upscaling so a small preview doesn't get too blurry
            self.imgs["big"].configure(size=(max(20,int(iw*scale)), max(20,int(ih*scale))))
        except Exception: pass

    # ---- shaded 3D preview: the scan's mesh rendered off-screen (worker thread, cached PNG) ----
    def _mesh_for_node(self, name, node):
        cur=self._proc_current(name, node)          # the version picked on the Process page (or the best available)
        if cur: return cur[2]
        c=os.path.join(PROJECTS, name, "data", node, "fuse_mesh.ply")
        return c if os.path.exists(c) else None
    def _node_of(self, name, path):
        base=os.path.basename(path)
        if base=="fuse_mesh.ply": return os.path.basename(os.path.dirname(path))
        if base.startswith(name+"_") and base.endswith(".ply"): return base[len(name)+1:-4]
        return None
    def _shade_mode_changed(self, v):
        self.shade_mode="wire" if v=="Wireframe" else "solid"
        if self.mv.winfo_manager(): self.mv.set_wire(self.shade_mode=="wire"); return   # live view: just redraw
        if self.selected: self._request_shaded(self.selected, self._film_sel)
    def _request_shaded(self, name, node=None):
        """Show the cached shaded render for this scan, or queue one. Never blocks the UI thread."""
        mesh=self._mesh_for_node(name, node) if node else self._find_mesh(name)
        if not mesh:
            self.big_hint.configure(text="No 3D model yet: this scan is raw data. Build it on the Projects page (or One-tap Edit on the scanner)."); self._preview_idle(); return
        node=node or self._node_of(name, mesh)
        if node and node!=self._film_sel: self._film_sel=node; self._mark_scan(node)
        key="%s__%s"%(name, node) if node else name; mode=self.shade_mode
        out=os.path.join(THUMBS, key+("__shaded.png" if mode=="solid" else "__wire.png"))
        self._shade_key=(key, mode)
        if self._mv_key!=key:                       # a different scan: back to the flat image until its 3D view is ready
            self._mv_key=None; self.mv.grid_remove(); self.big.grid()
        self._mv_want=(key, mesh if not mesh.startswith(PROJECTS) else os.path.join(THUMBS, "view", key+"_fuse_mesh.ply"))
        self._mv_start()                            # a local model: start the live view now, don't wait for the still image
        st=self._mesh_stats.get(key)
        if st: self._show_stats(st)
        try:
            fresh=os.path.exists(out) and os.path.getmtime(out)>=os.path.getmtime(mesh) and os.path.getsize(out)>1024
            if fresh:
                Image.open(out).verify()          # a render killed half-way leaves a broken PNG behind: redo it
        except Exception:
            fresh=False
            try: os.remove(out)
            except Exception: pass
        if fresh:
            self._show_shaded(out)
            if not st: threading.Thread(target=lambda: self.q.put(("mesh_stats", key, _ply_counts(mesh))), daemon=True).start()
            return
        if (key,mode) in self._shade_failed:
            self.big_hint.configure(text="Scanner's own preview · could not draw the 3D model"); return
        if mesh.startswith(PROJECTS) and self.pulling:
            self.big_hint.configure(text="Scanner preview · the 3D render waits for the import to finish"); return
        self.big_hint.configure(text="Drawing the 3D model…"); self._dim_preview(); self._preview_busy("Drawing the 3D model")
        with self._shade_lock:
            self._shade_want=(key, mode, name, mesh, out); start=not self._shade_running; self._shade_running=True
        if start: threading.Thread(target=self._shade_thread, daemon=True).start()
    def _shade_thread(self):
        while True:
            with self._shade_lock:
                want=self._shade_want; self._shade_want=None
                if not want: self._shade_running=False; return
            key,mode,name,mesh,out=want
            try:
                path=mesh
                if mesh.startswith(PROJECTS):   # on the slow device mount: copy to the local cache first (shared with View in 3D)
                    cache=os.path.join(THUMBS, "view"); os.makedirs(cache, exist_ok=True)
                    path=os.path.join(cache, key+"_fuse_mesh.ply")
                    if not os.path.exists(path) or os.path.getsize(path)!=os.path.getsize(mesh):
                        self.q.put(("shade_msg", key, "Copying the 3D model off the scanner…")); shutil.copyfile(mesh, path)
                stats=_ply_counts(path)
                self.q.put(("mesh_stats", key, stats))
                _render_mesh_png(path, out, mode)
                self.q.put(("shaded", key, mode, out))
            except Exception as e:
                log_error("shaded-preview "+key, e); self.q.put(("shaded", key, mode, None))
    def _preview_busy(self, text):
        """Show the spinner overlay with a step name (call again to change the text)."""
        try:
            self.big_loader_lbl.configure(text=text)
            if not self.big_loader.winfo_ismapped(): self.big_loader.place(relx=0.5, rely=0.5, anchor="center")
            self.big_loader.lift()
            if self._spin_job is None: self._spin_tick()
        except Exception: pass
    def _preview_idle(self):
        try:
            self.big_loader.place_forget()
            if self._spin_job: self.after_cancel(self._spin_job); self._spin_job=None
        except Exception: pass
    def _spin_tick(self):
        cv=self._spin_cv; cv.delete("all"); self._spin_ang=(self._spin_ang+14)%360
        cv.create_oval(6,6,38,38, outline="#1d222c", width=4)
        cv.create_arc(6,6,38,38, start=-self._spin_ang, extent=90, style="arc", outline=AC, width=4)
        self._spin_job=self.after(40, self._spin_tick)
    def _dim_preview(self):
        """Darken whatever the preview box shows while a render is in flight, so the wait is obvious."""
        try:
            from PIL import ImageEnhance
            src=getattr(self, "_big_src", None)
            if src is None: return
            dim=ImageEnhance.Brightness(src).enhance(0.35)
            self.imgs["big_dim"]=ctk.CTkImage(light_image=dim, dark_image=dim, size=self.imgs["big"].cget("size") if "big" in self.imgs else (320,240))
            self.big.configure(image=self.imgs["big_dim"], text="")
        except Exception: pass
    def _show_shaded(self, out):
        self._set_big_image(out); self._preview_idle()
        self.big_hint.configure(text="Still image · the live 3D view is loading")
        self._mv_start()
    def _make_mv(self, software=False):
        w=None
        if not software and os.environ.get("POINTYOINK_NO_GL")!="1" and self.cfg.get("gl_view","auto")!="software":
            try:
                import glview; w=glview.GLView(self._mv_wrap)
            except Exception as e: log_line("GL view unavailable, using the software view: %s" % e); w=None
        if w is None:
            import meshview; w=meshview.MeshView(self._mv_wrap)
        w.grid(row=0,column=0, sticky="nsew", padx=12, pady=12); w.grid_remove(); return w
    def _mv_start(self):
        """Load the interactive view for the current scan (mesh prep runs in a thread) and swap it in."""
        want=self._mv_want
        if not want or self._mv_key==want[0] or not os.path.exists(want[1]): return
        key,path=want; self._mv_key=key; self.mv.wire=(self.shade_mode=="wire")
        self.big_hint.configure(text="Still image · loading the live 3D view…"); self._preview_busy("Loading the live 3D view")
        def ready(ok, k=key):
            if k!=self._mv_key: return
            if not ok and getattr(self.mv, "failed", False) and not isinstance(self.mv, __import__("meshview").MeshView):
                log_line("GL view failed in this window (%s); switching to the software view" % getattr(self.mv, "_err", ""))
                try: self.mv.destroy()
                except Exception: pass
                self.mv=self._make_mv(software=True); self.mv.wire=(self.shade_mode=="wire"); self.mv.load(path, ready); return
            self._preview_idle()
            if ok:
                self.big.grid_remove(); self.mv.grid()
                self.big_hint.configure(text="Drag to rotate · scroll to zoom · right-drag to pan · double-click to reset")
            else:
                self.big_hint.configure(text="Still image · the live 3D view is loading")
        self.mv.load(path, ready)
    def _show_stats(self, st):
        v,f=st
        if f: self.renders_lbl.configure(text="3D model · %s triangles"%_kfmt(f))
        elif v: self.renders_lbl.configure(text="Points only · %s points"%_kfmt(v))

    # ---- import ----
    def on_pull(self):
        if self.pulling: return
        sel=[n for n,v in self.pull_sel.items() if v.get()]
        if not sel: self.set_banner("Tick at least one project to import.", WARN); return
        onpc=[n for n in sel if (self._proj(n) or {}).get("local")]
        if onpc:
            sel=[n for n in sel if n not in onpc]
            if not sel: self.set_banner("Those are already on this PC: open the Projects page to work on them.", MUT); return
        already=[n for n in sel if self.is_imported(n) and not self.changed(n)]
        if already:
            names=", ".join(self.disp(n) for n in already)
            if not self._confirm("Already imported",
                    "%d of these were already imported and haven't changed:\n%s\n\nImport them again anyway?"%(len(already), names)):
                sel=[n for n in sel if n not in already]
                if not sel:
                    self.set_banner("Nothing to import (all already imported).", MUT); return
        self.pulling=True; self.cancel=False; self._pull_list=sel; self._export_fails=[]
        self.import_btn.grid_remove(); self.cancel_btn.grid(row=0,column=3)
        self.progress.grid(row=1,column=0, columnspan=3, sticky="ew", pady=(8,0)); self.progline.grid(row=2,column=0, columnspan=3, sticky="w")
        dest=self.dest.get() or DEFAULT_DEST; mo=self.models_only.get(); cleanup=self.cleanup.get(); self._persist()
        fmts=[]
        if self.exp_stl.get(): fmts.append("stl")
        if self.exp_obj.get(): fmts.append("obj")
        if self.exp_glb.get(): fmts.append("glb")
        threading.Thread(target=self._pull_worker, args=(sel,dest,mo,fmts,cleanup), daemon=True).start()
    def _pull_worker(self, sel, dest, mo, fmts, cleanup):
        total=len(sel); failed=[]
        try:
            os.makedirs(dest, exist_ok=True)
            for i,name in enumerate(sel):
                if self.cancel: break
                try:
                    if mo:
                        self._import_flat(name, dest, fmts, cleanup, i, total)   # clean flat layout: <name>/<name>_<node>.ply (+.stl)
                    else:
                        self._import_full(name, dest, i, total)         # full project incl. raw frames (nested mirror)
                except Exception as e:
                    failed.append(name); log_error("import", e)
        except Exception as e:                       # e.g. the destination cannot be created
            log_error("import-setup", e); failed=list(sel)
        finally:
            self.proc=None
            self.q.put(("cancelled" if self.cancel else "done", dest, failed))

    def _import_flat(self, name, dest, fmts, cleanup, i, total, src_root=None, nodes=None):
        """Copy just the finished models into <dest>/<name>/ with clean unique names.
        nodes: optional list of scan ids to keep (WiFi picker); default all."""
        keep=nodes
        src=os.path.join(src_root or PROJECTS, name); out=os.path.join(dest, name); os.makedirs(out, exist_ok=True)
        revo=os.path.join(src, name+".revo")
        if os.path.exists(revo):
            try: shutil.copyfile(revo, os.path.join(out, name+".revo"))
            except Exception: pass
        nodes=sorted(glob.glob(os.path.join(src, "data", "*")))
        if nodes is not None and keep is not None: nodes=[nd for nd in nodes if os.path.basename(nd) in keep]
        n=max(1,len(nodes))
        meshes=[]
        for j,nd in enumerate(nodes):
            if self.cancel: return
            if not os.path.isdir(nd): continue
            node=os.path.basename(nd)
            self.q.put(("prog", (i*100 + j*90//n)/(total*100), "Importing %s - scan %d/%d"%(name, j+1, n)))
            m=os.path.join(nd,"fuse_mesh.ply"); c=os.path.join(nd,"fuse.ply"); pv=os.path.join(nd,"preview.png")
            if os.path.exists(m):
                d=os.path.join(out, "%s_%s.ply"%(name,node)); shutil.copyfile(m, d); meshes.append(d)
            if os.path.exists(c):
                shutil.copyfile(c, os.path.join(out, "%s_%s_cloud.ply"%(name,node)))
            if os.path.exists(pv):
                try: shutil.copyfile(pv, os.path.join(out, "%s_%s.png"%(name,node)))
                except Exception: pass
        if (fmts or cleanup) and not self.cancel:
            self._process_meshes(meshes, name, fmts, cleanup, i, total)

    def _ensure_clean_vars(self):
        """Clean-up knobs, named and defaulted like the scanner's Mesh panel (dev/design/device/SCANNER-EDIT-OPTIONS.md).
        The Process page is built before the settings vars, so both sides call this."""
        if "clean_iso" in self.__dict__: return
        self.clean_iso=ctk.StringVar(value=str(self.cfg.get("clean_isolation",15)))
        self.clean_holes=ctk.BooleanVar(value=bool(self.cfg.get("clean_fill_holes",False)))
        self.clean_smooth=ctk.StringVar(value=str(self.cfg.get("clean_smooth_times",3)))
        self.clean_keep=ctk.StringVar(value=str(self.cfg.get("clean_keep_pct",100)))
        self.clean_do_iso=ctk.BooleanVar(value=bool(self.cfg.get("clean_do_iso",True)))
        self.clean_do_smooth=ctk.BooleanVar(value=bool(self.cfg.get("clean_do_smooth",True)))
        self.clean_do_keep=ctk.BooleanVar(value=bool(self.cfg.get("clean_do_keep",False)))
        self.clean_do_base=ctk.BooleanVar(value=bool(self.cfg.get("clean_do_base",False)))
    def _num(self, var, default, lo, hi):
        try: v=float(str(var.get()).strip().rstrip("%"))
        except Exception: v=default
        return max(lo, min(hi, v))
    def _clean_args(self):
        """process.py flags for the clean-up knobs shown on the Process page."""
        self._ensure_clean_vars()
        args=["--clean", "--isolation-rate", "%g" % (self._num(self.clean_iso,15,0,100) if self.clean_do_iso.get() else 0),
              "--smooth-times", "%d" % (int(self._num(self.clean_smooth,3,0,50)) if self.clean_do_smooth.get() else 0)]
        if self.clean_holes.get(): args.append("--fill-holes")
        if self.clean_do_base.get(): args.append("--base-remove")
        keep=self._num(self.clean_keep,100,1,100)
        if self.clean_do_keep.get() and keep<100: args+=["--simplify-pct", "%g" % keep]
        return args
    def _clean_subprocess(self, src, out):
        """Run process.py --clean in a memory-capped child so a huge mesh cannot take the app down."""
        try:
            env=dict(os.environ); env.setdefault("POINTYOINK_MEM_CAP_GB", "10")
            r=subprocess.run([_sys.executable, os.path.join(HERE, "process.py"), src, out]+self._clean_args(),
                             capture_output=True, text=True, timeout=1800, env=env)
            if r.returncode==0 and os.path.exists(out) and os.path.getsize(out)>1024: return True
            log_line("clean %s failed (rc=%s): %s" % (os.path.basename(src), r.returncode, (r.stdout+r.stderr)[-400:]))
        except Exception as e: log_error("clean "+os.path.basename(src), e)
        return False

    def _process_meshes(self, plys, name, fmts, cleanup, i, total):
        """Optionally clean each mesh into <stem>_clean.ply (the imported original is kept), then export
        the requested formats from the cleaned copy when there is one. Failures land in self._export_fails."""
        import trimesh
        for ply in plys:
            if self.cancel: return
            src=ply
            if cleanup:
                self.q.put(("prog", (i+1)/total, "Cleaning up %s…"%name))
                out=ply[:-4]+"_clean.ply"
                if self._clean_subprocess(ply, out): src=out
                else: self._export_fails.append(os.path.basename(out))
            if not fmts: continue
            try: m=trimesh.load(src, force="mesh")
            except Exception as e:
                log_error("load "+os.path.basename(src), e); self._export_fails.append(os.path.basename(src)); continue
            for ext in fmts:
                if self.cancel: return
                self.q.put(("prog", (i+1)/total, "Converting %s to %s"%(name, ext.upper())))
                try: m.export(src[:-4]+"."+ext)
                except Exception as e:
                    log_error("convert %s -> %s"%(os.path.basename(src), ext), e)
                    self._export_fails.append(os.path.basename(src)[:-4]+"."+ext)

    def _import_full(self, name, dest, i, total):
        """Full project including raw frames - kept in the device's nested layout (needed to re-process)."""
        src=os.path.join(PROJECTS,name)+"/"; dst=os.path.join(dest,name)+"/"; os.makedirs(dst, exist_ok=True)
        cmd=["rsync","-a","--info=progress2",src,dst]
        self.q.put(("prog", i/total, "Project %d of %d - %s (full)"%(i+1,total,name)))
        self.proc=subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for ln in self.proc.stdout:
            if self.cancel: self.proc.terminate(); break
            mm=re.search(r"(\d+)%",ln)
            if mm:
                fp=int(mm.group(1)); self.q.put(("prog",(i*100+fp)/(total*100),"Project %d of %d - %s (%d%%)"%(i+1,total,name,fp)))
        self.proc.wait()
        if self.proc.returncode not in (0,None) and not self.cancel: raise RuntimeError("rsync rc=%s"%self.proc.returncode)


    def on_cancel(self):
        self.cancel=True
        if self.proc:
            try: self.proc.terminate()
            except Exception: pass
    def open_folder(self): subprocess.Popen(["xdg-open", self.dest.get() or DEFAULT_DEST])

    # ---- 3D view ----
    def _find_mesh(self, name):
        """Largest mesh for a project: prefer the local flat copy, then a full-import mirror, then the device."""
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        flat=[p for p in glob.glob(os.path.join(local, name+"_*.ply")) if not p.endswith("_cloud.ply")]
        if flat:
            try: return max(flat, key=os.path.getsize)
            except Exception: return flat[0]
        for base in (local, os.path.join(PROJECTS, name)):
            plys=glob.glob(os.path.join(base, "data", "*", "fuse_mesh.ply"))
            if plys:
                try: return max(plys, key=os.path.getsize)
                except Exception: return plys[0]
        return None
    def on_view_3d(self):
        name=self.selected
        if not name: return
        src=self._find_mesh(name)
        if not src:
            self.set_banner("This project has no 3D model yet. Build one first.", WARN); return
        self.set_status("Loading 3D view: reading the model…")
        self._open_loader("Loading 3D view", "Reading the 3D model… large scans take a few seconds.")
        threading.Thread(target=self._view_worker, args=(name, src), daemon=True).start()
    def _view_worker(self, name, src):
        # if the mesh is on the (slow) device mount, copy it to a local cache first
        path=src
        if src.startswith(PROJECTS):
            try:
                cache=os.path.join(THUMBS, "view"); os.makedirs(cache, exist_ok=True)
                path=os.path.join(cache, name+"_fuse_mesh.ply")
                if not os.path.exists(path) or os.path.getsize(path)!=os.path.getsize(src):
                    self.q.put(("loader_msg", "Copying the 3D model from the scanner…"))
                    shutil.copyfile(src, path)
            except Exception as e:
                log_error("view-copy", e); self.q.put(("view_done", None)); return
        try:
            viewer=os.path.join(HERE, "viewer.py")
            proc=subprocess.Popen([_sys.executable, viewer, path, name],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            got=False; tail=[]
            for ln in proc.stdout:
                tail=(tail+[ln.strip()])[-5:]
                if "PYVIEW_READY" in ln: got=True; self.q.put(("view_done", None)); break
                if "PYVIEW_ERROR" in ln: got=True; log_line("viewer: "+ln.strip()); self.q.put(("view_done", ln.strip())); break
            if not got:
                log_line("viewer exited before drawing: %s" % " | ".join(tail))
                self.q.put(("view_done", "The 3D viewer closed before it drew anything (see Help > Log)."))
        except Exception as e:
            log_error("view-launch", e); self.q.put(("view_done", str(e)))

    # ---- base removal (interactive cut-plane) ----
    def on_remove_base(self, node=None):
        if getattr(self, "_basing", False): return
        name=self.selected
        if not name: return
        node=node or self._film_sel
        if node:
            cur=self._proc_current(name, node); src=cur[2] if cur else None       # the selected scan, or the combined model
        else: src=self._find_mesh(name)
        if not src:
            self.set_banner("This scan has no 3D model yet. Build it first.", WARN); return
        if node and os.environ.get("POINTYOINK_NO_GL")!="1" and self.cfg.get("gl_view","auto")!="software":
            self._cut_dialog(name, node, src); return              # the in-app cut view; the matplotlib tool stays as the fallback
        self._basing=True
        try: self.base_btn.configure(state="disabled")
        except Exception: pass
        self.set_status("Base removal: opening the cut-plane tool…")
        self._open_loader("Base removal", "Opening the cut-plane tool… large scans take a few seconds.\nDrag the line to just above the table, then Apply cut. The cut is remembered for this scan.")
        threading.Thread(target=self._base_worker, args=(name, src, node), daemon=True).start()
    def _cut_dialog(self, name, node, src):
        """Remove base inside the app: the scan in the GPU view, the part to keep in grey, the part to remove in red,
        one slider along the table's normal, Flip, Apply. Saves <name>_<node>_clean.ply and remembers the plane."""
        import numpy as np
        t=self._top("Remove base · %s" % self._scan_label(name, node), 980, 780, key="cut")
        if t is None: return
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name); out=os.path.join(local, "%s_%s_clean.ply" % (name, node))
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=12, pady=12)
        card.grid_columnconfigure(0, weight=1); card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(card, text="Grey stays, red goes. Drag the slider until only the table is red. Flip if it picked the wrong side. The cut is remembered for combining.",
                     text_color=MUT, font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=900).grid(row=0,column=0, sticky="w", padx=16, pady=(12,6))
        box=ctk.CTkFrame(card, fg_color="#0a0c10", corner_radius=10); box.grid(row=1,column=0, sticky="nsew", padx=14, pady=4)
        box.grid_columnconfigure(0, weight=1); box.grid_rowconfigure(0, weight=1)
        view=self._new_view(box); view.grid(row=0,column=0, sticky="nsew", padx=4, pady=4)
        if not hasattr(view, "set_colors"):
            t.destroy(); self._dialogs.pop("cut", None); self._basing=True; self._open_loader("Base removal", "Opening the cut-plane tool…")
            threading.Thread(target=self._base_worker, args=(name, src, node), daemon=True).start(); return
        load=ctk.CTkLabel(box, text="Loading the 3D view…", text_color=MUT, font=ctk.CTkFont(size=14), fg_color="#0a0c10"); load.grid(row=0,column=0, sticky="nsew", padx=4, pady=4); load.lift()
        ctl=ctk.CTkFrame(card, fg_color="transparent"); ctl.grid(row=2,column=0, sticky="ew", padx=14, pady=(6,12)); ctl.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ctl, text="Cut height", text_color=MUT, font=ctk.CTkFont(size=12)).grid(row=0,column=0, padx=(4,10))
        slider=ctk.CTkSlider(ctl, from_=0, to=1000, number_of_steps=1000, progress_color=AC, button_color=AC, button_hover_color=AC_H, fg_color="#0d0f14"); slider.grid(row=0,column=1, sticky="ew")
        val=ctk.CTkLabel(ctl, text="", text_color=TX, font=ctk.CTkFont(size=12), width=150); val.grid(row=0,column=2, padx=10)
        flipb=ctk.CTkButton(ctl, text="Flip side", width=90, height=32, corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX); flipb.grid(row=0,column=3, padx=4)
        cancelb=ctk.CTkButton(ctl, text="Cancel", width=90, height=32, corner_radius=16, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=TX); cancelb.grid(row=0,column=4, padx=4)
        applyb=ctk.CTkButton(ctl, text="✂  Apply cut", width=130, height=32, corner_radius=16, fg_color=AC, hover_color=AC_H, text_color="#04121f", font=ctk.CTkFont(size=12, weight="bold")); applyb.grid(row=0,column=5, padx=(4,0))
        status=ctk.CTkLabel(card, text="", text_color=MUT, font=ctk.CTkFont(size=11), anchor="w"); status.grid(row=3,column=0, sticky="w", padx=16, pady=(0,10))
        st={"n":None, "H":None, "Hmin":0.0, "Hmax":1.0, "cut":0.0, "keep_above":True, "V":None, "job":None, "busy":False}
        KEEP=np.array([0.74,0.76,0.80], np.float32); GONE=np.array([1.0,0.36,0.42], np.float32)
        def close():
            self._dialogs.pop("cut", None); t.destroy()
        t.protocol("WM_DELETE_WINDOW", close); cancelb.configure(command=close)
        def paint():
            st["job"]=None
            if st["H"] is None: return
            keep=(st["H"]>st["cut"]) if st["keep_above"] else (st["H"]<st["cut"])
            cols=np.where(keep[:,None], KEEP, GONE).astype(np.float32); view.set_colors(cols)
            import shade
            n=st["n"]; V=st["V"]; c_w=V.mean(0)+n*(st["cut"]-V.mean(0).dot(n))
            cv=shade.world_to_view(c_w, view.tf); nv=shade.world_to_view(c_w+n*10.0, view.tf)-cv
            view.plane=(cv, nv if st["keep_above"] else -nv, 1.1); view.draw()
            val.configure(text="%.1f mm · %d%% removed" % (st["cut"]-st["Hmin"], 100-int(keep.mean()*100)))
        def schedule():
            if st["job"] is None: st["job"]=t.after(60, paint)
        def on_slide(v):
            st["cut"]=st["Hmin"]+(st["Hmax"]-st["Hmin"])*float(v)/1000.0; schedule()
        slider.configure(command=on_slide)
        def flip(): st["keep_above"]=not st["keep_above"]; schedule()
        flipb.configure(command=flip)
        def ready(ok):
            if not t.winfo_exists(): return
            if not ok or getattr(view, "_src", None) is None or view.tf is None:
                load.configure(text="Could not load this model"); return
            def work():
                try:
                    import shade, cutplane
                    v_view, f = view._src; V=shade.view_to_world(v_view, view.tf); rng=np.random.default_rng(0)
                    n=cutplane.ransac_normal(V, rng); H=V.dot(n)
                    # the table is the densest height; point the normal so the object sits above it
                    hist, edges=np.histogram(H, bins=120); h_tab=float(0.5*(edges[hist.argmax()]+edges[hist.argmax()+1]))
                    if H.mean() < h_tab: n=-n; H=-H; h_tab=-h_tab
                    res=(n, H, V, h_tab)
                except Exception as e: log_error("cut setup", e); res=None
                def done():
                    if not t.winfo_exists(): return
                    if res is None: load.configure(text="Could not find the table in this scan"); return
                    n, H, V, h_tab = res; st["n"]=n; st["H"]=H; st["V"]=V; st["Hmin"]=float(H.min()); st["Hmax"]=float(H.max())
                    st["cut"]=min(st["Hmax"], h_tab+0.02*(st["Hmax"]-st["Hmin"])); st["keep_above"]=True     # just above the table
                    slider.set(1000.0*(st["cut"]-st["Hmin"])/max(1e-6, st["Hmax"]-st["Hmin"])); load.grid_remove(); paint()
                    status.configure(text="Starting just above the flattest surface. %s" % ("Drag to rotate, scroll to zoom." ))
                self.q.put(("call", done))
            threading.Thread(target=work, daemon=True).start()
        view.load(src, ready, max_faces=600000)
        def apply():
            if st["H"] is None or st["busy"]: return
            st["busy"]=True; applyb.configure(state="disabled"); status.configure(text="Cutting the full model… (a big scan takes a few seconds)")
            n=st["n"]; spec="%.6f,%.6f,%.6f,%.4f,%s" % (n[0], n[1], n[2], st["cut"], "1" if st["keep_above"] else "0")
            def work():
                ok=False; plane=None
                try:
                    env=dict(os.environ, OPENBLAS_NUM_THREADS="1"); env.setdefault("POINTYOINK_MEM_CAP_GB", "10")
                    r=subprocess.run([_sys.executable, os.path.join(HERE, "cutplane.py"), src, out, "--plane", spec], capture_output=True, text=True, timeout=1800, env=env)
                    for ln in r.stdout.splitlines():
                        if ln.startswith("CUT_DONE"):
                            ok=True
                            try: plane=json.loads(ln[9:]).get("plane")
                            except Exception: plane=None
                    if not ok: log_line("cut failed: %s" % (r.stdout+r.stderr)[-400:])
                except Exception as e: log_error("cut", e)
                def done():
                    st["busy"]=False
                    if ok:
                        self.q.put(("base_done", ("ok", out, node, plane)))
                        if t.winfo_exists(): close()
                    elif t.winfo_exists(): applyb.configure(state="normal"); status.configure(text="The cut failed (see Help > Log).", text_color=WARN)
                self.q.put(("call", done))
            threading.Thread(target=work, daemon=True).start()
        applyb.configure(command=apply)
    def _base_worker(self, name, src, node=None):
        path=src; dest=self.dest.get() or DEFAULT_DEST; outdir=os.path.join(dest, name)
        if src.startswith(PROJECTS):   # on the slow device mount - copy locally first
            try:
                os.makedirs(outdir, exist_ok=True)
                path=os.path.join(outdir, name+"_fuse_mesh.ply")
                if not os.path.exists(path) or os.path.getsize(path)!=os.path.getsize(src):
                    self.q.put(("loader_msg", "Copying the 3D model from the scanner…"))
                    shutil.copyfile(src, path)
            except Exception as e:
                log_error("base-copy", e); self.q.put(("base_done", ("err", "copy failed"))); return
        out=os.path.join(outdir, "%s_%s_clean.ply" % (name, node)) if node else os.path.splitext(path)[0]+"_clean.ply"
        try:
            os.makedirs(outdir, exist_ok=True)
            tool=os.path.join(HERE, "cutplane.py")
            env=dict(os.environ, OPENBLAS_NUM_THREADS="1",
                     POINTYOINK_MEM_CAP_GB=os.environ.get("POINTYOINK_MEM_CAP_GB", "10"))
            proc=subprocess.Popen([_sys.executable, tool, path, out],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
            for ln in proc.stdout:
                ln=ln.strip()
                if ln.startswith("CUT_READY"): self.q.put(("loader_close", None))
                elif ln.startswith("CUT_DONE"):
                    try: payload=json.loads(ln[9:])
                    except Exception: payload={}
                    self.q.put(("base_done", ("ok", out, node, payload.get("plane")))); break
                elif ln.startswith("CUT_CANCELLED"): self.q.put(("base_done", ("cancel", None))); break
                elif ln.startswith("CUT_ERROR"): log_line("cutplane: "+ln); self.q.put(("base_done", ("err", ln))); break
        except Exception as e:
            log_error("base-launch", e); self.q.put(("base_done", ("err", str(e))))

    # ---- process on PC: raw depth frames -> fused mesh, via fuse.py (Open3D TSDF) ----
    # ---- Process page ----
    PROC_DETAIL=(("Fast (0.6 mm)",0.6),("Normal (0.4 mm, like the scanner)",0.4),("Fine (0.3 mm, ~2x memory)",0.3),("Ultra (0.2 mm, ~6x memory)",0.2))
    def _build_process_page(self, pr):
        pr.grid_columnconfigure(0, weight=1); pr.grid_rowconfigure(1, weight=1)
        ph=ctk.CTkFrame(pr, fg_color="transparent"); ph.grid(row=0,column=0, sticky="ew", padx=16, pady=(14,4))
        ctk.CTkLabel(ph, text="Prepare", font=ctk.CTkFont(size=18, weight="bold"), text_color=TX).pack(side="left")
        self.proc_pick=ctk.CTkOptionMenu(ph, values=["No projects on this PC yet"], width=230, command=self._proc_pick, fg_color="#0d0f14", button_color=CARD2,
                                         button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=10)
        self.proc_pick.pack(side="left", padx=(16,8))
        self._tip(self.proc_pick, "Which project to work on. Same selection as the Import tab.")
        self.proc_title=ctk.CTkLabel(ph, text="", text_color=MUT, font=ctk.CTkFont(size=12)); self.proc_title.pack(side="left")
        ctk.CTkButton(ph, text="Delete project from this PC", width=190, height=30, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE,
                      hover_color="#3a2530", text_color=MUT, command=self._proc_delete_project).pack(side="right")
        self.base_btn=ctk.CTkButton(ph, text="✂  Remove base", width=130, height=30, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE,
                                    hover_color=CARD2, text_color=TX, command=self.on_remove_base); self.base_btn.pack(side="right", padx=8)
        self._tip(self.base_btn, "Slice the table or turntable off the current scan with a cut plane. Saves a cleaned copy; the original is kept.")
        self.align_btn=ctk.CTkButton(ph, text="⧉  Combine scans…", width=150, height=30, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE,
                                     hover_color=CARD2, text_color=TX, command=lambda: self._align_dialog(self.selected)); self.align_btn.pack(side="right", padx=8)
        self._tip(self.align_btn, "Scanned each side separately? Line the scans up on matching points (or automatically) and build one model from all of them.")
        self.proc_btn=ctk.CTkButton(ph, text="⚙  Build all models", width=150, height=30, corner_radius=8, fg_color="transparent", border_width=1, border_color=AC,
                                    hover_color=CARD2, text_color=AC, command=self.on_process_pc); self.proc_btn.pack(side="right", padx=8)
        self._tip(self.proc_btn, "Build the 3D model of every scan that has raw scan data, on this PC.")
        dr=ctk.CTkFrame(pr, fg_color="transparent"); dr.grid(row=2,column=0, sticky="ew", padx=16, pady=(0,10))
        ctk.CTkLabel(dr, text="Detail when building", text_color=MUT, font=ctk.CTkFont(size=12)).pack(side="left")
        cur=float(self.cfg.get("fuse_voxel",0.4) or 0.4); lab=min(self.PROC_DETAIL, key=lambda d: abs(d[1]-cur))[0]   # the vars come later
        self.proc_detail=ctk.CTkOptionMenu(dr, values=[d[0] for d in self.PROC_DETAIL], width=300, command=self._proc_detail_changed, fg_color="#0d0f14", button_color=CARD2,
                                           button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=10)
        self.proc_detail.pack(side="left", padx=10); self.proc_detail.set(lab)
        ctk.CTkLabel(dr, text="Normal matches the scanner. Finer takes longer and needs more graphics memory (about 2 GB per scan at Normal).",
                     text_color=DIM, font=ctk.CTkFont(size=10)).pack(side="left")
        self.proc_cards=ctk.CTkScrollableFrame(pr, fg_color="transparent"); self.proc_cards.grid(row=1,column=0, sticky="nsew", padx=10)
        self.proc_cards.grid_columnconfigure(0, weight=1)
        self.tools=ctk.CTkFrame(pr, fg_color="transparent", height=1); self.tools.grid(row=4,column=0); self.tools.grid_remove()   # kept for older call sites
        self._proc_rows={}; self._proc_names=[]
        self._proc_empty=self._empty_state(self.proc_cards, "projects"); self._proc_empty.grid(row=0,column=0, sticky="nsew", pady=40)
    def _proc_detail_changed(self, label):
        for l,v in self.PROC_DETAIL:
            if l==label: self.fuse_voxel.set(v)
    def _proc_pick(self, label):
        for n in self._proc_names:
            if self.disp(n)==label: self.select_project(n); return
    def _proc_nodes(self, name):
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name); nodes=set()
        for d in glob.glob(os.path.join(local, "data", "*")):
            if os.path.isdir(d): nodes.add(os.path.basename(d))
        for f in glob.glob(os.path.join(local, name+"_*.ply")):
            n=os.path.basename(f)[len(name)+1:-4]
            for suf in ("_cloud","_pcfused","_clean"):
                if n.endswith(suf): n=n[:-len(suf)]
            nodes.add(n)
        return sorted(nodes)
    def _proc_versions(self, name, node):
        """The model files a scan has on this PC: [(key, label, path)] in default preference order."""
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name); out=[]
        for key,label,cands in (("clean","prepared here",[os.path.join(local,"%s_%s_clean.ply"%(name,node)), os.path.join(local,"%s_%s_pcfused_clean.ply"%(name,node))]),
                                ("scanner","from the scanner",[os.path.join(local,"%s_%s.ply"%(name,node)), os.path.join(local,"data",node,"fuse_mesh.ply")]),
                                ("pcfused","built here",[os.path.join(local,"%s_%s_pcfused.ply"%(name,node))])):
            for c in cands:
                if os.path.exists(c) and os.path.getsize(c)>1024: out.append((key,label,c)); break
        return out
    def _proc_current(self, name, node):
        """Which version the preview and exports use for this scan: the user's pick if it still exists, else the first available."""
        vs=self._proc_versions(name, node)
        if not vs: return None
        want=self.records.get(name,{}).get("current",{}).get(node)
        for v in vs:
            if v[0]==want: return v
        return vs[0]
    def _proc_set_current(self, name, node, key):
        self.records.setdefault(name,{}).setdefault("current",{})[node]=key; self._persist(); self._mesh_stats={}
        self._proc_render(name)
        if self.selected==name: self._mv_key=None; self._request_shaded(name, node)
    def _trash(self, path):
        """Move a file or folder to the desktop trash (gio), else into <dest>/.trash."""
        try:
            if subprocess.run(["gio","trash",path], capture_output=True, timeout=30).returncode==0: return True
        except Exception: pass
        try:
            tdir=os.path.join(self.dest.get() or DEFAULT_DEST, ".trash"); os.makedirs(tdir, exist_ok=True)
            shutil.move(path, os.path.join(tdir, time.strftime("%Y%m%d-%H%M%S_")+os.path.basename(path))); return True
        except Exception as e:
            log_error("trash", e); return False
    def _proc_delete_version(self, name, node, key, path):
        if not self._confirm("Delete this version?", "%s: the %s version of scan %s goes to the trash.\nOther versions and the raw data stay." % (self.disp(name), dict(clean="prepared", scanner="scanner's", pcfused="built-here")[key], node)): return
        if self._trash(path):
            self.set_banner("Moved to the trash: %s" % os.path.basename(path), MUT); self._mesh_stats={}; self.gallery_cache.pop(name, None)
            self._proc_render(name)
            if self.selected==name: self._mv_key=None; self.projects_sig=None; self.listed=False; self.start_listing()
    def _proc_delete_project(self):
        name=self.selected
        if not name: return
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        if not os.path.isdir(local): self.set_banner("That project is not on this PC.", WARN); return
        if not self._confirm("Delete from this PC?", "%s and everything in its folder go to the trash.\nThe copy on the scanner is not touched." % self.disp(name)): return
        if self._trash(local):
            self.set_banner("Moved to the trash: %s" % self.disp(name), MUT); self.selected=None; self.gallery_cache.pop(name, None)
            self.projects_sig=None; self.listed=False; self.start_listing(); self._proc_render(None)
    def _proc_refresh(self):
        if not hasattr(self, "proc_pick"): return
        dest=self.dest.get() or DEFAULT_DEST
        self._proc_names=[p["name"] for p in self.projects if os.path.isdir(os.path.join(dest, p["name"]))]
        self.proc_pick.configure(values=[self.disp(n) for n in self._proc_names] or ["No projects on this PC yet"])
        name=self.selected if self.selected in self._proc_names else (self._proc_names[0] if self._proc_names else None)
        self.proc_pick.set(self.disp(name) if name else "No projects on this PC yet")
        self._proc_render(name)
    def _proc_render(self, name):
        if getattr(self, "page", "import")=="projects" and hasattr(self, "projpanel"): self._panel_refresh()
        for w in self.proc_cards.winfo_children():
            if w is not self._proc_empty: w.destroy()
        self._proc_rows={}
        if not name:
            self.proc_title.configure(text=""); self._proc_empty.grid(); return
        self._proc_empty.grid_remove()
        nodes=self._proc_nodes(name); local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        self.proc_title.configure(text="%d scan%s · %s" % (len(nodes), "" if len(nodes)==1 else "s", name if self.disp(name)!=name else ""))
        self._proc_next_strip(name, nodes, local)
        for i,node in enumerate(nodes):
            vs=self._proc_versions(name, node); cur=self._proc_current(name, node)
            raw=len(glob.glob(os.path.join(local, "data", node, "cache", "*.dph")))
            card=ctk.CTkFrame(self.proc_cards, fg_color=CARD, corner_radius=14); card.grid(row=i+1, column=0, sticky="ew", padx=6, pady=6)
            card.grid_columnconfigure(1, weight=1)
            thumb=os.path.join(local, "data", node, "preview.png")
            if not os.path.exists(thumb): thumb=os.path.join(local, "%s_%s.png" % (name, node))
            tl=ctk.CTkLabel(card, text="", fg_color="#0a0c10", corner_radius=8, width=110, height=70); tl.grid(row=0,column=0, rowspan=3, padx=(14,12), pady=12)
            if os.path.exists(thumb):
                try: self.imgs["proc_"+node]=cimg(thumb, 110); tl.configure(image=self.imgs["proc_"+node])
                except Exception: pass
            elif cur: self._card_thumb(name, node, cur[2], tl)      # no scanner picture (a model built or combined here): render one
            top=ctk.CTkFrame(card, fg_color="transparent"); top.grid(row=0,column=1, sticky="ew", pady=(12,0))
            ctk.CTkLabel(top, text=self._scan_label(name, node), font=ctk.CTkFont(size=14, weight="bold"), text_color=TX).pack(side="left")
            ctk.CTkLabel(top, text=(node if node!="combined" else "all aligned scans in one model"), text_color=MUT, font=ctk.CTkFont(size=11)).pack(side="left", padx=10)
            if node=="combined": status="%d version%s · built from the scans you lined up" % (len(vs), "" if len(vs)==1 else "s")
            else: status=("no 3D model yet · %d raw frames" % raw) if not vs else ("%d version%s · %d raw frames" % (len(vs), "" if len(vs)==1 else "s", raw) if raw else "%d version%s · no raw data on this PC" % (len(vs), "" if len(vs)==1 else "s"))
            ctk.CTkLabel(top, text=status, text_color=(WARN if not vs else MUT), font=ctk.CTkFont(size=11)).pack(side="left", padx=6)
            if node!="combined":
                stw, stc = self.STAGE_WORDS[self._device_stage(local, node)]
                if stw: ctk.CTkLabel(top, text="· scanner: "+stw, text_color=stc, font=ctk.CTkFont(size=11)).pack(side="left")
            vr=ctk.CTkFrame(card, fg_color="transparent"); vr.grid(row=1,column=1, sticky="ew", pady=(6,0))
            ctk.CTkLabel(vr, text="Versions:" if vs else "", text_color=MUT, font=ctk.CTkFont(size=11)).pack(side="left", padx=(0,6))
            for key,label,path in vs:
                is_cur=(cur and cur[0]==key)
                chip=ctk.CTkFrame(vr, fg_color=("#15304d" if is_cur else CARD2), corner_radius=9); chip.pack(side="left", padx=3)
                b=ctk.CTkButton(chip, text=("✓ " if is_cur else "")+label, height=22, corner_radius=9, fg_color="transparent", hover_color=STROKE,
                                text_color=(AC if is_cur else TX), font=ctk.CTkFont(size=11), command=lambda n=name,nd=node,k=key: self._proc_set_current(n, nd, k)); b.pack(side="left", padx=(6,0))
                self._tip(b, "%s · %s\nClick to make this the version the preview and exports use." % (os.path.basename(path), human(os.path.getsize(path))))
                x=ctk.CTkButton(chip, text="✕", width=22, height=22, corner_radius=9, fg_color="transparent", hover_color="#3a2530", text_color=MUT,
                                font=ctk.CTkFont(size=11), command=lambda n=name,nd=node,k=key,pth=path: self._proc_delete_version(n, nd, k, pth)); x.pack(side="left")
                self._tip(x, "Delete this version (to the trash)")
            act=ctk.CTkFrame(card, fg_color="transparent"); act.grid(row=0,column=2, rowspan=2, padx=14, pady=12, sticky="e")
            has_prep=any(k=="clean" for k,_,_ in vs)
            # one obvious next step per scan: build if there is nothing yet, prepare once there is a model, export once prepared
            primary="build" if (raw and not vs) else ("prepare" if (vs and not has_prep) else ("export" if vs else None))
            def mk(kind, text, tip, enabled, cmd):
                filled=(kind==primary and enabled)
                b=ctk.CTkButton(act, text=text, width=150, height=30, corner_radius=8, fg_color=(AC if filled else "transparent"),
                                hover_color=(AC_H if filled else CARD2), border_width=(0 if filled else 1), border_color=STROKE,
                                text_color=("#04121f" if filled else (TX if enabled else MUT)), state=("normal" if enabled else "disabled"), command=cmd)
                b.pack(side="top", fill="x", pady=2); self._tip(b, tip); return b
            bb=mk("build", "⚙  Build model", "Build this scan's 3D model from its raw data, on this PC." if raw else "No raw scan data on this PC for this scan (share the project over WiFi as Full project).",
                  bool(raw), lambda n=name,nd=node: self._proc_build(n, [nd]))
            if node=="combined": bb.pack_forget()                     # the combined model is rebuilt from the Combine window, not here
            cb=mk("prepare", "✦  Prepare…", "Remove floating pieces, smooth the surface, fill small holes, reduce triangles. You see before and after, then keep or discard.",
                  bool(vs), lambda n=name,nd=node: self._prepare_dialog(n, nd))
            xb=mk("export", "⬆  Export…", "Save this scan as STL, OBJ, GLB or PLY, with its size and a mesh check.", bool(vs), lambda n=name,nd=node: self._export_dialog(n, nd))
            pb=ctk.CTkProgressBar(card, height=6, corner_radius=3, progress_color=AC, fg_color="#0d0f14"); pb.set(0)
            pl=ctk.CTkLabel(card, text="", text_color=MUT, font=ctk.CTkFont(size=11), anchor="w")
            self._proc_rows[node]={"card":card, "bar":pb, "lbl":pl, "build":bb, "prepare":cb, "export":xb}
    def _card_thumb(self, name, node, path, label):
        """Small shaded render for a card without a scanner picture, cached under THUMBS, made in a thread."""
        key="%s__%s__card" % (name, node); out=os.path.join(THUMBS, key+".png")
        def put():
            try:
                if label.winfo_exists(): self.imgs["proc_"+node]=cimg(out, 110); label.configure(image=self.imgs["proc_"+node])
            except Exception: pass
        if os.path.exists(out) and os.path.getmtime(out)>=os.path.getmtime(path): put(); return
        def work():
            try:
                import shade; os.makedirs(THUMBS, exist_ok=True)
                v,f=shade.load_oriented(path, 150000); shade.render(v, f, size=(330, 210), grid=False, gizmo=False).save(out)
                self.q.put(("call", put))
            except Exception as e: log_error("card thumb", e)
        threading.Thread(target=work, daemon=True).start()
    STEPS=("Build", "Cut base", "Combine", "Prepare", "Export")
    def _base_planes(self, name): return self.records.get(name,{}).get("base_plane",{}) or {}
    def _proc_next(self, name, nodes, local):
        """What to do now for this project: (title, detail, button text, command, step index into STEPS)."""
        scans=[n for n in nodes if n!="combined"]
        unbuilt=[n for n in scans if not self._proc_versions(name, n) and glob.glob(os.path.join(local,"data",n,"cache","*.dph"))]
        built=[n for n in scans if self._proc_versions(name, n)]
        planes=self._base_planes(name); nobase=[n for n in built if n not in planes]
        if built and nobase:
            n0=nobase[0]
            def go(n=n0): self._pick_scan_by_node(name, n); self.on_remove_base(n)
            return ("Cut the base off %s" % self._scan_label(name, n0),
                    "%d of %d scan%s still %s the table under the part. Drag one line above it and apply. The cut is remembered and applied again when the scans are combined, so the base never gets fused in." % (len(nobase), len(built), "" if len(built)==1 else "s", "has" if len(nobase)==1 else "have"),
                    "✂  Remove base on %s" % self._scan_label(name, n0), go, 1)
        if unbuilt:
            return ("Build the 3D model%s" % ("" if len(unbuilt)==1 else "s"),
                    "%d scan%s %s raw data only. Easiest is One-tap Edit on the scanner, then share the project again. Or build here now (seconds on a graphics card) and prepare it yourself." % (len(unbuilt), "" if len(unbuilt)==1 else "s", "has" if len(unbuilt)==1 else "have"),
                    "⚙  Build %d model%s here" % (len(unbuilt), "" if len(unbuilt)==1 else "s"), lambda: self._proc_build(name, unbuilt), 0)
        target="combined" if "combined" in nodes else (built[0] if built else None)
        if len(built)>=2 and "combined" not in nodes:
            return ("Line up the scans and build one model", "You scanned %d sides. Click matching spots on two scans at a time, then build one model from all of them." % len(built),
                    "⧉  Combine scans…", lambda: self._align_dialog(name), 2)
        if not target: return ("Nothing to prepare yet", "Share this project over WiFi as Full project to get its raw data, or plug the scanner in.", None, None, 0)
        vs=self._proc_versions(name, target); lab=self._scan_label(name, target)
        if not any(k=="clean" for k,_,_ in vs):
            return ("Prepare the %s model" % lab.lower() if target=="combined" else "Prepare %s" % lab, "Remove floating pieces, smooth, fill holes. You see before and after and keep or discard.",
                    "✦  Prepare…", lambda: self._prepare_dialog(name, target), 3)
        return ("Export", "%s is prepared. Save it as STL for a slicer, or OBJ, GLB, PLY." % lab, "⬆  Export…", lambda: self._export_dialog(name, target), 4)
    def _pick_scan_by_node(self, name, node):
        """Select a scan tile the way a click on the strip would (so Remove base and the preview follow)."""
        if self.selected!=name: self.select_project(name)
        self._film_sel=node; self._mark_scan(node)
        try: self._request_shaded(name, node)
        except Exception: pass
    HOWTO=(("Import", "⬇", "Get the project off the scanner: USB lists everything on it, WiFi Share to PC sends one project. Finished models is quick; Full project also brings the raw frames you need for building and combining here."),
           ("Build", "⚙", "A scan is raw frames until something fuses them into a 3D model. The scanner does that with One-tap Edit; this PC does it with Build, in seconds on a graphics card, using the scanner's own registration. Easiest: One-tap Edit on the scanner when it turns out fine, Build here when it does not."),
           ("Cut base", "✂", "Every scan carries the table under the part. Drag one line above it and apply. The cut is remembered for that scan and applied again when scans are combined, so the table never gets fused in."),
           ("Combine", "⧉", "Scanned each side separately? Pick a base scan, click three to five matching spots on it and on another scan, Line up, check the orange overlay, Keep. Repeat for each side, then Build one model from all their frames at once. Your points stay editable."),
           ("Prepare", "✦", "Remove floating pieces, smooth, fill small holes, reduce triangles. It runs on a copy and shows before and after; Keep or Discard. Once Combined exists, prepare that one."),
           ("Export", "⬆", "Pick the version, the format (STL for slicers, OBJ, GLB, PLY) and the folder. The size and a mesh check are shown first: open edges and extra pieces mean the surface is not closed."))
    def _howto_dialog(self):
        t=self._top("How PointYoink works", 700, 640, key="howto")
        if t is None: return
        box=ctk.CTkScrollableFrame(t, fg_color="transparent"); box.pack(fill="both", expand=True, padx=12, pady=(12,0))
        ctk.CTkLabel(box, text="Scan to model, in five steps", text_color=TX, font=ctk.CTkFont(size=17, weight="bold"), anchor="w").pack(fill="x", padx=10, pady=(6,2))
        ctk.CTkLabel(box, text="The NEXT bar on the Projects page always shows which step you are on and does it with one button. Originals are never changed: every step saves a new version.",
                     text_color=MUT, font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=620).pack(fill="x", padx=10, pady=(0,10))
        for i,(nm,ico,txt) in enumerate(self.HOWTO):
            card=ctk.CTkFrame(box, fg_color=CARD, corner_radius=12); card.pack(fill="x", padx=6, pady=4)
            ctk.CTkLabel(card, text="%s  %d · %s" % (ico, i, nm) if i else "%s  %s" % (ico, nm), text_color=AC, font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(fill="x", padx=14, pady=(10,2))
            ctk.CTkLabel(card, text=txt, text_color=TX, font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=600).pack(fill="x", padx=14, pady=(0,10))
        row=ctk.CTkFrame(t, fg_color="transparent"); row.pack(fill="x", padx=12, pady=10)
        def ok(): self.cfg["seen_howto"]=True; save_cfg(self.cfg); self._dialogs.pop("howto", None); t.destroy()
        ctk.CTkButton(row, text="Got it", width=110, height=34, corner_radius=17, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=ok).pack(side="right")
        ctk.CTkLabel(row, text="Open this again any time from the ? button or the NEXT bar.", text_color=DIM, font=ctk.CTkFont(size=11)).pack(side="left", padx=6)
        t.protocol("WM_DELETE_WINDOW", ok)
    def _next_refresh(self, name=None, nodes=None, local=None):
        """The NEXT bar under the project title on the Projects page: what to do now, the step trail, one button."""
        ns=self.next_strip
        for w in ns.winfo_children(): w.destroy()
        if self.page!="projects" or not name: ns.pack_forget(); return
        title, detail, btxt, cmd, step = self._proc_next(name, nodes, local)
        ns.pack(fill="x", pady=(2,6)); ns.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ns, text="NEXT", text_color=AC, font=ctk.CTkFont(size=10, weight="bold")).grid(row=0,column=0, padx=(14,10), pady=(10,0), sticky="w")
        hb=ctk.CTkButton(ns, text="how this works", width=90, height=20, corner_radius=6, fg_color="transparent", hover_color="#15304d", text_color=DIM, font=ctk.CTkFont(size=10), command=self._howto_dialog)
        hb.grid(row=2,column=0, padx=(8,0), pady=(0,10), sticky="w")
        ctk.CTkLabel(ns, text=title, text_color=TX, font=ctk.CTkFont(size=14, weight="bold"), anchor="w").grid(row=0,column=1, sticky="w", pady=(10,0))
        ctk.CTkLabel(ns, text=detail, text_color=MUT, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=640).grid(row=1,column=1, columnspan=2, sticky="w", padx=(0,14), pady=(0,2))
        trail=ctk.CTkFrame(ns, fg_color="transparent"); trail.grid(row=2,column=1, columnspan=2, sticky="w", pady=(0,10))
        for i,nm in enumerate(self.STEPS):
            col=(OK if i<step else (AC if i==step else DIM)); mark=("✓ " if i<step else ("▶ " if i==step else ""))
            ctk.CTkLabel(trail, text=mark+nm, text_color=col, font=ctk.CTkFont(size=11, weight=("bold" if i==step else "normal"))).pack(side="left")
            if i<len(self.STEPS)-1: ctk.CTkLabel(trail, text="  →  ", text_color=DIM, font=ctk.CTkFont(size=11)).pack(side="left")
        if btxt: ctk.CTkButton(ns, text=btxt, width=210, height=34, corner_radius=17, fg_color=AC, hover_color=AC_H, text_color="#04121f", font=ctk.CTkFont(size=13, weight="bold"), command=cmd).grid(row=0,column=2, padx=14, pady=(8,0), sticky="e")
    def _proc_next_strip(self, name, nodes, local):
        title, detail, btxt, cmd, step = self._proc_next(name, nodes, local)
        strip=ctk.CTkFrame(self.proc_cards, fg_color="#0f1a2b", corner_radius=14, border_width=1, border_color="#1f3a5f"); strip.grid(row=0, column=0, sticky="ew", padx=6, pady=(4,10))
        strip.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(strip, text="NEXT", text_color=AC, font=ctk.CTkFont(size=11, weight="bold")).grid(row=0,column=0, padx=(16,10), pady=(12,0), sticky="w")
        ctk.CTkLabel(strip, text=title, text_color=TX, font=ctk.CTkFont(size=15, weight="bold"), anchor="w").grid(row=0,column=1, sticky="w", pady=(12,0))
        ctk.CTkLabel(strip, text=detail, text_color=MUT, font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=640).grid(row=1,column=1, sticky="w", pady=(0,4))
        trail=ctk.CTkFrame(strip, fg_color="transparent"); trail.grid(row=2,column=1, sticky="w", pady=(0,12))
        for i,nm in enumerate(self.STEPS):
            col=(OK if i<step else (AC if i==step else DIM)); mark=("✓ " if i<step else ("▶ " if i==step else ""))
            ctk.CTkLabel(trail, text=mark+nm, text_color=col, font=ctk.CTkFont(size=11, weight=("bold" if i==step else "normal"))).pack(side="left")
            if i<len(self.STEPS)-1: ctk.CTkLabel(trail, text="  →  ", text_color=DIM, font=ctk.CTkFont(size=11)).pack(side="left")
        if btxt:
            ctk.CTkButton(strip, text=btxt, width=190, height=36, corner_radius=18, fg_color=AC, hover_color=AC_H, text_color="#04121f", font=ctk.CTkFont(size=13, weight="bold"), command=cmd).grid(row=0,column=2, rowspan=3, padx=16, pady=12)
    def _panel_refresh(self):
        """The right column on the Projects page: what to do next, the selected scan's versions and actions, project actions."""
        pp=self.projpanel
        for w in pp.winfo_children(): w.destroy()
        name=self.selected; dest=self.dest.get() or DEFAULT_DEST; local=os.path.join(dest, name) if name else None
        if not name or not local or not os.path.isdir(local):
            ctk.CTkLabel(pp, text="Pick a project on the left.", text_color=MUT, font=ctk.CTkFont(size=12)).pack(anchor="w", padx=16, pady=20); return
        nodes=self._proc_nodes(name)
        self._next_refresh(name, nodes, local)
        ctk.CTkFrame(pp, fg_color="transparent", height=6).pack()
        # the selected scan
        node=self._film_sel if self._film_sel in nodes else (nodes[0] if nodes else None)
        if node:
            vs=self._proc_versions(name, node); cur=self._proc_current(name, node)
            raw=len(glob.glob(os.path.join(local, "data", node, "cache", "*.dph"))); has_prep=any(k=="clean" for k,_,_ in vs)
            self._title(pp, self._scan_label(name, node), size=14, pady=(10,0))
            sub=("built from the scans you lined up" if node=="combined" else ("%d raw frames on this PC" % raw if raw else "no raw data on this PC"))
            ctk.CTkLabel(pp, text=sub, text_color=MUT, font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", padx=6)
            if node!="combined":
                stw, stc = self.STAGE_WORDS[self._device_stage(local, node)]
                if stw: ctk.CTkLabel(pp, text="Scanner: "+stw, text_color=stc, font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", padx=6)
                hasp=node in self._base_planes(name)
                ctk.CTkLabel(pp, text=("Base cut saved ✓ (applied when combining)" if hasp else "Base not cut yet"), text_color=(OK if hasp else WARN), font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", padx=6)
            if vs:
                ctk.CTkLabel(pp, text="Versions (tick = the one the preview and exports use)", text_color=DIM, font=ctk.CTkFont(size=10), anchor="w").pack(fill="x", padx=6, pady=(8,2))
                for key,label,path in vs:
                    is_cur=(cur and cur[0]==key)
                    chip=ctk.CTkFrame(pp, fg_color=("#15304d" if is_cur else CARD2), corner_radius=9); chip.pack(fill="x", padx=6, pady=2)
                    b=ctk.CTkButton(chip, text=("✓ " if is_cur else "")+label+"  ·  "+human(os.path.getsize(path)), height=24, corner_radius=9, fg_color="transparent", hover_color=STROKE, anchor="w",
                                    text_color=(AC if is_cur else TX), font=ctk.CTkFont(size=11), command=lambda n=name,nd=node,k=key: self._proc_set_current(n, nd, k)); b.pack(side="left", fill="x", expand=True, padx=(6,0))
                    x=ctk.CTkButton(chip, text="✕", width=24, height=24, corner_radius=9, fg_color="transparent", hover_color="#3a2530", text_color=MUT, font=ctk.CTkFont(size=11),
                                    command=lambda n=name,nd=node,k=key,pth=path: self._proc_delete_version(n, nd, k, pth)); x.pack(side="right")
                    self._tip(x, "Delete this version (to the trash)")
            else: ctk.CTkLabel(pp, text="No 3D model yet", text_color=WARN, font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", padx=6, pady=(6,0))
            combined_exists=("combined" in nodes and node!="combined")
            primary="build" if (raw and not vs) else ("cut" if (vs and node!="combined" and node not in self._base_planes(name)) else (None if combined_exists else ("prepare" if (vs and not has_prep) else ("export" if vs else None))))
            def mk(kind, text, enabled, cmd, tip):
                filled=(kind==primary and enabled)
                b=ctk.CTkButton(pp, text=text, height=32, corner_radius=8, fg_color=(AC if filled else "transparent"), hover_color=(AC_H if filled else CARD2), border_width=(0 if filled else 1), border_color=STROKE,
                                text_color=("#04121f" if filled else (TX if enabled else MUT)), state=("normal" if enabled else "disabled"), anchor="w", command=cmd)
                b.pack(fill="x", padx=6, pady=(6,0)); self._tip(b, tip); return b
            if node!="combined": mk("build", "⚙  Build model", bool(raw), lambda n=name,nd=node: self._proc_build(n, [nd]), "Build this scan's 3D model from its raw data, on this PC." if raw else "No raw data on this PC for this scan (share the project over WiFi as Full project).")
            if node!="combined": mk("cut", "✂  Remove base…", bool(vs), lambda nd=node: self.on_remove_base(nd), "Drag one line just above the table and apply. Saves a prepared version and remembers the cut for combining.")
            aside="This project has a Combined model: prepare and export that one (pick the Combined tile). The cards page still allows it per scan."
            mk("prepare", "✦  Prepare…", bool(vs) and not combined_exists, lambda n=name,nd=node: self._prepare_dialog(n, nd), aside if combined_exists else "Remove floating pieces, smooth, fill holes, reduce triangles. Before and after, then keep or discard.")
            mk("export", "⬆  Export…", bool(vs) and not combined_exists, lambda n=name,nd=node: self._export_dialog(n, nd), aside if combined_exists else "Save as STL, OBJ, GLB or PLY with a size and mesh check.")
            if combined_exists: ctk.CTkLabel(pp, text="Combined exists: prepare and export it instead of single scans.", text_color=DIM, font=ctk.CTkFont(size=10), anchor="w", justify="left", wraplength=230).pack(fill="x", padx=6, pady=(4,0))
        self._hr(pp, pady=(14,6)); self._title(pp, "Whole project", size=13)
        def act(text, cmd, tip=None, danger=False):
            b=ctk.CTkButton(pp, text=text, height=30, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=("#3a2530" if danger else CARD2), text_color=(MUT if danger else TX), anchor="w", command=cmd)
            b.pack(fill="x", padx=6, pady=3)
            if tip: self._tip(b, tip)
        act("⇆  Compare versions…", lambda: self._compare_dialog(name), "Two 3D views side by side, any scan or version in each, turning together.")
        act("⧉  Combine scans…", lambda: self._align_dialog(name), "Scanned each side separately? Line the scans up and build one model from all of them.")
        act("⚙  Build all models", self.on_process_pc, "Build the 3D model of every scan that has raw data.")
        act("▤  All scans as cards…", lambda: self._set_mode("Process"), "The detail page: every scan with its versions and actions.")
        act("🗑  Delete project from this PC", self._proc_delete_project, "Everything in its folder goes to the trash. The scanner copy is not touched.", danger=True)
    def _proc_progress(self, node, frac, text):
        r=self._proc_rows.get(node)
        if not r: return
        try:
            if not r["bar"].winfo_manager():
                r["bar"].grid(row=2,column=1, columnspan=2, sticky="ew", padx=(0,14), pady=(8,0)); r["lbl"].grid(row=3,column=1, columnspan=2, sticky="w", pady=(2,10))
            if frac is None: r["bar"].configure(mode="indeterminate"); r["bar"].start()
            else:
                if r["bar"].cget("mode")=="indeterminate": r["bar"].stop(); r["bar"].configure(mode="determinate")
                r["bar"].set(max(0.0, min(1.0, frac)))
            r["lbl"].configure(text=text)
        except Exception: pass
    def _proc_build(self, name, nodes):
        if getattr(self, "_fusing", False): self.set_banner("A build is already running.", WARN); return
        if not _has_open3d():
            self._alert("Open3D needed", "Building models needs Open3D, which isn't installed for this Python.\n\nInstall it with:\n  pip3 install --user --break-system-packages open3d\n\n(~400 MB. The GPU is used automatically when available.)"); return
        self._fusing=True
        try: self.proc_btn.configure(state="disabled")
        except Exception: pass
        for nd in nodes: self._proc_progress(nd, None, "Starting…")
        self.set_status("Building 3D model%s…" % ("" if len(nodes)==1 else "s"))
        threading.Thread(target=self._fuse_worker, args=(name, nodes), daemon=True).start()
    def _device_stage(self, local, node):
        """How far the scanner itself took this scan: 'meshed' (One-tap Edit or Mesh was run there), 'fused'
        (point cloud only), 'raw' (frames only), or None (nothing on this PC for it)."""
        d=os.path.join(local, "data", node)
        if os.path.exists(os.path.join(d, "fuse_mesh.ply")) or os.path.exists(os.path.join(local, "%s_%s.ply" % (os.path.basename(local), node))): return "meshed"
        if os.path.exists(os.path.join(d, "fuse.ply")) or os.path.exists(os.path.join(local, "%s_%s_cloud.ply" % (os.path.basename(local), node))): return "fused"
        if glob.glob(os.path.join(d, "cache", "*.dph")): return "raw"
        return None
    STAGE_WORDS={"meshed": ("edited on the scanner", OK), "fused": ("fused on the scanner, not meshed", WARN), "raw": ("raw only, not edited on the scanner", WARN), None: ("", MUT)}
    def _scan_label(self, name, node):
        if node=="combined": return "Combined"
        nodes=[n for n in self._proc_nodes(name) if n!="combined"]
        return "Scan %02d" % (nodes.index(node)+1) if node in nodes else node
    def _mesh_info(self, path, cb):
        """Size, counts, pieces and open edges of a model, measured in a memory-capped child; cb(dict or None) on the UI thread."""
        def work():
            info=None
            try:
                env=dict(os.environ); env.setdefault("POINTYOINK_MEM_CAP_GB", "10")
                r=subprocess.run([_sys.executable, os.path.join(HERE, "process.py"), path, "--info"], capture_output=True, text=True, timeout=600, env=env)
                for ln in r.stdout.splitlines():
                    if ln.startswith("STAGE info "): info=json.loads(ln[11:])
            except Exception as e: log_error("mesh info", e)
            self.q.put(("call", lambda: cb(info)))
        threading.Thread(target=work, daemon=True).start()
    def _info_text(self, info):
        if not info: return "Could not measure this model (see Help > Log)."
        ext=info.get("extent") or [0,0,0]
        closed="closed surface" if info.get("watertight") else "not a closed surface"
        return ("Size %.0f × %.0f × %.0f mm (as measured by the scanner)\n%s triangles · %d piece%s · %s open edge%s · %s" % (
            ext[0], ext[1], ext[2], human_count(info.get("faces",0)), info.get("pieces",0), "" if info.get("pieces")==1 else "s",
            human_count(info.get("open_edges",0)), "" if info.get("open_edges")==1 else "s", closed))
    def _prepare_dialog(self, name, node):
        """The four named clean-up actions, run on a copy, shown before and after, then Keep or Discard."""
        cur=self._proc_current(name, node)
        if not cur: return
        self._ensure_clean_vars()
        t=self._top("Prepare · %s" % self._scan_label(name, node), 960, 780, key="prepare")
        if t is None: return
        src=cur[2]; final=os.path.join(os.path.dirname(src), "%s_%s_clean.ply" % (name, node)); tmp=final[:-4]+".tmp.ply"
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(card, text="Starting from the version “%s” · %s" % (cur[1], human(os.path.getsize(src))), text_color=MUT, font=ctk.CTkFont(size=12)).pack(anchor="w", padx=18, pady=(14,6))
        opts=ctk.CTkFrame(card, fg_color="transparent"); opts.pack(fill="x", padx=12)
        def row(var, title, before, entry, after, tip):
            r=ctk.CTkFrame(opts, fg_color=CARD2, corner_radius=10); r.pack(fill="x", padx=4, pady=3)
            cbx=ctk.CTkCheckBox(r, text=title, variable=var, width=24, checkbox_width=18, checkbox_height=18, corner_radius=5, border_color=STROKE, fg_color=AC, hover_color=AC,
                                text_color=TX, font=ctk.CTkFont(size=12, weight="bold")); cbx.pack(side="left", padx=(12,10), pady=7)
            self._tip(cbx, tip)
            ctk.CTkLabel(r, text=before, text_color=MUT, font=ctk.CTkFont(size=11)).pack(side="left")
            if entry is not None:
                e=ctk.CTkEntry(r, textvariable=entry, width=46, height=24, corner_radius=6, fg_color="#0d0f14", border_color=STROKE, text_color=TX, justify="center"); e.pack(side="left", padx=6)
                ctk.CTkLabel(r, text=after, text_color=MUT, font=ctk.CTkFont(size=11)).pack(side="left")
        row(self.clean_do_iso, "Remove floating pieces", "drop pieces smaller than", self.clean_iso, "% of the biggest one",
            "Loose bits that are not part of the object. The scanner's Isolation rate; its default is 15%.")
        row(self.clean_do_base, "Remove base", "cuts off the biggest flat surface (table, turntable, floor). Check the After view: it can bite into a flat part of the object", None, "",
            "Automatic. For a cut you place by hand, use Remove base on the scan instead.")
        row(self.clean_do_smooth, "Smooth surface", "", self.clean_smooth, "passes (the scanner uses 3)",
            "Evens out scan ripple. More passes soften small detail.")
        row(self.clean_holes, "Fill small holes", "closes small gaps in the surface. Off on the scanner by default: it can invent surface where the scan missed", None, "",
            "Only small holes are closed. Intentional openings in the part can get filled too, so check the result.")
        row(self.clean_do_keep, "Reduce triangle count", "keep", self.clean_keep, "% of the triangles (smaller file, less detail)",
            "The scanner's Simplify ratio (it uses 40%). 100 keeps every triangle.")
        prev=ctk.CTkFrame(card, fg_color="transparent"); prev.pack(fill="both", expand=True, padx=12, pady=(10,0))
        prev.grid_columnconfigure((0,1), weight=1); prev.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(prev, text="Before · drag to turn, both views turn together", text_color=MUT, font=ctk.CTkFont(size=11)).grid(row=0,column=0)
        ctk.CTkLabel(prev, text="After", text_color=MUT, font=ctk.CTkFont(size=11)).grid(row=0,column=1)
        boxes=[ctk.CTkFrame(prev, fg_color="#0a0c10", corner_radius=10) for _ in range(2)]
        boxes[0].grid(row=1,column=0, sticky="nsew", padx=(4,3), pady=4); boxes[1].grid(row=1,column=1, sticky="nsew", padx=(3,4), pady=4)
        views=[]; loads=[]
        for b in boxes:
            b.grid_columnconfigure(0, weight=1); b.grid_rowconfigure(0, weight=1)
            v=self._new_view(b); v.grid(row=0,column=0, sticky="nsew", padx=4, pady=4); views.append(v)
            l=ctk.CTkLabel(b, text="", text_color=MUT, font=ctk.CTkFont(size=13), fg_color="#0a0c10"); l.grid(row=0,column=0, sticky="nsew", padx=4, pady=4); loads.append(l)
        def sync(a, b):
            try:
                if hasattr(a, "rot"): b.rot=a.rot.copy()
                else: b.azim, b.elev=a.azim, a.elev
                b.zoom=a.zoom; b.pan=list(a.pan); b.draw()
            except Exception: pass
        for a,b in ((views[0],views[1]),(views[1],views[0])):
            for ev in ("<B1-Motion>","<B2-Motion>","<B3-Motion>","<ButtonRelease-1>","<MouseWheel>","<Button-4>","<Button-5>","<Double-Button-1>"):
                a.bind(ev, lambda e, a=a, b=b: sync(a, b), add="+")
        def show(i, path, text):
            loads[i].configure(text=text); loads[i].grid(); loads[i].lift()
            def cb(ok):
                if not t.winfo_exists(): return
                if ok: loads[i].grid_remove(); sync(views[0], views[1]) if i==1 else None
                else: loads[i].configure(text="Could not load this model")
            views[i].load(path, cb, max_faces=600000)
        show(0, src, "Loading…"); loads[1].configure(text="Press Run to see the result here"); loads[1].grid(); loads[1].lift()
        status=ctk.CTkLabel(card, text="Tick what to do, then Run. Nothing is changed until you press Keep.", text_color=MUT, font=ctk.CTkFont(size=12)); status.pack(anchor="w", padx=18, pady=(6,0))
        btns=ctk.CTkFrame(card, fg_color="transparent"); btns.pack(fill="x", padx=12, pady=(6,12))
        def able(b, on, fill=AC):
            b.configure(state=("normal" if on else "disabled"), fg_color=(fill if on else CARD2), text_color=("#04121f" if on else DIM), text_color_disabled=DIM)
        def close():
            try:
                if os.path.exists(tmp): os.remove(tmp)
            except Exception: pass
            self._dialogs.pop("prepare", None); t.destroy()
        t.protocol("WM_DELETE_WINDOW", close)
        def keep():
            try: os.replace(tmp, final)
            except Exception as e: log_error("prepare keep", e); status.configure(text="Could not save the prepared version (see Help > Log).", text_color=WARN); return
            self._persist(); self._mesh_stats={}; self.gallery_cache.pop(name, None); self.projects_sig=None
            self.set_banner("%s prepared: saved as a new version, the original is kept." % self._scan_label(name, node), OK)
            self._proc_set_current(name, node, "clean"); close()
        def discard(): close(); self.set_banner("Discarded. Nothing was changed.", MUT)
        def run():
            if not (self.clean_do_iso.get() or self.clean_do_smooth.get() or self.clean_holes.get() or self.clean_do_keep.get() or self.clean_do_base.get()):
                status.configure(text="Tick at least one action.", text_color=WARN); return
            self._persist(); able(runb, False); keepb.pack_forget(); discb.pack_forget()
            loads[1].configure(text="Working…"); loads[1].grid(); loads[1].lift(); t0=time.time()
            status.configure(text="Working on a copy… (a big model takes a minute)", text_color=MUT)
            def tick():
                if t.winfo_exists() and runb.cget("state")=="disabled": status.configure(text="Working on a copy… %ds (a big model takes a minute)" % int(time.time()-t0)); t.after(1000, tick)
            t.after(1000, tick)
            def work():
                ok=self._clean_subprocess(src, tmp)
                def done():
                    if not t.winfo_exists(): return
                    able(runb, True)
                    if ok:
                        st=None
                        try: st=os.path.getsize(tmp)
                        except Exception: pass
                        status.configure(text="Done: %s → %s. Turn the views to compare, then Keep or Discard." % (human(os.path.getsize(src)), human(st or 0)), text_color=TX)
                        show(1, tmp, "Loading the result…"); keepb.pack(side="right", padx=6); discb.pack(side="right", padx=6)
                    else: status.configure(text="Could not prepare this scan (see Help > Log).", text_color=WARN); loads[1].configure(text="No result")
                self.q.put(("call", done))
            threading.Thread(target=work, daemon=True).start()
        runb=ctk.CTkButton(btns, text="Run", width=110, height=34, corner_radius=17, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=run); runb.pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Close", width=90, height=34, corner_radius=17, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=close).pack(side="left", padx=6)
        keepb=ctk.CTkButton(btns, text="Keep", width=110, height=34, corner_radius=17, fg_color=OK, hover_color="#35b57c", text_color="#04121f", command=keep)
        discb=ctk.CTkButton(btns, text="Discard", width=100, height=34, corner_radius=17, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=TX, command=discard)
    def _link_views(self, views):
        """Dragging, zooming or panning any of these views moves all of them the same way."""
        def sync(a):
            for b in views:
                if b is a: continue
                try:
                    if hasattr(a, "rot") and hasattr(b, "rot"): b.rot=a.rot.copy()
                    else: b.azim, b.elev=a.azim, a.elev
                    b.zoom=a.zoom; b.pan=list(a.pan); b.draw()
                except Exception: pass
        for a in views:
            for ev in ("<B1-Motion>","<B2-Motion>","<B3-Motion>","<ButtonRelease-1>","<MouseWheel>","<Button-4>","<Button-5>","<Double-Button-1>"):
                a.bind(ev, lambda e, a=a: sync(a), add="+")
    def _compare_dialog(self, name):
        """Two linked 3D views; pick any scan and version for each side."""
        nodes=self._proc_nodes(name); choices=[]
        for nd in nodes:
            for key,label,path in self._proc_versions(name, nd): choices.append(("%s · %s" % (self._scan_label(name, nd), label), path))
        if len(choices)<2: self._alert("Compare", "Nothing to compare yet: this project has fewer than two model versions."); return
        t=self._top("Compare · %s" % self.disp(name), 1180, 760, key="compare")
        if t is None: return
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=12, pady=12)
        card.grid_columnconfigure((0,1), weight=1); card.grid_rowconfigure(1, weight=1)
        menu=dict(fg_color="#0d0f14", button_color=CARD2, button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=8)
        lookup=dict(choices); labels=[c[0] for c in choices]
        cur=self._film_sel if self._film_sel in nodes else nodes[0]
        left0=next((l for l in labels if l.startswith(self._scan_label(name, cur)+" ·")), labels[0])
        right0=next((l for l in labels if l.startswith("Combined ·")), None) or next((l for l in labels if l!=left0), labels[-1])
        sels=[ctk.StringVar(value=left0), ctk.StringVar(value=right0)]
        views=[]; loads=[]
        for i in range(2):
            ctk.CTkOptionMenu(card, values=labels, variable=sels[i], width=360, command=lambda _, i=i: load(i), **menu).grid(row=0,column=i, sticky="w", padx=14, pady=(12,6))
            box=ctk.CTkFrame(card, fg_color="#0a0c10", corner_radius=10); box.grid(row=1,column=i, sticky="nsew", padx=(14,4) if i==0 else (4,14), pady=(0,12))
            box.grid_columnconfigure(0, weight=1); box.grid_rowconfigure(0, weight=1)
            v=self._new_view(box); v.grid(row=0,column=0, sticky="nsew", padx=4, pady=4); views.append(v)
            l=ctk.CTkLabel(box, text="Loading…", text_color=MUT, font=ctk.CTkFont(size=13), fg_color="#0a0c10"); l.grid(row=0,column=0, sticky="nsew", padx=4, pady=4); loads.append(l)
        self._link_views(views)
        def load(i):
            loads[i].configure(text="Loading…"); loads[i].grid(); loads[i].lift()
            def cb(ok):
                if not t.winfo_exists(): return
                if ok: loads[i].grid_remove()
                else: loads[i].configure(text="Could not load this model")
            views[i].load(lookup[sels[i].get()], cb, max_faces=600000)
        ctk.CTkLabel(card, text="Drag either view: both turn together. Scroll to zoom, right-drag to pan, double-click to reset.", text_color=DIM, font=ctk.CTkFont(size=10)).grid(row=2,column=0, columnspan=2, sticky="w", padx=14, pady=(0,10))
        load(0); load(1)
    def _export_dialog(self, name, node):
        """Version, format and destination together, with the model's size and a mesh check."""
        vs=self._proc_versions(name, node)
        if not vs: return
        cur=self._proc_current(name, node) or vs[0]
        t=self._top("Export · %s" % self._scan_label(name, node), 640, 470, key="export")
        if t is None: return
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=12, pady=12)
        def line(label):
            r=ctk.CTkFrame(card, fg_color="transparent"); r.pack(fill="x", padx=18, pady=5)
            ctk.CTkLabel(r, text=label, width=90, anchor="w", text_color=MUT, font=ctk.CTkFont(size=12)).pack(side="left"); return r
        menu=dict(fg_color="#0d0f14", button_color=CARD2, button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=8)
        labels=[l for _,l,_ in vs]; vsel=ctk.StringVar(value=cur[1])
        r=line("Version"); ctk.CTkOptionMenu(r, values=labels, variable=vsel, width=220, command=lambda _: refresh(), **menu).pack(side="left")
        fsel=ctk.StringVar(value=self.cfg.get("export_fmt","STL"))
        r=line("Format"); ctk.CTkOptionMenu(r, values=["STL","OBJ","GLB","PLY"], variable=fsel, width=120, **menu).pack(side="left")
        ctk.CTkLabel(r, text="STL for slicers · OBJ and GLB for other 3D apps · PLY is the original", text_color=DIM, font=ctk.CTkFont(size=10)).pack(side="left", padx=10)
        dv=ctk.StringVar(value=self.cfg.get("export_dir") or os.path.join(self.dest.get() or DEFAULT_DEST, "exports"))
        r=line("Save to"); ctk.CTkEntry(r, textvariable=dv, height=28, corner_radius=6, fg_color="#0d0f14", border_color=STROKE, text_color=TX).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(r, text="Browse", width=70, height=28, corner_radius=6, fg_color=CARD2, hover_color=STROKE, text_color=TX,
                      command=lambda: dv.set(filedialog.askdirectory(initialdir=dv.get() or HOME) or dv.get())).pack(side="left", padx=6)
        base=ctk.StringVar(value="%s_%s" % (self.disp(name).replace(" ","_"), self._scan_label(name, node).replace(" ","")))
        r=line("File name"); ctk.CTkEntry(r, textvariable=base, height=28, corner_radius=6, fg_color="#0d0f14", border_color=STROKE, text_color=TX).pack(side="left", fill="x", expand=True)
        info=ctk.CTkLabel(card, text="Measuring the model…", justify="left", anchor="w", text_color=TX, font=ctk.CTkFont(size=12), wraplength=560); info.pack(fill="x", padx=18, pady=(12,2))
        ctk.CTkLabel(card, text="Open edges and extra pieces mean the surface is not closed. Slicers usually repair small gaps; big ones need Prepare or a mesh editor. An STL file on its own is not a promise that it prints.",
                     justify="left", anchor="w", text_color=DIM, font=ctk.CTkFont(size=10), wraplength=560).pack(fill="x", padx=18)
        def path_of(): return dict((l,p) for _,l,p in vs)[vsel.get()]
        def refresh():
            info.configure(text="Measuring the model…"); pth=path_of()
            self._mesh_info(pth, lambda i: (info.configure(text=self._info_text(i)) if t.winfo_exists() else None))
        status=ctk.CTkLabel(card, text="", text_color=MUT, font=ctk.CTkFont(size=12)); status.pack(anchor="w", padx=18, pady=(8,0))
        btns=ctk.CTkFrame(card, fg_color="transparent"); btns.pack(side="bottom", fill="x", padx=12, pady=(6,12))
        def close(): self._dialogs.pop("export", None); t.destroy()
        t.protocol("WM_DELETE_WINDOW", close)
        def go():
            src=path_of(); fmt=fsel.get().lower(); ddir=os.path.expanduser(dv.get().strip() or "."); nm=re.sub(r"[^\w.-]+", "_", base.get().strip()) or "model"
            self.cfg["export_fmt"]=fsel.get(); self.cfg["export_dir"]=ddir; save_cfg(self.cfg)
            out=os.path.join(ddir, "%s.%s" % (nm, fmt)); n=1
            while os.path.exists(out): out=os.path.join(ddir, "%s_%d.%s" % (nm, n, fmt)); n+=1
            gob.configure(state="disabled"); status.configure(text="Writing %s…" % os.path.basename(out), text_color=MUT)
            def work():
                err=None
                try:
                    os.makedirs(ddir, exist_ok=True)
                    if fmt=="ply": shutil.copyfile(src, out)
                    else:
                        import trimesh; trimesh.load(src, force="mesh").export(out)
                except Exception as e: err=e; log_error("export", e)
                def done():
                    if not t.winfo_exists(): return
                    gob.configure(state="normal")
                    if err: status.configure(text="Export failed (see Help > Log).", text_color=WARN); return
                    status.configure(text="Saved %s (%s)" % (out, human(os.path.getsize(out))), text_color=OK)
                    self.set_banner("Exported %s" % os.path.basename(out), OK)
                    if self.auto_open.get(): subprocess.Popen(["xdg-open", ddir])
                self.q.put(("call", done))
            threading.Thread(target=work, daemon=True).start()
        gob=ctk.CTkButton(btns, text="Export", width=120, height=34, corner_radius=17, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=go); gob.pack(side="right", padx=6)
        ctk.CTkButton(btns, text="Close", width=90, height=34, corner_radius=17, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=close).pack(side="right", padx=6)
        refresh()

    # ---- combine: align scans on matching points (or automatically), then fuse every scan's frames into one model ----
    def _new_view(self, parent):
        w=None
        if os.environ.get("POINTYOINK_NO_GL")!="1" and self.cfg.get("gl_view","auto")!="software":
            try:
                import glview; w=glview.GLView(parent)
            except Exception as e: log_line("GL view unavailable for the align window: %s" % e); w=None
        if w is None:
            import meshview; w=meshview.MeshView(parent)
        return w
    PAIR_COLOURS=((1.0,0.36,0.42),(0.24,0.81,0.56),(0.35,0.69,1.0),(1.0,0.75,0.3),(0.85,0.5,1.0),(0.4,0.9,0.9),(1.0,0.55,0.25),(0.7,0.9,0.3))
    def _align_dialog(self, name):
        if not name: return
        nodes=[n for n in self._proc_nodes(name) if n!="combined" and self._proc_current(name, n)]
        if len(nodes)<2: self._alert("Combine scans", "This project needs at least two scans with a 3D model.\nBuild them first (Build model on each scan)."); return
        t=self._top("Combine scans · %s" % self.disp(name), 1180, 820, key="align")
        if t is None: return
        rec=self.records.setdefault(name,{}).setdefault("align",{})
        st={"base": rec.get("_base") if rec.get("_base") in nodes else nodes[0], "moving": None, "pairs": [], "pending": None, "result": None, "busy": False}
        st["moving"]=next((n for n in nodes if n!=st["base"]), None)
        lab=lambda n: self._scan_label(name, n)
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=12, pady=12)
        card.grid_columnconfigure((0,1), weight=1); card.grid_rowconfigure(2, weight=1)
        menu=dict(fg_color="#0d0f14", button_color=CARD2, button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=8)
        bar=ctk.CTkFrame(card, fg_color="transparent"); bar.grid(row=0,column=0, columnspan=2, sticky="ew", padx=14, pady=(12,4))
        ctk.CTkLabel(bar, text="Base scan", text_color=MUT, font=ctk.CTkFont(size=12)).pack(side="left")
        bsel=ctk.StringVar(value=lab(st["base"])); msel=ctk.StringVar(value=lab(st["moving"]) if st["moving"] else "")
        bmenu=ctk.CTkOptionMenu(bar, values=[lab(n) for n in nodes], variable=bsel, width=130, command=lambda _: pick_base(), **menu); bmenu.pack(side="left", padx=(8,18))
        ctk.CTkLabel(bar, text="Scan to line up", text_color=MUT, font=ctk.CTkFont(size=12)).pack(side="left")
        mmenu=ctk.CTkOptionMenu(bar, values=[lab(n) for n in nodes if n!=st["base"]], variable=msel, width=130, command=lambda _: pick_moving(), **menu); mmenu.pack(side="left", padx=(8,18))
        chips=ctk.CTkLabel(bar, text="", text_color=OK, font=ctk.CTkFont(size=12)); chips.pack(side="left", padx=6)
        hint=ctk.CTkLabel(card, text="", text_color=MUT, font=ctk.CTkFont(size=12), justify="left", wraplength=1100, anchor="w"); hint.grid(row=1,column=0, columnspan=2, sticky="ew", padx=16, pady=(0,6))
        frames=[ctk.CTkFrame(card, fg_color="#0a0c10", corner_radius=10) for _ in range(2)]
        frames[0].grid(row=2,column=0, sticky="nsew", padx=(14,4), pady=4); frames[1].grid(row=2,column=1, sticky="nsew", padx=(4,14), pady=4)
        caps=[ctk.CTkLabel(f, text="", text_color=MUT, font=ctk.CTkFont(size=11)) for f in frames]
        views=[self._new_view(f) for f in frames]
        loads=[ctk.CTkLabel(f, text="Loading the 3D view…", text_color=MUT, font=ctk.CTkFont(size=14), fg_color="#0a0c10") for f in frames]
        for f,c,v,l in zip(frames, caps, views, loads):
            f.grid_columnconfigure(0, weight=1); f.grid_rowconfigure(1, weight=1); c.grid(row=0,column=0, sticky="w", padx=10, pady=(6,0)); v.grid(row=1,column=0, sticky="nsew", padx=6, pady=6)
            l.grid(row=1,column=0, sticky="nsew", padx=6, pady=6); l.lift()
        can_pick=all(hasattr(v, "pick") for v in views)
        def able(b, on, fill=AC):
            """Buttons read as off when off: grey with dim text, instead of a bright button with invisible text."""
            b.configure(state=("normal" if on else "disabled"), fg_color=(fill if on else CARD2), text_color=("#04121f" if on else DIM), text_color_disabled=DIM,
                        hover_color=(AC_H if fill==AC else "#35b57c") if on else CARD2)
        status=ctk.CTkLabel(card, text="", text_color=TX, font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=1100); status.grid(row=3,column=0, columnspan=2, sticky="ew", padx=16, pady=(6,0))
        btns=ctk.CTkFrame(card, fg_color="transparent"); btns.grid(row=4,column=0, columnspan=2, sticky="ew", padx=12, pady=(6,12))
        def refresh_chips():
            done=[n for n in nodes if n in rec and isinstance(rec[n], dict) and rec[n].get("base")==st["base"]]
            chips.configure(text=("Lined up so far: "+", ".join(lab(n) for n in done)) if done else "Nothing lined up yet")
            able(comb, bool(done), OK); comb.configure(text="⧉  Build one model from %d scan%s" % (len(done)+1, "" if not done else "s"))
        def load_views():
            st["pairs"]=[]; st["pending"]=None; st["result"]=None
            for v in views:
                v.markers=[] if hasattr(v, "markers") else None
                if hasattr(v, "clear_layers"): v.clear_layers(draw=False)
            caps[0].configure(text="Base · %s · click a recognisable spot" % lab(st["base"]))
            caps[1].configure(text="%s · then click the same spot here" % (lab(st["moving"]) if st["moving"] else "no scan"))
            saved=rec.get(st["moving"]) if st["moving"] else None
            saved=saved if (isinstance(saved, dict) and saved.get("base")==st["base"]) else None
            if saved: st["pairs"]=[list(pr) for pr in saved.get("pairs", [])]
            def restore(i):
                """Put a kept alignment back on screen: its dots on both views, its overlay on the base view."""
                if not saved or not can_pick or getattr(views[i], "tf", None) is None: return
                import shade
                pts=[pr[0] for pr in st["pairs"]] if i==0 else [pr[1] for pr in st["pairs"]]
                views[i].markers=[(shade.world_to_view(pt, views[i].tf), self.PAIR_COLOURS[k % len(self.PAIR_COLOURS)]) for k,pt in enumerate(pts)]
                views[i].draw()
                if i==0 and hasattr(views[0], "add_layer") and st["moving"]:
                    views[0].clear_layers(draw=False); views[0].add_layer(self._proc_current(name, st["moving"])[2], saved["matrix"], colour=(1.0,0.55,0.25))
                    caps[0].configure(text="Base · %s (grey) with %s as kept (orange)" % (lab(st["base"]), lab(st["moving"])))
            def shown(i):
                def cb(ok):
                    if not t.winfo_exists(): return
                    loads[i].grid_remove()
                    if not ok: caps[i].configure(text=caps[i].cget("text")+"  (could not load)", text_color=WARN)
                    else: restore(i)
                return cb
            for i,l in enumerate(loads): l.configure(text="Loading the 3D view…"); l.grid(); l.lift()
            views[0].load(self._proc_current(name, st["base"])[2], shown(0), max_faces=600000)     # lighter copies: they appear in seconds and picking stays accurate
            if st["moving"]: views[1].load(self._proc_current(name, st["moving"])[2], shown(1), max_faces=600000)
            else: loads[1].grid_remove()
            keepb.pack_forget()
            if saved:
                fit=saved.get("fitness"); n=len(st["pairs"])
                status.configure(text="%s was lined up %s%s%s. Add or undo points and press Line up from points again, or press Start over." % (
                    lab(st["moving"]), saved.get("when","before"), (" with %d point pair%s" % (n, "" if n==1 else "s")) if n else " by Auto", (", %.0f%% overlap" % (fit*100)) if fit else ""), text_color=TX)
            else: status.configure(text="")
            hint.configure(text=("Click a spot you can recognise on the base scan, then the same spot on the other scan. Three pairs are enough; five spread-out ones are better. Then press Line up from points. "
                                 "Or press Auto if the two scans overlap a lot.") if can_pick else
                                "Point picking needs the graphics-card 3D view (Settings). Auto still works when the scans overlap a lot.")
            pairs_lbl.configure(text="%d pair%s" % (len(st["pairs"]), "" if len(st["pairs"])==1 else "s")); able(alignb, len(st["pairs"])>=3)
        def pick_base():
            newb=next(n for n in nodes if lab(n)==bsel.get())
            if newb==st["base"]: return
            others=[n for n in nodes if n in rec and isinstance(rec[n], dict) and rec[n].get("base")!=newb]
            if others and not self._confirm("Change the base scan?", "Scans already lined up were lined up to %s. Changing the base drops those." % lab(st["base"])): bsel.set(lab(st["base"])); return
            for n in others: rec.pop(n, None)
            st["base"]=newb; rec["_base"]=newb; self._persist()
            mmenu.configure(values=[lab(n) for n in nodes if n!=newb]); st["moving"]=next((n for n in nodes if n!=newb), None); msel.set(lab(st["moving"]) if st["moving"] else "")
            load_views(); refresh_chips()
        def pick_moving():
            st["moving"]=next(n for n in nodes if lab(n)==msel.get()); load_views()
        def on_pick(which, world, view):
            try: _on_pick(which, world, view)
            except Exception as e: log_error("align pick", e); status.configure(text="Could not place that point (see Help > Log).", text_color=WARN)
        def _on_pick(which, world, view):
            if st["busy"] or not can_pick: return
            i=len(st["pairs"]); col=self.PAIR_COLOURS[i % len(self.PAIR_COLOURS)]
            if which==0:
                if st["pending"] is not None: views[0].markers.pop()      # re-pick the base point
                st["pending"]=world; views[0].markers.append((view, col)); views[0].draw()
                status.configure(text="Point %d on the base. Now click the same spot on %s." % (i+1, lab(st["moving"])))
            else:
                if st["pending"] is None: status.configure(text="Click the base scan first."); return
                st["pairs"].append([list(map(float, st["pending"])), list(map(float, world))]); st["pending"]=None
                views[1].markers.append((view, col)); views[1].draw()
                pairs_lbl.configure(text="%d pair%s" % (len(st["pairs"]), "" if len(st["pairs"])==1 else "s"))
                status.configure(text="%d pair%s. %s" % (len(st["pairs"]), "" if len(st["pairs"])==1 else "s", "Press Line up from points, or add more." if len(st["pairs"])>=3 else "Add %d more." % (3-len(st["pairs"]))))
            able(alignb, len(st["pairs"])>=3)
        if can_pick:
            views[0].on_pick=lambda w,v: on_pick(0, w, v); views[1].on_pick=lambda w,v: on_pick(1, w, v)
        def start_over():
            st["pairs"]=[]; st["pending"]=None; st["result"]=None
            for v in views:
                if hasattr(v, "markers"): v.markers=[]
                if hasattr(v, "clear_layers"): v.clear_layers(draw=False)
                v.draw()
            caps[0].configure(text="Base · %s · click a recognisable spot" % lab(st["base"]))
            pairs_lbl.configure(text="0 pairs"); able(alignb, False); keepb.pack_forget(); status.configure(text="Cleared. Click new points, or Auto.", text_color=MUT)
        def undo():
            if st["pending"] is not None: st["pending"]=None; views[0].markers.pop(); views[0].draw()
            elif st["pairs"]: st["pairs"].pop(); views[0].markers.pop(); views[1].markers.pop(); views[0].draw(); views[1].draw()
            pairs_lbl.configure(text="%d pair%s" % (len(st["pairs"]), "" if len(st["pairs"])==1 else "s")); able(alignb, len(st["pairs"])>=3)
        busy={"t0":0.0, "msg":"", "job":None}
        def busy_text(m): busy["msg"]=m
        def busy_tick():
            if not busy["job"] or not t.winfo_exists(): return
            if busy["msg"] is not None: status.configure(text="Working: %s… %ds" % (busy["msg"] or "starting", int(time.time()-busy["t0"])), text_color=TX)
            busy["job"]=t.after(500, busy_tick)
        def busy_on(m):
            busy["t0"]=time.time(); busy["msg"]=m; bar.grid(row=5,column=0, columnspan=2, sticky="ew", padx=16, pady=(0,10)); bar.configure(mode="indeterminate"); bar.start()
            for b in (alignb, autob, comb): b.configure(state="disabled")
            busy["job"]=t.after(10, busy_tick)
        def busy_off():
            if busy["job"]:
                try: t.after_cancel(busy["job"])
                except Exception: pass
            busy["job"]=None
            if t.winfo_exists():
                bar.stop(); bar.grid_remove(); able(alignb, len(st["pairs"])>=3); autob.configure(state="normal"); refresh_chips()
        def run_align(auto):
            if st["busy"] or not st["moving"]: return
            if not _has_open3d() and auto: self._alert("Open3D needed", "Auto and the fine adjustment need Open3D.\n  pip3 install --user --break-system-packages open3d"); return
            st["busy"]=True; keepb.pack_forget(); busy_on("Starting…" if not auto else "Starting Auto… this takes a minute or two")
            base_p=self._proc_current(name, st["base"])[2]; mov_p=self._proc_current(name, st["moving"])[2]
            os.makedirs(THUMBS, exist_ok=True); pj=os.path.join(THUMBS, "align_pairs.json"); oj=os.path.join(THUMBS, "align_result.json")
            json.dump({"pairs": st["pairs"]}, open(pj, "w"))
            def work():
                res=None; err=""
                try:
                    env=dict(os.environ); env.setdefault("POINTYOINK_MEM_CAP_GB", "8")
                    cmd=[_sys.executable, os.path.join(HERE, "align.py"), "--base", base_p, "--moving", mov_p, "--out", oj]
                    if st["pairs"]: cmd+=["--pairs", pj]
                    if auto: cmd.append("--auto")
                    proc=subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env); tail=[]
                    for ln in proc.stdout:
                        ln=ln.strip(); tail=(tail+[ln])[-6:]
                        if ln.startswith("STAGE error "): err=ln[12:]
                        elif ln.startswith("STAGE "):
                            try: msg=json.loads(ln.split(" ",2)[2]).get("msg")
                            except Exception: msg=None
                            if msg: self.q.put(("call", lambda m=msg: busy_text(m)))
                    proc.wait()
                    if proc.returncode==0 and os.path.exists(oj): res=json.load(open(oj))
                    else: log_line("align failed: %s %s" % (err, " | ".join(tail)))
                except Exception as e: log_error("align", e)
                def done():
                    st["busy"]=False; busy_off()
                    if not t.winfo_exists(): return
                    if not res: status.configure(text="Could not line these up%s. Try more spread-out points, or pick a scan with more overlap." % ((": "+err) if err else ""), text_color=WARN); return
                    st["result"]=res; fit=res.get("fitness"); rmse=res.get("rmse"); pe=res.get("pair_error_after")
                    words=("%.0f%% of %s overlaps the base, typical gap %.2f mm" % (fit*100, lab(st["moving"]), rmse)) if fit is not None else "rough fit from your points only (no Open3D)"
                    if pe is not None: words+="; your points land %.1f mm apart" % pe
                    verdict="Looks good." if (fit is None or (fit>=0.3 and (rmse or 0)<1.5)) else "Weak fit: check the overlay before keeping it."
                    status.configure(text="%s %s" % (words, verdict), text_color=(TX if verdict.startswith("Looks") else WARN))
                    if hasattr(views[0], "add_layer"):
                        views[0].clear_layers(draw=False); views[0].add_layer(mov_p, res["matrix"], colour=(1.0,0.55,0.25))
                        caps[0].configure(text="Base · %s (grey) with %s lined up (orange)" % (lab(st["base"]), lab(st["moving"])))
                    keepb.pack(side="right", padx=6)
                self.q.put(("call", done))
            threading.Thread(target=work, daemon=True).start()
        def keep():
            if not st["result"]: return
            rec[st["moving"]]={"base": st["base"], "matrix": st["result"]["matrix"], "fitness": st["result"].get("fitness"), "rmse": st["result"].get("rmse"),
                               "pairs": [list(pr) for pr in st["pairs"]], "when": time.strftime("%Y-%m-%d %H:%M")}
            rec["_base"]=st["base"]; self._persist(); refresh_chips(); keepb.pack_forget()
            self.set_banner("%s lined up to %s. Saved with the project." % (lab(st["moving"]), lab(st["base"])), OK)
            nxt=next((n for n in nodes if n!=st["base"] and not (isinstance(rec.get(n), dict) and rec[n].get("base")==st["base"])), None)
            if nxt: st["moving"]=nxt; msel.set(lab(nxt)); load_views(); status.configure(text="Now line up %s." % lab(nxt))
            else: status.configure(text="Every scan is lined up. Build one model from all of them below.")
        def combine():
            done=[n for n in nodes if n in rec and isinstance(rec[n], dict) and rec[n].get("base")==st["base"]]
            self._combine(name, st["base"], done, status); able(comb, False, OK); busy_on(None)   # the combine worker writes its own frame counts into the status line
            def watch():
                if not t.winfo_exists(): return
                if not getattr(self, "_fusing", False): busy_off()
                else: t.after(700, watch)
            t.after(1500, watch)
        pairs_lbl=ctk.CTkLabel(btns, text="0 pairs", text_color=MUT, font=ctk.CTkFont(size=12)); pairs_lbl.pack(side="left", padx=(6,10))
        ctk.CTkButton(btns, text="Undo point", width=100, height=32, corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=undo).pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Start over", width=90, height=32, corner_radius=16, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=MUT, command=start_over).pack(side="left", padx=4)
        alignb=ctk.CTkButton(btns, text="Line up from points", width=160, height=32, corner_radius=16, fg_color=AC, hover_color=AC_H, text_color="#04121f", state="disabled", command=lambda: run_align(False)); alignb.pack(side="left", padx=4)
        autob=ctk.CTkButton(btns, text="Auto", width=80, height=32, corner_radius=16, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=TX, command=lambda: run_align(True)); autob.pack(side="left", padx=4)
        self._tip(autob, "Finds the fit by itself. Works when the two scans share a lot of surface; otherwise use points.")
        comb=ctk.CTkButton(btns, text="⧉  Build one model", width=230, height=32, corner_radius=16, fg_color=OK, hover_color="#35b57c", text_color="#04121f", state="disabled", command=combine); comb.pack(side="right", padx=6)
        self._tip(comb, "Fuses the raw frames of the base scan and every lined-up scan into one model, in the base scan's position. Needs the raw data of each scan on this PC.")
        keepb=ctk.CTkButton(btns, text="Keep this alignment", width=160, height=32, corner_radius=16, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=keep)
        bar=ctk.CTkProgressBar(card, height=6, corner_radius=3, progress_color=AC, fg_color="#0d0f14")
        able(alignb, False); load_views(); refresh_chips()
    def _combine(self, name, base, aligned, status=None):
        """Fuse the base scan's frames and every aligned scan's frames (moved by its saved transform) into <name>_combined_pcfused.ply."""
        if getattr(self, "_fusing", False): self.set_banner("A build is already running.", WARN); return
        if not _has_open3d(): self._alert("Open3D needed", "Building models needs Open3D.\n  pip3 install --user --break-system-packages open3d"); return
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name); rec=self.records.get(name,{}).get("align",{})
        sets=[]
        for node in [base]+list(aligned):
            cache=os.path.join(local,"data",node,"cache"); calib=os.path.join(local,"data",node,"param","Pl.bin")
            if not glob.glob(os.path.join(cache,"*.dph")) or not os.path.exists(calib):
                self._alert("Raw data needed", "%s has no raw scan data on this PC. Share the project over WiFi as Full project, then build again." % self._scan_label(name, node)); return
            tj=""
            if node!=base:
                tj=os.path.join(local, "align_%s.json" % node); json.dump({"base": base, "matrix": rec[node]["matrix"]}, open(tj, "w"))
            pj=""
            plane=self._base_planes(name).get(node)
            if plane:
                pj=os.path.join(local, "plane_%s.json" % node); json.dump(plane, open(pj, "w"))
            sets.append("%s,%s,%s,%s" % (cache, calib, tj, pj))
        ncut=sum(1 for sp in sets if sp.split(",")[3])
        out=os.path.join(local, "%s_combined_pcfused.ply" % name); voxel=float(self.fuse_voxel.get() or 0.4)
        self._fusing=True; self.set_status("Building one model from %d scans%s…" % (len(sets), (", dropping the base of %d" % ncut) if ncut else ""))
        if ncut<len(sets): self.set_banner("%d of %d scans have no base cut saved: their table will be in the combined model. Remove base on each scan first for a clean result." % (len(sets)-ncut, len(sets)), WARN)
        def say(txt):
            self.q.put(("fuse_status", txt))
            if status is not None: self.q.put(("call", lambda: (status.configure(text=txt) if status.winfo_exists() else None)))
        def work():
            ok=False
            try:
                cmd=[_sys.executable, os.path.join(HERE,"fuse.py"), "--out", out, "--voxel", str(voxel)] + ([] if self.cfg.get("fuse_device","auto")=="cpu" else ["--gpu"])
                for sp in sets: cmd+=["--set", sp]
                proc=subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=dict(os.environ, OPENBLAS_NUM_THREADS="1"))
                for ln in proc.stdout:
                    ln=ln.strip()
                    if not ln.startswith("STAGE "): continue
                    parts=ln.split(" ",2); stage=parts[1]
                    try: payload=json.loads(parts[2]) if len(parts)>2 else {}
                    except Exception: payload={}
                    if stage=="integrate": say("Combining: frame %d of %d…" % (payload.get("done",0), payload.get("total",0)))
                    elif stage=="extract": say("Building the combined 3D model…")
                    elif stage=="done": ok=True
                    elif stage=="error": log_line("combine %s: %s" % (name, payload.get("msg","")))
                proc.wait()
            except Exception as e: log_error("combine", e)
            self.q.put(("fuse_done", ("ok", os.path.basename(out)) if (ok and os.path.exists(out)) else ("err", "the combined model could not be built - see the log")))
            if ok: say("Done: %s. It shows in the project as Combined." % os.path.basename(out))
        threading.Thread(target=work, daemon=True).start()

    def on_process_pc(self):
        if getattr(self, "_fusing", False): return
        name=self.selected
        if not name: return
        if not _has_open3d():
            self._alert("Open3D needed",
                "Building models needs Open3D, which isn't installed for this Python.\n\n"
                "Install it with:\n  pip3 install --user --break-system-packages open3d\n\n"
                "(~400 MB. The GPU is used automatically when available.)")
            return
        self._fusing=True
        try: self.proc_btn.configure(state="disabled")
        except Exception: pass
        self.set_status("Building 3D models…")
        for nd in self._proc_nodes(name): self._proc_progress(nd, None, "Waiting…")
        threading.Thread(target=self._fuse_worker, args=(name,), daemon=True).start()
    def _fuse_worker(self, name, only_nodes=None):
        dest=self.dest.get() or DEFAULT_DEST; local=os.path.join(dest, name)
        nodes=[]
        for base in (os.path.join(PROJECTS, name), local):          # device listing first, else local
            try:
                nodes=[n for n in sorted(os.listdir(os.path.join(base,"data"))) if os.path.isdir(os.path.join(base,"data",n))]
                if nodes: break
            except Exception: pass
        if only_nodes: nodes=[n for n in nodes if n in only_nodes]
        if not nodes:
            self.q.put(("fuse_done", ("err","no scan data found"))); return
        outs=[]; voxel=float(self.fuse_voxel.get() or 0.4)
        for ni,node in enumerate(nodes):
            self.q.put(("fuse_node", node, None, "Preparing…"))
            lcache=os.path.join(local,"data",node,"cache"); lparam=os.path.join(local,"data",node,"param")
            dcache=os.path.join(PROJECTS,name,"data",node,"cache"); dparam=os.path.join(PROJECTS,name,"data",node,"param")
            if not glob.glob(os.path.join(lcache,"*.dph")):          # smart: local frames if present, else pull just what's needed
                try: frames=sorted(f for f in os.listdir(dcache) if f.endswith((".dph",".inf")))
                except Exception:
                    self.q.put(("fuse_status","Scan %s: no raw frames on the device or disk - skipping"%node)); continue
                os.makedirs(lcache, exist_ok=True); os.makedirs(lparam, exist_ok=True)
                for i,f in enumerate(frames):
                    if i%20==0: self.q.put(("fuse_status","Scan %d/%d: pulling frame %d/%d off the scanner…"%(ni+1,len(nodes),i+1,len(frames))))
                    try: shutil.copyfile(os.path.join(dcache,f), os.path.join(lcache,f))
                    except Exception as e: log_error("pull-frame "+f, e)
                try:
                    for f in os.listdir(dparam): shutil.copyfile(os.path.join(dparam,f), os.path.join(lparam,f))
                except Exception as e: log_error("pull-param", e)
            calib=os.path.join(lparam,"Pl.bin")
            if not os.path.exists(calib):
                self.q.put(("fuse_status","Scan %s: no calibration (Pl.bin) - skipping"%node)); continue
            out=os.path.join(local, "%s_%s_pcfused.ply"%(name,node))
            self.q.put(("fuse_status","Scan %d/%d: fusing…"%(ni+1,len(nodes))))
            try:
                proc=subprocess.Popen([_sys.executable, os.path.join(HERE,"fuse.py"), "--frames", lcache, "--calib", calib,
                                       "--out", out, "--voxel", str(voxel)] + ([] if self.cfg.get("fuse_device","auto")=="cpu" else ["--gpu"]),
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                      env=dict(os.environ, OPENBLAS_NUM_THREADS="1"))
                ok=False; devname="GPU"
                for ln in proc.stdout:
                    ln=ln.strip()
                    if not ln.startswith("STAGE "): continue
                    parts=ln.split(" ",2); stage=parts[1]
                    try: payload=json.loads(parts[2]) if len(parts)>2 else {}
                    except Exception: payload={}
                    if stage=="device": devname="GPU" if "CUDA" in str(payload.get("device","")) else "CPU"
                    elif stage=="integrate":
                        d,t_=payload.get("done",0),payload.get("total",0) or 1
                        self.q.put(("fuse_status","Scan %d/%d: %s integrating frame %d/%d…"%(ni+1,len(nodes),devname,d,t_)))
                        self.q.put(("fuse_node", node, 0.9*d/t_, "%s: frame %d of %d" % (devname, d, t_)))
                    elif stage=="extract":
                        self.q.put(("fuse_status","Scan %d/%d: building the 3D model…"%(ni+1,len(nodes)))); self.q.put(("fuse_node", node, 0.95, "Building the 3D model…"))
                    elif stage=="done": ok=True
                    elif stage=="error": log_line("fuse %s/%s: %s"%(name,node,payload.get("msg","")))
                proc.wait()
                if ok and os.path.exists(out): outs.append(os.path.basename(out)); self.q.put(("fuse_node", node, 1.0, "Built: %s" % os.path.basename(out)))
                else: log_line("fuse produced no mesh for %s/%s"%(name,node)); self.q.put(("fuse_node", node, 0.0, "Could not build this scan (see Help > Log)"))
            except Exception as e:
                log_error("fuse-launch", e)
        if outs: self.q.put(("fuse_done", ("ok", ", ".join(outs))))
        else: self.q.put(("fuse_done", ("err", "no scans could be processed - see the log")))

    # ---- live: pose + IMU over TCP 9999 (60-byte packets: 8-byte header + 13 float32) ----
    def live_find(self):
        self.set_status("Looking for the scanner on your network…")
        threading.Thread(target=self._live_find_worker, daemon=True).start()
    def _live_find_worker(self):
        import socket, concurrent.futures as cf
        try:
            s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8",80)); me=s.getsockname()[0]; s.close()
        except Exception: self.q.put(("live_found", None)); return
        base=".".join(me.split(".")[:3])
        def probe(i):
            ip="%s.%d"%(base,i); c=socket.socket(); c.settimeout(0.5)
            try: c.connect((ip,9999)); return ip
            except Exception: return None
            finally: c.close()
        with cf.ThreadPoolExecutor(120) as ex: hits=[h for h in ex.map(probe, range(1,255)) if h]
        self.q.put(("live_found", hits[0] if hits else None))
    def live_toggle(self):
        if self._live_on:
            self._live_on=False; self.live_btn.configure(text="▶ Connect", fg_color=AC); self.live_rate.configure(text=""); return
        ip=self.live_ip.get().strip()
        if not ip: self.set_banner("Enter the scanner's IP, or hit Find.", WARN); return
        self.cfg["scanner_ip"]=ip; self._live_on=True; self._live_n=0; self._live_t=time.time(); self._live_trail=[]
        self.live_btn.configure(text="■ Stop", fg_color="#3a2530")
        self._live_empty()   # clears the backsplash; the trail drawing takes over
        threading.Thread(target=self._live_worker, args=(ip,), daemon=True).start()
        self._live_draw()
    def _live_worker(self, ip):
        import socket, struct
        try:
            s=socket.socket(); s.settimeout(3); s.connect((ip,9999)); s.settimeout(2)
        except Exception as e:
            self._live_on=False; self.q.put(("live_err", "Couldn't reach %s:9999 (%s)"%(ip,e))); return
        buf=b""
        while self._live_on:
            try: chunk=s.recv(4096)
            except socket.timeout: continue
            except Exception: break
            if not chunk: break
            buf+=chunk
            while len(buf)>=60:
                pkt=buf[:60]; buf=buf[60:]
                try: self._live_last=struct.unpack_from("<13f", pkt, 8); self._live_n+=1
                except Exception: pass
        s.close()
        if self._live_on: self._live_on=False; self.q.put(("live_err", "Stream ended."))
    def _live_draw(self):
        if not self._live_on: return
        f=self._live_last; cv=self.live_cv
        if f:
            qx,qy,qz,qw=f[0],f[1],f[2],f[3]           # quaternion, assumed (x,y,z,w)
            R=[[1-2*(qy*qy+qz*qz), 2*(qx*qy-qz*qw),   2*(qx*qz+qy*qw)],
               [2*(qx*qy+qz*qw),   1-2*(qx*qx+qz*qz), 2*(qy*qz-qx*qw)],
               [2*(qx*qz-qy*qw),   2*(qy*qz+qx*qw),   1-2*(qx*qx+qy*qy)]]
            W=max(60,cv.winfo_width()); H=max(60,cv.winfo_height()); cx,cy=W*0.33,H*0.52; L=min(W,H)*0.30
            def proj(v):
                x=R[0][0]*v[0]+R[0][1]*v[1]+R[0][2]*v[2]; y=R[1][0]*v[0]+R[1][1]*v[1]+R[1][2]*v[2]; z=R[2][0]*v[0]+R[2][1]*v[1]+R[2][2]*v[2]
                return cx + L*(x - 0.35*z), cy - L*(y - 0.35*z)
            cv.delete("all")
            cv.create_text(cx, 16, text="orientation", fill=MUT, font=(WORDMARK, 9))
            corners=[(sx,sy,sz) for sx in (-.6,.6) for sy in (-.35,.35) for sz in (-.15,.15)]
            P=[proj(v) for v in corners]
            for i in range(8):
                for j in range(i+1,8):
                    if sum(a!=b for a,b in zip(corners[i],corners[j]))==1: cv.create_line(*P[i],*P[j], fill="#2d3644", width=1)
            for v,col,lbl in (((1,0,0),"#ff5d6c","X"),((0,1,0),"#3ecf8e","Y"),((0,0,1),"#5ab0ff","Z")):
                x2,y2=proj(v); cv.create_line(cx,cy,x2,y2, fill=col, width=3, arrow="last"); cv.create_text(x2,y2-11,text=lbl,fill=col,font=(WORDMARK,10,"bold"))
            self._live_trail.append((f[10],f[12]))
            if len(self._live_trail)>600: self._live_trail=self._live_trail[-600:]
            tx0,ty0,tw,th=W*0.66,H*0.12,W*0.31,H*0.76
            cv.create_rectangle(tx0,ty0,tx0+tw,ty0+th, outline="#2d3644"); cv.create_text(tx0+tw/2,ty0-9,text="path (top-down, mm)",fill=MUT,font=(WORDMARK,9))
            xs=[q[0] for q in self._live_trail]; zs=[q[1] for q in self._live_trail]
            rng=max(max(xs)-min(xs), max(zs)-min(zs), 50.0); mx,mz=(max(xs)+min(xs))/2,(max(zs)+min(zs))/2
            pts=[(tx0+tw/2+(x-mx)/rng*tw*0.9, ty0+th/2-(z-mz)/rng*th*0.9) for x,z in self._live_trail]
            if len(pts)>1: cv.create_line(*[c for q in pts for c in q], fill=AC, width=2)
            if pts: cv.create_oval(pts[-1][0]-4,pts[-1][1]-4,pts[-1][0]+4,pts[-1][1]+4, fill="#ffb020", outline="")
            dt=time.time()-self._live_t
            self.live_rate.configure(text=("%.0f pkt/s"%(self._live_n/dt)) if dt>0.5 else "")
            self.live_txt.configure(text=("orientation (quat)\n x %+.3f\n y %+.3f\n z %+.3f\n w %+.3f\n\nposition (mm)\n x %8.1f\n y %8.1f\n z %8.1f\n\ngyro\n %+.3f %+.3f %+.3f\n\naccel (g)\n %+.3f %+.3f %+.3f"
                                          %(f[0],f[1],f[2],f[3],f[10],f[11],f[12],f[4],f[5],f[6],f[7],f[8],f[9])), text_color=TX)
        self.after(40, self._live_draw)

    # ---- Live tab sources ----
    def _live_src_changed(self, v):
        if "RANGE" in v: self.live_miraco.grid_remove(); self.live_range.grid()
        else: self.live_range.grid_remove(); self.live_miraco.grid()
    # ---- RANGE: tethered scanner as a live camera source (range.py) ----
    def range_toggle(self):
        if self._range_busy: return
        if self._range_on: self._range_disconnect(); return
        self._range_busy=True; self.range_btn.configure(state="disabled")
        self.range_status.configure(text="Looking for the RANGE…", text_color=MUT); self.set_status("RANGE - connecting…")
        threading.Thread(target=self._range_connect_worker, daemon=True).start()
    def _range_connect_worker(self):
        try:
            import range as R
            dev=R.find_device()
            if not dev or not dev.get("node"):
                self.q.put(("range_err", "RANGE not detected. Plug it into a direct USB port (not a hub) and try again. If it just disconnected, it's rebooting: give it ~10 s.")); return
            if dev["on_hub"]:
                self.q.put(("range_err", "RANGE is on a USB hub port (500 mA). It needs 5V/1A: move it to a rear motherboard port.")); return
            xu=R.XU(dev["node"]); fw=xu.firmware()
            if not fw:
                self.q.put(("range_err", "RANGE is still booting - give it a few seconds and try again.")); return
            intr=xu.intrinsics()
            xu.projector(True); time.sleep(2.5)
            st=R.DepthStream(dev["node"]); st.start()
            col=None
            if dev.get("rgb_node"):
                col=R.ColorStream(dev["rgb_node"]); col.start()
            self.q.put(("range_ok", (dev, xu, intr, st, col, fw)))
        except Exception as e:
            log_error("range-connect", e); self.q.put(("range_err", "RANGE connect failed: %s" % e))
    def _range_disconnect(self):
        self._range_on=False; self._range_busy=True; st=self._range_stream; col=self._range_color; xu=self._range
        self.range_btn.configure(text="▶ Connect", fg_color=AC, state="disabled"); self.set_status("RANGE - stopping…")
        def _off():
            try:
                if xu: xu.projector(False)        # while still streaming; the reboot below would also kill it
                time.sleep(0.8)
                if col: col.stop()
                if st: st.stop()
            except Exception as e: log_error("range-disconnect", e)
            self.q.put(("range_off", None))
        threading.Thread(target=_off, daemon=True).start()
        self.after(15000, lambda: self._range_busy and self.q.put(("range_off", None)))   # watchdog: never leave the button dead
    def _range_layout(self):
        v=self.range_view.get()
        if v=="All": self.range_single.grid_remove(); self.range_grid.grid()
        else: self.range_grid.grid_remove(); self.range_single.grid()
    def _range_rotate(self):
        self.range_rot=(self.range_rot+90)%360; self.cfg["range_rot"]=self.range_rot
        self.range_rot_btn.configure(text="↻ %d°" % self.range_rot)
    def _range_frame(self, key):
        """PIL image for one view (rotated to taste), or None if that stream has no frame yet."""
        pil=self._range_raw(key)
        return pil.rotate(self.range_rot, expand=True) if (pil is not None and self.range_rot) else pil
    def _range_raw(self, key):
        import range as R
        st=self._range_stream; col=self._range_color
        if key=="Depth":  return Image.fromarray(R.depth_to_image(st.latest)) if st and st.latest is not None else None
        if key=="IR L":   return Image.fromarray(st.ir_left) if st and st.ir_left is not None else None
        if key=="IR R":   return Image.fromarray(st.ir_right) if st and st.ir_right is not None else None
        if key=="Color":  return Image.fromarray(col.latest) if col and col.latest is not None else None
        if key=="Combined":
            if st and st.latest is not None and col and col.latest is not None: return Image.fromarray(R.combined_image(st.latest, col.latest))
            return self._range_raw("Depth")
        return None
    def _range_show(self, label, pil, pad=16):
        if pil is None: return
        w=max(64, label.winfo_width()-pad); h=max(64, label.winfo_height()-pad); iw,ih=pil.size
        sc=min(w/float(iw), h/float(ih)); size=(max(32,int(iw*sc)), max(32,int(ih*sc)))
        key="range_%d" % id(label)
        self.imgs[key]=ctk.CTkImage(light_image=pil, dark_image=pil, size=size)
        label.configure(image=self.imgs[key], text="")
    def _range_draw(self):
        if not self._range_on: return
        try:
            import numpy as np
            v=self.range_view.get()
            if v=="All":
                for key,lab in self.range_tiles.items(): self._range_show(lab, self._range_frame(key), pad=6)
            else:
                self._range_show(self.range_single, self._range_frame(v))
            st=self._range_stream; col=self._range_color
            if st and st.latest is not None:
                fr=st.latest; nz=fr[fr>0]
                self.range_info.configure(text="depth frames %d  ·  color frames %d  ·  valid %.0f%%  ·  depth %.0f-%.0f mm (median %.0f)  ·  sweet spot 300-800 mm" % (
                    st.count, col.count if col else 0, 100*(fr>0).mean(), (nz.min()*0.1 if nz.size else 0), (nz.max()*0.1 if nz.size else 0), (np.median(nz)*0.1 if nz.size else 0)))
        except Exception as e: log_error("range-draw", e)
        self.after(80, self._range_draw)
    def range_capture(self):
        st=self._range_stream
        if not self._range_on or st is None or st.latest is None:
            self.set_banner("Connect the RANGE first, then Capture.", WARN); return
        fr=st.latest.copy(); intr=self._range_intr; col=self._range_color
        rgb=col.latest.copy() if col and col.latest is not None else None
        dest=os.path.join(self.dest.get() or DEFAULT_DEST, "range"); os.makedirs(dest, exist_ok=True)
        base=os.path.join(dest, "range_%s" % time.strftime("%Y%m%d_%H%M%S")); rot=self.range_rot
        def _save():
            try:
                import range as R
                P=R.rotate_cloud(R.backproject(fr, intr), rot)
                if len(P)<100: self.q.put(("range_err", "Almost no depth in view - point the RANGE at something 30-80 cm away.")); return
                R.save_cloud(P, base+".ply")
                if rgb is not None: Image.fromarray(rgb).rotate(rot, expand=True).save(base+".jpg", quality=90)
                self.q.put(("range_captured", (base+".ply", len(P))))
            except Exception as e: log_error("range-capture", e); self.q.put(("range_err", "Capture failed: %s" % e))
        threading.Thread(target=_save, daemon=True).start()

    # ---- WiFi: the scanner's Share to PC > Wi-Fi, received by us (wifi.py) ----
    def on_wifi(self):
        if self._wifi: self._wifi_cancel(); return
        if self.pulling: self.set_banner("Wait for the current import to finish first.", WARN); return
        import wifi
        dest=self.dest.get() or DEFAULT_DEST; os.makedirs(dest, exist_ok=True)
        code=(self.cfg.get("wifi_code") or "").strip() or None
        try:
            rx=wifi.Receiver(dest, code, lambda k,i: self.q.put(("wifi", k, i))); rx.start()
        except OSError as e:
            log_error("wifi-start", e)
            self.set_banner("Can't open port 9706 (%s). Is another PointYoink or Revo Scan running?" % getattr(e, "strerror", e), WARN); return
        self._wifi=rx; self._wifi_projects=None
        self.wifi_btn.configure(text="Stop", fg_color="#3a2530")
        self.set_banner("WiFi share open - on the MIRACO: Share to PC > Wi-Fi, enter code %s" % rx.code, AC)
        self.set_status("WiFi: waiting for the scanner")
        self._wifi_dialog(rx)
    def _wifi_dialog(self, rx):
        t=self._top("Share to PC over WiFi", 520, 400, key="wifi")
        if t is None: return
        t.protocol("WM_DELETE_WINDOW", self._wifi_cancel); t.resizable(False, False); self.wifi_top=t
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=16); card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(card, text="On the MIRACO, open the project, tap the share icon,\npick Wi-Fi and enter this code",
                     text_color=MUT, font=ctk.CTkFont(size=13), justify="center").pack(pady=(22,10))
        tiles=ctk.CTkFrame(card, fg_color="transparent"); tiles.pack()
        self.wifi_tiles=[]
        for ch in rx.code:
            tl=ctk.CTkLabel(tiles, text=ch, width=64, height=78, corner_radius=14, fg_color="#0d0f14", text_color=AC,
                            font=ctk.CTkFont(family=WORDMARK, size=44, weight="bold")); tl.pack(side="left", padx=6); self.wifi_tiles.append(tl)
        st=ctk.CTkFrame(card, fg_color="transparent"); st.pack(pady=(16,2))
        self.wifi_dot=ctk.CTkLabel(st, text="●", text_color=MUT, font=ctk.CTkFont(size=14)); self.wifi_dot.pack(side="left", padx=(0,6))
        self.wifi_state=ctk.CTkLabel(st, text="Waiting for the scanner  ·  this PC is %s" % rx.ip, text_color=MUT, font=ctk.CTkFont(size=12)); self.wifi_state.pack(side="left")
        # receiving block: thumbnail + name, progress, stats (shown once data flows)
        self.wifi_recv=ctk.CTkFrame(card, fg_color="transparent")
        row=ctk.CTkFrame(self.wifi_recv, fg_color="transparent"); row.pack(fill="x", padx=24)
        self.wifi_thumb=ctk.CTkLabel(row, text="", width=84, height=56, fg_color="#0a0c10", corner_radius=10); self.wifi_thumb.pack(side="left")
        self.wifi_proj=ctk.CTkLabel(row, text="", text_color=TX, font=ctk.CTkFont(size=13, weight="bold"), anchor="w"); self.wifi_proj.pack(side="left", padx=12)
        # speed graph: fills left to right with progress, height = transfer speed (old-school copy dialog)
        self.wifi_graph=tk.Canvas(self.wifi_recv, height=84, bg="#0d0f14", highlightthickness=0); self.wifi_graph.pack(fill="x", padx=24, pady=(12,6))
        self.wifi_samples=[]; self._wifi_last_sample=0.0
        stats=ctk.CTkFrame(self.wifi_recv, fg_color="transparent"); stats.pack(fill="x", padx=24)
        self.wifi_stats={}
        for key,cap in (("got","received"),("files","files"),("rate","speed"),("eta","time left")):
            col=ctk.CTkFrame(stats, fg_color="#0d0f14", corner_radius=10); col.pack(side="left", expand=True, fill="x", padx=3)
            v=ctk.CTkLabel(col, text="-", text_color=TX, font=ctk.CTkFont(size=14, weight="bold")); v.pack(pady=(8,0))
            ctk.CTkLabel(col, text=cap, text_color=MUT, font=ctk.CTkFont(size=10)).pack(pady=(0,8)); self.wifi_stats[key]=v
        self.wifi_hint=ctk.CTkLabel(card, text="Both must be on the same network. If the scanner isn't found within 30 seconds, allow port 9706 (UDP and TCP) in your firewall.",
                                    text_color=MUT, font=ctk.CTkFont(size=10), wraplength=420, justify="center"); self.wifi_hint.pack(pady=(10,0))
        br=ctk.CTkFrame(card, fg_color="transparent"); br.pack(side="bottom", pady=(0,16))
        self.wifi_newcode=ctk.CTkButton(br, text="↻ New code", width=110, corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=self._wifi_new_code)
        self.wifi_newcode.pack(side="left", padx=6)
        ctk.CTkButton(br, text="Cancel", width=100, corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=self._wifi_cancel).pack(side="left", padx=6)
        self._wifi_pulse_i=0; self._wifi_pulse()
    def _wifi_pulse(self):
        """Breathing status dot while the dialog is up."""
        rx=self._wifi
        try:
            if not rx or not self.wifi_dot.winfo_exists(): return
            self._wifi_pulse_i=(self._wifi_pulse_i+1)%20; k=abs(10-self._wifi_pulse_i)/10.0
            base=OK if rx.t0 else (AC if rx.seen else MUT)
            r,g,b=int(base[1:3],16),int(base[3:5],16),int(base[5:7],16); f=0.45+0.55*k
            self.wifi_dot.configure(text_color="#%02x%02x%02x" % (int(r*f),int(g*f),int(b*f)))
            self.after(60, self._wifi_pulse)
        except Exception: pass
    def _wifi_graph_add(self, frac, rate):
        """Append a (progress, speed) sample and redraw the area chart."""
        sm=self.wifi_samples; now=time.time()
        # sample by time, not by progress: a raw-frames project arrives as thousands of 0.9 MB files
        if not sm or now-self._wifi_last_sample>=0.3 or frac-sm[-1][0]>=0.01:
            sm.append((frac, rate)); self._wifi_last_sample=now
        else: sm[-1]=(frac, rate)
        cv=self.wifi_graph
        try: W=max(50, cv.winfo_width()); H=int(cv.cget("height"))
        except Exception: return
        cv.delete("all")
        for gy in (0.25,0.5,0.75): cv.create_line(0, H*gy, W, H*gy, fill="#161a22")
        top=max(r for _,r in sm)*1.15 or 1.0
        pts=[(4+f*(W-8), H-4-(r/top)*(H-14)) for f,r in sm]
        if len(pts)>=2:
            poly=[(pts[0][0], H-4)]+pts+[(pts[-1][0], H-4)]
            cv.create_polygon(*[c for xy in poly for c in xy], fill="#1d3f66", outline="")
            cv.create_line(*[c for xy in pts for c in xy], fill=AC, width=2, smooth=True)
        x=4+min(1.0, frac)*(W-8)
        cv.create_rectangle(x, 0, W, H, fill="#0d0f14", outline="")       # the unfilled remainder
        cv.create_line(x, 0, x, H, fill=AC, width=1)
        self._wifi_peak=max(r for _,r in sm)          # shown in the stats row, not over the curve
    def _wifi_set_code(self, code):
        for tl,ch in zip(self.wifi_tiles, code): tl.configure(text=ch)
    def _wifi_new_code(self):
        """Fresh random code without closing the dialog (only while nothing is being received)."""
        rx=self._wifi
        if not rx or rx.t0: return
        import wifi
        rx.stop(); shutil.rmtree(rx.stage, ignore_errors=True)     # synchronous: the port must be free before the next bind
        try:
            nrx=wifi.Receiver(rx.dest, None, lambda k,i: self.q.put(("wifi", k, i))); nrx.start()
        except OSError as e:
            log_error("wifi-newcode", e); self._wifi=None; self._wifi_cancel(); return
        self._wifi=nrx; self._wifi_set_code(nrx.code)
        self.wifi_state.configure(text="Waiting for the scanner  ·  this PC is %s" % nrx.ip, text_color=MUT)
        self.set_banner("WiFi share open - on the MIRACO: Share to PC > Wi-Fi, enter code %s" % nrx.code, AC)
    def _wifi_close_dialog(self):
        d=getattr(self, "_dialogs", {}).pop("wifi", None)
        try:
            if d is not None and d.winfo_exists(): d.destroy()
        except Exception: pass
    def _wifi_cancel(self):
        rx=self._wifi
        if not rx: self._wifi_close_dialog(); return
        self._wifi=None; threading.Thread(target=rx.stop, daemon=True).start(); self._wifi_close_dialog()
        self.wifi_btn.configure(text="📶  WiFi", fg_color="transparent")
        got=rx.bytes
        try: shutil.rmtree(rx.stage, ignore_errors=True)
        except Exception: pass
        self.set_status("")
        self.set_banner("WiFi share stopped%s." % (" at %.0f MB - share again on the scanner to retry" % (got/1048576) if got else ""), WARN if got else MUT)
    def _wifi_event(self, kind, info):
        rx=self._wifi
        if not rx: return
        if kind=="searching":
            self.wifi_state.configure(text="Scanner found at %s - enter the code on it." % info["ip"], text_color=OK); self.set_status("WiFi: scanner found, waiting for the code")
            try: self.wifi_hint.pack_forget()      # the firewall hint only matters while nothing has been heard
            except Exception: pass
        elif kind=="badcode":
            if info["locked"]:
                self.wifi_state.configure(text="Too many wrong codes - closing this share. Click WiFi for a new code.", text_color=WARN)
                self.after(2500, self._wifi_cancel)
            else:
                self.wifi_state.configure(text="Wrong code entered on the scanner - try again (%d attempts left)." % (5-rx.bad), text_color=WARN)
        elif kind=="connected":
            self.wifi_state.configure(text="Code accepted  ·  receiving", text_color=OK); self.set_status("WiFi: receiving…")
            try:
                self.wifi_hint.pack_forget(); self.wifi_recv.pack(fill="x", pady=(14,0)); self.wifi_newcode.configure(state="disabled")
                self.wifi_top.geometry("520x560")     # room for the thumbnail, progress and stats rows
            except Exception: pass
        elif kind=="progress":
            now=time.time()
            if now-getattr(self, "_wifi_last_draw", 0.0) < 0.08: return      # never let redraws pile up on the UI thread
            self._wifi_last_draw=now
            tot=info["total"]; frac=(info["bytes"]/tot) if tot else 0; rate=info["rate"]; avg=info.get("avg") or rate
            self._wifi_graph_add(frac, rate)
            left=(tot-info["bytes"])/avg if (tot and avg>0) else None
            self.wifi_stats["got"].configure(text=("%.0f%%  ·  %.0f / %.0f MB" % (100*frac, info["bytes"]/1048576, tot/1048576)) if tot else "%.0f MB" % (info["bytes"]/1048576))
            self.wifi_stats["files"].configure(text=str(info["files"]))
            self.wifi_stats["rate"].configure(text="%.0f MB/s  ·  peak %.0f" % (rate/1048576, getattr(self, "_wifi_peak", rate)/1048576))
            self.wifi_stats["eta"].configure(text=("%d s" % left if left<90 else "%d min" % (left/60)) if left is not None else "-")
            self.set_status("WiFi: %.0f%%" % (100*frac))
            if not self.wifi_proj.cget("text"):     # name + thumbnail as soon as they exist in staging
                try:
                    projs=[d for d in os.listdir(rx.stage) if os.path.isdir(os.path.join(rx.stage, d))]
                    if projs:
                        self.wifi_proj.configure(text="%s%s" % (self.disp(projs[0]), "  (+%d more)" % (len(projs)-1) if len(projs)>1 else ""))
                        pv=glob.glob(os.path.join(rx.stage, projs[0], "data", "*", "preview.png"))
                        if pv:
                            self.imgs["wifi_thumb"]=cimg(pv[0], 84); self.wifi_thumb.configure(image=self.imgs["wifi_thumb"])
                except Exception: pass
        elif kind=="done":
            self._wifi=None; threading.Thread(target=rx.stop, daemon=True).start(); self._wifi_close_dialog(); self.wifi_btn.configure(text="📶  WiFi", fg_color="transparent")
            projects=info["projects"]
            if not projects:
                shutil.rmtree(rx.stage, ignore_errors=True); self.set_banner("The scanner finished but sent no project.", WARN); self.set_status(""); return
            self.set_banner("Received %s over WiFi - choose what to keep." % ", ".join(projects), OK); self.set_status("")
            self._wifi_picker(rx.stage, projects)
    def _wifi_recover(self):
        """A transfer that finished but was never imported (app closed, picker lost) is still in
        staging: offer it again instead of leaving a gigabyte stranded in a hidden folder."""
        if self._wifi or self.pulling: return
        stage=os.path.join(self.dest.get() or DEFAULT_DEST, ".wifi-incoming")
        try: projects=sorted(d for d in os.listdir(stage) if os.path.isdir(os.path.join(stage, d, "data")))
        except Exception: return
        if not projects:
            shutil.rmtree(stage, ignore_errors=True); return
        self.set_banner("A WiFi transfer was received earlier but never imported - choose what to keep.", AC)
        self._wifi_picker(stage, projects)
    def _wifi_picker(self, stage, projects):
        """After a transfer: show each scan with its sizes, tick what to keep, models-only or full."""
        rows=[]
        for name in projects:
            for nd in sorted(glob.glob(os.path.join(stage, name, "data", "*"))):
                if not os.path.isdir(nd): continue
                def sz(pat):
                    return sum(os.path.getsize(f) for f in glob.glob(os.path.join(nd, pat)) if os.path.isfile(f))
                raw=sum(os.path.getsize(f) for f in glob.glob(os.path.join(nd, "cache", "*")))
                rows.append({"project":name, "node":os.path.basename(nd), "mesh":sz("fuse_mesh.ply"), "cloud":sz("fuse.ply"),
                             "raw":raw, "frames":len(glob.glob(os.path.join(nd, "cache", "*.dph"))), "thumb":os.path.join(nd, "preview.png")})
        t=self._top("Received over WiFi", 640, min(720, 215+66*max(1,len(rows))), key="wifipick")
        if t is None: return
        t.protocol("WM_DELETE_WINDOW", lambda: None)   # decide with the buttons; the data is only in staging
        ctk.CTkLabel(t, text="%s  ·  %d scan%s" % (", ".join(projects), len(rows), "" if len(rows)==1 else "s"),
                     font=ctk.CTkFont(family=WORDMARK, size=15, weight="bold"), text_color=TX).pack(anchor="w", padx=20, pady=(18,2))
        ctk.CTkLabel(t, text="Tick the scans to keep. Formats and clean-up follow the options in the main window.",
                     font=ctk.CTkFont(size=12), text_color=MUT).pack(anchor="w", padx=20)
        lst=ctk.CTkScrollableFrame(t, fg_color=CARD, corner_radius=12); lst.pack(fill="both", expand=True, padx=16, pady=10)
        vars_=[]
        for r in rows:
            v=ctk.BooleanVar(value=True); vars_.append(v)
            row=ctk.CTkFrame(lst, fg_color=CARD2, corner_radius=10); row.pack(fill="x", padx=6, pady=4)
            ctk.CTkCheckBox(row, text="", variable=v, width=24, fg_color=AC, hover_color=AC_H).pack(side="left", padx=(10,4), pady=10)
            if os.path.exists(r["thumb"]):
                try:
                    key="wifipick_%s_%s" % (r["project"], r["node"]); self.imgs[key]=cimg(r["thumb"], 72)
                    ctk.CTkLabel(row, image=self.imgs[key], text="").pack(side="left", padx=6)
                except Exception: pass
            col=ctk.CTkFrame(row, fg_color="transparent"); col.pack(side="left", fill="x", expand=True, padx=6)
            ctk.CTkLabel(col, text="scan %s" % r["node"], text_color=TX, font=ctk.CTkFont(size=12, weight="bold"), anchor="w").pack(anchor="w")
            parts=[]
            if r["mesh"]: parts.append("3D model %s" % human(r["mesh"]))
            if r["cloud"]: parts.append("point cloud %s" % human(r["cloud"]))
            parts.append("%d raw frames %s" % (r["frames"], human(r["raw"])) if r["frames"] else "no raw frames")
            if not r["mesh"] and not r["cloud"]: parts.insert(0, "raw scan data only, no 3D model yet (Build it on the Projects page, or One-tap Edit on the scanner)")
            ctk.CTkLabel(col, text="  ·  ".join(parts), text_color=MUT, font=ctk.CTkFont(size=11), anchor="w").pack(anchor="w")
        any_model=any(r["mesh"] or r["cloud"] for r in rows)
        mode=ctk.StringVar(value=("models" if (self.models_only.get() and any_model) else "full"))
        mr=ctk.CTkFrame(t, fg_color="transparent"); mr.pack(fill="x", padx=20)
        rb=ctk.CTkRadioButton(mr, text="Models only (3D models + points, clean names)", variable=mode, value="models", fg_color=AC, hover_color=AC_H, text_color=TX); rb.pack(side="left", padx=(0,16))
        ctk.CTkRadioButton(mr, text="Full project (raw scan data too)", variable=mode, value="full", fg_color=AC, hover_color=AC_H, text_color=TX).pack(side="left")
        if not any_model:
            rb.configure(state="disabled")
            ctk.CTkLabel(t, text="Raw scan data only: there are no 3D models to save yet, so the full project is kept. Build them on the Projects page.",
                         text_color=WARN, font=ctk.CTkFont(size=11), wraplength=580, justify="left").pack(anchor="w", padx=22, pady=(6,0))
        br=ctk.CTkFrame(t, fg_color="transparent"); br.pack(fill="x", padx=16, pady=14)
        def close():
            self._dialogs.pop("wifipick", None); t.destroy()
        def discard():
            close(); shutil.rmtree(stage, ignore_errors=True); self.set_banner("Discarded the received project.", MUT)
        def go():
            keep={}
            for r,v in zip(rows, vars_):
                if v.get(): keep.setdefault(r["project"], []).append(r["node"])
            if not keep: discard(); return
            if mode.get()=="models" and not any((r["mesh"] or r["cloud"]) for r,v in zip(rows, vars_) if v.get()):
                self.set_banner("The ticked scans have no 3D models yet: choose Full project to keep their raw data.", WARN); return
            dest=self.dest.get() or DEFAULT_DEST
            self._wifi_confirm(keep, dest, lambda names, replace: start(keep, dest, names, replace))
        def start(keep, dest, names, replace):
            close()
            for n,label in names.items():
                if label.strip(): self.records.setdefault(n, {})["label"]=label.strip()
            self.pulling=True; self.cancel=False; self._pull_list=list(keep); self._export_fails=[]
            self.import_btn.grid_remove(); self.cancel_btn.grid(row=0,column=3)
            self.progress.grid(row=1,column=0, columnspan=3, sticky="ew", pady=(8,0)); self.progline.grid(row=2,column=0, columnspan=3, sticky="w")
            cleanup=self.cleanup.get()
            fmts=[e for e,v in (("stl",self.exp_stl),("obj",self.exp_obj),("glb",self.exp_glb)) if v.get()]
            self.set_banner("Saving %s…" % ", ".join(self.disp(n) for n in keep), AC)
            threading.Thread(target=self._wifi_finish_worker, args=(stage, keep, dest, mode.get()=="models", fmts, cleanup, replace), daemon=True).start()
        ctk.CTkButton(br, text="Import", width=110, height=34, corner_radius=17, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=go).pack(side="right", padx=6)
        ctk.CTkButton(br, text="Discard", width=100, height=34, corner_radius=17, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=discard).pack(side="right", padx=6)
    def _wifi_confirm(self, keep, dest, then):
        """Name the incoming project(s) and, when one is already on this PC, choose keep-and-add or replace."""
        existing=[n for n in keep if os.path.isdir(os.path.join(dest, n))]
        t=self._top("Before importing", 520, 300+60*len(keep)+(70 if existing else 0), key="wifiname")
        if t is None: return
        t.protocol("WM_DELETE_WINDOW", lambda: (self._dialogs.pop("wifiname", None), t.destroy()))
        ctk.CTkLabel(t, text="Name it (optional)", font=ctk.CTkFont(family=WORDMARK, size=15, weight="bold"), text_color=TX).pack(anchor="w", padx=22, pady=(20,2))
        ctk.CTkLabel(t, text="A name you will recognise, like \"headrest front\". The scanner's id stays as the folder name.",
                     text_color=MUT, font=ctk.CTkFont(size=11), wraplength=460, justify="left").pack(anchor="w", padx=22)
        vars_={}
        for n in keep:
            row=ctk.CTkFrame(t, fg_color="transparent"); row.pack(fill="x", padx=22, pady=(10,0))
            ctk.CTkLabel(row, text=n, text_color=MUT, font=ctk.CTkFont(size=11), width=190, anchor="w").pack(side="left")
            v=ctk.StringVar(value=self.records.get(n, {}).get("label") or ""); vars_[n]=v
            ctk.CTkEntry(row, textvariable=v, placeholder_text="name (optional)", fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=10).pack(side="left", fill="x", expand=True, padx=(8,0))
        mode=ctk.StringVar(value="merge")
        if existing:
            box=ctk.CTkFrame(t, fg_color="#3d2f14", corner_radius=10); box.pack(fill="x", padx=22, pady=(16,0))
            ctk.CTkLabel(box, text="%s already on this PC" % ("These projects are" if len(existing)>1 else self.disp(existing[0])+" is"),
                         text_color=WARN, font=ctk.CTkFont(size=12, weight="bold"), anchor="w").pack(anchor="w", padx=12, pady=(8,2))
            ctk.CTkRadioButton(box, text="Keep what's there, add the scanner's files (models built on this PC stay)", variable=mode, value="merge",
                               fg_color=AC, hover_color=AC_H, text_color=TX, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=12, pady=2)
            ctk.CTkRadioButton(box, text="Replace everything in that folder", variable=mode, value="replace",
                               fg_color=AC, hover_color=AC_H, text_color=TX, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=12, pady=(2,10))
        br=ctk.CTkFrame(t, fg_color="transparent"); br.pack(fill="x", padx=18, pady=16)
        def ok():
            self._dialogs.pop("wifiname", None); t.destroy(); then({n: v.get() for n,v in vars_.items()}, mode.get()=="replace")
        ctk.CTkButton(br, text="Import", width=110, height=34, corner_radius=17, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=ok).pack(side="right", padx=6)
        ctk.CTkButton(br, text="Back", width=90, height=34, corner_radius=17, fg_color=CARD2, hover_color=STROKE, text_color=TX,
                      command=lambda: (self._dialogs.pop("wifiname", None), t.destroy())).pack(side="right", padx=6)
    def _wifi_finish_worker(self, stage, keep, dest, mo, fmts, cleanup, replace=False):
        failed=[]; total=len(keep)
        for i,(name,nodes) in enumerate(keep.items()):
            if self.cancel: break
            try:
                if mo:
                    if replace and os.path.isdir(os.path.join(dest, name)): shutil.rmtree(os.path.join(dest, name), ignore_errors=True)
                    self._import_flat(name, dest, fmts, cleanup, i, total, src_root=stage, nodes=nodes)
                else:
                    self.q.put(("prog", i/total, "Saving %s (full project)" % name))
                    for nd in glob.glob(os.path.join(stage, name, "data", "*")):     # drop the scans that weren't ticked
                        if os.path.basename(nd) not in nodes: shutil.rmtree(nd, ignore_errors=True)
                    out=os.path.join(dest, name); src=os.path.join(stage, name)
                    if os.path.isdir(out) and replace: shutil.rmtree(out)
                    if os.path.isdir(out):        # keep-and-add: swap in the scanner's version of each ticked scan, keep everything else
                        for nd in glob.glob(os.path.join(src, "data", "*")):
                            tgt=os.path.join(out, "data", os.path.basename(nd)); os.makedirs(os.path.dirname(tgt), exist_ok=True)
                            if os.path.isdir(tgt): shutil.rmtree(tgt)
                            shutil.move(nd, tgt)
                        for f in os.listdir(src):
                            fp=os.path.join(src, f)
                            if os.path.isfile(fp): shutil.copyfile(fp, os.path.join(out, f))
                        shutil.rmtree(src, ignore_errors=True)
                    else:
                        shutil.move(src, out)
                try:   # keep a thumbnail so the project list can show it later
                    root=os.path.join(stage if mo else dest, name, "data")
                    for node in sorted(os.listdir(root)):
                        pv=os.path.join(root, node, "preview.png")
                        if os.path.exists(pv): os.makedirs(THUMBS, exist_ok=True); shutil.copyfile(pv, os.path.join(THUMBS, name+"__thumb.png")); break
                except Exception: pass
            except Exception as e:
                failed.append(name); log_error("wifi-import", e)
        if failed or self.cancel:
            log_line("WiFi import %s: received data kept in %s and offered again at the next start" % ("cancelled" if self.cancel else "failed", stage))
        else:
            shutil.rmtree(stage, ignore_errors=True)
        self.q.put(("cancelled" if self.cancel else "done", dest, failed))

    # ---- screenshots ----
    def refresh_screenshots(self):
        if not quick_mounted():
            self.set_banner("Screenshots come over USB: plug in, tap File Transfer, click USB.", WARN); return
        self.set_status("Reading screenshots off the device…")
        threading.Thread(target=self._shots_worker, daemon=True).start()
    def _shots_worker(self):
        items=list_screenshots(); local=[]
        cache=os.path.join(THUMBS, "shots"); os.makedirs(cache, exist_ok=True)
        for i,(nm,path) in enumerate(items):
            dst=os.path.join(cache, nm)
            try:
                if not os.path.exists(dst) or os.path.getsize(dst)!=os.path.getsize(path):
                    self.q.put(("status", "Loading screenshot %d/%d…"%(i+1, len(items))))
                    shutil.copyfile(path, dst)
                local.append((nm, dst))
            except Exception as e: log_error("shot-copy "+nm, e)
        # recordings: don't copy the (large) video for display - keep the device path + size
        recs=[]
        for nm,path in list_recordings():
            try: recs.append((nm, path, os.path.getsize(path)))
            except Exception: recs.append((nm, path, 0))
        self.q.put(("shots", (local, recs)))
    def render_shots(self, data):
        images, recs = data
        self._shots_items=images; self._recs=recs
        for w in self.shots.winfo_children(): w.destroy()
        self.shots_lbl.configure(text="%d screenshot%s · %d recording%s on the device"
                                 % (len(images), "" if len(images)==1 else "s", len(recs), "" if len(recs)==1 else "s"))
        if not images and not recs:
            ctk.CTkLabel(self.shots, text="Nothing found on the device.\n(Take a screenshot or recording on the scanner, then Refresh.)",
                         text_color=MUT, justify="left").grid(row=0,column=0, padx=20, pady=20, sticky="w"); return
        idx=0
        for nm,path in images:
            r,c=divmod(idx, 4); idx+=1
            cell=ctk.CTkFrame(self.shots, fg_color=CARD2, corner_radius=10); cell.grid(row=r,column=c, padx=6, pady=6, sticky="nsew")
            try:
                self.imgs["shot_"+nm]=cimg(path, 150)
                lbl=ctk.CTkLabel(cell, image=self.imgs["shot_"+nm], text=""); lbl.pack(padx=6, pady=(6,2))
                lbl.bind("<Button-1>", lambda e,p=path: self._enlarge(p))
            except Exception:
                ctk.CTkLabel(cell, text="(image)", text_color=MUT).pack(padx=20, pady=20)
            ctk.CTkLabel(cell, text=nm[:20], text_color=MUT, font=ctk.CTkFont(size=9)).pack(pady=(0,6))
        for nm,path,sz in recs:
            r,c=divmod(idx, 4); idx+=1
            cell=ctk.CTkFrame(self.shots, fg_color=CARD2, corner_radius=10); cell.grid(row=r,column=c, padx=6, pady=6, sticky="nsew")
            ctk.CTkLabel(cell, text="▶", text_color=AC, font=ctk.CTkFont(size=40)).pack(padx=6, pady=(14,2))
            ctk.CTkLabel(cell, text=nm[:20], text_color=MUT, font=ctk.CTkFont(size=9)).pack()
            ctk.CTkLabel(cell, text=human(sz), text_color="#5a6474", font=ctk.CTkFont(size=9)).pack(pady=(0,8))
    def pull_screenshots(self):
        imgs=getattr(self, "_shots_items", []); recs=getattr(self, "_recs", [])
        if not imgs and not recs:
            self.set_banner("Nothing to pull - hit Refresh first.", MUT); return
        dest=os.path.join(self.dest.get() or DEFAULT_DEST, "captures"); os.makedirs(dest, exist_ok=True)
        self.set_status("Pulling screenshots & recordings…")
        threading.Thread(target=self._pull_shots_worker, args=(list(imgs), list(recs), dest), daemon=True).start()
    def _pull_shots_worker(self, imgs, recs, dest):
        n=0
        for nm,path in imgs:
            try: shutil.copyfile(path, os.path.join(dest, nm)); n+=1
            except Exception as e: log_error("pull-shot "+nm, e)
        for j,(nm,path,sz) in enumerate(recs):
            try:
                self.q.put(("status", "Pulling recording %d/%d (%s)…"%(j+1, len(recs), human(sz))))
                shutil.copyfile(path, os.path.join(dest, nm)); n+=1
            except Exception as e: log_error("pull-rec "+nm, e)
        self.q.put(("shots_pulled", (n, dest)))

    def _open_loader(self, title, msg):
        if getattr(self,"_loader",None):
            try: self._loader.destroy()
            except Exception: pass
        t=ctk.CTkToplevel(self); t.title(title); t.configure(fg_color=BG); t.resizable(False,False)
        try: t.transient(self); t.attributes("-topmost",True)
        except Exception: pass
        w,h=380,150
        try:
            self.update_idletasks()
            x=self.winfo_rootx()+(self.winfo_width()-w)//2; y=self.winfo_rooty()+(self.winfo_height()-h)//3
            t.geometry("%dx%d+%d+%d"%(w,h,x,y))
        except Exception: pass
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(family=WORDMARK, size=15,weight="bold"), text_color=TX).pack(anchor="w", padx=18, pady=(16,2))
        self._loader_msg=ctk.CTkLabel(card, text=msg, text_color=MUT, font=ctk.CTkFont(size=12), wraplength=320, justify="left")
        self._loader_msg.pack(anchor="w", padx=18)
        pb=ctk.CTkProgressBar(card, mode="indeterminate", height=6, corner_radius=3, progress_color=AC); pb.pack(fill="x", padx=18, pady=(14,16)); pb.start()
        self._loader=t
    def _close_loader(self):
        t=getattr(self,"_loader",None)
        if t:
            try: t.destroy()
            except Exception: pass
            self._loader=None

    # ---- export zip ----
    def on_export_zip(self):
        if self.pulling: return
        sel=[n for n,v in self.pull_sel.items() if v.get()]
        if not sel:
            self.set_banner("Tick the project(s) you want to zip.", WARN); return
        dest=self.dest.get() or DEFAULT_DEST
        try: sizes=self._estimate_sizes(sel, dest)
        except Exception: sizes=None
        mode=self._ask_zip_format(sizes)
        if not mode: return
        missing=[n for n in sel if not os.path.isdir(os.path.join(dest,n))]
        if missing:
            if self._confirm("Import first?",
                    "%d selected project(s) haven't been imported yet, so there's nothing local to zip:\n%s\n\nImport them now, then zip?"%(len(missing), ", ".join(self.disp(n) for n in missing))):
                self._zip_after=sel; self._zip_mode=mode
                for n,v in self.pull_sel.items(): v.set(n in sel)
                self.on_pull(); return
            sel=[n for n in sel if n not in missing]
            if not sel:
                self.set_banner("Nothing to zip.", MUT); return
        self._start_zip(sel, dest, mode)
    def _ask_zip_format(self, sizes=None):
        """Choose what goes in the zip. Returns 'stl'/'obj'/'glb'/'models'/'all' or None."""
        t=ctk.CTkToplevel(self); t.title("Export ZIP"); t.configure(fg_color=BG); t.resizable(False,False)
        try: t.transient(self); t.attributes("-topmost",True)
        except Exception: pass
        w,h=470,410
        try:
            self.update_idletasks()
            x=self.winfo_rootx()+(self.winfo_width()-w)//2; y=self.winfo_rooty()+(self.winfo_height()-h)//3
            t.geometry("%dx%d+%d+%d"%(w,h,x,y))
        except Exception: pass
        res={"v":None}
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(card, text="Export ZIP", font=ctk.CTkFont(family=WORDMARK, size=15,weight="bold"), text_color=TX).pack(anchor="w", padx=18, pady=(16,2))
        ctk.CTkLabel(card, text="What should go in the zip? Files are added flat with clean names.",
                     text_color=MUT, font=ctk.CTkFont(size=12), wraplength=410, justify="left").pack(anchor="w", padx=18, pady=(0,10))
        def pick(v): res["v"]=v; t.destroy()
        opts=[("STL only","stl","for 3D printing"),("OBJ only","obj","for editing"),
              ("GLB only","glb","for the web / editing"),
              ("All models","models","every PLY, STL, OBJ, GLB"),
              ("Everything","all","models, previews, metadata")]
        for label,val,hint in opts:
            row=ctk.CTkFrame(card, fg_color="transparent"); row.pack(fill="x", padx=16, pady=3)
            ctk.CTkButton(row, text=label, width=120, height=32, corner_radius=16, fg_color=CARD2,
                          hover_color=AC, text_color=TX, anchor="w", command=lambda v=val: pick(v)).pack(side="left")
            szt=""
            if sizes and sizes.get(val):
                szt="≈ "+human(sizes[val])
            ctk.CTkLabel(row, text=szt, text_color=(AC if sizes and sizes.get(val,0)>0 else MUT),
                         font=ctk.CTkFont(size=11,weight="bold"), width=78, anchor="e").pack(side="right")
            ctk.CTkLabel(row, text=hint, text_color=MUT, font=ctk.CTkFont(size=11)).pack(side="left", padx=10)
        ctk.CTkLabel(card, text="Sizes are rough estimates before compression - the real zip is smaller.",
                     text_color=MUT, font=ctk.CTkFont(size=10), wraplength=410, justify="left").pack(anchor="w", padx=18, pady=(8,0))
        try:
            t.grab_set(); t.wait_window()
        except Exception: pass
        return res["v"]
    def _mesh_cloud_sources(self, name, dest):
        """(mesh_plys, cloud_plys) for a project: local flat > local nested > device nested."""
        local=os.path.join(dest, name)
        m=[p for p in glob.glob(os.path.join(local, name+"_*.ply")) if not p.endswith("_cloud.ply")]
        c=glob.glob(os.path.join(local, name+"_*_cloud.ply"))
        if not m:
            m=glob.glob(os.path.join(local, "data","*","fuse_mesh.ply")) or glob.glob(os.path.join(PROJECTS, name, "data","*","fuse_mesh.ply"))
            c=glob.glob(os.path.join(local, "data","*","fuse.ply")) or glob.glob(os.path.join(PROJECTS, name, "data","*","fuse.ply"))
        return m, c
    def _estimate_sizes(self, sel, dest):
        """Rough uncompressed byte estimates per zip mode. The real zip is smaller (compressed)."""
        est={"stl":0,"obj":0,"glb":0,"models":0,"all":0}
        for name in sel:
            meshes,clouds=self._mesh_cloud_sources(name, dest)
            ply_bytes=0
            for mp in meshes:
                try: ply_bytes+=os.path.getsize(mp)
                except Exception: pass
                v,f=_ply_counts(mp)
                # use the real converted file if it already exists, else estimate from the mesh
                for mode,estfn in (("stl",84+50*f),("obj",v*22+f*24),("glb",v*28+f*12+2048)):
                    conv=mp[:-4]+"."+mode
                    est[mode]+= os.path.getsize(conv) if os.path.exists(conv) else estfn
            for cp in clouds:
                try: ply_bytes+=os.path.getsize(cp)
                except Exception: pass
            est["models"]+=ply_bytes
            # everything = models + previews + metadata. Walk the folder only when it's LOCAL
            # (walking the slow device mount here would freeze the dialog); otherwise approximate.
            localdir=os.path.join(dest,name)
            if os.path.isdir(localdir):
                allb=0
                for root,dirs,fs in os.walk(localdir):
                    dirs[:]=[d for d in dirs if d!="cache"]
                    for fn in fs:
                        try: allb+=os.path.getsize(os.path.join(root,fn))
                        except Exception: pass
                est["all"]+=allb
            else:
                est["all"]+=int(ply_bytes*1.03)+256*1024   # models + a little for previews/metadata
        return est
    def _start_zip(self, sel, dest, mode):
        self.pulling=True
        self.zip_btn.configure(state="disabled")
        self.progress.grid(row=1,column=0, columnspan=4, sticky="ew", pady=(8,0)); self.progline.grid(row=2,column=0, columnspan=4, sticky="w")
        threading.Thread(target=self._zip_worker, args=(sel,dest,mode), daemon=True).start()
    def _project_meshes(self, base, name):
        """Mesh .ply files for an imported project (flat layout, else nested mirror)."""
        flat=[p for p in glob.glob(os.path.join(base, name+"_*.ply")) if not p.endswith("_cloud.ply")]
        if flat: return sorted(flat)
        return sorted(glob.glob(os.path.join(base, "data", "*", "fuse_mesh.ply")))
    def _zip_worker(self, sel, dest, mode):
        import zipfile
        os.makedirs(dest, exist_ok=True)
        tag={"stl":"stl","obj":"obj","glb":"glb","models":"models","all":"full"}.get(mode,mode)
        if len(sel)==1:
            zpath=os.path.join(dest, "%s_%s.zip"%(sel[0], tag))
        else:
            zpath=os.path.join(dest, "pointyoink-%s-%s.zip"%(tag, time.strftime("%Y%m%d-%H%M%S")))
        # build the file list (src, arcname). flat for models/format modes; nested for 'all'
        files=[]; zfails=0
        try:
            for name in sel:
                base=os.path.join(dest, name)
                if mode in ("stl","obj","glb"):
                    for ply in self._project_meshes(base, name):
                        # name flat & unique: <name>_<node>.<ext>
                        node=os.path.basename(os.path.dirname(ply)) if os.sep+"data"+os.sep in ply else os.path.basename(ply)[:-4]
                        stem=node if node.startswith(name) else "%s_%s"%(name,node)
                        target=os.path.join(os.path.dirname(ply), stem+"."+mode)
                        if not os.path.exists(target):
                            self.q.put(("prog", 0.0, "Converting %s to %s…"%(name, mode.upper())))
                            try:
                                import trimesh; trimesh.load(ply, force="mesh").export(target)
                            except Exception as e: zfails+=1; log_error("zip-convert "+os.path.basename(ply), e); continue
                        files.append((target, os.path.basename(target)))
                elif mode=="models":
                    for f in glob.glob(os.path.join(base,"*")):
                        if f.lower().endswith((".ply",".stl",".obj",".glb")): files.append((f, os.path.basename(f)))
                    for f in glob.glob(os.path.join(base,"data","*","*")):
                        # nested scans all have the same file names (fuse_mesh.ply): make each entry unique
                        if f.lower().endswith((".ply",".stl",".obj",".glb")):
                            node=os.path.basename(os.path.dirname(f)); stem,ext=os.path.splitext(os.path.basename(f))
                            files.append((f, "%s_%s_%s%s" % (name, node, stem, ext)))
                else:  # all
                    for root,dirs,fs in os.walk(base):
                        dirs[:]=[d for d in dirs if d!="cache"]
                        for f in fs: fp=os.path.join(root,f); files.append((fp, os.path.join(name, os.path.relpath(fp, base))))
            if not files:
                self.q.put(("zipfail", ("%d conversion(s) failed, nothing to zip - see Help > Log" % zfails) if zfails
                            else "no matching files (try importing with that format first)")); return
            total=len(files)
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for i,(fp,arc) in enumerate(files):
                    self.q.put(("prog", i/total, "Zipping %d/%d - %s"%(i+1,total,os.path.basename(fp))))
                    try: z.write(fp, arc)
                    except Exception as e: zfails+=1; log_error("zip "+arc, e)
            self.q.put(("zipped", zpath, os.path.getsize(zpath), zfails))
        except Exception as e:
            log_error("zip", e); self.q.put(("zipfail", str(e)))
    def _finish(self, dest, failed, cancelled=False):
        self.pulling=False; self.cancel_btn.grid_remove(); self._bottom_refresh()
        try:
            if self.progress.cget("mode")=="indeterminate": self.progress.stop(); self.progress.configure(mode="determinate")
        except Exception: pass
        self.progress.set(0); self.progress.grid_remove()
        if cancelled: self.progline.configure(text="Cancelled."); self.set_banner("Import cancelled.", WARN)
        elif failed:
            self.progline.configure(text="Done with errors: "+", ".join(failed))
            if self._confirm("Some imports failed", "Some projects failed:\n"+"\n".join(failed)+"\n\nRetry those?"):
                for n,v in self.pull_sel.items(): v.set(n in failed)
                self.on_pull(); return
        else:
            for n in getattr(self, "_pull_list", []):
                if n not in failed:
                    p=self._proj(n) or {}
                    self.records.setdefault(n, {}).update(
                        imported_to=os.path.join(dest, n), imported_at=int(time.time()),
                        sig={"edit_time":p.get("edit_time"), "nodes":p.get("nodes"), "meshes":p.get("meshes")})
            self._persist()
            ef=getattr(self, "_export_fails", [])
            if ef:
                self.progline.configure(text="Imported, but %d export(s) failed."%len(ef))
                self.set_banner("Import done, but %d file(s) failed to export - see Help > Log: %s"
                                % (len(ef), ", ".join(ef[:3]) + ("…" if len(ef)>3 else "")), WARN)
            else:
                self.progline.configure(text="Done."); self.set_banner("Import complete.", OK)
            self.projects_sig=None; self.gallery_cache={}; self.listed=False; self.start_listing()   # new projects appear
            za=getattr(self, "_zip_after", None)
            if za:
                self._zip_after=None
                mode=getattr(self,"_zip_mode","models"); self._zip_mode=None
                self._start_zip([n for n in za if os.path.isdir(os.path.join(dest,n))], dest, mode); return
            if self.auto_open.get(): self.open_folder()

    # ---- queue ----
    def drain_loop(self):
        try:
            while True:
                kind,*rest=self.q.get_nowait()
                if kind=="mounted":
                    ok,msg=rest; self._mounting=False
                    if ok: self.listed=False
                    else: self.set_banner("Couldn't connect: "+msg, WARN); log_line("mount failed: "+msg)
                elif kind=="projects":
                    self.listing=False; self.listed=True; self.listed_src=self._listing_src
                    if not getattr(self, "_first_listed", False):
                        self._first_listed=True
                        if self.listed_src=="local" and any(p.get("local") for p in rest[0]): self._set_mode("Local")   # no scanner: start on what is on this PC
                    self.render_list(rest[0]); self._proc_refresh()
                    if self.page=="projects": self._panel_refresh()
                    if not getattr(self, "_shots_loaded", False):   # auto-load device screenshots once
                        self._shots_loaded=True; self.refresh_screenshots()
                elif kind=="sizes": self.projects_sig=None; self.update_summary()
                elif kind=="shaded":
                    key,mode,out=rest
                    if out is None: self._shade_failed.add((key,mode))
                    if (key,mode)==self._shade_key:
                        if out: self._show_shaded(out)
                        else: self.big_hint.configure(text="Scanner's own preview · could not draw the 3D model (see Help > Log)"); self._preview_idle()
                elif kind=="shade_msg":
                    if self._shade_key and rest[0]==self._shade_key[0]: self.big_hint.configure(text=rest[1])
                elif kind=="mesh_stats":
                    key,st=rest; self._mesh_stats[key]=st
                    if self._shade_key and key==self._shade_key[0]: self._show_stats(st)
                elif kind=="gallery": n,items=rest; self.gallery_cache[n]=items; self.render_gallery(n,items)
                elif kind=="files":
                    n,(tot,files)=rest
                    if self.selected==n:
                        self.files_box.configure(state="normal"); self.files_box.delete("1.0","end")
                        self.files_box.insert("end","Model files in %s  (total %s)\n\n"%(n,human(tot)))
                        for node,fn,sz in sorted(files,key=lambda x:-x[2]):
                            self.files_box.insert("end","  %-5s %9s   %s/%s\n"%("CLOUD" if (fn=="fuse.ply" or fn.endswith("_cloud.ply")) else "MESH", human(sz), node, fn))
                        self.files_box.configure(state="disabled")
                elif kind=="prog":
                    frac,line=rest
                    if "Converting" in line:                       # conversion has no % - animate instead of sitting at 99%
                        if self.progress.cget("mode")!="indeterminate":
                            self.progress.configure(mode="indeterminate"); self.progress.start()
                    else:
                        if self.progress.cget("mode")=="indeterminate":
                            self.progress.stop(); self.progress.configure(mode="determinate")
                        self.progress.set(frac)
                    self.progline.configure(text=line)
                elif kind=="done": self._finish(rest[0],rest[1])
                elif kind=="cancelled": self._finish(rest[0],rest[1], cancelled=True)
                elif kind=="zipped":
                    zpath,sz,zfails=rest; self.pulling=False; self.zip_btn.configure(state="normal")
                    self.progress.set(0); self.progress.grid_remove()
                    self.progline.configure(text="Zipped -> %s (%s)"%(os.path.basename(zpath), human(sz)))
                    if self.auto_open.get(): subprocess.Popen(["xdg-open", os.path.dirname(zpath)])   # same option as imports
                    if zfails:
                        self.set_banner("ZIP ready, but %d file(s) failed - see Help > Log."%zfails, WARN)
                    else:
                        self.set_banner("ZIP ready in your save folder.", OK)
                    if self.auto_open.get(): self.open_folder()
                elif kind=="zipfail":
                    self.pulling=False; self.zip_btn.configure(state="normal"); self.progress.grid_remove()
                    self.set_banner("ZIP failed: "+rest[0], WARN)
                elif kind=="loader_msg":
                    if getattr(self,"_loader_msg",None):
                        try: self._loader_msg.configure(text=rest[0])
                        except Exception: pass
                elif kind=="view_done":
                    self._close_loader(); self.set_status("")
                    if rest[0]: self.set_banner("3D view failed - see Help > Log.", WARN)
                elif kind=="status":
                    self.set_status(rest[0])
                elif kind=="shots":
                    self.render_shots(rest[0]); self.set_status("")
                elif kind=="shots_pulled":
                    n, d = rest[0]; self.set_status("")
                    self.set_banner("Pulled %d screenshot%s -> %s" % (n, "" if n==1 else "s", d), OK)
                    if self.auto_open.get(): subprocess.Popen(["xdg-open", d])
                elif kind=="fuse_node": self._proc_progress(rest[0], rest[1], rest[2])
                elif kind=="call":
                    try: rest[0]()
                    except Exception as e: log_error("ui call", e)
                elif kind=="proc_clean_done":
                    n,node,ok=rest; self.set_status("")
                    if ok:
                        self.set_banner("Cleaned scan %s: saved as a new version, the original is kept." % node, OK)
                        self._proc_set_current(n, node, "clean"); self.gallery_cache.pop(n, None); self.projects_sig=None
                    else: self.set_banner("Clean-up failed for scan %s (see Help > Log)." % node, WARN); self._proc_render(n)
                elif kind=="fuse_status":
                    self.set_status(rest[0])
                    if getattr(self,"_loader_msg",None):
                        try: self._loader_msg.configure(text=rest[0])
                        except Exception: pass
                elif kind=="fuse_done":
                    self._close_loader(); self._fusing=False
                    try: self.proc_btn.configure(state="normal")
                    except Exception: pass
                    status, info = rest[0]
                    if status=="ok":
                        n=len(info.split(", ")); self.set_banner("Built %d 3D model%s on this PC" % (n, "" if n==1 else "s"), OK); self.set_status("3D model%s built: %s" % ("" if n==1 else "s", info))
                        self.projects_sig=None; self.gallery_cache={}; self._mesh_stats={}
                        sel=self.selected; self.listed=False; self.start_listing()
                        if sel: self.after(1500, lambda s=sel: (self.select_project(s) if s in [p["name"] for p in self.projects] else None))
                        if self.auto_open.get(): self.open_folder()
                    else:
                        self.set_banner("Building the model failed - see Help > Log. %s" % (info or ""), WARN); self.set_status("")
                        if self.selected: self._proc_render(self.selected)
                elif kind=="live_found":
                    if rest[0]:
                        self.live_ip.set(rest[0]); self.set_status("Scanner found at %s"%rest[0])
                        self.set_banner("Scanner found at %s - hit Connect on the Live tab."%rest[0], OK)
                    else:
                        self.set_status(""); self.set_banner("No scanner answering on port 9999 on this network (is its WiFi on?).", WARN)
                elif kind=="live_err":
                    self.live_btn.configure(text="▶ Connect", fg_color=AC); self.live_rate.configure(text="")
                    self.live_txt.configure(text=rest[0], text_color=WARN); self.set_banner(rest[0], WARN); self._live_empty()
                elif kind=="range_ok":
                    dev,xu,intr,st,col,fw=rest[0]
                    self._range=xu; self._range_intr=intr; self._range_stream=st; self._range_color=col; self._range_on=True; self._range_busy=False
                    self.range_btn.configure(text="■ Disconnect", fg_color="#3a2530", state="normal")
                    self.range_status.configure(text="RANGE connected  ·  usb %s  ·  firmware %s  ·  projector on%s" % (dev["usb_path"], fw, "" if col else "  ·  no color camera found"), text_color=OK)
                    self.set_status("RANGE live"); self._range_layout(); self._range_draw()
                elif kind=="range_err":
                    self._range_busy=False; self.range_btn.configure(state="normal")
                    self.set_banner(rest[0], WARN); self.set_status(""); self.range_status.configure(text=rest[0], text_color=WARN)
                elif kind=="range_off":
                    if self._range_busy or self._range_on:
                        self._range=None; self._range_stream=None; self._range_color=None; self._range_on=False; self._range_busy=False; self.set_status("")
                        self.range_btn.configure(text="▶ Connect", fg_color=AC, state="normal")
                        self.range_status.configure(text="Disconnected. The RANGE reboots itself now (normal after a stream stops) - back in about 10 s.", text_color=MUT)
                elif kind=="range_captured":
                    out,n=rest[0]
                    self.set_banner("Captured %d points -> %s" % (n, os.path.basename(out)), OK); self.set_status("Captured -> %s" % os.path.basename(out))
                    if self.auto_open.get(): subprocess.Popen(["xdg-open", os.path.dirname(out)])
                elif kind=="wifi": self._wifi_event(rest[0], rest[1])
                elif kind=="loader_close":
                    self._close_loader()
                elif kind=="base_done":
                    self._close_loader(); self._basing=False
                    try: self.base_btn.configure(state="normal")
                    except Exception: pass
                    status, info = rest[0][0], rest[0][1]
                    if status=="ok":
                        node=rest[0][2] if len(rest[0])>2 else None; plane=rest[0][3] if len(rest[0])>3 else None
                        nm=self.selected
                        if nm and node and plane:
                            self.records.setdefault(nm,{}).setdefault("base_plane",{})[node]=plane; self._persist()
                        self.set_banner("Base removed from %s: saved as the prepared version%s." % (self._scan_label(nm, node) if (nm and node) else "the model", ", and the cut is remembered for combining" if plane else ""), OK)
                        self.set_status("")
                        self.projects_sig=None; self._mesh_stats={}; self.gallery_cache.pop(nm, None)
                        if nm and node: self._proc_set_current(nm, node, "clean")
                        else: self._proc_refresh()
                    elif status=="cancel":
                        self.set_banner("Base removal cancelled.", MUT); self.set_status("")
                    else:
                        self.set_banner("Base removal failed - see Help > Log.", WARN); self.set_status("")
        except queue.Empty: pass
        self.after(200, self.drain_loop)

if __name__ == "__main__":
    App().mainloop()
