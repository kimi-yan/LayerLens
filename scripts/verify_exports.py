"""Download a finished job's exports from the running service and check them.

Checks: PSD (and depth PSD) open with psd-tools and match the layer manifest; the PNG ZIP
unpacks completely with full-canvas layers; recomposing the ZIP layers in order reproduces the
service's composite.png. Usage: python scripts/verify_exports.py <job_id> [<job_id> ...]
"""
from io import BytesIO
import json
import sys
from urllib.request import urlopen
from zipfile import ZipFile

import numpy as np
from PIL import Image
from psd_tools import PSDImage

BASE = "http://127.0.0.1:8765"


def get(path):
    with urlopen(BASE + path, timeout=120) as response:
        return response.read()


def check(job_id):
    job = json.loads(get(f"/api/v1/jobs/{job_id}"))
    manifest = json.loads(get(f"/api/v1/jobs/{job_id}/results/base/layers"))
    tags = [layer["tag"] for layer in manifest["layers"]]
    report = {"job": job_id, "engine": job["engine_id"], "exports": sorted(job["exports"]), "layers": len(tags),
              "canvas": [manifest["width"], manifest["height"]]}
    psd = PSDImage.open(BytesIO(get(job["exports"]["psd"])))
    assert psd.size == (manifest["width"], manifest["height"]), "PSD canvas differs from the manifest"
    assert [layer.name for layer in psd] == tags, "PSD layer order differs from the manifest"
    for layer, entry in zip(psd, manifest["layers"]):
        assert list(layer.bbox) == entry["bbox_xyxy"], f"PSD bounds differ for {layer.name}"
    report["psd_bytes"] = len(get(job["exports"]["psd"]))
    if "depth-psd" in job["exports"]:
        depth = PSDImage.open(BytesIO(get(job["exports"]["depth-psd"])))
        assert [layer.name for layer in depth] == tags, "Depth PSD layers differ"
        report["depth_psd"] = True
    bundle = ZipFile(BytesIO(get(job["exports"]["png-zip"])))
    assert bundle.testzip() is None, "ZIP CRC check failed"
    records = json.loads(bundle.read("manifest.json"))["layers"]
    assert [record["tag"] for record in records] == tags, "ZIP layer order differs"
    canvas = Image.new("RGBA", psd.size)
    for record in records:
        with Image.open(BytesIO(bundle.read(record["file"]))) as layer:
            assert layer.size == psd.size and layer.mode == "RGBA", f"{record['file']} is not a full-canvas RGBA layer"
            canvas.alpha_composite(layer)
    composite = Image.open(BytesIO(get(manifest["composite"]))).convert("RGBA")
    difference = np.abs(np.asarray(canvas, dtype=np.int16) - np.asarray(composite, dtype=np.int16))
    report["zip_files"] = len(bundle.namelist())
    report["recompose_max_diff"] = int(difference.max())
    assert difference.max() <= 1, "Recomposed ZIP layers do not match the composite"
    return report


if __name__ == "__main__":
    failures = 0
    for job_id in sys.argv[1:]:
        try:
            print("PASS", json.dumps(check(job_id)))
        except (AssertionError, OSError, KeyError, ValueError) as error:
            failures += 1
            print("FAIL", job_id, repr(error))
    sys.exit(1 if failures else 0)
