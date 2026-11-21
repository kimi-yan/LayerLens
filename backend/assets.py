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


def sidecar_details(result):
    """The informative subset of the engine sidecar, re-read on every manifest load.

    Details are a derived view of the sidecar, not part of the cached preview: baking them into
    manifest.json would hide newly added fields (e.g. prompt_tokens) behind old caches forever.
    """
    path = result / "input.psd.json"
    if not path.is_file():
        return {}
    meta = json.loads(path.read_text(encoding="utf-8"))
    details = {}
    for key in ("prompt", "prompt_tokens", "reconstruction_mae", "dropped_empty_layers", "recursive_prompts", "recursive_fidelity"):
        value = meta.get(key)
        if value not in (None, "", [], {}):
            details[key] = value
    return details


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
        details = sidecar_details(result)
        if details:
            manifest["details"] = details
        derivation = result / "derivation.json"
        if derivation.is_file():
            manifest["derivation"] = json.loads(derivation.read_text(encoding="utf-8"))
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        publish_preview(temporary, preview)
    finally:
        if temporary.is_dir():
            rmtree(temporary)


def publish_preview(temporary, preview):
    """Copy the built assets over the live preview in place, manifest last.

    Renaming or deleting the preview directory fails on Windows while a response is still
    streaming one of its files, so every asset is overwritten in place instead. Leftover files
    from an older build are dropped best-effort; the manifest never references them.
    """
    preview.mkdir(exist_ok=True)
    names = {path.name for path in temporary.iterdir()}
    for name in sorted(names - {"manifest.json"}):
        copyfile(temporary / name, preview / name)
    copyfile(temporary / "manifest.json", preview / "manifest.json")
    for path in preview.iterdir():
        if path.name not in names:
            try:
                path.unlink()
            except OSError:
                pass


def preview_complete(preview, manifest):
    names = ["source.png", "composite.png"]
    if manifest.get("has_depth"):
        names.append("depth.png")
    for layer in manifest["layers"]:
        names.extend(layer[kind] for kind in ("image", "mask", "depth") if kind in layer)
    return all((preview / name).is_file() for name in names)


def preview_manifest(result):
    preview = result / "preview"
    manifest_path = preview / "manifest.json"
    with preview_lock:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
        # A manifest whose assets are gone (partial loss on disk) is as stale as no manifest at all.
        if manifest is None or not preview_complete(preview, manifest):
            try:
                build_preview(result)
            except (OSError, ValueError) as error:
                # Unreadable PSD: the result is missing, not a server error.
                raise FileNotFoundError(result / "input.psd") from error
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        details = sidecar_details(result)
        if details:
            manifest["details"] = details
    return manifest


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
        # Keep a concurrent preview rebuild from overwriting assets mid-read.
        with preview_lock:
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
            # Trashing or restoring a member changes the member hash; older archives of this
            # batch are orphans once the new one exists.
            for old in root.glob(f"batch-{batch_id}-*.zip"):
                if old != path:
                    try:
                        old.unlink()
                    except OSError:
                        pass
    finally:
        temporary.unlink(missing_ok=True)
    return path
