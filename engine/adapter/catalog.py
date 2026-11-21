"""What each engine needs on disk, where it comes from, and how to install it.

This is the single place for pinned upstream commits and weight revisions. The adapters,
the download scripts, scripts/doctor.py and the /capabilities endpoint all read from here,
so a version bump only touches this file. Standard library only: the download scripts run
it from the engine's own venv before anything else is installed.
"""

SEETHROUGH_REPO = "https://github.com/shitagaki-lab/see-through.git"
SEETHROUGH_COMMIT = "fc4440db7c076518e313f378e28b1504e9886a95"
SEETHROUGH_WEIGHTS = {
    # alias -> (Hugging Face repo under layerdifforg, revision)
    "layerdiff": ("seethroughv0.0.2_layerdiff3d", "4477e6ce529bc6a141732e1a55a4932176db3b89"),
    "marigold": ("seethroughv0.0.1_marigold", "4f4ffc46050b6feb764b628859968a26e76e6b6a"),
}
# Optional: only the front/back ("depth") secondary split uses it.
LAMA_URL = "https://huggingface.co/dreMaz/AnimeMangaInpainting/resolve/main/lama_large_512px.ckpt"
LAMA_SHA256 = "11d30fbb3000fb2eceae318b75d9ced9229d99ae990a7f8b3ac35c8d31f2c935"
LAMA_SIZE = 204544673

QWEN_PIPELINE = ("Qwen", "Qwen-Image-Layered", "8f0ca708dfff6ba1dd5f2d85d78f8c108a040bcf")
QWEN_GGUF = ("unsloth", "Qwen-Image-Layered-GGUF", "4bb0a560b7a8fb190b10498992ea39af5a80ff04")
QWEN_QUANTS = {
    "Q3_K_M": "qwen-image-layered-Q3_K_M.gguf", "Q4_K_M": "qwen-image-layered-Q4_K_M.gguf",
    "Q5_K_M": "qwen-image-layered-Q5_K_M.gguf", "Q6_K": "qwen-image-layered-Q6_K.gguf", "Q8_0": "qwen-image-layered-Q8_0.gguf",
}

# Missing paths are reported relative to the workspace; the prefix tells the user which setup
# step is unfinished.
PARTS = (
    (".local/", "environment", "推理环境"),
    ("engine/see-through/", "source", "上游代码"),
    ("data/cache/", "weights", "模型权重"),
)

SETUP = {
    "seethrough": {
        "command": r".\scripts\setup-engine.ps1 -Engine seethrough",
        "guide": "docs/engines.md#see-through二次元角色拆层",
        "download_gib": 13,
        "disk_gib": 21,
    },
    "qwen": {
        "command": r".\scripts\setup-engine.ps1 -Engine qwen",
        "guide": "docs/engines.md#qwen-image-layered通用拆层",
        "download_gib": 28,
        "disk_gib": 41,
    },
}


def missing_parts(paths):
    """Group missing relative paths into the setup steps that would create them."""
    parts = []
    for path in paths:
        key, label = next(((key, label) for prefix, key, label in PARTS if path.startswith(prefix)), ("adapter", "适配器文件"))
        if key not in [part["key"] for part in parts]:
            parts.append({"key": key, "label": label})
    return parts
