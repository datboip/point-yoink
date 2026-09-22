import math
import unittest

import viewnav


def _dist(a, b):
    return math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))


class ViewNavTests(unittest.TestCase):
    def test_home_hit_is_fixed_top_left(self):
        rot = viewnav.rot_from_az_el(-35, 30)
        self.assertEqual(viewnav.layout(900, 700)["home"], (9, 9, 30, 30))
        self.assertEqual(viewnav.hit(rot, 900, 700, 20, 20)["kind"], "home")

    def test_axes_share_corner_and_extend_past_cube_edges(self):
        rot = viewnav.rot_from_az_el(-35, 30)
        lo, axes = viewnav.axis_segments(rot, 900, 700)
        starts = {tuple(round(float(v), 5) for v in seg[0]) for seg in axes.values()}
        self.assertEqual(len(starts), 1)
        cube_edge = lo["cube"][2] * 0.34 * 2.0
        self.assertGreater(_dist(*axes["x"]), cube_edge * 0.70)
        self.assertGreater(_dist(*axes["y"]), cube_edge * 0.70)
        self.assertGreater(_dist(*axes["z"]), cube_edge * 1.05)

    def test_cube_is_smaller_than_old_overlay(self):
        self.assertLessEqual(viewnav.layout(900, 700)["cube"][2], 112)
        self.assertLess(viewnav.layout(900, 700)["cube"][2], 130)

    def test_face_labels_are_cube_local_decals(self):
        rot = viewnav.rot_from_az_el(-35, 30)
        _lo, regs = viewnav.regions(rot, 900, 700)
        self.assertTrue(regs)
        for reg in regs:
            self.assertEqual(len(reg.get("label_quad", [])), 4)
            self.assertEqual(len(reg.get("poly", [])), 4)

    def test_finish_click_distinguishes_click_from_drag(self):
        nav = {"kind": "face", "view": (0.0, 0.0)}
        self.assertIs(viewnav.finish_click((nav, 10, 10), 13, 14), nav)
        self.assertIsNone(viewnav.finish_click((nav, 10, 10), 20, 10))
        self.assertIsNone(viewnav.finish_click(None, 10, 10))
        self.assertFalse(viewnav.drag_exceeded((nav, 10, 10), 13, 14))
        self.assertTrue(viewnav.drag_exceeded((nav, 10, 10), 15, 14))
        self.assertTrue(viewnav.drag_exceeded((nav, 10, 10), 13, 15))

    def test_rendered_cube_has_translucent_faces_and_opaque_marks(self):
        img = viewnav.draw_overlay_image(180, 210, viewnav.rot_from_az_el(-35, 30))
        alpha = list(img.getchannel("A").getdata())
        translucent = [a for a in alpha if 80 <= a <= 190]
        opaque = [a for a in alpha if a >= 240]
        self.assertGreater(len(translucent), 250)
        self.assertGreater(len(opaque), 80)
        self.assertLess(max(translucent), 200)


if __name__ == "__main__":
    unittest.main()
