from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    resolution: Literal[1024, 1280] = 1280
    resolution_depth: Literal[512, 640, 768] = 768
    inference_steps: int = Field(default=30, ge=1, le=100)
    inference_steps_depth: Literal[-1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10] = -1
    seed: int = Field(default=42, ge=0, le=4294967295)
    # Left/right pairs (eyes, brows, ears, hands) as separate layers: measured identical fidelity,
    # about 9% more time (docs/architecture.md). Off gives the coarser ~17-layer part set.
    tblr_split: bool = True
    low_vram: bool = True

    @field_validator("low_vram")
    @classmethod
    def require_safe_profile(cls, enabled):
        if not enabled:
            raise ValueError("This GPU profile requires group offload")
        return enabled


class QwenOptions(BaseModel):
    """Parameters passed to QwenImageLayeredPipeline. Ranges are limited to measured RTX 4070 runs,
    except layers: 6–8 are allowed at the user's own risk and the form warns before submission."""

    model_config = ConfigDict(extra="forbid", strict=True)

    resolution: Literal[640] = 640
    layers: int = Field(default=5, ge=3, le=8)
    inference_steps: int = Field(default=30, ge=20, le=50)
    true_cfg_scale: float = Field(default=4.0, ge=1.0, le=8.0)
    seed: int = Field(default=777, ge=0, le=4294967295)
    prompt: str = Field(default="", max_length=1500)
    negative_prompt: str = Field(default=" ", max_length=500)
    caption_language: Literal["en", "zh"] = "en"
    cfg_normalize: bool = True
