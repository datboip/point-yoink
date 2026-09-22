import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from edit_commit import finish_journal, publish_temp, validate_mesh, write_mesh_temp
from edit_session import EditSession, EditSizeError, HistoryLimitError


def mesh_session(history_bytes=1024 * 1024):
    vertices=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]], dtype=np.float64)
    faces=np.array([[0,1,2],[0,2,3]], dtype=np.int64)
    colors=np.array([[255,0,0,255],[0,255,0,255],[0,0,255,255],[255,255,255,255]], dtype=np.uint8)
    return EditSession("project", "scan", "/source.ply", "scanner", "mesh", vertices, faces,
                       vertex_colors=colors, signature={"size":1,"mtime_ns":1,"sha256":"x"},
                       history_bytes=history_bytes)


class EditSessionTests(unittest.TestCase):
    def test_mesh_delete_undo_redo_and_branch(self):
        session=mesh_session()
        original=session.faces.copy()
        selection=np.array([True,True,True,False])
        self.assertTrue(session.delete_selected(selection))
        first_revision=session.geometry_revision
        self.assertTrue(session.dirty)
        self.assertEqual(len(session.faces), 1)
        self.assertTrue(session.undo())
        np.testing.assert_array_equal(session.faces, original)
        self.assertFalse(session.dirty)
        self.assertTrue(session.can_redo)
        self.assertTrue(session.redo())
        self.assertEqual(session.geometry_revision, first_revision)
        self.assertTrue(session.undo())
        self.assertTrue(session.delete_selected(np.array([True,False,True,True])))
        self.assertGreater(session.geometry_revision, first_revision)
        self.assertFalse(session.can_redo)

    def test_same_count_change_tracks_savepoint_exactly(self):
        session=mesh_session()
        original=session.vertices.copy()
        moved=original.copy(); moved[:, 0] += 2.5
        self.assertTrue(session.replace_vertices(moved))
        self.assertEqual(len(session.vertices), len(original))
        self.assertTrue(session.dirty)
        saved=session.geometry_revision; session.mark_saved()
        self.assertFalse(session.dirty)
        self.assertTrue(session.undo())
        np.testing.assert_array_equal(session.vertices, original)
        self.assertTrue(session.dirty)
        self.assertTrue(session.redo())
        np.testing.assert_array_equal(session.vertices, moved)
        self.assertEqual(session.geometry_revision, saved)
        self.assertFalse(session.dirty)
        self.assertFalse(session.replace_vertices(moved.copy()))

    def test_history_limit_refuses_before_mutation(self):
        session=mesh_session(history_bytes=1)
        original=session.faces.copy()
        with self.assertRaises(HistoryLimitError):
            session.delete_selected(np.array([True,True,True,False]))
        np.testing.assert_array_equal(session.faces, original)
        self.assertFalse(session.dirty)

    def test_point_delete_restores_values_and_colors(self):
        vertices=np.arange(15, dtype=np.float64).reshape(5,3)
        colors=np.arange(20, dtype=np.uint8).reshape(5,4)
        session=EditSession("p", "n", "/points.ply", "cloud", "points", vertices,
                            vertex_colors=colors, signature={"size":1,"mtime_ns":1,"sha256":"x"})
        self.assertTrue(session.delete_selected(np.array([False,True,False,True,False])))
        self.assertEqual(len(session.vertices), 3)
        self.assertTrue(session.undo())
        np.testing.assert_array_equal(session.vertices, vertices)
        np.testing.assert_array_equal(session.vertex_colors, colors)
        self.assertTrue(session.redo())
        np.testing.assert_array_equal(session.vertices, vertices[[0,2,4]])

    def test_snapshot_compacts_vertices_and_attributes(self):
        session=mesh_session()
        session.delete_selected(np.array([True,True,True,False]))
        snap=session.snapshot()
        self.assertEqual(len(snap.faces), 1)
        self.assertEqual(len(snap.vertices), 3)
        self.assertEqual(len(snap.vertex_colors), 3)
        self.assertLess(snap.faces.max(), len(snap.vertices))

    def test_file_limit_refuses_without_reduction(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder, "mesh.ply")
            snap=mesh_session().snapshot()
            tmp=write_mesh_temp(snap, path); os.replace(tmp, path)
            with mock.patch("trimesh.load") as loader:
                with self.assertRaises(EditSizeError):
                    EditSession.from_file("p", "n", path, "scanner", max_faces=1)
                loader.assert_not_called()

    def test_colored_mesh_round_trip_keeps_vertex_colors(self):
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder, "mesh.ply")
            original=mesh_session(); snap=original.snapshot()
            tmp=write_mesh_temp(snap, path); os.replace(tmp, path)
            loaded=EditSession.from_file("p", "n", path, "scanner")
            np.testing.assert_array_equal(loaded.vertex_colors, original.vertex_colors)


class EditCommitTests(unittest.TestCase):
    def test_publish_archives_and_validates(self):
        with tempfile.TemporaryDirectory() as folder:
            target=os.path.join(folder, "edited.ply")
            original=mesh_session().snapshot()
            tmp=write_mesh_temp(original, target); os.replace(tmp, target)
            changed=mesh_session(); changed.replace_vertices(changed.vertices+3.0)
            incoming=write_mesh_temp(changed.snapshot(), target)
            archive=os.path.join(folder, "old.ply")
            journal=os.path.join(folder, "pending.json")
            publish_temp(incoming, target, archive, journal, {"schema":1})
            self.assertTrue(validate_mesh(target))
            self.assertTrue(validate_mesh(archive))
            self.assertTrue(os.path.exists(journal))
            finish_journal(journal)
            self.assertFalse(os.path.exists(journal))

    def test_replace_failure_leaves_target_and_archive_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            target=os.path.join(folder, "edited.ply")
            snap=mesh_session().snapshot()
            tmp=write_mesh_temp(snap, target); os.replace(tmp, target)
            before=Path(target).read_bytes()
            incoming=write_mesh_temp(snap, target)
            archive=os.path.join(folder, "old.ply")
            journal=os.path.join(folder, "pending.json")
            real_replace=os.replace
            def fail_target(src, dst):
                if dst==target:
                    raise OSError("forced replacement failure")
                return real_replace(src, dst)
            with mock.patch("edit_commit.os.replace", side_effect=fail_target):
                with self.assertRaises(OSError):
                    publish_temp(incoming, target, archive, journal, {"schema":1})
            self.assertEqual(Path(target).read_bytes(), before)
            self.assertTrue(validate_mesh(archive))
            self.assertTrue(os.path.exists(journal))


if __name__ == "__main__":
    unittest.main()
