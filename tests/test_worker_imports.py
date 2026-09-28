"""The worker scripts cap their own memory only when run, never when imported: the app imports cutplane in its
own process (Remove base), and an import-time setrlimit used to cap the whole app for the rest of the session."""
import os
import subprocess
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


class ImportDoesNotCapTests(unittest.TestCase):
    def test_import_leaves_rlimit_alone(self):
        code = ("import resource, sys; sys.path.insert(0, %r); before = resource.getrlimit(resource.RLIMIT_AS);"
                "import cutplane, process, align; after = resource.getrlimit(resource.RLIMIT_AS);"
                "print(before == after, before, after)") % HERE
        env = dict(os.environ, POINTYOINK_MEM_CAP_GB="3")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("True"), r.stdout)

    def test_workers_still_cap_themselves(self):
        import cutplane
        code = ("import resource, sys; sys.path.insert(0, %r); import cutplane; cutplane._cap_memory();"
                "print(resource.getrlimit(resource.RLIMIT_AS)[0])") % HERE
        env = dict(os.environ, POINTYOINK_MEM_CAP_GB="3")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(int(r.stdout.strip()), 3 * 1024 ** 3)
        self.assertTrue(callable(cutplane.main))


class RansacNormalTests(unittest.TestCase):
    def test_finds_the_table(self):
        import cutplane
        rng = np.random.default_rng(1)
        table = np.c_[rng.uniform(-50, 50, (4000, 2)), rng.normal(0, 0.05, 4000)]          # z = 0 plane
        part = rng.normal(0, 8, (1500, 3)) + [0, 0, 20]                                      # a blob above it
        n = cutplane.ransac_normal(np.vstack([table, part]), np.random.default_rng(0))
        self.assertGreater(abs(float(np.asarray(n)[2])), 0.99)


if __name__ == "__main__":
    unittest.main()
