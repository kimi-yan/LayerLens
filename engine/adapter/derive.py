"""Apply upstream See-through secondary splitting and keep the result artifact contract."""
import json
from pathlib import Path
from shutil import copyfile
import sys

from run import verify
from runner_io import guard_owner


def derive(parent, work, method, tags):
    import numpy as np
    from utils.inference_utils import cluster_inpaint_part, dump_parts_psd, part_lr_split, psd2partdicts, save_part
    from utils.torch_utils import seed_everything

    seed_everything(42)
    original = psd2partdicts(str(parent / "input.psd"))
    parts = original["parts"]
    split, unchanged = [], []
    for tag in tags:
        current = parts[tag]
        mask = current["img"][..., 3] > 10
        if not mask.any():
            unchanged.append(tag)
            continue
        normalized = current["depth"].astype(np.float32)
        payload = {**current, "depth": normalized, "mask": mask}
        if method == "depth":
            if np.unique(normalized[mask]).size < 2:
                unchanged.append(tag)
                continue
            candidates = cluster_inpaint_part(**payload)
            children = {f"{tag}-{index}": part for index, part in enumerate(candidates)}
        else:
            children = part_lr_split(tag, payload)
        if len(children) != 2 or any(
            not np.any(child["img"][..., 3] > 10) or not np.isfinite(child["depth_median"])
            for child in children.values()
        ):
            unchanged.append(tag)
            continue
        labels = set(children)
        if labels & parts.keys():
            unchanged.append(tag)
            continue
        del parts[tag]
        for label, part in children.items():
            saved = save_part(label, str(work), part, crop=False, save_to_disk=False)
            saved["xyxy"] = [int(position) for position in saved["xyxy"]]
            saved["depth_median"] = float(saved["depth_median"])
            parts[label] = saved
        split.append(tag)
    if not split:
        sys.exit("LAYERLENS_NO_SPLIT")
    for part in parts.values():
        if part["depth"].dtype != np.uint8:
            part["depth"] = np.rint(np.clip(part["depth"], 0, 1) * 255).astype(np.uint8)
    dump_parts_psd(parts, original["frame_size"], str(work / "input.psd"))
    (work / "input").mkdir()
    copyfile(parent / "input" / "src_img.png", work / "input" / "src_img.png")
    (work / "derivation.json").write_text(json.dumps({"split": split, "unchanged": unchanged}), encoding="utf-8")
    verify(work, "input", original["frame_size"][0])


if __name__ == "__main__":
    guard_owner()
    source, output, operation, *targets = sys.argv[1:]
    folder = Path(output)
    progress = folder / "progress.ndjson"
    with progress.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({"stage": "postprocess"}) + "\n")
    derive(Path(source), folder, operation, targets)
    with progress.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"stage": "complete"}) + "\n")
