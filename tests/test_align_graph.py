import unittest

import numpy as np

from pointyoink import App


def translate(x=0.0, y=0.0, z=0.0):
    m = np.eye(4, dtype=float)
    m[:3, 3] = [x, y, z]
    return m.tolist()


class AlignGraphTests(unittest.TestCase):
    def test_resolves_chained_alignment_to_selected_base(self):
        app = App.__new__(App)
        rec = {
            "scan2": {"base": "scan1", "matrix": translate(10.0, 0.0, 0.0)},
            "scan3": {"base": "scan2", "matrix": translate(0.0, 5.0, 0.0)},
            "_base": "scan1",
        }

        mat = np.array(app._align_matrix_to_base(rec, "scan3", "scan1"))

        np.testing.assert_allclose(mat[:3, 3], [10.0, 5.0, 0.0])

    def test_resolves_reverse_path_when_user_chooses_other_base(self):
        app = App.__new__(App)
        rec = {
            "scan2": {"base": "scan1", "matrix": translate(10.0, 0.0, 0.0)},
        }

        mat = np.array(app._align_matrix_to_base(rec, "scan1", "scan2"))

        np.testing.assert_allclose(mat[:3, 3], [-10.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
