"""Download the pinned See-through weights into data/cache.

Usage, from the workspace root:
  .local\\seethrough-venv\\Scripts\\python.exe scripts\\engines\\download_seethrough.py [--skip-lama]

LayerDiff and Marigold are required (about 12.6 GiB). The LaMa inpainting checkpoint (195 MiB)
is only used by the front/back split; skip it with --skip-lama.
"""
from pathlib import Path, PurePosixPath
import sys

import hub
from engine.adapter.catalog import LAMA_SHA256, LAMA_SIZE, LAMA_URL, SEETHROUGH_WEIGHTS

FILE_TYPES = {".json", ".txt", ".model", ".safetensors"}
LAMA = hub.ROOT / "data" / "cache" / "torch" / "hub" / "checkpoints" / "lama_large_512px.ckpt"


def plan():
    entries = []
    for alias, (repo, revision) in SEETHROUGH_WEIGHTS.items():
        for file in hub.siblings("layerdifforg", repo, revision):
            name = PurePosixPath(file["rfilename"])
            if name.suffix in FILE_TYPES:
                # The ModelScope copy lives under the author's account, not the organisation.
                sources = [url.replace("/models/layerdifforg/", "/models/ljsabc/") for url in hub.urls("layerdifforg", repo, revision, name)]
                entries.append((hub.MODELS / alias / Path(*name.parts), sources, file))
    return entries


if __name__ == "__main__":
    entries = plan()
    if "--skip-lama" not in sys.argv:
        mirror = LAMA_URL.replace("https://huggingface.co/", "https://hf-mirror.com/")
        entries.append((LAMA, [mirror, LAMA_URL], {"size": LAMA_SIZE, "lfs": {"sha256": LAMA_SHA256}}))
    hub.run(entries, "See-through weights")
