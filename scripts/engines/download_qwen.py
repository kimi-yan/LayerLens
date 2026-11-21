"""Download the pinned Qwen-Image-Layered files into data/cache/models.

Usage, from the workspace root:
  .local\\qwen-venv\\Scripts\\python.exe scripts\\engines\\download_qwen.py [Q4_K_M ...]

The diffusers pipeline comes from Qwen/Qwen-Image-Layered without its 41 GB BF16 transformer
(about 16 GiB). The transformer comes from unsloth's GGUF build instead; Q4_K_M (12 GiB) is
the default and the only quantization the service has been measured with.
"""
from pathlib import Path, PurePosixPath
import sys

import hub
from engine.adapter.catalog import QWEN_GGUF, QWEN_PIPELINE, QWEN_QUANTS


def plan(quants):
    unknown = [quant for quant in quants if quant not in QWEN_QUANTS]
    if unknown:
        raise SystemExit(f"Unknown quantization {', '.join(unknown)}. Choose from {', '.join(QWEN_QUANTS)}.")
    entries = []
    owner, repo, revision = QWEN_PIPELINE
    for file in hub.siblings(owner, repo, revision):
        name = PurePosixPath(file["rfilename"])
        # The BF16 transformer shards are replaced by the GGUF file.
        if name.parts[0] == "transformer" and name.name != "config.json":
            continue
        if name.suffix in {".json", ".txt", ".safetensors", ".jinja"}:
            entries.append((hub.MODELS / "qwen-image-layered" / Path(*name.parts), hub.urls(owner, repo, revision, name), file))
    owner, repo, revision = QWEN_GGUF
    wanted = {QWEN_QUANTS[quant] for quant in quants}
    for file in hub.siblings(owner, repo, revision):
        if file["rfilename"] in wanted:
            name = PurePosixPath(file["rfilename"])
            entries.append((hub.MODELS / "qwen-image-layered-gguf" / name.name, hub.urls(owner, repo, revision, name), file))
    if len([entry for entry in entries if entry[0].suffix == ".gguf"]) != len(wanted):
        raise SystemExit("A requested GGUF file is missing from the pinned revision")
    return entries


if __name__ == "__main__":
    hub.run(plan(sys.argv[1:] or ["Q4_K_M"]), "Qwen-Image-Layered files")
