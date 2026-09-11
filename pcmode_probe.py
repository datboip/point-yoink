#!/usr/bin/env python3
# PC-mode discovery probe for the Revopoint MIRACO. Stand-in for the Revo Scan PC side:
#   1. answers the scanner's UDP 9706 "R-SEARCH" with the reply format found in the
#      Revo Scan 5 binary (status line "200 OK * HTTP/1.1", no-space KEY:VALUE headers,
#      HOSTIP/HOSTPORT telling it where to connect),
#   2. runs an HTTP server on HOSTPORT that logs every request the scanner makes.
# Run with the scanner on WiFi and switched to PC mode:
#   python3 pcmode_probe.py [--port 8080] [--code 1234] [--secs 120]
# Stdlib only.
import argparse, socket, struct, threading, time, json, re, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
    finally: s.close()

def ts(): return time.strftime("%H:%M:%S")

class Log:
    http = []; searches = 0; other = []

class H(BaseHTTPRequestHandler):
    server_version = "RevoScan/5.5.5"
    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        line = "%s %s" % (self.command, self.path)
        Log.http.append((ts(), line, dict(self.headers), body[:2000]))
        print("\n[%s] >>> HTTP %s from %s" % (ts(), line, self.client_address[0]))
        for k, v in self.headers.items(): print("    %s: %s" % (k, v))
        if body: print("    body: %r" % body[:600])
        resp = json.dumps({"code": 0, "msg": "ok", "result": 0}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp))); self.end_headers(); self.wfile.write(resp)
    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_OPTIONS = _handle
    def log_message(self, *a): pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080, help="HOSTPORT we advertise and serve HTTP on")
    ap.add_argument("--ip", default=None, help="HOSTIP to advertise (default: auto-detect LAN ip)")
    ap.add_argument("--code", default="", help="VERIFYCODE (4-digit) or empty")
    ap.add_argument("--man", default="R-SEARCH", help="MAN header value (R-SEARCH or R-NONE)")
    ap.add_argument("--location", default="", help="LOCATION header (default empty)")
    ap.add_argument("--name", default=socket.gethostname())
    ap.add_argument("--secs", type=int, default=120)
    ap.add_argument("--no-hostport", action="store_true", help="omit HOSTIP/HOSTPORT (control test)")
    a = ap.parse_args()
    ip = a.ip or lan_ip()

    httpd = ThreadingHTTPServer(("0.0.0.0", a.port), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print("[%s] HTTP server listening on %s:%d" % (ts(), ip, a.port))

    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    u.bind(("0.0.0.0", 9706))
    try:   # also hear the multicast copies (group seen in the binary)
        mreq = struct.pack("4s4s", socket.inet_aton("239.255.0.1"), socket.inet_aton("0.0.0.0"))
        u.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except Exception as e: print("(multicast join failed: %s)" % e)
    u.settimeout(1.0)
    print("[%s] UDP responder on 9706; reply -> HOSTIP:%s HOSTPORT:%d MAN:%s VERIFYCODE:%r" % (ts(), ip, a.port, a.man, a.code))
    print("waiting for R-SEARCH (scanner must be on WiFi in PC mode)...")

    t0 = time.time(); last_src = None
    while time.time() - t0 < a.secs:
        try: data, addr = u.recvfrom(4096)
        except socket.timeout: continue
        txt = data.decode(errors="replace")
        if txt.startswith("R-SEARCH"):
            m = re.search(r"TRANSTIME:(\d+)", txt); t = m.group(1) if m else str(int(time.time()))
            hdrs = ["MAN:%s" % a.man, "HOSTNAME:%s" % a.name]
            if not a.no_hostport: hdrs += ["HOSTIP:%s" % ip, "HOSTPORT:%d" % a.port]
            hdrs += ["TRANSTIME:%s" % t, "LOCATION:%s" % a.location, "VERIFYCODE:%s" % a.code]
            reply = ("200 OK * HTTP/1.1\r\n" + "\r\n".join(hdrs) + "\r\n\r\n").encode()
            u.sendto(reply, addr)
            Log.searches += 1
            if addr != last_src or Log.searches % 3 == 1:
                print("[%s] R-SEARCH from %s:%d (TRANSTIME %s) -> replied '200 OK * HTTP/1.1' + %d headers" % (ts(), addr[0], addr[1], t, len(hdrs)))
            last_src = addr
        else:
            Log.other.append((ts(), addr, data[:300]))
            print("[%s] other UDP from %s:%d: %r" % (ts(), addr[0], addr[1], data[:200]))
        if Log.http: pass

    httpd.shutdown()
    print("\n=== SUMMARY ===")
    print("R-SEARCH answered: %d   other UDP: %d   HTTP requests received: %d" % (Log.searches, len(Log.other), len(Log.http)))
    for t_, line, h, b in Log.http[:20]:
        print("  [%s] %s  %s" % (t_, line, (b[:120] if b else b"")))
    if not Log.http:
        print("no HTTP request arrived -> the scanner did not accept this reply; try --man R-NONE, --code 1234, --location http://%s:%d/" % (ip, a.port))
    return 0

if __name__ == "__main__":
    sys.exit(main())
