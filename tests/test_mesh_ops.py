import unittest

import numpy as np

import mesh_ops


class MeshOpsTests(unittest.TestCase):
    def test_grow_one_face_ring(self):
        faces = np.array([[0, 1, 2], [2, 1, 3], [2, 3, 4]], dtype=np.int64)
        sel = np.zeros(5, dtype=bool)
        sel[[0, 1, 2]] = True

        grown = mesh_ops.grow_selection(faces, sel)

        self.assertEqual(set(np.flatnonzero(grown)), {0, 1, 2, 3})

    def test_shrink_removes_boundary_ring(self):
        faces = []
        for y in range(5):
            for x in range(5):
                a = y * 6 + x
                b = a + 1
                c = a + 6
                d = c + 1
                faces.extend(([a, b, c], [b, d, c]))
        faces = np.array(faces, dtype=np.int64)
        sel = np.ones(36, dtype=bool)

        shrunk = mesh_ops.shrink_selection(faces, sel)

        self.assertTrue(shrunk.any())
        self.assertLess(int(shrunk.sum()), int(sel.sum()))
        self.assertEqual(set(np.flatnonzero(shrunk)), {14, 15, 20, 21})

    def test_connected_selection_keeps_close_shells_separate(self):
        faces = np.array([[0, 1, 2], [2, 1, 3], [4, 5, 6], [6, 5, 7]], dtype=np.int64)
        sel = np.zeros(8, dtype=bool)
        sel[[0, 1, 2]] = True

        connected = mesh_ops.connected_selection(faces, sel)

        self.assertEqual(set(np.flatnonzero(connected)), {0, 1, 2, 3})

    def test_connected_selection_uses_partial_vertex_seed(self):
        faces = np.array([[0, 1, 2], [2, 1, 3], [4, 5, 6]], dtype=np.int64)
        sel = np.zeros(7, dtype=bool)
        sel[1] = True

        connected = mesh_ops.connected_selection(faces, sel)

        self.assertEqual(set(np.flatnonzero(connected)), {0, 1, 2, 3})

    def test_face_grow_and_shrink_preserve_last_ring(self):
        faces = np.array([[0, 1, 2], [2, 1, 3], [2, 3, 4]], dtype=np.int64)
        sel = np.array([True, False, False])

        grown = mesh_ops.grow_face_selection(faces, sel)
        shrunk = mesh_ops.shrink_face_selection(faces, sel)

        self.assertEqual(set(np.flatnonzero(grown)), {0, 1})
        self.assertEqual(set(np.flatnonzero(shrunk)), {0})

    def test_face_connected_keeps_shells_separate(self):
        faces = np.array([[0, 1, 2], [2, 1, 3], [4, 5, 6], [6, 5, 7]], dtype=np.int64)
        sel = np.array([True, False, False, False])

        connected = mesh_ops.connected_face_selection(faces, sel)

        self.assertEqual(set(np.flatnonzero(connected)), {0, 1})

    def test_face_center_box_does_not_select_projected_whole_cube(self):
        centers = np.array([
            [50, 50],
            [150, 50],
            [50, 150],
            [150, 150],
            [100, 100],
        ], dtype=float)
        box = np.array([[80, 80], [120, 80], [120, 120], [80, 120]], dtype=float)

        selected = mesh_ops.face_center_selection(centers, box)

        self.assertEqual(set(np.flatnonzero(selected)), {4})

    def test_face_center_box_respects_visible_mask(self):
        centers = np.array([[100, 100], [105, 105]], dtype=float)
        box = np.array([[80, 80], [120, 80], [120, 120], [80, 120]], dtype=float)
        visible = np.array([True, False])

        selected = mesh_ops.face_center_selection(centers, box, visible)

        self.assertEqual(set(np.flatnonzero(selected)), {0})

    def test_face_radius_selection_uses_centers(self):
        centers = np.array([[100, 100], [140, 100], [200, 200]], dtype=float)

        selected = mesh_ops.face_radius_selection(centers, 100, 100, 45)

        self.assertEqual(set(np.flatnonzero(selected)), {0, 1})


if __name__ == "__main__":
    unittest.main()
