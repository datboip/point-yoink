"""align.py as the app runs it: Auto with only 1-2 point pairs placed ignores them instead of failing, and the
Auto correspondences it hands back are real matches under the final transform. Small synthetic scans, a few
seconds; skipped without Open3D."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _has_open3d():
    try:
        import open3d  # noqa: F401
        return True
    except Exception:
        return False


def _scene():
    import trimesh
    parts = [trimesh.creation.box(extents=[80, 50, 20]),
             trimesh.creation.cylinder(radius=10, height=30).apply_translation([20, 5, 25]),
             trimesh.creation.icosphere(subdivisions=3, radius=12).apply_translation([-25, -10, 18]),
             trimesh.creation.box(extents=[15, 15, 12]).apply_translation([-20, 15, 16])]
    return trimesh.util.concatenate(parts)


@unittest.skipUnless(_has_open3d(), "needs Open3D")
class AlignCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import trimesh
        cls.tmp = tempfile.TemporaryDirectory(); d = cls.tmp.name
        base = _scene()
        a = np.radians(8.0); c, s = np.cos(a), np.sin(a)
        T = np.eye(4); T[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]; T[:3, 3] = [6.0, -4.0, 2.0]
        cls.T_true = T                                            # moving -> base
        mov = base.copy(); mov.apply_transform(np.linalg.inv(T))
        cls.base = os.path.join(d, "base.ply"); cls.mov = os.path.join(d, "mov.ply")
        base.export(cls.base); mov.export(cls.mov)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_align(self, pairs, *extra):
        d = self.tmp.name; out = os.path.join(d, "res_%d.json" % len(pairs)); pj = os.path.join(d, "pairs_%d.json" % len(pairs))
        with open(pj, "w") as fh: json.dump({"pairs": pairs}, fh)
        cmd = [sys.executable, os.path.join(HERE, "align.py"), "--base", self.base, "--moving", self.mov, "--out", out,
               "--samples", "30000", "--auto", "--pairs", pj] + list(extra)
        env = dict(os.environ, OMP_NUM_THREADS="2", POINTYOINK_MEM_CAP_GB="8")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
        res = None
        if os.path.exists(out):
            with open(out) as fh: res = json.load(fh)
        return r, res

    def test_auto_with_two_pairs_ignores_them(self):
        pairs = [[[0.0, 0.0, 10.0], [1.0, 2.0, 9.0]], [[20.0, 5.0, 40.0], [15.0, 9.0, 38.0]]]
        r, res = self.run_align(pairs)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsNotNone(res)
        self.assertNotIn("invalid_landmarks", res.get("reason_codes", []))
        self.assertIsNone(res.get("pair_error_after"))            # the two pairs were not used

    def test_auto_pairs_are_real_matches(self):
        r, res = self.run_align([], "--max-dist", "6")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        T = np.array(res["matrix"])
        for b, m in res.get("auto_pairs") or []:
            gap = np.linalg.norm(T[:3, :3] @ np.array(m) + T[:3, 3] - np.array(b))
            self.assertLessEqual(gap, 6.0)
        if res.get("median_residual_mm") is not None:
            self.assertLess(res["median_residual_mm"], 6.0)


if __name__ == "__main__":
    unittest.main()
