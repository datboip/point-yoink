#!/usr/bin/env python3
# WiFi "Share to PC" receiver for the Revopoint MIRACO. Stands in for Revo Scan 5 and saves
# everything the scanner pushes. No vendor software, stdlib only.
# Protocol (verified on a MIRACO Pro, firmware 1.0.0.129):
#   1. while its Share to PC > Wi-Fi screen is up the scanner broadcasts UDP 9706
#      "R-SEARCH * HTTP/1.1\r\nTRANSTIME:<secs>" every 3 s
#   2. we answer unicast "200 OK * HTTP/1.1" with no-space headers, including VERIFYCODE:<4 digits>;
#      the person types that code into the scanner
#   3. the scanner then connects as an HTTP client (cpp-httplib) to the PC on TCP 9706 (it ignores
#      HOSTPORT): GET /connect?verifycode=.., GET /heartbeat, POST /file per file, GET /close?closetype=done.
#      POST /file headers: path (project-relative), filenum, datatype, partnum, partindex (1-based),
#      totalsize (whole transfer), transsize; body = one 4 MiB part. Parts come over several
#      connections at once, so each is written at (partindex-1)*4 MiB. Its last entry is the
#      project folder itself with no body; it must be answered too or the scanner says "failed".
#   Every reply is {"code":0}. A 1 GB project takes about 50 s on ordinary WiFi.
# Usage as a script: python3 wifi.py [--dest DIR] [--code 1234]
import argparse, socket, struct, threading, time, re, os, secrets
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = 9706
PART = 4 * 1024 * 1024
STAGE = ".wifi-incoming"          # projects land here first, then the app moves/imports them

def random_code(): return "%04d" % secrets.randbelow(10000)
MAX_BAD_CODES = 5              # a 4-digit code is small; lock the session after a few wrong guesses

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
    except Exception: return "0.0.0.0"
    finally: s.close()

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RevoScan/5.5.5"
    def _ok(self):
        b = b'{"code": 0, "msg": "ok", "result": 0}'
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _deny(self, status=403):
        self.send_response(status); self.send_header("Content-Length", "0"); self.end_headers(); self.close_connection = True
    def _allowed(self, route):
        """Every request must carry our code (query string or header) and come from the one
        client that passed /connect. Wrong codes count towards a lockout."""
        rx = self.server.rx; peer = self.client_address[0]
        m = re.search(r"[?&]verifycode=(\d*)", self.path)
        code = m.group(1) if m else (self.headers.get("verifycode") or "")
        if rx.locked or not secrets.compare_digest(code, rx.code):
            if not rx.locked: rx._bad_code(peer)
            return False
        if route == "/connect":
            return rx._claim(peer)
        return rx.peer == peer
    def do_GET(self):
        rx = self.server.rx; route = self.path.split("?")[0]
        if not self._allowed(route): self._deny(); return
        if route == "/connect": rx._connected()
        elif route == "/close": rx._closed()
        self._ok()
    def do_POST(self):
        rx = self.server.rx; route = self.path.split("?")[0]
        if not self._allowed(route): self._deny(); return
        n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n) if n else b""
        if route == "/file": rx._file(self.headers, body)
        elif route == "/close": rx._closed()
        self._ok()
    do_PUT = do_POST
    def log_message(self, *a): pass

class Receiver:
    """Answers discovery and receives one or more projects into <dest>/.wifi-incoming/.
    on_event(kind, info) is called from worker threads with kinds:
      searching {ip}  badcode {ip,locked}  connected {}  progress {bytes,total,files,rate,avg}  done {projects:[names]}
    rate = bytes/s over the last second (what a graph wants), avg = since the start (what an ETA wants).
    Only requests carrying the code are served, from the first client that passes /connect;
    after MAX_BAD_CODES wrong codes the session is locked (start a new one for a new code)."""
    def __init__(self, dest, code=None, on_event=None, name=None):
        self.dest = dest; self.stage = os.path.join(dest, STAGE); self.code = code or random_code()
        self.on_event = on_event or (lambda k, i: None); self.name = name or socket.gethostname()
        self.httpd = None; self.udp = None; self._on = False
        self.files = {}; self.bytes = 0; self.total = 0; self.t0 = None; self.seen = set(); self._lock = threading.Lock()
        self.peer = None; self.bad = 0; self.locked = False
        self._hist = []                    # (time, bytes) for the instantaneous rate
    def _emit(self, kind, **info):
        try: self.on_event(kind, info)
        except Exception: pass
    def start(self):
        os.makedirs(self.stage, exist_ok=True)
        self.httpd = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler); self.httpd.daemon_threads = True; self.httpd.rx = self
        self.httpd.block_on_close = False        # don't wait for the scanner's keep-alive connections on shutdown
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        u.bind(("0.0.0.0", PORT))
        try: u.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack("4s4s", socket.inet_aton("239.255.0.1"), socket.inet_aton("0.0.0.0")))
        except Exception: pass
        u.settimeout(0.5); self.udp = u; self._on = True; self.ip = lan_ip()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self._discovery, daemon=True).start()
    def stop(self):
        self._on = False
        try:
            if self.httpd: self.httpd.shutdown(); self.httpd.server_close()
        except Exception: pass
        try:
            if self.udp: self.udp.close()
        except Exception: pass
    def _discovery(self):
        while self._on:
            try: data, addr = self.udp.recvfrom(4096)
            except socket.timeout: continue
            except OSError: break
            txt = data.decode(errors="replace")
            if not txt.startswith("R-SEARCH"): continue
            m = re.search(r"TRANSTIME:(\d+)", txt); t = m.group(1) if m else str(int(time.time()))
            hdrs = ["MAN:R-SEARCH", "HOSTNAME:%s" % self.name, "HOSTIP:%s" % self.ip, "HOSTPORT:%d" % PORT,
                    "TRANSTIME:%s" % t, "LOCATION:", "VERIFYCODE:%s" % self.code]
            try: self.udp.sendto(("200 OK * HTTP/1.1\r\n" + "\r\n".join(hdrs) + "\r\n\r\n").encode(), addr)
            except OSError: pass
            if addr[0] not in self.seen: self.seen.add(addr[0]); self._emit("searching", ip=addr[0])
    # ---- HTTP side ----
    def _claim(self, peer):
        with self._lock:
            if self.peer in (None, peer): self.peer = peer; return True
        return False
    def _bad_code(self, peer):
        with self._lock:
            self.bad += 1; lock = self.bad >= MAX_BAD_CODES
            if lock: self.locked = True
        self._emit("badcode", ip=peer, locked=lock)
    def _connected(self):
        self.t0 = self.t0 or time.time(); self._emit("connected")
    def _closed(self):
        try: projects = sorted(d for d in os.listdir(self.stage) if os.path.isdir(os.path.join(self.stage, d)))
        except Exception: projects = []
        self._emit("done", projects=projects)
    def _file(self, h, body):
        rel = h.get("path", "").replace("\\", "/").strip("/")
        if not rel or ".." in rel.split("/"): return
        out = os.path.join(self.stage, rel)
        idx = int(h.get("partindex") or 1)
        if h.get("datatype", "file") != "file" or os.path.isdir(out) or (not body and "/" not in rel):
            os.makedirs(out, exist_ok=True); return           # a folder entry (the project dir comes last)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with self._lock:
            with open(out, "r+b" if os.path.exists(out) else "wb") as f:
                f.seek((idx - 1) * PART); f.write(body)
            self.files.setdefault(rel, set()).add(idx); self.bytes += len(body)
            self.total = int(h.get("totalsize") or self.total or 0)
            now = time.time(); avg = self.bytes / max(0.1, now - (self.t0 or now))
            self._hist.append((now, self.bytes)); self._hist = [x for x in self._hist if now - x[0] <= 1.0]
            span = now - self._hist[0][0]
            rate = (self.bytes - self._hist[0][1]) / span if span >= 0.25 else avg    # last-second rate
            due = now - getattr(self, "_last_emit", 0.0) >= 0.1                        # thousands of parts arrive; report 10x/s
            if due: self._last_emit = now
        if due: self._emit("progress", bytes=self.bytes, total=self.total, files=len(self.files), rate=rate, avg=avg)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=os.path.expanduser("~/revopoint-scans-models"))
    ap.add_argument("--code", default=None, help="4-digit code to show (default: random)")
    ap.add_argument("--secs", type=int, default=1800)
    a = ap.parse_args()
    done = {}
    def ev(kind, info):
        if kind == "progress":
            if info["files"] % 50 == 0: print("  %d files, %.0f MB, %.1f MB/s" % (info["files"], info["bytes"] / 1048576, info["rate"] / 1048576), flush=True)
        else: print("[%s] %s %s" % (time.strftime("%H:%M:%S"), kind, info), flush=True)
        if kind == "done": done["p"] = info["projects"]
    rx = Receiver(a.dest, a.code, ev); rx.start()
    print("On the MIRACO: Share to PC > Wi-Fi, enter %s   (receiving into %s)" % (rx.code, rx.stage), flush=True)
    t0 = time.time()
    while time.time() - t0 < a.secs and "p" not in done: time.sleep(0.5)
    rx.stop()
    print("received %d files, %.0f MB; projects: %s" % (len(rx.files), rx.bytes / 1048576, done.get("p")))
