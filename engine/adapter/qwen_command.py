"""Launch Qwen-Image-Layered in its isolated environment (.local/qwen-venv)."""
from dataclasses import dataclass
import json
import os
from pathlib import Path

from .catalog import QWEN_GGUF, QWEN_PIPELINE, QWEN_QUANTS, SETUP

PIPELINE_REVISION = QWEN_PIPELINE[2]
GGUF_REVISION = QWEN_GGUF[2]


@dataclass(frozen=True)
class QwenEngine:
    workspace: Path
    quant: str = "Q4_K_M"
    offload: str = "group"
    # Pins blocks on demand: bit-identical output, about 10% slower denoising, about 6 GB less RAM
    # and no 17 GB of pinned host memory (output/qwen-bench b5 vs b6).
    low_cpu_mem: bool = True

    depth_split_ready = False
    setup = SETUP["qwen"]

    @property
    def python(self):
        return self.workspace / ".local" / "qwen-venv" / "Scripts" / "python.exe"

    @property
    def models(self):
        return self.workspace / "data" / "cache" / "models"

    @property
    def pipeline(self):
        return self.models / "qwen-image-layered"

    @property
    def text_encoder(self):
        return self.models / "qwen-image-layered-te-nf4"

    @property
    def gguf(self):
        return self.models / "qwen-image-layered-gguf" / QWEN_QUANTS[self.quant]

    @property
    def weights(self):
        return f"Qwen/Qwen-Image-Layered@{PIPELINE_REVISION[:7]} + unsloth GGUF {self.quant}@{GGUF_REVISION[:7]}"

    def missing(self):
        paths = [
            self.python,
            Path(__file__).with_name("qwen_run.py"),
            self.pipeline / "model_index.json",
            self.pipeline / "transformer" / "config.json",
            self.pipeline / "vae" / "diffusion_pytorch_model.safetensors",
            self.pipeline / "scheduler" / "scheduler_config.json",
            self.pipeline / "tokenizer" / "vocab.json",
            self.pipeline / "processor" / "preprocessor_config.json",
            self.text_encoder / "config.json",
            self.text_encoder / "model.safetensors.index.json",
            self.gguf,
        ]
        return [path.relative_to(self.workspace).as_posix() for path in paths if not path.is_file()]

    def request(self, work, **fields):
        return {
            "work": str(work), "models": str(self.pipeline), "text_encoder": str(self.text_encoder),
            "gguf": str(self.gguf), "weights": self.weights, "offload": self.offload,
            "low_cpu_mem": self.low_cpu_mem, **fields,
        }

    def launch(self, source, work, options):
        return self._launch(work, self.request(work, mode="layers", source=str(source), options=options))

    def launch_derivation(self, parent, work, operation, options):
        request = self.request(work, mode="recursive", parent=str(parent), targets=operation["target_tags"],
                               layers=operation.get("layers") or 3, options=options)
        return self._launch(work, request)

    def _launch(self, work, request):
        args = [self.python, "-u", Path(__file__).with_name("qwen_run.py"), work / "progress.ndjson",
                "--request", json.dumps(request)]
        env = {
            **os.environ,
            "HF_HOME": str(self.workspace / "data" / "cache" / "hf"),
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            "TORCH_HOME": str(self.workspace / "data" / "cache" / "torch"),
            "PYTHONUTF8": "1",
            "OMP_NUM_THREADS": "8",
        }
        return [str(arg) for arg in args], Path(__file__).parent, env
