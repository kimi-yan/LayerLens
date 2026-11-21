from io import BytesIO
from hashlib import sha256
import json
from pathlib import Path
import re
from shutil import copyfile, rmtree
from threading import Lock
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image, ImageOps
from psd_tools import PSDImage


thumbnail_lock = Lock()
preview_lock = Lock()
png_zip_lock = Lock()
batch_zip_lock = Lock()
ASSET_NAME = re.compile(r"(?:source|composite|depth|layer-\d+|mask-\d+|depth-\d+)\.png")


def thumbnail(source, folder):
    path = folder / "thumb-v1.png"
    if path.is_file():
        return path
    with thumbnail_lock:
        if not path.is_file():
            temporary = path.with_suffix(".tmp")
            try:
                with Image.open(source) as original:
                    image = ImageOps.exif_transpose(original).convert("RGBA")
                    image.thumbnail((112, 112))
                    image.save(temporary, format="PNG")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    return path


def build_preview(result: Path):
    color = PSDImage.open(result / "input.psd")
    depth_path = result / "input_depth.psd"
    depth = PSDImage.open(depth_path) if depth_path.is_file() else None
    if not len(color) or (depth is not None and len(color) != len(depth)):
        raise ValueError("Color and depth layer counts must match")
    meta = json.loads((result / "input.psd.json").read_text(encoding="utf-8"))
    if tuple(meta["frame_size"]) != color.size or (depth is not None and color.size != depth.size):
        raise ValueError("Color and depth canvases must match")
    preview = result / "preview"
    temporary = result / f".preview-{uuid4().hex}"
    temporary.mkdir()
    try:
        flat = Image.new("RGBA", color.size)
        scene_depth = Image.new("RGBA", color.size)
        layers = []
        for index, layer in enumerate(color):
            rgba = layer.topil().convert("RGBA")
            alpha = rgba.getchannel("A")
            bounds = list(layer.bbox)
            rgba.save(temporary / f"layer-{index}.png")
            alpha.save(temporary / f"mask-{index}.png")
            flat.alpha_composite(rgba, tuple(bounds[:2]))
            layers.append({
                "id": f"layer-{index}",
                "tag": layer.name,
                "bbox_xyxy": bounds,
                "z_index": index,
                "image": f"layer-{index}.png",
                "mask": f"mask-{index}.png",
            })
            if depth is not None:
                depth_layer = depth[index]
                if layer.name != depth_layer.name or layer.bbox != depth_layer.bbox:
                    raise ValueError(f"Depth layer is out of alignment: {layer.name}")
                gray = depth_layer.topil().convert("L")
                if gray.size != rgba.size:
                    raise ValueError(f"Depth pixels are out of alignment: {layer.name}")
                depth_rgba = gray.convert("RGBA")
                depth_rgba.putalpha(alpha)
                filename = f"depth-{index}.png"
                depth_rgba.save(temporary / filename)
                scene_depth.alpha_composite(depth_rgba, tuple(bounds[:2]))
                layers[-1]["depth"] = filename
        flat.save(temporary / "composite.png")
        if depth is not None:
            scene_depth.save(temporary / "depth.png")
        copyfile(result / "input" / "src_img.png", temporary / "source.png")
        manifest = {"width": color.width, "height": color.height, "layers": layers, "has_depth": depth is not None}
        details = {key: meta[key] for key in ("prompt", "reconstruction_mae", "dropped_empty_layers", "recursive_prompts", "recursive_fidelity") if meta.get(key)}
        if details:
            manifest["details"] = details
        derivation = result / "derivation.json"
        if derivation.is_file():
            manifest["derivation"] = json.loads(derivation.read_text(encoding="utf-8"))
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        temporary.rename(preview)
    finally:
        if temporary.is_dir():
            rmtree(temporary)


def preview_manifest(result):
    manifest = result / "preview" / "manifest.json"
    if not manifest.is_file():
        with preview_lock:
            if not manifest.is_file():
                build_preview(result)
    return json.loads(manifest.read_text(encoding="utf-8"))


def safe_stem(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(name).stem).strip(" .")[:100] or "image"


def png_zip(result):
    path = result / "layers.zip"
    if path.is_file():
        return path
    manifest = preview_manifest(result)
    with png_zip_lock:
        if path.is_file():
            return path
        temporary = result / "layers.zip.tmp"
        try:
            with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=3) as bundle:
                records = []
                for layer in manifest["layers"]:
                    filename = f"{layer['z_index'] + 1:02d}-{safe_stem(layer['tag'])}.png"
                    canvas = Image.new("RGBA", (manifest["width"], manifest["height"]))
                    with Image.open(result / "preview" / layer["image"]) as image:
                        canvas.paste(image, tuple(layer["bbox_xyxy"][:2]))
                    buffer = BytesIO()
                    canvas.save(buffer, "PNG", compress_level=3)
                    bundle.writestr(filename, buffer.getvalue())
                    records.append({"file": filename, "tag": layer["tag"], "z_index": layer["z_index"]})
                bundle.writestr("manifest.json", json.dumps({**manifest, "layers": records}, ensure_ascii=False))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def batch_zip(root, batch_id, rows):
    members = sha256(",".join(row["id"] for row in rows).encode()).hexdigest()[:16]
    path = root / f"batch-{batch_id}-{members}.zip"
    if path.is_file():
        return path
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        with ZipFile(temporary, "w") as bundle:
            for row in rows:
                if row["status"] == "succeeded":
                    result = root / row["id"] / "result"
                    prefix = f"{row['seq']}-{safe_stem(row['filename'])}"
                    for source, filename in (
                        (result / "input.psd", "image.psd"),
                        (result / "input_depth.psd", "depth.psd"),
                        (png_zip(result), "layers.zip"),
                    ):
                        if filename == "depth.psd" and not source.is_file():
                            continue
                        bundle.write(source, f"{prefix}/{filename}")
            bundle.writestr("manifest.json", json.dumps([
                {"job_id": row["id"], "filename": row["filename"], "status": row["status"], "error": row["error"]}
                for row in rows
            ], ensure_ascii=False))
        with batch_zip_lock:
            if not path.is_file():
                temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
