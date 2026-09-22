import math
import os
import tempfile
import unittest

import numpy as np

import fixture
import registration_metrics as rm


def T_translate(x=0.0, y=0.0, z=0.0):
    T = np.eye(4, dtype=float)
    T[:3, 3] = [x, y, z]
    return T


def T_rot_z(deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    T = np.eye(4, dtype=float)
    T[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    return T


class LandmarkValidationTests(unittest.TestCase):
    def test_known_planar_non_collinear_pairs_are_valid(self):
        pairs = [
            ([0, 0, 0], [10, 0, 0]),
            ([20, 0, 0], [30, 0, 0]),
            ([0, 20, 0], [10, 20, 0]),
        ]
        self.assertEqual(len(rm.validate_landmark_pairs(pairs)), 3)

    def test_collinear_coincident_and_nonfinite_landmarks_are_refused(self):
        collinear = [([0,0,0],[0,0,0]), ([1,0,0],[1,0,0]), ([2,0,0],[2,0,0])]
        with self.assertRaisesRegex(ValueError, "collinear|coincident"):
            rm.validate_landmark_pairs(collinear)
        duplicate = [([0,0,0],[0,0,0]), ([0,0,0],[1,0,0]), ([0,1,0],[0,1,0])]
        with self.assertRaisesRegex(ValueError, "duplicate|coincident"):
            rm.validate_landmark_pairs(duplicate)
        nonfinite = [([0,0,0],[0,0,0]), ([1,0,0],[1,0,0]), ([0,1,0],[math.nan,1,0])]
        with self.assertRaisesRegex(ValueError, "non-finite"):
            rm.validate_landmark_pairs(nonfinite)

    def test_kabsch_recovers_known_transform(self):
        src = np.array([[0,0,0], [20,0,0], [0,30,0], [5,6,7]], dtype=float)
        T = T_translate(3, -4, 8) @ T_rot_z(25)
        dst = rm.apply_transform(src, T)
        got = rm.kabsch_transform(src, dst)
        np.testing.assert_allclose(got, T, atol=1e-10)


class FixtureContractTests(unittest.TestCase):
    def test_scan_to_fixture_and_moving_to_base_composition(self):
        fixture_pts = np.array([[0,0,0], [40,0,0], [0,30,0], [5,6,12]], dtype=float)
        T_F_A = T_translate(10, 0, 0) @ T_rot_z(10)
        T_F_B = T_translate(10, 5, 0) @ T_rot_z(-15)
        scan_a = rm.apply_transform(fixture_pts, np.linalg.inv(T_F_A))
        scan_b = rm.apply_transform(fixture_pts, np.linalg.inv(T_F_B))
        pairs_a = [[fixture_pts[i].tolist(), scan_a[i].tolist()] for i in range(len(fixture_pts))]
        pairs_b = [[fixture_pts[i].tolist(), scan_b[i].tolist()] for i in range(len(fixture_pts))]
        solve_a = fixture.solve_scan_to_fixture_from_pairs(pairs_a)
        solve_b = fixture.solve_scan_to_fixture_from_pairs(pairs_b)
        np.testing.assert_allclose(solve_a["matrix"], T_F_A, atol=1e-10)
        np.testing.assert_allclose(solve_b["matrix"], T_F_B, atol=1e-10)
        T_A_B = np.asarray(fixture.compose_scan_to_scan_through_fixture(solve_a["matrix"], solve_b["matrix"]))
        expected = np.linalg.inv(T_F_A) @ T_F_B
        np.testing.assert_allclose(T_A_B, expected, atol=1e-10)

    def test_object_shift_on_fixture_is_measurable_after_fixture_alignment(self):
        obj = np.array([[100, 0, 0], [110, 0, 0], [100, 10, 0]], dtype=float)
        T_F_A = np.eye(4)
        T_F_B = np.eye(4)
        T_A_B = np.asarray(fixture.compose_scan_to_scan_through_fixture(T_F_A, T_F_B))
        scan_a_obj = obj
        scan_b_obj = obj + np.array([3.0, 0.0, 0.0])
        moved = rm.apply_transform(scan_b_obj, T_A_B)
        self.assertAlmostEqual(float(np.linalg.norm(moved - scan_a_obj, axis=1).mean()), 3.0)

    def test_fixture_profile_has_content_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "fixture.ply")
            with open(path, "wb") as f:
                f.write(b"fixture-test")
            prof = fixture.FixtureProfile.create("base-a", path)
            self.assertEqual(prof.units, "mm")
            self.assertEqual(prof.reference_fingerprint["sha256"], fixture.file_fingerprint(path)["sha256"])
            loaded = fixture.FixtureProfile.from_json(prof.to_json())
            self.assertEqual(loaded.id, "base-a")


class MetricTests(unittest.TestCase):
    def test_bidirectional_coverage_reports_unmatched_surface(self):
        src = np.array([[0,0,0], [1,0,0], [2,0,0]], dtype=float)
        tgt = np.array([[0,0,0], [1,0,0], [9,0,0]], dtype=float)
        r = rm.coverage_report(src, tgt, np.eye(4), threshold_mm=0.25)
        self.assertAlmostEqual(r["source_coverage"], 2/3)
        self.assertAlmostEqual(r["target_coverage"], 2/3)
        self.assertAlmostEqual(r["unmatched_fraction"], 1/3)

    def test_plane_only_matching_reports_weak_directions(self):
        xs, ys = np.meshgrid(np.linspace(-10, 10, 5), np.linspace(-10, 10, 5))
        pts = np.column_stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)])
        normals = np.tile([0, 0, 1], (len(pts), 1))
        r = rm.point_to_plane_constraint_rank(pts, normals, characteristic_length_mm=20)
        self.assertLess(r["rank"], 6)
        self.assertIn("tx", r["weak_directions"])
        self.assertIn("ty", r["weak_directions"])

    def test_varied_normals_constrain_all_directions(self):
        pts = np.array([[0,0,0],[20,0,0],[0,20,0],[0,0,20],[20,20,20],[10,-16,6],[12,7,15],[3,14,9]], dtype=float)
        normals = np.array([
            [0.1257302210933933, -0.1321048632913019, 0.6404226504432821],
            [0.10490011715303971, -0.535669373161111, 0.36159505490948474],
            [1.3040000451301372, 0.9470809631292422, -0.7037352358069926],
            [-1.2654214710460525, -0.6232744625373522, 0.0413259793472436],
            [-2.3250307746388343, -0.21879166393254573, -1.2459109472530652],
            [-0.7322673547034516, -0.5442589828573099, -0.31630015636915454],
            [0.4116305363741328, 1.0425133694426776, -0.12853466294403426],
            [1.3664634705496859, -0.6651946734866135, 0.3515100700930197],
        ], dtype=float)
        r = rm.point_to_plane_constraint_rank(pts, normals, characteristic_length_mm=30)
        self.assertEqual(r["rank"], 6)


if __name__ == "__main__":
    unittest.main()
