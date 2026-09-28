"""process.py: the stretched-triangle test, Rebuild surface keeping a real hole open, and the thread cap.
The rebuild test runs the real CLI on a small synthetic plate (a few seconds; skipped without Open3D)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import process


def plate_with_hole(size=60.0, step=0.5, hole_d=12.0):
    """A flat square plate (open border) with a round through-hole in the middle, as a triangle mesh."""
    n = int(size / step)
    xs, ys = np.meshgrid(np.arange(n + 1) * step - size / 2, np.arange(n + 1) * step - size / 2)
    v = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], 1)
    faces = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i; b = a + 1; c = a + n + 1; d = c + 1
            faces += [[a, b, d], [a, d, c]]
    f = np.array(faces)
    centres = v[f].mean(1)
    f = f[np.hypot(centres[:, 0], centres[:, 1]) > hole_d / 2]
    return v, f


def hits(v, f, x, y):
    """Does a vertical line at (x, y) cross any triangle? (2D point-in-triangle on the XY projection.)"""
    t = v[f][:, :, :2]; p = np.array([x, y])
    def cross(a, b): return a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    d1 = cross(t[:, 1] - t[:, 0], p - t[:, 0]); d2 = cross(t[:, 2] - t[:, 1], p - t[:, 1]); d3 = cross(t[:, 0] - t[:, 2], p - t[:, 2])
    neg = (d1 < 0) | (d2 < 0) | (d3 < 0); pos = (d1 > 0) | (d2 > 0) | (d3 > 0)
    return bool((~(neg & pos)).any())


class SliverTests(unittest.TestCase):
    def test_needle_goes_big_good_triangle_stays(self):
        v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0],
                      [10, 10, 0], [20, 10, 0], [15, 18.66, 0],        # big, well shaped (equilateral, edge 10)
                      [0, 2, 0], [10, 2.1, 0], [0, 2.2, 0]], float)     # needle: long and thin
        f = np.array([[0, 1, 3], [0, 3, 2]] * 20 + [[4, 5, 6], [7, 8, 9]])
        bad, med = process.sliver_mask(v, f, 4)
        self.assertGreater(med, 0)
        self.assertFalse(bad[-2])                                       # the big equilateral face is surface
        self.assertTrue(bad[-1])                                        # the needle is a sliver


class ThreadCapTests(unittest.TestCase):
    def test_threads_follow_omp(self):
        old = os.environ.get("OMP_NUM_THREADS")
        try:
            os.environ["OMP_NUM_THREADS"] = "3"
            self.assertEqual(process._threads(), min(3, os.cpu_count() or 1))
            os.environ["OMP_NUM_THREADS"] = "junk"
            self.assertEqual(process._threads(), min(2, os.cpu_count() or 1))
        finally:
            if old is None: os.environ.pop("OMP_NUM_THREADS", None)
            else: os.environ["OMP_NUM_THREADS"] = old


class TrimTests(unittest.TestCase):
    def test_trim_drops_far_points_only(self):
        v, f = plate_with_hole()
        probe = np.array([[10.0, 10.0, 0.1], [0.0, 0.0, 0.0], [10.0, 10.0, 5.0]])
        np.testing.assert_array_equal(process.trim_to_input(probe, v, f), [False, True, True])


def _has_open3d():
    try:
        import open3d  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(_has_open3d(), "needs Open3D")
class RebuildKeepsHolesTests(unittest.TestCase):
    def test_hole_and_border_stay_open(self):
        import trimesh
        v, f = plate_with_hole()
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "plate.ply"); out = os.path.join(d, "out.ply")
            trimesh.Trimesh(v, f, process=False).export(src)
            env = dict(os.environ, OMP_NUM_THREADS="2", POINTYOINK_MEM_CAP_GB="6")
            r = subprocess.run([sys.executable, os.path.join(HERE, "process.py"), src, out, "--clean", "--isolation-rate", "100",
                                "--smooth-times", "0", "--slivers", "4", "--rebuild-surface", "8"],
                               capture_output=True, text=True, timeout=300, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            done = [json.loads(ln[11:]) for ln in r.stdout.splitlines() if ln.startswith("STAGE done ")]
            self.assertTrue(done and not done[0]["warnings"], r.stdout)
            self.assertIn("STAGE rebuilt", r.stdout)
            m = trimesh.load(out, process=False, force="mesh")
            V = np.asarray(m.vertices); F = np.asarray(m.faces)
            self.assertLess(float(np.abs(V[:, 2]).max()), 1.5)               # no skin bulging off the plate
            for x, y in ((0, 0), (3, 0), (0, -3), (-2.5, 2.5)):
                self.assertFalse(hits(V, F, x, y), "hole covered at (%s, %s)" % (x, y))
            for x, y in ((15, 15), (-20, 5), (0, 20)):
                self.assertTrue(hits(V, F, x, y), "plate missing at (%s, %s)" % (x, y))


if __name__ == "__main__":
    unittest.main()
