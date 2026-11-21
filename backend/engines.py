from dataclasses import dataclass

from engine.adapter.catalog import missing_parts
from engine.adapter.command import Engine
from engine.adapter.qwen_command import QwenEngine
from .options import Options, QwenOptions


@dataclass(frozen=True)
class EngineProfile:
    id: str
    name: str
    version: str
    options: type
    adapter: object | None = None
    depth: bool = False
    actions: tuple[str, ...] = ()
    resolutions: tuple[int, ...] = ()
    runtime: str = ""
    max_split_targets: int = 32
    split_layers: tuple[int, int] | None = None

    @property
    def exports(self):
        return ["psd", "depth-psd", "png-zip"] if self.depth else ["psd", "png-zip"]

    def ready_actions(self):
        if self.adapter is None:
            return []
        return [method for method in self.actions if method != "depth" or getattr(self.adapter, "depth_split_ready", False)]

    def capabilities(self):
        missing = self.adapter.missing() if self.adapter is not None else ["adapter"]
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "ready": self.adapter is not None and not missing,
            "missing": missing, "missing_parts": missing_parts(missing),
            "setup": getattr(self.adapter, "setup", None), "defaults": self.options().model_dump(),
            "options_schema": self.options.model_json_schema(), "depth": self.depth,
            "actions": self.ready_actions(), "declared_actions": list(self.actions), "resolutions": list(self.resolutions),
            "exports": self.exports, "runtime": self.runtime,
            "max_split_targets": self.max_split_targets,
            "split_layers": list(self.split_layers) if self.split_layers else None,
        }


def profiles(workspace, seethrough=None, qwen=None):
    qwen_adapter = qwen if qwen is not None else QwenEngine(workspace)
    return {
        "seethrough": EngineProfile(
            "seethrough", "See-through V3", "layerdiff3d-0.0.2", Options,
            seethrough if seethrough is not None else Engine(workspace), depth=True, actions=("left_right", "depth"),
            resolutions=(1024, 1280), runtime="Group Offload",
        ),
        "qwen": EngineProfile(
            "qwen", "Qwen-Image-Layered", "qwen-image-layered-gguf-" + getattr(qwen_adapter, "quant", "Q4_K_M"),
            QwenOptions, qwen_adapter, actions=("recursive",), resolutions=(640,),
            runtime="GGUF " + getattr(qwen_adapter, "quant", "Q4_K_M") + " · NF4 编码器 · Group Offload",
            max_split_targets=1, split_layers=(2, 4),
        ),
    }
