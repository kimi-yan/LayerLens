"""Render source, recomposition and every layer of a LayerLens result into one review image.

Usage: python scripts/bench/qwen_contact_sheet.py <result-dir> [<out.png>]
"""
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw
from psd_tools import PSDImage

TILE = 256


def checker(size, step=12):
    board = Image.new("RGB", size, (235, 235, 235))
    draw = ImageDraw.Draw(board)
    for y in range(0, size[1], step):
        for x in range((y // step % 2) * step, size[0], step * 2):
            draw.rectangle([x, y, x + step - 1, y + step - 1], fill=(200, 200, 200))
    return board


def tile(image, label):
    image = image.convert("RGBA")
    image.thumbnail((TILE, TILE))
    canvas = checker((TILE, TILE + 18))
    canvas.paste(image, ((TILE - image.width) // 2, (TILE - image.height) // 2), image)
    ImageDraw.Draw(canvas).text((4, TILE + 3), label, fill=(20, 20, 20))
    return canvas


if __name__ == "__main__":
    result = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else result / "contact-sheet.png"
    psd = PSDImage.open(result / "input.psd")
    meta = json.loads((result / "input.psd.json").read_text(encoding="utf-8"))
    flat = Image.new("RGBA", psd.size)
    tiles = [tile(Image.open(result / "input" / "src_img.png"), "source")]
    layers = []
    for layer in psd:
        full = Image.new("RGBA", psd.size)
        full.alpha_composite(layer.topil().convert("RGBA"), tuple(layer.bbox[:2]))
        flat.alpha_composite(full)
        layers.append(tile(full, f"{layer.name} bbox={list(layer.bbox)}"))
    tiles.append(tile(flat, f"recomposed mae={meta.get('reconstruction_mae')}"))
    tiles.extend(layers)
    sheet = Image.new("RGB", (TILE * len(tiles), TILE + 18), "white")
    for index, item in enumerate(tiles):
        sheet.paste(item, (index * TILE, 0))
    sheet.save(out)
    print(out)
