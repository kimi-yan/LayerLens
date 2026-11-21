"""Launch See-through in its isolated environment."""
from dataclasses import dataclass
import os
from pathlib import Path

from .catalog import SETUP


@dataclass(frozen=True)
class Engine:
    workspace: Path

    @property
    def python(self):
        return self.workspace / ".local" / "seethrough-venv" / "Scripts" / "python.exe"

    @property
    def upstream(self):
        return self.workspace / "engine" / "see-through"

    @property
    def cache(self):
        return self.workspace / "data" / "cache"

    @property
    def depth_split_ready(self):
        return (self.cache / "torch" / "hub" / "checkpoints" / "lama_large_512px.ckpt").is_file()

    setup = SETUP["seethrough"]

    def missing(self):
        paths = [
            self.python,
            self.upstream / "inference" / "scripts" / "inference_psd.py",
            self.cache / "models" / "layerdiff" / "model_index.json",
            self.cache / "models" / "marigold" / "model_index.json",
        ]
        return [path.relative_to(self.workspace).as_posix() for path in paths if not path.is_file()]

    def launch(self, source, work, options):
        models = self.cache / "models"
        args = [
            self.python, "-u", Path(__file__).with_name("run.py"), work / "progress.ndjson",
            "--srcp", source, "--save_dir", work, "--save_to_psd", "--group_offload",
            "--repo_id_layerdiff", models / "layerdiff",
            "--repo_id_depth", models / "marigold",
        ]
        for field in ("resolution", "resolution_depth", "inference_steps", "inference_steps_depth", "seed"):
            args.extend((f"--{field}", options[field]))
        if options["tblr_split"]:
            args.append("--tblr_split")
        return self._launch(args)

    def launch_derivation(self, parent, work, operation, options=None):
        args = [
            self.python, "-u", Path(__file__).with_name("derive.py"),
            parent, work, operation["method"], *operation["target_tags"],
        ]
        return self._launch(args)

    def _launch(self, args):
        env = {
            **os.environ,
            "HF_HOME": str(self.cache / "hf"),
            "HF_ENDPOINT": "https://hf-mirror.com",
            "HF_HUB_OFFLINE": "1",
            "TORCH_HOME": str(self.cache / "torch"),
            "PYTHONUTF8": "1",
            "OPENBLAS_NUM_THREADS": "8",
            "MKL_NUM_THREADS": "8",
            "OMP_NUM_THREADS": "8",
        }
        return [str(arg) for arg in args], self.upstream, env

