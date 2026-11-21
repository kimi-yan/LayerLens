"""Collect the licensed LayerLens sample set into data/samples and write a sha256 manifest."""
import hashlib
import json
from pathlib import Path
from shutil import copyfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "data" / "samples"
QWEN_COMMIT = "main"
QWEN_URL = "https://raw.githubusercontent.com/QwenLM/Qwen-Image-Layered/{commit}/assets/test_images/{name}"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    entries = []
    character = TARGET / "character"
    character.mkdir(parents=True, exist_ok=True)
    for name in ("test_image.png", "test_image1.png", "test_image2.png", "test_image3.png", "test_image4.png"):
        target = character / f"seethrough-{name}"
        copyfile(ROOT / "engine" / "see-through" / "common" / "assets" / name, target)
        entries.append({"file": target.relative_to(TARGET).as_posix(), "source": f"engine/see-through/common/assets/{name}",
                        "license": "Apache-2.0 (See-through upstream)", "kind": "character"})
    graphic = TARGET / "graphic"
    graphic.mkdir(exist_ok=True)
    for index in range(1, 14):
        name = f"{index}.png"
        target = graphic / f"qwen-{index:02d}.png"
        if not target.is_file():
            with urlopen(QWEN_URL.format(commit=QWEN_COMMIT, name=name), timeout=60) as source:
                target.write_bytes(source.read())
        entries.append({"file": target.relative_to(TARGET).as_posix(),
                        "source": f"github.com/QwenLM/Qwen-Image-Layered assets/test_images/{name}",
                        "license": "Apache-2.0 (Qwen-Image-Layered)", "kind": "graphic"})
    product = TARGET / "product"
    for path in sorted(product.glob("*.png")) if product.is_dir() else []:
        entries.append({"file": path.relative_to(TARGET).as_posix(), "source": "generated for LayerLens validation",
                        "license": "project-generated test asset", "kind": "product"})
    for entry in entries:
        entry["sha256"] = digest(TARGET / entry["file"])
    (TARGET / "manifest.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(entries)} samples in {TARGET}")


if __name__ == "__main__":
    main()
