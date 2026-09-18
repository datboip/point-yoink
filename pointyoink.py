#!/usr/bin/env python3
# PointYoink - pull 3D scans off a Revopoint MIRACO over USB (Linux).
# MIT licensed. See LICENSE.
# Unofficial. Not affiliated with or endorsed by Revopoint.
# "Revopoint" and "MIRACO" are trademarks of their respective owners.
import os, re, json, time, glob, shutil, threading, subprocess, queue, faulthandler, signal
# Every subprocess we spawn for heavy work (fuse.py, process.py, align.py, cutplane.py) already
# caps BLAS threading - but in-process numpy/scipy/trimesh calls (mesh stats, thumbnails) never
# did, so a single call could spawn one BLAS thread per CPU core and peg the whole machine for
# several seconds (input lag system-wide, even outside this app - GPU video keeps playing since
# it doesn't need the starved CPU scheduler). setdefault so an explicit user override still wins.
# RAYON_NUM_THREADS covers fast_simplification (Rust/rayon, used for every mesh-view decimation) -
# a real gap in the original cap: the BLAS-only vars above never touched it, so a
# single decimation could still burst every core even with those set.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "RAYON_NUM_THREADS"):
    os.environ.setdefault(_v, "2")
faulthandler.register(signal.SIGUSR1, all_threads=True)      # kill -USR1 <pid> prints every thread's stack to stderr: for diagnosing a freeze
# Tk creates a real X window per widget and, with an X Input Method configured (XMODIFIERS=@im=ibus on
# GNOME), does a synchronous XIM round-trip (XCreateIC -> _XimProtoCreateIC -> _XimRead) to ibus-daemon
# for EVERY one of them, at ~100 ms a reply. Confirmed with a native stack of the frozen app and a
# plain-tkinter control: 80 widgets took 20+ s with ibus, 0.05 s with the IM disabled. It also stalls
# keyboard input in every other app while it runs, because ibus is the keyboard path for all of them.
# Must be set before Tk opens the display; child Tk processes (viewer.py etc.) inherit it.
if os.environ.get("POINTYOINK_XIM") != "1":
    os.environ["XMODIFIERS"] = "@im=none"
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image

# CTkScrollbar._draw() ends with a synchronous canvas.update_idletasks() call. That can process
# a pending <Configure>/dimension-change event on a DIFFERENT scrollbar instance elsewhere in the
# app, whose own handler (_update_dimensions_event, or .set() via xscrollcommand/yscrollcommand)
# calls _draw() again - which ends with its OWN update_idletasks(), which can trigger yet another
# instance's redraw, and so on. This chains across every CTkScrollableFrame in the app (list, film
# strip, options, detail panel, captures, ...), not just recursing on one instance - a per-instance
# guard doesn't stop a cascade across different instances (a per-instance
# version of this patch still hung, SIGUSR1 dumps showing the chain hop through
# _update_dimensions_event on a second scrollbar mid-draw). The actual fix: track nesting globally,
# and only let the OUTERMOST _draw() call really flush idle tasks. Any _draw() invoked while
# already inside another one still does its real drawing work (so that widget still ends up
# visually correct) but has its own trailing update_idletasks() suppressed for that call - it
# rides along on the outer call's own event processing instead of starting a new idle-flush that
# can hop to yet another widget. That breaks the cascade at its root instead of just at one node.
try:
    _ctk_draw_depth = [0]
    _orig_ctk_scrollbar_draw = ctk.CTkScrollbar._draw
    def _pointyoink_guarded_scrollbar_draw(self, *a, **k):
        _ctk_draw_depth[0] += 1
        nested = _ctk_draw_depth[0] > 1
        canvas = getattr(self, "_canvas", None) if nested else None
        orig_update_idletasks = canvas.update_idletasks if canvas is not None else None
        try:
            if canvas is not None: canvas.update_idletasks = lambda: None
            return _orig_ctk_scrollbar_draw(self, *a, **k)
        finally:
            if orig_update_idletasks is not None: canvas.update_idletasks = orig_update_idletasks
            _ctk_draw_depth[0] -= 1
    ctk.CTkScrollbar._draw = _pointyoink_guarded_scrollbar_draw
except Exception:
    pass   # if a future customtkinter version changes this internal, fail open rather than crash

APP = "PointYoink"; VERSION = "1.0.0-rc1"
GITHUB = "https://github.com/datboip/point-yoink"
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
_ICON_ROOT = os.path.join(HERE, "assets", "icons", "png"); _icon_cache = {}
_ICON_SRC_SIZES=(32, 36, 40, 48)   # the source folders actually shipped
def _icon(name, state="default", size=18):
    """Load a PointYoink line icon as a CTkImage (states: default/accent/muted/danger/on-accent). Prefers the
    2x (size*2) source, but falls back to the largest shipped source and scales, so any display size works
    (the shipped sources are only 32/36/40/48). Returns None if missing, so callers keep their text label."""
    key=(name, state, size)
    if key in _icon_cache: return _icon_cache[key]
    ci=None
    for src in (size*2, 48, 40, 36, 32):   # try exact 2x, then the biggest available down to the smallest
        try:
            img=Image.open(os.path.join(_ICON_ROOT, state, str(src), name+".png"))
            ci=ctk.CTkImage(dark_image=img, light_image=img, size=(size, size)); break
        except Exception: continue
    _icon_cache[key]=ci; return ci
def _build_id():
    """A short stamp so two builds of the same VERSION are tellable apart: the git short hash while
    running from the repo (with '+' if there are uncommitted changes), else the source file's date-time."""
    try:
        import subprocess as _sp
        h=_sp.run(["git","-C",HERE,"rev-parse","--short","HEAD"], capture_output=True, text=True, timeout=2)
        if h.returncode==0 and h.stdout.strip():
            d=_sp.run(["git","-C",HERE,"status","--porcelain","-uno"], capture_output=True, text=True, timeout=2)
            return h.stdout.strip()+("+" if d.stdout.strip() else "")
    except Exception: pass
    try: return time.strftime("%m%d-%H%M", time.localtime(os.path.getmtime(os.path.join(HERE,"pointyoink.py"))))
    except Exception: return "?"
BUILD = _build_id()
RELEASE = not any(t in VERSION for t in ("-pre", "-rc"))   # a stamped release hides the dev build id; pre/rc builds show it
DEFAULT_DEST = os.path.join(HOME, "revopoint-scans-models")
VID = "2207"
for d in (THUMBS, CFG_DIR): os.makedirs(d, exist_ok=True)

_INSTANCE_LOCK = None
_SIGTERM_PENDING = False
_SIGTERM_TIME = 0.0

def _early_sigterm(*_a):
    """Between acquiring the lock and the App installing its real handler, a newer build could SIGTERM us.
    Catch it here so the default action can't kill us abruptly; App._install_handoff picks up the flag AND
    the real receipt time (monotonic) so a request that arrived during a long startup is correctly aged."""
    global _SIGTERM_PENDING, _SIGTERM_TIME
    if not _SIGTERM_PENDING: _SIGTERM_TIME = time.monotonic()   # keep the FIRST arrival time
    _SIGTERM_PENDING = True

def _ver_key(v):
    """Order two version strings. A release ('0.9.156') outranks the same-numbered pre ('0.9.156-pre'),
    so relaunching a dev build never kicks a real release of the same number, and two identical builds
    tie (neither is 'newer', so no take-over ping-pong)."""
    v = (v or "").strip().split("+", 1)[0]       # drop +build metadata FIRST (so a '-' in it can't look like a pre-release)
    is_release = 1 if "-" not in v else 0
    base = v.split("-", 1)[0]
    parts = []
    for p in base.split("."):
        try: parts.append(int(p))
        except Exception: parts.append(0)
    return (tuple(parts), is_release)

def _read_lock_holder(fh):
    """(pid, version) recorded by the instance that currently holds the lock, or (None, None)."""
    try:
        fh.seek(0); lines = fh.read().splitlines()
        pid = int(lines[0].strip()) if lines and lines[0].strip() else None
        ver = lines[1].strip() if len(lines) > 1 else ""
        return pid, ver
    except Exception:
        return None, None

def _acquire_single_instance():
    """Hold a process lock so PointYoink never runs two UI instances against the same device.

    If the lock is already held but WE are a strictly newer build than the one holding it, ask that
    instance to bow out (SIGTERM) and take the lock over. Same or older -> refuse (the caller shows the
    'already running' dialog). This makes relaunching a fresh build during development just work."""
    global _INSTANCE_LOCK
    lock_path = os.path.join(CFG_DIR, "pointyoink.lock")
    try:
        import fcntl
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)   # do NOT truncate: we may need to read the holder
        _INSTANCE_LOCK = os.fdopen(fd, "r+")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            hpid, hver = _read_lock_holder(_INSTANCE_LOCK)
            # Only take over a holder that recorded a VERSION: that proves it's a build new enough to catch
            # the SIGTERM and hand off cleanly. A pid-only record is a pre-handoff build (or a partial write)
            # - SIGTERM would just kill it and lose its work, so we refuse and show 'already running' instead.
            if not (hpid and hver and _ver_key(VERSION) > _ver_key(hver)):
                return False
            try: log_line("single-instance-takeover %s > %s (pid %s)" % (VERSION, hver, hpid))
            except Exception: pass
            try: os.kill(hpid, signal.SIGTERM)   # ask the older instance to close cleanly
            except Exception: pass
            # Wait longer than the old instance's bounded exit (it accepts a request up to ~6s old, then a
            # watchdog forces its exit within ~4s), so a slow but honoured hand-off still lands the lock here.
            for _ in range(130):                 # ~13s
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); break
                except BlockingIOError:
                    time.sleep(0.1)
            else:
                return False                     # it never let go: don't fight it
        # Install the guard BEFORE publishing our pid/version: once the version is on disk another launcher
        # can read it and SIGTERM us, so the handler must already be in place or the default action kills us.
        try: signal.signal(signal.SIGTERM, _early_sigterm)
        except Exception: pass
        _INSTANCE_LOCK.seek(0); _INSTANCE_LOCK.truncate()
        _INSTANCE_LOCK.write("%s\n%s\n" % (os.getpid(), VERSION)); _INSTANCE_LOCK.flush()
        return True
    except Exception:
        return True   # never block a real launch over a lock-file problem

def _show_single_instance_error():
    """A dark, on-theme 'already running' dialog (not the plain gray Tk messagebox) with a button that
    brings the window that's already open to the front."""
    msg = "PointYoink is already running.\nUse the window that's already open."
    try: log_line("single-instance-blocked")
    except Exception: pass
    try:
        try: ctk.set_appearance_mode("dark")
        except Exception: pass
        win = ctk.CTk(); win.title(APP); win.configure(fg_color=BG); win.resizable(False, False)
        W,H = 440, 250
        try:
            sw,sh = win.winfo_screenwidth(), win.winfo_screenheight()
            win.geometry("%dx%d+%d+%d" % (W,H,(sw-W)//2,(sh-H)//2))
        except Exception:
            win.geometry("%dx%d" % (W,H))
        try:
            if os.path.exists(ICON):
                from PIL import Image as _Img, ImageTk as _ITk
                win._icon = _ITk.PhotoImage(_Img.open(ICON).convert("RGBA").resize((48,48), _Img.LANCZOS))
                win.iconphoto(True, win._icon)
        except Exception: pass
        try: win.attributes("-topmost", True)
        except Exception: pass
        card = ctk.CTkFrame(win, fg_color=CARD, corner_radius=14, border_width=1, border_color=STROKE)
        card.pack(fill="both", expand=True, padx=16, pady=16)
        head = ctk.CTkFrame(card, fg_color="transparent"); head.pack(fill="x", padx=20, pady=(20,2))
        try:
            if os.path.exists(ICON):
                from PIL import Image as _Img2
                win._hdr = ctk.CTkImage(_Img2.open(ICON).convert("RGBA"), size=(30,30))
                ctk.CTkLabel(head, image=win._hdr, text="").pack(side="left", padx=(0,10))
        except Exception: pass
        ctk.CTkLabel(head, text="PointYoink is already running", text_color=TX,
                     font=ctk.CTkFont(family=WORDMARK, size=17, weight="bold")).pack(side="left")
        ctk.CTkLabel(card, text="Only one copy runs at a time, so it never fights the scanner with itself.\nUse the window that's already open.",
                     text_color=MUT, font=ctk.CTkFont(size=13), justify="left", wraplength=348).pack(anchor="w", padx=20, pady=(6,0))
        row = ctk.CTkFrame(card, fg_color="transparent"); row.pack(side="bottom", fill="x", padx=20, pady=18)
        def show_it():
            try: subprocess.run(["wmctrl","-a",APP], timeout=3)
            except Exception: pass
            win.destroy()
        ctk.CTkButton(row, text="Show the open window", height=34, corner_radius=17, fg_color=AC, hover_color=AC_H,
                      text_color="#04121f", font=ctk.CTkFont(size=13, weight="bold"), command=show_it).pack(side="right")
        ctk.CTkButton(row, text="OK", width=72, height=34, corner_radius=17, fg_color=CARD2, hover_color=STROKE,
                      text_color=TX, command=win.destroy).pack(side="right", padx=(0,8))
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.after(60000, win.destroy)   # never hang forever if left unattended
        win.mainloop()
    except Exception as e:
        try: log_error("single-instance-dialog", e)
        except Exception: pass
        try: _sys.stderr.write(msg + "\n")
        except Exception: pass

# live 3D preview detail (Settings): triangles kept for the interactive view; exports are never reduced
LIVE_QUALITY_FACES={"low":100000, "medium":300000, "high":1000000}
LIVE_QUALITY_LABEL={"low":"Low (fast)", "medium":"Medium", "high":"High (crisp)"}

# palette
BG="#0e1117"; CARD="#171b23"; CARD2="#1d222c"; STROKE="#2a3140"; SELB="#22304a"
SEL_FILL="#252b37"; SEL_EDGE="#3d4a63"   # a selected project row: a lifted lighter card with a soft neutral edge (not a blue outline, which collided with the blue "on this PC" badge)
AC="#4aa3ff"; AC_H="#63b3ff"; OK="#3ecf8e"; WARN="#ffb454"; DANGER="#ff6b6b"
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
    try:
        tmp=CFG+".tmp"
        with open(tmp, "w") as f: json.dump(c, f, indent=2)
        os.replace(tmp, CFG)          # atomic - a crash or power loss mid-write can never leave a truncated config
    except Exception as e: log_line("save_cfg: %s" % e)

# ---------------- device / mount ----------------
NO_DEVICE = os.environ.get("POINTYOINK_NO_DEVICE") == "1"   # test instances must never touch the scanner: two apps on one MTP mount freeze both

def usb_state():
    if NO_DEVICE: return ("absent", None)
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

def _revo_busdev():
    """(busnum, devnum) of the connected Revopoint device from sysfs, zero-padded to match gvfs's
    mtp://[usb:BUS,DEV]/ activation-root format - so do_mount() only ever unmounts OUR device's own
    gvfs claim, never a phone or other camera the user has plugged in at the same time."""
    for d in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        try:
            if open(d).read().strip() != VID: continue
            dd = os.path.dirname(d)
            bus = open(os.path.join(dd, "busnum")).read().strip()
            devn = open(os.path.join(dd, "devnum")).read().strip()
            return "%03d" % int(bus), "%03d" % int(devn)
        except Exception: pass
    return None

def _mountinfo_path(s):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)

def mountpoint_seen(path=MOUNT):
    """True if the mountpoint is present in /proc/self/mountinfo without touching FUSE/MTP.

    Do not replace this with os.listdir(), os.stat(), or `ls` in passive polling. A stale jmtpfs
    mount can stall in the kernel/userspace FUSE path and make unrelated USB input feel frozen.
    """
    if NO_DEVICE: return False
    target=os.path.abspath(path)
    try:
        with open("/proc/self/mountinfo", "r", errors="ignore") as fh:
            for line in fh:
                parts=line.split()
                if len(parts)>4 and os.path.abspath(_mountinfo_path(parts[4]))==target: return True
    except Exception: pass
    return False

def quick_mounted(probe=False, timeout=2):
    if NO_DEVICE: return False
    if not mountpoint_seen(): return False
    if not probe: return True
    try:
        return subprocess.run(["ls", os.path.join(MOUNT, "Internal shared storage")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout).returncode == 0
    except Exception: return False

def _release_gvfs_claim():
    """Release GNOME's gvfs MTP claim on OUR scanner only (matched by bus/dev or the Revopoint/Chishine
    name) - never someone else's phone or camera also plugged in right now."""
    try:
        bd=_revo_busdev()
        tag=("[usb:%s,%s]" % bd) if bd else None
        lst=subprocess.run(["gio","mount","-l"], capture_output=True, text=True, timeout=10).stdout
        for m in re.findall(r"(mtp://[^\s/]+/)", lst):
            ml=m.lower()
            if (tag and tag in m) or "revo" in ml or "chishine" in ml:
                subprocess.run(["gio","mount","-u",m], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except Exception: pass

def do_mount():
    if NO_DEVICE: return (False, "device access is off in this instance")
    if quick_mounted(probe=True, timeout=2): return True, "already mounted"
    # GNOME auto-mounts the scanner through gvfs the moment it enters File Transfer mode, which makes it
    # "busy" for jmtpfs - and it re-grabs in the gap after we release it. So retry the whole release+mount
    # a few times instead of failing on the first collision.
    err=""
    for attempt in range(3):
        _release_gvfs_claim()
        subprocess.run(["fusermount","-uz",MOUNT], stderr=subprocess.DEVNULL)
        subprocess.run(["pkill","-9","-f","jmtpfs .*%s" % os.path.basename(MOUNT)], stderr=subprocess.DEVNULL)
        if not os.path.isdir(MOUNT):
            try: os.makedirs(MOUNT, exist_ok=True)
            except FileExistsError: pass
        time.sleep(1.2 + 0.6*attempt)          # let GNOME actually let go before we grab
        try: r = subprocess.run(["jmtpfs",MOUNT], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            err="jmtpfs timed out"; continue
        time.sleep(1.5)
        if quick_mounted(probe=True, timeout=3): return True, "mounted"
        err=(r.stderr or r.stdout or "").strip() or "device busy"
        # a "busy" failure is almost always GNOME re-grabbing - loop and release it again
    # libmtp's raw panics are noise to a user; say what it actually means
    if any(k in err for k in ("device is busy", "busy", "Can't open device", "MtpErrorCantOpenDevice", "Unable to open", "timed out")):
        return False, ("The scanner isn't available over USB. Make sure it's on File Transfer (not PC mode "
                       "or a Model screen); if it keeps failing, unplug/replug and tap File Transfer, then click USB.")
    return False, (err or "mount failed - replug USB & re-tap File Transfer")

def list_projects(progress=None):
    out = []
    if not quick_mounted(probe=True, timeout=2): return out
    try: names = sorted(os.listdir(PROJECTS), reverse=True)
    except Exception: return out
    total = len(names)
    for i, name in enumerate(names):
        if progress:
            try: progress(i + 1, total)   # live count so the "reading scanner projects" banner visibly moves (MTP is slow, not frozen)
            except Exception: pass
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
        try:
            src=None
            for node in sorted(os.listdir(os.path.join(pdir,"data"))):
                prev=os.path.join(pdir,"data",node,"preview.png")
                if os.path.exists(prev): src=prev; break
            # refresh when the source preview is newer too (an in-place replace under the same name), not
            # only when the cached thumb is missing - else a replaced project keeps its old list preview
            if src and (not os.path.exists(tp) or os.path.getmtime(tp)<os.path.getmtime(src)):
                os.makedirs(THUMBS, exist_ok=True); shutil.copyfile(src, tp)
        except Exception: pass
        if os.path.exists(tp): info["thumb"]=tp
        out.append(info)
    return out

def list_screenshots():
    """Device screenshots (Internal shared storage/Screenshots), newest first.
    Retries: MTP listings intermittently throw, and a silent failure would drop real screenshots."""
    if not quick_mounted(probe=True, timeout=2): return []
    for attempt in range(3):
        try:
            return [(f, os.path.join(SCREENSHOTS, f)) for f in sorted(os.listdir(SCREENSHOTS), reverse=True)
                    if f.lower().endswith((".png", ".jpg", ".jpeg"))]
        except Exception:
            time.sleep(0.4)
    return []

def list_recordings():
    """Screen recordings (videos) anywhere on the device except the big Projects tree.
    MTP folder listings intermittently throw or come back empty, and a silent failure here made real
    recordings look like "0 recordings on the device", so retry the whole scan a few times and only trust
    a clean pass (no per-folder error)."""
    exts=(".mp4",".mkv",".webm",".mov",".avi",".m4v")
    if not quick_mounted(probe=True, timeout=2): return []
    root=os.path.join(MOUNT, "Internal shared storage")
    out=[]
    for attempt in range(3):
        out=[]; clean=True
        try:
            entries=os.listdir(root)
        except Exception:
            clean=False; entries=[]
        for entry in entries:
            if entry=="Projects": continue    # skip the huge scan tree
            sub=os.path.join(root, entry)
            try:
                if os.path.isdir(sub):
                    for f in sorted(os.listdir(sub), reverse=True):
                        if f.lower().endswith(exts): out.append((f, os.path.join(sub, f)))
                elif entry.lower().endswith(exts):
                    out.append((entry, sub))
            except Exception:
                clean=False   # a subfolder listing hiccuped: retry the whole scan rather than drop it silently
        if clean: break
        time.sleep(0.4)
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
        flat=[x for x in glob.glob(os.path.join(pdir, name+"_*.ply")) if not x.endswith("_cloud.ply") and not x.endswith(".tmp.ply")]
        nested=glob.glob(os.path.join(pdir, "data", "*", "fuse_mesh.ply"))
        clouds=glob.glob(os.path.join(pdir, name+"_*_cloud.ply")) or glob.glob(os.path.join(pdir, "data", "*", "fuse.ply"))
        mesh_nodes=set(node_of(x) for x in flat) | set(os.path.basename(os.path.dirname(x)) for x in nested)
        # meshes the SCANNER made (One-tap Edit / Mesh there): the plain <name>_<node>.ply or data/<node>/fuse_mesh.ply, not our _pcfused/_clean builds
        dev_meshed=set(node_of(x) for x in flat if not (x.endswith("_pcfused.ply") or x.endswith("_clean.ply"))) | set(os.path.basename(os.path.dirname(x)) for x in nested)
        # count the same scans the film strip tiles (gather_gallery): a raw scan with only a scanner
        # preview (name_<node>.png) is still a scan you can select and build, so the header "N scans" must
        # include it, or the count disagrees with the tiles ("1 scan" over 5 tiles).
        preview_nodes=set(node_of(p) for p in glob.glob(os.path.join(pdir, name+"_*.png")))
        nodes=mesh_nodes | preview_nodes | set(os.path.basename(d) for d in glob.glob(os.path.join(pdir, "data", "*")) if os.path.isdir(d))
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
        srcs=sorted(glob.glob(os.path.join(pdir, name+"_*.png"))) + sorted(glob.glob(os.path.join(pdir, "data", "*", "preview.png")))
        src=srcs[0] if srcs else None
        # refresh when the source is newer too (in-place replace), not only when the thumb is missing
        if src and (not os.path.exists(tp) or os.path.getmtime(tp)<os.path.getmtime(src)):
            try: os.makedirs(THUMBS, exist_ok=True); shutil.copyfile(src, tp)
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

def gather_gallery(name, local=None, render_combined=True):
    paths=[]
    try: nodes=sorted(os.listdir(os.path.join(PROJECTS,name,"data")))
    except Exception:
        nodes=[]
    if not nodes and local:      # project only on this PC: flat <name>_<node>.png or nested previews
        for png in sorted(glob.glob(os.path.join(local, name+"_*.png"))): paths.append((os.path.basename(png)[len(name)+1:-4], png))
        if not paths:
            for pv in sorted(glob.glob(os.path.join(local, "data", "*", "preview.png"))): paths.append((os.path.basename(os.path.dirname(pv)), pv))
        _combined_tile(name, local, paths, render=render_combined)
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
    if local: _combined_tile(name, local, paths, render=render_combined)       # also when the scanner is connected (the combined model lives on this PC)
    return paths

def _combined_tile(name, local, paths, render=True):
    """The model built from all lined-up scans gets its own tile, but only render it when mesh previews are enabled."""
    comb=os.path.join(local, name+"_combined_pcfused.ply")
    if not os.path.exists(comb): return
    tp=os.path.join(THUMBS, "%s__combined__card.png" % name)
    stale=(not os.path.exists(tp) or os.path.getmtime(tp)<os.path.getmtime(comb))
    if stale:
        if not render:
            if os.path.exists(tp): paths.append(("combined", tp))
            return
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

def _ply_element_count(path, element):
    """Read a PLY header (ascii, at the top of any PLY) for 'element <element> N'. Cheap: no full parse.
    Returns N or None. Used to tell if editing loaded a REDUCED version of a big scan."""
    try:
        with open(path, "rb") as f:
            for _ in range(300):
                ln=f.readline()
                if not ln: break
                s=ln.decode("ascii", "replace").strip()
                if s=="end_header": break
                if s.startswith("element "+element+" "):
                    return int(s.split()[2])
    except Exception: pass
    return None
def _write_ply_points(path, pts):
    """Write an (N,3) point array as a binary-little-endian PLY (no deps, so the app process never has to
    import open3d just to hand points to the memory-capped rebuild subprocess)."""
    import numpy as np
    pts=np.ascontiguousarray(pts, dtype="<f4")
    hdr=("ply\nformat binary_little_endian 1.0\nelement vertex %d\nproperty float x\nproperty float y\nproperty float z\nend_header\n" % len(pts)).encode("ascii")
    tmp=path+".tmp"
    with open(tmp, "wb") as f:
        f.write(hdr); f.write(pts.tobytes())
    os.replace(tmp, path)

def cimg(path, w):
    im=Image.open(path); r=w/im.width; return ctk.CTkImage(light_image=im, dark_image=im, size=(w, int(im.height*r)))

# ---- side-panel "inspector" building blocks ----
DIM="#828d9c"; DIM2="#5a6474"; CHIP="#232a36"; CHIP_TX="#c8d0db"   # DIM lifted for readability (was #5a6474, too low-contrast on the dark cards)
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
    def add(self, name, tag=None, icon=None, img=None):
        cell=ctk.CTkFrame(self.bar, fg_color="transparent"); cell.pack(side="left", padx=(0,4))
        f=ctk.CTkFont(size=self._size, weight="bold"); text=(("  "+name) if img else (((icon+"  ") if icon else "")+name))
        b=ctk.CTkButton(cell, text=text, image=img, compound="left", width=f.measure(text)+22+(20 if img else 0), height=30, corner_radius=6, fg_color="transparent",
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
        self._tabs[name]={"btn":b,"ul":ul,"frame":frame,"cell":cell}
        if self._cur is None: self.set(name)
        return frame
    def set_tab_visible(self, name, on, before=None):
        """Show or hide a whole tab (its button + underline). If the active tab is hidden, switch to the first."""
        t=self._tabs.get(name)
        if not t: return
        cell=t["cell"]
        if on:
            if not cell.winfo_manager():
                kw={"side":"left","padx":(0,4)}
                if before and before in self._tabs: kw["before"]=self._tabs[before]["cell"]
                cell.pack(**kw)
        else:
            cell.pack_forget()
            if self._cur==name:
                for n in self._tabs:
                    if n!=name and self._tabs[n]["cell"].winfo_manager(): self.set(n); break
    def set(self, name, fire=False):
        if name not in self._tabs: return
        if name==self._cur: return   # already here: re-selecting must not re-grid frames (a tab like Edit shows a shared frame the app manages) or re-fire the command
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
    "captures": ("No captures yet", "The scanner's screenshots and recordings only come over USB. Plug in and tap File Transfer."),
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
        import importlib.util; return importlib.util.find_spec("trimesh") is not None     # no import: see _preload
    except Exception: return False

def _preload():
    """Import the heavy libraries once, on the main thread, before any worker thread exists. A first import of
    trimesh (it pulls in shapely) from a worker thread can garbage-collect a Tk font on that thread, which is a Tk
    call from the wrong thread: the app then deadlocks on the splash."""
    try:
        import trimesh, shade  # noqa: F401
    except Exception as e: log_line("preload: %s" % e)

_has_open3d_cache=None
def _has_open3d():
    """Open3D is a ~400MB optional dep for Process on PC. Probe in a subprocess so the
    GUI process never loads it (it would stay resident in the app's memory). Every caller
    of this runs it directly on the UI thread (from a button's own command=), and the
    subprocess spawn has up to a 60s timeout - cache the result so that cost is paid at
    most once per run instead of on every Build/Combine click."""
    global _has_open3d_cache
    if _has_open3d_cache is not None: return _has_open3d_cache
    try:
        _has_open3d_cache=subprocess.run([_sys.executable, "-c", "import open3d"], capture_output=True, timeout=60).returncode==0
    except Exception: _has_open3d_cache=False
    return _has_open3d_cache

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

def detect_base(path):
    """Does this model still have the table/turntable stuck to it? We can't ask the scanner, so we look at
    the geometry: RANSAC the single most-populated flat plane and call it a base only when it is BOTH large
    (a good fraction of the surface lies on it) AND sitting at an extreme of the model (the way a table sits
    under the part). Deliberately conservative - a genuinely flat-bottomed part is a smaller flat patch, so
    we would rather say 'no base' than nag about a table that isn't there. Returns {present, frac} or None
    if we can't tell (too few points / load failed)."""
    try:
        import numpy as np, trimesh
        m = trimesh.load(path, process=False, force="mesh")
        V = np.asarray(m.vertices, dtype=np.float64)
    except Exception:
        return None
    n = len(V)
    if n < 3000: return None
    rng = np.random.default_rng(0)
    if n > 40000: V = V[rng.choice(n, 40000, replace=False)]; n = len(V)
    bb = V.max(0) - V.min(0); diag = float(np.linalg.norm(bb)) or 1.0
    tol = diag * 0.004
    best_inl = 0; best_n = None; best_p = None
    for _ in range(150):
        i = rng.choice(n, 3, replace=False)
        p0, p1, p2 = V[i]
        nrm = np.cross(p1 - p0, p2 - p0); L = float(np.linalg.norm(nrm))
        if L < 1e-9: continue
        nrm = nrm / L
        inl = int((np.abs((V - p0) @ nrm) < tol).sum())
        if inl > best_inl: best_inl = inl; best_n = nrm; best_p = p0
    if best_n is None: return {"present": False, "frac": 0.0}
    frac = best_inl / n
    proj = V @ best_n
    plane_at = float(proj[np.abs((V - best_p) @ best_n) < tol].mean())
    span = float(proj.max() - proj.min()) or 1.0
    at_extreme = min(abs(plane_at - proj.min()), abs(plane_at - proj.max())) < span * 0.12
    return {"present": bool(frac >= 0.15 and at_extreme), "frac": float(frac)}

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

# ---- optional live-view experiment (dev-only) ----
# The Live tab (MIRACO camera over USB + position over WiFi) is a personal testbed that never
# ships: it lives in dev/live_view.py and is mixed in only when that local module is present.
# A normal checkout or an installed .deb has no dev/ dir, so HAS_LIVE stays False and the app
# carries none of that code - just the null-object stubs below.
try:
    import sys as _sysmod
    _devdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev")
    if os.path.isdir(_devdir):
        if _devdir not in _sysmod.path: _sysmod.path.insert(0, _devdir)
        import live_view as _lv
        LiveMixin = _lv.LiveMixin; HAS_LIVE = True
    else:
        _lv = None; HAS_LIVE = False
except Exception:
    _lv = None; HAS_LIVE = False
if not HAS_LIVE:
    class LiveMixin:                         # shipped builds: no Live tab, no live-view code
        def build_live_tab(self, lv): return False
        def live_busy(self): return False
        def live_on_queue(self, kind, rest): return False
        def live_cleanup(self): pass


class App(LiveMixin, ctk.CTk):
    def __init__(self):
        super().__init__()
        try: self.tk.call("tk", "useinputmethods", "0")   # belt and braces with the XMODIFIERS override at the top of the file
        except Exception: pass
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
        self.title(("%s  %s" % (APP, VERSION)) if RELEASE else ("%s  %s (%s)" % (APP, VERSION, BUILD)))
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
        self._closing=False; self._children=set(); self._children_lock=threading.Lock(); self._job_seq=0
        self._mounting=False; self.auto_tried=False; self._wifi=None; self._wifi_bg=False; self.listed_src=None; self._listing_src=None; self._refresh_probe_busy=False; self._shots_busy=False; self._open3d_probe_busy=False
        self._device_mounted=False; self._device_touch_cool_until=0.0
        self.report_callback_exception = self._on_tk_error
        log_line("PointYoink %s (%s) started" % (VERSION, BUILD))

        self.grid_columnconfigure(0, weight=1); self.grid_rowconfigure(2, weight=1, minsize=300)
        self.search=ctk.StringVar(); self.shade_mode="solid"; self._film_sel=None; self._film_cells={}; self._film_imgs={}
        self._shade_lock=threading.Lock(); self._shade_want=None; self._shade_running=False; self._shade_key=None
        self._warm_lock=threading.Lock(); self._warm_q=[]; self._warm_running=False   # background preview-cache warmer (open project first)
        self._shade_failed=set(); self._mesh_stats={}
        self._base_geom={}; self._base_busy=set()   # per-model-file "is the table still on?" verdicts (detect_base), computed in the background
        self._header(); self._statusbar(); self._body(); self._build_options(); self._actions(); self._bottombar()
        self.search.trace_add("write", lambda *a: self._search_changed())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_loop(); self.drain_loop(); self._pulse()
        self._install_handoff()   # a strictly newer build relaunching will SIGTERM us; step aside cleanly
        self.after(60000, lambda: self._close_splash(force=True))  # last-resort fallback only
        self._when_ready(self._wifi_recover)   # offer a stranded WiFi transfer, if any: after the splash, never before
        self._when_ready(lambda: self.after(2500, self._start_prewarm))   # then quietly warm the preview cache in the background

    # ---- lifecycle helpers ----
    def _next_job(self, prefix):
        self._job_seq=getattr(self, "_job_seq", 0)+1
        return "%s:%d" % (prefix, self._job_seq)

    def _start_thread(self, target, *args, name=None, **kwargs):
        label=name or getattr(target, "__name__", "worker")
        def run():
            try: target(*args, **kwargs)
            except Exception as e: log_error("thread "+label, e)
        t=threading.Thread(target=run, daemon=True, name=("PointYoink-"+label)[:64])
        t.start(); return t

    def _forget_child(self, proc):
        try:
            with self._children_lock: self._children.discard(proc)
        except Exception: pass

    def _popen(self, cmd, watch=False, **kwargs):
        if os.name=="posix": kwargs.setdefault("start_new_session", True)
        # Spawn AND register atomically under the lock the shutdown uses, so there is no window where a child
        # exists but isn't tracked. If a close has already begun (_closing), refuse to spawn at all - returning
        # None - rather than create a child that could outlive us. Callers spawn from worker threads, so a None
        # here only happens during teardown, where the operation is being abandoned anyway.
        with self._children_lock:
            if getattr(self, "_closing", False): return None
            proc=subprocess.Popen(cmd, **kwargs); self._children.add(proc)
        if watch: self._start_thread(self._watch_child, proc, name="watch-child")
        return proc

    def _watch_child(self, proc):
        try: proc.wait()
        except Exception: pass
        self._forget_child(proc)

    def _terminate_proc(self, proc, kill=False):
        if not proc or proc.poll() is not None: return
        try:
            if os.name=="posix": os.killpg(os.getpgid(proc.pid), signal.SIGKILL if kill else signal.SIGTERM)
            elif kill: proc.kill()
            else: proc.terminate()
        except Exception:
            try:
                if kill: proc.kill()
                else: proc.terminate()
            except Exception: pass

    def _terminate_children(self):
        try:
            with self._children_lock:
                children=list(self._children); self._children.clear()
        except Exception:
            children=[]
        for proc in children: self._terminate_proc(proc, kill=False)
        deadline=time.time()+1.2
        for proc in children:
            if proc.poll() is not None: continue
            try: proc.wait(timeout=max(0.02, min(0.2, deadline-time.time())))
            except Exception: pass
        for proc in children:
            if proc.poll() is None: self._terminate_proc(proc, kill=True)

    def _run_child(self, cmd, timeout=None, **kwargs):
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
        kwargs.setdefault("text", True)
        proc=self._popen(cmd, **kwargs)
        if proc is None:   # refused because the app is closing (see _popen)
            return subprocess.CompletedProcess(cmd, 1, "", "app is closing")
        try:
            out,err=proc.communicate(timeout=timeout)
            return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            self._terminate_proc(proc, kill=False)
            try: proc.communicate(timeout=3)
            except Exception: pass
            if proc.poll() is None: self._terminate_proc(proc, kill=True)
            raise
        finally:
            self._forget_child(proc)

    def _start_open3d_probe(self):
        global _has_open3d_cache
        if _has_open3d_cache is not None or getattr(self, "_open3d_probe_busy", False): return
        self._open3d_probe_busy=True
        def work():
            global _has_open3d_cache
            ok=False
            try:
                ok=self._run_child([_sys.executable, "-c", "import open3d"], timeout=60).returncode==0
            except Exception:
                ok=False
            _has_open3d_cache=ok
            self.q.put(("open3d_checked", ok))
        self._start_thread(work, name="open3d-probe")

    def _require_open3d(self, action="Building models"):
        global _has_open3d_cache
        if _has_open3d_cache is True: return True
        if _has_open3d_cache is False:
            self._alert("Open3D needed",
                ("%s needs Open3D, which isn't installed for this Python.\n\n"
                 "Install it with:\n  pip3 install --user --break-system-packages open3d\n\n"
                 "(~400 MB. The GPU is used automatically when available.)") % action)
            return False
        self._start_open3d_probe()
        self.set_status("Checking Open3D…")
        self.set_banner("Checking Open3D - try again in a moment.", AC)
        return False

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
            # centre the splash on where the main window will appear (its saved place), not on the pointer's monitor:
            # on a multi-monitor desk the two would otherwise open on different screens
            mx,my,mw,mh=self._pointer_monitor()
            m=re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", self.cfg.get("geometry","") or "")
            if m and int(m.group(1))>=400 and int(m.group(2))>=400:      # ignore a degenerate saved size (e.g. old "1x1" corruption) rather than centre on it
                gw,gh,gx,gy=map(int, m.groups()); cx,cy=gx+gw//2, gy+gh//2
            else:
                cx,cy=mx+mw//2, my+mh//2
                self.geometry("+%d+%d" % (mx+(mw-self.winfo_reqwidth())//2, my+40))      # first run: the app opens on this monitor too
            sp.geometry("%dx%d+%d+%d"%(W,H, cx-W//2, cy-H//2))
            try: sp.attributes("-alpha",1.0); sp.attributes("-topmost",True)
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
            lx, ly = W//2, int(H*0.30)
            # a spinner ring around the logo: a faint full track with one bright arc that rotates.
            # It runs on its own timer (see _splash_anim), decoupled from the setup checks, so the
            # motion stays smooth however fast or slow the probes finish.
            R=98
            # Tk canvas arcs are not anti-aliased (jaggy ring): pre-render the 40 positions of the
            # spinner as supersampled PIL images and cycle them - same motion, smooth edges.
            from PIL import ImageDraw
            SS=4; sz=2*R+8; bb=[4*SS, 4*SS, (sz-4)*SS, (sz-4)*SS]; frames=[]
            for i in range(40):
                a=(90-9*i)%360
                im=Image.new("RGBA", (sz*SS, sz*SS), (0,0,0,0)); d=ImageDraw.Draw(im)
                d.ellipse(bb, outline="#1b2230", width=3*SS)                      # faint full track
                d.arc(bb, start=-(a+95), end=-a, fill=AC, width=3*SS)             # the bright arc (PIL angles run clockwise)
                frames.append(ImageTk.PhotoImage(im.resize((sz, sz), Image.LANCZOS)))
            # a 41st, track-only frame: shown while the window is still being built (Tk can't tick a
            # timer then), so the ring never looks like a spinner that got stuck - the bright arc
            # appears and starts turning the moment the mainloop is free and real loading begins.
            im=Image.new("RGBA", (sz*SS, sz*SS), (0,0,0,0)); ImageDraw.Draw(im).ellipse(bb, outline="#1b2230", width=3*SS)
            frames.append(ImageTk.PhotoImage(im.resize((sz, sz), Image.LANCZOS)))
            self.imgs["sp_ring"]=frames; self._sp_ring_i=0
            self._sp_ring=cv.create_image(lx, ly, image=frames[-1])
            if os.path.exists(ICON):
                try:
                    self.imgs["splash"]=ImageTk.PhotoImage(Image.open(ICON).convert("RGBA").resize((150,150), Image.LANCZOS))
                    cv.create_image(lx, ly, image=self.imgs["splash"], anchor="center")
                except Exception as e: log_error("splash-logo", e)
            cv.create_text(W//2, int(H*0.555), text=APP, fill=TX, font=(WORDMARK, 30, "bold"))
            cv.create_text(W//2, int(H*0.635), text="Y O I N K .   C L E A N .   K E E P .",
                           fill=MUT, font=(WORDMARK, 9))
            px0, py, pw, ph = W//2-125, int(H*0.80), 250, 5
            cv.create_rectangle(px0, py, px0+pw, py+ph, fill="#0c0f15", outline="")
            self._sp_fill=cv.create_rectangle(px0, py, px0+1, py+ph, fill=AC, outline="")
            self._sp_px0, self._sp_pw, self._sp_py, self._sp_ph = px0, pw, py, ph
            self._sp_status=cv.create_text(W//2, int(H*0.865), text="Getting things ready…", fill=MUT, font=(WORDMARK, 10))
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
            self._splash_a=1.0; sp.update_idletasks()
            # Blocking, synchronous, HERE: no other thread and no Tk font has been created yet (that happens in
            # _header/_body, called after this returns), so the first import of trimesh/shapely cannot race a
            # worker thread (e.g. a thumbnail render spawned the moment the project list arrives) and cannot
            # finalise a Tk object from the wrong thread. That race was a real deadlock: two
            # threads both doing "import shapely" for the first time, one hung forever in Font.__del__ while
            # holding the module's import lock, the other blocked forever waiting for that same lock.
            _preload()
            self.after(60, lambda: self._run_checks(0))
            self._splash_anim()
        except Exception as e:
            log_error("splash", e); self.deiconify()
    def _splash_anim(self):
        """Spin the bright arc of the loader ring. Self-reschedules until the splash is gone."""
        if not self._splash: return
        try:
            fr=self.imgs.get("sp_ring") or []
            if len(fr)>1:                          # cycle the 40 rotating frames; the last one is the static track
                self._sp_ring_i=(self._sp_ring_i+1)%(len(fr)-1); self._sp_cv.itemconfigure(self._sp_ring, image=fr[self._sp_ring_i])
        except Exception: return
        self.after(16, self._splash_anim)
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
                self.after(250, self._splash_preload_start)   # warm the preview cache before opening, so nothing loads "as we go"
            return
        c=self._checklist[i]
        # one steady line instead of flickering through every package name; the bar carries the progress
        def work():                                 # off the UI thread: a slow probe (a subprocess import under load) must not freeze the window
            try: ok=bool(c["fn"]())
            except Exception: ok=False
            def done():
                c["ok"]=ok; setbar((i+1)/len(self._checklist)); self._run_checks(i+1)
            self.q.put(("call", done))           # never call Tk (not even after) from a worker thread: the queue is drained on the main thread
        threading.Thread(target=work, daemon=True).start()
    def _splash_preload_start(self):
        """During the splash, render MISSING scan previews so the workspace opens warm (no 'loading as we
        go'). Most-recent projects first (the one you'll likely open). Capped so boot never hangs; the cache
        persists, so later launches skip everything, and anything past the cap renders on first click."""
        if not self._splash: return
        dest=self.dest.get() or DEFAULT_DEST
        try:
            names=sorted((d for d in os.listdir(dest) if not d.startswith(".") and os.path.isdir(os.path.join(dest, d))),
                         key=lambda d: -os.path.getmtime(os.path.join(dest, d)))
        except Exception: names=[]
        jobs=[]
        for name in names:
            try:
                for node in self._proc_nodes(name): jobs.append((name, node))
            except Exception: pass
        last=self.cfg.get("last_open")
        if last: jobs.sort(key=lambda j: 0 if j[0]==last else 1)   # warm the project you'll most likely REOPEN first (stable sort keeps the rest newest-first)
        if not jobs: self.after(150, self._close_splash); return
        threading.Thread(target=self._splash_preload_worker, args=(jobs,), daemon=True).start()
    def _splash_preload_worker(self, jobs):
        import time as _t, shade as _sh
        deadline=_t.time()+25; total=len(jobs)
        faces=LIVE_QUALITY_FACES.get(self.cfg.get("live_quality","medium"), 300000)   # the detail the interactive 3D view will actually request
        env=dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
        for i,(name,node) in enumerate(jobs, 1):
            if _t.time()>deadline: break
            try:
                mesh=self._mesh_for_node(name, node)
                if mesh and not mesh.startswith(PROJECTS):
                    verkey=(self._proc_current(name, node) or (None,))[0]
                    out=os.path.join(THUMBS, "%s__%s__%s__shaded.png" % (name, node, verkey or "v"))
                    if not (os.path.exists(out) and os.path.getmtime(out)>=os.path.getmtime(mesh) and os.path.getsize(out)>1024):
                        self._run_child([_sys.executable, os.path.join(HERE, "shade.py"), mesh, out, "--size", "900x600"], timeout=120, env=env)   # the still preview PNG (40k)
                    film=os.path.join(THUMBS, "%s__%s__%s__film.png" % (name, node, verkey or "v"))   # the little strip thumbnail - warm it too, or the scan strip flashes blue -> grey on open
                    if not (os.path.exists(film) and os.path.getmtime(film)>=os.path.getmtime(mesh) and os.path.getsize(film)>1024):
                        self._run_child([_sys.executable, os.path.join(HERE, "shade.py"), mesh, film, "--size", "300x220"], timeout=120, env=env)
                    # warm the EXACT entry the interactive viewer reads (detail faces + normals), independent of the PNG,
                    # so the first 3D open is a pure cache read - not another parse+simplify+normals pass
                    nkey=_sh._mesh_key(mesh, faces, None)
                    npz=os.path.join(_sh.MESH_CACHE, nkey+"_n.npz") if nkey else None
                    if not (npz and os.path.exists(npz)):
                        self._run_child([_sys.executable, os.path.join(HERE, "shade.py"), mesh, "--warm", str(faces)], timeout=180, env=env)
            except Exception as e: log_error("preload", e)
            self.q.put(("splash_preload", i, total))
        self.q.put(("splash_preload_done",))
    def _close_splash(self, force=False):
        """The window appears only when it is complete: checks done and the first project list rendered."""
        if self._splash and not force and not getattr(self, "_first_render_done", False):
            self._checks_done=True
            try: self._sp_cv.itemconfigure(self._sp_status, text="loading your projects…", fill=MUT)
            except Exception: pass
            self.after(150, self._close_splash); return
        # Multiple self.after(150, self._close_splash) calls can already be queued from the
        # "not ready yet" branch above by the time _first_render_done/_checks_done both flip
        # true - each one reaches here and would otherwise re-run the whole forced-paint+reveal
        # sequence a second time (pointyoink.log shows two full
        # forced-first-paint passes, 0.159s then 28.473s, same session - a real ~28s extra
        # freeze this guard prevents).
        if getattr(self, "_splash_closing", False): return
        self._splash_closing=True
        if self._splash:
            # bring the window in invisible, let every widget paint (the splash only covers the middle, so a visible
            # window would be seen building itself), then cross-fade: window in, splash out
            try: self.attributes("-alpha", 0.0)
            except Exception: pass
            self.deiconify()
            # Wait for the REAL first paint, not a fixed guess. A fixed delay here used to let the
            # crossfade start before CustomTkinter's widgets were actually drawn, so the reveal
            # showed a half-built UI fading in. A single update_idletasks() can also return before
            # genuinely done (drawing one widget can queue more idle work), so loop until a pass
            # finds nothing left to do. MUST be update_idletasks(), never plain update(): update()
            # drains every pending X event including raw input (mouse motion, at whatever the
            # mouse's poll rate is) - same machine, same instant:
            # update_idletasks() took 0.27s, update() hung 20+s and never returned while the mouse
            # kept moving. update() was the actual bug this whole fix introduced.
            for _pass in range(6):
                t0=time.perf_counter()
                try: self.update_idletasks()
                except Exception as e: log_error("forced-first-paint", e); break
                dt=time.perf_counter()-t0
                log_line("forced-first-paint[pass %d]: +%.3fs" % (_pass, dt))
                if dt < 0.03: break
            def reveal(step=0):
                a=min(1.0, step/8.0)
                try: self.attributes("-alpha", a)
                except Exception: pass
                if a<1.0: self.after(30, lambda: reveal(step+1))
            reveal(); self._splash_fade(-0.2)
        else:
            self.deiconify()
            try: self.attributes("-alpha", 1.0)
            except Exception: pass
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
        mv_loading=getattr(self,"_mv_loading",False)
        busy = (self._mounting or self.listing or self.pulling or mv_loading
                or getattr(self,"_basing",False) or getattr(self,"_loader",None) is not None)
        self._pt=getattr(self,"_pt",0)+1
        try:
            if busy:
                if getattr(self, "_banner_color", None)!=WARN:   # don't let the busy pulse paint over a warning/error dot (e.g. "mount failed" must stay orange, not blue)
                    self.dot.configure(text_color=(AC if self._pt%2 else "#2b5c8a"))
                self._spin.configure(text=self._SPINNER[self._pt % len(self._SPINNER)], text_color=AC)
                msg = self._status_msg or ("Connecting to the scanner…" if self._mounting else
                      "Reading projects off the scanner…" if self.listing else
                      "Importing…" if self.pulling else
                      "Loading the 3D view…" if mv_loading else "Working…")   # so bottom no longer says "Ready" while the preview loads
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
        vl=ctk.CTkLabel(wm, text=("v%s" % VERSION) if RELEASE else ("v%s · %s" % (VERSION, BUILD)), font=ctk.CTkFont(size=10), text_color=DIM); vl.pack(side="left", padx=(6,0), pady=(6,0))
        self._tip(vl, ("PointYoink v%s." % VERSION) if RELEASE else ("Build %s - the git commit this app is running (a trailing + means uncommitted changes). Match it to `git log --oneline -1` to know it's current." % BUILD))
        self._modes=TabStrip(h, command=lambda lab: self._set_mode(HEADER_KEY.get(lab, lab)), content=False, base=CARD, size=13)
        self._modes.grid(row=0,column=2, sticky="w", padx=(26,0), pady=(8,0))
        self._modes.add("Import", img=_icon("import","default",16)); self._modes.add("Projects", img=_icon("projects","default",16)); self._modes.add("Captures", img=_icon("captures","default",16))
        if HAS_LIVE: self._modes.add("Live view", tag="Planned", img=_icon("live-view","default",16))
        self.mode_sw=_ModeSwitch(self._modes)
        btns=ctk.CTkFrame(h, fg_color="transparent"); btns.grid(row=0,column=3, sticky="e", padx=(0,14)); self._hbtns=btns
        _mic=_icon("menu","muted",18)
        mb=ctk.CTkButton(btns, text=("" if _mic else "≡"), image=_mic, width=36, height=30, corner_radius=6, fg_color="transparent", hover_color=CARD2, text_color=MUT,
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
        mlbl=ctk.CTkLabel(d, text="MIRACO", font=ctk.CTkFont(size=13, weight="bold"), text_color=TX); mlbl.grid(row=0,column=1, padx=(0,16))
        self.dot=ctk.CTkLabel(d, text="●", text_color=MUT, font=ctk.CTkFont(size=14), width=16); self.dot.grid(row=0,column=2, padx=(0,6))
        self.banner=ctk.CTkLabel(d, text="Checking for the scanner…", text_color=MUT, anchor="w", justify="left", font=ctk.CTkFont(size=12))
        self.banner.grid(row=0,column=3, sticky="ew", padx=(0,12))
        _legend="Scanner status:\n  green = connected & ready\n  blue = working (connecting / reading / importing)\n  orange = needs attention (not in File Transfer, mount failed…)\n  grey = idle / no scanner"
        for _w in (mlbl, self.dot): self._tip(_w, _legend)   # so the dot colour isn't a mystery
        self.banner.bind("<Configure>", self._wrap_banner)
        def vsep(col): tk.Frame(d, bg=STROKE, width=1, bd=0, highlightthickness=0).grid(row=0,column=col, sticky="ns", pady=13)
        vsep(4)
        self.wifi_btn=ctk.CTkButton(d, text="  WiFi", image=_icon("wifi","default"), compound="left", width=96, height=32, corner_radius=8, fg_color="transparent", border_width=1,
                                    border_color=STROKE, hover_color=CARD2, text_color=TX, font=ctk.CTkFont(size=12, weight="bold"), command=self.on_wifi)
        self.wifi_btn.grid(row=0,column=5, padx=(12,4))
        self._tip(self.wifi_btn, "Receive a project over WiFi, no cable: the scanner's Share to PC > Wi-Fi sends it straight to PointYoink.")
        self.action_btn=ctk.CTkButton(d, text="  USB", image=_icon("usb","default"), compound="left", width=96, height=32, corner_radius=8, fg_color="transparent", border_width=1,
                                      border_color=STROKE, hover_color=CARD2, text_color=TX, font=ctk.CTkFont(size=12, weight="bold"), command=self.on_mount)
        self.action_btn.grid(row=0,column=6, padx=(4,12))
        self._tip(self.action_btn, "Connect to the scanner over the USB-C cable (it must be in File Transfer mode) and list its projects.")
        vsep(7)
        rb=ctk.CTkButton(d, text="  Refresh", image=_icon("refresh","default"), compound="left", width=96, height=32, corner_radius=8, fg_color="transparent", hover_color=CARD2,
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
        source="device" if (self.listed_src=="device" and mountpoint_seen()) else "local"
        if source=="device": self.hold_banner("Refreshing the scanner project list…", AC)
        else: self.set_status("Refreshing projects on this PC…")
        self.start_listing(source)

    # ---- body: three columns (list | preview | import options) plus the other modes ----
    def _body(self):
        body=ctk.CTkFrame(self, fg_color="transparent"); body.grid(row=2,column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1); body.grid_rowconfigure(0, weight=1)
        # top-level modes (header tabs): Import (projects), Captures, Process, Live view. Only one is shown.
        self.mode_frames={}
        for m in (("Projects","Captures","Process","Live") if HAS_LIVE else ("Projects","Captures","Process")):
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
        srow=ctk.CTkFrame(left, fg_color="transparent"); srow.grid(row=1,column=0, sticky="ew", padx=18, pady=(0,6)); srow.grid_columnconfigure(0, weight=1)
        se=ctk.CTkEntry(srow, placeholder_text="⌕  Search projects…", height=34, corner_radius=8,
                        fg_color="#0d0f14", border_color=STROKE, text_color=TX, placeholder_text_color=MUT, font=ctk.CTkFont(size=12))
        se.grid(row=0,column=0, sticky="ew"); self._search_entry=se
        se.bind("<KeyRelease>", lambda e: (self.search.set(se.get()), self._sync_search_clear()))
        self.search_clear=ctk.CTkButton(srow, text="✕", width=30, height=34, corner_radius=8, fg_color="transparent",
                                        hover_color=CARD2, text_color=MUT, font=ctk.CTkFont(size=13), command=self._clear_search)
        self._tip(self.search_clear, "Clear the search")   # shown only while there's text (see _sync_search_clear)
        self.llist=ctk.CTkScrollableFrame(left, fg_color="transparent"); self.llist.grid(row=2,column=0, sticky="nsew", padx=(8,2), pady=0); self._autohide(self.llist)
        self.llist.bind("<Configure>", lambda e: self._fit_scrollbar_later(self.llist, "vertical"), add="+")
        self.llist.grid_columnconfigure(0, weight=1)
        self.list_empty=None   # the "No projects yet" panel, created by render_list; kept as tall as the list's visible area
        self.llist._parent_canvas.bind("<Configure>", lambda e: self._fit_empty("list_empty", self.llist), add="+")
        self._list_footer_sep=tk.Frame(left, bg=STROKE, height=1, bd=0, highlightthickness=0)   # shown only when the footer has text (import: 'N selected')
        self.sel_lbl=ctk.CTkLabel(left, text="No projects selected", text_color=MUT, anchor="w", font=ctk.CTkFont(size=11))
        self.sel_lbl.grid(row=4,column=0, sticky="ew", padx=18, pady=(7,12))
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
        self.tabs=TabStrip(centre, base=BG, size=13, command=self._on_preview_tab); self.tabs.grid(row=1,column=0, sticky="nsew")
        pv=self.tabs.add("3D Preview"); self._edit_tab=self.tabs.add("Edit"); fl=self.tabs.add("Files")
        try: self.tabs.set_tab_visible("Edit", False)   # hidden until a local project page shows it (Import can't edit)
        except Exception: pass
        self._preview_tab=pv   # the Edit tab reuses this same live-view frame (keeps the camera/object); it has no frame of its own
        ctl=ctk.CTkFrame(self.tabs.bar, fg_color="transparent"); ctl.pack(side="right", pady=(0,4)); self._tab_ctl=ctl   # the 3D-only toolbar (View in 3D / Solid-Wireframe / Reset view); hidden for a flat 2D scanner preview
        self.view_btn=ctk.CTkButton(ctl, text="⟳  View in 3D", width=98, height=30, corner_radius=8, fg_color="transparent", border_width=1,
                                    border_color=STROKE, hover_color=CARD2, text_color=TX, font=ctk.CTkFont(size=12), command=self.on_view_3d)
        self._tip(self.view_btn, "Open this scan in the interactive viewer: drag to rotate, scroll to zoom.")
        self.shade_sw=ctk.CTkSegmentedButton(ctl, values=["Solid","Wireframe"], command=self._shade_mode_changed, height=30, corner_radius=8,
                                             fg_color=CARD2, selected_color=SELB, selected_hover_color=SELB, unselected_color=CARD2, unselected_hover_color=STROKE,
                                             text_color=TX, font=ctk.CTkFont(size=11))
        self.shade_sw.pack(side="right"); self.shade_sw.set("Solid")
        # Mesh vs the scanner's fused point cloud - so you can see what the mesh was built from (e.g. whether
        # One-tap Edit on the device sealed an opening the points show as open).
        self.pts_sw=ctk.CTkSegmentedButton(ctl, values=["Mesh","Points"], command=self._view_mode_changed, height=30, corner_radius=8,
                                           fg_color=CARD2, selected_color=SELB, selected_hover_color=SELB, unselected_color=CARD2, unselected_hover_color=STROKE,
                                           text_color=TX, font=ctk.CTkFont(size=11))
        self.pts_sw.pack(side="right", padx=(0,8)); self.pts_sw.set("Mesh")   # (no tooltip: CTkSegmentedButton.bind raises, like shade_sw)
        # "Reset view" lived here but it did the same thing as "⌂ Fit" in the bottom-left nav - dropped as a
        # duplicate. _reset_view stays (Fit and double-click use it).
        # preview box: the rendered PNG (or the scanner's preview) with a hint line at the bottom
        pv.grid_columnconfigure(0, weight=1); pv.grid_rowconfigure(0, weight=1, minsize=120)
        # corner_radius=0: this panel holds the OpenGL 3D view, which is a real X child window and can't be
        # clipped to rounded corners - its square edges bled past a rounded frame. A
        # square panel matches the viewport it holds. The rest of the app stays rounded.
        bigwrap=ctk.CTkFrame(pv, fg_color="#0a0c10", corner_radius=0, height=120, border_width=1, border_color=STROKE)
        bigwrap.grid(row=0,column=0, sticky="nsew", pady=(10,8)); bigwrap.grid_propagate(False)
        bigwrap.grid_columnconfigure(0, weight=1); bigwrap.grid_rowconfigure(0, weight=1)
        self.big=ctk.CTkLabel(bigwrap, text="Select a project to preview its scans", fg_color="transparent", text_color=MUT)
        self.big.grid(row=0,column=0, sticky="nsew", padx=12, pady=12)
        self.big.bind("<Configure>", self._on_big_resize)
        self.big.bind("<Button-1>", self._enter_3d)   # click the still to open the interactive 3D view
        # interactive 3D: the GPU view (glview.py, full mesh) when OpenGL works in this window, else the
        # software renderer (meshview.py). Same mouse language either way.
        self._mv_wrap=bigwrap; self.mv=self._make_mv(); self._mv_key=None; self._mv_want=None
        self.big_hint=ctk.CTkLabel(bigwrap, text="", text_color=MUT, font=ctk.CTkFont(size=11), fg_color="#0a0c10", corner_radius=0)
        self.big_hint.place(relx=0.5, rely=1.0, y=-10, anchor="s")
        # loading overlay: a spinning ring + the current step, centred on the preview while it works
        # (square to match the squared-off preview panel it sits on)
        self.big_loader=ctk.CTkFrame(bigwrap, fg_color="#11151c", corner_radius=0, border_width=1, border_color=STROKE)
        self._spin_cv=tk.Canvas(self.big_loader, width=44, height=44, bg="#11151c", highlightthickness=0); self._spin_cv.pack(padx=18, pady=(16,6))
        self.big_loader_lbl=ctk.CTkLabel(self.big_loader, text="", text_color=TX, font=ctk.CTkFont(size=12)); self.big_loader_lbl.pack(padx=22, pady=(0,16))
        self._spin_job=None; self._spin_ang=0
        self.renders_lbl=ctk.CTkLabel(bigwrap, text="", text_color=MUT, font=ctk.CTkFont(size=11), fg_color="#0a0c10", corner_radius=6)
        # standard-view nav (Fusion-style): snap the 3D view to Home / Top / Front / Back / Left / Right,
        # so the model is never lost off-screen; drag still gives free rotation to any angle.
        self.view_nav=ctk.CTkFrame(bigwrap, fg_color="#0d1017", corner_radius=6, border_width=1, border_color=STROKE)
        _navbtns=(("Fit",None,None,"Fit the model in view: home angle, zoom and pan reset"), ("Top",0,90,"Top-down"),
                  ("Front",0,0,"Front"), ("Back",180,0,"Back"), ("Left",-90,0,"Left side"), ("Right",90,0,"Right side"))
        for _i,(lab, az, el, tip) in enumerate(_navbtns):
            cmd=self._reset_view if az is None else (lambda a=az,e=el: self._set_view(a,e))
            b=ctk.CTkButton(self.view_nav, text=("  "+lab if az is None else lab), image=(_icon("fit-view","default",16) if az is None else None), compound="left",
                            width=(52 if az is None else 42), height=22, corner_radius=4,
                            fg_color="transparent", hover_color=CARD2, text_color=TX, font=ctk.CTkFont(size=11), command=cmd)
            b.pack(side="left", padx=(5 if _i==0 else 1, 5 if _i==len(_navbtns)-1 else 1), pady=3); self._tip(b, tip)   # equal end gutters
        # The point/mesh editor tools live in the right-side palette (self.editpanel / _build_edit_palette),
        # shown in place of the project panel while editing. No bottom overlay bar any more.
        # nothing selected: an empty state sits over the box (inset so the rounded border stays visible); select_project hides it
        self.big_empty=self._empty_state(bigwrap, "preview"); self.big_empty.grid(row=0,column=0, sticky="nsew", padx=6, pady=6)
        self.film=ctk.CTkScrollableFrame(pv, orientation="horizontal", fg_color="transparent", height=128); self._autohide(self.film, "horizontal")
        self.film.grid(row=1,column=0, sticky="ew"); self.film.grid_remove()
        self.film.bind("<Configure>", lambda e: self.after(80, self._film_fit))
        # Files tab: the project's model files (what an import copies) above the save folder on this PC
        fl.grid_columnconfigure(0, weight=1); fl.grid_rowconfigure(3, weight=1)
        self.files_hdr=ctk.CTkLabel(fl, text="Model files in this project", text_color=MUT, font=ctk.CTkFont(size=11), anchor="w")
        self.files_hdr.grid(row=0,column=0, sticky="ew", pady=(10,2))
        self.files_list=ctk.CTkScrollableFrame(fl, fg_color="#0a0c10", corner_radius=10, height=160)
        self.files_list.grid(row=1,column=0, sticky="ew"); self.files_list.grid_columnconfigure(0, weight=1); self._autohide(self.files_list)
        self._folder_tab=fl

        # -- right: Import options (always visible, scrolls) --
        tk.Frame(pm, bg=STROKE, width=1, bd=0, highlightthickness=0).grid(row=0,column=3, sticky="ns")
        self.side=ctk.CTkFrame(pm, fg_color="transparent", width=278); self.side.grid(row=0,column=4, sticky="nsew")
        self.side.grid_propagate(False); self.side.grid_columnconfigure(0, weight=1); self.side.grid_rowconfigure(0, weight=1)
        self.opts=ctk.CTkScrollableFrame(self.side, fg_color="transparent"); self.opts.grid(row=0,column=0, sticky="nsew", padx=(6,12)); self._autohide(self.opts)
        self.opts.bind("<Configure>", lambda e: self._fit_scrollbar_later(self.opts, "vertical"), add="+")
        self.projpanel=ctk.CTkScrollableFrame(self.side, fg_color="transparent"); self.projpanel.grid(row=0,column=0, sticky="nsew", padx=(6,12)); self.projpanel.grid_remove(); self._autohide(self.projpanel)
        self.projpanel.bind("<Configure>", lambda e: self._fit_scrollbar_later(self.projpanel, "vertical"), add="+")
        self.editpanel=ctk.CTkScrollableFrame(self.side, fg_color="transparent"); self.editpanel.grid(row=0,column=0, sticky="nsew", padx=(6,12)); self.editpanel.grid_remove(); self._autohide(self.editpanel)
        self._build_edit_palette(self.editpanel)   # the point/mesh editor tools live here (shown in place of the project panel while editing)
        self.rail_btns={}; self.rail_bars={}

        # -- Process mode: the selected project's scans, each with its versions and the tools --
        self._build_process_page(self.mode_frames["Process"])

        # Captures mode: device screenshots AND screen recordings, out of the project list
        sc=self.mode_frames["Captures"]
        sc.grid_columnconfigure(0, weight=1); sc.grid_rowconfigure(1, weight=1)
        sctop=ctk.CTkFrame(sc, fg_color="transparent"); sctop.grid(row=0,column=0, sticky="ew", padx=10, pady=(10,4))
        self.shots_lbl=ctk.CTkLabel(sctop, text="Screenshots & recordings on the device", text_color=MUT,
                                    font=ctk.CTkFont(size=12)); self.shots_lbl.pack(side="left")
        _pab=ctk.CTkButton(sctop, text="⤓ Pull all", width=96, height=30, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE,
                      hover_color=CARD2, text_color=TX, command=self.pull_screenshots); _pab.pack(side="right", padx=4)
        self._tip(_pab, "Copy every screenshot & recording off the scanner into your save folder's “captures” subfolder (Open folder shows where).")
        ctk.CTkButton(sctop, text="↻ Refresh", width=96, height=30, corner_radius=8, fg_color=CARD2,
                      hover_color=STROKE, text_color=TX, command=self.refresh_screenshots).pack(side="right", padx=4)
        self.shots=ctk.CTkScrollableFrame(sc, fg_color="#0a0c10", corner_radius=10); self._autohide(self.shots)
        self.shots.bind("<Configure>", lambda e: self._fit_scrollbar_later(self.shots, "vertical"), add="+")
        self.shots.grid(row=1,column=0, sticky="nsew", padx=10, pady=(0,10))
        for c in range(4): self.shots.grid_columnconfigure(c, weight=1)
        self._shots_items=[]
        # no scanner yet: the empty state fills the visible area (the scrollable frame only grows with content)
        self.shots_empty=self._empty_state(self.shots, "captures")
        self.shots_empty.grid(row=0,column=0,columnspan=4, sticky="nsew")
        self.shots._parent_canvas.bind("<Configure>", lambda e: self._fit_empty("shots_empty", self.shots), add="+")
        # Live view (MIRACO camera over USB + position over WiFi) is a dev-only experiment, built
        # only when the local dev/live_view.py module is present (never in shipped builds).
        if HAS_LIVE: self.build_live_tab(self.mode_frames["Live"])

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
        btns={"captures": [("Connect over USB", self.on_mount), ("How to connect?", self._usb_help)],   # screenshots only come over USB
              "projects": [("Connect over USB", self.on_mount), ("Share over WiFi", self.on_wifi), ("How to connect?", self._usb_help)],
              "preview":  []}[kind]
        es=EmptyState(parent, kind, btns, scale=self._ui_scale)
        tips={"captures": "Plug in the USB-C cable and tap File Transfer on the scanner first.",
              "projects": "USB lists every project on the scanner (it must be in File Transfer mode)."}
        if es.buttons and kind in tips: self._tip(es.buttons[0], tips[kind])
        if kind=="projects" and len(es.buttons)>1: self._tip(es.buttons[1], "No cable: the scanner's Share to PC > Wi-Fi sends one project straight here.")
        if es.buttons: self._tip(es.buttons[-1], "Step-by-step: how to put the scanner in File Transfer mode and connect.")
        return es
    def _fit_empty(self, attr, sf):
        """Keep the empty-state frame stored as self.<attr> as tall as the scrollable frame's visible area
        (a scrollable frame only grows with its content, so the panel would otherwise sit in a strip)."""
        w=getattr(self, attr, None)
        try:
            if w is None or not w.winfo_exists() or not w.winfo_manager(): return
            h=sf._parent_canvas.winfo_height(); wd=sf._parent_canvas.winfo_width()
            if h>1 and abs(h-w.winfo_height())>2: w.configure(height=h)
            if wd>1 and abs(wd-w.winfo_width())>2: w.configure(width=wd)       # a scrollable frame does not stretch its content sideways
        except Exception: pass
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
        _expl=ctk.CTkLabel(op, text="A quick copy of every scan as it comes in - originals kept. Export on the Projects page picks one model, with a size and mesh check.", text_color=MUT, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=240)
        _expl.pack(fill="x", padx=6, pady=(4,0))
        op.bind("<Configure>", lambda e,l=_expl: l.configure(wraplength=max(180, e.width-24)), add="+")   # wrap to the panel, don't clip at the right edge
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
        self.ftree.grid(row=0,column=0, sticky="nsew", padx=(4,0), pady=4)
        fsb=ctk.CTkScrollbar(tw, command=self.ftree.yview, fg_color="transparent"); fsb.grid(row=0,column=1, sticky="ns", padx=(0,3), pady=(28,6))   # start below the treeview's column header so it doesn't ride up into it
        self.ftree.configure(yscrollcommand=fsb.set); self._ftree_sb=fsb   # the save folder holds thousands of files: it must scroll
        self.ftree.bind("<<TreeviewOpen>>", self._folder_expand); self.ftree.bind("<Double-1>", self._folder_open)
        self.ftree.tag_configure("dir", foreground=AC); self.ftree.tag_configure("mesh", foreground=OK)
        tw.bind("<Configure>", self._fit_folder)
        self.side_mode=None; self._folder_loaded=False

    def set_side(self, key, open_only=False):
        """Kept for callers (older paths). The side panel is now the always-visible Import options
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
        self._cur_mode=m   # remember the visible tab so the bottom bar doesn't say "Editing <project>" on Captures/Live
        try: self.tabs.set_tab_visible("Edit", self.page=="projects", before="Files")   # can't edit scanner projects on the Import page
        except Exception: pass
        for k,f in self.mode_frames.items():
            if f is target: f.grid()
            else: f.grid_remove()
        self._modes.set("Projects" if m in ("Local","Process") else MODE_LABEL.get(m, m))
        if m in ("Projects","Local"): self._apply_page()
        elif m=="Process": self._proc_refresh()
        if m=="Projects" and self.tabs.get()=="Files" and not self._folder_loaded: self._folder_loaded=True; self.refresh_folder()
        if m=="Captures" and not getattr(self,"_shots_loaded",False) and not getattr(self,"_shots_busy",False):
            self.after(60, self.refresh_screenshots)   # land on Captures -> read device captures if connected, else show the locally-cached ones; never sit on a dead "No captures yet"
        self.update_summary()   # refresh the bottom bar for the tab we switched to (clears a stale "Editing <project>" on Captures/Live)
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
        elif not self.selected and self.projbar.winfo_ismapped(): self._clear_selection()   # no selection but the centre still shows a project (e.g. after deleting it): reset it
        if not imp:
            self._panel_refresh()
            if not self.cfg.get("seen_howto") and self.projects and os.environ.get("POINTYOINK_NO_HOWTO")!="1": self._howto_when_ready()
        self.update_summary()   # bottom status reflects the page (Import: batch selection; Projects: what's open)
    def _clear_selection(self):
        """Nothing selected on this page: the centre goes back to its empty state."""
        self.selected=None; self._film_sel=None; self._film_cells={}
        try:
            self.next_strip.pack_forget(); self.projbar.grid_remove(); self.film.grid_remove(); self.proj_empty.grid()
            self._mv_key=None; self.mv.grid_remove(); self.big.grid(); self.big_empty.grid(); self.big_empty.lift()
            self.view_nav.place_forget()
            self._set_files_rows(msg="Pick a project to see its model files.")   # don't leave the last project's file list up when nothing is picked
        except Exception as e: log_error("clear selection", e)
        try: self.update_summary()
        except Exception: pass
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
    def _set_files_rows(self, msg=None, name=None, files=None, tot=0):
        """Render the project's model files as styled rows (a mesh/points icon, the name, its kind and node,
        the size), instead of the old monospace text dump."""
        fl=getattr(self, "files_list", None)
        if fl is None: return
        for w in fl.winfo_children(): w.destroy()
        if msg is not None:
            self.files_hdr.configure(text="Model files in this project")
            ctk.CTkLabel(fl, text=msg, text_color=MUT, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=360).pack(fill="x", padx=10, pady=8)
            return
        self.files_hdr.configure(text="Model files in this project  ·  %s total" % human(tot))
        if not files:
            ctk.CTkLabel(fl, text="No model files yet - this project is raw scan data.\nBuild the models on the Projects page, or One-tap Edit on the scanner and share it again.",
                         text_color=WARN, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=360).pack(fill="x", padx=10, pady=8)
            return
        for node, fn, sz in sorted(files, key=lambda x: -x[2]):
            is_cloud=(fn=="fuse.ply" or fn.endswith("_cloud.ply"))
            row=ctk.CTkFrame(fl, fg_color=CARD2, corner_radius=8); row.pack(fill="x", padx=6, pady=3)
            ic=_icon("points" if is_cloud else "mesh", "default", 18)
            if ic is not None: ctk.CTkLabel(row, image=ic, text="").pack(side="left", padx=(10,8), pady=6)
            txt=ctk.CTkFrame(row, fg_color="transparent"); txt.pack(side="left", fill="x", expand=True, pady=4)
            ctk.CTkLabel(txt, text=fn, text_color=TX, font=ctk.CTkFont(size=12), anchor="w").pack(fill="x")
            ctk.CTkLabel(txt, text=("Point cloud" if is_cloud else "Mesh")+"  ·  "+node, text_color=DIM, font=ctk.CTkFont(size=10), anchor="w").pack(fill="x")
            ctk.CTkLabel(row, text=human(sz), text_color=MUT, font=ctk.CTkFont(size=11, weight="bold")).pack(side="right", padx=12)
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
        self._imp_samples=[]; self._imp_last=0.0; self._imp_top=None   # USB import transfer popup (WiFi-style)
        self.progline=ctk.CTkLabel(a, text="", text_color=MUT, anchor="w", font=ctk.CTkFont(size=11))
        def outlined(text, cmd, w=130, icon=None):
            return ctk.CTkButton(a, text=text, image=(_icon(icon,"default") if icon else None), compound="left", width=w, height=40, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE,
                                 hover_color=CARD2, text_color=TX, font=ctk.CTkFont(size=13), command=cmd)
        self.open_btn=outlined("  Open folder", self.open_folder, icon="external-link"); self.open_btn.grid(row=0,column=1, padx=6, pady=(12,4))
        self.zip_btn=outlined("  Export ZIP", self.on_export_zip, icon="archive"); self.zip_btn.grid(row=0,column=2, padx=6, pady=(12,4))
        self.cancel_btn=ctk.CTkButton(a, text="Cancel", width=150, height=40, corner_radius=8,
                                      fg_color="#3a2530", hover_color=DANGER, text_color=TX, command=self.on_cancel)
        self.import_btn=SplitButton(a, "Import selected", self.on_pull,
                                    [("Import selected", self.on_pull), ("Export ZIP of selected", self.on_export_zip),
                                     ("Select all projects", self.select_all), ("Open save folder", self.open_folder)])
        self.import_btn.grid(row=0,column=3, padx=(6,20), pady=(12,4))

    # ---- dialogs ----
    def _btn_busy(self, btn, label):
        """Show a button as working - dark, dim readable label, clicks blocked - instead of Tk's disabled
        look (grey letters on a bright button). Remembers the button's own look for _btn_idle."""
        try:
            if not hasattr(btn, "_idle_cfg"):
                btn._idle_cfg={k: btn.cget(k) for k in ("text","fg_color","hover_color","text_color")}
            btn.configure(text=label, fg_color=CARD2, hover_color=CARD2, text_color=DIM, text_color_disabled=DIM, state="disabled")
        except Exception: pass
    def _btn_idle(self, btn):
        try:
            c=getattr(btn, "_idle_cfg", None)
            btn.configure(state="normal", **(c or {}))
        except Exception: pass
    def _centred(self, w, h):
        """Opening geometry for a popup: centred on the main window and clamped so it never opens bigger
        than the app (room left for its own title bar). Only the first pop - the user can drag or resize it
        anywhere after. Also gives an off-screen render a real position instead of a missing window
        manager parking it top-left."""
        try:
            self.update_idletasks()
            pw, ph = self.winfo_width(), self.winfo_height()
            if pw > 1 and ph > 1:
                px, py = self.winfo_rootx(), self.winfo_rooty()
                w = min(w, max(420, pw - 60)); h = min(h, max(320, ph - 85))
            else:                                   # not mapped yet: fall back to the screen centre
                px = py = 0; pw, ph = self.winfo_screenwidth(), self.winfo_screenheight()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            TITLE = 36                             # the WM puts the popup's own title bar ABOVE the +y we ask for: pull up to centre the whole frame
            x = min(max(0, px + (pw - w)//2), max(0, sw - w))
            y = min(max(0, py + (ph - h)//2 - TITLE), max(0, sh - h))
            return "%dx%d+%d+%d" % (w, h, x, y)
        except Exception:
            return "%dx%d" % (w, h)
    def _top(self, title, w=560, h=440, key=None):
        key=key or title
        if not hasattr(self,"_dialogs"): self._dialogs={}
        ex=self._dialogs.get(key)
        if ex is not None:
            try:
                if ex.winfo_exists():
                    ex.deiconify(); ex.lift(); ex.focus_force(); return None
            except Exception: pass
        t=ctk.CTkToplevel(self); t.title("%s · %s" % (APP, title)); t.configure(fg_color=BG)
        t.geometry(self._centred(w, h))          # front and centre on the main window, never bigger than it on first pop
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
        # without this, closing via the window's own X button skips choose() entirely, so
        # wait_window() below can return through the except (or never) with the grab still held
        # on a dialog that's gone - the whole app then looks frozen.
        dlg.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        try:
            dlg.grab_set(); dlg.wait_window()
        except Exception: pass
        finally:
            try: dlg.grab_release()
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
    def _usb_help(self):
        """Step-by-step for connecting over USB, matching the scanner's own 'Share to PC -> USB Cable' screen,
        plus the recovery steps when it won't connect (unplug, wait, re-tap File Transfer)."""
        t=self._top("Connect over USB", 560, 600)
        if t is None: return
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=16, pady=16)
        ctk.CTkLabel(card, text="Connect the scanner over USB", text_color=TX, font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(18,2))
        ctk.CTkLabel(card, text="On the scanner: Share to PC  ›  USB Cable", text_color=MUT, font=ctk.CTkFont(size=11)).pack()
        body=ctk.CTkFrame(card, fg_color="transparent"); body.pack(fill="x", padx=22, pady=(14,4))
        for num,txt in (("1","Plug the USB-C cable into the scanner and this PC."),
                        ("2","A window pops up on the scanner - tap “File Transfer” (not “PC Mode”)."),
                        ("3","Click “Connect over USB” here (or Rescan). The scanner's projects appear on the left.")):
            row=ctk.CTkFrame(body, fg_color="transparent"); row.pack(fill="x", pady=6)
            ctk.CTkLabel(row, text=num, text_color="#04121f", fg_color=AC, corner_radius=13, width=26, height=26,
                         font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0,12))
            ctk.CTkLabel(row, text=txt, text_color=TX, font=ctk.CTkFont(size=12), justify="left", wraplength=430, anchor="w").pack(side="left", fill="x", expand=True)
        self._hr(card, pady=(10,8))
        ctk.CTkLabel(card, text="If it won't connect", text_color=TX, font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(fill="x", padx=22)
        for txt in ("No pop-up on the scanner? It's on a Model/edit screen or in PC mode - go back to Share to PC first.",
                    "Still stuck: unplug the cable, wait ~5 seconds for it to disconnect, then plug back in and tap File Transfer again.",
                    "Need the raw scan data (to rebuild on the PC)? That's very slow over USB - use Share over WiFi › Full project instead."):
            r=ctk.CTkFrame(card, fg_color="transparent"); r.pack(fill="x", padx=22, pady=3)
            ctk.CTkLabel(r, text="•", text_color=MUT).pack(side="left", padx=(2,8), anchor="n")
            ctk.CTkLabel(r, text=txt, text_color=MUT, font=ctk.CTkFont(size=11), justify="left", wraplength=440, anchor="w").pack(side="left", fill="x", expand=True)
        br=ctk.CTkFrame(card, fg_color="transparent"); br.pack(side="bottom", pady=(10,16))
        ctk.CTkButton(br, text="Connect over USB", width=170, height=36, corner_radius=8, fg_color=AC, hover_color=AC_H,
                      text_color="#04121f", font=ctk.CTkFont(size=12, weight="bold"), command=lambda:(t.destroy(), self.on_mount())).pack(side="left", padx=6)
        ctk.CTkButton(br, text="Close", width=100, height=36, corner_radius=8, fg_color="transparent", border_width=1,
                      border_color=STROKE, hover_color=CARD2, text_color=TX, command=t.destroy).pack(side="left", padx=6)
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
        ctk.CTkLabel(t, text="build "+BUILD, font=ctk.CTkFont(size=11), text_color=DIM).pack()
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
        qr=ctk.CTkFrame(t, fg_color="transparent"); qr.pack(fill="x", padx=20, pady=(10,0))
        ctk.CTkLabel(qr, text="Live 3D preview detail", text_color=TX).pack(side="left")
        qv=ctk.StringVar(value=LIVE_QUALITY_LABEL.get(self.cfg.get("live_quality","medium"), "Medium"))
        ctk.CTkOptionMenu(qr, variable=qv, values=list(LIVE_QUALITY_LABEL.values()), width=230, fg_color="#0d0f14", button_color=CARD2,
                          button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=10).pack(side="left", padx=8)
        ctk.CTkLabel(qr, text="triangles kept for rotating: 100k fast · 300k · 1M crisp (exports are never reduced)", text_color=MUT, font=ctk.CTkFont(size=10)).pack(side="left")
        pr=ctk.CTkFrame(t, fg_color="transparent"); pr.pack(fill="x", padx=20, pady=(10,0))
        ctk.CTkLabel(pr, text="Build 3D models on", text_color=TX).pack(side="left")
        fdv=ctk.StringVar(value={"cpu":"CPU only"}.get(self.cfg.get("fuse_device","auto"), "NVIDIA GPU when available"))
        ctk.CTkOptionMenu(pr, variable=fdv, values=["NVIDIA GPU when available","CPU only"], width=230, fg_color="#0d0f14", button_color=CARD2,
                          button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=10).pack(side="left", padx=8)
        rdv=ctk.BooleanVar(value=bool(self.cfg.get("register_drift", True)))
        rdc=ctk.CTkCheckBox(t, text="Fix drift before building a scan the scanner never fused (registers the frames; a few minutes for a long scan)", variable=rdv,
                            fg_color=AC, hover_color=AC_H, border_color=DIM, text_color=TX, font=ctk.CTkFont(size=12), checkbox_width=20, checkbox_height=20, corner_radius=5)
        rdc.pack(anchor="w", padx=20, pady=(6,0))
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
            self.cfg["live_quality"]=next((k for k,v in LIVE_QUALITY_LABEL.items() if v==qv.get()), "medium")
            self.cfg["fuse_device"]="cpu" if fdv.get().startswith("CPU") else "auto"; self.cfg["register_drift"]=bool(rdv.get())
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

    def _safe_geometry(self):
        """self.geometry(), but never a degenerate size (e.g. "1x1") caught mid-startup before the
        window has actually been sized/mapped - keep whatever was there before instead of
        overwriting a good saved size with garbage that then loads tiny next time."""
        g=self.geometry()
        try:
            w,h=(int(v) for v in g.split("+")[0].split("x"))
            if w>=400 and h>=400: return g
        except Exception: pass
        return self.cfg.get("geometry", g)
    def _persist(self):
        self.cfg.update(dest=self.dest.get(), models_only=self.models_only.get(),
                        auto_open=self.auto_open.get(), geometry=self._safe_geometry(),
                        exp_stl=self.exp_stl.get(), exp_obj=self.exp_obj.get(), exp_glb=self.exp_glb.get(),
                        fuse_voxel=round(float(self.fuse_voxel.get() or 0.4),2),
                        clean_isolation=self._num(self.clean_iso,15,0,100), clean_fill_holes=self.clean_holes.get(),
                        clean_smooth_times=int(self._num(self.clean_smooth,3,0,50)), clean_keep_pct=self._num(self.clean_keep,100,1,100),
                        clean_do_iso=self.clean_do_iso.get(), clean_do_smooth=self.clean_do_smooth.get(), clean_do_keep=self.clean_do_keep.get(), clean_do_base=self.clean_do_base.get(),
                        scanner_ip=(self.live_ip.get().strip() if getattr(self, "live_ip", None) is not None else self.cfg.get("scanner_ip","")),
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
        # edit_time only advances on a real edit, so ">" is right there; counts use "!=" so a scan swapped
        # or removed on the device (same-ish count, different content) is still flagged, not just additions.
        # (A same-count, same-edit_time in-place mesh replacement can't be detected without a content hash,
        # which the recorded sig doesn't carry - accepted limitation.)
        return ((p.get("edit_time") or 0) > (sig.get("edit_time") or 0)
                or (p.get("nodes") or 0) != (sig.get("nodes") or 0)
                or (p.get("meshes") or 0) != (sig.get("meshes") or 0))
    def rename_project(self, name):
        """Give a project a friendly name. Same dialog as Name this scan, so they look and land the same."""
        cur=self.records.get(name, {}).get("label") or ""
        t=self._top("Name this project", 420, 190, key="projname")
        if t is None: return
        ctk.CTkLabel(t, text="A name for this project. Its id (%s) stays as the folder name. Leave empty to go back to the id." % name,
                     text_color=MUT, font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=380).pack(fill="x", padx=20, pady=(18,8))
        v=ctk.StringVar(value=cur); e=ctk.CTkEntry(t, textvariable=v, fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=10); e.pack(fill="x", padx=20); e.focus_set()
        def ok(*_):
            self.records.setdefault(name,{})["label"]=(v.get().strip() or None)
            self._persist(); self._dialogs.pop("projname", None); t.destroy()
            self.projects_sig=None  # force re-render
        e.bind("<Return>", ok)
        br=ctk.CTkFrame(t, fg_color="transparent"); br.pack(fill="x", padx=16, pady=14)
        ctk.CTkButton(br, text="Save", width=100, height=32, corner_radius=16, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=ok).pack(side="right", padx=6)
        ctk.CTkButton(br, text="Cancel", width=90, height=32, corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=lambda: (self._dialogs.pop("projname", None), t.destroy())).pack(side="right", padx=6)
    def _install_handoff(self):
        """When a newer build launches, _acquire_single_instance SIGTERMs the running one. Catch it and
        close cleanly. The periodic tick also keeps the Python interpreter ticking so the signal handler
        actually runs while Tk owns the main loop."""
        self._handoff_requested = False; self._handoff_req_mono = 0.0
        def _on_term(*_a):
            if not self._handoff_requested: self._handoff_req_mono = time.monotonic()   # keep the FIRST arrival
            self._handoff_requested = True
        # Install the real handler FIRST, then fold in anything the early handler already caught. A signal
        # landing between these lines is safe: after this line it hits _on_term; before it, _SIGTERM_PENDING
        # is set and we read it just below - so no request is lost.
        try:
            signal.signal(signal.SIGTERM, _on_term)
        except Exception: pass
        if _SIGTERM_PENDING:
            self._handoff_requested = True; self._handoff_req_mono = _SIGTERM_TIME or time.monotonic()
        self._handoff_tick()

    def _handoff_busy(self):
        """Never hand off while there's work that closing would lose or corrupt. The newer instance then
        times out on the lock and shows the ordinary 'already running' window instead of yanking this one
        out from under the work."""
        if getattr(self, "_edit_dirty", False) or getattr(self, "_edit_saving", False): return True   # unsaved editor edits / a save in flight
        if getattr(self, "_fusing", False) or getattr(self, "pulling", False): return True             # a build or an import/zip
        if getattr(self, "_wifi", None): return True          # a WiFi receive is in progress (set before 'pulling')
        if getattr(self, "_mounting", False): return True     # mounting the device over MTP
        if self.live_busy(): return True                     # a dev live-view stream is up or connecting (no-op in shipped builds)
        try:
            with self._children_lock:
                if self._children: return True                # a heavy subprocess (Prepare / Build / Combine / cut) is running
        except Exception: pass
        return False

    def _handoff_tick(self):
        if getattr(self, "_handoff_requested", False):
            self._handoff_requested = False
            age = time.monotonic() - getattr(self, "_handoff_req_mono", 0.0)
            if age > 6.0:
                # the requester waits ~8s for the lock then gives up and shows 'already running'. Only accept
                # a request young enough to leave room for our own cleanup before that timeout; an older one
                # (a long-blocked tick or slow startup) is stale - honouring it would close us after our
                # replacement already bailed, leaving nothing open. Monotonic clock so a wall-clock jump can't
                # make a fresh request look stale or vice versa.
                try: log_line("handoff-ignored: request %.1fs old (requester has given up)" % age)
                except Exception: pass
            elif self._handoff_busy():
                try: log_line("handoff-refused: busy (unsaved edits or an operation is running)")
                except Exception: pass
            else:
                self._handoff_close(); return
        try: self.after(300, self._handoff_tick)
        except Exception: pass

    def _handoff_close(self):
        """A newer build is taking over. Bow out without prompting (only reached when nothing is in
        flight, per _handoff_busy), but still run the real device cleanup so we don't leave the scanner
        or a stream busy."""
        self._closing = True   # from here on _popen refuses to spawn a child that would outlive us
        # Arm the exit watchdog FIRST, before log_line or any other call that could touch a slow/hung
        # filesystem: even if everything below stalls, this forces the exit so we can't linger past the newer
        # instance's lock wait and strand the handoff (it waits longer than this bound, see _acquire...).
        try: threading.Thread(target=lambda: (time.sleep(4.0), os._exit(0)), daemon=True).start()
        except Exception: pass
        try: log_line("handoff-close: newer build took over")
        except Exception: pass
        try:
            if self._wifi: self._wifi.stop()
        except Exception: pass
        try: self.live_cleanup()   # stop any dev live-view stream (no-op in shipped builds)
        except Exception: pass
        try: self._terminate_children()
        except Exception: pass
        try: self._persist()   # flush config so the newer build opens on the same state
        except Exception: pass
        try: self.withdraw()
        except Exception: pass
        try: self.quit()
        except Exception: pass
        os._exit(0)

    def on_close(self):
        if not self._guard_unsaved_edits(): return   # don't let closing the app silently drop unsaved editor edits
        self._closing = True   # _popen reaps any child started from here on, so nothing outlives _terminate_children
        try:
            if self._wifi: self._wifi.stop()
        except Exception: pass
        # os._exit(0) does NOT kill child processes, so a live v4l2-ctl stream would otherwise outlive
        # the GUI and keep the camera busy. live_cleanup turns the projector off and stops the streams
        # (bounded, ~10s worst case if live); a no-op in shipped builds with no Live tab.
        try: self.live_cleanup()
        except Exception: pass
        try: self._terminate_children()
        except Exception as e: log_error("child-cleanup", e)
        self._persist()
        # tearing down thousands of widgets one by one is what made closing look like popups dying in slow motion:
        # hide the window first, then leave; daemon threads and child processes go with us.
        # update_idletasks() used to be called here too, but it's the exact same call proven
        # (repeated SIGUSR1 thread dumps, identical stuck stack each time) to hang for a sustained
        # period inside CustomTkinter's own scrollbar redraw code on this GNOME/X11 desktop -
        # os._exit(0) below exits regardless, so nothing here needs pending idle tasks flushed first.
        try: self.withdraw()
        except Exception: pass
        try: self.quit()
        except Exception: pass
        os._exit(0)

    # ---- helpers ----
    def browse(self):
        d=filedialog.askdirectory(initialdir=self.dest.get() or HOME)
        if d: self.dest.set(d)
    def set_banner(self, text, color): self.banner.configure(text=text); self.dot.configure(text_color=color); self._banner_color=color
    def hold_banner(self, text, color, secs=5.0):
        """Device / scanner / WiFi state owns the TOP MIRACO line (never the bottom bar). Holding it for a
        few seconds keeps the periodic USB probe from overwriting an active WiFi/network status; an ongoing
        operation keeps calling this so the hold refreshes, and when it stops the probe reclaims the line
        within `secs` - self-healing, no per-endpoint cleanup needed."""
        self._dev_hold=time.time()+secs; self.set_banner(text, color)
    def _probe_banner(self, text, color):
        """The probe's own banner write, suppressed while a device op is holding the line."""
        if time.time() >= getattr(self, "_dev_hold", 0.0): self.set_banner(text, color)
    def select_all(self):
        for v in self.pull_sel.values(): v.set(True)
        self.update_summary()
    def select_none(self):
        for v in self.pull_sel.values(): v.set(False)
        self.update_summary()
    def update_summary(self):
        cm=getattr(self, "_cur_mode", None)
        if cm=="Captures":                      # not editing a project here - don't leave "Editing <project>" in the bottom bar
            self.sel_lbl.configure(text="Captures"); self.summary.configure(text="Captures · the scanner's screenshots & recordings (USB)"); return
        if cm=="Live":
            self.sel_lbl.configure(text="Live view"); self.summary.configure(text="Live view · scanner cameras"); return
        if getattr(self, "page", "import")=="projects":
            # ONE status: the open project shows in the title and the bottom bar. No floating "Open: X" footer.
            nm=self.disp(self.selected) if self.selected else None
            self.sel_lbl.configure(text="")
            try: self._list_footer_sep.grid_remove()
            except Exception: pass
            editing=getattr(self, "_in_edit_mode", False)
            try:
                self.summary.configure(text=(("Editing %s" % nm) if editing else nm) if nm else "Working locally · pick a project on the left")
            except Exception: pass
            return
        sel=[n for n,v in self.pull_sel.items() if v.get()]; n=len(sel); s="" if n==1 else "s"
        self.sel_lbl.configure(text=("%d project%s selected"%(n,s)) if n else "No projects selected")
        try: self._list_footer_sep.grid(row=3,column=0, sticky="ew", padx=12, pady=(6,0)) if n else self._list_footer_sep.grid_remove()
        except Exception: pass
        if not sel:
            self.summary.configure(text="No projects selected"); self.import_btn.configure(text="Import selected"); return
        known=[self.size_cache.get(x) for x in sel]; tot=sum(z for z in known if z); miss=sum(1 for z in known if not z)
        est=" · %s%s"%(human(tot), "+" if miss else "") if tot else ""
        note="" if self.models_only.get() else " · full, with raw frames"
        self.summary.configure(text="%d project%s%s%s"%(n,s,est,note))
        self.import_btn.configure(text="Import %d project%s"%(n,s))
    def _search_changed(self):
        self.projects_sig=None
        self._sync_search_clear()
        if self.projects: self.render_list(self.projects)
    def _sync_search_clear(self):
        """Show the search clear (✕) only while there's text to clear."""
        try:
            if (self.search.get() or "").strip():
                if not self.search_clear.winfo_manager(): self.search_clear.grid(row=0,column=1, padx=(4,0))
            else:
                self.search_clear.grid_remove()
        except Exception: pass
    def _clear_search(self):
        try:
            self._search_entry.delete(0,"end"); self.search.set(""); self._sync_search_clear(); self._search_entry.focus_set()
        except Exception: pass

    # ---- polling ----
    def refresh_loop(self):
        # One startup probe only. Periodic idle polling made the app harder to reason about while chasing
        # keyboard stalls; USB/Rescan/Refresh now perform explicit work when the user asks. This probe
        # still reads only sysfs and /proc/self/mountinfo, never the MTP/FUSE tree.
        if not self.pulling and not self._wifi and not getattr(self, "_refresh_probe_busy", False):
            self._refresh_probe_busy=True
            def probe():
                try:
                    st,serial=usb_state(); mounted=mountpoint_seen()
                except Exception as e:
                    log_error("refresh-probe", e); st,serial,mounted="absent",None,False
                self.q.put(("refresh_probe", st, serial, mounted))
            self._start_thread(probe, name="refresh-probe")
    def _refresh_probe_done(self, st, serial, mounted):
        self._refresh_probe_busy=False; self._device_mounted=mounted
        self.serial=serial
        if self.listed_src is None and not self.listing:
            self.listed=False; self.start_listing("local")   # initial view: local only, no scanner probe
        elif not mounted and self.listed_src=="device":
            self.listed=False; self.start_listing("local")   # scanner disappeared; fall back without poking MTP
        if st=="absent":
            # editing saved scans without a scanner is normal - don't cry wolf. Prompt only on the Import page.
            if self.page=="import": self._probe_banner("Scanner not detected - plug in the USB-C cable, or use WiFi.", WARN)
            else: self._probe_banner("Working on saved scans · connect the scanner over USB or WiFi to import more.", MUT)
            self.action_btn.configure(text="🔌  USB", state="normal"); self.auto_tried=False
        elif st=="adb":
            self._probe_banner("MIRACO detected · Not connected - tap “File Transfer” on the scanner", WARN)
            self.action_btn.configure(text="🔌  USB", state="normal"); self.auto_tried=False
        elif st=="mtp" and not mounted:
            self.action_btn.configure(text="🔌  USB", state="normal")
            if self._mounting: self._probe_banner("Connecting…", AC)
            else: self._probe_banner("MIRACO detected · click USB when you're ready to read it", AC)
        elif mounted:
            self.action_btn.configure(text="🔌  Rescan", state="normal")
            if self.listed_src=="device" and self.listed:
                if self.page=="import": self._probe_banner("Connected - tick scans to import, click one to preview.", OK)
                else: self._probe_banner("MIRACO connected - open the Import tab to bring its projects over.", OK)   # guide, don't leave them wondering
                if self.projects: self.render_list(self.projects)   # refresh badges if files changed on disk (cheap no-op otherwise)
            elif not self.listing:
                # MTP reads are slow, so only auto-read when the user is actually on the Import tab -
                # never slow-scan the scanner while they're editing local scans on the Projects page.
                if self.page=="import":
                    self._probe_banner("Connected - reading scanner projects…", AC)
                    self.start_listing("device")
                else:
                    self._probe_banner("MIRACO connected - open the Import tab to read its projects.", AC)
    def start_listing(self, source=None):
        if self.listing: return
        dest=self.dest.get() or DEFAULT_DEST
        if source not in ("device", "local"):
            source="device" if (self.listed_src=="device" and mountpoint_seen()) else "local"
        if source=="device":
            if not mountpoint_seen():
                self.set_banner("USB is not mounted - click USB after tapping File Transfer on the scanner.", WARN)
                source="local"
            else:
                now=time.time(); cool=getattr(self, "_device_touch_cool_until", 0.0)
                if now<cool:
                    self.set_banner("USB was just scanned - waiting a few seconds before touching MTP again.", WARN)
                    source="local"
                else:
                    self._device_touch_cool_until=now+10.0
        self.listing=True; self._listing_src=source
        def work():
            try:
                dev=list_projects(progress=lambda i,t: self.q.put(("listing_progress", i, t))) if source=="device" else []
                names={p["name"] for p in dev}; local=list_local_projects(dest); lmap={p["name"]: p for p in local}
                for p in dev:                                  # a project that is also on this PC keeps what the PC knows about it
                    lp=lmap.get(p["name"])
                    if lp:
                        for k in ("combined", "prepared", "dev_meshed"): p[k]=lp.get(k)
                        p["on_pc"]=True
                self.q.put(("projects", dev+[p for p in local if p["name"] not in names]))
            except Exception as e:
                log_error("list-projects", e); self.q.put(("projects_failed", str(e)))
        self._start_thread(work, name="list-projects")
    def on_mount(self):
        if self._mounting: return
        self._user_mount=True                       # a click, not the background probe: worth guiding on failure
        st,_=usb_state()
        if st!="mtp":
            self.set_banner("Tap “File Transfer” on the MIRACO first.", WARN); self.after(300, self._usb_help); return
        self._mounting=True; self._shots_loaded=False; self.set_banner("Connecting…", AC)
        def work():
            try: self.q.put(("mounted", *do_mount()))
            except Exception as e: log_error("mount", e); self.q.put(("mounted", False, str(e)))
        threading.Thread(target=work, daemon=True).start()

    # ---- list ----
    def _list_thumb(self, name, fallback=None):
        """Best thumbnail for a project row: the render the big preview would show (its largest model's
        node at the CURRENT version), then the combined render, then the newest render that is still backed
        by a current version. Never a switched-away/deleted version's stale render; falls back to the flat
        scanner preview."""
        try:
            cands=[c for c in glob.glob(os.path.join(THUMBS, glob.escape(name)+"__*__shaded.png")) if os.path.getsize(c)>1024]
            if cands:
                def fresh(path, node):
                    mesh=self._mesh_for_node(name, node)
                    try: return not mesh or os.path.getmtime(path)>=os.path.getmtime(mesh)
                    except Exception: return True
                # 1) exactly what the hero preview renders: largest mesh's node at its current version
                hero=self._find_mesh(name); node=self._node_of(name, hero) if hero else None
                if node:
                    verkey=(self._proc_current(name, node) or (None,))[0]
                    want=os.path.join(THUMBS, "%s__%s__%s__shaded.png" % (name, node, verkey or "v"))
                    if os.path.exists(want) and os.path.getsize(want)>1024 and fresh(want, node):
                        return want
                # 2) the whole-project combined render, if one is current
                comb=next((c for c in cands if os.path.basename(c).startswith(name+"__combined__")
                           and fresh(c, "combined")), None)
                if comb: return comb
                # 3) newest render still backed by a current version (orphans of deleted/old versions pruned)
                valid={}
                for nd in self._proc_nodes(name):
                    vk=(self._proc_current(name, nd) or (None,))[0]
                    valid["%s__%s__%s__shaded.png" % (name, nd, vk or "v")]=nd
                live=[c for c in cands if os.path.basename(c) in valid and fresh(c, valid[os.path.basename(c)])]
                if live: return max(live, key=os.path.getmtime)
        except Exception as e:
            log_error("list-thumb", e)
        return fallback
    def render_list(self, projs):
        # include imported/changed state and the search text so the list re-renders when files or the filter change
        q=(self.search.get() or "").strip().lower()
        self.all_projects=projs; projs=self._page_filter(projs)
        sig=json.dumps([q, self.page]+[[p, self.is_imported(p["name"]), self.changed(p["name"])] for p in projs])
        if sig==self.projects_sig: return
        self.projects_sig=sig; self.projects=projs
        # Unmap the list while its rows are destroyed/rebuilt - see render_gallery for why
        # (same CTkScrollableFrame redraw-recursion bug).
        self.llist.grid_remove()
        for w in self.llist.winfo_children(): w.destroy()
        try:
            self._render_list_body(projs, q)
        except Exception as e:
            log_error("render_list", e); self.projects_sig=None    # force a real retry next time, don't get stuck showing a blank list
            for w in self.llist.winfo_children(): w.destroy()
            ctk.CTkLabel(self.llist, text="Couldn't load the project list (see Help > Log).", text_color=WARN, font=ctk.CTkFont(size=12)).grid(row=0, column=0, sticky="w", padx=16, pady=20)
        finally:
            self.llist.grid(); self._fit_scrollbar_later(self.llist, "vertical", 120)
    def _render_list_body(self, projs, q):
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
            _sel=(name==self.selected)   # selected = a lifted (lighter) card with a soft neutral edge (see SEL_FILL/SEL_EDGE); select_project() must match this exactly
            card=ctk.CTkFrame(self.llist, fg_color=(SEL_FILL if _sel else ROW), corner_radius=10, border_width=(1 if _sel else 0), border_color=SEL_EDGE)
            card.grid(row=2*shown, column=0, sticky="ew", pady=(2,0), padx=4); card.grid_columnconfigure(2, weight=1)
            self.rows[name]=card
            if self.page=="import":
                ctk.CTkCheckBox(card, text="", width=24, checkbox_width=20, checkbox_height=20, corner_radius=5, border_color=DIM,
                                variable=var, fg_color=AC, hover_color=AC_H).grid(row=0,column=0, padx=(8,0), pady=10)
            tbox=ctk.CTkFrame(card, fg_color="#0a0c10", corner_radius=8, width=60, height=50); tbox.grid(row=0,column=1, padx=(2,2), pady=8); tbox.grid_propagate(False)
            tbox.grid_columnconfigure(0, weight=1); tbox.grid_rowconfigure(0, weight=1)
            thumb=self._list_thumb(name, p.get("thumb"))   # prefer a shaded render of the model over the scanner's flat preview
            if thumb:
                try: self.imgs["row_"+name]=cimg(thumb,54); card._thumb_lbl=ctk.CTkLabel(tbox, image=self.imgs["row_"+name], text=""); card._thumb_lbl.grid(row=0,column=0)
                except Exception: card._thumb_lbl=ctk.CTkLabel(tbox, text="-", text_color=MUT); card._thumb_lbl.grid(row=0,column=0)
            else:
                card._thumb_lbl=ctk.CTkLabel(tbox, text="-", text_color=MUT); card._thumb_lbl.grid(row=0,column=0)
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
            ml2=None; badge_ws=[]
            if len(badges)>1:               # what has been made from it; wrap to more rows so a third badge doesn't clip off the card edge
                ml2=ctk.CTkFrame(txt, fg_color="transparent"); ml2.pack(anchor="w", fill="x", pady=(3,0))
                brow=None; used=0; budget=232
                for badge in badges[1:]:
                    bw=len(badge[0])*6+26
                    if brow is None or used+bw>budget:
                        brow=ctk.CTkFrame(ml2, fg_color="transparent"); brow.pack(anchor="w", fill="x", pady=(0,2)); used=0; badge_ws.append(brow)
                    lb=ctk.CTkLabel(brow, text=badge[0], text_color=badge[1], fg_color=badge[2], corner_radius=6, width=1, height=18, font=ctk.CTkFont(size=10)); lb.pack(side="left", padx=(0,6), ipadx=6)
                    badge_ws.append(lb); used+=bw
            for w in [card, tbox, txt, ml] + ([ml2] if ml2 else []) + badge_ws + tbox.winfo_children() + txt.winfo_children() + ml.winfo_children():
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
        if not self._guard_unsaved_edits(): return   # protect unsaved editor edits (even re-selecting reloads the view)
        self.selected=name
        for n,card in self.rows.items():
            s=(n==name); card.configure(fg_color=(SEL_FILL if s else ROW), border_width=(1 if s else 0), border_color=SEL_EDGE)   # match render_list's selection style exactly
        p=next((x for x in self.projects if x["name"]==name), None)
        if not p: return
        if p.get("local") and self.cfg.get("last_open")!=name:   # remember what to warm FIRST next launch (the project you keep reopening)
            self.cfg["last_open"]=name
            try: save_cfg(self.cfg)
            except Exception: pass
        self.big_empty.grid_remove()
        self._film_sel=None; self._film_cells={}
        if getattr(self, "_in_edit_mode", False):   # don't strand a new project in the previous one's Edit tab
            self._in_edit_mode=False; self._editmode_chrome(False)
            try:
                if self.tabs.get()=="Edit": self.tabs.set("3D Preview"); self._preview_tab.grid()
            except Exception: pass
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
        self.update_summary()   # bottom status shows the open project on the Projects page
        if p.get("local"): self._warm_project(name)   # warm THIS project's scan previews first, so clicking between its scans is instant
        self.renders_lbl.configure(text=""); self.renders_lbl.place(relx=1.0, rely=0.0, x=-12, y=10, anchor="ne")
        self.view_nav.place(relx=0.0, rely=0.0, x=8, y=8, anchor="nw"); self.view_nav.lift()
        if p.get("meshes") or p.get("nodes"): self.tools.grid()   # Process on PC works on unfused scans too
        else: self.tools.grid_remove()
        if getattr(self, "page", "import")=="projects": self._schedule_panel_refresh(20)
        else: self._proc_dirty=True
        for w in self.film.winfo_children(): w.destroy()
        if name in self.gallery_cache: self.render_gallery(name, self.gallery_cache[name])
        else:
            ctk.CTkLabel(self.film, text="loading scan renders…", text_color=MUT).pack(side="left", padx=8, pady=40)
            local=os.path.join(self.dest.get() or DEFAULT_DEST, name); render_combined=self._auto_mesh_preview()
            threading.Thread(target=lambda n=name, l=local, rc=render_combined: self.q.put(("gallery",n,gather_gallery(n, l, render_combined=rc))), daemon=True).start()
        self._set_files_rows(msg="Computing model files…")
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        threading.Thread(target=lambda n=name, l=local: self.q.put(("files",n,project_model_size(n, l))), daemon=True).start()
        self._maybe_schedule_shaded(name, None, 250)
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
        # Unmap the strip before destroying/rebuilding its cells: CTkScrollableFrame's own
        # <Configure> handler retriggers its scrollbar's set()->_draw()->update_idletasks()
        # on every child added while mapped, and each update_idletasks() call can flush the
        # NEXT cell's pending <Configure> mid-draw, recursing (SIGUSR1
        # dumps caught the main thread stuck in exactly this loop after a project click).
        self.film.grid_remove()
        for w in self.film.winfo_children(): w.destroy()
        self._film_cells={}; self._film_imgs={}
        if not items:
            return
        todo=[]
        for i,(node,path) in enumerate(items):
            try:
                thumb=self._scan_thumb(name, node) or path   # shaded render if we have one, else the scanner's blue preview
                self.imgs["g_"+name+node]=cimg(thumb,100)
                cell=ctk.CTkFrame(self.film, fg_color="#0a0c10", corner_radius=10, border_width=2, border_color=(AC if node==self._film_sel else STROKE))
                cell.pack(side="left", padx=(0,10), pady=(6,4))
                im=ctk.CTkLabel(cell, image=self.imgs["g_"+name+node], text=""); im.pack(padx=8, pady=(8,2))
                cap=ctk.CTkLabel(cell, text=self._scan_label(name, node), text_color=(AC if node=="combined" else MUT), font=ctk.CTkFont(size=11)); cap.pack(pady=(0,6))
                self._tip(cap, "the combined model" if node=="combined" else ("scanner id: "+node))   # the raw id, for cross-referencing the device
                for w in (cell, im, cap): w.bind("<Button-1>", lambda e,nd=node,pp=path: self._pick_scan(name, nd, pp))
                self._film_cells[node]=cell; self._film_imgs[node]=im
                if thumb==path:                              # no shaded render yet: queue one so the blue preview is replaced
                    mesh=self._mesh_for_node(name, node)
                    if mesh: todo.append((node, mesh))
            except Exception: pass
        self.film.grid()
        self.after(120, self._film_fit)
        if todo: self._start_thread(self._film_thumb_worker, name, todo, name="film-thumbs")
    def _scan_thumb(self, name, node):
        """A cached small shaded render of one scan for the strip (grey on grid, like the big preview),
        or None if it hasn't been rendered yet. Keyed on the current version and checked against the mesh's
        mtime, so switching version or editing the scan never shows the previous version's thumbnail."""
        try:
            verkey=(self._proc_current(name, node) or (None,))[0]
            p=os.path.join(THUMBS, "%s__%s__%s__film.png" % (name, node, verkey or "v"))
            if not (os.path.exists(p) and os.path.getsize(p)>1024): return None
            mesh=self._mesh_for_node(name, node)
            if mesh and os.path.getmtime(p) < os.path.getmtime(mesh): return None   # mesh edited since (local OR device): stale
            return p
        except Exception:
            return None
    def _film_thumb_worker(self, name, todo):
        for node, mesh in todo:
            if self.selected!=name: return                    # moved on
            verkey=(self._proc_current(name, node) or (None,))[0]
            out=os.path.join(THUMBS, "%s__%s__%s__film.png" % (name, node, verkey or "v"))
            try:
                src=mesh
                if mesh.startswith(PROJECTS):                  # device mount is slow: reuse the local view-cache copy if present
                    cached=os.path.join(THUMBS, "view", ("%s__%s__%s"%(name,node,verkey or "v"))+"_fuse_mesh.ply")   # match the name _shade_thread writes (key = name__node__verkey), or this never hit
                    if os.path.exists(cached):
                        if os.path.getmtime(cached) < os.path.getmtime(mesh):   # device mesh edited since we copied it: refresh the local copy so the thumb isn't stale
                            try: shutil.copyfile(mesh, cached)
                            except Exception: pass
                        src=cached
                # freshness is judged against the mesh itself (the source of truth), not the local copy whose
                # mtime is just copy-time, so the reader (which checks the mesh) and this writer agree.
                if os.path.exists(out) and os.path.getmtime(out)>=os.path.getmtime(mesh) and os.path.getsize(out)>1024:
                    self.q.put(("film_thumb", name, node, out)); continue
                env=dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
                r=self._run_child([_sys.executable, os.path.join(HERE, "shade.py"), src, out, "--size", "300x220"], timeout=300, env=env)
                if r.returncode==0 and os.path.exists(out) and os.path.getsize(out)>1024:
                    self.q.put(("film_thumb", name, node, out))
            except Exception as e:
                log_error("film-thumb "+node, e)
    def _film_fit(self):
        """Show the strip's scrollbar only when the thumbnails do not fit."""
        self._fit_scrollbar(self.film, "horizontal")
    def _fit_scrollbar(self, frame, orient="vertical"):
        """Show a CTkScrollableFrame's scrollbar only when its content actually overflows."""
        try:
            canvas=frame._parent_canvas; sb=frame._scrollbar
            if not getattr(frame, "_fit_bound", False):
                # CTk re-grids its scrollbar on a mousewheel or hover, which re-showed a bar we had hidden;
                # re-check just after those so it hides again when the content still fits.
                for seq in ("<MouseWheel>","<Button-4>","<Button-5>","<Enter>"):
                    canvas.bind(seq, lambda e,f=frame,o=orient: self._fit_scrollbar_later(f,o,10), add="+")
                frame._fit_bound=True
            canvas.update_idletasks(); bbox=canvas.bbox("all")
            if not bbox:
                need=False
            elif orient=="horizontal":
                need=(bbox[2]-bbox[0]) > canvas.winfo_width()+2
            else:
                need=(bbox[3]-bbox[1]) > canvas.winfo_height()+2
            if need: sb.grid()
            else: sb.grid_remove()
        except Exception: pass
    def _autohide(self, frame, orient="vertical"):
        """Reliable scrollbar auto-hide: drive visibility from the canvas's own scroll state. The canvas
        calls this on every view/scrollregion change with (first,last) fractions; if the whole content is
        visible (0..1) the bar is removed, otherwise shown - regardless of CTk re-gridding it on resize."""
        try:
            canvas=frame._parent_canvas; sb=frame._scrollbar
            def on_set(first, last):
                try: sb.set(first, last)
                except Exception: pass
                try:
                    if float(first)<=0.0001 and float(last)>=0.9999: sb.grid_remove()
                    else: sb.grid()
                except Exception: pass
            canvas.configure(**{("yscrollcommand" if orient=="vertical" else "xscrollcommand"): on_set})
            canvas.after(0, lambda: canvas.event_generate("<Configure>"))   # kick one recompute so it hides straight away
        except Exception: pass
    def _fit_scrollbar_later(self, frame, orient="vertical", ms=80):
        try: self.after(ms, lambda: self._fit_scrollbar(frame, orient))
        except Exception: pass
    def _fresh_shaded_out(self, name, node):
        """Path to this scan's cached shaded/wire PNG if present and newer than its mesh, else None.
        Mirrors the freshness test in _request_shaded so a click can show the still with no blue flash."""
        try:
            mesh=self._mesh_for_node(name, node)
            if not mesh or not os.path.exists(mesh): return None
            verkey=(self._proc_current(name, node) or (None,))[0]
            key="%s__%s__%s"%(name, node, verkey or "v")
            out=os.path.join(THUMBS, key+("__shaded.png" if self.shade_mode=="solid" else "__wire.png"))
            if os.path.exists(out) and os.path.getmtime(out)>=os.path.getmtime(mesh) and os.path.getsize(out)>1024:
                return out
        except Exception: pass
        return None
    def _pick_scan(self, name, node, path):
        if not self._guard_unsaved_edits(): return   # protect unsaved editor edits (re-clicking the same scan also reloads)
        self._film_sel=node; self._mark_scan(node)
        # Show the cached grey shaded still IMMEDIATELY when it exists, instead of first flashing the
        # scanner's blue preview.png (raw point cloud on black) and swapping the grey in 350 ms later.
        # The scanner preview stays as the fallback for scans with no fused mesh / no cached render yet.
        if self._auto_mesh_preview() and self._fresh_shaded_out(name, node):
            job=getattr(self, "_shade_job", None)
            if job:
                try: self.after_cancel(job)
                except Exception: pass
                self._shade_job=None
            self._request_shaded(name, node)     # fresh cache: shows grey now, no render, no blue
        elif self._auto_mesh_preview() and self._mesh_for_node(name, node):
            # not cached yet but the scan HAS a mesh: go straight to the "Drawing the 3D model" spinner and
            # swap grey in when ready - never flash the blue point cloud first (that reads as "loaded twice").
            self._request_shaded(name, node)
        else:
            self._set_big_image(path); self._maybe_schedule_shaded(name, node, 350)   # raw scan / previews off: the scanner's own preview is the right fallback
        if self.page=="projects": self._schedule_panel_refresh()
    def _mark_scan(self, node):
        for nd,cell in self._film_cells.items():
            try: cell.configure(border_color=(AC if nd==node else STROKE))
            except Exception: pass
    def _enlarge(self, path):
        self._set_big_image(path)

    def _set_big_image(self, path):
        """Show a preview that scales to fill the box and re-fits on window resize."""
        if not path or not os.path.exists(path):      # a scan node with no preview render is normal, not an error to log
            self._big_src=None
            try: self.big._label.configure(image=""); self.big.configure(image=None, text="(preview unavailable)")
            except Exception: pass
            return
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
    def _cloud_for_node(self, name, node):
        """The fused point cloud for a scan, or None. Models-only imports write <name>_<node>_cloud.ply;
        full-project imports keep the fuse under data/<node>/. Checks both so Points doesn't falsely say
        'no cloud' for a full import."""
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        cands=[os.path.join(local, "%s_%s_cloud.ply" % (name, node)),
               os.path.join(local, "data", node, "fuse.ply"),
               os.path.join(local, "data", node, "fuse_cloud.ply"),
               os.path.join(local, "data", node, "fuse_mesh.ply")]   # last resort: a mesh's vertices work as points
        for c in cands:
            try:
                if os.path.exists(c) and os.path.getsize(c)>1024: return c
            except Exception: pass
        return None
    def _mesh_for_node(self, name, node):
        cur=self._proc_current(name, node)          # the version picked on the Process page (or the best available)
        if cur: return cur[2]
        c=os.path.join(PROJECTS, name, "data", node, "fuse_mesh.ply")
        return c if os.path.exists(c) else None
    def _node_of(self, name, path):
        base=os.path.basename(path)
        if base=="fuse_mesh.ply": return os.path.basename(os.path.dirname(path))
        if base.startswith(name+"_") and base.endswith(".ply"):
            n=base[len(name)+1:-4]
            for suf in ("_pcfused","_clean","_cloud","_edited"):   # strip version/kind suffix so we return the real node id (matches node_of / _proc_nodes), not "<node>_pcfused"
                if n.endswith(suf): n=n[:-len(suf)]
            return n
        return None
    def _shade_mode_changed(self, v):
        self.shade_mode="wire" if v=="Wireframe" else "solid"
        if self.mv.winfo_manager(): self.mv.set_wire(self.shade_mode=="wire"); return   # live view: just redraw
        # still image: re-render it now in the chosen mode. (Going through _maybe_schedule_shaded meant the
        # opt-in auto-preview gate could swallow the first toggle, so it "took two clicks" to switch.)
        if self.selected and self._film_sel: self._request_shaded(self.selected, self._film_sel)
    def _guard_unsaved_edits(self):
        """The ONE gate before any navigation that would replace the edited view (tab switch, scan tile,
        project select, version change, app close). Returns True to proceed, False to abort. Keep saves and
        stays put (caller aborts); Discard drops the edits and proceeds; Cancel aborts. On every 'proceed' it
        clears edit mode, so a clean exit tidies up too. Blocks while a save is in flight."""
        if not getattr(self, "_in_edit_mode", False):
            return True                            # not in the editor: nothing to guard or clear
        if getattr(self, "_edit_saving", False):   # a save is running: don't let the view move out from under it
            self._alert("Still saving", "Hang on - still saving your edited model. Try again in a moment.")
            return False
        if getattr(self, "_edit_dirty", False):
            choice=self._modal("Unsaved edits",
                               "You've edited this scan but haven't saved.\nKeep it as a new model, or throw the edits away?",
                               [("Save as model","keep",True),("Discard","discard",False),("Cancel","cancel",False)])
            if choice=="keep":
                self._edit_keep(); return False    # save + land on the new model; the pending nav is abandoned
            if choice!="discard":
                return False                       # Cancel, or closed with X (returns None): abort, never treat as discard
        # proceed (clean exit OR explicit Discard): always clear edit mode so it never lingers
        self._edit_dirty=False; self._in_edit_mode=False
        try: self._editmode_chrome(False); self.mv.set_edit_tool(None); self._hide_edit_palette()
        except Exception: pass
        return True
    def _on_preview_tab(self, name):
        """Sub-tab click: 3D preview | Edit | Files. Edit has no frame of its own - it reuses the live 3D
        view (same camera, same object) and just turns on the point tools, so editing feels like the same
        thing you were looking at, not a separate place."""
        if name=="Edit":
            if getattr(self, "_in_edit_mode", False): return   # already editing: re-clicking Edit must NOT re-enter (that reloads the model and drops unsaved edits)
            try: self._edit_tab.grid_remove(); self._preview_tab.grid()   # hide the empty Edit frame, keep the live view
            except Exception: pass
            self._enter_edit_mode(); return
        # leaving the Edit tab: the shared guard prompts if there are unsaved edits (mesh OR points)
        if getattr(self, "_in_edit_mode", False):
            if not self._guard_unsaved_edits():        # cancel, or keep-in-progress: stay in the editor
                try: self.tabs.set("Edit"); self._edit_tab.grid_remove(); self._preview_tab.grid()
                except Exception: pass
                return
            try: self.pts_sw.set("Mesh"); self._request_shaded(self.selected, self._film_sel)   # discarded: back to the model
            except Exception: pass
        if name=="3D Preview":
            try: self._preview_tab.grid()
            except Exception: pass
    def _enter_edit_mode(self):
        """Enter the Edit tab: same camera/object, show the tools. Edits the built Mesh (cut faces) or the
        Points (clean + rebuild), per the Edit: switch."""
        if getattr(self, "_edit_saving", False): return   # a save is in flight: don't reload the editor under it
        name=self.selected; node=getattr(self, "_film_sel", None)
        if not (name and node) or node=="combined":
            self.set_banner("Open a scan first - Edit works on that scan's model or points.", MUT)
            try: self.tabs.set("3D Preview")
            except Exception: pass
            return
        tgt=self.edit_target_sw.get() if hasattr(self, "edit_target_sw") else "Mesh"
        if tgt=="Points" and not self._cloud_for_node(name, node):
            self.set_banner("This scan has no point cloud - editing the model instead.", MUT)
            try: self.edit_target_sw.set("Mesh")
            except Exception: pass
            tgt="Mesh"
        if tgt=="Mesh" and not self._mesh_for_node(name, node):
            if self._cloud_for_node(name, node):
                self.set_banner("No built model yet - editing the points instead.", MUT)
                try: self.edit_target_sw.set("Points")
                except Exception: pass
                tgt="Points"
            else:
                self.set_banner("This scan has no model or points to edit yet.", WARN)
                try: self.tabs.set("3D Preview")
                except Exception: pass
                return
        self._in_edit_mode=True
        self._editmode_chrome(True)                                    # hide the view switches: this is the editing workspace
        try: self.edit_target_sw.set(tgt)
        except Exception: pass
        # reset selection settings ONCE per edit session (not on every palette show)
        try:
            if getattr(self,"sel_mode_sw",None): self.sel_mode_sw.set("Replace")
            if getattr(self,"depth_sw",None): self.depth_sw.set("Through object")
            self.vis_only.set(False); self.mv.set_visible_only(False); self.mv.edit_mode="replace"
            self.mv.on_brush_size=self._on_brush_size                  # scroll-resize the brush -> palette slider tracks it
            self._vis_warned=False; self.mv._depth_failed=False
        except Exception: pass
        self._show_edit_palette()                                      # swap the right panel to the editor tools right away
        if tgt=="Points":
            self.pts_sw.set("Points"); self._view_mode_changed("Points")   # cloud + tools, keeps the camera
        else:
            self._enter_mesh_edit(name, node)
    def _enter_mesh_edit(self, name, node):
        """Load the scan's current model into the live view and make its FACES editable (cut junk directly)."""
        mesh=self._mesh_for_node(name, node)
        if not mesh:
            self.set_banner("No model to edit for this scan.", WARN); return
        verkey=(self._proc_current(name, node) or (None,))[0] if node else None
        key="%s__%s__%s" % (name, node, verkey or "v")
        src=mesh if not mesh.startswith(PROJECTS) else os.path.join(THUMBS, "view", key+"_fuse_mesh.ply")
        if not (src and os.path.exists(src)): src=mesh
        self._edit_ctx=(name, node, mesh); self._edit_dirty=False; self._edit_saving=False; self._edit_orig_n=0; self._edit_cur_n=0
        for _w in (getattr(self,"edit_keep",None), getattr(self,"edit_discard",None)):
            try: _w.configure(state="disabled")
            except Exception: pass
        self._map_mv_under_still()
        try: self.mv._keep_view=True
        except Exception: pass
        self._mv_key=None; self._mv_loading=True; self._preview_busy("Loading the model to edit")
        faces=5_000_000   # edit at full detail (don't decimate the model just because you're trimming it)
        def ready(ok):
            self._mv_loading=False; self._preview_idle()
            if not ok:
                self.big_hint.configure(text="Couldn't load the model to edit (see Help > Log)."); return
            try: self.big.grid_remove(); self.mv.grid(); self.mv.lift()
            except Exception: pass
            for _w in (self.view_nav, self.renders_lbl, self.big_hint):
                try: _w.lift()
                except Exception: pass
            started=False
            try: started=self.mv.begin_mesh_edit()
            except Exception as e: log_error("begin-mesh-edit", e)
            if not started:
                self.big_hint.configure(text="Couldn't open this model for editing (see Help > Log)."); return
            try:
                self._edit_orig_n=int(len(self.mv._medit_faces)); self._edit_cur_n=self._edit_orig_n
                full=_ply_element_count(src, "face")   # warn if the model was too big and got decimated for editing
                if full and self._edit_orig_n and self._edit_orig_n < full-1000:
                    self.set_banner("Editing a reduced model: %s of %s faces (the saved model uses this resolution)." % (_kfmt(self._edit_orig_n), _kfmt(full)), WARN)
                self.mv.on_points_change=self._edit_points_changed
                self.mv.set_edit_tool(None); self._set_edit_tool(None, _init=True)
                self._show_edit_palette()
                self.renders_lbl.configure(text="Editing the model")
                self.big_hint.configure(text="Select the junk and Delete to cut it off · “Save as new model” saves it · Discard reverts")
            except Exception as e: log_error("mesh-edit-bar", e)
        try: self.mv.load(src, ready, max_faces=faces)
        except Exception as e:
            log_error("mesh-edit-load", e); self._mv_loading=False; self._preview_idle()
    def _edit_target_changed(self, v):
        """Edit: Mesh <-> Points. Prompts to keep unsaved edits before reloading the other target."""
        if not getattr(self, "_in_edit_mode", False): return
        prev="Points" if v=="Mesh" else "Mesh"   # the target we were on before this click
        def _revert():
            try: self.edit_target_sw.set(prev)
            except Exception: pass
        if getattr(self, "_edit_saving", False):   # block target switch mid-save
            _revert(); self._alert("Still saving", "Hang on - still saving your edited model. Try again in a moment."); return
        if getattr(self, "_edit_dirty", False):
            choice=self._modal("Unsaved edits",
                               "Keep your current edits before switching what you edit?",
                               [("Save as model","keep",True),("Discard","discard",False),("Cancel","cancel",False)])
            if choice=="keep":
                _revert(); self._edit_keep(); return     # save first; user can switch again after
            if choice!="discard":                        # cancel or X (None): stay put, revert the switch
                _revert(); return
            self._edit_dirty=False
        self._enter_edit_mode()                 # reload the newly-chosen target
    def _editmode_chrome(self, on):
        """Editing is its own workspace, so hide the Mesh/Points + Solid/Wireframe VIEW switches while in the
        Edit tab (Reset view stays). Without this, entering Edit just looked like the Points toggle flipped."""
        try:
            if on:
                self.shade_sw.pack_forget(); self.pts_sw.pack_forget()
            else:                                                      # restore in original right-to-left order
                for w in (self.shade_sw, self.pts_sw):
                    try: w.pack_forget()
                    except Exception: pass
                self.shade_sw.pack(side="right")
                self.pts_sw.pack(side="right", padx=(0,8))
        except Exception: pass
    def _view_mode_changed(self, v):
        """Mesh <-> Fused points. Points loads the scanner's fused cloud (name_node_cloud.ply) into the
        interactive view, oriented to overlay the mesh, so you can compare the built mesh with what the
        scanner actually captured."""
        name=self.selected; node=getattr(self, "_film_sel", None)
        if not (name and node) or node=="combined":
            try: self.pts_sw.set("Mesh")
            except Exception: pass
            return
        if v!="Points":
            if getattr(self, "_edit_dirty", False) and not getattr(self, "_edit_saving", False):
                choice=self._modal("Unsaved point edits",
                                   "You've cleaned some points but haven't saved them.\nKeep them as a new model, or throw the edits away?",
                                   [("Save as model","keep",True),("Discard","discard",False),("Cancel","cancel",False)])
                if choice=="cancel":
                    try: self.pts_sw.set("Points")   # stay in the editor
                    except Exception: pass
                    return
                if choice=="keep":
                    try: self.pts_sw.set("Points")
                    except Exception: pass
                    self._edit_keep(); return       # save + rebuild, then it switches to the new model itself
                self._edit_dirty=False              # discard: drop the edits and fall through to the mesh
            try: self.mv.set_edit_tool(None); self._hide_edit_palette()   # leave edit mode with the points
            except Exception: pass
            # back to the mesh: load it straight into the live view (which is showing points) and swap when
            # ready, so there's no flash of the still PNG at the default angle. Keep the camera.
            mesh=self._mesh_for_node(name, node)
            verkey=(self._proc_current(name, node) or (None,))[0] if node else None
            key=("%s__%s__%s"%(name, node, verkey or "v")) if node else (name if not node else None)
            src=mesh if (mesh and not mesh.startswith(PROJECTS)) else (os.path.join(THUMBS, "view", key+"_fuse_mesh.ply") if key else None)
            if not (src and os.path.exists(src)):
                try: self.mv._keep_view=True
                except Exception: pass
                self._request_shaded(name, node); return      # device mesh with no local copy: normal path (may flash once)
            self.mv._keep_view=True; self._mv_key=key; self._mv_want=(key, src); self._shade_key=(key, self.shade_mode)
            self._mv_loading=True; self._preview_busy("Loading the 3D view")
            faces=LIVE_QUALITY_FACES.get(self.cfg.get("live_quality","medium"), 300000)
            def mready(ok):
                self._mv_loading=False; self._preview_idle()
                if ok:
                    try: self.big.grid_remove(); self.mv.grid(); self.mv.lift()
                    except Exception: pass
                    for _w in (self.view_nav, self.renders_lbl, self.big_hint):
                        try: _w.lift()
                        except Exception: pass
                    self.big_hint.configure(text="Drag to rotate · scroll to zoom · right-drag to pan · double-click to reset")
                    st=self._mesh_stats.get(key)              # returning from Points: restore the mesh overlay, not the stale "Fused points · scanner"
                    if st:
                        try: self._show_stats(st)
                        except Exception: pass
                    else:
                        try: self.renders_lbl.configure(text="3D model")
                        except Exception: pass
                else:
                    self._request_shaded(name, node)          # fell over: fall back to the still + normal load
            try: self.mv.load(src, mready, max_faces=faces)
            except Exception as e:
                log_error("points-to-mesh", e); self._mv_loading=False; self._request_shaded(name, node)
            return
        cloud=self._cloud_for_node(name, node)
        if not cloud:
            self.set_banner("This scan has no fused point cloud on this PC (import the project again to get it).", MUT)
            try: self.pts_sw.set("Mesh")
            except Exception: pass
            return
        self._map_mv_under_still()                             # the GL view must be mapped to upload
        tf=getattr(self.mv, "tf", None)                        # align the points to the mesh if it's loaded
        self._edit_ctx=(name, node, cloud); self._edit_dirty=False; self._edit_saving=False; self._edit_orig_n=0
        for _w in (getattr(self,"edit_keep",None), getattr(self,"edit_discard",None)):   # fresh editor: nothing to save yet
            try: _w.configure(state="disabled")
            except Exception: pass
        self._mv_key=None                                      # the interactive view now shows points, not the tracked mesh: so toggling back to Mesh actually reloads it (else _mv_start short-circuits and stays on points)
        try: self.mv._keep_view=True                           # show the points at the mesh's current camera, not the default
        except Exception: pass
        real_cloud=not str(cloud).endswith("fuse_mesh.ply")   # the last-resort fallback is a MESH's vertices, not a real fused cloud - label it honestly
        self._mv_loading=True; self._preview_busy("Loading the fused points" if real_cloud else "Loading the model's points")
        def ready(ok):
            self._mv_loading=False; self._preview_idle()
            if ok:
                try: self.big.grid_remove(); self.mv.grid(); self.mv.lift()
                except Exception: pass
                for _w in (self.view_nav, self.renders_lbl, self.big_hint):
                    try: _w.lift()
                    except Exception: pass
                try: self.renders_lbl.configure(text="Fused points · scanner" if real_cloud else "Model vertices · no separate cloud")
                except Exception: pass
                if getattr(self, "_in_edit_mode", False):
                    self.big_hint.configure(text=("Select and Delete to clean · then “Save as new model” to save it (holes stay open) · Discard to revert" if real_cloud
                                                  else "No separate point cloud for this scan - these are the model's own vertices · Select and Delete, then Save as new model"))
                    try:
                        self._edit_orig_n=int(self.mv._pts_n or 0); self._edit_cur_n=self._edit_orig_n   # baseline: edits are dirty once we drop below this
                        full=_ply_element_count(cloud, "vertex")   # if the cloud was capped, say so - the save uses this reduced set
                        if full and self._edit_orig_n and self._edit_orig_n < full-1000:
                            self.set_banner("Editing a reduced set: %s of %s points (the saved model uses this resolution)." % (_kfmt(self._edit_orig_n), _kfmt(full)), WARN)
                        self.mv.on_points_change=self._edit_points_changed; self.mv.set_edit_tool(None)
                        self._set_edit_tool(None, _init=True)
                        self._show_edit_palette()
                        self._edit_points_changed(self.mv._pts_n if self.mv._pts_n else 0)
                    except Exception as e: log_error("edit-bar", e)
                else:
                    # plain Points view (from the 3D Preview toggle): just for comparing mesh vs capture, no tools
                    try: self.mv.on_points_change=None; self.mv.set_edit_tool(None); self._hide_edit_palette()
                    except Exception: pass
                    self.big_hint.configure(text=("The raw captured points (open the Edit tab to clean them) · drag to rotate" if real_cloud
                                                  else "The model's own vertices (no separate cloud for this scan) · drag to rotate"))
            else:
                self.big_hint.configure(text="Couldn't load the fused points (see Help > Log).")
        # Load the FULL cloud for editing (not the 400k preview cap), so Delete + Keep act on every point and
        # the saved model is exact. Fused MIRACO clouds are a few hundred k points; 6M is effectively no cap.
        try: self.mv.load_points(cloud, ready, tf=tf, max_points=6_000_000)
        except Exception as e:
            log_error("load-points", e); self._mv_loading=False; self._preview_idle()
    def _build_edit_palette(self, p):
        """The point/mesh editor's tools, laid out as a roomy vertical palette on the right (shown in place
        of the project panel while editing). Replaces the old cramped bottom overlay bar."""
        ctk.CTkLabel(p, text="Editing this scan", font=ctk.CTkFont(size=14, weight="bold"), text_color=TX, anchor="w").pack(fill="x", padx=6, pady=(6,0))
        ctk.CTkLabel(p, text="Trim the model, then Save. The original is always kept.", font=ctk.CTkFont(size=10), text_color=DIM, anchor="w", justify="left", wraplength=250).pack(fill="x", padx=6, pady=(0,8))
        ctk.CTkLabel(p, text="WHAT TO EDIT", font=ctk.CTkFont(size=10, weight="bold"), text_color=MUT, anchor="w").pack(fill="x", padx=6, pady=(2,2))
        self.edit_target_sw=ctk.CTkSegmentedButton(p, values=["Mesh","Points"], command=self._edit_target_changed, height=30, corner_radius=8,
                                                   fg_color=CARD2, selected_color=SELB, selected_hover_color=SELB, unselected_color=CARD2, unselected_hover_color=STROKE, text_color=TX, font=ctk.CTkFont(size=12))
        self.edit_target_sw.pack(fill="x", padx=6); self.edit_target_sw.set("Mesh")
        ctk.CTkLabel(p, text="Mesh: cut junk off the built model. Points: clean the raw capture, then rebuild.", font=ctk.CTkFont(size=10), text_color=DIM, anchor="w", justify="left", wraplength=250).pack(fill="x", padx=6, pady=(2,10))
        ctk.CTkLabel(p, text="SELECT WITH", font=ctk.CTkFont(size=10, weight="bold"), text_color=MUT, anchor="w").pack(fill="x", padx=6, pady=(2,3))
        grid=ctk.CTkFrame(p, fg_color="transparent"); grid.pack(fill="x", padx=6)
        grid.grid_columnconfigure((0,1), weight=1, uniform="tool")
        self._edit_tool_btns={}; self._edit_tool_icon={}
        tools=(("lasso","lasso","Lasso","Trace a freehand loop around what to select"),
               ("rect","box","Box","Drag a rectangle to select"),
               ("brush","brush","Brush","Paint over what to select · scroll to size the brush"),
               ("magic","magic","Magic","Click one spot to grab everything connected to it"))
        for i,(tool,iname,label,tip) in enumerate(tools):
            b=ctk.CTkButton(grid, text="  "+label, image=_icon(iname,"default"), compound="left", anchor="w", height=46, corner_radius=10,
                            fg_color=CARD, hover_color=STROKE, text_color=TX, font=ctk.CTkFont(size=13), command=lambda t=tool: self._set_edit_tool(t))
            b.grid(row=i//2, column=i%2, sticky="ew", padx=2, pady=2); self._tip(b, tip); self._edit_tool_btns[tool]=b; self._edit_tool_icon[tool]=iname
        # settings for the active tool (brush size, magic reach, or a hint) - filled by _refresh_tool_settings
        self.tool_settings=ctk.CTkFrame(p, fg_color="transparent"); self.tool_settings.pack(fill="x", padx=6, pady=(6,2))
        # add / remove from the selection (Shift and Ctrl still work, but you don't have to know that)
        ctk.CTkLabel(p, text="SELECTION MODE", font=ctk.CTkFont(size=10, weight="bold"), text_color=MUT, anchor="w").pack(fill="x", padx=6, pady=(6,2))
        self.sel_mode_sw=ctk.CTkSegmentedButton(p, values=["Replace","Add","Subtract"], command=self._sel_mode_changed, height=28, corner_radius=8,
                                                fg_color=CARD2, selected_color=SELB, selected_hover_color=SELB, unselected_color=CARD2, unselected_hover_color=STROKE, text_color=TX, font=ctk.CTkFont(size=11))
        self.sel_mode_sw.pack(fill="x", padx=6); self.sel_mode_sw.set("Replace")
        ctk.CTkLabel(p, text="REACH", font=ctk.CTkFont(size=10, weight="bold"), text_color=MUT, anchor="w").pack(fill="x", padx=6, pady=(8,2))
        self.vis_only=ctk.BooleanVar(value=False)
        self.depth_sw=ctk.CTkSegmentedButton(p, values=["Through object","Visible only"], command=self._depth_mode_changed, height=28, corner_radius=8,
                                             fg_color=CARD2, selected_color=SELB, selected_hover_color=SELB, unselected_color=CARD2, unselected_hover_color=STROKE, text_color=TX, font=ctk.CTkFont(size=11))
        self.depth_sw.pack(fill="x", padx=6); self.depth_sw.set("Through object")
        ctk.CTkLabel(p, text="Visible only skips whatever is hidden behind the object.", font=ctk.CTkFont(size=10), text_color=DIM, anchor="w", justify="left", wraplength=250).pack(fill="x", padx=6, pady=(2,8))
        self._hr(p, pady=(2,6))
        srow=ctk.CTkFrame(p, fg_color="transparent"); srow.pack(fill="x", padx=6)
        for label,iname,cmd,tip in (("Invert","invert-selection", lambda:self.mv.invert_selection(), "Select everything except what's selected"),
                                    ("Clear","clear-selection", lambda:self.mv.clear_selection(), "Unselect everything")):
            bb=ctk.CTkButton(srow, text="  "+label, image=_icon(iname,"default"), compound="left", height=28, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=TX, font=ctk.CTkFont(size=11), command=cmd)
            bb.pack(side="left", expand=True, fill="x", padx=2); self._tip(bb, tip)
        self.edit_count=ctk.CTkLabel(p, text="", text_color=MUT, font=ctk.CTkFont(size=11), anchor="w"); self.edit_count.pack(fill="x", padx=6, pady=(4,2))
        drow=ctk.CTkFrame(p, fg_color="transparent"); drow.pack(fill="x", padx=6, pady=(2,0))
        self.edit_delete_btn=ctk.CTkButton(drow, text="  Delete selected", image=_icon("delete","danger"), compound="left", height=34, corner_radius=8, fg_color="#3a2530", hover_color="#4a2f3c", text_color="#ff9db0", font=ctk.CTkFont(size=12, weight="bold"), command=self._edit_delete, state="disabled")
        self.edit_delete_btn.pack(side="left", expand=True, fill="x", padx=(0,2)); self._tip(self.edit_delete_btn, "Delete the selected (red) part")
        _undo_ic=_icon("undo","default")   # icon-only, so fall back to a text label if the icon asset is missing (a control that renders blank is worse than a wide one)
        self.edit_undo_btn=ctk.CTkButton(drow, text=("" if _undo_ic else "Undo"), image=_undo_ic, width=(44 if _undo_ic else 70), height=34, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, command=self._edit_undo, state="disabled")
        self.edit_undo_btn.pack(side="left", padx=(2,0)); self._tip(self.edit_undo_btn, "Undo the last delete")
        self._hr(p, pady=(12,6))
        self.edit_keep=ctk.CTkButton(p, text="  Save as new model", image=_icon("save-version","muted"), compound="left", height=40, corner_radius=8, fg_color=CARD2, hover_color=AC_H, text_color=DIM, font=ctk.CTkFont(size=13, weight="bold"), command=self._edit_keep, state="disabled")
        self.edit_keep.pack(fill="x", padx=6, pady=(0,4)); self._tip(self.edit_keep, "Save your edits as a new model version. The original is kept.")
        self.edit_discard=ctk.CTkButton(p, text="Discard edits", height=30, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=MUT, font=ctk.CTkFont(size=11), command=self._edit_discard, state="disabled")
        self.edit_discard.pack(fill="x", padx=6, pady=(0,2)); self._tip(self.edit_discard, "Throw the edits away and reload the original.")
        ctk.CTkButton(p, text="  Done editing", image=_icon("back","muted"), compound="left", height=28, corner_radius=8, fg_color="transparent", border_width=0, hover_color=CARD2, text_color=MUT, font=ctk.CTkFont(size=11),
                      command=lambda: self.tabs.set("3D Preview", True)).pack(fill="x", padx=6, pady=(6,12))
    def _show_edit_palette(self):
        # just swaps the panel into view - the session reset lives in _enter_edit_mode so re-showing
        # the palette (e.g. after a load completes) never wipes what the user just set.
        try: self.projpanel.grid_remove(); self.editpanel.grid(); self.editpanel.lift()
        except Exception: pass
        try: self.update_summary()   # bottom bar -> "Editing <project>"
        except Exception: pass
    def _hide_edit_palette(self):
        try:
            self.editpanel.grid_remove()
            if self.page=="projects": self.projpanel.grid()
        except Exception: pass
        try: self.update_summary()   # bottom bar -> just "<project>" (viewing, not editing)
        except Exception: pass
    def _toggle_visible_only(self):
        try: self.mv.set_visible_only(bool(self.vis_only.get()))
        except Exception as e: log_error("visible-only", e)
    def _depth_mode_changed(self, v):
        try: self.vis_only.set(v=="Visible only"); self._toggle_visible_only()
        except Exception: pass
    def _sel_mode_changed(self, v):
        try: self.mv.edit_mode={"Replace":"replace","Add":"add","Subtract":"subtract"}.get(v,"replace")
        except Exception: pass
    def _brush_size_changed(self, v):
        try:
            self.mv.brush_px=float(v)
            if getattr(self,"_brush_val",None): self._brush_val.configure(text="%d px" % int(v))
        except Exception: pass
    def _on_brush_size(self, px):
        """Scroll on the model resized the brush - keep the palette slider and number in sync."""
        try:
            if getattr(self,"_brush_slider",None): self._brush_slider.set(float(px))
            if getattr(self,"_brush_val",None): self._brush_val.configure(text="%d px" % int(px))
        except Exception: pass
    def _magic_reach_changed(self, v):
        try:
            self.mv.magic_k=float(v)
            if getattr(self,"_magic_val",None): self._magic_val.configure(text="%.1f×" % float(v))
        except Exception: pass
    def _refresh_tool_settings(self, tool):
        """Fill the tool-settings box under the tools with controls for the active tool."""
        ts=getattr(self, "tool_settings", None)
        if ts is None: return
        for w in ts.winfo_children():
            try: w.destroy()
            except Exception: pass
        self._brush_val=None; self._magic_val=None
        if tool=="brush":
            row=ctk.CTkFrame(ts, fg_color="transparent"); row.pack(fill="x")
            ctk.CTkLabel(row, text="Brush size", text_color=MUT, font=ctk.CTkFont(size=11)).pack(side="left")
            self._brush_val=ctk.CTkLabel(row, text="%d px" % int(self.mv.brush_px), text_color=TX, font=ctk.CTkFont(size=11)); self._brush_val.pack(side="right")
            self._brush_slider=ctk.CTkSlider(ts, from_=6, to=120, number_of_steps=114, command=self._brush_size_changed); self._brush_slider.set(float(self.mv.brush_px)); self._brush_slider.pack(fill="x", pady=(2,0))
            ctk.CTkLabel(ts, text="Scroll on the model also sizes it.", text_color=DIM, font=ctk.CTkFont(size=10), anchor="w").pack(fill="x")
        elif tool=="magic":
            row=ctk.CTkFrame(ts, fg_color="transparent"); row.pack(fill="x")
            ctk.CTkLabel(row, text="Magic reach", text_color=MUT, font=ctk.CTkFont(size=11)).pack(side="left")
            self._magic_val=ctk.CTkLabel(row, text="%.1f×" % float(getattr(self.mv,"magic_k",3.0)), text_color=TX, font=ctk.CTkFont(size=11)); self._magic_val.pack(side="right")
            s=ctk.CTkSlider(ts, from_=1.0, to=8.0, number_of_steps=70, command=self._magic_reach_changed); s.set(float(getattr(self.mv,"magic_k",3.0))); s.pack(fill="x", pady=(2,0))
            ctk.CTkLabel(ts, text="Higher jumps bigger gaps between points.", text_color=DIM, font=ctk.CTkFont(size=10), anchor="w", justify="left", wraplength=250).pack(fill="x")
        elif tool in ("lasso","rect"):
            ctk.CTkLabel(ts, text=("Trace a loop around the part to select." if tool=="lasso" else "Drag a rectangle over the part to select."),
                         text_color=DIM, font=ctk.CTkFont(size=10), anchor="w", justify="left", wraplength=250).pack(fill="x")
        else:
            ctk.CTkLabel(ts, text="Pick a tool, then drag on the model.", text_color=DIM, font=ctk.CTkFont(size=10), anchor="w", justify="left", wraplength=250).pack(fill="x")
    def _set_edit_tool(self, tool, _init=False):
        """Pick a point-selection tool (or click the active one / pass None to go back to orbit)."""
        cur=getattr(self.mv, "edit_tool", None)
        if not _init and cur==tool: tool=None
        try: self.mv.set_edit_tool(tool)
        except Exception: pass
        try:   # set_edit_tool resets edit_mode to replace; re-apply the palette's Replace/Add/Subtract choice
            if getattr(self, "sel_mode_sw", None): self.mv.edit_mode={"Replace":"replace","Add":"add","Subtract":"subtract"}.get(self.sel_mode_sw.get(),"replace")
        except Exception: pass
        for t,b in getattr(self, "_edit_tool_btns", {}).items():
            active=(t==tool)
            try: b.configure(fg_color=(AC if active else CARD), text_color=("#04121f" if active else TX),
                             image=_icon(self._edit_tool_icon.get(t,t), "on-accent" if active else "default"))   # icon tint follows the pressed state
            except Exception: pass
        self._refresh_tool_settings(tool)
        if tool=="magic" and not _init:   # Magic needs scipy for a true connected-region grow; say so if it's missing
            try: import scipy.spatial  # noqa: F401
            except Exception: self.set_banner("Magic is limited without SciPy (it grabs a radius, not the connected region). Install python3-scipy for the full tool.", WARN)
        try: self.big_hint.configure(text={
            "lasso":"Trace a loop around the part · then Delete",
            "rect":"Drag a box over the part · then Delete",
            "brush":"Paint over the part · scroll to size the brush · then Delete",
            "magic":"Click a spot to grab everything connected · then Delete",
        }.get(tool, "Pick a tool on the right, then drag on the model"))
        except Exception: pass
    def _edit_delete(self):
        try:
            self.mv.delete_selected()
            self._edit_sync_dirty()
        except Exception as e: log_error("edit-delete", e)
    def _edit_undo(self):
        try:
            self.mv.undo_points()
            self._edit_sync_dirty()
        except Exception as e: log_error("edit-undo", e)
    def _edit_sync_dirty(self):
        """Dirty = fewer points/faces than we loaded. Drives Save/Discard. Works for both edit targets
        (mesh mode has _pts_n=0, so we track the count via _edit_cur_n from on_points_change)."""
        try:
            n=int(getattr(self, "_edit_cur_n", 0) or 0); orig=int(getattr(self, "_edit_orig_n", 0) or 0)
            dirty=bool(orig and n<orig and not getattr(self, "_edit_saving", False))
            self._edit_dirty=dirty
            st="normal" if dirty else "disabled"
            try: self.edit_keep.configure(state=st, fg_color=(AC if dirty else CARD2), text_color=("#04121f" if dirty else DIM),
                                          image=_icon("save-version", "on-accent" if dirty else "muted"))   # muted when nothing to save
            except Exception: pass
            try: self.edit_discard.configure(state=st)
            except Exception: pass
        except Exception: pass
    def _edit_del_faces(self):
        """How many the current Delete would remove: faces (all 3 verts selected) in Mesh mode, else points."""
        try:
            sel=getattr(self.mv, "_pts_sel", None)
            if sel is None or not sel.any(): return 0
            if getattr(self.mv, "edit_target", "points")=="mesh" and getattr(self.mv, "_medit_faces", None) is not None:
                return int(sel[self.mv._medit_faces].all(axis=1).sum())
            return int(sel.sum())
        except Exception: return 0
    def _edit_update_actions(self):
        """Enable Delete only when the selection would actually remove something; Undo only when there's history."""
        try:
            can_del=bool(getattr(self, "_edit_del_count", 0))
            mesh=getattr(self.mv, "edit_target", "points")=="mesh"
            can_undo=bool(self.mv._medit_undo if mesh else self.mv._pts_undo)
            if getattr(self, "edit_delete_btn", None): self.edit_delete_btn.configure(state="normal" if can_del else "disabled")
            if getattr(self, "edit_undo_btn", None): self.edit_undo_btn.configure(state="normal" if can_undo else "disabled")
        except Exception: pass
    def _edit_points_changed(self, n):
        """Live count (fired by glview after a select or edit). n is points or faces per the edit target."""
        self._edit_cur_n=int(n or 0)
        try:
            mesh=getattr(self.mv, "edit_target", "points")=="mesh"
            unit="faces" if mesh else "points"
            deln=self._edit_del_faces(); self._edit_del_count=deln   # what Delete will actually remove
            orig=int(getattr(self, "_edit_orig_n", 0) or 0)
            removed=(orig-n) if (orig and n<orig) else 0
            parts=["%s %s" % (_kfmt(n), unit), "%s selected" % _kfmt(deln)]
            if removed: parts.append("%s removed" % _kfmt(removed))
            self.edit_count.configure(text="  ·  ".join(parts))
        except Exception: pass
        # if Visible only was on but the depth read failed, we silently selected through - say so once.
        try:
            if getattr(self.mv, "visible_only", False) and getattr(self.mv, "_depth_failed", False) and not getattr(self, "_vis_warned", False):
                self._vis_warned=True
                self.set_banner("Visible only couldn't read depth here, so it selected through. Rotate a little and try again.", WARN)
            self.mv._depth_failed=False
        except Exception: pass
        self._edit_update_actions()
        self._edit_sync_dirty()
    def _edit_discard(self):
        """Throw away the edits: reload the current target (mesh or points) fresh from disk."""
        if not getattr(self, "_edit_ctx", None): return
        self._edit_saving=False; self._edit_dirty=False
        self._enter_edit_mode()   # honours the Edit: switch, reloads clean (clears undo, resets dirty)
        self.set_banner("Discarded the edits.", MUT)
    def _edit_keep(self):
        """Save the edit as a new 'cleaned model' version. Mesh target: write the trimmed model as-is (no
        rebuild). Points target: rebuild a mesh from the cleaned cloud (ball-pivot keeps openings)."""
        ctx=getattr(self, "_edit_ctx", None)
        if not ctx or getattr(self, "_edit_saving", False): return
        name,node,_=ctx
        is_mesh=(getattr(self.mv, "edit_target", "points")=="mesh")
        payload=None
        if is_mesh:
            try: payload=self.mv.mesh_world()
            except Exception as e: log_error("edit-keep-mesh", e)
            if payload is None or payload[1] is None or len(payload[1])<1:
                self.set_banner("Nothing left to save.", WARN); return
        else:
            pts=None
            try: pts=self.mv.points_world()
            except Exception as e: log_error("edit-keep-points", e)
            if pts is None or len(pts)<100:
                self.set_banner("Not enough points left to build a model.", WARN); return
            import numpy as np
            payload=np.ascontiguousarray(pts, dtype=np.float64)
        self._edit_saving=True; self._edit_sync_dirty()
        try: self.edit_keep.configure(state="disabled", text="Saving…" if is_mesh else "Building…")
        except Exception: pass
        self._preview_busy("Saving the edited model" if is_mesh else "Rebuilding the model from your cleaned points")
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        out=os.path.join(local, "%s_%s_edited.ply" % (name, node))
        def work():
            try:
                if os.path.exists(out) and os.path.getsize(out)>1024:   # keep the previous edited model
                    # if we can't back it up, ABORT rather than overwrite it - the old edited version must not be lost
                    vdir=os.path.join(local, ".versions"); os.makedirs(vdir, exist_ok=True)
                    shutil.copy2(out, os.path.join(vdir, "%s_edited_%s.ply" % (node, time.strftime("%Y%m%d-%H%M%S"))))
                if is_mesh:
                    import trimesh
                    verts,faces=payload
                    m=trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                    tmp=out+".tmp.ply"; m.export(tmp); os.replace(tmp, out)
                    ok=(os.path.exists(out) and os.path.getsize(out)>1024); err=None if ok else "empty result"
                else:
                    # rebuild in a memory-capped child: write the cleaned points, reconstruct there
                    tmpc=out+".pts.ply"; _write_ply_points(tmpc, payload)
                    env=dict(os.environ); env.setdefault("POINTYOINK_MEM_CAP_GB", "10")
                    r=self._run_child([_sys.executable, os.path.join(HERE, "process.py"), tmpc, out, "--rebuild-points"], timeout=1800, env=env)
                    try: os.remove(tmpc)
                    except Exception: pass
                    ok=(r.returncode==0 and os.path.exists(out) and os.path.getsize(out)>1024)
                    err=None if ok else ((r.stderr or r.stdout or "rebuild failed")[-200:] if r else "rebuild failed")
            except Exception as e:
                ok=False; err=str(e); log_error("edit-keep-save", e)
            self.q.put(("call", lambda: self._edit_keep_done(name, node, out, ok, err)))
        threading.Thread(target=work, daemon=True).start()
    def _edit_keep_done(self, name, node, out, ok, err):
        self._edit_saving=False; self._preview_idle()
        try: self.edit_keep.configure(text="Save as new model")
        except Exception: pass
        if not ok:
            self._edit_sync_dirty()
            self.set_banner("Couldn't save the edited model (%s). Your edits are still on screen." % (err or "see Help > Log"), WARN); return
        self._mesh_stats={}; self.gallery_cache.pop(name, None); self.projects_sig=None
        # decide BEFORE touching the view. If the user moved on (even to another scan in the same
        # project), record the version WITHOUT calling _proc_set_current - that would switch the view back.
        moved = (self.selected!=name or getattr(self, "_film_sel", None)!=node)
        if moved:
            self.records.setdefault(name,{}).setdefault("current",{})[node]="edited"; self._persist()
            self.after(0, lambda: self._proc_render(name))   # refresh that project's cards, but leave the current view alone
            self.set_banner("Saved a cleaned model of %s." % self._scan_label(name, node), OK); return
        self.set_banner("Saved a cleaned model of %s. Showing it now." % self._scan_label(name, node), OK)
        self._edit_dirty=False; self._in_edit_mode=False; self._editmode_chrome(False)
        try: self.mv.set_edit_tool(None); self._hide_edit_palette()   # leave edit mode; we're switching to the rebuilt Mesh
        except Exception: pass
        try:
            if self.tabs.get()=="Edit": self.tabs.set("3D Preview"); self._preview_tab.grid()   # land on the finished model
        except Exception: pass
        self._proc_set_current(name, node, "edited")   # still here: switch the shown model to the rebuilt one
    def _reset_view(self, _=None):
        """Reset the live 3D view to its default angle and zoom (same as double-clicking the model)."""
        try:
            mv=getattr(self, "mv", None)
            if mv is not None and mv.winfo_manager():   # only meaningful while the interactive view is showing
                mv.reset()
            else:
                self.set_status("Reset view works once the 3D model is loaded (click a scan).")
        except Exception as e: log_error("reset-view", e)
    def _enter_3d(self, _=None):
        """Open the interactive 3D view for the selected scan (clicking the still image). No-op if it's
        already showing, or if there's nothing to load yet."""
        try:
            mv=getattr(self, "mv", None)
            if mv is not None and mv.winfo_manager(): return       # already interactive
            if getattr(self, "_mv_want", None): self._mv_start()
        except Exception as e: log_error("enter-3d", e)
    def _set_view(self, azim, elev):
        """Snap the 3D view to a standard angle (Home/Top/Front/…). If the live view isn't up yet, load
        it and apply the angle once it's ready."""
        try:
            mv=getattr(self, "mv", None)
            if mv is not None and mv.winfo_manager():
                mv.set_view(azim, elev); return
        except Exception as e:
            log_error("set-view", e); return
        self._pending_view=(azim, elev)
        if getattr(self, "_mv_want", None): self._mv_start()   # ready() applies _pending_view
    def _schedule_shaded(self, name, node=None, delay=250):
        """Debounce expensive mesh preview work so rapid scan clicks do not start a render/load per click."""
        job=getattr(self, "_shade_job", None)
        if job:
            try: self.after_cancel(job)
            except Exception: pass
        def go(n=name, nd=node):
            self._shade_job=None
            if self.selected==n: self._request_shaded(n, nd)
        self._shade_job=self.after(delay, go)
    def _cancel_mv_start(self):
        job=getattr(self, "_mv_job", None)
        if job:
            try: self.after_cancel(job)
            except Exception: pass
            self._mv_job=None
    def _auto_mesh_preview(self):
        return bool(self.cfg.get("auto_shaded_preview", True))
    def _maybe_schedule_shaded(self, name, node=None, delay=250):
        if self._auto_mesh_preview():
            self._schedule_shaded(name, node, delay); return
        try:
            job=getattr(self, "_shade_job", None)
            if job: self.after_cancel(job)
            self._shade_job=None; self._shade_want=None; self._cancel_mv_start()
            self._mv_key=None; self.mv.grid_remove(); self.big.grid(); self._preview_idle()
            has_mesh=bool(self._mesh_for_node(name, node) if node else self._find_mesh(name))
            if has_mesh:
                self._show_3d_controls(True)
                self.big_hint.configure(text="Scanner preview · View in 3D loads the model only when you ask")
            else:                                   # raw scan: don't promise a 3D view that can't exist
                self._show_3d_controls(False); self.renders_lbl.configure(text="")
                self.big_hint.configure(text="This scan is still raw data - showing the scanner's point cloud. Press “Build model” to make the 3D model.")
        except Exception: pass
    def _show_3d_controls(self, on):
        """Show the 3D-only chrome (Fit/Top/Front view nav, Solid/Wireframe, Reset view, View in 3D) only
        when a real 3D model is on screen. A raw scan shows the flat scanner point cloud, where those
        controls do nothing - hiding them is clearer than a separate 2D/3D tab."""
        try:
            if on: self.view_nav.place(relx=0.0, rely=0.0, x=8, y=8, anchor="nw"); self.view_nav.lift()
            else: self.view_nav.place_forget()
        except Exception: pass
        try:
            ctl=getattr(self, "_tab_ctl", None)
            if ctl is not None:
                if on:
                    if not ctl.winfo_manager(): ctl.pack(side="right", pady=(0,4))
                else: ctl.pack_forget()
        except Exception: pass
    def _request_shaded(self, name, node=None):
        """Show the cached shaded render for this scan, or queue one. Never blocks the UI thread."""
        try: self.pts_sw.set("Mesh")   # showing the mesh (or its still): the Points toggle reflects that
        except Exception: pass
        if not node:                    # resolve the scan so we honour the TICKED version, not just the biggest file
            node=self._film_sel if (self._film_sel and self._film_sel!="combined") else None
            if not node:
                try:
                    ns=self._proc_nodes(name)
                    if len(ns)==1: node=ns[0]   # single scan: the version chip and the preview must agree
                except Exception: pass
        # honour the picked version (_mesh_for_node -> _proc_current) whenever we know the scan; _find_mesh
        # (largest file) was showing the sealed scanner model even when "prepared copy" was ticked - the
        # "first load sealed, toggle to points and back shows the holey one" bug.
        mesh=self._mesh_for_node(name, node) if node else self._find_mesh(name)
        if not mesh:
            # genuinely no fused mesh (only raw frames). Clear the interactive target so clicking the
            # preview doesn't open the PREVIOUS scan's 3D view - that was the "says no model but then
            # loads" bug.
            self._mv_want=None; self._mv_key=None; self._shade_key=None; self._cancel_mv_start()   # reset _shade_key too, or a late mesh_stats for the PREVIOUS scan re-stamps its triangle count here
            try: self.mv.grid_remove(); self.big.grid()
            except Exception: pass
            try: self.renders_lbl.configure(text="")   # clear the "3D model · N triangles" overlay - a raw scan has no mesh, so it must not carry the previous scan's count
            except Exception: pass
            self._show_3d_controls(False)   # flat scanner cloud: no 3D nav / shading / reset to offer
            self.big_hint.configure(text="This scan is still raw data - showing the scanner's point cloud. Press “Build model” to make the 3D model (a few seconds), or One-tap Edit on the scanner."); self._preview_idle(); return
        node=node or self._node_of(name, mesh)
        self._show_3d_controls(True)        # a fused mesh exists for this scan: the 3D view and its controls apply
        if node and node!=self._film_sel: self._film_sel=node; self._mark_scan(node)
        # include the version in the cache key: each version's mesh differs, and a node-only key made the
        # scanner and prepared renders collide on one file, so switching back showed the stale one.
        verkey=(self._proc_current(name, node) or (None,))[0] if node else None
        key=("%s__%s__%s"%(name, node, verkey or "v")) if node else name; mode=self.shade_mode
        out=os.path.join(THUMBS, key+("__shaded.png" if mode=="solid" else "__wire.png"))
        self._shade_key=(key, mode)
        self._cancel_mv_start()
        if self._mv_key!=key:                       # a different scan: back to the flat image until its 3D view is ready
            self._mv_key=None; self.mv.grid_remove(); self.big.grid()
        self._mv_want=(key, mesh if not mesh.startswith(PROJECTS) else os.path.join(THUMBS, "view", key+"_fuse_mesh.ply"))
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
            self._show_3d_controls(False); self.big_hint.configure(text="Scanner's own preview · could not draw the 3D model"); return
        if mesh.startswith(PROJECTS) and self.pulling:
            self._show_3d_controls(False); self.big_hint.configure(text="Scanner preview · the 3D render waits for the import to finish"); return
        self._dim_preview(); self._preview_busy("Drawing the 3D model")   # spinner box carries the text; _preview_busy clears the bottom hint
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
                env=dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
                cmd=[_sys.executable, os.path.join(HERE, "shade.py"), path, out, "--size", "900x600"]
                if mode=="wire": cmd.append("--wire")
                r=self._run_child(cmd, timeout=600, env=env)
                if r.returncode!=0 or not os.path.exists(out) or os.path.getsize(out)<1024:
                    raise RuntimeError("shade.py failed rc=%s: %s" % (r.returncode, ((r.stdout or "")+(r.stderr or ""))[-500:]))
                self.q.put(("shaded", key, mode, out))
            except Exception as e:
                log_error("shaded-preview "+key, e); self.q.put(("shaded", key, mode, None))
    def _warm_enqueue(self, jobs, front=False):
        """Queue (name,node) preview renders for the background warmer. front=True jumps the queue -
        used for the project you just opened, so its scans warm before the rest of the library."""
        if not jobs: return
        with self._warm_lock:
            if front:
                drop=set(jobs)                                    # drop any already-queued copies, then jump them to the front -
                self._warm_q[:]=[q for q in self._warm_q if q not in drop]   # reopening a project re-prioritises, never re-stacks it
                for j in reversed(jobs): self._warm_q.insert(0, j)
            else:
                seen=set(self._warm_q)
                self._warm_q.extend(j for j in jobs if j not in seen)
            start = not self._warm_running
            if start: self._warm_running=True
        if start: threading.Thread(target=self._warm_worker, daemon=True).start()
    def _warm_project(self, name):
        """Warm the OPEN project's scan previews first, so clicking Scan 1 / Scan 2 / Combined is instant."""
        try: nodes=self._proc_nodes(name)
        except Exception: return
        self._warm_enqueue([(name, n) for n in nodes], front=True)
    def _warm_imported(self, names):
        """After a mid-session import, render the new projects' scan thumbnails (still + strip + 300k mesh)
        so their scan strip isn't blue and opening them is instant - the splash preloader only runs at boot.
        Shown as a visible 'Preparing previews' stage in the app status, so import completion doesn't just
        silently flip blue tiles to grey after the project has already opened."""
        jobs=[]
        for name in names:
            try:
                for node in self._proc_nodes(name): jobs.append((name, node))
            except Exception: pass
        if jobs:
            self.set_status("Preparing previews…  0 / %d" % len(jobs))
            threading.Thread(target=self._warm_imported_worker, args=(jobs,), daemon=True).start()
    def _warm_imported_worker(self, jobs):
        import shade as _sh
        faces=LIVE_QUALITY_FACES.get(self.cfg.get("live_quality","medium"), 300000)
        env=dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
        touched=set(); total=len(jobs)
        for i,(name,node) in enumerate(jobs, 1):
            self.q.put(("call", lambda i=i: self.set_status("Preparing previews…  %d / %d" % (i, total))))
            try:
                mesh=self._mesh_for_node(name, node)
                if not mesh or mesh.startswith(PROJECTS): continue
                verkey=(self._proc_current(name, node) or (None,))[0]
                while getattr(self, "_shade_running", False): time.sleep(0.3)   # yield to the user's own clicks
                still=os.path.join(THUMBS, "%s__%s__%s__shaded.png" % (name, node, verkey or "v"))
                if not (os.path.exists(still) and os.path.getmtime(still)>=os.path.getmtime(mesh) and os.path.getsize(still)>1024):
                    self._run_child([_sys.executable, os.path.join(HERE, "shade.py"), mesh, still, "--size", "900x600"], timeout=120, env=env)
                film=os.path.join(THUMBS, "%s__%s__%s__film.png" % (name, node, verkey or "v"))
                if not (os.path.exists(film) and os.path.getmtime(film)>=os.path.getmtime(mesh) and os.path.getsize(film)>1024):
                    self._run_child([_sys.executable, os.path.join(HERE, "shade.py"), mesh, film, "--size", "300x220"], timeout=120, env=env)
                nkey=_sh._mesh_key(mesh, faces, None); npz=os.path.join(_sh.MESH_CACHE, nkey+"_n.npz") if nkey else None
                if not (npz and os.path.exists(npz)):
                    self._run_child([_sys.executable, os.path.join(HERE, "shade.py"), mesh, "--warm", str(faces)], timeout=180, env=env)
                touched.add(name)
            except Exception as e: log_error("warm-imported", e)
        for name in touched:                                  # if one of these is open, refresh its strip to swap blue -> grey
            self.q.put(("call", lambda n=name: self._refresh_after_warm(n)))
        self.q.put(("call", lambda: self.set_status("Previews ready." if touched else "Ready")))
    def _refresh_after_warm(self, name):
        if self.selected==name:
            self.gallery_cache.pop(name, None)   # force a re-render so the strip picks up the new grey thumbnails
            try: self.select_project(name)
            except Exception as e: log_error("refresh-after-warm", e)
    def _start_prewarm(self):
        """Queue every local project's previews to warm in the background, once per session. Silent - the
        on-disk cache persists, so this fills gaps; the open project (front of the queue) warms first."""
        if getattr(self, "_prewarm_started", False): return
        self._prewarm_started=True
        dest=self.dest.get() or DEFAULT_DEST
        # scan the local folder directly, NOT self.all_projects - that list reflects the current page
        # (device projects on the Import page), which would leave the queue empty and warm nothing.
        try: names=sorted(d for d in os.listdir(dest) if not d.startswith(".") and os.path.isdir(os.path.join(dest, d)))
        except Exception: names=[]
        jobs=[]
        for name in names:
            try:
                for node in self._proc_nodes(name): jobs.append((name, node))
            except Exception: pass
        self._warm_enqueue(jobs)
    def _warm_worker(self):
        import time as _t
        while True:
            with self._warm_lock:
                if not self._warm_q: self._warm_running=False; return
                name, node = self._warm_q.pop(0)
            try:
                mesh=self._mesh_for_node(name, node)
                if not mesh or mesh.startswith(PROJECTS): continue           # no fused mesh, or it's on the scanner mount
                verkey=(self._proc_current(name, node) or (None,))[0]
                out=os.path.join(THUMBS, "%s__%s__%s__shaded.png" % (name, node, verkey or "v"))
                if os.path.exists(out) and os.path.getmtime(out)>=os.path.getmtime(mesh) and os.path.getsize(out)>1024:
                    continue                                                 # already cached and fresh
                while getattr(self, "_shade_running", False): _t.sleep(0.3) # let the user's own click take the machine
                env=dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
                self._run_child([_sys.executable, os.path.join(HERE, "shade.py"), mesh, out, "--size", "900x600"], timeout=600, env=env)
            except Exception as e:
                log_error("warm", e)
            _t.sleep(0.08)
    def _preview_busy(self, text):
        """Show the spinner overlay with a step name (call again to change the text)."""
        try:
            self.big_loader_lbl.configure(text=text)
            self.big_hint.configure(text="")   # the spinner box already says it; don't repeat it in the bottom hint
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
        # The mesh is cached now (parse+simplify+normals warmed on the splash), so the interactive 3D view
        # loads in a fraction of a second and looks better than the flat still - so auto-load it by default
        # instead of making the user click the still. Set "auto_live_preview": false to keep it click-to-open.
        if self.cfg.get("auto_live_preview", True):
            self.big_hint.configure(text="Loading the interactive 3D view…")
            self._schedule_mv_start(self._shade_key[0], 300)   # was 1800ms and opt-in; snappy now that meshes are cached
        else:
            self.big_hint.configure(text="Still image · click to open the interactive 3D view (or pick a view above)")
    def _make_mv(self, software=False):
        w=None
        if not software and os.environ.get("POINTYOINK_NO_GL")!="1" and self.cfg.get("gl_view","auto")!="software":
            try:
                import glview; w=glview.GLView(self._mv_wrap)
            except Exception as e: log_line("GL view unavailable, using the software view: %s" % e); w=None
        if w is None:
            import meshview; w=meshview.MeshView(self._mv_wrap)
        w.grid(row=0,column=0, sticky="nsew", padx=12, pady=12); w.grid_remove(); return w
    def _schedule_mv_start(self, key, delay=1800):
        self._cancel_mv_start()
        def go(k=key):
            self._mv_job=None
            if self._shade_key and self._shade_key[0]==k: self._mv_start()
        self._mv_job=self.after(delay, go)
    def _map_mv_under_still(self):
        """Grid the 3D view in its cell but stacked BELOW the still image, so it is mapped (GL context, upload) while invisible."""
        try:
            self.mv.grid(); self.mv.lower(self.big)
        except Exception as e:
            log_error("map-mv-under-still", e)
    def _mv_start(self):
        """Load the interactive view for the current scan (mesh prep runs in a thread) and swap it in."""
        want=self._mv_want
        if not want or self._mv_key==want[0] or not os.path.exists(want[1]): return
        key,path=want; self._mv_key=key; self.mv.wire=(self.shade_mode=="wire")
        try: import shade; self._mv_c0=(dict(shade.CACHE_STATS), time.time())   # snapshot to log mesh-cache reuse for THIS open
        except Exception: self._mv_c0=None
        self._mv_loading=True    # so the bottom bar shows activity, not "Ready", while the 3D view loads
        self._preview_busy("Loading the live 3D view")   # spinner box carries the text; _preview_busy clears the bottom hint
        # The GL view only gets a context, and only uploads the mesh (which is what fires ready()),
        # once it is MAPPED (<Map> -> initgl; a hidden view must never touch GL, see glview.py). It used
        # to be mapped only from ready() - a circular wait: the spinner sat there forever while the
        # prepared mesh waited in _pending. Surfaced on the first real-display test of this
        # path. So map it now, underneath the still image; it loads out of sight and the swap is instant.
        self._map_mv_under_still()
        def ready(ok, k=key):
            if k!=self._mv_key: return
            if not ok and getattr(self.mv, "failed", False) and not isinstance(self.mv, __import__("meshview").MeshView):
                log_line("GL view failed in this window (%s); switching to the software view" % getattr(self.mv, "_err", ""))
                try: self.mv.destroy()
                except Exception: pass
                self.mv=self._make_mv(software=True); self.mv.wire=(self.shade_mode=="wire"); self._map_mv_under_still(); self.mv.load(path, ready, max_faces=300000); return
            self._mv_loading=False
            self._preview_idle()
            c0=getattr(self, "_mv_c0", None)
            if c0:
                try:
                    import shade; now=shade.CACHE_STATS; b,t0=c0
                    d={s: now[s]-b.get(s,0) for s in now}
                    log_line("mesh-cache open %s: reused mem=%d disk=%d, parsed=%d in %.2fs" %
                             (k, d["mem"], d["disk"], d["compute"], time.time()-t0))
                except Exception: pass
                self._mv_c0=None
            if ok:
                self.big.grid_remove(); self.mv.grid(); self.mv.lift()
                for _w in (self.view_nav, self.renders_lbl, self.big_hint):   # keep overlays above the GL viewport (an X child window)
                    try: _w.lift()
                    except Exception: pass
                pv=self.__dict__.pop("_pending_view", None)   # a view button was clicked while it was still loading
                if pv:
                    try: self.mv.set_view(*pv)
                    except Exception: pass
                self.big_hint.configure(text="Drag to rotate · scroll to zoom · right-drag to pan · double-click to reset")
            else:
                # this used to reuse the exact "...is loading" text shown WHILE still loading, so a real
                # failure was indistinguishable from a load that's just slow.
                log_line("live 3D view failed to load for %s: %s" % (k, getattr(self.mv, "_err", "unknown")))
                try: self.mv.grid_remove()
                except Exception: pass
                self.big_hint.configure(text="Still image · couldn't load the live 3D view (see Help > Log)")
        # GLView's own default cap is 3M faces - for a casual rotate/zoom preview (not the precise
        # cut-plane tool, which already caps at 600k) that meant a 500-650k triangle mesh never got
        # decimated at all, paying full uncapped normal-computation cost every time a scan was
        # selected: measured 16-37s, consistently, not a one-off.
        # The cap is now the Settings "Live 3D preview detail" choice (mesh prep measured 0.7 s at 300k).
        self.mv.load(path, ready, max_faces=LIVE_QUALITY_FACES.get(self.cfg.get("live_quality","medium"), 300000))
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
        # Full project brings the raw capture frames, which crawl over the USB cable (MTP, often under
        # ~150 KB/s), so a project can take an hour or more. WiFi is far faster for raw data. Warn, but
        # let them proceed - some people do want the raw frames over the cable.
        if not self.models_only.get():
            if not self._confirm("Full project over USB is slow",
                    "Full project copies the raw capture frames. That is slow over the USB cable (MTP - often under 150 KB/s), so this can take an hour or more.\n\n"
                    "Faster options:\n"
                    "  •  Share over WiFi › Full project - much faster for raw data\n"
                    "  •  Finished models - just the built model, quick over USB\n\n"
                    "Import the full project over USB anyway?"):
                return   # nothing started yet (pulling/buttons unchanged), just back out
        self.pulling=True; self.cancel=False; self._pull_list=sel; self._export_fails=[]
        self._imp_samples=[]; self._imp_last=0.0   # fresh speed graph for this import
        self.import_btn.grid_remove(); self.cancel_btn.grid(row=0,column=3)
        self._import_popup()   # the transfer popup owns the progress; the bottom bar stays clean unless you Run in background
        dest=self.dest.get() or DEFAULT_DEST; mo=self.models_only.get(); cleanup=self.cleanup.get(); clean_opts=self._clean_options() if cleanup else None; self._persist()
        fmts=[]
        if self.exp_stl.get(): fmts.append("stl")
        if self.exp_obj.get(): fmts.append("obj")
        if self.exp_glb.get(): fmts.append("glb")
        self._start_thread(self._pull_worker, sel, dest, mo, fmts, cleanup, clean_opts, name="pull")
    def _pull_worker(self, sel, dest, mo, fmts, cleanup, clean_opts=None):
        total=len(sel); failed=[]; no_models=[]
        try:
            os.makedirs(dest, exist_ok=True)
            for i,name in enumerate(sel):
                if self.cancel: break
                try:
                    if mo:
                        n=self._import_flat(name, dest, fmts, cleanup, i, total, clean_opts=clean_opts)   # clean flat layout: <name>/<name>_<node>.ply (+.stl)
                        if not n: no_models.append(name)          # nothing to copy yet (never built) - not an error, but not a real import either
                    else:
                        self._import_full(name, dest, i, total)         # full project incl. raw frames (nested mirror)
                except Exception as e:
                    failed.append(name); log_error("import", e)
        except Exception as e:                       # e.g. the destination cannot be created
            log_error("import-setup", e); failed=list(sel)
        finally:
            self.proc=None
            self.q.put(("cancelled" if self.cancel else "done", dest, failed, no_models))

    def _import_flat(self, name, dest, fmts, cleanup, i, total, src_root=None, nodes=None, clean_opts=None):
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
        # plan the files first so we know the total byte count -> a smooth speed graph (byte-based, not per-scan)
        plan=[]; total_bytes=0
        for nd in nodes:
            if not os.path.isdir(nd): continue
            node=os.path.basename(nd)
            if not os.path.exists(os.path.join(nd, "fuse_mesh.ply")):
                continue   # finished-models: skip a scan with no built mesh entirely - don't leave a stray preview-only "scan" that shows blue and can't be built (its raw data wasn't imported)
            for fn,outn,kind in (("fuse_mesh.ply","%s_%s.ply"%(name,node),"mesh"),
                                 ("fuse.ply","%s_%s_cloud.ply"%(name,node),"cloud"),
                                 ("preview.png","%s_%s.png"%(name,node),"prev"),
                                 ("property.rvproj","%s_%s.rvproj"%(name,node),"meta")):   # scan metadata (counts + modes) so it shows offline
                sp=os.path.join(nd,fn)
                if os.path.exists(sp):
                    try: total_bytes+=os.path.getsize(sp)
                    except Exception: pass
                    plan.append((sp, os.path.join(out,outn), kind, node))
        total_bytes=max(1,total_bytes)
        meshes=[]; _t0=time.time(); done=[0]; _last=[0.0]; nscan=[0]; last_node=[None]
        def rep(force=False):
            now=time.time()
            if not force and now-_last[0]<0.2: return
            _last[0]=now; rate=done[0]/max(0.2, now-_t0)
            self.q.put(("prog", (i*100 + 90*done[0]//total_bytes)/(total*100),
                        "Project %d of %d · %s · scan %d/%d · %s / %s · %s/s"
                        % (i+1,total,name,min(n,nscan[0]),n,human(done[0]),human(total_bytes),human(rate)), rate))
        def on_bytes(b): done[0]+=b; rep()
        for sp,dp,kind,node in plan:
            if self.cancel: return
            if node!=last_node[0]: nscan[0]+=1; last_node[0]=node
            try:
                self._copy_chunked(sp, dp, on_bytes)     # chunked -> reports bytes/sec continuously (shutil.copyfile is one opaque blocking call)
                if kind=="mesh": meshes.append(dp)
            except Exception as e: log_error("copy "+kind, e)
        rep(force=True)
        if (fmts or cleanup) and not self.cancel:
            self._process_meshes(meshes, name, fmts, cleanup, i, total, clean_opts=clean_opts)
        return len(meshes)

    def _ensure_clean_vars(self):
        """Clean-up knobs, named and defaulted like the scanner's Mesh panel.
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
    def _clean_options(self):
        """Snapshot cleanup settings on the Tk thread before workers start."""
        self._ensure_clean_vars()
        return {
            "iso": self._num(self.clean_iso,15,0,100), "holes": bool(self.clean_holes.get()),
            "smooth": int(self._num(self.clean_smooth,3,0,50)), "keep": self._num(self.clean_keep,100,1,100),
            "do_iso": bool(self.clean_do_iso.get()), "do_smooth": bool(self.clean_do_smooth.get()),
            "do_keep": bool(self.clean_do_keep.get()), "do_base": bool(self.clean_do_base.get()),
        }
    def _clean_args_from(self, opts):
        """process.py flags for the clean-up knobs shown on the Process page."""
        args=["--clean", "--isolation-rate", "%g" % (opts["iso"] if opts.get("do_iso") else 0),
              "--smooth-times", "%d" % (opts["smooth"] if opts.get("do_smooth") else 0)]
        if opts.get("holes"): args.append("--fill-holes")
        if opts.get("do_base"): args.append("--base-remove")
        keep=opts.get("keep", 100)
        if opts.get("do_keep") and keep<100: args+=["--simplify-pct", "%g" % keep]
        return args
    def _clean_args(self):
        return self._clean_args_from(self._clean_options())
    def _clean_subprocess(self, src, out, clean_opts=None):
        """Run process.py --clean in a memory-capped child so a huge mesh cannot take the app down."""
        try:
            env=dict(os.environ); env.setdefault("POINTYOINK_MEM_CAP_GB", "10")
            opts=clean_opts if clean_opts is not None else self._clean_options()
            r=self._run_child([_sys.executable, os.path.join(HERE, "process.py"), src, out]+self._clean_args_from(opts),
                              timeout=1800, env=env)
            self._last_clean_warnings=[]
            try:
                for ln in (r.stdout or "").splitlines():
                    if ln.startswith("STAGE done "): self._last_clean_warnings=list(json.loads(ln[11:]).get("warnings") or [])
            except Exception: pass
            if r.returncode==0 and os.path.exists(out) and os.path.getsize(out)>1024: return True
            log_line("clean %s failed (rc=%s): %s" % (os.path.basename(src), r.returncode, ((r.stdout or "")+(r.stderr or ""))[-400:]))
        except Exception as e: log_error("clean "+os.path.basename(src), e)
        return False
    def _convert_subprocess(self, src, out):
        """Convert a mesh to another format in a memory-capped child (process.py with no ops just loads
        and re-exports), so a huge scan cannot exhaust the app's own memory during import/ZIP conversion."""
        try:
            env=dict(os.environ); env.setdefault("POINTYOINK_MEM_CAP_GB", "10")
            r=self._run_child([_sys.executable, os.path.join(HERE, "process.py"), src, out], timeout=1800, env=env)
            if r.returncode==0 and os.path.exists(out) and os.path.getsize(out)>0: return True
            log_line("convert %s -> %s failed (rc=%s): %s" % (os.path.basename(src), os.path.basename(out), r.returncode, ((r.stdout or "")+(r.stderr or ""))[-400:]))
        except Exception as e: log_error("convert "+os.path.basename(src), e)
        return False

    def _process_meshes(self, plys, name, fmts, cleanup, i, total, clean_opts=None):
        """Optionally clean each mesh into <stem>_clean.ply (the imported original is kept), then export
        the requested formats from the cleaned copy when there is one. Failures land in self._export_fails."""
        for ply in plys:
            if self.cancel: return
            src=ply
            if cleanup:
                self.q.put(("prog", (i+1)/total, "Cleaning up %s…"%name))
                out=ply[:-4]+"_clean.ply"
                if self._clean_subprocess(ply, out, clean_opts): src=out
                else: self._export_fails.append(os.path.basename(out))
            if not fmts: continue
            for ext in fmts:
                if self.cancel: return
                self.q.put(("prog", (i+1)/total, "Converting %s to %s"%(name, ext.upper())))
                # convert in a memory-capped child, not in-process: a huge scan mustn't exhaust the app
                if not self._convert_subprocess(src, src[:-4]+"."+ext):
                    self._export_fails.append(os.path.basename(src)[:-4]+"."+ext)

    def _copy_chunked(self, src, dst, on_bytes, chunk=1<<20):
        """Copy a file in 1 MB chunks, calling on_bytes(n) after each - lets the import report bytes/sec
        continuously so the speed graph is a real curve, not one flat block per file."""
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            while True:
                if self.cancel: break
                buf=fi.read(chunk)
                if not buf: break
                fo.write(buf); on_bytes(len(buf))
    def _import_full(self, name, dest, i, total):
        """Full project including raw frames - kept in the device's nested layout (needed to re-process)."""
        src=os.path.join(PROJECTS,name)+"/"; dst=os.path.join(dest,name)+"/"; os.makedirs(dst, exist_ok=True)
        cmd=["rsync","-a","--info=progress2",src,dst]
        self.q.put(("prog", i/total, "Project %d of %d - %s (full)"%(i+1,total,name)))
        proc=self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.proc=proc
        try:
            for ln in proc.stdout:
                if self.cancel: proc.terminate(); break
                # rsync --info=progress2 line: "   1,234,567  45%   12.34MB/s    0:00:30"
                # use ALL of it (bytes, speed, time-left), not just the % - a full-project copy over the
                # slow MTP link sits at a low % for ages, so bytes/speed/ETA are what shows it's alive.
                m=re.search(r"([\d,]+)\s+(\d+)%\s+(\S+)\s+(\d+:\d+:\d+)", ln)
                if m:
                    by=int(m.group(1).replace(",","")); fp=int(m.group(2)); spd=m.group(3); eta=m.group(4)
                    if eta.startswith("0:"): eta=eta[2:]                # drop the zero-hour -> mm:ss
                    rm=re.match(r"([\d.]+)\s*([kKmMgG]?)", spd); rb=0.0   # "12.34MB/s" -> bytes/s for the speed graph
                    if rm: rb=float(rm.group(1))*{"":1,"k":1e3,"m":1e6,"g":1e9}.get(rm.group(2).lower(),1)
                    self.q.put(("prog",(i*100+fp)/(total*100),
                                "Project %d of %d · %s · %s · %s · %s left" % (i+1,total,name,human(by),spd,eta), rb))
                else:
                    mm=re.search(r"(\d+)%",ln)
                    if mm:
                        fp=int(mm.group(1)); self.q.put(("prog",(i*100+fp)/(total*100),"Project %d of %d · %s · %d%%"%(i+1,total,name,fp)))
            proc.wait()
            if proc.returncode not in (0,None) and not self.cancel: raise RuntimeError("rsync rc=%s"%proc.returncode)
        finally:
            self._forget_child(proc)
            if self.proc is proc: self.proc=None


    def on_cancel(self):
        self.cancel=True
        try:                                            # immediate feedback: the worker may take a moment to stop the current file
            self.cancel_btn.configure(text="Cancelling…", state="disabled")
            self.set_banner("Cancelling - stopping after the current file…", WARN)
        except Exception: pass
        if self.proc:
            try: self.proc.terminate()
            except Exception: pass
    def open_folder(self): subprocess.Popen(["xdg-open", self.dest.get() or DEFAULT_DEST])

    # ---- 3D view ----
    def _find_mesh(self, name):
        """Largest mesh for a project: prefer the local flat copy, then a full-import mirror, then the device."""
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        flat=[p for p in glob.glob(os.path.join(local, name+"_*.ply")) if not p.endswith("_cloud.ply") and not p.endswith(".tmp.ply")]
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
        # honor the scan + version the preview is showing (self._film_sel), so "View in 3D" opens THIS
        # scan, not the largest/combined mesh. Fall back to the largest ONLY when no scan is selected -
        # if a scan is selected but has no mesh yet, say so instead of quietly opening a different scan.
        sel=getattr(self, "_film_sel", None)
        if sel:
            src=self._mesh_for_node(name, sel)
            if not src:
                self.set_banner("This scan has no 3D model yet. Build this scan first.", WARN); return
        else:
            src=self._find_mesh(name)
            if not src:
                self.set_banner("This project has no 3D model yet. Build one first.", WARN); return
        self.set_status("Loading 3D view: reading the model…")
        token=self._next_job("view"); self._view_job=token
        self._open_loader("Loading 3D view", "Reading the 3D model… large scans take a few seconds.", token=token)
        self._start_thread(self._view_worker, name, src, token, name="view")
    def _view_worker(self, name, src, token):
        # if the mesh is on the (slow) device mount, copy it to a local cache first
        path=src
        if src.startswith(PROJECTS):
            try:
                cache=os.path.join(THUMBS, "view"); os.makedirs(cache, exist_ok=True)
                node=os.path.basename(os.path.dirname(src))   # .../data/<node>/fuse_mesh.ply - key on the scan, not just the project, or different scans collide on name_fuse_mesh.ply
                path=os.path.join(cache, "%s__%s_fuse_mesh.ply" % (name, node))
                if not os.path.exists(path) or os.path.getsize(path)!=os.path.getsize(src) or os.path.getmtime(path)<os.path.getmtime(src):
                    self.q.put(("loader_msg", token, "Copying the 3D model from the scanner…"))   # size AND mtime: a rebuilt mesh of the same byte length must still refresh the copy
                    shutil.copyfile(src, path)
            except Exception as e:
                log_error("view-copy", e); self.q.put(("view_done", token, None)); return
        try:
            viewer=os.path.join(HERE, "viewer.py")
            proc=self._popen([_sys.executable, viewer, path, name], watch=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            got=False; tail=[]
            for ln in proc.stdout:
                tail=(tail+[ln.strip()])[-5:]
                if "PYVIEW_READY" in ln: got=True; self.q.put(("view_done", token, None)); break
                if "PYVIEW_ERROR" in ln: got=True; log_line("viewer: "+ln.strip()); self.q.put(("view_done", token, ln.strip())); break
            if not got:
                log_line("viewer exited before drawing: %s" % " | ".join(tail))
                self.q.put(("view_done", token, "The 3D viewer closed before it drew anything (see Help > Log)."))
        except Exception as e:
            log_error("view-launch", e); self.q.put(("view_done", token, str(e)))

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
        try: self._btn_busy(self.base_btn, "Opening…")
        except Exception: pass
        self.set_status("Base removal: opening the cut-plane tool…")
        token=self._next_job("base"); self._base_job=token; dest=self.dest.get() or DEFAULT_DEST
        self._open_loader("Base removal", "Opening the cut-plane tool… large scans take a few seconds.\nDrag the line to just above the table, then Apply cut. The cut is remembered for this scan.", token=token)
        self._start_thread(self._base_worker, name, src, node, dest, token, name="base")
    def _cut_dialog(self, name, node, src):
        """Remove base inside the app: the scan in the GPU view, the part to keep in grey, the part to remove in red,
        one slider along the table's normal, Flip, Apply. Saves <name>_<node>_clean.ply and remembers the plane."""
        import numpy as np
        t=self._top("Remove base · %s" % self._scan_label(name, node), 1300, 1020, key="cut")
        if t is None: return
        dest=self.dest.get() or DEFAULT_DEST; local=os.path.join(dest, name); out=os.path.join(local, "%s_%s_clean.ply" % (name, node))
        root=ctk.CTkFrame(t, fg_color="transparent"); root.pack(fill="both", expand=True, padx=12, pady=12)
        root.grid_columnconfigure(0, weight=1); root.grid_rowconfigure(0, weight=1)
        # -- left: the 3D view, full height --
        card=ctk.CTkFrame(root, fg_color=CARD, corner_radius=14); card.grid(row=0,column=0, sticky="nsew", padx=(0,10))
        card.grid_columnconfigure(0, weight=1); card.grid_rowconfigure(0, weight=1)
        box=ctk.CTkFrame(card, fg_color="#0a0c10", corner_radius=10); box.grid(row=0,column=0, sticky="nsew", padx=10, pady=(10,4))
        box.grid_columnconfigure(0, weight=1); box.grid_rowconfigure(0, weight=1)
        view=self._new_view(box); view.grid(row=0,column=0, sticky="nsew", padx=4, pady=4)
        if not hasattr(view, "set_split"):
            t.destroy(); self._dialogs.pop("cut", None); self._basing=True; token=self._next_job("base"); self._base_job=token; self._open_loader("Base removal", "Opening the cut-plane tool…", token=token)
            self._start_thread(self._base_worker, name, src, node, dest, token, name="base"); return
        load=ctk.CTkLabel(box, text="Loading the 3D view…", text_color=MUT, font=ctk.CTkFont(size=14), fg_color="#0a0c10"); load.grid(row=0,column=0, sticky="nsew", padx=4, pady=4); load.lift()
        status=ctk.CTkLabel(card, text="", text_color=MUT, font=ctk.CTkFont(size=11), anchor="w"); status.grid(row=1,column=0, sticky="ew", padx=16, pady=(0,10))
        # -- right: the tool palette (same style as the Import options / Edit palette) --
        pal=ctk.CTkFrame(root, fg_color=CARD, corner_radius=14, width=340); pal.grid(row=0,column=1, sticky="nsew"); pal.grid_propagate(False)
        pal.grid_columnconfigure(0, weight=1); pal.grid_rowconfigure(1, weight=1)
        head=ctk.CTkFrame(pal, fg_color="transparent"); head.grid(row=0,column=0, sticky="ew", padx=16, pady=(14,4))
        ctk.CTkLabel(head, text="Remove base", font=ctk.CTkFont(size=18, weight="bold"), text_color=TX, anchor="w").pack(fill="x")
        ctk.CTkLabel(head, text="Grey stays, red goes. Say where the table is, then slide the cut just above it.",
                     text_color=MUT, font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=300).pack(fill="x", pady=(2,0))
        body=ctk.CTkScrollableFrame(pal, fg_color="transparent"); body.grid(row=1,column=0, sticky="nsew", padx=4); self._autohide(body)
        # TABLE: where is it?
        self._title(body, "Table", pady=(6,0))
        dirvar=ctk.StringVar(value="")
        class _DirSel:                         # tiny shim so the behaviour code's dirsel.set()/get() keeps working with radio rows
            def set(self, v): dirvar.set(v)
            def get(self): return dirvar.get()
            def configure(self, **k): pass
        dirsel=_DirSel()
        _pick_dir=lambda: choose_dir(dirvar.get())
        self._opt(body, "radio", "Floor grid", "The view's floor grid is the table.", dirvar, "Floor grid", _pick_dir)
        self._opt(body, "radio", "Auto-detect", "The flattest surface in the scan.", dirvar, "Auto-detect", _pick_dir)
        self._opt(body, "radio", "Click spots on the table", "Click 3 or more spots on the table.", dirvar, "Click spots on the table", _pick_dir)
        spots=ctk.CTkFrame(body, fg_color="transparent"); spots.pack(fill="x", padx=12, pady=(0,2))
        clearb=ctk.CTkButton(spots, text="↺ Clear points", width=118, height=28, corner_radius=14, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=MUT, font=ctk.CTkFont(size=11)); clearb.pack(side="left", padx=(24,0))
        clearb.configure(state="disabled")
        self._tip(clearb, "Remove all the clicked spots. Click a dot again to remove just that one; Backspace undoes the last.")
        dirhint=ctk.CTkLabel(body, text="", text_color=DIM, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=280); dirhint.pack(fill="x", padx=(36,12), pady=(2,4))
        self._hr(body, pady=(6,2))
        # CUT: where along that direction, which side, fine tilt
        self._title(body, "Cut", pady=(0,0))
        ctk.CTkLabel(body, text="Cut height", text_color=MUT, font=ctk.CTkFont(size=12), anchor="w").pack(fill="x", padx=16, pady=(2,0))
        slider=ctk.CTkSlider(body, from_=0, to=1000, number_of_steps=1000, progress_color=AC, button_color=AC, button_hover_color=AC_H, fg_color="#0d0f14"); slider.pack(fill="x", padx=16, pady=(2,0))
        val=ctk.CTkLabel(body, text="", text_color=TX, font=ctk.CTkFont(size=12), anchor="w"); val.pack(fill="x", padx=16)
        cutvar=ctk.DoubleVar(value=0.0)                    # exact height above the lowest point, in mm; tracks the slider and drives it
        def _cut_from_var(_=None):
            if st["H"] is None: return
            try: mm=float(cutvar.get())
            except Exception: return
            st["cut"]=min(st["Hmax"], max(st["Hmin"], st["Hmin"]+mm))
            slider.set(1000.0*(st["cut"]-st["Hmin"])/max(1e-6, st["Hmax"]-st["Hmin"])); schedule()
        def _cut_step(d):
            try: v=float(cutvar.get())
            except Exception: v=0.0
            cutvar.set(round(max(0.0, v+d), 2)); _cut_from_var()
        cr=ctk.CTkFrame(body, fg_color="transparent"); cr.pack(fill="x", padx=16, pady=(6,0))
        ctk.CTkLabel(cr, text="Exact", text_color=TX, font=ctk.CTkFont(size=12), width=54, anchor="w").pack(side="left")
        ctk.CTkButton(cr, text="−", width=36, height=32, corner_radius=8, fg_color=CARD2, hover_color=STROKE, text_color=TX, font=ctk.CTkFont(size=15), command=lambda: _cut_step(-0.5)).pack(side="left")
        ce=ctk.CTkEntry(cr, textvariable=cutvar, width=78, height=32, justify="center", fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=8, font=ctk.CTkFont(size=13)); ce.pack(side="left", padx=6)
        ce.bind("<Return>", _cut_from_var); ce.bind("<FocusOut>", _cut_from_var)
        ctk.CTkButton(cr, text="+", width=36, height=32, corner_radius=8, fg_color=CARD2, hover_color=STROKE, text_color=TX, font=ctk.CTkFont(size=15), command=lambda: _cut_step(+0.5)).pack(side="left")
        ctk.CTkLabel(cr, text="mm", text_color=MUT, font=ctk.CTkFont(size=13)).pack(side="left", padx=(6,0))
        ctk.CTkLabel(body, text="Fine tilt", text_color=MUT, font=ctk.CTkFont(size=12), anchor="w").pack(fill="x", padx=16, pady=(8,0))
        tiltx=ctk.DoubleVar(value=0.0); tilty=ctk.DoubleVar(value=0.0)
        def _tilt_row(label, var):
            """Same shape as Cut height: a slider for the coarse move plus a typeable -/+ field for the exact number."""
            ctk.CTkLabel(body, text=label, text_color=MUT, font=ctk.CTkFont(size=12), anchor="w").pack(fill="x", padx=16, pady=(4,0))
            sl=ctk.CTkSlider(body, from_=-30, to=30, number_of_steps=120, progress_color=AC, button_color=AC, button_hover_color=AC_H, fg_color="#0d0f14")
            sl.pack(fill="x", padx=16, pady=(2,0)); sl.set(0)
            def sync_slider():
                try: sl.set(max(-30.0, min(30.0, float(var.get()))))
                except Exception: pass
            def from_slider(v): var.set(round(float(v)*2)/2.0); _tilt()          # snap to half degrees
            sl.configure(command=from_slider)
            r=ctk.CTkFrame(body, fg_color="transparent"); r.pack(fill="x", padx=16)
            ctk.CTkLabel(r, text="Exact", text_color=TX, font=ctk.CTkFont(size=12), width=54, anchor="w").pack(side="left")
            def step(d):
                try: v=float(var.get())
                except Exception: v=0.0
                var.set(max(-30.0, min(30.0, round(v+d, 2)))); sync_slider(); _tilt()
            ctk.CTkButton(r, text="−", width=36, height=32, corner_radius=8, fg_color=CARD2, hover_color=STROKE, text_color=TX, font=ctk.CTkFont(size=15), command=lambda: step(-0.5)).pack(side="left")
            e=ctk.CTkEntry(r, textvariable=var, width=78, height=32, justify="center", fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=8, font=ctk.CTkFont(size=13)); e.pack(side="left", padx=6)
            e.bind("<Return>", lambda ev: (sync_slider(), _tilt())); e.bind("<FocusOut>", lambda ev: (sync_slider(), _tilt()))
            ctk.CTkButton(r, text="+", width=36, height=32, corner_radius=8, fg_color=CARD2, hover_color=STROKE, text_color=TX, font=ctk.CTkFont(size=15), command=lambda: step(+0.5)).pack(side="left")
            ctk.CTkLabel(r, text="°", text_color=MUT, font=ctk.CTkFont(size=13)).pack(side="left", padx=(6,0))
            return sl
        tiltx_sl=_tilt_row("Pitch", tiltx); tilty_sl=_tilt_row("Roll", tilty)
        def _tilt_zero():
            for v,sl in ((tiltx,tiltx_sl),(tilty,tilty_sl)): v.set(0.0); sl.set(0)
        tiltlbl=ctk.CTkLabel(body, text="Nudge if the table sits slightly off. Type a value and press Enter.", text_color=DIM, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=290); tiltlbl.pack(fill="x", padx=16, pady=(4,0))
        brow=ctk.CTkFrame(body, fg_color="transparent"); brow.pack(fill="x", padx=14, pady=(8,2))
        flipb=ctk.CTkButton(brow, text="Flip side", width=110, height=32, corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX); flipb.pack(side="left", padx=(0,8))
        self._tip(flipb, "Swap which side is kept: the red half becomes grey and the grey half red.")
        resetb=ctk.CTkButton(brow, text="↺ Reset", width=96, height=32, corner_radius=16, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=MUT, font=ctk.CTkFont(size=12)); resetb.pack(side="left")
        self._tip(resetb, "Deselect everything: no table direction, no spots, no cut shown. Start again from scratch.")
        self._hr(body, pady=(6,2))
        def no_table():
            self.records.setdefault(name,{}).setdefault("base_plane",{})[node]={"skip": True}; self._persist()
            self.set_banner("%s: no table to cut, step done." % self._scan_label(name, node), OK); self._proc_refresh(); close()
        skipb=ctk.CTkButton(body, text="No table in this scan", height=32, corner_radius=16, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=MUT, font=ctk.CTkFont(size=12), command=no_table); skipb.pack(fill="x", padx=14, pady=(2,4))
        self._tip(skipb, "The scanner already dropped the floor (its scan settings can do that), or there was none. Marks the Cut base step done for this scan.")
        _bp=(self.records.get(name,{}).get("base_plane",{}) or {}).get(node)
        if _bp and not (isinstance(_bp, dict) and _bp.get("skip")) and os.path.exists(out):
            undob=ctk.CTkButton(body, text="↩ Restore scan before cut", height=32, corner_radius=16, fg_color="transparent", border_width=1, border_color=WARN, hover_color=CARD2, text_color=WARN, font=ctk.CTkFont(size=12),
                                command=lambda: (close(), self._undo_base_cut(name, node))); undob.pack(fill="x", padx=14, pady=(0,4))
            self._tip(undob, "Undo the base cut on this scan: the cut copy goes to the trash (a prepared copy it replaced comes back), the remembered plane is forgotten, and the scan shows the scanner's model again.")
        foot=ctk.CTkFrame(pal, fg_color="transparent"); foot.grid(row=2,column=0, sticky="ew", padx=14, pady=(6,14)); foot.grid_columnconfigure(0, weight=1)
        cancelb=ctk.CTkButton(foot, text="Cancel", width=96, height=34, corner_radius=17, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=TX); cancelb.grid(row=0,column=0, sticky="w")
        applyb=ctk.CTkButton(foot, text="✂  Apply cut", width=140, height=34, corner_radius=17, fg_color=AC, hover_color=AC_H, text_color="#04121f", font=ctk.CTkFont(size=12, weight="bold")); applyb.grid(row=0,column=1, sticky="e")
        st={"n":None, "H":None, "Hmin":0.0, "Hmax":1.0, "cut":0.0, "keep_above":True, "V":None, "job":None, "busy":False, "picks":[], "n_auto":None, "n_grid":None}
        KEEP=np.array([0.74,0.76,0.80], np.float32); GONE=np.array([1.0,0.36,0.42], np.float32)
        def close():
            self._dialogs.pop("cut", None); t.destroy()
        t.protocol("WM_DELETE_WINDOW", close); cancelb.configure(command=close)
        def paint():
            st["job"]=None
            if st["H"] is None: return
            keep=(st["H"]>st["cut"]) if st["keep_above"] else (st["H"]<st["cut"])
            f=np.asarray(view._src[1]); view.set_split(keep[f].all(axis=1), tuple(KEEP), tuple(GONE))   # two plain materials: nothing for the card to lose
            if getattr(view, "_err", None): log_line("cut view: %s" % view._err); view._err=None
            import shade
            n=st["n"]; V=st["V"]; c_w=V.mean(0)+n*(st["cut"]-V.mean(0).dot(n))
            cv=shade.world_to_view(c_w, view.tf); nv=shade.world_to_view(c_w+n*10.0, view.tf)-cv
            view.plane=(cv, nv if st["keep_above"] else -nv, 1.1); view.draw()
            val.configure(text="%.1f mm · %d%% removed" % (st["cut"]-st["Hmin"], 100-int(keep.mean()*100)))
            try: cutvar.set(round(st["cut"]-st["Hmin"], 2))
            except Exception: pass
        def schedule():
            if st["job"] is None: st["job"]=t.after(60, paint)
        def on_slide(v):
            st["cut"]=st["Hmin"]+(st["Hmax"]-st["Hmin"])*float(v)/1000.0; schedule()
        slider.configure(command=on_slide)
        def flip(): st["keep_above"]=not st["keep_above"]; schedule()
        flipb.configure(command=flip)
        def use_normal(n, start=None, ref=None, base=True):
            """Set the cut direction. Which way is up: agree with the auto-detected table plane when it points roughly the
            same way, else the end with the bigger flat sheet is the table. The cut starts just above the densest
            height in the lower third (the table), or where asked."""
            V=st["V"]; n=np.asarray(n, float); n/=np.linalg.norm(n)+1e-9
            # which way is up: the scanner always looks at the table from above, and the first frame's camera sits at the
            # origin of the scan's coordinates, so the table's normal points from the scan toward the origin
            if float(np.dot(n, -V.mean(0)))<0: n=-n
            H=V.dot(n)
            lo=float(H.min()); rng=float(H.max()-lo)
            low=H[H<lo+0.35*rng]; hist,edges=np.histogram(low, bins=60); h_tab=float(0.5*(edges[hist.argmax()]+edges[hist.argmax()+1]))
            st["n"]=n; st["H"]=H; st["Hmin"]=lo; st["Hmax"]=float(H.max())
            st["cut"]=float(start) if start is not None else min(st["Hmax"], h_tab+0.02*(st["Hmax"]-st["Hmin"]))
            if base: st["keep_above"]=True; st["n_base"]=n.copy(); _tilt_zero()
            self._btn_idle(applyb)                             # a plane exists: Apply is live
            slider.set(1000.0*(st["cut"]-st["Hmin"])/max(1e-6, st["Hmax"]-st["Hmin"])); paint()
        def _blank_cut():
            """No cut shown: model all grey, no plane, readouts cleared. Spots are left alone."""
            st["n"]=None; st["H"]=None; st["keep_above"]=True; st["cut"]=0.0; st["n_base"]=None
            try:
                f=np.asarray(view._src[1]); view.set_split(np.ones(len(f), bool), tuple(KEEP), tuple(GONE))
            except Exception: pass
            view.plane=None; view.draw()
            slider.set(0); val.configure(text=""); cutvar.set(0.0); _tilt_zero()
            self._btn_busy(applyb, "✂  Apply cut")          # nothing to apply yet
        def _fit_spots():
            """Re-fit the cut plane from the clicked spots (3+); with fewer, show no cut at all."""
            k=len(st["picks"])
            if k==0: _blank_cut(); dirhint.configure(text="click 3+ spots on the table (click a dot to remove it)"); return
            if k<3: _blank_cut(); dirhint.configure(text="%d of 3 spots" % k); return
            # best-fit plane through every spot (least squares): more spots average out a wobbly click
            P=np.array(st["picks"]); c=P.mean(0); _,sv,vt=np.linalg.svd(P-c); n=vt[2]
            if k==3 and sv[1]<1e-6: dirhint.configure(text="those spots are in a line, click another"); view.draw(); return
            n=n/np.linalg.norm(n)
            if float(np.dot(n, -st["V"].mean(0)))<0: n=-n                     # toward the camera = up
            level=float(c.dot(n)); spread=float(np.abs((P-c).dot(n)).max())
            keep_markers=list(view.markers); use_normal(n, start=level+1.5+spread); view.markers=keep_markers; view.draw()
            dirhint.configure(text="plane through %d spots (within %.1f mm) - click more to refine" % (k, spread))
        def on_pick_spot(world, viewpt):
            """Left-click adds a spot; clicking an existing dot removes it. Right-drag is left alone (that's pan)."""
            w=np.asarray(world, float)
            if st["picks"]:
                P=np.array(st["picks"]); d=np.linalg.norm(P-w, axis=1); j=int(d.argmin())
                ext=float(np.linalg.norm(st["V"].max(0)-st["V"].min(0))) or 1.0
                if d[j] < 0.025*ext:                                           # clicked on a dot: take it away
                    st["picks"].pop(j); view.markers.pop(j); _fit_spots(); return
            st["picks"].append(w); view.markers.append((viewpt, self.PAIR_COLOURS[(len(st["picks"])-1) % len(self.PAIR_COLOURS)]))
            _fit_spots()
        def undo_spot(_=None):
            if not st["picks"] or view.on_pick is None: return                 # only while clicking spots
            st["picks"].pop()
            if view.markers: view.markers.pop()
            _fit_spots()
        def clear_spots():
            st["picks"]=[]; view.markers=[]; view.on_pick=on_pick_spot; _fit_spots()
        def choose_dir(which):
            if st["V"] is None: return
            view.on_pick=None; clearb.configure(state="disabled")
            if which=="Floor grid":
                view.markers=[]; use_normal(st["n_grid"], ref=st.get("n_auto_up")); dirhint.configure(text="the view's floor grid is the table")
            elif which=="Auto-detect":
                view.markers=[]; use_normal(st["n_auto"]); dirhint.configure(text="the flattest surface in the scan")
            elif which=="Click spots on the table":
                # spots persist across modes: show the ones already placed (and their plane if 3+), otherwise no cut
                import shade
                view.markers=[(shade.world_to_view(w, view.tf), self.PAIR_COLOURS[i % len(self.PAIR_COLOURS)]) for i,w in enumerate(st["picks"])]
                view.on_pick=on_pick_spot; clearb.configure(state="normal"); _fit_spots()
        def reset_cut():
            """Blank slate: nothing selected, no spots, no cut shown."""
            st["picks"]=[]; view.markers=[]; view.on_pick=None; clearb.configure(state="disabled")
            dirsel.set(""); _blank_cut()
            dirhint.configure(text="pick where the table is above, or click spots on it")
        clearb.configure(command=clear_spots)
        resetb.configure(command=reset_cut)
        for _k in ("<BackSpace>", "<Delete>"):                     # undo the last spot; right-click stays free for panning
            try: t.bind(_k, undo_spot)
            except Exception: pass
        def _tilt(_=None):
            """Nudge the current base normal by two small angles about axes perpendicular to it."""
            nb=st.get("n_base")
            if nb is None or st["V"] is None: return
            def _deg(v):
                try: return max(-30.0, min(30.0, float(v.get())))
                except Exception: return 0.0
            ax=np.radians(_deg(tiltx)); ay=np.radians(_deg(tilty))
            a=np.array([1.0,0.0,0.0]) if abs(nb[0])<0.9 else np.array([0.0,1.0,0.0])
            u=np.cross(nb,a); u/=np.linalg.norm(u)+1e-9; w=np.cross(nb,u)
            def rot(v,k,th): return v*np.cos(th)+np.cross(k,v)*np.sin(th)+k*np.dot(k,v)*(1-np.cos(th))
            use_normal(rot(rot(nb,u,ax),w,ay), start=st["cut"], base=False)
        def ready(ok):
            if not t.winfo_exists(): return
            if not ok or getattr(view, "_src", None) is None or view.tf is None:
                load.configure(text="Could not load this model"); return
            def work():
                try:
                    import shade, cutplane
                    v_view, f = view._src; V=shade.view_to_world(v_view, view.tf); rng=np.random.default_rng(0)
                    n_auto=cutplane.ransac_normal(V, rng)
                    n_grid=np.asarray(view.tf["R"])[2]            # the view's up axis in scan coordinates: the floor grid
                    res=(V, n_auto, n_grid)
                except Exception as e: log_error("cut setup", e); res=None
                def done():
                    if not t.winfo_exists(): return
                    if res is None: load.configure(text="Could not find the table in this scan"); return
                    V, n_auto, n_grid = res; st["V"]=V; st["n_auto"]=n_auto; st["n_grid"]=n_grid
                    Ha=V.dot(n_auto); lo,hi=np.percentile(Ha,[3,97]); band=0.03*(hi-lo)
                    st["n_auto_up"]=(-n_auto if (Ha>hi-band).sum()>(Ha<lo+band).sum() else n_auto)   # auto plane oriented with its sheet at the bottom
                    load.grid_remove(); dirsel.set("Floor grid"); choose_dir("Floor grid")
                    status.configure(text="Starting just above the table along the floor grid. Drag to rotate, scroll to zoom.")
                self.q.put(("call", done))
            threading.Thread(target=work, daemon=True).start()
        view.load(src, ready, max_faces=600000)
        def apply():
            if st["H"] is None or st["busy"]: return
            st["busy"]=True; self._btn_busy(applyb, "Cutting…"); status.configure(text="Cutting the full model… (a big scan takes a few seconds)")
            try:                                   # keep whatever prepared copy the cut is about to replace (Prepare does the same)
                if os.path.exists(out):
                    vdir=os.path.join(local, ".versions"); os.makedirs(vdir, exist_ok=True)
                    bk=os.path.join(vdir, "%s_clean_%s.ply" % (node, time.strftime("%Y%m%d-%H%M%S"))); shutil.copy2(out, bk)
                    self.records.setdefault(name,{}).setdefault("base_cut_backup",{})[node]=bk
            except Exception as e: log_error("cut-backup", e)
            n=st["n"]; spec="%.6f,%.6f,%.6f,%.4f,%s" % (n[0], n[1], n[2], st["cut"], "1" if st["keep_above"] else "0")
            def work():
                ok=False; plane=None
                try:
                    env=dict(os.environ, OPENBLAS_NUM_THREADS="1"); env.setdefault("POINTYOINK_MEM_CAP_GB", "10")
                    r=self._run_child([_sys.executable, os.path.join(HERE, "cutplane.py"), src, out, "--plane", spec], timeout=1800, env=env)
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
                        self.q.put(("base_done", ("ok", out, node, plane, name)))
                        if t.winfo_exists(): close()
                    elif t.winfo_exists(): self._btn_idle(applyb); status.configure(text="The cut failed (see Help > Log).", text_color=WARN)
                self.q.put(("call", done))
            threading.Thread(target=work, daemon=True).start()
        applyb.configure(command=apply)
    def _base_worker(self, name, src, node=None, dest=None, token=None):
        path=src; dest=dest or DEFAULT_DEST; outdir=os.path.join(dest, name)
        def done(payload):
            if len(payload)<5: payload=tuple(payload)+(name,)
            self.q.put(("base_done", token, payload) if token else ("base_done", payload))
        if src.startswith(PROJECTS):   # on the slow device mount - copy locally first
            try:
                os.makedirs(outdir, exist_ok=True)
                path=os.path.join(outdir, name+"_fuse_mesh.ply")
                if not os.path.exists(path) or os.path.getsize(path)!=os.path.getsize(src) or os.path.getmtime(path)<os.path.getmtime(src):
                    self.q.put(("loader_msg", token, "Copying the 3D model from the scanner…"))   # size AND mtime: a rebuilt mesh of the same byte length must still refresh the copy
                    shutil.copyfile(src, path)
            except Exception as e:
                log_error("base-copy", e); done(("err", "copy failed", node, None, name)); return
        out=os.path.join(outdir, "%s_%s_clean.ply" % (name, node)) if node else os.path.splitext(path)[0]+"_clean.ply"
        try:
            os.makedirs(outdir, exist_ok=True)
            tool=os.path.join(HERE, "cutplane.py")
            env=dict(os.environ, OPENBLAS_NUM_THREADS="1",
                     POINTYOINK_MEM_CAP_GB=os.environ.get("POINTYOINK_MEM_CAP_GB", "10"))
            proc=self._popen([_sys.executable, tool, path, out],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
            try:
                for ln in proc.stdout:
                    ln=ln.strip()
                    if ln.startswith("CUT_READY"): self.q.put(("loader_close", token))
                    elif ln.startswith("CUT_DONE"):
                        try: payload=json.loads(ln[9:])
                        except Exception: payload={}
                        done(("ok", out, node, payload.get("plane"), name)); break
                    elif ln.startswith("CUT_CANCELLED"): done(("cancel", None, node, None, name)); break
                    elif ln.startswith("CUT_ERROR"): log_line("cutplane: "+ln); done(("err", ln, node, None, name)); break
                proc.wait()
            finally:
                self._forget_child(proc)
        except Exception as e:
            log_error("base-launch", e); done(("err", str(e), node, None, name))

    # ---- process on PC: raw depth frames -> fused mesh, via fuse.py (Open3D TSDF) ----
    # ---- Process page ----
    PROC_DETAIL=(("Fast (0.6 mm)",0.6),("Normal (0.4 mm, like the scanner)",0.4),("Fine (0.3 mm, ~2x memory)",0.3),("Ultra (0.2 mm, ~6x memory)",0.2))
    def _build_process_page(self, pr):
        pr.grid_columnconfigure(0, weight=1); pr.grid_rowconfigure(1, weight=1)
        ph=ctk.CTkFrame(pr, fg_color="transparent"); ph.grid(row=0,column=0, sticky="ew", padx=16, pady=(14,4))
        ctk.CTkButton(ph, text="‹  Back", width=76, height=30, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE,
                      hover_color=CARD2, text_color=TX, command=lambda: self._set_mode("Local")).pack(side="left", padx=(0,12))
        ctk.CTkLabel(ph, text="All scans", font=ctk.CTkFont(size=18, weight="bold"), text_color=TX).pack(side="left")
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
        self.proc_cards=ctk.CTkScrollableFrame(pr, fg_color="transparent"); self.proc_cards.grid(row=1,column=0, sticky="nsew", padx=10); self._autohide(self.proc_cards)
        self.proc_cards.bind("<Configure>", lambda e: self._fit_scrollbar_later(self.proc_cards, "vertical"), add="+")
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
    def _has_raw_frames(self, local, node):
        cache=getattr(self, "_raw_frame_cache", {})
        d=os.path.join(local, "data", node, "cache")
        try: sig=(d, os.path.getmtime(d))
        except Exception: sig=(d, None)
        if sig in cache: return cache[sig]
        found=False
        try:
            with os.scandir(d) as it:
                for ent in it:
                    if ent.name.endswith(".dph"):
                        found=True; break
        except Exception: found=False
        cache[sig]=found; self._raw_frame_cache=cache
        return found

    def _proc_nodes(self, name):
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name); nodes=set()
        for d in glob.glob(os.path.join(local, "data", "*")):
            if os.path.isdir(d): nodes.add(os.path.basename(d))
        for f in glob.glob(os.path.join(local, name+"_*.ply")):
            if f.endswith(".tmp.ply"): continue      # a Prepare temp file mid-write, not a scan node
            n=os.path.basename(f)[len(name)+1:-4]
            for suf in ("_cloud","_pcfused","_clean","_edited"):
                if n.endswith(suf): n=n[:-len(suf)]
            nodes.add(n)
        return sorted(nodes)
    def _proc_versions(self, name, node):
        """The model files a scan has on this PC: [(key, label, path)] in default preference order."""
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name); out=[]
        for key,label,cands in (("edited","cleaned model",[os.path.join(local,"%s_%s_edited.ply"%(name,node))]),
                                ("clean","prepared copy",[os.path.join(local,"%s_%s_clean.ply"%(name,node)), os.path.join(local,"%s_%s_pcfused_clean.ply"%(name,node))]),
                                ("scanner","the scanner's model",[os.path.join(local,"%s_%s.ply"%(name,node)), os.path.join(local,"data",node,"fuse_mesh.ply")]),
                                ("pcfused","PC build (from raw data)",[os.path.join(local,"%s_%s_pcfused.ply"%(name,node))])):
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
        # No explicit pick: anchor on the scanner's model (the device's sealed, nicest result) so a fresh
        # open shows ONE coherent model that matches the thumbnail and the fused points - NOT the holey
        # prepared copy just because it sorts first. The prepared / PC-build versions are alternatives you
        # switch to on purpose. Coherence fix: "going back and seeing the other one feels like
        # you're editing something else."
        for v in vs:
            if v[0]=="scanner": return v
        return vs[0]
    def _toggle_alt_versions(self, node):
        s=getattr(self, "_alt_ver_open", set())
        s.discard(node) if node in s else s.add(node)
        self._alt_ver_open=s
        self._schedule_panel_refresh(10)
    def _pick_version(self, name, node, key):
        """User clicked a version chip: guard unsaved editor edits (this reloads the preview) then switch."""
        if not self._guard_unsaved_edits(): return
        self._proc_set_current(name, node, key)
    def _proc_set_current(self, name, node, key):
        self.records.setdefault(name,{}).setdefault("current",{})[node]=key; self._persist(); self._mesh_stats={}
        label={"clean":"prepared copy","scanner":"scanner's model","pcfused":"PC build","edited":"cleaned model"}.get(key,key)
        if self.selected==name:
            self._film_sel=node; self._mv_key=None
            self.set_banner("Now showing the %s of %s." % (label, self._scan_label(name, node)), MUT)
            try: self._request_shaded(name, node)               # re-render the preview from the chosen version now
            except Exception: pass
            if self.page=="projects": self._schedule_panel_refresh(50)   # move the ✓ / rebuild the version chips (deferred: don't destroy the clicked button mid-callback)
        self.after(0, lambda: self._proc_render(name))          # rebuild the cards page too, deferred for the same reason
    def _trash(self, path):
        """Move a file or folder to the desktop trash (gio), else into <dest>/.trash. Can be slow
        for a big folder (the gio call is timeout-bounded, but its own fallback move is a real
        copy+delete if .trash lands on a different filesystem) - always call via _trash_async
        from a UI handler, never directly, or a big project freezes the whole window."""
        try:
            if subprocess.run(["gio","trash",path], capture_output=True, timeout=30).returncode==0: return True
        except Exception: pass
        try:
            tdir=os.path.join(self.dest.get() or DEFAULT_DEST, ".trash"); os.makedirs(tdir, exist_ok=True)
            shutil.move(path, os.path.join(tdir, time.strftime("%Y%m%d-%H%M%S_")+os.path.basename(path))); return True
        except Exception as e:
            log_error("trash", e); return False
    def _trash_async(self, path, done):
        """Run _trash() off the main thread and deliver the result back via the queue - a project
        folder can be big, so this must never block the UI thread."""
        def _run():
            ok=self._trash(path)
            self.q.put(("call", lambda: done(ok)))
        threading.Thread(target=_run, daemon=True).start()
    def _proc_delete_version(self, name, node, key, path):
        if not self._confirm("Delete this version?", "%s: the %s version of scan %s goes to the trash.\nOther versions and the raw data stay." % (self.disp(name), dict(clean="prepared copy", scanner="scanner's model", pcfused="PC build", edited="cleaned model")[key], node)): return
        self.set_status("Moving to the trash…")
        def _done(ok):
            self.set_status("")
            if not ok: self.set_banner("Couldn't move that to the trash.", WARN); return
            self.set_banner("Moved to the trash: %s" % os.path.basename(path), MUT); self._mesh_stats={}; self.gallery_cache.pop(name, None)
            self._proc_render(name)
            if self.selected==name: self._mv_key=None; self.projects_sig=None; self.listed=False; self.start_listing()
        self._trash_async(path, _done)
    def _proc_delete_project(self):
        name=self.selected
        if not name: return
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        if not os.path.isdir(local): self.set_banner("That project is not on this PC.", WARN); return
        if not self._confirm("Delete from this PC?", "%s and everything in its folder go to the trash.\nThe copy on the scanner is not touched." % self.disp(name)): return
        self.set_status("Moving to the trash…")
        def _done(ok):
            self.set_status("")
            if not ok: self.set_banner("Couldn't move that project to the trash.", WARN); return
            self.set_banner("Moved to the trash: %s" % self.disp(name), MUT); self.gallery_cache.pop(name, None)
            self._clear_selection()   # the deleted project was selected: reset the centre (title, NEXT bar, scan strip), don't leave it stale
            self.projects_sig=None; self.listed=False; self.start_listing(); self._proc_render(None)
        self._trash_async(local, _done)
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
        try:
            self._proc_render_body(name)
        except Exception as e:
            log_error("proc_render", e)
            for w in self.proc_cards.winfo_children():
                if w is not self._proc_empty: w.destroy()
            self._proc_empty.grid_remove()
            ctk.CTkLabel(self.proc_cards, text="Couldn't refresh this page (see Help > Log). Try picking the project again.",
                         text_color=WARN, font=ctk.CTkFont(size=12)).grid(row=0, column=0, sticky="w", padx=16, pady=20)
    def _proc_render_body(self, name):
        if not name:
            self.proc_title.configure(text=""); self._proc_empty.grid(); return
        self._proc_empty.grid_remove()
        nodes=self._proc_nodes(name); local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        self.proc_title.configure(text="%d scan%s · %s" % (len(nodes), "" if len(nodes)==1 else "s", name if self.disp(name)!=name else ""))
        self._proc_next_strip(name, nodes, local)
        for i,node in enumerate(nodes):
            vs=self._proc_versions(name, node); cur=self._proc_current(name, node)
            raw=self._has_raw_frames(local, node)
            card=ctk.CTkFrame(self.proc_cards, fg_color=CARD, corner_radius=14); card.grid(row=i+1, column=0, sticky="ew", padx=6, pady=6)
            card.grid_columnconfigure(1, weight=1)
            thumb=os.path.join(local, "data", node, "preview.png")
            if not os.path.exists(thumb): thumb=os.path.join(local, "%s_%s.png" % (name, node))
            tl=ctk.CTkLabel(card, text="", fg_color="#0a0c10", corner_radius=8, width=110, height=70); tl.grid(row=0,column=0, rowspan=3, padx=(14,12), pady=12)
            if os.path.exists(thumb):
                try: self.imgs["proc_"+node]=cimg(thumb, 110); tl.configure(image=self.imgs["proc_"+node])
                except Exception: pass
            elif cur and self._auto_mesh_preview(): self._card_thumb(name, node, cur[2], tl)      # no scanner picture: optional mesh render only
            top=ctk.CTkFrame(card, fg_color="transparent"); top.grid(row=0,column=1, sticky="ew", pady=(12,0))
            ctk.CTkLabel(top, text=self._scan_label(name, node), font=ctk.CTkFont(size=14, weight="bold"), text_color=TX).pack(side="left")
            _order=[n for n in nodes if n!="combined"]   # index into the real scans, not the raw enumerate i (which would count a combined node)
            ctk.CTkLabel(top, text=("scan %d of %d" % (_order.index(node)+1, len(_order)) if node!="combined" else "all aligned scans in one model"), text_color=MUT, font=ctk.CTkFont(size=11)).pack(side="left", padx=10)
            if node=="combined": status="%d version%s · built from the scans you lined up" % (len(vs), "" if len(vs)==1 else "s")
            else: status=("no 3D model yet · raw data on this PC" if raw else "no 3D model yet · no raw data on this PC") if not vs else ("%d version%s · raw data on this PC" % (len(vs), "" if len(vs)==1 else "s") if raw else "%d version%s · no raw data on this PC" % (len(vs), "" if len(vs)==1 else "s"))
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
                                text_color=(AC if is_cur else TX), font=ctk.CTkFont(size=11), command=lambda n=name,nd=node,k=key: self._pick_version(n, nd, k)); b.pack(side="left", padx=(6,0))
                self._tip(b, "%s · %s\nClick to make this the version the preview and exports use." % (os.path.basename(path), human(os.path.getsize(path))))
                x=ctk.CTkButton(chip, text="✕", width=22, height=22, corner_radius=9, fg_color="transparent", hover_color="#3a2530", text_color=MUT,
                                font=ctk.CTkFont(size=11), command=lambda n=name,nd=node,k=key,pth=path: self._proc_delete_version(n, nd, k, pth)); x.pack(side="left", padx=(0,4))
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
        # include the version in the key, or a node with both a scanner and a prepared mesh renders one
        # version and serves it for the other (same bug fixed for the shaded still/film).
        verkey=(self._proc_current(name, node) or (None,))[0]
        key="%s__%s__%s__card" % (name, node, verkey or "v"); out=os.path.join(THUMBS, key+".png")
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
    def _base_sig(self, path):
        try: st=os.stat(path); return (st.st_mtime, st.st_size)
        except OSError: return None
    def _base_verdict(self, path):
        """Cached table verdict for the CURRENT contents of `path`, or None if not computed yet / stale.
        The cache key carries mtime+size so a replaced model (Prepare, Restore) isn't answered from the old
        file's result. The returned dict's 'present' is True (table), False (none) or None (couldn't tell)."""
        if not path: return None
        e=self._base_geom.get(path)
        if e is not None and e.get("sig")==self._base_sig(path): return e
        return None
    def _table_ruled_out(self, name, node):
        """True only when the geometry positively found NO table on this scan's current model (scanner
        already trimmed it), so we don't nag to Cut base. Unknown/undetected/still-checking -> False, i.e.
        fall back to offering the cut."""
        cur=self._proc_current(name, node)
        if not cur: return False
        e=self._base_verdict(cur[2])
        return bool(e is not None and e.get("present") is False)
    def _want_base(self, path):
        """Compute the table verdict for `path` once (per file version), in the background, then refresh."""
        if not path or path in self._base_busy: return
        sig=self._base_sig(path)
        if sig is None: return
        e=self._base_geom.get(path)
        if e is not None and e.get("sig")==sig: return   # already have a fresh verdict for this exact file
        self._base_busy.add(path)
        def work():
            r=None
            try: r=detect_base(path)
            except Exception as e2: log_error("detect_base", e2)
            self.q.put(("base_geom", path, sig, r))
        threading.Thread(target=work, daemon=True).start()
    def _proc_next(self, name, nodes, local):
        """What to do now for this project: (title, detail, button text, command, step index into STEPS)."""
        scans=[n for n in nodes if n!="combined"]
        unbuilt=[n for n in scans if not self._proc_versions(name, n) and self._has_raw_frames(local, n)]
        built=[n for n in scans if self._proc_versions(name, n)]
        planes=self._base_planes(name); nobase=[n for n in built if n not in planes]
        sel=self._film_sel if self._film_sel in scans else None   # the scan you're looking at
        # SELECTION-FIRST: if the scan you're looking at is raw, NEXT is to build THAT scan
        if sel is not None and sel in unbuilt:
            return ("Build %s" % self._scan_label(name, sel),
                    "This scan is still raw data. Build its 3D model on this PC (a few seconds on a graphics card), or use One-tap Edit on the scanner.",
                    "⚙  Build model", lambda n=sel: self._proc_build(name, [n]), 0, None)
        # base cut is about the scan you're looking at. If that scan's base is already done, don't nag about
        # another scan's base while you inspect a finished one - move on to the project step (Combine still
        # flags any uncut bases before it merges).
        if nobase and not (sel is not None and sel in built and sel not in nobase):
            n0=sel if sel in nobase else nobase[0]
            def go(n=n0): self._pick_scan_by_node(name, n); self.on_remove_base(n)
            def skip(n=n0): self._skip_base(name, n)
            if self._table_ruled_out(name, n0):
                # geometry found no flat table on this scan. Detection can miss a sparse table, so don't
                # decide for the user: lead with a one-click skip they confirm (recorded, never asked again),
                # and keep "cut anyway" as the fallback for a table the detector may have missed.
                return ("No table detected on %s" % self._scan_label(name, n0),
                        "The geometry shows no flat table under this scan, so there is probably nothing to cut. Skip it (the scanner most likely trimmed it already), or cut anyway if a table is still on the part.",
                        "✓  Looks clear - skip base cut", skip, 1,
                        ("Cut base anyway", go))
            return ("Cut the base off %s" % self._scan_label(name, n0),
                    "%d of %d scan%s may still have the table under the part. Drag one line above it and apply, or skip if this scan has no base. The cut is remembered and applied when the scans are combined." % (len(nobase), len(built), "" if len(built)==1 else "s"),
                    "✂  Remove base on %s" % self._scan_label(name, n0), go, 1,
                    ("No base - skip", skip))
        if unbuilt:
            return ("Build the 3D model%s" % ("" if len(unbuilt)==1 else "s"),
                    "%d scan%s %s raw data only. Easiest is One-tap Edit on the scanner, then share the project again. Or build here now (seconds on a graphics card) and prepare it yourself." % (len(unbuilt), "" if len(unbuilt)==1 else "s", "has" if len(unbuilt)==1 else "have"),
                    "⚙  Build %d model%s here" % (len(unbuilt), "" if len(unbuilt)==1 else "s"), lambda: self._proc_build(name, unbuilt), 0, None)
        keep_sep=bool(self.records.get(name, {}).get("keep_separate"))
        # Several built scans and no combined model yet: PointYoink assumes they're sides of one object and
        # pushes Combine - but they might be separate objects. Offer the choice instead of assuming, and
        # remember it (reversible below with "Combine them after all").
        if len(built)>=2 and "combined" not in nodes and not keep_sep:
            return ("Combine these scans into one model?",
                    "You have %d scans. If they're sides of one object, line them up into a single model. If they're separate objects, keep them apart and prepare or export each on its own." % len(built),
                    "⧉  Combine scans…", lambda: self._align_dialog(name), 2,
                    ("Keep separate - different objects", lambda: self._keep_separate(name)))
        if keep_sep and "combined" not in nodes and len(built)>=2:
            # SELECTION-FIRST: Prepare/Export the scan you're actually looking at, not just the first
            # unprepared one - else viewing Scan 2 could Prepare/Export Scan 1. Only walk to the next
            # unprepared scan when nothing (or a raw/uncut scan) is selected.
            unprepared=[n for n in built if not any(k=="clean" for k,_,_ in self._proc_versions(name, n))]
            target=sel if (sel is not None and sel in built) else (unprepared[0] if unprepared else built[0])
        else:
            target="combined" if "combined" in nodes else (built[0] if built else None)
        if not target: return ("Nothing to prepare yet", "Share this project over WiFi as Full project to get its raw data, or plug the scanner in.", None, None, 0, None)
        undo=("⧉  Combine them after all", lambda: self._unkeep_separate(name)) if (keep_sep and len(built)>=2 and "combined" not in nodes) else None
        vs=self._proc_versions(name, target); lab=self._scan_label(name, target)
        if not any(k=="clean" for k,_,_ in vs):
            return ("Prepare the %s model" % lab.lower() if target=="combined" else "Prepare %s" % lab, "Remove floating pieces, smooth, fill holes. You see before and after and keep or discard.",
                    "✦  Prepare…", lambda: self._prepare_dialog(name, target), 3, undo)
        return ("Export", "%s is prepared. Save it as STL for a slicer, or OBJ, GLB, PLY." % lab, "⬆  Export…", lambda: self._export_dialog(name, target), 4, undo)
    def _undo_base_cut(self, name, node):
        """Put a scan back the way it was before Remove base: the cut copy is trashed (or the prepared copy it
        replaced is restored), the remembered plane is forgotten, and the scan shows the scanner's model again."""
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name); out=os.path.join(local, "%s_%s_clean.ply" % (name, node))
        rec=self.records.setdefault(name,{}); bk=(rec.get("base_cut_backup",{}) or {}).pop(node, None); restored=False
        try:
            if bk and os.path.exists(bk): shutil.move(bk, out); os.utime(out, None); restored=True
            elif os.path.exists(out): self._trash(out)
        except Exception as e:
            log_error("undo-base-cut", e); self.set_banner("Couldn't undo the cut (see Help > Log).", WARN); return
        (rec.get("base_plane",{}) or {}).pop(node, None)
        if restored: rec.setdefault("current",{})[node]="clean"
        else: (rec.get("current",{}) or {}).pop(node, None)          # back to the default: the scanner's model
        self._persist(); self.projects_sig=None; self._mesh_stats={}; self.gallery_cache.pop(name, None)
        self.set_banner("%s: base cut undone%s." % (self._scan_label(name, node), " - the prepared copy is back" if restored else ""), OK)
        if self.selected==name: self._proc_render(name); self._mv_key=None; self._maybe_schedule_shaded(name, node, 250)
        else: self._proc_refresh()
    def _skip_base(self, name, node):
        """Mark a scan as having no base to cut (the NEXT bar suggested it, but detection is not certain).
        Same as choosing No base inside the cut dialog: remembered, and treated as 'no table' when combining."""
        self.records.setdefault(name,{}).setdefault("base_plane",{})[node]={"skip": True}; self._persist()
        self.set_banner("Marked %s as no base to cut." % self._scan_label(name, node), MUT)
        if self.selected==name:
            self._panel_refresh()                       # rebuilds the NEXT bar too (with proper nodes/local)
    def _keep_separate(self, name):
        """The user says these scans are different objects, not sides of one. Stop pushing Combine on the
        NEXT bar; each scan is prepared/exported on its own. Reversible ("Combine them after all")."""
        self.records.setdefault(name, {})["keep_separate"]=True; self._persist()
        self.set_banner("Keeping these scans separate - prepare or export each on its own. Combine is still one click away if you change your mind.", MUT)
        if self.selected==name: self._panel_refresh()
    def _unkeep_separate(self, name):
        """Undo Keep separate: the NEXT bar offers Combine again."""
        self.records.setdefault(name, {}).pop("keep_separate", None); self._persist()
        if self.selected==name: self._panel_refresh()
    def _pick_scan_by_node(self, name, node):
        """Select a scan tile the way a click on the strip would (so Remove base and the preview follow)."""
        if not self._guard_unsaved_edits(): return
        if self.selected!=name: self.select_project(name)
        self._film_sel=node; self._mark_scan(node)
        try: self._maybe_schedule_shaded(name, node, 250)
        except Exception: pass
    # Each step: a purpose line, a couple of short "where / do" cards, an optional info card that jumps to
    # another step, a where-label for the picture, and a set of labelled screenshots shown one at a time.
    GUIDE=(dict(name="Import", icon="import", title="Get your scan onto the PC",
                purpose="Pull the finished scan, or the full project, off the MIRACO over USB or Wi-Fi.",
                steps=[("On the scanner", "Share to PC, then pick USB Cable or Wi-Fi."),
                       ("In PointYoink", "Click USB or WiFi here, tick the project, and Import.")],
                info=None, where="On the scanner",
                shots=[("USB", "scanner-usb-tab"), ("Wi-Fi", "scanner-wifi-code")],
                caption="Finished models is quick; Full project also brings the raw frames for Build and Combine."),
           dict(name="Build", icon="build", title="Build a model",
                purpose="Turn your captured scan into a surface you can edit and export.",
                steps=[("On the scanner", "Open your scan and tap One-tap Edit. Fusion + Mesh also works."),
                       ("In PointYoink", "Import the Full project, then choose Build model.")],
                info=("Already have a finished model?", "Skip to Cut base", 2), where="On the scanner",
                shots=[("One-tap Edit", "scanner-onetap-edit"), ("Fusion", "scanner-fusion-panel"), ("Mesh", "scanner-mesh-panel")],
                caption="Choose One-tap Edit for the simplest workflow."),
           dict(name="Cut base", icon="cut-base", title="Cut the base off",
                purpose="If the table or turntable is still attached, remove it here.",
                steps=[("In PointYoink", "Adjust the cut height until only the table is red, then Apply cut."),
                       ("Remembered", "PointYoink keeps the cut and reuses it when scans are combined.")],
                info=None, where="In PointYoink",
                shots=[("Remove base", "app-cutbase")], caption="Grey stays, red goes; only the table should be red."),
           dict(name="Combine", icon="combine", title="Combine the sides",
                purpose="Scanned each side separately? Line them up into one model.",
                steps=[("In PointYoink", "Pick 3-5 matching spots on two scans, line up, Keep."),
                       ("Then", "Repeat for each side and build one model from all their frames.")],
                info=None, where="In PointYoink",
                shots=[("Combine scans", "app-combine")], caption="Click the same feature on both scans, then Line up (or Auto)."),
           dict(name="Prepare", icon="prepare", title="Prepare the surface",
                purpose="Remove floating pieces, reduce triangles, and smooth the surface.",
                steps=[("On the scanner", "Isolation, Simplify and Smooth, one panel each."),
                       ("In PointYoink", "Prepare runs all three on a copy, with before and after.")],
                info=None, where="On the scanner",
                shots=[("Isolation", "scanner-isolation"), ("Simplify", "scanner-simplify"), ("Smooth", "scanner-smooth")],
                caption="Keep or Discard the result; the original is never changed."),
           dict(name="Export", icon="export", title="Export for a slicer",
                purpose="Save the finished model as STL, OBJ, GLB or PLY.",
                steps=[("In PointYoink", "Pick the version, the format and the folder."),
                       ("Checked first", "The size and a mesh check are shown before it saves.")],
                info=None, where="In PointYoink",
                shots=[("Export", "app-export")], caption="STL for slicers; the size and a mesh check are shown before it saves."))
    def _when_ready(self, fn):
        """Run fn once the splash is gone and the main window is on screen. A dialog opened earlier is attached to the
        withdrawn main window and drags it onto the screen half-built."""
        if getattr(self, "_splash", None) or not self.winfo_viewable(): self.after(500, lambda: self._when_ready(fn)); return
        fn()
    def _howto_when_ready(self):
        """The first-run panel waits until the splash is gone and the window is up; a dialog opened earlier drags the
        main window onto the screen half-built."""
        if getattr(self, "_splash", None) or not self.winfo_viewable(): self.after(700, self._howto_when_ready); return
        if not self.cfg.get("seen_howto") and "howto" not in getattr(self, "_dialogs", {}): self._howto_dialog()
    def _zoom_image(self, path):
        """Pop a big, dismissible view of a screenshot - walkthrough thumbnails are small to read, so any
        image is click-to-enlarge. Click the image or press Esc to close."""
        try:
            im=Image.open(path); im.load(); iw,ih=im.size      # load the image FIRST: if it fails, no empty window is left behind
        except Exception as e:
            log_error("zoom-image", e); return
        try:
            top=tk.Toplevel(self); top.configure(bg="#05070a"); top.title(os.path.basename(path))
            try: top.attributes("-topmost", True)
            except Exception: pass
            # size to THIS window's monitor, not winfo_screenwidth() (that spans every monitor on a multi-head
            # desktop, which made the popup as big as all the screens together).
            try:
                mw=self.winfo_width() or 1600; mh=self.winfo_height() or 1000
                mx=self.winfo_rootx(); my=self.winfo_rooty()
            except Exception:
                mw,mh,mx,my=1600,1000,120,120
            maxw=min(1500, max(1,int(mw*0.92))); maxh=min(920, max(1,int(mh*0.92)))
            scale=min(maxw/iw, maxh/ih, 1.0)                    # fit the window; never upscale past the source
            w,h=max(1,int(iw*scale)), max(1,int(ih*scale))
            img=ctk.CTkImage(dark_image=im, light_image=im, size=(w,h)); top._zimg=img
            wrap=ctk.CTkFrame(top, fg_color="#05070a"); wrap.pack(fill="both", expand=True)
            lbl=ctk.CTkLabel(wrap, image=img, text="", cursor="hand2"); lbl.pack(padx=12, pady=(12,4))
            ctk.CTkLabel(wrap, text="click the image or press Esc to close", text_color=MUT, font=ctk.CTkFont(size=11)).pack(pady=(0,10))
            W,H=w+24,h+56
            try: top.geometry("%dx%d+%d+%d" % (W,H, mx+max(0,(mw-W)//2), my+max(0,(mh-H)//2)))   # centred on the app window
            except Exception: pass
            for wg in (top, lbl): wg.bind("<Button-1>", lambda e: top.destroy())
            top.bind("<Escape>", lambda e: top.destroy())
        except Exception as e:
            log_error("zoom-image", e)
            try: top.destroy()          # don't leave a half-built window if something after Toplevel() failed
            except Exception: pass
    def _clickimg(self, parent, path, w, **cellkw):
        """An image in a card that enlarges on click (hand cursor + click binding)."""
        cell=ctk.CTkFrame(parent, fg_color="#0a0c10", corner_radius=10, border_width=1, border_color=STROKE); cell.pack(**cellkw)
        try:
            key="ci_"+path
            self.imgs[key]=cimg(path, w)
            lbl=ctk.CTkLabel(cell, image=self.imgs[key], text="", cursor="hand2"); lbl.pack(padx=6, pady=6)
            lbl.bind("<Button-1>", lambda e: self._zoom_image(path))
        except Exception: pass
        return cell
    def _howto_dialog(self):
        # A guide with a clickable step strip across the top, short scanner/PC instructions on the left, and
        # one readable screenshot (with tabs for the alternatives) on the right.
        t=self._top("How PointYoink works", 1180, 640, key="howto")
        if t is None: return
        adir=os.path.join(HERE, "assets", "device")
        G=self.GUIDE; N=len(G); self._guide_i=0; self._guide_shot=0
        hdr=ctk.CTkFrame(t, fg_color="transparent"); hdr.pack(fill="x", padx=28, pady=(14,2))
        ctk.CTkLabel(hdr, text="How PointYoink works", text_color=TX, font=ctk.CTkFont(size=23, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(hdr, text="From scanner to usable model.", text_color=MUT, font=ctk.CTkFont(size=13)).pack(anchor="w")
        # clickable step strip
        strip=ctk.CTkFrame(t, fg_color="transparent"); strip.pack(fill="x", padx=22, pady=(8,0))
        cells=[]
        for k,g in enumerate(G):
            c=ctk.CTkFrame(strip, fg_color="transparent"); c.pack(side="left", expand=True, fill="x")
            top_=ctk.CTkFrame(c, fg_color="transparent"); top_.pack()
            num=ctk.CTkLabel(top_, text=str(k+1), width=30, height=30, corner_radius=15, fg_color=CARD2, text_color=MUT, font=ctk.CTkFont(size=13, weight="bold")); num.pack(side="left", padx=(0,9))
            nm=ctk.CTkLabel(top_, text=g["name"], text_color=MUT, font=ctk.CTkFont(size=15, weight="bold")); nm.pack(side="left")
            ul=tk.Frame(c, bg=BG, height=3, bd=0, highlightthickness=0); ul.pack(fill="x", pady=(8,0))
            for wg in (c, top_, num, nm): wg.configure(cursor="hand2"); wg.bind("<Button-1>", lambda e,kk=k: jump(kk))
            cells.append((num, nm, ul))
        tk.Frame(t, bg=STROKE, height=1, bd=0, highlightthickness=0).pack(fill="x", padx=22)
        body=ctk.CTkFrame(t, fg_color="transparent")   # packed AFTER nav, so the footer is reserved at the bottom and a tall screenshot can never push the buttons off screen
        body.grid_columnconfigure(0, weight=5, uniform="c"); body.grid_columnconfigure(1, weight=7, uniform="c"); body.grid_rowconfigure(0, weight=1)
        leftw=ctk.CTkFrame(body, fg_color="transparent"); leftw.grid(row=0, column=0, sticky="nsew", padx=(2,16))
        rightw=ctk.CTkFrame(body, fg_color="transparent"); rightw.grid(row=0, column=1, sticky="nsew")
        nav=ctk.CTkFrame(t, fg_color="transparent"); nav.pack(side="bottom", fill="x", padx=28, pady=(0,16))
        body.pack(side="top", fill="both", expand=True, padx=24, pady=(14,6))
        def close(): self.cfg["seen_howto"]=True; save_cfg(self.cfg); self._dialogs.pop("howto", None); t.destroy()
        back_b=ctk.CTkButton(nav, text="‹  Back", width=96, height=36, corner_radius=18, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=lambda: jump(self._guide_i-1)); back_b.pack(side="left")
        pg=ctk.CTkLabel(nav, text="", text_color=MUT, font=ctk.CTkFont(size=12)); pg.pack(side="left", padx=16)
        next_b=ctk.CTkButton(nav, text="Next", height=36, corner_radius=18, fg_color=AC, hover_color=AC_H, text_color="#04121f", font=ctk.CTkFont(size=13, weight="bold"), command=lambda: (close() if self._guide_i>=N-1 else jump(self._guide_i+1))); next_b.pack(side="right")
        ctk.CTkButton(nav, text="Close guide", width=100, height=36, corner_radius=18, fg_color="transparent", hover_color=CARD2, text_color=MUT, command=close).pack(side="right", padx=10)
        def settab(k): self._guide_shot=k; render()
        def jump(k): self._guide_i=max(0, min(N-1, k)); self._guide_shot=0; render()
        def render():
            for k,(num,nm,ul) in enumerate(cells):   # strip highlight
                on=(k==self._guide_i)
                num.configure(fg_color=(AC if on else CARD2), text_color=("#04121f" if on else MUT)); nm.configure(text_color=(TX if on else MUT)); ul.configure(bg=(AC if on else BG))
            for w in leftw.winfo_children(): w.destroy()
            for w in rightw.winfo_children(): w.destroy()
            g=G[self._guide_i]
            shots=[(lab,s) for lab,s in g["shots"] if os.path.exists(os.path.join(adir, s+".png"))]
            # LEFT: title, purpose, short instruction cards (top-aligned)
            ctk.CTkLabel(leftw, text=g["title"], text_color=TX, font=ctk.CTkFont(size=28, weight="bold"), anchor="w", justify="left").pack(anchor="w", pady=(6,3))
            ctk.CTkLabel(leftw, text=g["purpose"], text_color=MUT, font=ctk.CTkFont(size=15), anchor="w", justify="left", wraplength=440).pack(anchor="w", pady=(0,14))
            for eb, txt in g["steps"]:
                cf=ctk.CTkFrame(leftw, fg_color=CARD2, corner_radius=10); cf.pack(fill="x", pady=6)
                ctk.CTkLabel(cf, text=eb.upper(), text_color=AC, font=ctk.CTkFont(size=11, weight="bold"), anchor="w").pack(fill="x", padx=16, pady=(12,0))
                ctk.CTkLabel(cf, text=txt, text_color=TX, font=ctk.CTkFont(size=15), anchor="w", justify="left", wraplength=410).pack(fill="x", padx=16, pady=(2,13))
            if g.get("info"):
                head, link, tgt = g["info"]
                nf=ctk.CTkFrame(leftw, fg_color="#15304d", corner_radius=10); nf.pack(fill="x", pady=6)
                ctk.CTkLabel(nf, text=head, text_color=AC, font=ctk.CTkFont(size=12, weight="bold"), anchor="w").pack(fill="x", padx=14, pady=(9,0))
                ctk.CTkButton(nf, text=link+"  ›", height=24, corner_radius=6, fg_color="transparent", hover_color="#1c3a5a", text_color=AC, font=ctk.CTkFont(size=12, weight="bold"), anchor="w", command=lambda tt=tgt: jump(tt)).pack(fill="x", padx=10, pady=(0,8))
            # RIGHT: "On the scanner" / "In PointYoink" label + Enlarge, one screenshot, tabs for the rest
            wl=ctk.CTkFrame(rightw, fg_color="transparent"); wl.pack(fill="x")
            ctk.CTkLabel(wl, text=g["where"], text_color=MUT, font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
            if shots:
                si=max(0, min(self._guide_shot, len(shots)-1)); path=os.path.join(adir, shots[si][1]+".png")
                ctk.CTkButton(wl, text="⤢  Enlarge", height=26, corner_radius=8, fg_color="transparent", hover_color=CARD2, text_color=AC, font=ctk.CTkFont(size=13), command=lambda p=path: self._zoom_image(p)).pack(side="right")
                # fit to BOTH a max width and a max height: PointYoink's own screens are near-square and would
                # otherwise be tall enough to push the footer buttons off the dialog.
                try: iw,ih=Image.open(path).size
                except Exception: iw,ih=2,1
                scale=min(620.0/iw, 340.0/ih); w=max(220, int(iw*scale))   # cap height too, leaving room for the label, caption and footer buttons
                self._clickimg(rightw, path, w, pady=(8,0))
                if len(shots)>1:
                    tabs=ctk.CTkFrame(rightw, fg_color="transparent"); tabs.pack(fill="x", pady=(12,0))
                    for k,(lab,s) in enumerate(shots):
                        on=(k==si)
                        ctk.CTkButton(tabs, text=lab, height=38, corner_radius=8, fg_color=(AC if on else CARD2), hover_color=(AC_H if on else STROKE), text_color=("#04121f" if on else TX), font=ctk.CTkFont(size=13, weight="bold"), command=lambda kk=k: settab(kk)).pack(side="left", expand=True, fill="x", padx=3)
                if g.get("caption"): ctk.CTkLabel(rightw, text=g["caption"], text_color=DIM, font=ctk.CTkFont(size=12), wraplength=620).pack(pady=(10,0))
            else:
                ph=ctk.CTkFrame(rightw, fg_color=CARD, corner_radius=12); ph.pack(fill="both", expand=True, pady=(8,0))
                inner=ctk.CTkFrame(ph, fg_color="transparent"); inner.pack(expand=True)
                pic=_icon(g["icon"], "muted", 54)
                if pic is not None: ctk.CTkLabel(inner, image=pic, text="").pack(pady=(0,12))
                ctk.CTkLabel(inner, text="This step happens right here in PointYoink.\nThe NEXT bar walks you straight into it.", text_color=MUT, font=ctk.CTkFont(size=13), justify="center").pack()
            back_b.configure(state=("disabled" if self._guide_i==0 else "normal"))
            next_b.configure(text=("Done" if self._guide_i>=N-1 else "Next: %s  ›" % G[self._guide_i+1]["name"]))
            pg.configure(text="%d of %d" % (self._guide_i+1, N))
        try:
            t.bind("<Left>", lambda e: jump(self._guide_i-1)); t.bind("<Right>", lambda e: jump(self._guide_i+1)); t.bind("<Escape>", lambda e: close())
        except Exception: pass
        t.protocol("WM_DELETE_WINDOW", close)
        render()
    def _next_refresh(self, name=None, nodes=None, local=None):
        """The NEXT bar under the project title on the Projects page: what to do now, the step trail, one button."""
        ns=self.next_strip
        for w in ns.winfo_children(): w.destroy()
        try:
            self._next_refresh_body(ns, name, nodes, local)
        except Exception as e:
            log_error("next_refresh", e)
            for w in ns.winfo_children(): w.destroy()
            ns.pack_forget()
    def _next_refresh_body(self, ns, name, nodes, local):
        if self.page!="projects" or not name: ns.pack_forget(); return
        title, detail, btxt, cmd, step, alt = self._proc_next(name, nodes, local)
        ns.pack(fill="x", pady=(2,4)); ns.grid_columnconfigure(1, weight=1)   # compact: give the 3D preview more room
        ctk.CTkLabel(ns, text="N E X T", text_color=MUT, font=ctk.CTkFont(size=9, weight="bold")).grid(row=0,column=0, padx=(14,10), pady=(9,0), sticky="w")   # a quiet section label, not a button
        hb=ctk.CTkButton(ns, text="How this works  (?)", width=152, height=24, corner_radius=12, fg_color="transparent", border_width=1, border_color=STROKE, hover_color="#15304d", text_color=AC, font=ctk.CTkFont(size=11), command=self._howto_dialog)
        hb.grid(row=2,column=0, padx=(12,26), pady=(0,8), sticky="w")   # right pad separates it from the step trail
        tl=ctk.CTkLabel(ns, text=title, text_color=TX, font=ctk.CTkFont(size=14, weight="bold"), anchor="w", justify="left"); tl.grid(row=0,column=1, sticky="w", pady=(8,0))
        self._tip(tl, detail)   # the per-step "why" on hover - no inline expand that jumps the layout; the full guide is the How this works button
        trail=ctk.CTkFrame(ns, fg_color="transparent"); trail.grid(row=2,column=1, sticky="w", pady=(0,8))
        nb=[None]
        def relayout(e):
            """Wide: the button sits on the right, text wraps before it. Narrow: the button drops under the text."""
            wide=e.width>=760
            wrap=max(240, e.width-(320 if (wide and btxt) else 130)); tl.configure(wraplength=wrap)
            if nb[0] is not None:
                if wide: nb[0].grid(row=0,column=2, rowspan=3, padx=16, pady=10, sticky="e")
                else: nb[0].grid(row=3,column=1, padx=(0,14), pady=(0,12), sticky="w")
        if getattr(self, "_next_cfg_id", None):
            try: ns.unbind("<Configure>", self._next_cfg_id)
            except Exception: pass
        self._next_cfg_id=ns.bind("<Configure>", lambda e: (tl.winfo_exists() and relayout(e)), add="+")
        for i,nm in enumerate(self.STEPS):
            col=(OK if i<step else (AC if i==step else DIM)); mark=("✓ " if i<step else ("▶ " if i==step else ""))
            ctk.CTkLabel(trail, text=mark+nm, text_color=col, font=ctk.CTkFont(size=11, weight=("bold" if i==step else "normal"))).pack(side="left")
            if i<len(self.STEPS)-1: ctk.CTkLabel(trail, text="  →  ", text_color=DIM, font=ctk.CTkFont(size=11)).pack(side="left")
        if btxt:
            bwrap=ctk.CTkFrame(ns, fg_color="transparent")
            ctk.CTkButton(bwrap, text=btxt, width=210, height=36, corner_radius=18, fg_color=AC, hover_color=AC_H, text_color="#04121f", font=ctk.CTkFont(size=13, weight="bold"), command=cmd).pack()
            if alt:   # a secondary "No base - skip" / suggestion opt-out sits under the main action
                ctk.CTkButton(bwrap, text=alt[0], width=220, height=26, corner_radius=13, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=MUT, font=ctk.CTkFont(size=11), command=alt[1]).pack(pady=(6,0))
            nb[0]=bwrap; nb[0].grid(row=0,column=2, rowspan=3, padx=16, pady=10, sticky="e")
    def _proc_next_strip(self, name, nodes, local):
        title, detail, btxt, cmd, step, alt = self._proc_next(name, nodes, local)
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
            bw=ctk.CTkFrame(strip, fg_color="transparent"); bw.grid(row=0,column=2, rowspan=3, padx=16, pady=12)
            ctk.CTkButton(bw, text=btxt, width=190, height=36, corner_radius=18, fg_color=AC, hover_color=AC_H, text_color="#04121f", font=ctk.CTkFont(size=13, weight="bold"), command=cmd).pack()
            if alt: ctk.CTkButton(bw, text=alt[0], width=190, height=24, corner_radius=12, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=MUT, font=ctk.CTkFont(size=11), command=alt[1]).pack(pady=(6,0))
    def _schedule_panel_refresh(self, delay=80):
        job=getattr(self, "_panel_job", None)
        if job:
            try: self.after_cancel(job)
            except Exception: pass
        self._panel_job=self.after(delay, lambda: (setattr(self, "_panel_job", None), self._panel_refresh()))

    # scanner's scan-settings + counts, decoded from property.rvproj (device's per-scan metadata). Codes
    # confirmed by correlating with the on-device labels; unconfirmed ones fall back to the raw number so we
    # never show a wrong label. (accuracy 0/2, object types other than General/Dark, and Near/Far are not
    # yet decoded - Near/Far isn't even in this file.)
    _META_ACC={1:"High"}
    _META_ALIGN={0:"Feature", 1:"Marker"}
    _META_OBJ={0:"General", 4:"Dark"}
    def _scan_meta(self, name, node):
        """Scan metadata (counts + scan modes) from the device's property.rvproj - imported as
        name_node.rvproj, or read live off the device mount. Returns a dict or None."""
        if not name or not node or node=="combined": return None
        local=os.path.join(self.dest.get() or DEFAULT_DEST, name)
        for p in (os.path.join(local, "%s_%s.rvproj" % (name, node)),
                  os.path.join(local, "data", node, "property.rvproj"),
                  os.path.join(PROJECTS, name, "data", node, "property.rvproj")):
            try:
                if not os.path.exists(p): continue
                d=json.load(open(p)); sp=d.get("scan_param", {})
                def lab(m, v): return m.get(v) if v in m else ("#%s" % v if v is not None else None)
                modes=[x for x in (lab(self._META_ACC, sp.get("accuracy_type")),
                                   lab(self._META_ALIGN, sp.get("scan_mode")),
                                   lab(self._META_OBJ, sp.get("scan_object"))) if x]
                return {"verts": d.get("vertex_count"), "polys": d.get("face_count"),
                        "points": d.get("point_count"), "frames": d.get("vf_count"),
                        "modes": modes, "color": {0:"off", 1:"on"}.get(sp.get("color_type"))}
            except Exception: pass
        return None

    def _panel_refresh(self):
        """The right column on the Projects page: what to do next, the selected scan's versions and actions, project actions."""
        pp=self.projpanel
        # Unmap while rebuilding - this panel gets a dozen+ widgets on every scan click, the
        # exact CTkScrollableFrame redraw-recursion trigger. Safe to
        # restore visibility unconditionally: this only runs while page=="projects" (checked
        # at every call site), which is the only time projpanel should be gridded anyway.
        pp.grid_remove()
        for w in pp.winfo_children(): w.destroy()
        try:
            self._panel_refresh_body(pp)
        except Exception as e:
            # this panel is cleared above before being rebuilt - any exception past that point used to
            # leave it permanently blank with nothing in the log (a Tk-callback exception, not caught by
            # drain_loop). Surfaced after a Remove Base completed and the panel went empty.
            log_error("panel_refresh", e)
            for w in pp.winfo_children(): w.destroy()
            ctk.CTkLabel(pp, text="Couldn't refresh this panel (see Help > Log). Try selecting the project again.",
                         text_color=WARN, font=ctk.CTkFont(size=12), wraplength=230, justify="left").pack(anchor="w", padx=16, pady=20)
        finally:
            if not getattr(self, "_in_edit_mode", False):   # keep the edit palette up while editing
                pp.grid(); self._fit_scrollbar_later(pp, "vertical", 120)
    def _panel_refresh_body(self, pp):
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
            raw=self._has_raw_frames(local, node); has_prep=any(k=="clean" for k,_,_ in vs)
            hdr=ctk.CTkFrame(pp, fg_color=CARD2, corner_radius=10); hdr.pack(fill="x", padx=6, pady=(10,4))   # group the scan status so it isn't loose floating text
            tr=ctk.CTkFrame(hdr, fg_color="transparent"); tr.pack(fill="x", padx=12, pady=(10,2))
            ctk.CTkLabel(tr, text=self._scan_label(name, node), text_color=TX, font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(side="left")
            if node!="combined":
                rb=ctk.CTkButton(tr, text="✎", width=26, height=24, corner_radius=6, fg_color="transparent", hover_color=CARD, text_color=MUT, font=ctk.CTkFont(size=13), command=lambda n=name,nd=node: self._rename_scan(n, nd)); rb.pack(side="right")
                self._tip(rb, "Name this scan: front, back, left side…")
            order=[n for n in nodes if n!="combined"]; pos=("scan %d of %d" % (order.index(node)+1, len(order))) if node in order else ""
            sub=("built from the scans you lined up" if node=="combined" else ((pos+" · " if pos else "")+"id "+node))   # show the real scanner id for reference, not the confusing raw/built wording
            ctk.CTkLabel(hdr, text=sub, text_color=MUT, font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", padx=12)
            meta=self._scan_meta(name, node)   # scanner scan-settings + counts (from the device's property.rvproj)
            if meta:
                line1=" / ".join(meta["modes"]) if meta.get("modes") else ""
                cts=[]
                if meta.get("polys"): cts.append("%s tris" % _kfmt(meta["polys"]))
                if meta.get("points"): cts.append("%s pts" % _kfmt(meta["points"]))
                if meta.get("frames"): cts.append("%d frames" % meta["frames"])
                mtxt=" · ".join([p for p in (line1, "  ".join(cts)) if p])
                if mtxt: ctk.CTkLabel(hdr, text=mtxt, text_color=DIM, font=ctk.CTkFont(size=10), anchor="w", justify="left", wraplength=230).pack(fill="x", padx=12, pady=(1,0))
            if node!="combined":
                stw, stc = self.STAGE_WORDS[self._device_stage(local, node)]
                if stw: ctk.CTkLabel(hdr, text="Scanner: "+stw, text_color=stc, font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", padx=12, pady=(2,0))
                hasp=node in self._base_planes(name)
                pl=self._base_planes(name).get(node) or {}
                if hasp:   # the user has already run our Remove base on this scan: that's the strongest signal
                    btxt=("Marked: no base to cut ✓" if pl.get("skip") else "Base removed ✓ - reapplied when combining"); bcol=OK; btip=None
                else:      # ask the geometry whether the table is still on (the scanner may have cut it already)
                    cpath=cur[2] if cur else None
                    e=self._base_verdict(cpath) if cpath else None
                    if cpath and e is None: self._want_base(cpath)   # compute once in the background, then refresh
                    present=e.get("present") if e is not None else "pending"
                    if present=="pending":
                        btxt="Base: checking the model…"; bcol=MUT; btip="Looking at the geometry to see if the table/turntable is still attached."
                    elif present is True:
                        btxt="Table still attached - use Remove base"; bcol=WARN; btip="A large flat plane sits under the part, so the table looks like it's still in the scan."
                    elif present is False:
                        btxt="No obvious table detected"; bcol=MUT; btip="No large flat base plane found, so the scanner most likely trimmed the table already. This is a guess from the geometry, not proof - Remove base is still there if a table is left."
                    else:      # couldn't tell (tiny model / load failed)
                        btxt="Couldn't check for a base"; bcol=MUT; btip="Not enough geometry to tell whether a table is attached. Use Remove base if the table is still on the part."
                bl=ctk.CTkLabel(hdr, text=btxt, text_color=bcol, font=ctk.CTkFont(size=11), anchor="w"); bl.pack(fill="x", padx=12)
                if btip: self._tip(bl, btip)
            ctk.CTkFrame(hdr, fg_color="transparent", height=4).pack()
            if vs:
                # One model, shown plainly - the scan rides on ONE identity so the preview, points and
                # export never feel like different objects. Extra versions hide behind "Other versions".
                def _sz(pth):
                    try: return "  ·  "+human(os.path.getsize(pth))
                    except Exception: return ""
                _vtip=("A version is just another saved copy of this same scan. The scanner's original is what came "
                       "off the device; every time you Prepare, Remove base or edit, PointYoink saves a NEW copy and "
                       "leaves the original untouched. This line shows which copy the preview, points and export use.")
                if cur:
                    ck,clabel,cpath=cur
                    mchip=ctk.CTkFrame(pp, fg_color="#15304d", corner_radius=9); mchip.pack(fill="x", padx=6, pady=(6,2))
                    ml=ctk.CTkLabel(mchip, text="Showing:  "+clabel, height=26, anchor="w", text_color=AC, font=ctk.CTkFont(size=12, weight="bold"))
                    ml.pack(side="left", fill="x", expand=True, padx=(10,0), pady=3)
                    self._tip(ml, _vtip+"\n\nFile: "+os.path.basename(cpath)+_sz(cpath))
                    info=ctk.CTkLabel(mchip, text="ⓘ", width=20, text_color=AC, font=ctk.CTkFont(size=12)); info.pack(side="right", padx=(0,8))
                    self._tip(info, _vtip)
                others=[(k,l,p) for (k,l,p) in vs if not (cur and k==cur[0])]
                if others:
                    alt_open=node in getattr(self, "_alt_ver_open", set())
                    tgl=ctk.CTkButton(pp, text=("Hide other copies ▴" if alt_open else "Other copies you've made (%d) ▾" % len(others)),
                                      height=22, corner_radius=8, fg_color="transparent", hover_color=CARD2, border_width=0,
                                      text_color=MUT, font=ctk.CTkFont(size=11), anchor="w",
                                      command=lambda nd=node: self._toggle_alt_versions(nd)); tgl.pack(fill="x", padx=6, pady=(0,2))
                    self._tip(tgl, "Other saved copies of this same scan (prepared, PC build, edited…). Switch only if you want a different one for export.")
                    if alt_open:
                        for key,label,path in others:
                            chip=ctk.CTkFrame(pp, fg_color=CARD2, corner_radius=9); chip.pack(fill="x", padx=6, pady=2)
                            b=ctk.CTkButton(chip, text=label+_sz(path), height=24, corner_radius=9, fg_color="transparent", hover_color=STROKE, anchor="w",
                                            text_color=TX, font=ctk.CTkFont(size=11), command=lambda n=name,nd=node,k=key: self._pick_version(n, nd, k)); b.pack(side="left", fill="x", expand=True, padx=(6,0))
                            self._tip(b, "Make this the model this scan uses. Nothing is changed or deleted.\n"+os.path.basename(path))
                            x=ctk.CTkButton(chip, text="✕", width=24, height=24, corner_radius=9, fg_color="transparent", hover_color="#3a2530", text_color=MUT, font=ctk.CTkFont(size=11),
                                            command=lambda n=name,nd=node,k=key,pth=path: self._proc_delete_version(n, nd, k, pth)); x.pack(side="right", padx=(0,5))
                            self._tip(x, "Delete this version (asks first; it goes to the trash)")
            else: ctk.CTkLabel(pp, text="No 3D model yet", text_color=WARN, font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", padx=6, pady=(6,0))
            combined_exists=("combined" in nodes and node!="combined")
            # Cut base stays the primary step until it's actually cut or the user skips it - the same as the
            # NEXT bar, which (when no table is detected) leads with a one-click 'skip base cut'. Detection is
            # advisory here too: it never silently drops the step, so the sidebar and NEXT never disagree.
            _need_cut=(vs and node!="combined" and node not in self._base_planes(name))
            primary="build" if (raw and not vs) else ("cut" if _need_cut else (None if combined_exists else ("prepare" if (vs and not has_prep) else ("export" if vs else None))))
            _mkicon={"build":"build","cut":"cut-base","prepare":"prepare","export":"export"}
            def mk(kind, text, enabled, cmd, tip):
                if not enabled: return None   # only show what this scan can actually do: a raw scan (no model) shows Build, not greyed Remove base / Prepare / Export
                # NEXT bar is the ONE filled primary CTA; the sidebar's matching action gets a subtle accent
                # (accent border + text), not a second full-fill button competing for attention.
                accent=(kind==primary)
                b=ctk.CTkButton(pp, text=text, image=_icon(_mkicon.get(kind,kind), "accent" if accent else "default"), compound="left",
                                height=32, corner_radius=8, fg_color="transparent", hover_color=CARD2,
                                border_width=1, border_color=(AC if accent else STROKE),
                                text_color=(AC if accent else TX), state="normal", anchor="w", command=cmd)
                b.pack(fill="x", padx=6, pady=(6,0)); self._tip(b, tip); return b
            if node!="combined": mk("build", "  Build model", bool(raw), lambda n=name,nd=node: self._proc_build(n, [nd]), "Build this scan's 3D model from its raw data, on this PC." if raw else "No raw data on this PC for this scan (share the project over WiFi as Full project).")
            if node!="combined": mk("cut", "  Remove base…", bool(vs), lambda nd=node: self.on_remove_base(nd), "Drag one line just above the table and apply. Saves a prepared version and remembers the cut for combining.")
            aside="This project has a Combined model - usually you prepare and export that (the Combined tile). This still works on just this scan."
            mk("prepare", "  Prepare…", bool(vs), lambda n=name,nd=node: self._prepare_dialog(n, nd), aside if combined_exists else "Remove floating pieces, smooth, fill holes, reduce triangles. Before and after, then keep or discard.")
            mk("export", "  Export…", bool(vs), lambda n=name,nd=node: self._export_dialog(n, nd), aside if combined_exists else "Save as STL, OBJ, GLB or PLY with a size and mesh check.")
            if combined_exists and node!="combined": ctk.CTkLabel(pp, text="Combined recommended - but you can still prepare/export this scan.", text_color=DIM, font=ctk.CTkFont(size=10), anchor="w", justify="left", wraplength=230).pack(fill="x", padx=6, pady=(4,0))
        self._hr(pp, pady=(14,6)); self._title(pp, "Whole project", size=13)
        def act(text, cmd, tip=None, danger=False, icon=None):
            b=ctk.CTkButton(pp, text=text, image=(_icon(icon, "danger" if danger else "default") if icon else None), compound="left",
                            height=30, corner_radius=8, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=("#3a2530" if danger else CARD2), text_color=(MUT if danger else TX), anchor="w", command=cmd)
            b.pack(fill="x", padx=6, pady=3)
            if tip: self._tip(b, tip)
        act("  Compare versions…", lambda: self._compare_dialog(name), "Two 3D views side by side, any scan or version in each, turning together.", icon="compare")
        act("  Combine scans…", lambda: self._align_dialog(name), "Scanned each side separately? Line the scans up and build one model from all of them.", icon="combine")
        act("  Build all models", self.on_process_pc, "Build the 3D model of every scan that has raw data.", icon="build")
        # "All scans as cards…" removed: it was a near-empty duplicate of this page (hero +
        # filmstrip + these same actions already live here). Build detail lives in Settings.
        act("  Delete project from this PC", self._proc_delete_project, "Everything in its folder goes to the trash. The scanner copy is not touched.", danger=True, icon="delete")
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
        if not self._require_open3d("Building models"): return
        self._fusing=True
        try: self._btn_busy(self.proc_btn, "Working…")
        except Exception: pass
        for nd in nodes: self._proc_progress(nd, None, "Starting…")
        self.set_status("Building 3D model%s…" % ("" if len(nodes)==1 else "s"))
        dest=self.dest.get() or DEFAULT_DEST; voxel=float(self.fuse_voxel.get() or 0.4); fuse_device=self.cfg.get("fuse_device","auto"); register_drift=bool(self.cfg.get("register_drift", True)); self._fuse_name=name
        self._start_thread(self._fuse_worker, name, nodes, dest, voxel, fuse_device, register_drift, name="fuse")
    def _device_stage(self, local, node):
        """How far the scanner itself took this scan: 'meshed' (One-tap Edit or Mesh was run there), 'fused'
        (point cloud only), 'raw' (frames only), or None (nothing on this PC for it)."""
        d=os.path.join(local, "data", node)
        if os.path.exists(os.path.join(d, "fuse_mesh.ply")) or os.path.exists(os.path.join(local, "%s_%s.ply" % (os.path.basename(local), node))): return "meshed"
        if os.path.exists(os.path.join(d, "fuse.ply")) or os.path.exists(os.path.join(local, "%s_%s_cloud.ply" % (os.path.basename(local), node))): return "fused"
        if self._has_raw_frames(local, node): return "raw"
        return None
    STAGE_WORDS={"meshed": ("One-tap edited ✓", OK), "fused": ("fused, not meshed", WARN), "raw": ("raw only, not edited", WARN), None: ("", MUT)}
    def _device_scan_names(self, name, root=None):
        """Names given to scans on the scanner: the project's .revo lists each scan with a name (equal to its id unless
        it was renamed on the device). {id: name} for the renamed ones."""
        root=root or os.path.join(self.dest.get() or DEFAULT_DEST, name)
        cache=getattr(self, "_dev_names", {}); key=(root, name)
        try:
            revo=os.path.join(root, name+".revo"); mt=os.path.getmtime(revo)
            if key in cache and cache[key][0]==mt: return cache[key][1]
            out={}
            for nd in (json.load(open(revo)).get("nodes") or []):
                g=str(nd.get("guid") or ""); nm=str(nd.get("name") or "").strip()
                if g and nm and nm!=g: out[g]=nm
            cache[key]=(mt, out); self._dev_names=cache; return out
        except Exception: return {}
    def _scan_label(self, name, node):
        if node=="combined": return "Combined"
        custom=(self.records.get(name, {}).get("scan_labels", {}) or {}).get(node)
        if custom: return custom
        dev=self._device_scan_names(name).get(node)
        if dev: return dev
        return node                                                    # no custom or device name: its own id, same as projects
    def _rename_scan(self, name, node):
        """Give a scan a name like front, back, left side. Shown on the strip, the panel, the cards and in Combine."""
        cur=(self.records.get(name, {}).get("scan_labels", {}) or {}).get(node, "")
        t=self._top("Name this scan", 420, 190, key="scanname")
        if t is None: return
        ctk.CTkLabel(t, text="A name for this scan (front, back, left side…). Its id (%s) stays as the file name. Leave empty to go back to the id." % node,
                     text_color=MUT, font=ctk.CTkFont(size=12), wraplength=380, justify="left").pack(anchor="w", padx=20, pady=(18,6))
        v=ctk.StringVar(value=cur); e=ctk.CTkEntry(t, textvariable=v, fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=10); e.pack(fill="x", padx=20); e.focus_set()
        def ok(*_):
            labs=self.records.setdefault(name, {}).setdefault("scan_labels", {}); txt=v.get().strip()
            if txt: labs[node]=txt
            else: labs.pop(node, None)
            self._persist(); self._dialogs.pop("scanname", None); t.destroy()
            self.gallery_cache.pop(name, None); self.projects_sig=None
            if self.selected==name: self.select_project(name); self._pick_scan_by_node(name, node)
        e.bind("<Return>", ok)
        br=ctk.CTkFrame(t, fg_color="transparent"); br.pack(fill="x", padx=16, pady=14)
        ctk.CTkButton(br, text="Save", width=100, height=32, corner_radius=16, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=ok).pack(side="right", padx=6)
        ctk.CTkButton(br, text="Cancel", width=90, height=32, corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=lambda: (self._dialogs.pop("scanname", None), t.destroy())).pack(side="right", padx=6)
    def _mesh_info(self, path, cb):
        """Size, counts, pieces and open edges of a model, measured in a memory-capped child; cb(dict or None) on the UI thread."""
        def work():
            info=None
            try:
                env=dict(os.environ); env.setdefault("POINTYOINK_MEM_CAP_GB", "10")
                r=self._run_child([_sys.executable, os.path.join(HERE, "process.py"), path, "--info"], timeout=600, env=env)
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
    # ---- prepared-version history: each Save keeps the previous prepared copy so it can be restored ----
    def _prep_history(self, name, node):
        return self.records.get(name,{}).get("prep_history",{}).get(node,[])
    def _prep_record(self, name, node, opts, prev_archive, size):
        hist=self.records.setdefault(name,{}).setdefault("prep_history",{}).setdefault(node,[])
        if not hist and prev_archive:      # a prepared copy existed before history tracking: seed it as version 1
            hist.append({"when":"earlier","settings":{},"current":False,"archive":prev_archive,"size":None}); prev_archive=None
        for h in hist:
            if h.get("current"): h["current"]=False; h["archive"]=prev_archive
        hist.append({"when":time.strftime("%Y-%m-%d %H:%M"),"settings":opts or {},"current":True,"archive":None,"size":size})
    def _prep_restore(self, name, node, idx):
        hist=self._prep_history(name, node)
        if idx<0 or idx>=len(hist) or hist[idx].get("current"): return
        entry=hist[idx]; arch=entry.get("archive")
        if not arch or not os.path.exists(arch): self.set_banner("That version's file is no longer on disk.", WARN); return
        final=os.path.join(self.dest.get() or DEFAULT_DEST, name, "%s_%s_clean.ply" % (name, node))
        try:
            vdir=os.path.join(os.path.dirname(final), ".versions"); os.makedirs(vdir, exist_ok=True)
            cur_arch=None
            if os.path.exists(final):
                cur_arch=os.path.join(vdir, "%s_clean_%s.ply" % (node, time.strftime("%Y%m%d-%H%M%S"))); shutil.copy2(final, cur_arch)
            shutil.copy2(arch, final); os.utime(final, None)   # copy2 keeps the archive's OLD mtime; bump it to now, or the freshness-by-mtime preview/mesh cache serves the PREVIOUS version and Restore shows the wrong model
        except Exception as e: log_error("prep restore", e); self.set_banner("Could not restore that version (see Help > Log).", WARN); return
        for h in hist:
            if h.get("current"): h["current"]=False; h["archive"]=cur_arch
        entry["current"]=True; entry["archive"]=None
        self._persist(); self._mesh_stats={}; self.gallery_cache.pop(name, None)
        self._proc_set_current(name, node, "clean")
        self.set_banner("Restored a previous prepared version of %s." % self._scan_label(name, node), OK)
    def _prep_history_dialog(self, name, node):
        hist=self._prep_history(name, node)
        if not hist:
            self._alert("Past versions", "No saved prepared versions yet for %s.\nPrepare it and Save prepared version to start a history." % self._scan_label(name, node)); return
        t=self._top("Past versions · %s" % self._scan_label(name, node), 560, min(720, 170+66*len(hist)), key="prephist")
        if t is None: return
        ctk.CTkLabel(t, text="Prepared versions of %s" % self._scan_label(name, node), font=ctk.CTkFont(size=15, weight="bold"), text_color=TX).pack(anchor="w", padx=20, pady=(16,2))
        ctk.CTkLabel(t, text="Every Save prepared version keeps the previous one here. Restore swaps it back in (the current one is kept too).", text_color=MUT, font=ctk.CTkFont(size=11), wraplength=500, justify="left").pack(anchor="w", padx=20, pady=(0,8))
        box=ctk.CTkScrollableFrame(t, fg_color=CARD, corner_radius=12); box.pack(fill="both", expand=True, padx=16, pady=(0,12))
        def summ(s):
            s=s or {}; b=[]
            if s.get("do_iso"): b.append("isolate %s%%" % s.get("iso"))
            if s.get("do_base"): b.append("remove base")
            if s.get("do_smooth"): b.append("smooth %s" % s.get("smooth"))
            if s.get("holes"): b.append("fill holes")
            if s.get("do_keep") and (s.get("keep") or 100)<100: b.append("keep %s%% tris" % s.get("keep"))
            return ", ".join(b) or ("earlier prepared copy" if not s else "no changes")
        for i in range(len(hist)-1, -1, -1):
            h=hist[i]; cur=h.get("current")
            row=ctk.CTkFrame(box, fg_color=CARD2, corner_radius=10); row.pack(fill="x", padx=6, pady=4)
            col=ctk.CTkFrame(row, fg_color="transparent"); col.pack(side="left", fill="x", expand=True, padx=12, pady=8)
            ctk.CTkLabel(col, text="Version %d%s · %s" % (i+1, "  (current)" if cur else "", h.get("when","")), text_color=(OK if cur else TX), font=ctk.CTkFont(size=12, weight="bold"), anchor="w").pack(anchor="w")
            ctk.CTkLabel(col, text="%s%s" % (summ(h.get("settings")), (" · "+human(h["size"])) if h.get("size") else ""), text_color=MUT, font=ctk.CTkFont(size=11), anchor="w", justify="left", wraplength=360).pack(anchor="w")
            if cur:
                ctk.CTkLabel(row, text="in use", text_color=OK, font=ctk.CTkFont(size=11)).pack(side="right", padx=14)
            else:
                avail=bool(h.get("archive") and os.path.exists(h.get("archive")))
                ctk.CTkButton(row, text="Restore" if avail else "file gone", width=94, height=28, corner_radius=14, fg_color=(AC if avail else CARD2), hover_color=AC_H,
                              text_color=("#04121f" if avail else DIM), state=("normal" if avail else "disabled"),
                              command=(lambda idx=i: (self._dialogs.pop("prephist", None), t.destroy(), self._prep_restore(name, node, idx)))).pack(side="right", padx=14)
    def _prepare_dialog(self, name, node):
        """The four named clean-up actions, run on a copy, shown before and after, then Keep or Discard."""
        cur=self._proc_current(name, node)
        if not cur: return
        self._ensure_clean_vars()
        t=self._top("Prepare · %s" % self._scan_label(name, node), 960, 780, key="prepare")
        if t is None: return
        t.resizable(False, False)     # lock it: long option text / the After render must not make the window jump sizes
        src=cur[2]; final=os.path.join(os.path.dirname(src), "%s_%s_clean.ply" % (name, node)); tmp=final[:-4]+".tmp.ply"
        pstate={"opts": None}     # the settings that produced the current tmp, recorded into history on Save
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(card, text="Starting from the version “%s” · %s" % (cur[1], human(os.path.getsize(src))), text_color=MUT, font=ctk.CTkFont(size=12)).pack(anchor="w", padx=18, pady=(14,6))
        opts=ctk.CTkFrame(card, fg_color="transparent"); opts.pack(fill="x", padx=12)
        def row(var, title, before, entry, after, tip):
            r=ctk.CTkFrame(opts, fg_color=CARD2, corner_radius=10); r.pack(fill="x", padx=4, pady=3)
            cbx=ctk.CTkCheckBox(r, text=title, variable=var, width=24, checkbox_width=18, checkbox_height=18, corner_radius=5, border_color=STROKE, fg_color=AC, hover_color=AC,
                                text_color=TX, font=ctk.CTkFont(size=12, weight="bold")); cbx.pack(side="left", padx=(12,10), pady=7)
            self._tip(cbx, tip)
            ctk.CTkLabel(r, text=before, text_color=MUT, font=ctk.CTkFont(size=11), wraplength=720, justify="left").pack(side="left", fill="x", expand=(entry is None))
            if entry is not None:
                e=ctk.CTkEntry(r, textvariable=entry, width=46, height=24, corner_radius=6, fg_color="#0d0f14", border_color=STROKE, text_color=TX, justify="center"); e.pack(side="left", padx=6)
                ctk.CTkLabel(r, text=after, text_color=MUT, font=ctk.CTkFont(size=11)).pack(side="left")
        row(self.clean_do_iso, "Remove floating pieces", "drop pieces smaller than", self.clean_iso, "% of the biggest one",
            "Loose bits that are not part of the object. The scanner's Isolation rate; its default is 15%.")
        row(self.clean_do_base, "Remove base", "cuts off the biggest flat surface (table/turntable); skips itself if it would take a big chunk of the part", None, "",
            "Automatic, off by default. For a cut you place by hand, use Remove base on the scan instead.")
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
        show(0, src, "Loading…"); loads[1].configure(text="Press Preview changes to see the result here"); loads[1].grid(); loads[1].lift()
        status=ctk.CTkLabel(card, text="Tick what to do, then Preview changes. Nothing is saved until you press Save prepared version.", text_color=MUT, font=ctk.CTkFont(size=12)); status.pack(anchor="w", padx=18, pady=(6,0))
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
            prev_archive=None
            try:
                if os.path.exists(final):
                    # keep the copy we're about to overwrite, so a re-Prepare doesn't silently lose it.
                    # This MUST succeed before we overwrite: the UI promises earlier versions are preserved,
                    # so if the backup fails we abort rather than destroy the previous version.
                    vdir=os.path.join(os.path.dirname(final), ".versions"); os.makedirs(vdir, exist_ok=True)
                    prev_archive=os.path.join(vdir, "%s_clean_%s.ply" % (node, time.strftime("%Y%m%d-%H%M%S")))
                    shutil.copy2(final, prev_archive)
                os.replace(tmp, final)
            except Exception as e:
                try:                                          # don't leave a stray/partial backup behind
                    if prev_archive and os.path.exists(prev_archive): os.remove(prev_archive)
                except Exception: pass
                log_error("prepare keep", e)
                status.configure(text="Couldn't back up the current version - nothing was overwritten, your prepared version is safe (see Help > Log).", text_color=WARN); return
            self._prep_record(name, node, pstate["opts"], prev_archive, os.path.getsize(final))
            self._persist(); self._mesh_stats={}; self.gallery_cache.pop(name, None); self.projects_sig=None
            n=len(self._prep_history(name, node))
            self.set_banner("%s: saved prepared version %d. Earlier versions kept (Past versions… to restore)." % (self._scan_label(name, node), n), OK)
            self._proc_set_current(name, node, "clean"); close()
        def discard(): close(); self.set_banner("Discarded. Nothing was changed.", MUT)
        def run():
            if not (self.clean_do_iso.get() or self.clean_do_smooth.get() or self.clean_holes.get() or self.clean_do_keep.get() or self.clean_do_base.get()):
                status.configure(text="Tick at least one action.", text_color=WARN); return
            clean_opts=self._clean_options(); pstate["opts"]=clean_opts; self._persist(); able(runb, False); keepb.pack_forget(); discb.pack_forget()
            loads[1].configure(text="Working…"); loads[1].grid(); loads[1].lift(); t0=time.time()
            status.configure(text="Working on a copy… (a big model takes a minute)", text_color=MUT)
            def tick():
                if t.winfo_exists() and runb.cget("state")=="disabled": status.configure(text="Working on a copy… %ds (a big model takes a minute)" % int(time.time()-t0)); t.after(1000, tick)
            t.after(1000, tick)
            def work():
                ok=self._clean_subprocess(src, tmp, clean_opts)
                def done():
                    if not t.winfo_exists(): return
                    able(runb, True)
                    if ok:
                        st=None
                        try: st=os.path.getsize(tmp)
                        except Exception: pass
                        warn=getattr(self, "_last_clean_warnings", None)
                        if warn:
                            status.configure(text="Done, but %s didn't run (see Help > Log). %s → %s. Compare, then Save or Discard." % (" and ".join(warn), human(os.path.getsize(src)), human(st or 0)), text_color=WARN)
                        else:
                            status.configure(text="Done: %s → %s. Turn the views to compare, then Save prepared version or Discard." % (human(os.path.getsize(src)), human(st or 0)), text_color=TX)
                        show(1, tmp, "Loading the result…"); keepb.pack(side="right", padx=6); discb.pack(side="right", padx=6)
                    else: status.configure(text="Could not prepare this scan (see Help > Log).", text_color=WARN); loads[1].configure(text="No result")
                self.q.put(("call", done))
            threading.Thread(target=work, daemon=True).start()
        runb=ctk.CTkButton(btns, text="Preview changes", width=150, height=34, corner_radius=17, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=run); runb.pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Close", width=90, height=34, corner_radius=17, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=close).pack(side="left", padx=6)
        if self._prep_history(name, node):
            ctk.CTkButton(btns, text="⤺ Past versions…", width=150, height=34, corner_radius=17, fg_color="transparent", border_width=1, border_color=STROKE, hover_color=CARD2, text_color=TX,
                          command=lambda: self._prep_history_dialog(name, node)).pack(side="left", padx=6)
        keepb=ctk.CTkButton(btns, text="Save prepared version", width=190, height=34, corner_radius=17, fg_color=OK, hover_color="#35b57c", text_color="#04121f", command=keep)
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
            for key,label,path in self._proc_versions(name, nd): choices.append(["%s · %s" % (self._scan_label(name, nd), label), path, nd])
        if len(choices)<2: self._alert("Compare", "Nothing to compare yet: this project has fewer than two model versions."); return
        # two scans can share a label (auto index clash or a duplicate custom name); without this, the
        # option strings collide, dict(choices) keeps one path, and picking the other loads the wrong model.
        # Append the node id to any colliding entry so every dropdown row maps to exactly one model.
        _seen={}
        for c in choices: _seen[c[0]]=_seen.get(c[0],0)+1
        for c in choices:
            if _seen[c[0]]>1: c[0]="%s  (%s)" % (c[0], c[2])
        t=self._top("Compare · %s" % self.disp(name), 1180, 760, key="compare")
        if t is None: return
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=12, pady=12)
        card.grid_columnconfigure((0,1), weight=1); card.grid_rowconfigure(1, weight=1)
        menu=dict(fg_color="#0d0f14", button_color=CARD2, button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=8)
        lookup={c[0]: c[1] for c in choices}; labels=[c[0] for c in choices]
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
        ctk.CTkLabel(card, text="Two versions side by side, turning together: put the scanner's model on one side and the PC build on the other to judge them at the same angle. Scroll to zoom, right-drag to pan, double-click to reset.", text_color=DIM, font=ctk.CTkFont(size=10), wraplength=1100).grid(row=2,column=0, columnspan=2, sticky="w", padx=14, pady=(0,10))
        load(0); load(1)
    def _export_dialog(self, name, node):
        """Version, format and destination together, with the model's size and a mesh check."""
        vs=self._proc_versions(name, node)
        if not vs: return
        cur=self._proc_current(name, node) or vs[0]
        _home=os.path.expanduser("~")
        def _tilde(p): return ("~"+p[len(_home):]) if p and (p==_home or p.startswith(_home+os.sep)) else p   # show ~/... not /home/<user>/... (dir boundary, so /home/rick_x isn't matched)
        t=self._top("Export · %s" % self._scan_label(name, node), 640, 360, key="export")
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
        dv=ctk.StringVar(value=_tilde(self.cfg.get("export_dir") or os.path.join(self.dest.get() or DEFAULT_DEST, "exports")))
        r=line("Save to"); ctk.CTkEntry(r, textvariable=dv, height=28, corner_radius=6, fg_color="#0d0f14", border_color=STROKE, text_color=TX).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(r, text="Browse", width=70, height=28, corner_radius=6, fg_color=CARD2, hover_color=STROKE, text_color=TX,
                      command=lambda: dv.set(_tilde(filedialog.askdirectory(initialdir=os.path.expanduser(dv.get()) or HOME) or os.path.expanduser(dv.get())))).pack(side="left", padx=6)
        base=ctk.StringVar(value="%s_%s" % (self.disp(name).replace(" ","_"), self._scan_label(name, node).replace(" ","")))
        r=line("File name"); ctk.CTkEntry(r, textvariable=base, height=28, corner_radius=6, fg_color="#0d0f14", border_color=STROKE, text_color=TX).pack(side="left", fill="x", expand=True)
        info=ctk.CTkLabel(card, text="Measuring the model…", justify="left", anchor="w", text_color=TX, font=ctk.CTkFont(size=12), wraplength=560); info.pack(fill="x", padx=18, pady=(12,2))
        ctk.CTkLabel(card, text="Open edges are gaps in the surface. Separate pieces are disconnected chunks (loose bits, or sides scanned apart) - not the same thing, and not always a problem. Slicers bridge small gaps; big ones need Prepare or a mesh editor. An STL is not a promise that it prints.",
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
            self._btn_busy(gob, "Writing…"); status.configure(text="Writing %s…" % os.path.basename(out), text_color=MUT)
            def work():
                err=None
                try:
                    os.makedirs(ddir, exist_ok=True)
                    if fmt=="ply": shutil.copyfile(src, out)
                    elif not self._convert_subprocess(src, out):   # capped child: a huge model can't take the app down
                        raise RuntimeError("could not convert to %s - see Help > Log" % fmt.upper())
                except Exception as e: err=e; log_error("export", e)
                def done():
                    if not t.winfo_exists(): return
                    self._btn_idle(gob)
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
        t=self._top("Combine scans · %s" % self.disp(name), 1180, 1000, key="align")
        if t is None: return
        rec=self.records.setdefault(name,{}).setdefault("align",{})
        st={"base": rec.get("_base") if rec.get("_base") in nodes else nodes[0], "moving": None, "pairs": [], "pending": None, "result": None, "busy": False}
        st["moving"]=next((n for n in nodes if n!=st["base"]), None)
        lab=lambda n: self._scan_label(name, n)
        # two scans can share a label (auto index clash or a duplicate custom name). The dropdowns key on
        # the label, so without this a duplicate makes both rows resolve to the FIRST matching scan and the
        # other can't be picked. Append the node id to any colliding label so each row maps to one scan.
        _lc={}
        for n in nodes: _lc[lab(n)]=_lc.get(lab(n),0)+1
        dlab=lambda n: ("%s  (%s)" % (lab(n), n)) if _lc.get(lab(n),0)>1 else lab(n)
        node_by_dlab={dlab(n): n for n in nodes}
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=12, pady=12)
        card.grid_columnconfigure((0,1), weight=1); card.grid_rowconfigure(2, weight=1); card.grid_rowconfigure(3, weight=1)
        menu=dict(fg_color="#0d0f14", button_color=CARD2, button_hover_color=STROKE, dropdown_fg_color=CARD2, text_color=TX, corner_radius=8)
        bar=ctk.CTkFrame(card, fg_color="transparent"); bar.grid(row=0,column=0, columnspan=2, sticky="ew", padx=14, pady=(12,4))
        ctk.CTkLabel(bar, text="Base scan", text_color=MUT, font=ctk.CTkFont(size=12)).pack(side="left")
        bsel=ctk.StringVar(value=dlab(st["base"])); msel=ctk.StringVar(value=dlab(st["moving"]) if st["moving"] else "")
        bmenu=ctk.CTkOptionMenu(bar, values=[dlab(n) for n in nodes], variable=bsel, width=130, command=lambda _: pick_base(), **menu); bmenu.pack(side="left", padx=(8,18))
        ctk.CTkLabel(bar, text="Scan to line up", text_color=MUT, font=ctk.CTkFont(size=12)).pack(side="left")
        mmenu=ctk.CTkOptionMenu(bar, values=[dlab(n) for n in nodes if n!=st["base"]], variable=msel, width=130, command=lambda _: pick_moving(), **menu); mmenu.pack(side="left", padx=(8,18))
        chips=ctk.CTkLabel(bar, text="", text_color=OK, font=ctk.CTkFont(size=12)); chips.pack(side="left", padx=6)
        hint=ctk.CTkLabel(card, text="", text_color=MUT, font=ctk.CTkFont(size=12), justify="left", wraplength=1100, anchor="w"); hint.grid(row=1,column=0, columnspan=2, sticky="ew", padx=16, pady=(0,6))
        # two pick views on top (base | moving), the merged result wide below (the user's chosen layout)
        frames=[ctk.CTkFrame(card, fg_color="#0a0c10", corner_radius=10) for _ in range(3)]
        frames[0].grid(row=2,column=0, sticky="nsew", padx=(14,4), pady=4); frames[1].grid(row=2,column=1, sticky="nsew", padx=(4,14), pady=4)
        frames[2].grid(row=3,column=0, columnspan=2, sticky="nsew", padx=14, pady=4)
        caps=[ctk.CTkLabel(f, text="", text_color=MUT, font=ctk.CTkFont(size=11)) for f in frames]
        views=[self._new_view(f) for f in frames]
        loads=[ctk.CTkLabel(f, text="Loading the 3D view…", text_color=MUT, font=ctk.CTkFont(size=14), fg_color="#0a0c10") for f in frames]
        for f,c,v,l in zip(frames, caps, views, loads):
            f.grid_columnconfigure(0, weight=1); f.grid_rowconfigure(1, weight=1); c.grid(row=0,column=0, sticky="w", padx=10, pady=(6,0)); v.grid(row=1,column=0, sticky="nsew", padx=6, pady=6)
            l.grid(row=1,column=0, sticky="nsew", padx=6, pady=6); l.lift()
        can_pick=all(hasattr(v, "pick") for v in views[:2])   # only the two top views take point clicks; the merge view just shows the result
        def able(b, on, fill=AC):
            """Buttons read as off when off: grey with dim text, instead of a bright button with invisible text."""
            b.configure(state=("normal" if on else "disabled"), fg_color=(fill if on else CARD2), text_color=("#04121f" if on else DIM), text_color_disabled=DIM,
                        hover_color=(AC_H if fill==AC else "#35b57c") if on else CARD2)
        status=ctk.CTkLabel(card, text="", text_color=TX, font=ctk.CTkFont(size=12), anchor="w", justify="left", wraplength=1100); status.grid(row=4,column=0, columnspan=2, sticky="ew", padx=16, pady=(6,0))
        btns=ctk.CTkFrame(card, fg_color="transparent"); btns.grid(row=5,column=0, columnspan=2, sticky="ew", padx=12, pady=(6,12))
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
            caps[2].configure(text="Alignment · %s (grey) + %s (orange) as you line them up" % (lab(st["base"]), lab(st["moving"]) if st["moving"] else "…"))
            saved=rec.get(st["moving"]) if st["moving"] else None
            saved=saved if (isinstance(saved, dict) and saved.get("base")==st["base"]) else None
            if saved: st["pairs"]=[list(pr) for pr in saved.get("pairs", [])]
            def restore(i):
                """Put a kept alignment back on screen: the pair dots on the two pick views, the orange overlay on the merged view."""
                if getattr(views[i], "tf", None) is None: return
                import shade
                if i in (0,1) and can_pick and saved:
                    pts=[pr[0] for pr in st["pairs"]] if i==0 else [pr[1] for pr in st["pairs"]]
                    views[i].markers=[(shade.world_to_view(pt, views[i].tf), self.PAIR_COLOURS[k % len(self.PAIR_COLOURS)]) for k,pt in enumerate(pts)]
                    views[i].draw()
                if i==2 and saved and hasattr(views[2], "add_layer") and st["moving"]:
                    views[2].clear_layers(draw=False); views[2].add_layer(self._proc_current(name, st["moving"])[2], saved["matrix"], colour=(1.0,0.55,0.25))
                    caps[2].configure(text="Alignment · %s (grey) with %s kept (orange)" % (lab(st["base"]), lab(st["moving"])))
            def shown(i):
                def cb(ok):
                    if not t.winfo_exists(): return
                    loads[i].grid_remove()
                    if not ok: caps[i].configure(text=caps[i].cget("text")+"  (could not load)", text_color=WARN)
                    else: restore(i)
                return cb
            for i,l in enumerate(loads): l.configure(text="Loading the 3D view…"); l.grid(); l.lift()
            views[0].load(self._proc_current(name, st["base"])[2], shown(0), max_faces=600000)     # lighter copies: they appear in seconds and picking stays accurate
            views[2].load(self._proc_current(name, st["base"])[2], shown(2), max_faces=600000)     # merged view starts as the base (grey); the moving scan drops in as an orange layer once lined up
            if st["moving"]: views[1].load(self._proc_current(name, st["moving"])[2], shown(1), max_faces=600000)
            else: loads[1].grid_remove()
            keepb.pack_forget()
            if saved:
                fit=saved.get("fitness"); n=len(st["pairs"])
                status.configure(text="%s was lined up %s%s%s. Add or undo points and press Line up from points again, or press Start over." % (
                    lab(st["moving"]), saved.get("when","before"), (" with %d point pair%s" % (n, "" if n==1 else "s")) if n else " by Auto", (", %.0f%% overlap" % (fit*100)) if fit else ""), text_color=TX)
            else: status.configure(text="")
            hint.configure(text=("Click a spot you can recognise on either scan, then the matching spot on the other. Three pairs are enough; five spread-out ones are better. Then press Line up from points. "
                                 "Or press Auto if the two scans overlap a lot.") if can_pick else
                                "Point picking needs the graphics-card 3D view (Settings). Auto still works when the scans overlap a lot.")
            pairs_lbl.configure(text="%d pair%s" % (len(st["pairs"]), "" if len(st["pairs"])==1 else "s")); able(alignb, len(st["pairs"])>=3)
        def pick_base():
            newb=node_by_dlab.get(bsel.get())
            if newb is None or newb==st["base"]: return
            others=[n for n in nodes if n in rec and isinstance(rec[n], dict) and rec[n].get("base")!=newb]
            if others and not self._confirm("Change the base scan?", "Scans already lined up were lined up to %s. Changing the base drops those." % lab(st["base"])): bsel.set(dlab(st["base"])); return
            for n in others: rec.pop(n, None)
            st["base"]=newb; rec["_base"]=newb; self._persist()
            mmenu.configure(values=[dlab(n) for n in nodes if n!=newb]); st["moving"]=next((n for n in nodes if n!=newb), None); msel.set(dlab(st["moving"]) if st["moving"] else "")
            load_views(); refresh_chips()
        def pick_moving():
            st["moving"]=node_by_dlab.get(msel.get()); load_views()
        def on_pick(which, world, view):
            try: _on_pick(which, world, view)
            except Exception as e: log_error("align pick", e); status.configure(text="Could not place that point (see Help > Log).", text_color=WARN)
        def _on_pick(which, world, view):
            if st["busy"] or not can_pick: return
            i=len(st["pairs"]); col=self.PAIR_COLOURS[i % len(self.PAIR_COLOURS)]
            pend=st["pending"]          # None, or (side, world): one half placed, waiting for its match on the other scan
            if pend is not None and pend[0]==which:        # clicked the same scan again: just move that half
                views[which].markers.pop(); st["pending"]=(which, world); views[which].markers.append((view, col)); views[which].draw(); return
            if pend is None:                                # first half of a pair, on EITHER scan
                st["pending"]=(which, world); views[which].markers.append((view, col)); views[which].draw()
                other=st["moving"] if which==0 else st["base"]
                status.configure(text="Point %d placed. Now click the same spot on %s." % (i+1, lab(other))); return
            # second half, on the other scan: store the pair base-first no matter which was clicked first
            base_pt, mov_pt = (pend[1], world) if pend[0]==0 else (world, pend[1])
            st["pairs"].append([list(map(float, base_pt)), list(map(float, mov_pt))]); st["pending"]=None
            views[which].markers.append((view, col)); views[which].draw()
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
            caps[2].configure(text="Alignment · %s (grey) + %s (orange) as you line them up" % (lab(st["base"]), lab(st["moving"]) if st["moving"] else "…"))
            pairs_lbl.configure(text="0 pairs"); able(alignb, False); keepb.pack_forget(); status.configure(text="Cleared. Click new points, or Auto.", text_color=MUT)
        def undo():
            pend=st["pending"]
            if pend is not None: st["pending"]=None; views[pend[0]].markers.pop(); views[pend[0]].draw()
            elif st["pairs"]: st["pairs"].pop(); views[0].markers.pop(); views[1].markers.pop(); views[0].draw(); views[1].draw()
            pairs_lbl.configure(text="%d pair%s" % (len(st["pairs"]), "" if len(st["pairs"])==1 else "s")); able(alignb, len(st["pairs"])>=3)
        busy={"t0":0.0, "msg":"", "job":None}
        def busy_text(m): busy["msg"]=m
        def busy_tick():
            if not busy["job"] or not t.winfo_exists(): return
            if busy["msg"] is not None: status.configure(text="Working: %s… %ds" % (busy["msg"] or "starting", int(time.time()-busy["t0"])), text_color=TX)
            busy["job"]=t.after(500, busy_tick)
        def busy_on(m):
            busy["t0"]=time.time(); busy["msg"]=m; bar.grid(row=6,column=0, columnspan=2, sticky="ew", padx=16, pady=(0,10)); bar.configure(mode="indeterminate"); bar.start()
            able(alignb, False); able(autob, False); able(comb, False, OK)
            busy["job"]=t.after(10, busy_tick)
        def busy_off():
            if busy["job"]:
                try: t.after_cancel(busy["job"])
                except Exception: pass
            busy["job"]=None
            if t.winfo_exists():
                bar.stop(); bar.grid_remove(); able(alignb, len(st["pairs"])>=3); able(autob, True); refresh_chips()
        def run_align(auto):
            if st["busy"] or not st["moving"]: return
            if auto and _has_open3d_cache is None:
                # First Auto click before the background Open3D check has landed: instead of doing nothing
                # (which made it "take two clicks"), start/await the check and run Auto as soon as it's ready.
                self._start_open3d_probe(); st["busy"]=True; busy_on("Checking Open3D…")
                def waito():
                    if not t.winfo_exists(): return
                    if _has_open3d_cache is None: t.after(200, waito); return
                    st["busy"]=False; busy_off(); run_align(True)
                t.after(200, waito); return
            if auto and not self._require_open3d("Auto alignment"): return
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
                    proc=self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env); tail=[]
                    for ln in proc.stdout:
                        ln=ln.strip(); tail=(tail+[ln])[-6:]
                        if ln.startswith("STAGE error "): err=ln[12:]
                        elif ln.startswith("STAGE "):
                            try: msg=json.loads(ln.split(" ",2)[2]).get("msg")
                            except Exception: msg=None
                            if msg: self.q.put(("call", lambda m=msg: busy_text(m)))
                    proc.wait(); self._forget_child(proc)
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
                    if hasattr(views[2], "add_layer"):
                        views[2].clear_layers(draw=False); views[2].add_layer(mov_p, res["matrix"], colour=(1.0,0.55,0.25))
                        caps[2].configure(text="Alignment · %s (grey) with %s lined up (orange)" % (lab(st["base"]), lab(st["moving"])))
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
        if not self._require_open3d("Building models"): return
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
            if plane and not plane.get("skip"):
                pj=os.path.join(local, "plane_%s.json" % node); json.dump(plane, open(pj, "w"))
            sets.append("%s,%s,%s,%s" % (cache, calib, tj, pj))
        ncut=sum(1 for sp in sets if sp.split(",")[3])
        out=os.path.join(local, "%s_combined_pcfused.ply" % name); voxel=float(self.fuse_voxel.get() or 0.4); fuse_device=self.cfg.get("fuse_device","auto"); self._fuse_name=name
        self._fusing=True; self.set_status("Building one model from %d scans%s…" % (len(sets), (", dropping the base of %d" % ncut) if ncut else ""))
        if ncut<len(sets): self.set_banner("%d of %d scans have no base cut saved: their table will be in the combined model. Remove base on each scan first for a clean result." % (len(sets)-ncut, len(sets)), WARN)
        def say(txt):
            self.q.put(("fuse_status", txt))
            if status is not None: self.q.put(("call", lambda: (status.configure(text=txt) if status.winfo_exists() else None)))
        def work():
            ok=False
            try:
                cmd=[_sys.executable, os.path.join(HERE,"fuse.py"), "--out", out, "--voxel", str(voxel)] + ([] if fuse_device=="cpu" else ["--gpu"])
                for sp in sets: cmd+=["--set", sp]
                proc=self._popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=dict(os.environ, OPENBLAS_NUM_THREADS="1"))
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
                proc.wait(); self._forget_child(proc)
            except Exception as e: log_error("combine", e)
            self.q.put(("fuse_done", name, ("ok", os.path.basename(out)) if (ok and os.path.exists(out)) else ("err", "the combined model could not be built - see the log")))
            if ok: say("Done: %s. It shows in the project as Combined." % os.path.basename(out))
        self._start_thread(work, name="combine")

    def on_process_pc(self):
        if getattr(self, "_fusing", False): return
        name=self.selected
        if not name: return
        if not self._require_open3d("Building models"): return
        self._fusing=True
        try: self._btn_busy(self.proc_btn, "Working…")
        except Exception: pass
        self.set_status("Building 3D models…")
        for nd in self._proc_nodes(name): self._proc_progress(nd, None, "Waiting…")
        dest=self.dest.get() or DEFAULT_DEST; voxel=float(self.fuse_voxel.get() or 0.4); fuse_device=self.cfg.get("fuse_device","auto"); register_drift=bool(self.cfg.get("register_drift", True)); self._fuse_name=name
        self._start_thread(self._fuse_worker, name, None, dest, voxel, fuse_device, register_drift, name="fuse")
    def _fuse_worker(self, name, only_nodes=None, dest=None, voxel=None, fuse_device="auto", register_drift=True):
        # thin guard: the impl emits fuse_done on every normal path, but an exception outside its inner
        # try (e.g. os.makedirs) used to skip that emit and leave _fusing=True (Build stuck "already running").
        try:
            self._fuse_worker_impl(name, only_nodes, dest, voxel, fuse_device, register_drift)
        except Exception as e:
            log_error("fuse-worker", e); self.q.put(("fuse_done", name, ("err", "the build stopped unexpectedly - see the log")))
    def _fuse_worker_impl(self, name, only_nodes=None, dest=None, voxel=None, fuse_device="auto", register_drift=True):
        dest=dest or DEFAULT_DEST; local=os.path.join(dest, name)
        nodes=[]
        for base in (os.path.join(PROJECTS, name), local):          # device listing first, else local
            try:
                nodes=[n for n in sorted(os.listdir(os.path.join(base,"data"))) if os.path.isdir(os.path.join(base,"data",n))]
                if nodes: break
            except Exception: pass
        if only_nodes: nodes=[n for n in nodes if n in only_nodes]
        if not nodes:
            self.q.put(("fuse_done", name, ("err","no scan data found"))); return
        outs=[]; voxel=float(voxel or 0.4)
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
            # a scan the scanner never fused has no registration, only live tracking: fix the drift first (register.py)
            if register_drift and not os.path.exists(os.path.join(lcache, "global_register_pose.pose")) \
                    and not os.path.exists(os.path.join(lcache, "pointyoink_register_pose.pose")):
                self.q.put(("fuse_status","Scan %d/%d: registering frames (fixing drift)…"%(ni+1,len(nodes)))); self.q.put(("fuse_node", node, 0.02, "Fixing drift: fusing fragments…"))
                try:
                    rp=self._popen([_sys.executable, os.path.join(HERE,"register.py"), "--frames", lcache, "--calib", calib] + ([] if fuse_device=="cpu" else ["--gpu"]),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=dict(os.environ, OPENBLAS_NUM_THREADS="1"))
                    for ln in rp.stdout:
                        ln=ln.strip()
                        if not ln.startswith("STAGE "): continue
                        parts=ln.split(" ",2); stage=parts[1]
                        try: payload=json.loads(parts[2]) if len(parts)>2 else {}
                        except Exception: payload={}
                        if stage=="fragments": self.q.put(("fuse_node", node, 0.02+0.13*payload.get("done",0)/max(1,payload.get("total",1)), "Fixing drift: fragment %d of %d" % (payload.get("done",0), payload.get("total",0))))
                        elif stage=="register": self.q.put(("fuse_node", node, 0.15+0.25*payload.get("done",0)/max(1,payload.get("total",1)), "Fixing drift: matching fragments %d of %d (%d loops found)" % (payload.get("done",0), payload.get("total",0), payload.get("loops",0))))
                        elif stage=="done": self.q.put(("fuse_node", node, 0.4, "Drift fixed: %d loop closures, frames moved %.0f mm on average" % (payload.get("loops",0), payload.get("moved_median_mm",0))))
                        elif stage=="error": log_line("register %s/%s: %s" % (name, node, payload.get("msg","")))
                    rp.wait(); self._forget_child(rp)
                except Exception as e: log_error("register-launch", e)
            self.q.put(("fuse_status","Scan %d/%d: fusing…"%(ni+1,len(nodes))))
            try:
                proc=self._popen([_sys.executable, os.path.join(HERE,"fuse.py"), "--frames", lcache, "--calib", calib,
                                  "--out", out, "--voxel", str(voxel)] + ([] if fuse_device=="cpu" else ["--gpu"]),
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
                        self.q.put(("fuse_node", node, 0.4+0.5*d/t_, "%s: frame %d of %d" % (devname, d, t_)))
                    elif stage=="extract":
                        self.q.put(("fuse_status","Scan %d/%d: building the 3D model…"%(ni+1,len(nodes)))); self.q.put(("fuse_node", node, 0.95, "Building the 3D model…"))
                    elif stage=="done": ok=True
                    elif stage=="error": log_line("fuse %s/%s: %s"%(name,node,payload.get("msg","")))
                proc.wait(); self._forget_child(proc)
                if ok and os.path.exists(out): outs.append(os.path.basename(out)); self.q.put(("fuse_node", node, 1.0, "Built: %s" % os.path.basename(out)))
                else: log_line("fuse produced no mesh for %s/%s"%(name,node)); self.q.put(("fuse_node", node, 0.0, "Could not build this scan (see Help > Log)"))
            except Exception as e:
                log_error("fuse-launch", e)
        if outs: self.q.put(("fuse_done", name, ("ok", ", ".join(outs))))
        else: self.q.put(("fuse_done", name, ("err", "no scans could be processed - see the log")))



    # ---- WiFi: the scanner's Share to PC > Wi-Fi, received by us (wifi.py) ----
    def on_wifi(self):
        if self._wifi:
            if self._wifi_bg: self._wifi_reopen(); return   # backgrounded: bring the window back, don't stop
            self._wifi_cancel(); return
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
        self.hold_banner("WiFi: waiting for the scanner", AC)
        self._wifi_dialog(rx)
    def _wifi_dialog(self, rx, restore=False):
        t=self._top("Share to PC over WiFi", 520, 400, key="wifi")
        if t is None: return
        # closing the window backgrounds the transfer (it keeps receiving); Cancel is the only stop
        t.protocol("WM_DELETE_WINDOW", self._wifi_background); t.resizable(False, False); self.wifi_top=t
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
        self.wifi_samples=[]; self._wifi_last_sample=0.0; self._wifi_thumb_ok=False
        stats=ctk.CTkFrame(self.wifi_recv, fg_color="transparent"); stats.pack(fill="x", padx=24)
        self.wifi_stats={}
        stats.grid_columnconfigure(0, weight=3); stats.grid_columnconfigure(1, weight=1)   # 2 x 2: the wide numbers left, the short ones right
        for key,cap,r,c in (("got","received",0,0),("files","files",0,1),("rate","speed",1,0),("eta","time left",1,1)):
            col=ctk.CTkFrame(stats, fg_color="#0d0f14", corner_radius=10); col.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
            v=ctk.CTkLabel(col, text="-", text_color=TX, font=ctk.CTkFont(size=14, weight="bold")); v.pack(pady=(8,0))
            ctk.CTkLabel(col, text=cap, text_color=MUT, font=ctk.CTkFont(size=10)).pack(pady=(0,8)); self.wifi_stats[key]=v
        self.wifi_hint=ctk.CTkLabel(card, text="Both must be on the same network. If the scanner isn't found within 30 seconds, allow port 9706 (UDP and TCP) in your firewall.",
                                    text_color=MUT, font=ctk.CTkFont(size=10), wraplength=420, justify="center"); self.wifi_hint.pack(pady=(10,0))
        br=ctk.CTkFrame(card, fg_color="transparent"); br.pack(side="bottom", pady=(0,16))
        self.wifi_newcode=ctk.CTkButton(br, text="↻ New code", width=110, corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=self._wifi_new_code)
        self.wifi_newcode.pack(side="left", padx=6)
        bgb=ctk.CTkButton(br, text="Run in background", width=140, corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=self._wifi_background)
        bgb.pack(side="left", padx=6); self._tip(bgb, "Keep receiving and hide this window - progress stays in the status bar; click WiFi to show it again.")
        ctk.CTkButton(br, text="Cancel", width=100, corner_radius=16, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=self._wifi_cancel).pack(side="left", padx=6)
        self._wifi_pulse_i=0; self._wifi_pulse()
        if restore and rx.t0:      # reopened mid-transfer: show the receiving block right away
            try:
                self.wifi_state.configure(text="Code accepted  ·  receiving", text_color=OK)
                self.wifi_hint.pack_forget(); self.wifi_recv.pack(fill="x", pady=(14,0)); self.wifi_newcode.configure(state="disabled")
                self.wifi_top.geometry("520x600")
            except Exception: pass
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
    def _speed_draw(self, cv, samples):
        """Area chart of transfer speed over TIME (x = sample order), like a network monitor. Shared by the
        WiFi panel and the USB import popup. Time-based, not progress-based: a slow full-project import barely
        advances in % for ages, so a progress x-axis piled every point at the far left (a flat line)."""
        try: W=max(50, cv.winfo_width()); H=int(cv.cget("height"))
        except Exception: return
        cv.delete("all")
        for gy in (0.25,0.5,0.75): cv.create_line(0, H*gy, W, H*gy, fill="#161a22")
        if not samples: return
        rates=[r for _,r in samples]; N=len(rates)
        # scale to the 90th percentile, not the peak: MTP sends an initial buffered burst that is many times
        # the steady rate, and peak-scaling squashed the whole rest of the transfer into a flat line at the
        # bottom. p90 lets the steady rate fill the chart; the one-time spike just clips at the top.
        srt=sorted(rates); p90=srt[min(N-1, int(N*0.9))]
        top=(p90 or max(rates) or 1.0)*1.3
        def _y(r): return H-4-min(1.0, r/top)*(H-14)   # clamp so a clipped spike sits on the top edge, not off-canvas
        if N==1:
            pts=[(W-4, _y(rates[0]))]
        else:
            pts=[(4+(i/(N-1))*(W-8), _y(r)) for i,r in enumerate(rates)]   # x = time order, newest on the right
        if len(pts)>=2:
            poly=[(pts[0][0], H-4)]+pts+[(pts[-1][0], H-4)]
            cv.create_polygon(*[c for xy in poly for c in xy], fill="#1d3f66", outline="")
            cv.create_line(*[c for xy in pts for c in xy], fill=AC, width=2, smooth=True)
    def _wifi_graph_add(self, frac, rate):
        """Add a speed sample (time-based) and redraw the WiFi area chart."""
        sm=self.wifi_samples; now=time.time()
        if not sm or now-self._wifi_last_sample>=0.25:
            sm.append((frac, rate)); self._wifi_last_sample=now
            if len(sm)>240: del sm[0]                  # rolling ~1 min window so it scrolls, not squashes
        else: sm[-1]=(frac, rate)
        self._speed_draw(self.wifi_graph, sm)
        self._wifi_peak=max(r for _,r in sm)          # shown in the stats row, not over the curve
    def _import_popup(self):
        """A transfer window for USB import, mirroring the WiFi receive dialog: project name, the speed
        graph, and received/scans/speed/time-left tiles. Closing it (or Run in background) just hides the
        window; the import keeps going with the thin bar in the status area."""
        top=getattr(self, "_imp_top", None)
        if top is not None:
            try:
                if top.winfo_exists():
                    top.deiconify(); top.lift()
                    self.progress.grid_remove(); self.progline.grid_remove()   # popup owns progress again; clear the bottom-bar copy
                    return
            except Exception: pass
        self._imp_samples=[]; self._imp_last=0.0
        top=tk.Toplevel(self); top.title("Importing"); top.configure(bg=BG)
        try: top.transient(self.winfo_toplevel())
        except Exception: pass
        top.geometry(self._centred(520, 500)); self._imp_top=top   # was 430: the graph + 4 stat tiles + button row overflowed, clipping the buttons
        card=ctk.CTkFrame(top, fg_color=CARD, corner_radius=16); card.pack(fill="both", expand=True, padx=16, pady=16)
        self.imp_title=ctk.CTkLabel(card, text="Importing…", text_color=TX, font=ctk.CTkFont(size=15, weight="bold")); self.imp_title.pack(pady=(16,2))
        self.imp_sub=ctk.CTkLabel(card, text="Copying off the scanner…", text_color=MUT, font=ctk.CTkFont(size=11)); self.imp_sub.pack()
        self.imp_graph=tk.Canvas(card, height=110, bg="#0d0f14", highlightthickness=0); self.imp_graph.pack(fill="x", padx=24, pady=(14,6))
        stats=ctk.CTkFrame(card, fg_color="transparent"); stats.pack(fill="x", padx=24)
        stats.grid_columnconfigure(0, weight=3); stats.grid_columnconfigure(1, weight=1)
        self.imp_stats={}
        for key,cap,r,c in (("got","received",0,0),("scans","scans",0,1),("rate","speed",1,0),("eta","time left",1,1)):
            col=ctk.CTkFrame(stats, fg_color="#0d0f14", corner_radius=10); col.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
            v=ctk.CTkLabel(col, text="-", text_color=TX, font=ctk.CTkFont(size=14, weight="bold")); v.pack(pady=(8,0))
            ctk.CTkLabel(col, text=cap, text_color=MUT, font=ctk.CTkFont(size=10)).pack(pady=(0,8)); self.imp_stats[key]=v
        br=ctk.CTkFrame(card, fg_color="transparent"); br.pack(side="bottom", pady=(10,18))
        ctk.CTkButton(br, text="Run in background", width=160, height=36, corner_radius=8, fg_color="transparent",
                      border_width=1, border_color=STROKE, hover_color=CARD2, text_color=TX,
                      font=ctk.CTkFont(size=12), command=self._import_background).pack(side="left", padx=6)
        ctk.CTkButton(br, text="Cancel", width=120, height=36, corner_radius=8, fg_color="#3a2530",
                      hover_color=DANGER, text_color=TX, font=ctk.CTkFont(size=12),
                      command=self.on_cancel).pack(side="left", padx=6)
        top.protocol("WM_DELETE_WINDOW", self._import_background)   # closing hides it; the import keeps running (bottom bar shows progress)
    def _import_background(self):
        """Hide the popup and move the progress to the thin bottom bar so the import keeps running quietly."""
        top=getattr(self, "_imp_top", None)
        try:
            if top is not None: top.withdraw()
        except Exception: pass
        if self.pulling:
            self.progress.grid(row=1,column=0, columnspan=3, sticky="ew", pady=(8,0))
            self.progline.grid(row=2,column=0, columnspan=3, sticky="w", padx=(20,0), pady=(0,10))
    def _close_import_popup(self):
        top=getattr(self, "_imp_top", None); self._imp_top=None
        if top is not None:
            try: top.destroy()
            except Exception: pass
    def _imp_graph_add(self, frac, rate):
        """Feed the USB import speed graph + stats popup."""
        cv=getattr(self, "imp_graph", None)
        if cv is None:
            return
        try:
            if not cv.winfo_exists(): return
        except Exception: return
        sm=getattr(self, "_imp_samples", None)
        if sm is None: sm=self._imp_samples=[]
        now=time.time()
        if not sm or now-getattr(self,"_imp_last",0.0)>=0.25:
            sm.append((frac, rate)); self._imp_last=now
            if len(sm)>240: del sm[0]                  # rolling ~1 min window so it scrolls over time
        else: sm[-1]=(frac, rate)
        self._speed_draw(cv, sm)
        try:
            self.imp_stats["rate"].configure(text=human(rate)+"/s")
        except Exception: pass
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
    def _wifi_background(self):
        """Hide the transfer window but keep receiving: the WiFi button turns 'receiving' and reopens it."""
        rx=self._wifi
        if not rx: self._wifi_close_dialog(); return
        self._wifi_bg=True; self._wifi_close_dialog()
        self.wifi_btn.configure(text="📶  Receiving…" if rx.t0 else "📶  Waiting…", fg_color="#12303f")
        self.set_banner(("WiFi still receiving in the background - click WiFi to show it." if rx.t0
                         else "WiFi share still open in the background - click WiFi to show the code."), AC)
    def _wifi_reopen(self):
        """Bring the backgrounded transfer window back, restoring the receiving view if it is already flowing."""
        rx=self._wifi
        if not rx: return
        self._wifi_bg=False
        self.wifi_btn.configure(text="Stop", fg_color="#3a2530")
        self._wifi_dialog(rx, restore=True)
    def _wifi_cancel(self):
        rx=self._wifi
        self._wifi_bg=False
        if not rx: self._wifi_close_dialog(); return
        self._wifi=None; self._wifi_close_dialog()
        self.wifi_btn.configure(text="📶  WiFi", fg_color="transparent")
        got=rx.bytes
        def _stop():
            rx.stop()
            try: shutil.rmtree(rx.stage, ignore_errors=True)                 # can be a partial multi-GB receive - never on the UI thread
            except Exception: pass
        threading.Thread(target=_stop, daemon=True).start()
        self.set_status("")
        self.set_banner("WiFi share stopped%s." % (" at %.0f MB - share again on the scanner to retry" % (got/1048576) if got else ""), WARN if got else MUT)
    def _wifi_event(self, kind, info):
        rx=self._wifi
        if not rx: return
        ui=not self._wifi_bg      # while backgrounded the dialog widgets are gone: keep only status/banner/button
        if kind=="searching":
            if ui: self.wifi_state.configure(text="Scanner found at %s - enter the code on it." % info["ip"], text_color=OK)
            self.hold_banner("WiFi: scanner found, waiting for the code", AC)
            try: self.wifi_hint.pack_forget()      # the firewall hint only matters while nothing has been heard
            except Exception: pass
        elif kind=="badcode":
            if info["locked"]:
                if ui: self.wifi_state.configure(text="Too many wrong codes - closing this share. Click WiFi for a new code.", text_color=WARN)
                self.after(2500, self._wifi_cancel)
            elif ui:
                self.wifi_state.configure(text="Wrong code entered on the scanner - try again (%d attempts left)." % (5-rx.bad), text_color=WARN)
        elif kind=="connected":
            self.hold_banner("WiFi: receiving…", AC)
            if self._wifi_bg: self.wifi_btn.configure(text="📶  Receiving…", fg_color="#12303f")
            if ui:
                self.wifi_state.configure(text="Code accepted  ·  receiving", text_color=OK)
                try:
                    self.wifi_hint.pack_forget(); self.wifi_recv.pack(fill="x", pady=(14,0)); self.wifi_newcode.configure(state="disabled")
                    self.wifi_top.geometry("520x600")     # room for the thumbnail, progress and stats rows (measured: card needs ~590 with its padding)
                except Exception: pass
        elif kind=="progress":
            now=time.time()
            if now-getattr(self, "_wifi_last_draw", 0.0) < 0.08: return      # never let redraws pile up on the UI thread
            self._wifi_last_draw=now
            tot=info["total"]; frac=(info["bytes"]/tot) if tot else 0; rate=info["rate"]; avg=info.get("avg") or rate
            self.hold_banner("WiFi: %.0f%%" % (100*frac), AC)
            if ui:
                self._wifi_graph_add(frac, rate)
                left=(tot-info["bytes"])/avg if (tot and avg>0) else None
                self.wifi_stats["got"].configure(text=("%.0f%%  ·  %.0f / %.0f MB" % (100*frac, info["bytes"]/1048576, tot/1048576)) if tot else "%.0f MB" % (info["bytes"]/1048576))
                self.wifi_stats["files"].configure(text=str(info["files"]))
                self.wifi_stats["rate"].configure(text="%.0f MB/s  ·  peak %.0f MB/s" % (rate/1048576, getattr(self, "_wifi_peak", rate)/1048576))
                self.wifi_stats["eta"].configure(text=("%d s" % left if left<90 else "%d min" % (left/60)) if left is not None else "-")
                if not self.wifi_proj.cget("text") or not getattr(self, "_wifi_thumb_ok", False):   # name as soon as the folder exists; the picture arrives later in the transfer, keep trying
                    try:
                        projs=[d for d in os.listdir(rx.stage) if os.path.isdir(os.path.join(rx.stage, d))]
                        if projs:
                            if not self.wifi_proj.cget("text"): self.wifi_proj.configure(text="%s%s" % (self.disp(projs[0]), "  (+%d more)" % (len(projs)-1) if len(projs)>1 else ""))
                            pv=sorted(glob.glob(os.path.join(rx.stage, projs[0], "data", "*", "preview.png")))
                            if pv and os.path.getsize(pv[0])>2000:
                                self.imgs["wifi_thumb"]=cimg(pv[0], 84); self.wifi_thumb.configure(image=self.imgs["wifi_thumb"]); self._wifi_thumb_ok=True
                    except Exception: pass
        elif kind=="done":
            self._wifi=None; self._wifi_bg=False; threading.Thread(target=rx.stop, daemon=True).start(); self._wifi_close_dialog(); self.wifi_btn.configure(text="📶  WiFi", fg_color="transparent")
            projects=info["projects"]
            if not projects:
                threading.Thread(target=shutil.rmtree, args=(rx.stage,), kwargs={"ignore_errors": True}, daemon=True).start()
                self.set_banner("The scanner finished but sent no project.", WARN); self.set_status(""); return
            incomplete=info.get("incomplete") or []
            if incomplete:   # the scanner said done, but some files are missing parts: warn, don't pretend it's clean
                self.set_banner("WiFi finished but %d file%s came in incomplete (missing parts) - share again for a clean copy." % (len(incomplete), "" if len(incomplete)==1 else "s"), WARN)
                log_line("wifi incomplete files: %s" % ", ".join(incomplete[:20]))
            else:
                self.set_banner("Received %s over WiFi - choose what to keep." % ", ".join(projects), OK)
            self.set_status("")
            self._wifi_picker(rx.stage, projects)
    def _wifi_recover(self):
        """A transfer that finished but was never imported (app closed, picker lost) is still in
        staging: offer it again instead of leaving a gigabyte stranded in a hidden folder."""
        if self._wifi or self.pulling: return
        stage=os.path.join(self.dest.get() or DEFAULT_DEST, ".wifi-incoming")
        try: projects=sorted(d for d in os.listdir(stage) if os.path.isdir(os.path.join(stage, d, "data")))
        except Exception: return
        if not projects:
            threading.Thread(target=shutil.rmtree, args=(stage,), kwargs={"ignore_errors": True}, daemon=True).start(); return
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
        t=self._top("Received over WiFi", 660, min(780, 300+66*max(1,len(rows))), key="wifipick")
        if t is None: return
        t.protocol("WM_DELETE_WINDOW", lambda: None)   # decide with the buttons; the data is only in staging
        ctk.CTkLabel(t, text="%s  ·  %d scan%s" % (", ".join(projects), len(rows), "" if len(rows)==1 else "s"),
                     font=ctk.CTkFont(family=WORDMARK, size=15, weight="bold"), text_color=TX).pack(anchor="w", padx=20, pady=(18,2))
        ctk.CTkLabel(t, text="Tick the scans to keep. Formats and clean-up follow the options in the main window.",
                     font=ctk.CTkFont(size=12), text_color=MUT).pack(anchor="w", padx=20)
        br=ctk.CTkFrame(t, fg_color="transparent"); br.pack(side="bottom", fill="x", padx=16, pady=14)      # buttons claim their space first: never clipped
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
            if not r["mesh"] and not r["cloud"]: parts.insert(0, "raw data only, no 3D model yet")
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
        def close():
            self._dialogs.pop("wifipick", None); t.destroy()
        def discard():
            close(); threading.Thread(target=shutil.rmtree, args=(stage,), kwargs={"ignore_errors": True}, daemon=True).start()
            self.set_banner("Discarded the received project.", MUT)
        def go():
            keep={}
            for r,v in zip(rows, vars_):
                if v.get(): keep.setdefault(r["project"], []).append(r["node"])
            if not keep: discard(); return
            if mode.get()=="models" and not any((r["mesh"] or r["cloud"]) for r,v in zip(rows, vars_) if v.get()):
                self.set_banner("The ticked scans have no 3D models yet: choose Full project to keep their raw data.", WARN); return
            dest=self.dest.get() or DEFAULT_DEST
            self._wifi_confirm(keep, dest, lambda names, replace: start(keep, dest, names, replace), stage=stage)
        def start(keep, dest, names, replace):
            close()
            for n,label in names.items():
                if label.strip(): self.records.setdefault(n, {})["label"]=label.strip()
            self.pulling=True; self.cancel=False; self._pull_list=list(keep); self._export_fails=[]
            self.import_btn.grid_remove(); self.cancel_btn.configure(text="Cancel", state="normal"); self.cancel_btn.grid(row=0,column=3, padx=(6,20), pady=(12,4), sticky="e")   # match the Import button's placement so it isn't crooked
            self.progress.grid(row=1,column=0, columnspan=3, sticky="ew", pady=(8,0)); self.progline.grid(row=2,column=0, columnspan=3, sticky="w", padx=(20,0), pady=(0,10))
            cleanup=self.cleanup.get(); clean_opts=self._clean_options() if cleanup else None
            fmts=[e for e,v in (("stl",self.exp_stl),("obj",self.exp_obj),("glb",self.exp_glb)) if v.get()]
            self.set_banner("Saving %s…" % ", ".join(self.disp(n) for n in keep), AC)
            self._start_thread(self._wifi_finish_worker, stage, keep, dest, mode.get()=="models", fmts, cleanup, replace, clean_opts, name="wifi-import")
        ctk.CTkButton(br, text="Import", width=110, height=34, corner_radius=17, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=go).pack(side="right", padx=6)
        ctk.CTkButton(br, text="Discard", width=100, height=34, corner_radius=17, fg_color=CARD2, hover_color=STROKE, text_color=TX, command=discard).pack(side="right", padx=6)
    def _peek(self, title, image_path, mesh_path=None, frames_dir=None, calib=None, key=None):
        """A bigger look at one scan: the live 3D view when there is a model; for raw data a quick draft is built
        (every 3rd frame, 1 mm) so it can still be turned; the scanner's preview picture is the fallback."""
        t=self._top(title, 820, 640, key="peek")
        if t is None: return
        box=ctk.CTkFrame(t, fg_color="#0a0c10", corner_radius=12); box.pack(fill="both", expand=True, padx=12, pady=12)
        box.grid_columnconfigure(0, weight=1); box.grid_rowconfigure(0, weight=1)
        foot=ctk.CTkLabel(t, text="", text_color=DIM, font=ctk.CTkFont(size=10)); foot.pack(pady=(0,8))
        def show_image():
            try:
                im=Image.open(image_path).convert("RGB")
                bb=Image.eval(im.convert("L"), lambda x: 255 if x>18 else 0).getbbox()      # crop the black around the object
                if bb:
                    m=24; im=im.crop((max(0,bb[0]-m), max(0,bb[1]-m), min(im.width,bb[2]+m), min(im.height,bb[3]+m)))
                im=im.resize((im.width*max(1, int(700/max(1,im.width))), im.height*max(1, int(700/max(1,im.width)))), Image.LANCZOS) if im.width<700 else im
                im.thumbnail((780, 560)); self.imgs["peek"]=ctk.CTkImage(light_image=im, dark_image=im, size=im.size)
                ctk.CTkLabel(box, image=self.imgs["peek"], text="").grid(row=0,column=0)
            except Exception: ctk.CTkLabel(box, text="No picture for this scan", text_color=MUT).grid(row=0,column=0)
            foot.configure(text="The scanner's own preview. No 3D model yet: it is raw data until it is built.")
        def show_mesh(path, note):
            v=self._new_view(box); v.grid(row=0,column=0, sticky="nsew", padx=4, pady=4)
            l=ctk.CTkLabel(box, text="Loading the 3D view…", text_color=MUT, font=ctk.CTkFont(size=13), fg_color="#0a0c10"); l.grid(row=0,column=0, sticky="nsew"); l.lift()
            v.load(path, lambda ok: (l.grid_remove() if (ok and t.winfo_exists()) else None), max_faces=600000)
            foot.configure(text=note+"  Drag to turn, scroll to zoom.")
        if mesh_path and os.path.exists(mesh_path): show_mesh(mesh_path, ""); return
        if frames_dir and calib and glob.glob(os.path.join(frames_dir, "*.dph")) and os.path.exists(calib) and _has_open3d_cache is True:
            # fallback key must be unique per scan: frames_dir is <node>/cache, so basename is always
            # "cache" and every scan would collide on cache__draft.ply. Use the last two path parts
            # (<node>_cache) instead. And only reuse a cached draft that is newer than its frames.
            os.makedirs(THUMBS, exist_ok=True)
            dkey=key or ("_".join([p for p in os.path.normpath(frames_dir).split(os.sep) if p][-2:]) or "draft")
            draft=os.path.join(THUMBS, "%s__draft.ply" % dkey)
            try: fresh_draft=os.path.exists(draft) and os.path.getmtime(draft)>=os.path.getmtime(frames_dir)
            except Exception: fresh_draft=os.path.exists(draft)
            if fresh_draft: show_mesh(draft, "Quick draft (every 3rd frame, 1 mm): the real build is finer."); return
            busy=ctk.CTkLabel(box, text="Building a quick draft so you can turn it…", text_color=MUT, font=ctk.CTkFont(size=13), fg_color="#0a0c10"); busy.grid(row=0,column=0, sticky="nsew")
            def work():
                ok=False
                try:
                    cmd=[_sys.executable, os.path.join(HERE,"fuse.py"), "--frames", frames_dir, "--calib", calib, "--out", draft, "--voxel", "1.0", "--every", "3"] + ([] if self.cfg.get("fuse_device","auto")=="cpu" else ["--gpu"])
                    r=self._run_child(cmd, timeout=600, env=dict(os.environ, OPENBLAS_NUM_THREADS="1"))
                    ok=(r.returncode==0 and os.path.exists(draft) and os.path.getsize(draft)>1024)
                    if not ok: log_line("draft build failed: %s" % (r.stdout+r.stderr)[-300:])
                except Exception as e: log_error("draft build", e)
                def done():
                    if not t.winfo_exists(): return
                    busy.grid_remove()
                    if ok: show_mesh(draft, "Quick draft (every 3rd frame, 1 mm): the real build is finer.")
                    else: show_image()
                self.q.put(("call", done))
            threading.Thread(target=work, daemon=True).start(); return
        show_image()
    def _wifi_confirm(self, keep, dest, then, stage=None):
        """Name the incoming project(s) and, when one is already on this PC, choose keep-and-add or replace."""
        existing=[n for n in keep if os.path.isdir(os.path.join(dest, n))]
        nscans=sum(len(v) for v in keep.values())
        t=self._top("Before importing", 600, min(840, 200+60*len(keep)+58*nscans+(90 if existing else 0)), key="wifiname")
        if t is None: return
        t.protocol("WM_DELETE_WINDOW", lambda: (self._dialogs.pop("wifiname", None), t.destroy()))
        ctk.CTkLabel(t, text="Name it (optional)", font=ctk.CTkFont(family=WORDMARK, size=15, weight="bold"), text_color=TX).pack(anchor="w", padx=22, pady=(20,2))
        ctk.CTkLabel(t, text="A name you will recognise, like \"headrest front\". The scanner's id stays as the folder name.",
                     text_color=MUT, font=ctk.CTkFont(size=11), wraplength=460, justify="left").pack(anchor="w", padx=22)
        vars_={}; scan_vars={}
        for n in keep:
            row=ctk.CTkFrame(t, fg_color="transparent"); row.pack(fill="x", padx=22, pady=(10,0))
            ctk.CTkLabel(row, text=n, text_color=MUT, font=ctk.CTkFont(size=11), width=190, anchor="w").pack(side="left")
            v=ctk.StringVar(value=self.records.get(n, {}).get("label") or ""); vars_[n]=v
            ctk.CTkEntry(row, textvariable=v, placeholder_text="name (optional)", fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=10).pack(side="left", fill="x", expand=True, padx=(8,0))
            # the scans too: "front", "back", "left side" is what you want to see when lining them up
            for i,node in enumerate(sorted(keep[n])):
                sr=ctk.CTkFrame(t, fg_color="transparent"); sr.pack(fill="x", padx=22, pady=(4,0))
                pv=None
                for cand in ([os.path.join(stage, n, "data", node, "preview.png")] if stage else [])+[os.path.join(dest, n, "data", node, "preview.png"), os.path.join(dest, n, "%s_%s.png" % (n, node))]:
                    if os.path.exists(cand): pv=cand; break
                th=ctk.CTkLabel(sr, text="", width=72, height=48, fg_color="#0a0c10", corner_radius=6); th.pack(side="left", padx=(8,8))
                if pv:
                    try: self.imgs["confirm_"+node]=cimg(pv, 72); th.configure(image=self.imgs["confirm_"+node])
                    except Exception: pass
                    root=os.path.join(stage or dest, n, "data", node)
                    mesh=next((c for c in [os.path.join(root, "fuse_mesh.ply")] if os.path.exists(c)), None)
                    th.bind("<Button-1>", lambda e, pv=pv, mesh=mesh, i=i, node=node, root=root, n=n: self._peek("scan %02d · %s" % (i+1, node), pv, mesh,
                            frames_dir=os.path.join(root, "cache"), calib=os.path.join(root, "param", "Pl.bin"), key="%s__%s" % (n, node)))
                    self._tip(th, "Click for a bigger look")
                ctk.CTkLabel(sr, text="%s\nscan %d of %d" % (node, i+1, len(keep[n])), text_color=DIM, font=ctk.CTkFont(size=10), width=110, anchor="w", justify="left").pack(side="left")
                sv=ctk.StringVar(value=self.records.get(n, {}).get("scan_labels", {}).get(node) or self._device_scan_names(n, os.path.join(stage, n) if stage else None).get(node) or ""); scan_vars[(n, node)]=sv
                ctk.CTkEntry(sr, textvariable=sv, placeholder_text="front, back, left side… (optional)", height=26, fg_color="#0d0f14", border_color=STROKE, text_color=TX, corner_radius=8, font=ctk.CTkFont(size=11)).pack(side="left", fill="x", expand=True, padx=(8,0))
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
            for (n,node),sv in scan_vars.items():
                txt=sv.get().strip(); labs=self.records.setdefault(n, {}).setdefault("scan_labels", {})
                if txt: labs[node]=txt
                else: labs.pop(node, None)
            self._dialogs.pop("wifiname", None); t.destroy(); then({n: v.get() for n,v in vars_.items()}, mode.get()=="replace")
        ctk.CTkButton(br, text="Import", width=110, height=34, corner_radius=17, fg_color=AC, hover_color=AC_H, text_color="#04121f", command=ok).pack(side="right", padx=6)
        ctk.CTkButton(br, text="Back", width=90, height=34, corner_radius=17, fg_color=CARD2, hover_color=STROKE, text_color=TX,
                      command=lambda: (self._dialogs.pop("wifiname", None), t.destroy())).pack(side="right", padx=6)
    def _wifi_finish_worker(self, stage, keep, dest, mo, fmts, cleanup, replace=False, clean_opts=None):
        failed=[]; no_models=[]; total=len(keep)
        for i,(name,nodes) in enumerate(keep.items()):
            if self.cancel: break
            try:
                if mo:
                    if replace and os.path.isdir(os.path.join(dest, name)): shutil.rmtree(os.path.join(dest, name), ignore_errors=True)
                    n=self._import_flat(name, dest, fmts, cleanup, i, total, src_root=stage, nodes=nodes, clean_opts=clean_opts)
                    if not n: no_models.append(name)
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
        self.q.put(("cancelled" if self.cancel else "done", dest, failed, no_models))

    # ---- screenshots ----
    def refresh_screenshots(self):
        if getattr(self, "_shots_busy", False): return
        self._shots_busy=True; self.hold_banner("Reading screenshots off the device…", AC)
        try:                                            # show a Reading state now, so the panel doesn't sit on "No captures yet" while it loads
            self.shots.grid_remove()
            for w in self.shots.winfo_children(): w.destroy()
            ctk.CTkLabel(self.shots, text="Reading captures off the device…\nOver USB this can take a few seconds.",
                         text_color=MUT, justify="left").grid(row=0, column=0, columnspan=4, padx=20, pady=20, sticky="w")
            self.shots.grid()
        except Exception: pass
        self._start_thread(self._shots_worker, name="shots")
    def _shots_worker(self):
        cache=os.path.join(THUMBS, "shots")
        try:
            if not quick_mounted(probe=True, timeout=2):
                # scanner not connected: still show the screenshots we've already cached locally (so pulled/seen
                # captures stay viewable offline) instead of a dead "connect USB" screen
                offline=[]
                try:
                    for nm in sorted((f for f in os.listdir(cache) if f.lower().endswith((".png",".jpg",".jpeg"))), reverse=True):
                        p=os.path.join(cache, nm)
                        if os.path.isfile(p): offline.append((nm, p))
                except Exception: pass
                self.q.put(("shots_offline", (offline, [])) if offline else ("shots_unmounted", None)); return
            items=list_screenshots(); local=[]
            os.makedirs(cache, exist_ok=True)
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
        except Exception as e:
            log_error("shots", e); self.q.put(("shots_failed", str(e)))
    def _play_recording(self, path, nm):
        """Play a recording. MTP is flaky for video, so if it's still on the scanner mount, copy it into a
        PLAYBACK CACHE (not captures/) first - playing is a preview, it must NOT flip the on-device/on-PC
        badge; only Pull all saves to the PC."""
        try:
            if path.startswith(MOUNT) or path.startswith(PROJECTS):
                cache=os.path.join(THUMBS, "video"); os.makedirs(cache, exist_ok=True)
                local=os.path.join(cache, nm)
                try: fresh=os.path.exists(local) and os.path.getsize(local)==os.path.getsize(path)
                except Exception: fresh=False
                if fresh: subprocess.Popen(["xdg-open", local]); return
                self.hold_banner("Loading %s to play…" % nm[:24], AC)
                threading.Thread(target=self._copy_and_play, args=(path, local, nm), daemon=True).start(); return
            subprocess.Popen(["xdg-open", path])
        except Exception as e:
            log_error("play-rec", e); self.set_banner("Couldn't open that recording (see Help > Log).", WARN)
    def _copy_and_play(self, src, local, nm):
        try:
            shutil.copyfile(src, local)
            def done():
                self._dev_hold=0.0                                  # release the top banner back to device state
                self.set_status("Playing %s" % nm[:24]); self.after(4000, lambda: self.set_status(""))   # transient bottom-bar note that clears itself (was a stuck top banner)
                try: subprocess.Popen(["xdg-open", local])
                except Exception as e: log_error("xdg-open rec", e)
            self.q.put(("call", done))
        except Exception as e:
            log_error("copy-play", e); self.q.put(("call", lambda: self.set_banner("Couldn't copy that recording to play (see Help > Log).", WARN)))
    def _video_thumb(self, src, nm):
        """First frame of a recording as a cached JPG (ffmpeg). Returns the path or None."""
        out=os.path.join(THUMBS, "video", os.path.splitext(nm)[0]+"__frame.jpg")
        try:
            if os.path.exists(out) and os.path.getsize(out)>512: return out
            os.makedirs(os.path.dirname(out), exist_ok=True); tmp=out+".tmp.%d.jpg"%os.getpid()
            r=subprocess.run(["ffmpeg","-y","-loglevel","error","-i",src,"-frames:v","1","-vf","scale=480:-1",tmp], capture_output=True, timeout=40)
            if r.returncode==0 and os.path.exists(tmp) and os.path.getsize(tmp)>512: os.replace(tmp, out); return out
        except Exception as e: log_error("video-thumb "+nm, e)
        return None
    def _video_meta(self, src):
        """(duration_seconds, width, height) via ffprobe, or (None, None, None)."""
        try:
            r=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries",
                              "stream=width,height:format=duration","-of","json",src], capture_output=True, text=True, timeout=20)
            d=json.loads(r.stdout or "{}"); w=h=dur=None
            if d.get("streams"): w=d["streams"][0].get("width"); h=d["streams"][0].get("height")
            try: dur=float(d.get("format",{}).get("duration"))
            except Exception: dur=None
            return dur, w, h
        except Exception: return None, None, None
    def _video_popout(self, path, nm):
        """A viewer for a recording: first frame + metadata (resolution, length, size, date), with Play."""
        try: top=self._top("Recording", 1020, 780, key="shot")
        except Exception as e: log_error("video-popout", e); return
        body=ctk.CTkFrame(top, fg_color="#0a0c10"); body.pack(fill="both", expand=True)
        img_lbl=ctk.CTkLabel(body, text="Reading the video…", text_color=MUT, fg_color="#0a0c10"); img_lbl.pack(fill="both", expand=True, padx=10, pady=10)
        bar=ctk.CTkFrame(top, fg_color="transparent"); bar.pack(fill="x", pady=(0,8))
        cap=ctk.CTkLabel(bar, text=nm, text_color=TX, font=ctk.CTkFont(size=12, weight="bold")); cap.pack(side="left", padx=(14,10))
        meta=ctk.CTkLabel(bar, text="reading metadata…", text_color=MUT, font=ctk.CTkFont(size=11)); meta.pack(side="left")
        ctk.CTkButton(bar, text="▶ Play", width=90, height=28, corner_radius=8, fg_color=CARD2, hover_color=STROKE,
                      text_color=TX, command=lambda: self._play_recording(path, nm)).pack(side="right", padx=(0,14))
        def load():
            import datetime
            thumb=self._video_thumb(path, nm); dur,w,h=self._video_meta(path)
            def show():
                if thumb and os.path.exists(thumb):
                    try:
                        im=Image.open(thumb).convert("RGB")
                        W=max(400, top.winfo_width()-40); H=max(300, top.winfo_height()-110)
                        r=min(W/im.width, H/im.height); im=im.resize((max(1,int(im.width*r)), max(1,int(im.height*r))))
                        self.imgs["vpop"]=ctk.CTkImage(light_image=im, dark_image=im, size=im.size); img_lbl.configure(image=self.imgs["vpop"], text="")
                    except Exception as e: img_lbl.configure(image=None, text="(no preview)"); log_error("vpop-show", e)
                else: img_lbl.configure(image=None, text="No preview frame - press ▶ Play to open it")
                parts=[]
                if w and h: parts.append("%d × %d" % (w, h))
                if dur: parts.append("%d:%02d" % (int(dur)//60, int(dur)%60))
                try: parts.append(human(os.path.getsize(path)))
                except Exception: pass
                stem=os.path.splitext(nm)[0]
                if len(stem)>=14 and stem[:14].isdigit():
                    try: parts.append(datetime.datetime.strptime(stem[:14], "%m%d%Y%H%M%S").strftime("%Y-%m-%d %H:%M:%S"))
                    except Exception: pass
                meta.configure(text="  ·  ".join(parts) or "-")
            self.q.put(("call", show))
        threading.Thread(target=load, daemon=True).start()
    def refresh_screenshots_soft(self):
        """Re-render the capture grid from what's already loaded (refresh the on-PC badges after a pull/copy) without re-reading the device."""
        try: self.render_shots((getattr(self,"_shots_items",[]), getattr(self,"_recs",[])))
        except Exception as e: log_error("shots-soft", e)
    def _del_capture(self, nm):
        """Remove a capture's copies FROM THIS PC - the pulled file in captures/ and the cached thumbnail -
        to the trash (recoverable). The scanner's original is NOT touched; if it's still on the device it
        reappears on the next read (and shows the 'on device' badge)."""
        if not self._confirm("Remove capture", "Remove %s from this PC?\n\nIt goes to the trash (recoverable). The scanner's original is not touched." % nm):
            return
        capdir=os.path.join(self.dest.get() or DEFAULT_DEST, "captures")
        gone=False
        for t in (os.path.join(capdir, nm), os.path.join(THUMBS, "shots", nm)):
            if os.path.exists(t):
                try: subprocess.run(["gio","trash",t], check=False, timeout=10); gone=True
                except Exception as e: log_error("trash-capture", e)
        self._shots_items=[(n,p) for (n,p) in getattr(self,"_shots_items",[]) if n!=nm]   # drop it from the current view now
        self._recs=[(n,p,s) for (n,p,s) in getattr(self,"_recs",[]) if n!=nm]
        self.refresh_screenshots_soft()
        self.set_banner(("Removed %s from this PC (in the trash)" % nm[:24]) if gone else ("%s wasn't saved on this PC" % nm[:24]), MUT)
    def _shot_popout(self, path):
        """Full-size pop-out viewer for a capture: fits the image to the window, ← / → to step through the
        rest, Esc to close. Uses the local cached copies, so it works whether or not the scanner is attached."""
        shots=[p for _,p in getattr(self, "_shots_items", [])] or [path]
        try: top=self._top("Capture viewer", 1120, 860, key="shot")
        except Exception as e: log_error("popout-open", e); return
        body=ctk.CTkFrame(top, fg_color="#0a0c10"); body.pack(fill="both", expand=True)
        img_lbl=ctk.CTkLabel(body, text="", fg_color="#0a0c10"); img_lbl.pack(fill="both", expand=True, padx=10, pady=10)
        bar=ctk.CTkFrame(top, fg_color="transparent"); bar.pack(fill="x", pady=(0,8))
        cap=ctk.CTkLabel(bar, text="", text_color=TX, font=ctk.CTkFont(size=12, weight="bold")); cap.pack(side="left", padx=(14,10))
        meta=ctk.CTkLabel(bar, text="", text_color=MUT, font=ctk.CTkFont(size=11)); meta.pack(side="left")
        st={"i": shots.index(path) if path in shots else 0}
        def show():
            import datetime
            p=shots[st["i"]]
            try:
                im=Image.open(p); fmt=(im.format or "IMG"); im=im.convert("RGB"); ow,oh=im.width, im.height
                W=max(400, top.winfo_width()-40); H=max(300, top.winfo_height()-110)
                r=min(W/ow, H/oh); im=im.resize((max(1,int(ow*r)), max(1,int(oh*r))))
                self.imgs["popout"]=ctk.CTkImage(light_image=im, dark_image=im, size=im.size)
                img_lbl.configure(image=self.imgs["popout"], text="")
                stem=os.path.splitext(os.path.basename(p))[0]; dt=None   # scanner names shots MMDDYYYYHHMMSS.png
                if len(stem)>=14 and stem[:14].isdigit():
                    try: dt=datetime.datetime.strptime(stem[:14], "%m%d%Y%H%M%S")
                    except Exception: dt=None
                if dt is None:
                    try: dt=datetime.datetime.fromtimestamp(os.path.getmtime(p))
                    except Exception: dt=None
                try: fsz=human(os.path.getsize(p))
                except Exception: fsz="?"
                cap.configure(text="%s   ·   %d / %d" % (os.path.basename(p), st["i"]+1, len(shots)))
                meta.configure(text="%d × %d px  ·  %s  ·  %s  ·  %s     (← → step · Esc close)"
                               % (ow, oh, fsz, fmt, dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "date unknown"))
            except Exception as e:
                img_lbl.configure(image=None, text="Couldn't open this image (see Help > Log)"); log_error("popout-show", e)
        def nav(d): st["i"]=(st["i"]+d)%len(shots); show()
        ctk.CTkButton(bar, text="Open file", width=90, height=28, corner_radius=8, fg_color="transparent", border_width=1,
                      border_color=STROKE, hover_color=CARD2, text_color=TX,
                      command=lambda: subprocess.Popen(["xdg-open", shots[st["i"]]])).pack(side="right", padx=(0,14))
        ctk.CTkButton(bar, text="→", width=44, height=28, corner_radius=8, fg_color=CARD2, hover_color=STROKE, command=lambda: nav(1)).pack(side="right", padx=4)
        ctk.CTkButton(bar, text="←", width=44, height=28, corner_radius=8, fg_color=CARD2, hover_color=STROKE, command=lambda: nav(-1)).pack(side="right", padx=4)
        top.bind("<Left>", lambda e: nav(-1)); top.bind("<Right>", lambda e: nav(1)); top.bind("<Escape>", lambda e: top.destroy())
        top.after(80, show)
    def render_shots(self, data):
        """Screenshots live on the device's slow MTP transport: every thumbnail is a full file read
        over that link (and Tk only actually reads the pixels when the image is first drawn, so this
        used to freeze the whole window for as long as all of them took - minutes, once a lot of
        screenshots pile up). Cells appear at once; each thumbnail is decoded in a worker thread and
        the already-decoded picture is dropped in as it arrives, one at a time, main thread untouched."""
        images, recs = data
        self._shots_items=images; self._recs=recs; self._shots_gen=getattr(self, "_shots_gen", 0)+1; gen=self._shots_gen
        # Same CTkScrollableFrame redraw-recursion trigger as render_gallery/render_list/
        # _panel_refresh: unmap before the destroy/rebuild burst.
        self.shots.grid_remove()
        for w in self.shots.winfo_children(): w.destroy()
        try:
            self._render_shots_body(images, recs, gen)
        except Exception as e:
            log_error("render_shots", e)
            for w in self.shots.winfo_children(): w.destroy()
            ctk.CTkLabel(self.shots, text="Couldn't load screenshots (see Help > Log). Try Refresh.", text_color=WARN).grid(row=0, column=0, padx=20, pady=20, sticky="w")
        finally:
            self.shots.grid(); self._fit_scrollbar_later(self.shots, "vertical", 120)
    def _render_shots_body(self, images, recs, gen):
        self.shots_lbl.configure(text="%d screenshot%s · %d recording%s on the device"
                                 % (len(images), "" if len(images)==1 else "s", len(recs), "" if len(recs)==1 else "s"))
        if not images and not recs:
            ctk.CTkLabel(self.shots, text="Nothing found on the device.\n(Take a screenshot or recording on the scanner, then Refresh.)",
                         text_color=MUT, justify="left").grid(row=0,column=0, padx=20, pady=20, sticky="w"); return
        labels={}; base=0
        def section(title, n, top):   # a labelled divider so images and videos read as two distinct groups
            ctk.CTkLabel(self.shots, text="%s  ·  %d" % (title, n), text_color=TX, font=ctk.CTkFont(size=12, weight="bold"),
                         anchor="w").grid(row=base, column=0, columnspan=4, padx=10, pady=(top,4), sticky="w")
        def ceil4(n): return -(-n//4)
        capdir=os.path.join(self.dest.get() or DEFAULT_DEST, "captures")   # a capture is "on PC" once it's been pulled here
        def badge(cell, nm):
            onpc=os.path.exists(os.path.join(capdir, nm))
            ctk.CTkLabel(cell, text=("✓ on PC" if onpc else "on device"), text_color=(OK if onpc else MUT),
                         fg_color=("#173a2a" if onpc else CHIP), corner_radius=6, font=ctk.CTkFont(size=9), height=16).pack(pady=(0,8), ipadx=5)
        def addx(cell, nm):   # a ✕ in the corner, matching the version-chip delete: removes the PC copies to trash
            x=ctk.CTkButton(cell, text="✕", width=22, height=22, corner_radius=6, fg_color="#0a0c10", hover_color="#3a2530",
                            text_color=MUT, font=ctk.CTkFont(size=11), command=lambda n=nm: self._del_capture(n))
            x.place(relx=1.0, rely=0.0, x=-4, y=4, anchor="ne"); self._tip(x, "Remove this capture's copy from this PC (to trash). The scanner's original is not touched.")
        if images:
            section("SCREENSHOTS", len(images), 6); base+=1
            for idx,(nm,path) in enumerate(images):
                r,c=divmod(idx,4)
                cell=ctk.CTkFrame(self.shots, fg_color=CARD2, corner_radius=10); cell.grid(row=base+r,column=c, padx=6, pady=6, sticky="nsew")
                lbl=ctk.CTkLabel(cell, text="loading…", text_color=DIM, width=250, height=150); lbl.pack(padx=8, pady=(8,2), fill="both", expand=True)
                lbl.bind("<Button-1>", lambda e,p=path: self._shot_popout(p)); labels[path]=lbl
                ctk.CTkLabel(cell, text=nm[:24], text_color=MUT, font=ctk.CTkFont(size=10)).pack(pady=(0,2))
                badge(cell, nm); addx(cell, nm)
            base+=ceil4(len(images))
        if recs:
            section("RECORDINGS", len(recs), 14 if images else 6); base+=1
            for idx,(nm,path,sz) in enumerate(recs):
                r,c=divmod(idx,4)
                cell=ctk.CTkFrame(self.shots, fg_color=CARD2, corner_radius=10); cell.grid(row=base+r,column=c, padx=6, pady=6, sticky="nsew")
                ico=ctk.CTkLabel(cell, text="▶", text_color=AC, font=ctk.CTkFont(size=38)); ico.pack(padx=6, pady=(16,2))
                ctk.CTkLabel(cell, text=nm[:20], text_color=TX, font=ctk.CTkFont(size=9)).pack()
                ctk.CTkLabel(cell, text="video · "+human(sz)+" · click to play", text_color=MUT, font=ctk.CTkFont(size=9)).pack(pady=(0,4))
                badge(cell, nm); addx(cell, nm)
                for w in (cell, ico): w.bind("<Button-1>", lambda e,p=path,n=nm: self._video_popout(p, n))   # show first frame + metadata first, play on demand (don't copy the whole file on a click)
            base+=ceil4(len(recs))
        def work():
            for nm,path in images:
                if gen!=self._shots_gen: return                # a newer refresh replaced this one: stop early
                try:
                    im=Image.open(path).convert("RGB"); r=250/im.width; im=im.resize((250,int(im.height*r)))
                except Exception: im=None
                def put(nm=nm, path=path, im=im):
                    if gen!=self._shots_gen: return
                    lbl=labels.get(path)
                    if lbl is None or not lbl.winfo_exists(): return
                    if im is None: lbl.configure(text="(image)")
                    else:
                        self.imgs["shot_"+nm]=ctk.CTkImage(light_image=im, dark_image=im, size=im.size)
                        lbl.configure(image=self.imgs["shot_"+nm], text="")
                self.q.put(("call", put))
        threading.Thread(target=work, daemon=True).start()
    def pull_screenshots(self):
        imgs=getattr(self, "_shots_items", []); recs=getattr(self, "_recs", [])
        if not imgs and not recs:
            self.set_banner("Nothing to pull - hit Refresh first.", MUT); return
        dest=os.path.join(self.dest.get() or DEFAULT_DEST, "captures"); os.makedirs(dest, exist_ok=True)
        self.hold_banner("Pulling screenshots & recordings…", AC)
        threading.Thread(target=self._pull_shots_worker, args=(list(imgs), list(recs), dest), daemon=True).start()
    def _pull_shots_worker(self, imgs, recs, dest):
        n=0; total=len(imgs)+len(recs)
        for i,(nm,path) in enumerate(imgs):
            if i%4==0 or i==len(imgs)-1: self.q.put(("shots_progress", i+1, total, ""))   # screenshots were copied silently; show progress so it never looks frozen
            try: shutil.copyfile(path, os.path.join(dest, nm)); n+=1
            except Exception as e: log_error("pull-shot "+nm, e)
        for j,(nm,path,sz) in enumerate(recs):
            try:
                self.q.put(("shots_progress", len(imgs)+j+1, total, " · recording "+human(sz)))
                shutil.copyfile(path, os.path.join(dest, nm)); n+=1
            except Exception as e: log_error("pull-rec "+nm, e)
        self.q.put(("shots_pulled", (n, dest)))

    def _open_loader(self, title, msg, token=None):
        token=token or self._next_job("loader")
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
        self._loader=t; self._loader_token=token
        return token
    def _close_loader(self, token=None):
        if token is not None and token!=getattr(self, "_loader_token", None): return
        t=getattr(self,"_loader",None)
        if t:
            try: t.destroy()
            except Exception: pass
            self._loader=None; self._loader_token=None

    # ---- export zip ----
    def on_export_zip(self):
        if self.pulling: return
        sel=[n for n,v in self.pull_sel.items() if v.get()]
        if not sel:
            self.set_banner("Tick the project(s) you want to zip.", WARN); return
        dest=self.dest.get() or DEFAULT_DEST
        try: sizes=self._estimate_sizes(sel, dest)
        except Exception: sizes=None
        mode,scope=self._ask_zip_format(sizes)
        if not mode: return
        self._zip_scope=scope
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
        self._start_zip(sel, dest, mode, scope)
    def _ask_zip_format(self, sizes=None):
        """Choose what goes in the zip. Returns (mode, scope): mode in stl/obj/glb/models/all (or None if
        cancelled), scope in 'current'/'all'. Scope only affects the model modes; 'Everything' is the full
        archive regardless."""
        t=ctk.CTkToplevel(self); t.title("Export ZIP"); t.configure(fg_color=BG); t.resizable(False,False)
        try: t.transient(self); t.attributes("-topmost",True)
        except Exception: pass
        w,h=470,470
        try:
            self.update_idletasks()
            x=self.winfo_rootx()+(self.winfo_width()-w)//2; y=self.winfo_rooty()+(self.winfo_height()-h)//3
            t.geometry("%dx%d+%d+%d"%(w,h,x,y))
        except Exception: pass
        res={"v":None}
        scope=ctk.StringVar(value="current")
        card=ctk.CTkFrame(t, fg_color=CARD, corner_radius=14); card.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(card, text="Export ZIP", font=ctk.CTkFont(family=WORDMARK, size=15,weight="bold"), text_color=TX).pack(anchor="w", padx=18, pady=(16,2))
        ctk.CTkLabel(card, text="What should go in the zip? Files are added flat with clean names.",
                     text_color=MUT, font=ctk.CTkFont(size=12), wraplength=410, justify="left").pack(anchor="w", padx=18, pady=(0,8))
        # version scope: the model modes ship the version you picked per scan by default, or every version
        sr=ctk.CTkFrame(card, fg_color="transparent"); sr.pack(fill="x", padx=16, pady=(0,8))
        ctk.CTkLabel(sr, text="Versions:", text_color=MUT, font=ctk.CTkFont(size=12)).pack(side="left", padx=(0,8))
        _seg=ctk.CTkSegmentedButton(sr, values=["Current","All versions"],
                               command=lambda v: scope.set("current" if v=="Current" else "all"),
                               fg_color=CARD2, selected_color=AC, selected_hover_color=AC_H,
                               unselected_color=CARD2, text_color=TX, height=28)
        _seg.set("Current"); _seg.pack(side="left")
        ctk.CTkLabel(sr, text="which version of each scan", text_color=MUT, font=ctk.CTkFont(size=10)).pack(side="left", padx=8)
        def pick(v): res["v"]=v; t.destroy()
        opts=[("STL only","stl","for 3D printing"),("OBJ only","obj","for editing"),
              ("GLB only","glb","for the web / editing"),
              ("All models","models","every PLY, STL, OBJ, GLB"),
              ("Everything","all","full archive: every version, previews, metadata")]
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
        t.protocol("WM_DELETE_WINDOW", lambda: pick(None))   # same fix as _modal(): closing via the X button must still release the grab
        try:
            t.grab_set(); t.wait_window()
        except Exception: pass
        finally:
            try: t.grab_release()
            except Exception: pass
        return res["v"], scope.get()
    def _mesh_cloud_sources(self, name, dest):
        """(mesh_plys, cloud_plys) for a project: local flat > local nested > device nested."""
        local=os.path.join(dest, name)
        m=[p for p in glob.glob(os.path.join(local, name+"_*.ply")) if not p.endswith("_cloud.ply") and not p.endswith(".tmp.ply")]
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
    def _start_zip(self, sel, dest, mode, scope="current"):
        self.pulling=True
        self._btn_busy(self.zip_btn, "Zipping…")
        self.progress.grid(row=1,column=0, columnspan=4, sticky="ew", pady=(8,0)); self.progline.grid(row=2,column=0, columnspan=4, sticky="w", padx=(20,0), pady=(0,10))
        threading.Thread(target=self._zip_worker, args=(sel,dest,mode,scope), daemon=True).start()
    def _project_meshes(self, base, name):
        """Mesh .ply files for an imported project (flat layout, else nested mirror)."""
        flat=[p for p in glob.glob(os.path.join(base, name+"_*.ply")) if not p.endswith("_cloud.ply") and not p.endswith(".tmp.ply")]
        if flat: return sorted(flat)
        return sorted(glob.glob(os.path.join(base, "data", "*", "fuse_mesh.ply")))
    def _scope_meshes(self, base, name, scope):
        """Mesh plys to export, honoring the version scope: 'current' = only the version picked per scan
        (what the UI shows), 'all' = every version file on disk. Falls back to all if nothing resolves."""
        if scope!="current": return self._project_meshes(base, name)
        out=[]
        for node in self._proc_nodes(name):
            cur=self._proc_current(name, node)
            if cur and cur[2] and os.path.exists(cur[2]): out.append(cur[2])
        # No silent fall-back to every version: if a "current" scope resolves nothing, the caller reports an
        # empty export rather than quietly shipping ALL versions the user didn't ask for.
        return sorted(set(out))
    def _zip_worker(self, sel, dest, mode, scope="current"):
        import zipfile
        # everything is inside the try, incl. makedirs: a bad destination must post a zipfail and clear
        # the busy state, not throw out of the thread and leave the ZIP button stuck disabled.
        files=[]; zfails=0
        try:
            os.makedirs(dest, exist_ok=True)
            tag={"stl":"stl","obj":"obj","glb":"glb","models":"models","all":"full"}.get(mode,mode)
            if len(sel)==1:
                zpath=os.path.join(dest, "%s_%s.zip"%(sel[0], tag))
            else:
                zpath=os.path.join(dest, "pointyoink-%s-%s.zip"%(tag, time.strftime("%Y%m%d-%H%M%S")))
            # build the file list (src, arcname). flat for models/format modes; nested for 'all'
            for name in sel:
                base=os.path.join(dest, name)
                if mode in ("stl","obj","glb"):
                    for ply in self._scope_meshes(base, name, scope):
                        # name flat & unique: <name>_<node>.<ext>
                        node=os.path.basename(os.path.dirname(ply)) if os.sep+"data"+os.sep in ply else os.path.basename(ply)[:-4]
                        stem=node if node.startswith(name) else "%s_%s"%(name,node)
                        target=os.path.join(os.path.dirname(ply), stem+"."+mode)
                        # re-convert when the target is missing OR older than its source PLY, so a changed
                        # model is never shipped as a stale export; convert in a memory-capped child.
                        if not os.path.exists(target) or os.path.getmtime(target) < os.path.getmtime(ply):
                            self.q.put(("prog", 0.0, "Converting %s to %s…"%(name, mode.upper())))
                            if not self._convert_subprocess(ply, target): zfails+=1; continue
                        files.append((target, os.path.basename(target)))
                elif mode=="models" and scope=="current":
                    # only the version picked per scan: its ply, any same-stem converted file, and point clouds
                    keep={os.path.splitext(os.path.basename(p))[0] for p in self._scope_meshes(base, name, "current")}
                    for f in glob.glob(os.path.join(base,"*")):
                        if f.endswith(".tmp.ply") or not f.lower().endswith((".ply",".stl",".obj",".glb")): continue
                        st=os.path.splitext(os.path.basename(f))[0]
                        if st in keep or f.endswith("_cloud.ply"): files.append((f, os.path.basename(f)))
                    for p in self._scope_meshes(base, name, "current"):      # nested device-mirror layout
                        if os.sep+"data"+os.sep in p:
                            node=os.path.basename(os.path.dirname(p)); stem,ext=os.path.splitext(os.path.basename(p))
                            files.append((p, "%s_%s_%s%s" % (name, node, stem, ext)))
                elif mode=="models":
                    for f in glob.glob(os.path.join(base,"*")):
                        if f.lower().endswith((".ply",".stl",".obj",".glb")) and not f.endswith(".tmp.ply"): files.append((f, os.path.basename(f)))
                    for f in glob.glob(os.path.join(base,"data","*","*")):
                        # nested scans all have the same file names (fuse_mesh.ply): make each entry unique
                        if f.lower().endswith((".ply",".stl",".obj",".glb")) and not f.endswith(".tmp.ply"):
                            node=os.path.basename(os.path.dirname(f)); stem,ext=os.path.splitext(os.path.basename(f))
                            files.append((f, "%s_%s_%s%s" % (name, node, stem, ext)))
                else:  # all
                    for root,dirs,fs in os.walk(base):
                        dirs[:]=[d for d in dirs if d!="cache"]
                        for f in fs:
                            if f.endswith(".tmp.ply"): continue      # never ship a half-written Prepare temp file
                            fp=os.path.join(root,f); files.append((fp, os.path.join(name, os.path.relpath(fp, base))))
            if not files:
                self.q.put(("zipfail", ("%d conversion(s) failed, nothing to zip - see Help > Log" % zfails) if zfails
                            else ("no current model version to export - pick a model for each scan, or choose All versions" if scope=="current"
                                  else "no matching files (try importing with that format first)"))); return
            total=len(files)
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for i,(fp,arc) in enumerate(files):
                    self.q.put(("prog", i/total, "Zipping %d/%d - %s"%(i+1,total,os.path.basename(fp))))
                    try: z.write(fp, arc)
                    except Exception as e: zfails+=1; log_error("zip "+arc, e)
            self.q.put(("zipped", zpath, os.path.getsize(zpath), zfails))
        except Exception as e:
            log_error("zip", e); self.q.put(("zipfail", str(e)))
    def _finish(self, dest, failed, cancelled=False, no_models=None):
        no_models=no_models or []
        self.pulling=False; self.cancel_btn.grid_remove(); self._bottom_refresh()
        try:
            if self.progress.cget("mode")=="indeterminate": self.progress.stop(); self.progress.configure(mode="determinate")
        except Exception: pass
        self.progress.set(0); self.progress.grid_remove(); self._close_import_popup()
        if cancelled: self.progline.configure(text="Cancelled."); self.set_banner("Import cancelled.", WARN)
        else:
            # record + warm every project that actually imported, even if others in the batch failed
            # (this used to run only in the all-success path, so a partly-failed batch left its successes
            # unrecorded, un-warmed, and unable to show as "imported" or detect future changes)
            done=[n for n in getattr(self, "_pull_list", []) if n not in failed and os.path.isdir(os.path.join(dest, n))]
            for n in done:
                p=self._proj(n) or {}
                self.records.setdefault(n, {}).update(
                    imported_to=os.path.join(dest, n), imported_at=int(time.time()),
                    sig={"edit_time":p.get("edit_time"), "nodes":p.get("nodes"), "meshes":p.get("meshes")})
            self._persist()
            self._warm_imported(done)   # render the just-imported scans' thumbnails so the strip isn't blue on first open
            ef=getattr(self, "_export_fails", [])
            if failed:
                self.progline.configure(text="Done with errors: "+", ".join(failed))
                self.set_banner("%d project%s imported, %d failed - see Help > Log." % (len(done), "" if len(done)==1 else "s", len(failed)), WARN)
                if self._confirm("Some imports failed", "%d imported. These failed:\n%s\n\nRetry the failed ones?" % (len(done), "\n".join(failed))):
                    for n,v in self.pull_sel.items(): v.set(n in failed)
                    self.on_pull(); return
            elif no_models:    # "models only" found nothing built yet - the .revo/metadata still copied, but nothing to show for it
                names=", ".join(self.disp(n) for n in no_models)
                self.progline.configure(text="Imported, but no models yet: "+names)
                self.set_banner("%s %s no built model on the scanner yet - build it there, or import the full project instead."
                                % (names, "has" if len(no_models)==1 else "have"), WARN)
            elif ef:
                self.progline.configure(text="Imported, but %d export(s) failed."%len(ef))
                self.set_banner("Import done, but %d file(s) failed to export - see Help > Log: %s"
                                % (len(ef), ", ".join(ef[:3]) + ("…" if len(ef)>3 else "")), WARN)
            else:
                self.progline.configure(text="Done."); self.set_banner("Import complete.", OK)
            self.after(6000, lambda: self.progline.winfo_exists() and self.progline.grid_remove())
            self._select_after_list=done[0] if done else None        # then jump to Projects and show what just arrived
            self.projects_sig=None; self.gallery_cache={}; self.listed=False; self.start_listing()   # new projects appear
            za=getattr(self, "_zip_after", None)
            if za:
                self._zip_after=None
                mode=getattr(self,"_zip_mode","models"); self._zip_mode=None
                scope=getattr(self,"_zip_scope","current")
                self._start_zip([n for n in za if os.path.isdir(os.path.join(dest,n))], dest, mode, scope); return
            if self.auto_open.get(): self.open_folder()

    # ---- queue ----
    def _slow_watch(self, kind, t0):
        """Log queue handlers that actually spend too long on the UI thread."""
        dt=time.time()-t0
        if dt>0.15: log_line("slow ui: %s handler took %.0f ms" % (kind, dt*1000))
    def drain_loop(self):
        more=False; processed=0; deadline=time.monotonic()+0.045
        try:
            while processed<64 and time.monotonic()<deadline:
                kind,*rest=self.q.get_nowait(); processed+=1
                _t_ev=time.time()
                try:
                    self._handle_event(kind, rest)
                except Exception as e:
                    # One bad event must never kill the pump: everything the app shows (the project list, the
                    # splash closing, thumbnails, WiFi/build progress) depends on this loop rescheduling itself.
                    # Before this fix an uncaught exception here propagated out of drain_loop and silently
                    # stopped it forever - the window would sit frozen (the splash never closes, nothing ever
                    # updates again) with no error visible anywhere but the log.
                    log_error("drain_loop event %r" % (kind,), e)
                finally:
                    self._slow_watch(kind, _t_ev)
        except queue.Empty: pass
        finally:
            try: more=not self.q.empty()
            except Exception: more=False
            self.after(10 if more else 80, self.drain_loop)      # bounded pump: large bursts yield back to Tk
    def _handle_event(self, kind, rest):
                if kind=="mounted":
                    ok,msg=rest; self._mounting=False; self._device_mounted=ok
                    if ok:
                        self.listed=False; self.projects_sig=None; self._user_mount=False
                        self.set_banner("Connected - reading scanner projects…", AC)
                        self.start_listing("device")
                    else:
                        self.set_banner("Couldn't connect: "+msg, WARN); log_line("mount failed: "+msg)
                        if getattr(self, "_user_mount", False):   # they clicked Connect and it failed: show the walkthrough
                            self._user_mount=False; self.after(400, self._usb_help)
                elif kind=="refresh_probe":
                    self._refresh_probe_done(*rest)
                elif kind=="listing_progress":
                    i,t=rest; self.hold_banner("Reading scanner projects… %d of %d" % (i, t), AC)   # live count = obviously working, not frozen (MTP is just slow)
                elif kind=="projects":
                    self.listing=False; self.listed=True; self.listed_src=self._listing_src; self.set_status("")   # clear "Refreshing projects…" - done, or it lingers as a fake perpetual-loading label
                    if self.listed_src=="device":                    # flip the "reading… N of M" banner to a clear DONE state instead of sticking at "10 of 10"
                        self._dev_hold=0.0; n=len(rest[0])
                        self.set_banner("Connected · read %d project%s - tick scans to import" % (n, "" if n==1 else "s"), OK)
                    if not getattr(self, "_first_listed", False):
                        self._first_listed=True
                        if self.listed_src=="local" and any(p.get("local") for p in rest[0]): self._set_mode("Local")   # no scanner: start on what is on this PC
                    self.render_list(rest[0])
                    if self.mode_frames.get("Process") and self.mode_frames["Process"].winfo_ismapped(): self._proc_refresh()
                    elif getattr(self, "page", "import")=="projects": self._schedule_panel_refresh(20)
                    else: self._proc_dirty=True
                    if not getattr(self, "_first_render_done", False):
                        self._first_render_done=True                      # the splash may go now (see _close_splash)
                    jump=getattr(self, "_select_after_list", None)
                    if jump:
                        self._select_after_list=None; self._set_mode("Local")
                        if any(p["name"]==jump for p in self.projects): self.select_project(jump)
                        self._folder_loaded=True; self.refresh_folder()
                    if self.page=="projects": self._panel_refresh()
                    # Device screenshots are explicit-only. Auto-loading them here made every startup
                    # touch the same MTP/FUSE mount that can stall USB input when jmtpfs is unhealthy.
                elif kind=="projects_failed":
                    self.listing=False; self.set_status(""); self.set_banner("Couldn't refresh projects - see Help > Log.", WARN)
                elif kind=="sizes": self.projects_sig=None; self.update_summary()
                elif kind=="shaded":
                    key,mode,out=rest
                    if out is None: self._shade_failed.add((key,mode))
                    else: self._shade_failed.discard((key,mode))   # a success clears the fail-mark, or one transient render error blocks this scan's preview until app restart
                    if (key,mode)==self._shade_key:
                        if out: self._show_shaded(out)
                        else: self.big_hint.configure(text="Scanner's own preview · could not draw the 3D model (see Help > Log)"); self._preview_idle()
                    if mode=="solid" and out:                       # upgrade this project's list thumbnail to the shaded render, in place (no re-render)
                        nm=key.rsplit("__",2)[0]; row=self.rows.get(nm)   # key is name__node__verkey (3 parts since the verkey was added); rows are keyed by bare project name
                        lbl=getattr(row, "_thumb_lbl", None) if row is not None else None
                        if lbl is not None:
                            try: self.imgs["row_"+nm]=cimg(out,54); lbl.configure(image=self.imgs["row_"+nm], text="")
                            except Exception: pass
                elif kind=="shade_msg":
                    if self._shade_key and rest[0]==self._shade_key[0]: self.big_hint.configure(text=rest[1])
                elif kind=="prewarm_msg":                       # subtle footer note; never overrides a real operation's status
                    txt=rest[0]
                    if txt:
                        if not self._status_msg or getattr(self, "_prewarm_owns", False): self._status_msg=txt; self._prewarm_owns=True
                    elif getattr(self, "_prewarm_owns", False): self._status_msg=None; self._prewarm_owns=False
                elif kind=="mesh_stats":
                    key,st=rest; self._mesh_stats[key]=st
                    if self._shade_key and key==self._shade_key[0]: self._show_stats(st)
                elif kind=="base_geom":
                    path,sig,r=rest; self._base_busy.discard(path)
                    # always cache, even an unknown (r is None): otherwise a tiny/failed model re-triggers
                    # detection on every panel refresh. 'present' None == couldn't tell.
                    self._base_geom[path]={"sig": sig, "present": (r.get("present") if r else None)}
                    if self.page=="projects" and not getattr(self, "_in_edit_mode", False):
                        self._next_refresh(); self._panel_refresh()   # the table verdict changes the NEXT step and the card line
                elif kind=="gallery": n,items=rest; self.gallery_cache[n]=items; self.render_gallery(n,items)
                elif kind=="film_thumb":                       # a scan's shaded strip thumbnail is ready: swap out the blue preview
                    n,node,out=rest
                    im=self._film_imgs.get(node) if self.selected==n else None
                    if im is not None:
                        try: self.imgs["g_"+n+node]=cimg(out,100); im.configure(image=self.imgs["g_"+n+node])
                        except Exception: pass
                elif kind=="files":
                    n,(tot,files)=rest
                    if self.selected==n: self._set_files_rows(name=n, files=files, tot=tot)
                elif kind=="prog":
                    frac=rest[0]; line=rest[1]; rate=rest[2] if len(rest)>2 else None
                    if "Converting" in line:                       # conversion has no % - animate instead of sitting at 99%
                        if self.progress.cget("mode")!="indeterminate":
                            self.progress.configure(mode="indeterminate"); self.progress.start()
                    else:
                        if self.progress.cget("mode")=="indeterminate":
                            self.progress.stop(); self.progress.configure(mode="determinate")
                        self.progress.set(frac)
                    self.progline.configure(text=line)
                    if rate is not None:
                        try: self._imp_graph_add(frac, rate)   # draw the speed graph (same as WiFi) from the rsync rate
                        except Exception: pass
                    if getattr(self, "_imp_top", None):        # fill the transfer popup's subtitle + stat tiles
                        try:
                            self.imp_sub.configure(text=line)
                            mg=re.search(r"·\s*([\d.]+\s*[KMGT]?B)\b", line)
                            if mg: self.imp_stats["got"].configure(text=mg.group(1))
                            ms=re.search(r"scan\s*(\d+/\d+)", line)
                            if ms: self.imp_stats["scans"].configure(text=ms.group(1))
                            else:
                                # full-project rsync has no per-scan progress; show the project's total scan
                                # count instead of a bare "-" (which reads as broken).
                                mn=re.search(r"·\s*(Project\S+)\s*·", line)
                                pj=next((x for x in self.projects if x.get("name")==mn.group(1)), None) if mn else None
                                if pj and pj.get("nodes"): self.imp_stats["scans"].configure(text=str(pj["nodes"]))
                            me=re.search(r"([\d:]+)\s*left", line)   # was (\d+:\d+): dropped the hours on a 1:28:02 ETA, so the tile showed 28:02
                            if me: self.imp_stats["eta"].configure(text=me.group(1))
                        except Exception: pass
                elif kind=="done": self._finish(rest[0],rest[1], no_models=rest[2] if len(rest)>2 else [])
                elif kind=="cancelled": self._finish(rest[0],rest[1], cancelled=True, no_models=rest[2] if len(rest)>2 else [])
                elif kind=="zipped":
                    zpath,sz,zfails=rest; self.pulling=False; self._btn_idle(self.zip_btn)
                    self.progress.set(0); self.progress.grid_remove()
                    self.progline.configure(text="Zipped -> %s (%s)"%(os.path.basename(zpath), human(sz)))
                    if self.auto_open.get(): subprocess.Popen(["xdg-open", os.path.dirname(zpath)])   # same option as imports
                    if zfails:
                        self.set_banner("ZIP ready, but %d file(s) failed - see Help > Log."%zfails, WARN)
                    else:
                        self.set_banner("ZIP ready in your save folder.", OK)
                    if self.auto_open.get(): self.open_folder()
                elif kind=="zipfail":
                    self.pulling=False; self._btn_idle(self.zip_btn); self.progress.grid_remove()
                    self.set_banner("ZIP failed: "+rest[0], WARN)
                elif kind=="loader_msg":
                    token,msg=(rest[0], rest[1]) if len(rest)>1 else (None, rest[0])
                    if token is not None and token!=getattr(self, "_loader_token", None): return
                    if getattr(self,"_loader_msg",None):
                        try: self._loader_msg.configure(text=msg)
                        except Exception: pass
                elif kind=="view_done":
                    token,err=(rest[0], rest[1]) if len(rest)>1 else (None, rest[0])
                    if token is not None and token!=getattr(self, "_view_job", None): return
                    self._close_loader(token); self.set_status("")
                    if err: self.set_banner("3D view failed - see Help > Log.", WARN)
                elif kind=="status":
                    self.set_status(rest[0])
                elif kind=="open3d_checked":
                    self._open3d_probe_busy=False
                    if self._status_msg=="Checking Open3D…": self.set_status("")
                elif kind=="shots":
                    self._shots_busy=False; self._shots_loaded=True; self._dev_hold=0.0   # loaded once; release the "Reading…" hold and let the probe own the device banner
                    self.render_shots(rest[0]); self.set_status("")   # the capture count lives in the panel header, NOT the device banner (or it leaks onto Import/Projects)
                elif kind=="shots_offline":
                    self._shots_busy=False; self._shots_loaded=True; self._dev_hold=0.0
                    self.render_shots(rest[0]); self.set_status("")
                elif kind=="shots_unmounted":
                    self._shots_busy=False; self.set_status(""); self._dev_hold=0.0
                    self.set_banner("No saved captures yet · connect the scanner over USB (tap File Transfer).", WARN)
                    try:
                        self.shots.grid_remove()
                        for w in self.shots.winfo_children(): w.destroy()
                        ctk.CTkLabel(self.shots, text="No captures on this PC yet.\nConnect the scanner over USB (tap File Transfer), then Refresh.",
                                     text_color=MUT, justify="left").grid(row=0,column=0, columnspan=4, padx=20, pady=20, sticky="w")
                        self.shots.grid()
                    except Exception: pass
                elif kind=="shots_failed":
                    self._shots_busy=False; self.set_status(""); self.set_banner("Couldn't read screenshots - see Help > Log.", WARN)
                elif kind=="shots_progress":
                    i,t=rest[0],rest[1]; extra=rest[2] if len(rest)>2 else ""
                    self.hold_banner("Pulling captures… %d of %d%s" % (i, t, extra), AC)
                elif kind=="shots_pulled":
                    n, d = rest[0]; self.set_status(""); self._dev_hold=0.0
                    self.set_banner("Pulled %d capture%s → %s" % (n, "" if n==1 else "s", d), OK)
                    self.refresh_screenshots_soft()   # flip the "on device" badges to "on PC" now they're saved
                    if self.auto_open.get(): subprocess.Popen(["xdg-open", d])
                elif kind=="fuse_node": self._proc_progress(rest[0], rest[1], rest[2])
                elif kind=="splash_preload":
                    done,total=rest
                    if getattr(self, "_splash", None):
                        try:
                            self._sp_cv.itemconfigure(self._sp_status, text="Preloading previews…  %d / %d" % (done, total), fill=MUT)
                            frac=done/max(1,total)
                            self._sp_cv.coords(self._sp_fill, self._sp_px0, self._sp_py, self._sp_px0+int(self._sp_pw*frac), self._sp_py+self._sp_ph)
                        except Exception: pass
                elif kind=="splash_preload_done":
                    self.after(120, self._close_splash)
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
                    job_name,payload=(rest[0], rest[1]) if len(rest)>1 else (getattr(self, "_fuse_name", None), rest[0])
                    self._close_loader(); self._fusing=False
                    try: self._btn_idle(self.proc_btn)
                    except Exception: pass
                    status, info = payload
                    if status=="ok":
                        n=len(info.split(", ")); self.set_banner("Built %d 3D model%s on this PC" % (n, "" if n==1 else "s"), OK); self.set_status("3D model%s built: %s" % ("" if n==1 else "s", info))
                        d=getattr(self,"_dialogs",{}).get("align")          # a finished Combine: close its dialog instead of leaving it up still saying "Combine"
                        try:
                            if d is not None and d.winfo_exists(): self._dialogs.pop("align",None); d.destroy()
                        except Exception: pass
                        self.projects_sig=None; self.gallery_cache={}; self._mesh_stats={}
                        sel=self.selected; self.listed=False; self.start_listing()
                        if sel: self.after(1500, lambda s=sel: (self.select_project(s) if s in [p["name"] for p in self.projects] else None))
                        if self.auto_open.get(): self.open_folder()
                    else:
                        self.set_banner("Building the model failed - see Help > Log. %s" % (info or ""), WARN); self.set_status("")
                        if self.selected: self._proc_render(self.selected)
                elif self.live_on_queue(kind, rest): pass   # dev live-view events (no-op in shipped builds)
                elif kind=="wifi": self._wifi_event(rest[0], rest[1])
                elif kind=="loader_close":
                    self._close_loader(rest[0] if rest else None)
                elif kind=="base_done":
                    token,payload=(rest[0], rest[1]) if len(rest)>1 else (None, rest[0])
                    if token is not None and token!=getattr(self, "_base_job", None): return
                    self._close_loader(token); self._basing=False
                    try: self._btn_idle(self.base_btn)
                    except Exception: pass
                    status, info = payload[0], payload[1]
                    if status=="ok":
                        node=payload[2] if len(payload)>2 else None; plane=payload[3] if len(payload)>3 else None; nm=payload[4] if len(payload)>4 else self.selected
                        if nm and node and plane:
                            self.records.setdefault(nm,{}).setdefault("base_plane",{})[node]=plane; self._persist()
                        self.set_banner("Base removed from %s: saved as the prepared version%s." % (self._scan_label(nm, node) if (nm and node) else "the model", ", and the cut is remembered for combining" if plane else ""), OK)
                        self.set_status("")
                        self.projects_sig=None; self._mesh_stats={}; self.gallery_cache.pop(nm, None)
                        if nm and node:
                            self.records.setdefault(nm,{}).setdefault("current",{})[node]="clean"; self._persist()
                            if self.selected==nm:
                                self._proc_render(nm); self._mv_key=None; self._maybe_schedule_shaded(nm, node, 250)
                            else: self._proc_refresh()
                        else: self._proc_refresh()
                    elif status=="cancel":
                        self.set_banner("Base removal cancelled.", MUT); self.set_status("")
                    else:
                        self.set_banner("Base removal failed - see Help > Log.", WARN); self.set_status("")

if __name__ == "__main__":
    if not _acquire_single_instance():
        _show_single_instance_error()
        _sys.exit(2)
    App().mainloop()
