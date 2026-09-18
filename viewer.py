#!/usr/bin/env python3
# Standalone 3D viewer for PointYoink. Run as a subprocess so a slow load or a
# GL hiccup never freezes or crashes the main app.
#   python3 viewer.py <file.ply> [title]
# Mouse: drag to rotate, scroll to zoom, right-drag to pan.
#
# It prints PYVIEW_LOADING at start and PYVIEW_READY only after the FIRST frame
# has actually been drawn (so the caller can keep its loader up until the mesh
# is really on screen, not just parsed), and PYVIEW_ERROR on failure.
import sys, os

def main():
    if len(sys.argv) < 2:
        print("usage: viewer.py <file.ply> [title]"); return 2
    path = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(path)
    print("PYVIEW_LOADING", flush=True)
    try:
        import trimesh
        from trimesh.viewer import SceneViewer
    except Exception as e:
        print("PYVIEW_ERROR trimesh/pyglet not available:", e, flush=True); return 3
    try:
        geom = trimesh.load(path, force="mesh")
        if getattr(geom, "faces", None) is None or len(geom.faces) == 0:
            geom = trimesh.load(path)  # point cloud (fuse.ply): keep as points
        # center on the bounding-box middle so the camera frames it, not off in a corner
        try:
            c = geom.bounds.mean(axis=0)
            geom.apply_translation(-c)
        except Exception:
            pass
        scene = geom.scene() if hasattr(geom, "scene") else trimesh.Scene(geom)
    except Exception as e:
        print("PYVIEW_ERROR could not load", path, e, flush=True); return 4

    try:
        import pyglet
        # build the window HIDDEN and don't start the loop yet
        viewer = SceneViewer(scene, caption="PointYoink - " + title, smooth=False,
                             background=(14, 17, 23, 255), visible=False, start_loop=False)
        # force the first (slow) render offscreen so the window is never shown black:
        # GL buffer upload for millions of verts happens here, while hidden.
        try:
            viewer.switch_to()
            viewer.dispatch_event("on_draw")
            viewer.flip()
        except Exception:
            pass
        viewer.set_visible(True)
        viewer.activate()
        print("PYVIEW_READY", flush=True)     # the window is up with the mesh drawn
        pyglet.app.run()
    except Exception as e:
        print("PYVIEW_ERROR viewer failed:", e, flush=True); return 5
    return 0

if __name__ == "__main__":
    sys.exit(main())
