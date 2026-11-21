"""Run Qwen-Image-Layered in its isolated environment and write the LayerLens result contract.

12 GB plan: the Qwen2.5-VL text encoder (NF4) captions and encodes prompts first and is then
released. The GGUF transformer then streams its blocks onto the GPU with group offload.
"""
import argparse
import gc
import json
from pathlib import Path
import re
import sys
import time

from runner_io import Reporter, Steps, guard_owner


# Alpha at or below this value counts as empty when cropping layer bounds.
ALPHA_FLOOR = 8
# Recursive split acceptance, 0-255 scale over the parent's footprint. The model always paints its
# first frame as an opaque background, so a translucent parent layer comes back as an opaque sheet
# (coffee poster L4: alpha error 207, color error 168). Accepted splits measured 2.6 and 4.7.
SPLIT_MAX_ALPHA_ERROR = 32
SPLIT_MAX_COLOR_ERROR = 24
# A parent whose 99.5th alpha percentile stays below this is a tint or overlay with no solid content.
OVERLAY_ALPHA = 64
TAG = re.compile(r"L\d+(?:\.\d+)*")


class Metrics:
    def __init__(self):
        self.started = time.perf_counter()
        self.phases = {}
        self.peak_reserved = 0

    def phase(self, name, began, **extra):
        import torch

        reserved = torch.cuda.max_memory_reserved()
        self.peak_reserved = max(self.peak_reserved, reserved)
        self.phases[name] = {"seconds": round(time.perf_counter() - began, 2), "peak_reserved_mib": reserved // 2**20, **extra}
        torch.cuda.reset_peak_memory_stats()

    def summary(self):
        return {"seconds": round(time.perf_counter() - self.started, 2),
                "peak_reserved_mib": self.peak_reserved // 2**20, "phases": self.phases}


def free_cuda():
    import torch

    gc.collect()
    torch.cuda.empty_cache()


def bucket_size(image, resolution):
    from diffusers.pipelines.qwenimage.pipeline_qwenimage_layered import calculate_dimensions

    width, height = calculate_dimensions(resolution * resolution, image.width / image.height)
    return width, height


def caption_view(image):
    """Flatten transparency onto white for the captioner, which reads RGB only.

    Pixels under alpha 0 often hold arbitrary colors in recursive-split layers.
    """
    from PIL import Image

    if image.mode != "RGBA" or image.getchannel("A").getextrema()[0] == 255:
        return image.convert("RGB")
    flat = Image.new("RGBA", image.size, (255, 255, 255, 255))
    flat.alpha_composite(image)
    return flat.convert("RGB")


def encode_prompts(models, text_encoder, jobs, report, metrics):
    """Caption images that have no prompt and encode positive and negative prompts.

    ``text_encoder`` is a pre-quantized NF4 checkpoint (scripts/engines/quantize_qwen_text_encoder.py).
    Quantizing the BF16 encoder on load peaks near 15 GB on the GPU.
    """
    import torch
    from diffusers import QwenImageLayeredPipeline
    from transformers import Qwen2_5_VLForConditionalGeneration

    began = time.perf_counter()
    report("loading")
    encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(text_encoder, dtype=torch.bfloat16, device_map={"": 0})
    pipe = QwenImageLayeredPipeline.from_pretrained(models, transformer=None, vae=None, text_encoder=encoder,
                                                    torch_dtype=torch.bfloat16)
    metrics.phase("text_encoder_load", began)
    began = time.perf_counter()
    for index, job in enumerate(jobs, 1):
        options = job["options"]
        if not job["prompt"]:
            report("captioning", index, len(jobs))
            preview = caption_view(pipe.image_processor.resize(job["image"], job["height"], job["width"]))
            with torch.inference_mode():
                job["prompt"] = pipe.get_image_caption(preview, use_en_prompt=options["caption_language"] == "en", device="cuda")
        report("encoding", index, len(jobs))
        # _get_qwen_prompt_embeds always returns the mask; encode_prompt drops an all-ones mask, and the
        # pipeline then cannot tell that precomputed negative embeddings enable true CFG.
        # Keep the natural token length (the helper pads only to the batch's real length): slicing to
        # 512 here would silently halve long manual prompts. decompose() caps at the pipeline's 1024.
        with torch.inference_mode():
            job["prompt_embeds"] = [value.cpu() for value in
                                    pipe._get_qwen_prompt_embeds(job["prompt"], device="cuda")]
            job["negative_embeds"] = [value.cpu() for value in
                                      pipe._get_qwen_prompt_embeds(options["negative_prompt"] or " ", device="cuda")]
    metrics.phase("captioning_and_encoding", began)
    del pipe, encoder
    free_cuda()


class Placeholder:
    """Stands in for the released text encoder; the pipeline only reads its dtype."""

    def __init__(self, dtype):
        self.dtype = dtype


def restore_unquantized_modules(transformer):
    """Dequantize GGUF tensors outside GGUFLinear layers.

    diffusers only swaps Linear layers for GGUF-aware ones. Other modules, such as the layered
    model's ``addition_t_embedding``, would read raw GGUF bytes (BF16 shows up as twice the width).
    """
    import gguf
    import torch
    from diffusers.quantizers.gguf.utils import GGUFLinear, GGUFParameter, dequantize_gguf_tensor

    plain = {gguf.GGMLQuantizationType.F32: torch.float32, gguf.GGMLQuantizationType.F16: torch.float16}
    restored = []
    for name, module in transformer.named_modules():
        if isinstance(module, GGUFLinear):
            continue
        for key, parameter in list(module.named_parameters(recurse=False)):
            if isinstance(parameter, GGUFParameter):
                if parameter.quant_type in plain:
                    raw = parameter.as_tensor().view(torch.uint8)
                    value = raw.view(plain[parameter.quant_type]).reshape(parameter.quant_shape)
                else:
                    value = dequantize_gguf_tensor(parameter)
                value = value.to(torch.bfloat16)
                setattr(module, key, torch.nn.Parameter(value, requires_grad=False))
                restored.append(f"{name}.{key}")
    return restored


def load_layer_pipeline(models, gguf, offload, report, metrics, low_cpu_mem=False, blocks_per_group=1):
    import torch
    from diffusers import (AutoencoderKLQwenImage, FlowMatchEulerDiscreteScheduler, GGUFQuantizationConfig,
                           QwenImageLayeredPipeline, QwenImageTransformer2DModel)

    class LayerPipeline(QwenImageLayeredPipeline):
        stage_report = None

        @property
        def _execution_device(self):
            return torch.device("cuda")

        def progress_bar(self, iterable=None, total=None):
            return Steps(self.stage_report, "layering", total, 1)

    began = time.perf_counter()
    report("loading")
    transformer = QwenImageTransformer2DModel.from_single_file(
        str(gguf), quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
        config=str(models), subfolder="transformer", torch_dtype=torch.bfloat16)
    print("Restored non-linear GGUF tensors:", restore_unquantized_modules(transformer), flush=True)
    if offload == "group":
        transformer.enable_group_offload(onload_device=torch.device("cuda"), offload_device=torch.device("cpu"),
                                         offload_type="block_level", num_blocks_per_group=blocks_per_group, use_stream=True,
                                         low_cpu_mem_usage=low_cpu_mem)
    elif offload == "none":
        transformer.to("cuda")
    else:
        raise ValueError(f"Unsupported offload mode {offload}")
    vae = AutoencoderKLQwenImage.from_pretrained(models, subfolder="vae", torch_dtype=torch.bfloat16).to("cuda")
    scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(models, subfolder="scheduler")
    pipe = LayerPipeline(scheduler=scheduler, vae=vae, text_encoder=None, tokenizer=None, processor=None, transformer=transformer)
    # register_modules stores the encoder in the config; the dtype holder is only read during preprocessing.
    object.__setattr__(pipe, "text_encoder", Placeholder(torch.bfloat16))
    pipe.stage_report = report
    metrics.phase("transformer_load", began)
    return pipe


def decompose(pipe, job, report, metrics, label):
    import torch

    options = job["options"]
    began = time.perf_counter()
    step_ends = []

    def on_step_end(_pipe, step, _timestep, tensors):
        step_ends.append(time.perf_counter())
        if step == options["inference_steps"] - 1:
            report("decoding")
        return tensors

    positive, positive_mask = job["prompt_embeds"]
    negative, negative_mask = job["negative_embeds"]
    cuda = lambda value: value.to("cuda") if value is not None else None
    with torch.inference_mode():
        output = pipe(
            image=job["image"], prompt=job["prompt"],
            prompt_embeds=cuda(positive), prompt_embeds_mask=cuda(positive_mask),
            negative_prompt_embeds=cuda(negative), negative_prompt_embeds_mask=cuda(negative_mask),
            true_cfg_scale=options["true_cfg_scale"], layers=job["layers"],
            num_inference_steps=options["inference_steps"], resolution=options["resolution"],
            cfg_normalize=options["cfg_normalize"], generator=torch.Generator(device="cuda").manual_seed(options["seed"]),
            # The pipeline default of 512 would cut long manual prompts; 1024 is its hard cap and costs
            # nothing for shorter ones because embeds carry only their real token length.
            max_sequence_length=1024,
            callback_on_step_end=on_step_end,
        )
    # Step 1 also covers VAE encoding and latent setup; the tail after the last step is VAE decoding.
    finished = time.perf_counter()
    timing = {"first_step_seconds": round(step_ends[0] - began, 2),
              "seconds_per_step": round((step_ends[-1] - step_ends[0]) / max(len(step_ends) - 1, 1), 2),
              "decode_seconds": round(finished - step_ends[-1], 2)}
    metrics.phase(label, began, **timing)
    return output.images[0]


def alpha_bounds(image):
    return image.getchannel("A").point(lambda value: 255 if value > ALPHA_FLOOR else 0).getbbox()


def write_contract(work, size, source, parts, sidecar):
    """Write input.psd, input.psd.json and input/src_img.png. ``parts`` is ordered bottom to top."""
    from PIL import Image
    from psd_tools import PSDImage
    from psd_tools.api.layers import PixelLayer
    from psd_tools.constants import Compression

    flat = Image.new("RGBA", size)
    for part in parts:
        flat.alpha_composite(part["image"], tuple(part["xyxy"][:2]))
    psd = PSDImage.frompil(flat)
    for part in parts:
        left, top = part["xyxy"][:2]
        psd.append(PixelLayer.frompil(part["image"], psd, name=part["tag"], top=top, left=left, compression=Compression.RLE))
    psd.save(work / "input.psd")
    (work / "input").mkdir(exist_ok=True)
    source.save(work / "input" / "src_img.png")
    sidecar = {**sidecar, "frame_size": list(size),
               "parts": {part["tag"]: {"xyxy": part["xyxy"], "z": index} for index, part in enumerate(parts)}}
    (work / "input.psd.json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=1), encoding="utf-8")


def verify(work):
    """Qwen contract: any aspect ratio, color PSD only, RGBA layers aligned with the sidecar."""
    import numpy as np
    from PIL import Image
    from psd_tools import PSDImage

    color = PSDImage.open(work / "input.psd")
    metadata = json.loads((work / "input.psd.json").read_text(encoding="utf-8"))
    if (work / "input_depth.psd").exists():
        raise ValueError("Qwen results do not carry a depth PSD")
    with Image.open(work / "input" / "src_img.png") as source:
        if source.size != color.size:
            raise ValueError("Source image does not match the PSD canvas")
    if tuple(metadata["frame_size"]) != color.size:
        raise ValueError("Engine PSD dimensions do not match the sidecar")
    layers = list(color)
    names = [layer.name for layer in layers]
    if not layers or len(set(names)) != len(names) or any(not TAG.fullmatch(name) for name in names):
        raise ValueError("Engine PSD layer names are missing, duplicated or malformed")
    if names != list(metadata["parts"]):
        raise ValueError("Engine PSD layer order does not match the sidecar")
    width, height = color.size
    for layer in layers:
        left, top, right, bottom = layer.bbox
        if tuple(metadata["parts"][layer.name]["xyxy"]) != layer.bbox:
            raise ValueError(f"Engine PSD offsets do not match the sidecar: {layer.name}")
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError(f"Layer lies outside the canvas: {layer.name}")
        pixels = layer.topil()
        if pixels.mode != "RGBA" or pixels.size != (right - left, bottom - top):
            raise ValueError(f"Layer is not an aligned RGBA raster: {layer.name}")
        if not np.any(np.asarray(pixels.getchannel("A")) > ALPHA_FLOOR):
            raise ValueError(f"Layer is empty: {layer.name}")


def reconstruction_error(parts, size, source):
    """Mean absolute RGB error of the recomposed layers over opaque source pixels, 0-255 scale."""
    import numpy as np
    from PIL import Image

    flat = Image.new("RGBA", size)
    for part in parts:
        flat.alpha_composite(part["image"], tuple(part["xyxy"][:2]))
    rebuilt = np.asarray(flat, dtype=np.float32)
    original = np.asarray(source.convert("RGBA"), dtype=np.float32)
    visible = original[..., 3] > 127
    if not visible.any():
        return None
    return round(float(np.abs(rebuilt[..., :3] - original[..., :3])[visible].mean()), 2)


def collect(images, tag_prefix, offset=(0, 0), scale_to=None):
    """Turn model frames into cropped parts; empty frames are dropped and reported."""
    from PIL import Image

    parts, dropped = [], []
    for index, frame in enumerate(images, 1):
        frame = frame.convert("RGBA")
        if scale_to is not None and frame.size != scale_to:
            frame = frame.resize(scale_to, Image.Resampling.LANCZOS)
        tag = f"{tag_prefix}{index}"
        bounds = alpha_bounds(frame)
        if bounds is None:
            dropped.append(tag)
            continue
        left, top, right, bottom = bounds
        parts.append({"tag": tag, "image": frame.crop(bounds),
                      "xyxy": [left + offset[0], top + offset[1], right + offset[0], bottom + offset[1]]})
    return parts, dropped


def is_overlay(image):
    import numpy as np

    return float(np.percentile(np.asarray(image.getchannel("A")), 99.5)) < OVERLAY_ALPHA


def fit_children(parent, children):
    """Check that ``children`` recompose ``parent`` and clip them to its alpha.

    ``parent`` is the cropped parent RGBA; ``children`` hold images and xyxy relative to the same
    crop. Errors compare premultiplied pixels over the union footprint on the 0-255 scale. A sub-layer
    cannot show where its parent was transparent, so each child's alpha is capped by the parent's;
    children that clip away to nothing are dropped from the returned list.
    """
    import numpy as np
    from PIL import Image

    flat = Image.new("RGBA", parent.size)
    for child in children:
        flat.alpha_composite(child["image"], tuple(child["xyxy"][:2]))
    source = np.asarray(parent, dtype=np.float32) / 255
    rebuilt = np.asarray(flat, dtype=np.float32) / 255
    floor = ALPHA_FLOOR / 255
    region = (source[..., 3] > floor) | (rebuilt[..., 3] > floor)
    if not region.any():
        return children, {"alpha_error": 0.0, "color_error": 0.0}
    alpha_error = np.abs(source[..., 3] - rebuilt[..., 3])[region].mean() * 255
    color_error = np.abs(source[..., :3] * source[..., 3:] - rebuilt[..., :3] * rebuilt[..., 3:])[region].mean() * 255
    cap = parent.getchannel("A")
    clipped = []
    for child in children:
        left, top, right, bottom = child["xyxy"]
        image = child["image"].copy()
        image.putalpha(Image.fromarray(np.minimum(np.asarray(image.getchannel("A")),
                                                  np.asarray(cap.crop((left, top, right, bottom))))))
        # A child whose content lay outside the parent's footprint clips away to nothing; an
        # empty layer would fail the output contract, so drop it instead of publishing it.
        if np.any(np.asarray(image.getchannel("A")) > ALPHA_FLOOR):
            clipped.append({**child, "image": image})
    return clipped, {"alpha_error": round(float(alpha_error), 2), "color_error": round(float(color_error), 2)}


def accepted(fidelity):
    return fidelity["alpha_error"] <= SPLIT_MAX_ALPHA_ERROR and fidelity["color_error"] <= SPLIT_MAX_COLOR_ERROR


def run_layers(request, report, metrics):
    from PIL import Image, ImageOps

    models, work, options = Path(request["models"]), Path(request["work"]), request["options"]
    with Image.open(request["source"]) as original:
        image = ImageOps.exif_transpose(original).convert("RGBA")
    width, height = bucket_size(image, options["resolution"])
    job = {"image": image, "options": options, "prompt": options["prompt"].strip(), "layers": options["layers"],
           "width": width, "height": height}
    encode_prompts(models, Path(request["text_encoder"]), [job], report, metrics)
    pipe = load_layer_pipeline(models, Path(request["gguf"]), request["offload"], report, metrics,
                               request.get("low_cpu_mem", False), request.get("blocks_per_group", 1))
    frames = decompose(pipe, job, report, metrics, "layering")
    report("exporting")
    size = frames[0].size
    source = image.resize(size, Image.Resampling.LANCZOS)
    parts, dropped = collect(frames, "L")
    if not parts:
        raise ValueError("The model returned only empty layers")
    sidecar = {"engine": "qwen-image-layered", "weights": request["weights"], "prompt": job["prompt"],
               "prompt_tokens": int(job["prompt_embeds"][0].shape[1]),
               "dropped_empty_layers": dropped, "reconstruction_mae": reconstruction_error(parts, size, source)}
    return size, source, parts, sidecar


def write_derivation(work, split, unchanged, rejected):
    (work / "derivation.json").write_text(json.dumps({"split": split, "unchanged": unchanged, "rejected": rejected}),
                                          encoding="utf-8")


def run_recursive(request, report, metrics):
    from PIL import Image
    from psd_tools import PSDImage

    models, work, options = Path(request["models"]), Path(request["work"]), request["options"]
    parent = Path(request["parent"])
    color = PSDImage.open(parent / "input.psd")
    meta = json.loads((parent / "input.psd.json").read_text(encoding="utf-8"))
    parts = [{"tag": layer.name, "image": layer.topil().convert("RGBA"), "xyxy": list(layer.bbox)} for layer in color]
    by_tag = {part["tag"]: part for part in parts}
    jobs, rejected = [], {}
    for tag in request["targets"]:
        part = by_tag[tag]
        if is_overlay(part["image"]):
            rejected[tag] = {"reason": "translucent_overlay"}
            continue
        width, height = bucket_size(part["image"], options["resolution"])
        # Sub-layers get their own caption: the parent prompt describes the whole image.
        jobs.append({"tag": tag, "image": part["image"], "options": options, "prompt": "",
                     "layers": request["layers"], "width": width, "height": height})
    if not jobs:
        write_derivation(work, [], [], rejected)
        sys.exit("LAYERLENS_SPLIT_REJECTED")
    encode_prompts(models, Path(request["text_encoder"]), jobs, report, metrics)
    pipe = load_layer_pipeline(models, Path(request["gguf"]), request["offload"], report, metrics,
                               request.get("low_cpu_mem", False), request.get("blocks_per_group", 1))
    split, unchanged, captions, fidelities = [], [], {}, {}
    for index, job in enumerate(jobs, 1):
        frames = decompose(pipe, job, report, metrics, f"layering_{index}")
        parent_part = by_tag[job["tag"]]
        left, top, right, bottom = parent_part["xyxy"]
        children, _ = collect(frames, job["tag"] + ".", scale_to=(right - left, bottom - top))
        if len(children) < 2:
            unchanged.append(job["tag"])
            continue
        children, fidelity = fit_children(parent_part["image"], children)
        # Clipping to the parent can empty stray children; without two survivors there is no split.
        if len(children) < 2:
            unchanged.append(job["tag"])
            continue
        print(f"Recursive split {job['tag']}: {fidelity}", flush=True)
        if not accepted(fidelity):
            rejected[job["tag"]] = {"reason": "not_recomposable", **fidelity}
            continue
        for child in children:
            child["xyxy"] = [child["xyxy"][0] + left, child["xyxy"][1] + top, child["xyxy"][2] + left, child["xyxy"][3] + top]
        position = parts.index(parent_part)
        parts[position:position + 1] = children
        split.append(job["tag"])
        captions[job["tag"]] = job["prompt"]
        fidelities[job["tag"]] = fidelity
    if not split:
        write_derivation(work, split, unchanged, rejected)
        sys.exit("LAYERLENS_SPLIT_REJECTED" if rejected else "LAYERLENS_NO_SPLIT")
    report("exporting")
    with Image.open(parent / "input" / "src_img.png") as source:
        source = source.convert("RGBA")
    size = tuple(meta["frame_size"])
    write_derivation(work, split, unchanged, rejected)
    sidecar = {"engine": "qwen-image-layered", "weights": request["weights"], "prompt": meta.get("prompt"),
               "recursive_prompts": {**meta.get("recursive_prompts", {}), **captions},
               "recursive_fidelity": {**meta.get("recursive_fidelity", {}), **fidelities},
               "reconstruction_mae": reconstruction_error(parts, size, source)}
    return size, source, parts, sidecar


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("progress")
    parser.add_argument("--request", required=True)
    parser.add_argument("--no-owner", action="store_true", help="benchmark mode without the LayerLens worker")
    arguments = parser.parse_args()
    if not arguments.no_owner:
        guard_owner()
    report = Reporter(arguments.progress)
    request = json.loads(arguments.request)
    metrics = Metrics()
    work = Path(request["work"])
    runner = run_recursive if request["mode"] == "recursive" else run_layers
    size, source, parts, sidecar = runner(request, report, metrics)
    write_contract(work, size, source, parts, {**sidecar, "metrics": metrics.summary()})
    report("validating")
    verify(work)
    report("complete")
