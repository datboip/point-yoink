#!/usr/bin/env python3
# Off-screen render of the PointYoink window for design work. Runs the real app from a given
# source dir under a virtual X server and captures a PNG, so nothing touches the user's desktop.
#   xvfb-run -a -s "-screen 0 1200x1100x24" ~/pointyoink/venv/bin/python dev/render.py \
#       --src . --out /tmp/shot.png [--section project|edit|import|folder|none] [--mode Projects|Captures|Live]
#       [--select Project09102026224255] [--geometry 1090x1070] [--dest ~/revopoint-scans-models]
# Uses an isolated config (never the user's ~/.config/pointyoink). Needs ImageMagick `import`.
import argparse, os, sys, json, subprocess, tempfile, time, traceback

ap = argparse.ArgumentParser()
ap.add_argument("--src", default=".", help="directory containing pointyoink.py to render")
ap.add_argument("--out", required=True)
ap.add_argument("--section", default="project")
ap.add_argument("--mode", default="Projects")
ap.add_argument("--select", default="first", help="project name to select, 'first', or 'none'")
ap.add_argument("--geometry", default="1090x1070")
ap.add_argument("--dest", default=os.path.expanduser("~/revopoint-scans-models"))
ap.add_argument("--wait", type=float, default=4.5, help="seconds to let the splash finish and lists load")
a = ap.parse_args()

if os.environ.get("DISPLAY", "") in (":0", ":0.0", ":1", ":1.0") and os.environ.get("POINTYOINK_RENDER_ALLOW_REAL_DISPLAY") != "1":
    # be loud rather than accidentally rendering (and grabbing the X server) on a real desktop
    sys.stderr.write("refusing to render on a real display; run under xvfb-run\n"); sys.exit(2)

cfgdir = tempfile.mkdtemp(prefix="py-render-")
cfg = os.path.join(cfgdir, "config.json")
json.dump({"dest": a.dest, "models_only": True, "geometry": a.geometry, "side": a.section if a.section != "none" else "none",
           "exp_stl": True, "ui_scale": 1.0}, open(cfg, "w"))
os.environ["POINTYOINK_CONFIG"] = cfg
os.environ["POINTYOINK_SCALE"] = "1.0"
os.environ.setdefault("POINTYOINK_NO_GL", "1")   # a virtual X server has no GL drawable for the GPU view
os.environ.setdefault("POINTYOINK_NO_HOWTO", "1")
os.environ["POINTYOINK_NO_DEVICE"] = "1"   # never touch the scanner from a test instance  # no first-run dialog in scripted runs
sys.path.insert(0, os.path.abspath(a.src))
import pointyoink as P

app = P.App()
def finish():
    try:
        if a.mode not in ("Projects", "Local"):
            app.mode_sw.set(a.mode); app._set_mode(a.mode)
        elif a.select != "none":
            app._set_mode(a.mode); app.update()      # Projects = the Import page, Local = the Projects page.
            # _set_mode re-filters the list for the page, so re-read app.projects here (the Import page
            # with no scanner attached has none) rather than trusting the pre-switch list.
            projs = app.projects or []
            if not projs:
                sys.stderr.write("no projects to select on mode %s (a scanner-less run? use --mode Local for the local page)\n" % a.mode)
            else:
                name = projs[0]["name"] if a.select == "first" else a.select
                if a.select != "first" and not any(p["name"] == name for p in projs):
                    sys.stderr.write("project %r is not in the %s list; using the first instead\n" % (name, a.mode)); name = projs[0]["name"]
                app.select_project(name)
                if a.mode == "Local": pass                          # the Projects page has no side sections
                elif a.section != "none": app.set_side(a.section, True)
                else: app.set_side(app.side_mode)  # fold
        else:
            app._set_mode(a.mode)
        try: app.geometry(a.geometry + "+0+0")      # pin the size/position; xvfb has no WM to honour cfg geometry
        except Exception: pass
        app.update_idletasks(); app.update()
        app.after(700, shoot)
    except Exception as e:
        sys.stderr.write("render setup failed:\n" + traceback.format_exc()); app.destroy()
def shoot():
    try:
        app.update()
        # capture the app window by its X id, not root: it isn't always placed at the origin under xvfb,
        # so a root grab can clip an edge or frame it in desktop black.
        subprocess.run(["import", "-window", str(app.winfo_id()), a.out], check=True, timeout=30)
        print("wrote", a.out)
    except Exception as e:
        sys.stderr.write("capture failed: %r\n" % e)
    app.destroy()
def when_listed():
    if app.listed or time.time() - t0 > a.wait + 6: finish()
    else: app.after(300, when_listed)
t0 = time.time()
app.after(int(a.wait * 1000), when_listed)
app.mainloop()
