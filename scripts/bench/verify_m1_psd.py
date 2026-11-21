"""Verify an M1 color PSD, depth PSD and accompanying layer coordinates."""
import argparse
import json
from pathlib import Path

from PIL import Image
from psd_tools import PSDImage


def validate(psd_path, resolution):
    depth_path = psd_path.with_stem(psd_path.stem + "_depth")
    sidecar = json.loads(psd_path.with_suffix(".psd.json").read_text(encoding="utf-8"))
    color = PSDImage.open(psd_path)
    depth = PSDImage.open(depth_path)
    assert color.size == depth.size == (resolution, resolution)
    assert sidecar["frame_size"] == [resolution, resolution]
    assert color.depth == depth.depth == 8
    names = [layer.name for layer in color]
    assert names and len(names) == len(set(names))
    assert names == [layer.name for layer in depth]
    assert set(names) == set(sidecar["parts"])
    transparency = False
    for color_layer, depth_layer in zip(color, depth):
        tag = color_layer.name
        expected = tuple(sidecar["parts"][tag]["xyxy"])
        assert color_layer.bbox == depth_layer.bbox == expected, tag
        x0, y0, x1, y1 = expected
        assert 0 <= x0 < x1 <= resolution and 0 <= y0 < y1 <= resolution, tag
        rgba = color_layer.topil().convert("RGBA")
        assert rgba.size == depth_layer.topil().size == (x1 - x0, y1 - y0), tag
        alpha_min, alpha_max = rgba.getchannel("A").getextrema()
        assert alpha_max > 0, tag
        transparency |= alpha_min < 255
    assert transparency, "No layer preserved transparency"
    composite = color.composite(force=True).convert("RGBA")
    assert composite.getchannel("A").getbbox()
    source_path = psd_path.parent / psd_path.stem / "src_img.png"
    with Image.open(source_path) as source:
        assert source.size == color.size
    return {
        "color_psd": str(psd_path),
        "depth_psd": str(depth_path),
        "dimensions": list(color.size),
        "layer_count": len(names),
        "layer_names": names,
        "color_bytes": psd_path.stat().st_size,
        "depth_bytes": depth_path.stat().st_size,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("psd", type=Path)
    parser.add_argument("resolution", type=int)
    args = parser.parse_args()
    record = validate(args.psd, args.resolution)
    content = json.dumps(record, indent=2)
    args.psd.parent.joinpath("validation.json").write_text(content, encoding="utf-8")
    print(content)
