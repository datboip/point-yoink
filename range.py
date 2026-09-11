#!/usr/bin/env python3
# Revopoint RANGE (tethered, USB 2207:110c) source for PointYoink - native Linux, no SDK.
# The RANGE is a standard UVC camera that computes depth on-board; it just needs its
# projector switched on over a UVC Extension Unit. Everything here is v4l2 + one ioctl.
#   depth stream : depth node 'Y16 ' 640x800 = depth (u16, x0.1 mm, 0 = invalid) + left IR + right IR
#   color stream : rgb node MJPG 1280x800 (separate camera, streams at the same time)
#   control      : UVC XU unit 4 - selector 7 EXECUTE (60-byte NUL-padded shell command),
#                  selectors 1/2 file read (see XU.read_file)
#   calibration  : /data/camparam/Pl.bin on the scanner (u16 w, u16 h, 3x3 f32 K)
# Needs v4l-utils (v4l2-ctl). The RANGE draws 5V/1A: a USB 2.0 hub port (500 mA) makes it
# reset when the projector fires - use a direct motherboard port. Stopping a stream makes
# the scanner reboot itself (~10 s), see _PipeStream.
import os, glob, struct, fcntl, ctypes, time, subprocess, threading
import numpy as np

VID, PID = "2207", "110c"
W, H = 640, 400
FRAME_BYTES = W * H * 2
DEPTH_SCALE = 0.1                    # mm per raw unit
IOC_XU = 0xC0107521                  # UVCIOC_CTRL_QUERY
SET_CUR, GET_CUR, GET_LEN = 0x01, 0x81, 0x85
XU_UNIT = 4
SEL_START_FINISH, SEL_DATA, SEL_EXECUTE = 1, 2, 7
HEADER_PIXELS = 40                   # per-frame metadata lives in row 0's first pixels

class _XUQ(ctypes.Structure):
    _fields_ = [("unit", ctypes.c_uint8), ("selector", ctypes.c_uint8), ("query", ctypes.c_uint8),
                ("size", ctypes.c_uint16), ("data", ctypes.POINTER(ctypes.c_uint8))]

def _revo_nodes():
    """/dev/video nodes that belong to the RANGE (v4l2-ctl groups them under 'REVO_PRODUCT')."""
    try:
        out = subprocess.run(["v4l2-ctl", "--list-devices"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    nodes, block = [], False
    for ln in out.splitlines():
        if ln and not ln[0].isspace(): block = "REVO" in ln.upper()
        elif block and "/dev/video" in ln: nodes.append(ln.strip())
    return nodes

def _node_with(fmt, nodes):
    for n in nodes:
        try:
            if fmt in subprocess.run(["v4l2-ctl", "-d", n, "--list-formats"], capture_output=True, text=True, timeout=5).stdout: return n
        except Exception:
            pass
    return None

def find_device():
    """-> {"usb_path","on_hub","node","rgb_node","serial"} or None. on_hub = behind a hub (power risk)."""
    for d in glob.glob("/sys/bus/usb/devices/*/"):
        try:
            if open(d + "idVendor").read().strip() != VID or open(d + "idProduct").read().strip() != PID:
                continue
            path = os.path.basename(d.rstrip("/"))
            serial = open(d + "serial").read().strip() if os.path.exists(d + "serial") else ""
            on_hub = "." in path.split("-", 1)[1] if "-" in path else False   # "1-5.2" hub vs "3-4" root
            nodes = _revo_nodes()
            return {"usb_path": path, "on_hub": on_hub, "node": _node_with("Y16", nodes),
                    "rgb_node": _node_with("MJPG", nodes), "serial": serial}
        except Exception:
            continue
    return None

class XU:
    """Control channel over the UVC Extension Unit, via the uvcvideo ioctl (no driver detach)."""
    def __init__(self, node): self.node = node
    def _q(self, sel, query, buf):
        fd = os.open(self.node, os.O_RDWR)
        try:
            arr = (ctypes.c_uint8 * 60)(*buf)
            fcntl.ioctl(fd, IOC_XU, _XUQ(XU_UNIT, sel, query, 60, arr))
            return bytes(arr)
        finally:
            os.close(fd)
    @staticmethod
    def _pad(b): return b + b"\x00" * (60 - len(b))
    def execute(self, cmd):
        """Run a shell command on the scanner (e.g. 'echo s 0x922 1 >/dev/rk_preisp')."""
        self._q(SEL_EXECUTE, SET_CUR, self._pad(cmd.encode()))
    def read_file(self, path, max_blocks=512):
        self._q(SEL_START_FINISH, SET_CUR, self._pad(bytes([1]) + path.encode() + b"\x00"))
        out = b""
        try:
            for _ in range(max_blocks):
                blk = self._q(SEL_DATA, GET_CUR, b"\x00" * 60)
                n = int.from_bytes(blk[:4], "little")
                if n == 0 or n > 56: break
                out += blk[4:4 + n]
        finally:
            self._q(SEL_START_FINISH, SET_CUR, b"\x00" * 60)
        return out
    def firmware(self):
        try: return self.read_file("/tmp/inited").split(b"\x00")[0].decode(errors="replace")
        except OSError: return ""
    def projector(self, on):
        v = "1" if on else "0"
        for reg in ("0xb00", "0xb01", "0x922"):     # LED master, IR projector, laser enable
            self.execute("echo s %s %s >/dev/rk_preisp" % (reg, v)); time.sleep(0.3)
    def intrinsics(self):
        pl = self.read_file("/data/camparam/Pl.bin")
        if len(pl) < 40: raise RuntimeError("could not read Pl.bin (%d bytes)" % len(pl))
        cw, ch = struct.unpack_from("<HH", pl, 0); m = struct.unpack_from("<9f", pl, 4)
        sx, sy = W / cw, H / ch
        return {"calib_w": cw, "calib_h": ch, "fx": m[0] * sx, "fy": m[4] * sy, "cx": m[2] * sx, "cy": m[5] * sy}

class _PipeStream:
    """Raw frames piped from `v4l2-ctl --stream-to=-`; subclasses parse the byte stream.
    IMPORTANT: the RANGE reboots itself ~2 s after ANY stream is stopped (bench-verified,
    with or without XU traffic). It re-enumerates and is usable again after ~10 s. So:
    open the streams once per session, switch the projector off WHILE streaming, and treat
    stop() as "power-cycle the scanner"."""
    def __init__(self, node, fmt, w, h):
        self.node = node; self.fmt = fmt; self.w = w; self.h = h
        self.proc = None; self.latest = None; self.count = 0; self._on = False
    def start(self):
        self.proc = subprocess.Popen(
            ["v4l2-ctl", "-d", self.node, "--set-fmt-video", "width=%d,height=%d,pixelformat=%s" % (self.w, self.h, self.fmt),
             "--stream-mmap", "--stream-to=-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self._on = True
        threading.Thread(target=self._pump, daemon=True).start()
    def _pump(self):
        buf = b""; out = self.proc.stdout
        while self._on:                       # drain until EOF (v4l2-ctl exited) - see stop()
            chunk = out.read(65536)
            if not chunk: break
            buf = self._feed(buf + chunk)
        self._on = False
    def _feed(self, buf): return b""
    def stop(self):
        # SIGINT lets v4l2-ctl do a clean STREAMOFF. The pump keeps draining stdout until the
        # process exits so it can never block on a full pipe and miss the signal.
        import signal
        p = self.proc
        if p:
            try:
                p.send_signal(signal.SIGINT)
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.send_signal(signal.SIGINT); p.wait(timeout=5)
            except Exception:
                try: p.kill()
                except Exception: pass
        self._on = False; self.proc = None

class DepthStream(_PipeStream):
    """'Y16 ' 640x800 on the depth node = three images stacked per frame:
    rows 0-399 depth (uint16, x0.1 mm), then left IR (uint8), then right IR (uint8).
    .latest = depth (H,W) uint16, .ir_left / .ir_right = (H,W) uint8."""
    FRAME = W * H * 2 + W * H * 2            # depth u16 + two u8 IR images
    def __init__(self, node):
        super().__init__(node, "Y16 ", W, 2 * H); self.ir_left = None; self.ir_right = None
    def _feed(self, buf):
        while len(buf) >= self.FRAME:
            fr = buf[:self.FRAME]; buf = buf[self.FRAME:]
            d = np.frombuffer(fr, dtype=np.uint16, count=W * H).reshape(H, W).copy()
            d[0, :HEADER_PIXELS] = 0
            self.ir_left = np.frombuffer(fr, dtype=np.uint8, count=W * H, offset=W * H * 2).reshape(H, W)
            self.ir_right = np.frombuffer(fr, dtype=np.uint8, count=W * H, offset=W * H * 3).reshape(H, W)
            self.latest = d; self.count += 1
        return buf

class ColorStream(_PipeStream):
    """MJPG 1280x800 from the RGB node, decoded at half size. .latest = (H,W,3) uint8 RGB."""
    def __init__(self, node): super().__init__(node, "MJPG", 1280, 800)
    def _feed(self, buf):
        import io
        from PIL import Image
        while True:
            a = buf.find(b"\xff\xd8")
            if a < 0: return b""
            b = buf.find(b"\xff\xd9", a + 2)
            if b < 0: return buf[a:]
            jpg = buf[a:b + 2]; buf = buf[b + 2:]
            try:
                im = Image.open(io.BytesIO(jpg)); im.draft("RGB", (W, H))
                self.latest = np.asarray(im.convert("RGB")); self.count += 1
            except Exception:
                pass

def backproject(frame, intr):
    """(H,W) uint16 depth -> (N,3) points in mm (camera frame)."""
    Z = frame.astype(np.float32) * DEPTH_SCALE; m = Z > 0
    v, u = np.mgrid[0:H, 0:W]
    X = (u[m] - intr["cx"]) * Z[m] / intr["fx"]; Y = (v[m] - intr["cy"]) * Z[m] / intr["fy"]
    return np.stack([X, Y, Z[m]], 1)

def rotate_cloud(points, deg):
    """Rotate camera-frame points to match an image rotated by `deg` (PIL sense: counter-clockwise).
    Image rotation is about the optical axis, so Z is untouched and (X, Y) turn in the image plane."""
    P = np.asarray(points, dtype=np.float32).copy()
    if deg % 360 == 90:    P[:, 0], P[:, 1] = points[:, 1], -points[:, 0]
    elif deg % 360 == 180: P[:, 0], P[:, 1] = -points[:, 0], -points[:, 1]
    elif deg % 360 == 270: P[:, 0], P[:, 1] = -points[:, 1], points[:, 0]
    return P

def depth_to_image(frame):
    """uint8 RGB preview, near = warm/bright, invalid = dark."""
    nz = frame[frame > 0]
    if nz.size < 50: return np.full((H, W, 3), (10, 12, 16), np.uint8)
    lo, hi = np.percentile(nz, [2, 98]); t = np.clip((frame.astype(np.float32) - lo) / max(hi - lo, 1.0), 0, 1)
    near = 1 - t
    img = np.stack([255 * (0.2 + 0.7 * near), 255 * (0.5 + 0.4 * near), 255 * (0.95 - 0.55 * near)], -1)
    img[frame == 0] = (10, 12, 16)
    return img.astype(np.uint8)

def combined_image(depth, rgb):
    """Depth heat map blended over the color frame. The depth and color cameras have different
    lenses and offsets, so this is a rough overlay for framing, not a registration."""
    from PIL import Image
    heat = depth_to_image(depth); valid = depth > 0
    if rgb.shape[:2] != (H, W): rgb = np.asarray(Image.fromarray(rgb).resize((W, H)))
    out = rgb.astype(np.float32) * 0.55
    out[valid] = rgb[valid] * 0.35 + heat[valid] * 0.65
    return out.astype(np.uint8)

def save_cloud(points, path):
    import trimesh
    zn = (points[:, 2] - points[:, 2].min()) / (np.ptp(points[:, 2]) + 1e-9)
    col = (np.stack([0.25 + 0.6 * zn, 0.6 + 0.3 * zn, 0.95 - 0.4 * zn, np.ones_like(zn)], 1) * 255).astype(np.uint8)
    trimesh.PointCloud(points, colors=col).export(path)
    return path

if __name__ == "__main__":
    dev = find_device(); print("device:", dev)
    if not dev or not dev["node"]: raise SystemExit("no RANGE found")
    if dev["on_hub"]: print("WARNING: behind a hub port (500 mA) - the RANGE needs 5V/1A; use a direct motherboard port")
    xu = XU(dev["node"]); print("firmware:", xu.firmware()); intr = xu.intrinsics(); print("intrinsics:", intr)
    xu.projector(True); time.sleep(2.5)
    s = DepthStream(dev["node"]); s.start(); c = ColorStream(dev["rgb_node"]) if dev.get("rgb_node") else None
    if c: c.start()
    t0 = time.time()
    while s.count < 8 and time.time() - t0 < 15: time.sleep(0.1)
    fr = s.latest; n = s.count; rgb = c.latest if c else None
    xu.projector(False); time.sleep(0.8)   # off WHILE streaming; stopping the stream reboots the scanner
    if c: c.stop()
    s.stop()
    if fr is None: raise SystemExit("no frames received")
    P = backproject(fr, intr)
    print("frames %d (color %d) in %.1fs, cloud %d points, z %.0f..%.0f mm" % (n, c.count if c else 0, time.time() - t0, len(P), P[:, 2].min() if len(P) else 0, P[:, 2].max() if len(P) else 0))
    print("saved", save_cloud(P, "/tmp/range_selftest.ply"))
    if rgb is not None:
        from PIL import Image
        Image.fromarray(combined_image(fr, rgb)).save("/tmp/range_selftest_combined.png"); print("saved /tmp/range_selftest_combined.png")
    print("the scanner now reboots itself (normal after a stream stops); back in ~10 s")
