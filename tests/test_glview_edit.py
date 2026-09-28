"""glview's mesh-edit logic without a GL context: the unbound GLView methods run against a stand-in 'self'.
Covers the module-level mesh_ops import (Grow/Shrink/Connected/Magic did nothing without it), Magic as one
connected-components pass, and Delete removing exactly the faces the overlay shows."""
import types
import unittest

import numpy as np

import glview
import mesh_ops
from edit_session import EditSession


def grid(nx, ny):
    """A flat nx x ny grid of quads, two triangles per quad. Quad (i, j) owns faces 2*(j*nx+i) and +1."""
    xs, ys = np.meshgrid(np.arange(nx + 1, dtype=float), np.arange(ny + 1, dtype=float))
    v = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], axis=1)
    faces = []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i; b = a + 1; c = a + nx + 1; d = c + 1
            faces += [[a, b, d], [a, d, c]]
    return v, np.array(faces, dtype=np.int64)


def quad(nx, i, j):
    k = 2 * (j * nx + i); return [k, k + 1]


def fake_view(v, f, session=None):
    s = types.SimpleNamespace(
        _medit_faces=np.ascontiguousarray(f, dtype=np.uint32), _pts_v=np.asarray(v, dtype=np.float32),
        magic_k=3.0, visible_only=False, edit_session=session, edit_target="mesh",
        _pts_sel=np.zeros(len(v), dtype=bool), _mesh_face_sel=np.zeros(len(f), dtype=bool), _medit_undo=[])
    for name in ("_face_graph", "_fgraph_key", "_remember_graph", "_mesh_magic_region", "delete_selected",
                 "_sync_edit_session", "mesh_grow_selection", "mesh_shrink_selection", "mesh_connected_selection"):
        setattr(s, name, types.MethodType(getattr(glview.GLView, name), s))
    s._mesh_recolor = lambda: None; s._display = lambda: None; s._reupload_mesh_faces = lambda: None
    return s


class GLViewImportTests(unittest.TestCase):
    def test_mesh_ops_is_a_module_global(self):
        self.assertIs(glview.mesh_ops, mesh_ops)

    def test_grow_shrink_connected_work(self):
        v, f = grid(6, 6); s = fake_view(v, f)
        s._mesh_face_sel[quad(6, 2, 2)] = True
        self.assertTrue(s.mesh_grow_selection())
        self.assertGreater(int(s._mesh_face_sel.sum()), 2)
        self.assertTrue(s.mesh_connected_selection())
        self.assertEqual(int(s._mesh_face_sel.sum()), len(f))


class MagicTests(unittest.TestCase):
    def test_magic_selects_the_flat_connected_region(self):
        v, f = grid(8, 4)
        v[(v[:, 0] > 4.5), 2] = (v[(v[:, 0] > 4.5), 0] - 4.5) * 10.0     # a steep wall from x=5 on
        s = fake_view(v, f)
        out = s._mesh_magic_region(0, np.ones(len(f), dtype=bool))
        self.assertIsNone(getattr(s, "_magic_error", None))
        centres = v[f].mean(axis=1)
        self.assertTrue(out[centres[:, 0] < 4.0].all())
        self.assertFalse(out[centres[:, 0] > 5.5].any())

    def test_component_of_matches_flood_fill(self):
        v, f = grid(10, 10); g = mesh_ops.FaceGraph(f)
        rng = np.random.RandomState(3); allowed = rng.rand(len(f)) > 0.3
        for seed in (0, 57, 150):
            ref = np.zeros(len(f), dtype=bool); ref[seed] = True; al = allowed.copy(); al[seed] = True
            while True:
                nxt = g.neighbours_of(ref) & al & ~ref
                if not nxt.any(): break
                ref |= nxt
            np.testing.assert_array_equal(g.component_of(seed, allowed), ref)

    def test_subgraph_equals_rebuild(self):
        v, f = grid(7, 5); g = mesh_ops.FaceGraph(f)
        keep = np.ones(len(f), dtype=bool); keep[[3, 4, 20, 41]] = False
        sub = g.subgraph(keep); ref = mesh_ops.FaceGraph(f[keep])
        self.assertEqual(sub.n, ref.n)
        np.testing.assert_array_equal(np.unique(np.sort(sub.pairs, axis=1), axis=0), ref.pairs)


class ExactDeleteTests(unittest.TestCase):
    def session(self, v, f):
        return EditSession("p", "n", "/source.ply", "scanner", "mesh", v, f,
                           signature={"size": 1, "mtime_ns": 1, "sha256": "x"}, history_bytes=64 * 1024 * 1024)

    def check(self, sel_faces):
        v, f = grid(6, 6); doc = self.session(v, f); s = fake_view(v, f, doc)
        s._mesh_face_sel[:] = False; s._mesh_face_sel[sel_faces] = True
        s._pts_sel[np.unique(f[sel_faces])] = True
        want = f[np.setdiff1d(np.arange(len(f)), sel_faces)]
        self.assertTrue(s.delete_selected())
        np.testing.assert_array_equal(doc.faces, want)                       # exactly the shown faces went
        self.assertTrue(doc.undo()); s._sync_edit_session()
        np.testing.assert_array_equal(doc.faces, f)
        np.testing.assert_array_equal(np.flatnonzero(s._mesh_face_sel), np.sort(sel_faces))   # undo brings back the same selection
        self.assertTrue(doc.redo()); s._sync_edit_session()
        np.testing.assert_array_equal(doc.faces, want)
        self.assertFalse(s._mesh_face_sel.any())

    def test_box_minus_one_quad(self):
        box = [k for j in range(1, 5) for i in range(1, 5) for k in quad(6, i, j)]
        hole = quad(6, 2, 2)
        self.check(np.array(sorted(set(box) - set(hole))))

    def test_gap_of_one_quad_between_two_blocks(self):
        left = [k for j in range(0, 6) for i in range(0, 2) for k in quad(6, i, j)]
        right = [k for j in range(0, 6) for i in range(3, 6) for k in quad(6, i, j)]
        self.check(np.array(sorted(left + right)))

    def test_graph_follows_delete_undo_redo_without_rebuild(self):
        v, f = grid(6, 6); doc = self.session(v, f); s = fake_view(v, f, doc)
        g0 = s._face_graph()
        s._mesh_face_sel[quad(6, 1, 1)] = True; s._pts_sel[np.unique(f[quad(6, 1, 1)])] = True
        s.delete_selected()
        g1 = s._face_graph()
        self.assertEqual(g1.n, len(f) - 2)
        ref = mesh_ops.FaceGraph(doc.faces)
        np.testing.assert_array_equal(np.unique(np.sort(g1.pairs, axis=1), axis=0), ref.pairs)
        doc.undo(); s._sync_edit_session()
        self.assertIs(s._face_graph(), g0)
        doc.redo(); s._sync_edit_session()
        self.assertIs(s._face_graph(), g1)


if __name__ == "__main__":
    unittest.main()
