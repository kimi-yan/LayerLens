"""Measure the LayerLens Qwen runner on the local GPU: dedicated/shared VRAM, RAM and timings.

Usage (from the workspace root, inside .local/qwen-venv):
  python scripts/bench/measure_qwen.py --image data/samples/graphic/qwen-01.png --out output/qwen-bench/run1 \
      --resolution 640 --layers 4 --steps 50
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys
from threading import Event, Thread
from time import perf_counter

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "engine" / "adapter"))
from measure_m1 import sample_windows_memory  # noqa: E402


def sample_ram(stop, peaks):
    process = psutil.Process(os.getpid())
    while not stop.is_set():
        peaks["rss"] = max(peaks.get("rss", 0), process.memory_info().rss)
        peaks["system_used"] = max(peaks.get("system_used", 0), psutil.virtual_memory().used)
        stop.wait(0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=640)
    parser.add_argument("--layers", type=int, default=4)
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--cfg", type=float, default=4.0)
    parser.add_argument("--seed", type=int, default=777)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--quant", default="Q4_K_M")
    parser.add_argument("--offload", default="group")
    parser.add_argument("--low-cpu-mem", action="store_true")
    parser.add_argument("--blocks-per-group", type=int, default=1)
    args = parser.parse_args()

    models = ROOT / "data" / "cache" / "models"
    args.out.mkdir(parents=True, exist_ok=False)
    request = {
        "mode": "layers", "source": str(args.image.resolve()), "work": str(args.out.resolve()),
        "models": str(models / "qwen-image-layered"),
        "gguf": str(models / "qwen-image-layered-gguf" / f"qwen-image-layered-{args.quant}.gguf"),
        "weights": f"unsloth/Qwen-Image-Layered-GGUF {args.quant}", "text_encoder": str(models / "qwen-image-layered-te-nf4"), "offload": args.offload,
        "low_cpu_mem": args.low_cpu_mem, "blocks_per_group": args.blocks_per_group,
        "options": {"resolution": args.resolution, "layers": args.layers, "inference_steps": args.steps,
                    "true_cfg_scale": args.cfg, "seed": args.seed, "prompt": args.prompt, "negative_prompt": " ",
                    "caption_language": "en", "cfg_normalize": True},
    }
    stop, vram, ram = Event(), {}, {}
    monitors = [Thread(target=sample_windows_memory, args=(stop, vram), daemon=True),
                Thread(target=sample_ram, args=(stop, ram), daemon=True)]
    for monitor in monitors:
        monitor.start()
    sys.argv = [str(ROOT / "engine" / "adapter" / "qwen_run.py"), str(args.out / "progress.ndjson"),
                "--request", json.dumps(request), "--no-owner"]
    start = perf_counter()
    success = False
    try:
        runpy.run_path(sys.argv[0], run_name="__main__")
        success = True
    finally:
        stop.set()
        for monitor in monitors:
            monitor.join()
        sidecar = args.out / "input.psd.json"
        summary = {
            "image": str(args.image), "success": success, "seconds": round(perf_counter() - start, 2),
            "windows_process_peak_mib": {key: value // 2**20 for key, value in vram.items()},
            "ram_peak_mib": {key: value // 2**20 for key, value in ram.items()},
            "request": request,
            "runner": json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else None,
        }
        (args.out / "measurement.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
        print("MEASUREMENT " + json.dumps({k: summary[k] for k in ("success", "seconds", "windows_process_peak_mib", "ram_peak_mib")}), flush=True)
