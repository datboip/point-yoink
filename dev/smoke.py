#!/usr/bin/env python3
# Headless smoke test for PointYoink. Runs the real app with an isolated config under a virtual X
# server, drives the main paths, and exits non-zero if anything raised. No scanner needed.
#   xvfb-run -a -s "-screen 0 1200x1100x24" ~/pointyoink/venv/bin/python dev/smoke.py [--src DIR] [--dest DIR]
import argparse, os, sys, json, tempfile, time, traceback

ap = argparse.ArgumentParser()
ap.add_argument("--src", default=".")
ap.add_argument("--dest", default=os.path.expanduser("~/revopoint-scans-models"))
a = ap.parse_args()
if os.environ.get("DISPLAY", "") in (":0", ":0.0", ":1", ":1.0") and os.environ.get("POINTYOINK_RENDER_ALLOW_REAL_DISPLAY") != "1":
    sys.stderr.write("refusing to run on a real display; use xvfb-run\n"); sys.exit(2)

cfgdir = tempfile.mkdtemp(prefix="py-smoke-"); cfg = os.path.join(cfgdir, "config.json")
json.dump({"dest": a.dest, "models_only": True, "geometry": "1090x1070", "exp_stl": True}, open(cfg, "w"))
os.environ["POINTYOINK_CONFIG"] = cfg; os.environ["POINTYOINK_SCALE"] = "1.0"
os.environ.setdefault("POINTYOINK_NO_GL", "1")   # a virtual X server has no GL drawable for the GPU view
os.environ.setdefault("POINTYOINK_NO_HOWTO", "1")  # no first-run dialog in scripted runs
sys.path.insert(0, os.path.abspath(a.src))
import pointyoink as P

app = P.App(); steps = []; failures = []
def step(name, fn):
    try: fn(); steps.append(name)
    except Exception as e:
        failures.append("%s: %r" % (name, e)); traceback.print_exc()
    app.update()

def run():
    step("listed", lambda: (_ for _ in ()).throw(RuntimeError("list never loaded")) if not app.listed else None)
    if app.projects:
        name = app.projects[0]["name"]
        step("select_project", lambda: app.select_project(name))
        step("gallery loaded", lambda: time.sleep(1.5))
        step("files tab", lambda: app.tabs.set([t for t in ("Files",) if True][0]) if hasattr(app.tabs, "set") else None)
        step("preview tab", lambda: app.tabs.set(app.tabs._name_list[0]) if hasattr(app.tabs, "_name_list") else None)
        for k in ("edit", "import", "folder", "project"):
            step("side " + k, lambda k=k: app.set_side(k, True))
        step("refresh_folder", lambda: app.refresh_folder())
        step("rename dialog (no-op)", lambda: None)
    else:
        steps.append("no local projects (empty state)")
    for m in ("Captures", "Live", "Projects"):
        step("mode " + m, lambda m=m: app._set_mode(m))
    import socket
    busy = socket.socket().connect_ex(("127.0.0.1", 9706)) == 0
    if busy: steps.append("wifi steps skipped (port 9706 in use by another PointYoink)")
    else:
        step("wifi open", lambda: app.on_wifi())
        step("wifi dialog exists", lambda: (_ for _ in ()).throw(RuntimeError("no wifi receiver")) if not app._wifi else None)
        step("wifi new code", lambda: app._wifi_new_code())
        step("wifi cancel", lambda: app._wifi_cancel())
    step("export zip w/o selection", lambda: app.on_export_zip())
    step("import w/o selection", lambda: app.on_pull())
    step("settings dialog", lambda: app.dlg_settings())
    step("help dialog", lambda: app.dlg_help())
    step("about dialog", lambda: app.dlg_about())
    app.after(800, finish)
def finish():
    log = os.path.join(cfgdir, "pointyoink.log")
    logged = open(log, errors="replace").read() if os.path.exists(log) else ""
    tb = logged.count("Traceback")
    try: app.on_close()
    except Exception: pass
    print("steps ok: %d  failures: %d  tracebacks in log: %d" % (len(steps), len(failures), tb))
    for f in failures: print("  FAIL", f)
    if tb:
        print("---- log ----"); print(logged[-3000:])
    sys.exit(1 if (failures or tb) else 0)
def when_listed():
    if app.listed or time.time() - t0 > 20: run()
    else: app.after(300, when_listed)
t0 = time.time(); app.after(3500, when_listed); app.mainloop()
