from pathlib import Path
import sys
import unittest

import numpy as np
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

    def test_child_clipped_to_nothing_is_dropped_not_published_empty(self):
        # Real failure (jobs ee764ccd / c7a2df61): a small stray child sat outside the parent's
        # footprint, passed the mean-error gate, then clipped to zero alpha and failed verify().
        parent = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        parent.paste((200, 50, 50, 255), (0, 0, 32, 64))
        faithful = child((200, 50, 50), 255, (32, 64), [0, 0, 32, 64])
        stray = child((200, 50, 50), 255, (4, 4), [40, 40, 44, 44])
        clipped, fidelity = fit_children(parent, [faithful, stray])
        self.assertTrue(accepted(fidelity), fidelity)
        self.assertEqual([part["tag"] for part in clipped], ["L1.1"])
        self.assertTrue(np.any(np.asarray(clipped[0]["image"].getchannel("A")) > 8))


if __name__ == "__main__":
    unittest.main()
