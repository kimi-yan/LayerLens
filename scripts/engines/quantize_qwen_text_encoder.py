"""One-time NF4 conversion of the Qwen2.5-VL text encoder used by Qwen-Image-Layered.

Quantizing on load materializes BF16 weights on the GPU first and peaks near 15 GB, which
spills into shared memory on a 12 GB card. Loading this pre-quantized copy avoids that.
Run inside .local/qwen-venv from the workspace root; scripts/setup-engine.ps1 does this once.
"""
from pathlib import Path

import torch
from transformers import BitsAndBytesConfig, Qwen2_5_VLForConditionalGeneration

ROOT = Path(__file__).resolve().parents[2] / "data" / "cache" / "models"
SOURCE = ROOT / "qwen-image-layered" / "text_encoder"
TARGET = ROOT / "qwen-image-layered-te-nf4"

if __name__ == "__main__":
    config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(SOURCE, quantization_config=config,
                                                               dtype=torch.bfloat16, device_map={"": 0})
    model.save_pretrained(TARGET, max_shard_size="2GB")
    size = sum(path.stat().st_size for path in TARGET.glob("*.safetensors"))
    print(f"Saved NF4 text encoder to {TARGET} ({size / 2**30:.2f} GiB)")
