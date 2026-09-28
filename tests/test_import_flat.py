"""The "Finished models" import (App._import_flat) on a fake scanner project: a scan with only the fused point
cloud (fuse.ply) must still come in (1.0.1 fix, lost once in 1.1.0), a scan with fuse_mesh_rgb.ply is picked up,
and a raw-only scan is skipped. Runs the real method against a stand-in 'self', no window."""
import os
import queue
import tempfile
import types
import unittest

import pointyoink


def write(path, nbytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f: f.write(b"x" * nbytes)


class ImportFlatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = self.tmp.name
        self.src = os.path.join(root, "src"); self.dest = os.path.join(root, "dest")
        p = os.path.join(self.src, "P1")
        write(os.path.join(p, "P1.revo"), 10)
        write(os.path.join(p, "data", "cloudonly", "fuse.ply"), 4096)
        write(os.path.join(p, "data", "cloudonly", "preview.png"), 100)
        write(os.path.join(p, "data", "rgb", "fuse_mesh_rgb.ply"), 4096)
        write(os.path.join(p, "data", "rgb", "fuse.ply"), 4096)
        write(os.path.join(p, "data", "rawonly", "cache", "frame_0.dph"), 100)

    def tearDown(self):
        self.tmp.cleanup()

    def run_import(self, nodes=None, fmts=()):
        processed = []
        fake = types.SimpleNamespace(q=queue.Queue(), cancel=False)
        fake._copy_chunked = types.MethodType(pointyoink.App._copy_chunked, fake)
        fake._process_meshes = lambda meshes, *a, **k: processed.append(list(meshes))
        n = pointyoink.App._import_flat(fake, "P1", self.dest, list(fmts), False, 0, 1, src_root=self.src, nodes=nodes)
        return n, processed

    def test_cloud_only_scan_is_kept(self):
        n, _ = self.run_import()
        out = os.path.join(self.dest, "P1")
        self.assertEqual(n, 3)                                   # rgb mesh + rgb cloud + the cloud-only scan's cloud
        self.assertTrue(os.path.exists(os.path.join(out, "P1_cloudonly_cloud.ply")))
        self.assertTrue(os.path.exists(os.path.join(out, "P1_cloudonly.png")))
        self.assertFalse(os.path.exists(os.path.join(out, "P1_cloudonly.ply")))   # no mesh row for a scan without one
        self.assertTrue(os.path.exists(os.path.join(out, "P1_rgb.ply")))
        self.assertFalse(any(f.startswith("P1_rawonly") for f in os.listdir(out)))

    def test_only_meshes_go_to_processing(self):
        _, processed = self.run_import(fmts=["stl"])
        self.assertEqual(processed, [[os.path.join(self.dest, "P1", "P1_rgb.ply")]])

    def test_node_filter(self):
        n, _ = self.run_import(nodes=["cloudonly"])
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
