from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from PIL import Image
from psd_tools import PSDImage

from .assets import batch_zip, png_zip, preview_manifest


class AssetTest(unittest.TestCase):
    def test_real_psd_offsets_stack_masks_and_zip(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = root / "one" / "result"
            (result / "input").mkdir(parents=True)
            size = (8, 8)
            color = PSDImage.new("RGBA", size)
            depth = PSDImage.new("L", size)
            for tag, rgb, gray, position in (
                ("face", (250, 0, 0, 255), 30, (2, 3)),
                ("front hair", (0, 220, 0, 128), 180, (3, 4)),
            ):
                x, y = position
                color.create_pixel_layer(Image.new("RGBA", (2, 2), rgb), name=tag, left=x, top=y)
                depth.create_pixel_layer(Image.new("L", (2, 2), gray), name=tag, left=x, top=y)
            color.save(result / "input.psd")
            depth.save(result / "input_depth.psd")
            Image.new("RGBA", size).save(result / "input" / "src_img.png")
            (result / "input.psd.json").write_text(json.dumps({"frame_size": size}), encoding="utf-8")

            manifest = preview_manifest(result)
            self.assertEqual([layer["tag"] for layer in manifest["layers"]], ["face", "front hair"])
            preview = result / "preview"
            with Image.open(preview / "composite.png") as composite, Image.open(preview / "mask-1.png") as mask:
                self.assertEqual(composite.getpixel((2, 3)), (250, 0, 0, 255))
                self.assertEqual(composite.getpixel((3, 4)), (125, 110, 0, 255))
                self.assertEqual(mask.getpixel((0, 0)), 128)
            with ZipFile(png_zip(result)) as bundle:
                record = json.loads(bundle.read("manifest.json"))
                with Image.open(BytesIO(bundle.read(record["layers"][1]["file"]))) as layer:
                    self.assertEqual(layer.size, size)
                    self.assertEqual(layer.getpixel((3, 4)), (0, 220, 0, 128))
                    self.assertEqual(layer.getpixel((0, 0))[3], 0)

            rows = [
                {"id": "one", "seq": 1, "filename": "same.png", "status": "succeeded", "error": None},
                {"id": "two", "seq": 2, "filename": "same.png", "status": "failed", "error": "CUDA_OOM"},
            ]
            original_zip = batch_zip(root, "batch", rows)
            with ZipFile(original_zip) as bundle:
                self.assertIn("1-same/image.psd", bundle.namelist())
                self.assertIn("1-same/depth.psd", bundle.namelist())
                self.assertIn("1-same/layers.zip", bundle.namelist())
                self.assertEqual(json.loads(bundle.read("manifest.json"))[1]["error"], "CUDA_OOM")
            updated_zip = batch_zip(root, "batch", rows[:1])
            self.assertNotEqual(updated_zip, original_zip)
            with ZipFile(updated_zip) as bundle:
                self.assertEqual(len(json.loads(bundle.read("manifest.json"))), 1)
            self.assertEqual(batch_zip(root, "batch", rows), original_zip)

    def test_color_only_engine_provides_preview_masks_and_zip_without_depth(self):
        with TemporaryDirectory() as temporary:
            result = Path(temporary)
            (result / "input").mkdir()
            color = PSDImage.new("RGBA", (8, 8))
            color.create_pixel_layer(Image.new("RGBA", (2, 2), (120, 30, 50, 128)), name="product", left=3, top=2)
            color.save(result / "input.psd")
            Image.new("RGBA", (8, 8)).save(result / "input" / "src_img.png")
            (result / "input.psd.json").write_text('{"frame_size":[8,8]}', encoding="utf-8")
            manifest = preview_manifest(result)
            self.assertFalse(manifest["has_depth"])
            self.assertNotIn("depth", manifest["layers"][0])
            self.assertTrue((result / "preview" / "mask-0.png").is_file())
            with ZipFile(png_zip(result)) as bundle:
                self.assertIn("01-product.png", bundle.namelist())

    def test_stale_preview_directory_without_manifest_is_rebuilt(self):
        with TemporaryDirectory() as temporary:
            result = Path(temporary)
            (result / "input").mkdir()
            color = PSDImage.new("RGBA", (8, 8))
            color.create_pixel_layer(Image.new("RGBA", (2, 2), (120, 30, 50, 255)), name="product", left=3, top=2)
            color.save(result / "input.psd")
            Image.new("RGBA", (8, 8)).save(result / "input" / "src_img.png")
            (result / "input.psd.json").write_text('{"frame_size":[8,8]}', encoding="utf-8")
            preview_manifest(result)
            # Simulate a partial loss: the directory survives but the manifest is gone.
            (result / "preview" / "manifest.json").unlink()
            (result / "preview" / "layer-0.png").unlink()
            manifest = preview_manifest(result)
            self.assertEqual([layer["tag"] for layer in manifest["layers"]], ["product"])
            self.assertTrue((result / "preview" / "layer-0.png").is_file())

    def test_preview_with_manifest_but_missing_assets_is_rebuilt(self):
        with TemporaryDirectory() as temporary:
            result = Path(temporary)
            (result / "input").mkdir()
            color = PSDImage.new("RGBA", (8, 8))
            color.create_pixel_layer(Image.new("RGBA", (2, 2), (120, 30, 50, 255)), name="product", left=3, top=2)
            color.save(result / "input.psd")
            Image.new("RGBA", (8, 8)).save(result / "input" / "src_img.png")
            (result / "input.psd.json").write_text('{"frame_size":[8,8],"reconstruction_mae":0.0,"prompt_tokens":600}', encoding="utf-8")
            manifest = preview_manifest(result)
            self.assertEqual(manifest["details"]["reconstruction_mae"], 0.0)
            self.assertEqual(manifest["details"]["prompt_tokens"], 600)
            (result / "preview" / "layer-0.png").unlink()
            (result / "preview" / "mask-0.png").unlink()
            rebuilt = preview_manifest(result)
            self.assertEqual([layer["tag"] for layer in rebuilt["layers"]], ["product"])
            self.assertTrue((result / "preview" / "layer-0.png").is_file())
            self.assertTrue((result / "preview" / "mask-0.png").is_file())

    def test_cached_preview_picks_up_new_sidecar_fields_without_rebuild(self):
        with TemporaryDirectory() as temporary:
            result = Path(temporary)
            (result / "input").mkdir()
            color = PSDImage.new("RGBA", (8, 8))
            color.create_pixel_layer(Image.new("RGBA", (2, 2), (120, 30, 50, 255)), name="product", left=3, top=2)
            color.save(result / "input.psd")
            Image.new("RGBA", (8, 8)).save(result / "input" / "src_img.png")
            (result / "input.psd.json").write_text('{"frame_size":[8,8]}', encoding="utf-8")
            cached = preview_manifest(result)
            self.assertNotIn("details", cached)
            # A newer engine run adds fields the cached manifest never saw.
            (result / "input.psd.json").write_text('{"frame_size":[8,8],"prompt_tokens":811}', encoding="utf-8")
            refreshed = preview_manifest(result)
            self.assertEqual(refreshed["details"]["prompt_tokens"], 811)
