from pathlib import Path
import sys
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine" / "adapter"))
from qwen_run import accepted, fit_children, is_overlay  # noqa: E402


def child(color, alpha, size=(4, 4), xyxy=None):
    return {"tag": "L1.1", "image": Image.new("RGBA", size, (*color, alpha)), "xyxy": xyxy or [0, 0, *size]}


class RecursiveSplitGuardTest(unittest.TestCase):
    def test_faithful_split_is_accepted_and_clipped_to_the_parent(self):
        parent = Image.new("RGBA", (8, 8), (200, 50, 50, 255))
        parent.putpixel((0, 0), (0, 0, 0, 0))
        children = [child((200, 50, 50), 255, (8, 8)), child((200, 50, 50), 255, (2, 2), [2, 2, 4, 4])]
        clipped, fidelity = fit_children(parent, children)
        self.assertTrue(accepted(fidelity), fidelity)
        # The background child covered the parent's transparent corner; clipping removes it.
        self.assertEqual(clipped[0]["image"].getpixel((0, 0))[3], 0)
        self.assertEqual(clipped[0]["image"].getpixel((1, 0))[3], 255)
        self.assertEqual(clipped[1]["image"].getpixel((0, 0))[3], 255)

    def test_opaque_sheet_over_a_translucent_parent_is_rejected(self):
        # Coffee poster L4: a uniform 19% tint came back as an opaque off-white sheet.
        parent = Image.new("RGBA", (4, 4), (110, 84, 34, 48))
        _, fidelity = fit_children(parent, [child((238, 237, 234), 255), child((51, 48, 32), 56)])
        self.assertFalse(accepted(fidelity), fidelity)
        self.assertGreater(fidelity["alpha_error"], 150)

    def test_overlay_detection(self):
        self.assertTrue(is_overlay(Image.new("RGBA", (8, 8), (110, 84, 34, 48))))
        glass = Image.new("RGBA", (8, 8), (200, 220, 255, 60))
        glass.paste((255, 255, 255, 255), (0, 0, 2, 8))
        self.assertFalse(is_overlay(glass))


if __name__ == "__main__":
    unittest.main()
