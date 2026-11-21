import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from PIL import Image

from engine.adapter.command import Engine
from .app import WORKSPACE, create_app
from .options import Options
from .store import Store


FAKE_ENGINE = """
import json, os, sys
from pathlib import Path
work = Path(sys.argv[1])
seed = int(sys.argv[2])
assert os.read(sys.stdin.fileno(), 1) == b'\\x01'
with (work / 'progress.ndjson').open('w') as stream:
    stream.write(json.dumps(dict(stage='layerdiff', step=1, total_steps=3)) + '\\n')
if seed == 7:
    os.read(sys.stdin.fileno(), 1)
    sys.exit(2)
if seed == 8:
    sys.exit('CUDA out of memory')
for filename in ('input.psd', 'input_depth.psd'):
    (work / filename).write_bytes(b'8BPS test')
with (work / 'progress.ndjson').open('a') as stream:
    stream.write(json.dumps(dict(stage='complete')) + '\\n')
"""


FAKE_QWEN = """
import json, os, sys
from pathlib import Path
from PIL import Image
from psd_tools import PSDImage
work, mode, layers = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
assert os.read(sys.stdin.fileno(), 1) == b'\\x01'
with (work / 'progress.ndjson').open('w') as stream:
    stream.write(json.dumps(dict(stage='layering', step=1, total_steps=2, pass_index=1)) + '\\n')
size = (6, 4)
tags = ['L1', 'L2'] if mode == 'layers' else ['L1', 'L2.1', 'L2.2']
psd = PSDImage.new('RGBA', size)
parts = {}
for index, tag in enumerate(tags):
    psd.create_pixel_layer(Image.new('RGBA', (2, 2), (40 * index, 90, 30, 255)), name=tag, left=index, top=1)
    parts[tag] = {'xyxy': [index, 1, index + 2, 3], 'z': index}
psd.save(work / 'input.psd')
(work / 'input').mkdir()
Image.new('RGBA', size, (9, 9, 9, 255)).save(work / 'input' / 'src_img.png')
(work / 'input.psd.json').write_text(json.dumps({'frame_size': size, 'parts': parts, 'prompt': 'two blocks', 'reconstruction_mae': 1.5}))
if mode == 'recursive':
    if layers == 4:
        sys.exit('LAYERLENS_NO_SPLIT')
    if layers == 3:
        sys.exit('LAYERLENS_SPLIT_REJECTED')
    (work / 'derivation.json').write_text(json.dumps({'split': ['L2'], 'unchanged': [], 'layers': layers}))
with (work / 'progress.ndjson').open('a') as stream:
    stream.write(json.dumps(dict(stage='complete')) + '\\n')
"""


class FakeEngine:
    depth_split_ready = True

    def missing(self):
        return []

    def launch(self, source, work, options):
        assert source.name == "input.png"
        return [sys.executable, "-c", FAKE_ENGINE, str(work), str(options["seed"])], work, os.environ

    def launch_derivation(self, parent, work, operation, options):
        assert (parent / "input.psd").is_file()
        return [sys.executable, "-c", FAKE_ENGINE, str(work), str(options["seed"])], work, os.environ


class FakeQwen:
    quant = "Q4_K_M"
    depth_split_ready = False

    def __init__(self, missing=()):
        self.absent = list(missing)
        self.derivations = []

    def missing(self):
        return self.absent

    def launch(self, source, work, options):
        assert set(options) >= {"layers", "true_cfg_scale", "prompt", "caption_language"}
        return [sys.executable, "-c", FAKE_QWEN, str(work), "layers", str(options["layers"])], work, os.environ

    def launch_derivation(self, parent, work, operation, options):
        self.derivations.append((operation, options))
        return [sys.executable, "-c", FAKE_QWEN, str(work), "recursive", str(operation.get("layers", 3))], work, os.environ


class ApiTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        image = io.BytesIO()
        Image.new("RGBA", (4, 4), (60, 20, 10, 255)).save(image, "PNG")
        self.image = image.getvalue()

    def client(self, qwen=None):
        self.qwen = qwen if qwen is not None else FakeQwen(missing=["data/cache/models/qwen-image-layered-gguf"])
        return TestClient(create_app(self.root, FakeEngine(), self.qwen), base_url="http://127.0.0.1")

    def submit_qwen(self, client, **options):
        response = client.post("/api/v1/jobs", files={"file": ("poster.png", self.image)},
                               data={"engine_id": "qwen", "options": json.dumps(options)})
        self.assertEqual(response.status_code, 202, response.text)
        return response.headers["Location"]

    def submit(self, client, seed):
        response = client.post("/api/v1/jobs", files={"file": ("same.png", self.image)},
                               data={"options": json.dumps({"seed": seed})})
        self.assertEqual(response.status_code, 202, response.text)
        return response.headers["Location"]

    def wait(self, client, location, status):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = client.get(location).json()
            if job["status"] == status:
                return job
            time.sleep(0.05)
        self.fail(f"Expected {status}, last response was {job}")

    def test_serial_queue_active_and_queued_cancellation_and_failure(self):
        with self.client() as client:
            holding = self.submit(client, 7)
            self.wait(client, holding, "running")
            cancelled = self.submit(client, 9)
            failing = self.submit(client, 8)
            good = self.submit(client, 10)
            self.assertNotEqual(holding, cancelled)
            self.assertEqual(client.get(failing).json()["queue_position"], 2)
            self.assertEqual(client.post(cancelled + "/cancel").json()["status"], "cancelled")
            self.assertEqual(client.post(holding + "/cancel").status_code, 200)
            self.wait(client, holding, "cancelled")
            self.assertEqual(self.wait(client, failing, "failed")["error"]["code"], "CUDA_OOM")
            completed = self.wait(client, good, "succeeded")
            for kind, url in completed["exports"].items():
                if kind == "png-zip":
                    continue
                self.assertEqual(client.get(url).content, b"8BPS test")
            self.assertEqual(client.post(good + "/cancel").status_code, 409)
            self.assertEqual(client.get(failing + "/results/base/exports/psd").status_code, 409)

    def test_restart_keeps_queue_and_reaps_current_engine(self):
        with self.client() as client:
            first = self.submit(client, 7)
            self.wait(client, first, "running")
            second = self.submit(client, 10)
        with self.client() as client:
            self.assertIn(client.get(first).json()["status"], ("queued", "running"))
            self.assertEqual(client.get(second).json()["status"], "queued")
            client.post(first + "/cancel").raise_for_status()
            self.wait(client, second, "succeeded")

    def test_invalid_input_and_unverified_memory_profile(self):
        with self.client() as client:
            response = client.post("/api/v1/jobs", files={"file": ("fake.png", b"wrong")})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["code"], "INVALID_IMAGE")
            self.assertEqual(response.json()["detail"]["filename"], "fake.png")
            response = client.post("/api/v1/jobs", files={"file": ("image.png", self.image)},
                                   data={"options": '{"low_vram":false}'})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["fields"][0]["loc"], ["low_vram"])
            response = client.post("/api/v1/jobs", files={"file": ("image.png", self.image)},
                                   headers={"Origin": "https://example.org"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual([path for path in self.root.iterdir() if path.is_dir()], [])

    def test_animated_input_is_rejected_with_a_specific_reason(self):
        animation = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(animation, "PNG", save_all=True,
                                              append_images=[Image.new("RGB", (4, 4), "blue")], duration=100)
        with self.client() as client:
            response = client.post("/api/v1/jobs", files={"file": ("animated.png", animation.getvalue())})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["code"], "ANIMATED_IMAGE")
            self.assertEqual(client.get("/api/v1/jobs").json(), [])

    def test_model_registry_reports_availability_and_filters_persisted_engine_id(self):
        with self.client() as client:
            engines = client.get("/api/v1/capabilities").json()["engines"]
            self.assertEqual([(engine["id"], engine["ready"]) for engine in engines], [("seethrough", True), ("qwen", False)])
            self.assertEqual(engines[1]["missing"], ["data/cache/models/qwen-image-layered-gguf"])
            self.assertEqual(engines[1]["missing_parts"], [{"key": "weights", "label": "模型权重"}])
            blocked = client.post("/api/v1/jobs", data={"engine_id": "qwen"}, files={"file": ("product.png", self.image)})
            self.assertEqual(blocked.status_code, 503)
            self.assertEqual(client.get("/api/v1/jobs").json(), [])
            created = client.get(self.submit(client, 10)).json()
            self.assertEqual((created["engine_id"], created["engine_version"]), ("seethrough", "layerdiff3d-0.0.2"))
            self.assertEqual(client.get("/api/v1/task-center?engine_id=qwen").json()["total"], 0)
            self.assertEqual(client.get("/api/v1/task-center?engine_id=seethrough").json()["total"], 1)

    def test_uninstalled_engines_report_setup_steps(self):
        from engine.adapter.catalog import missing_parts
        from engine.adapter.command import Engine
        from engine.adapter.qwen_command import QwenEngine

        with TemporaryDirectory() as empty:
            for adapter, needed in ((Engine(Path(empty)), ["environment", "source", "weights"]), (QwenEngine(Path(empty)), ["environment", "weights"])):
                self.assertEqual([part["key"] for part in missing_parts(adapter.missing())], needed)
                self.assertIn("setup-engine.ps1", adapter.setup["command"])

    def test_missing_result_details_allow_the_saved_input_to_be_reused(self):
        with self.client() as client:
            location = self.submit(client, 10)
            job = self.wait(client, location, "succeeded")
            folder = self.root / job["job_id"]
            (folder / "result").rename(folder / "moved-result")
            response = client.get(location + "/results/base/layers")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["detail"]["code"], "RESULT_MISSING")
            self.assertEqual(client.get(location + "/source").content, self.image)

    def test_single_worker_lock_and_1280_engine_mapping(self):
        store = Store(self.root)
        with store.worker_lock():
            with self.assertRaisesRegex(RuntimeError, "already owns"):
                with store.worker_lock():
                    self.fail("Second worker acquired the lock")
        args, cwd, env = Engine(WORKSPACE).launch(self.root / "input.png", self.root, Options().model_dump())
        self.assertEqual(args[args.index("--resolution") + 1], "1280")
        self.assertEqual(args[args.index("--resolution_depth") + 1], "768")
        self.assertIn("--group_offload", args)
        self.assertEqual(cwd.name, "see-through")
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")

    def test_batch_admission_is_atomic_and_keeps_duplicate_names_isolated(self):
        with self.client() as client:
            files = [("files", ("same.png", self.image)), ("files", ("same.png", b"invalid"))]
            response = client.post("/api/v1/batches", files=files)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["file_index"], 1)
            self.assertEqual(client.get("/api/v1/jobs").json(), [])
            self.assertEqual([path for path in self.root.iterdir() if path.is_dir()], [])

            files[1] = ("files", ("same.png", self.image))
            created = client.post("/api/v1/batches", files=files, data={"options": '{"seed":7}'})
            self.assertEqual(created.status_code, 202, created.text)
            batch = created.json()
            first, second = batch["jobs"]
            self.assertNotEqual(first["job_id"], second["job_id"])
            self.assertEqual(first["filename"], second["filename"])
            endpoint = f"/api/v1/batches/{batch['batch_id']}"
            self.assertEqual(client.get(endpoint + "/exports/zip").status_code, 409)
            for job in batch["jobs"]:
                self.assertTrue((self.root / job["job_id"] / "input.png").is_file())
                client.post(f"/api/v1/jobs/{job['job_id']}/cancel").raise_for_status()

    def test_original_available_while_pending_and_retry_is_a_new_task(self):
        with self.client() as client:
            previous = self.submit(client, 7)
            self.wait(client, previous, "running")
            image = client.get(previous + "/source")
            self.assertEqual(image.headers["content-type"], "image/png")
            self.assertEqual(image.content, self.image)
            self.assertEqual(client.post(previous + "/retry").status_code, 409)
            client.post(previous + "/cancel").raise_for_status()
            cancelled = self.wait(client, previous, "cancelled")

            retry = client.post(previous + "/retry")
            self.assertEqual(retry.status_code, 202, retry.text)
            fresh = retry.json()
            self.assertNotEqual(fresh["job_id"], cancelled["job_id"])
            self.assertEqual(fresh["effective_options"], cancelled["effective_options"])
            self.assertEqual(client.get(f"/api/v1/jobs/{fresh['job_id']}/source").content, self.image)

    def test_task_center_cancel_trash_and_restore_preserves_input(self):
        with self.client() as client:
            location = self.submit(client, 7)
            running = self.wait(client, location, "running")
            self.assertEqual(client.delete(location).status_code, 409)
            active = client.get("/api/v1/task-center?filter=active&q=same").json()
            self.assertEqual(active["counts"]["active"], 1)
            self.assertEqual(active["jobs"][0]["job_id"], running["job_id"])
            self.assertEqual(client.get(location + "/thumbnail").headers["content-type"], "image/png")
            client.post(location + "/cancel").raise_for_status()
            self.wait(client, location, "cancelled")
            self.assertEqual(client.delete(location).status_code, 204)
            self.assertEqual(client.get(location).status_code, 404)
            self.assertEqual(client.get(location + "/source").status_code, 404)
            self.assertEqual(client.get("/api/v1/jobs").json(), [])
            trash = client.get("/api/v1/task-center?filter=trash").json()
            self.assertTrue(trash["jobs"][0]["deleted_at"])
            self.assertEqual(trash["counts"]["trash"], 1)
            self.assertEqual(client.post(location + "/restore").json()["status"], "cancelled")
            self.assertEqual(client.get(location + "/source").content, self.image)
            self.assertEqual(client.get("/api/v1/task-center?filter=trash").json()["total"], 0)

    def test_bulk_trash_and_permanent_deletion_remove_files_rows_and_cached_archives(self):
        with self.client() as client:
            files = [("files", (f"image-{index}.png", self.image)) for index in range(2)]
            batch = client.post("/api/v1/batches", files=files).json()
            ids = [job["job_id"] for job in batch["jobs"]]
            for job_id in ids:
                self.wait(client, f"/api/v1/jobs/{job_id}", "succeeded")
            archive = self.root / f"batch-{batch['batch_id']}-cached.zip"
            archive.write_bytes(b"cached")
            other_archive = self.root / "batch-unrelated.zip"
            other_archive.write_bytes(b"unrelated")

            selection = {"ids": ids}
            self.assertEqual(client.post("/api/v1/task-center/purge", json=selection).status_code, 409)
            self.assertTrue(archive.is_file())
            client.post("/api/v1/task-center/trash", json=selection).raise_for_status()
            self.assertEqual(client.get("/api/v1/task-center?filter=trash").json()["total"], 2)
            client.post("/api/v1/task-center/restore", json={"ids": [ids[0]]}).raise_for_status()
            self.assertEqual(client.post("/api/v1/task-center/purge", json=selection).status_code, 409)
            self.assertTrue(all((self.root / item).is_dir() for item in ids))
            client.post("/api/v1/task-center/trash", json={"ids": [ids[0]]}).raise_for_status()
            deleted = client.post("/api/v1/task-center/purge", json=selection).json()
            self.assertCountEqual(deleted["purged"], ids)
            self.assertEqual(deleted["failed"], [])
            self.assertTrue(all(Store(self.root).get(item, include_deleted=True) is None for item in ids))
            self.assertFalse(any((self.root / item).exists() for item in ids))
            self.assertFalse(archive.exists())
            self.assertTrue(other_archive.is_file())

    def test_purge_blocks_active_dependencies_and_allows_retry_after_a_file_error(self):
        with self.client() as client:
            parent = self.submit(client, 10)
            parent_id = self.wait(client, parent, "succeeded")["job_id"]
            holding = self.submit(client, 7)
            self.wait(client, holding, "running")
            child = self.submit(client, 9)
            child_id = client.get(child).json()["job_id"]
            with Store(self.root).connect() as db:
                db.execute("UPDATE jobs SET derivation=? WHERE id=?", (json.dumps({"parent_id": parent_id}), child_id))
            self.assertEqual(client.delete(parent).status_code, 204)
            selection = {"ids": [parent_id]}
            blocked = client.post("/api/v1/task-center/purge", json=selection)
            self.assertEqual(blocked.json()["detail"]["code"], "ACTIVE_DERIVATION")
            client.post(child + "/cancel").raise_for_status()
            with patch("backend.app.rmtree", side_effect=PermissionError("test file in use")):
                self.assertEqual(client.post("/api/v1/task-center/purge", json=selection).json()["failed"], [parent_id])
            self.assertEqual(client.post("/api/v1/task-center/restore", json=selection).status_code, 409)
            self.assertEqual(client.post("/api/v1/task-center/purge", json=selection).json()["purged"], [parent_id])
            client.post(holding + "/cancel").raise_for_status()

    def test_capabilities_describe_each_engine_without_a_global_first_engine(self):
        with self.client(FakeQwen()) as client:
            body = client.get("/api/v1/capabilities").json()
            engines = {engine["id"]: engine for engine in body["engines"]}
            self.assertEqual(body["default_engine"], "seethrough")
            self.assertTrue(engines["qwen"]["ready"])
            self.assertEqual(engines["seethrough"]["exports"], ["psd", "depth-psd", "png-zip"])
            self.assertEqual(engines["qwen"]["exports"], ["psd", "png-zip"])
            self.assertEqual(engines["seethrough"]["actions"], ["left_right", "depth"])
            self.assertEqual(engines["qwen"]["actions"], ["recursive"])
            self.assertEqual(engines["qwen"]["resolutions"], [640])
            self.assertEqual(engines["qwen"]["max_split_targets"], 1)
            self.assertEqual(engines["qwen"]["split_layers"], [2, 4])
            self.assertFalse(engines["qwen"]["depth"])
            self.assertIn("true_cfg_scale", engines["qwen"]["defaults"])
            # Deprecated top-level fields describe the default engine, not whichever engine is listed first.
            self.assertEqual(body["resolutions"], [1024, 1280])
            self.assertTrue(body["depth_split_ready"])
            self.assertEqual(body["export_formats"], ["depth-psd", "png-zip", "psd"])

    def test_qwen_options_are_validated_against_the_measured_profile(self):
        with self.client(FakeQwen()) as client:
            for options, field in (({"layers": 9}, "layers"), ({"resolution": 1024}, "resolution"),
                                   ({"inference_steps": 10}, "inference_steps"), ({"tblr_split": True}, "tblr_split"),
                                   ({"true_cfg_scale": "4"}, "true_cfg_scale")):
                response = client.post("/api/v1/jobs", files={"file": ("poster.png", self.image)},
                                       data={"engine_id": "qwen", "options": json.dumps(options)})
                self.assertEqual(response.status_code, 422, options)
                self.assertEqual(response.json()["detail"]["fields"][0]["loc"], [field])
            job = client.get(self.submit_qwen(client, true_cfg_scale=4, layers=3)).json()
            self.assertEqual(job["effective_options"]["true_cfg_scale"], 4)
            self.assertEqual(job["engine_version"], "qwen-image-layered-gguf-Q4_K_M")

    def test_qwen_job_publishes_color_layers_without_depth_exports(self):
        with self.client(FakeQwen()) as client:
            job = self.wait(client, self.submit_qwen(client), "succeeded")
            self.assertEqual(set(job["exports"]), {"psd", "png-zip"})
            location = f"/api/v1/jobs/{job['job_id']}"
            self.assertEqual(client.get(location + "/results/base/exports/depth-psd").status_code, 404)
            manifest = client.get(location + "/results/base/layers").json()
            self.assertFalse(manifest["has_depth"])
            self.assertNotIn("depth", manifest)
            self.assertEqual([layer["tag"] for layer in manifest["layers"]], ["L1", "L2"])
            self.assertEqual(manifest["details"], {"prompt": "two blocks", "reconstruction_mae": 1.5})
            self.assertEqual(client.get(manifest["layers"][1]["image"]).headers["content-type"], "image/png")
            with ZipFile(io.BytesIO(client.get(job["exports"]["png-zip"]).content)) as bundle:
                self.assertEqual(sorted(bundle.namelist()), ["01-L1.png", "02-L2.png", "manifest.json"])
                with Image.open(io.BytesIO(bundle.read("02-L2.png"))) as layer:
                    self.assertEqual((layer.size, layer.getpixel((1, 1))[3], layer.getpixel((0, 0))[3]), ((6, 4), 255, 0))
            self.assertTrue(client.get(job["exports"]["psd"]).content.startswith(b"8BPS"))

    def test_qwen_recursive_split_is_limited_and_creates_a_child_task(self):
        with self.client(FakeQwen()) as client:
            parent = self.wait(client, self.submit_qwen(client, layers=3), "succeeded")
            endpoint = f"/api/v1/jobs/{parent['job_id']}/results/base/postprocess"
            self.assertEqual(client.post(endpoint, json={"method": "left_right", "target_tags": ["L2"]}).json()["detail"]["code"],
                             "ACTION_UNAVAILABLE")
            self.assertEqual(client.post(endpoint, json={"method": "recursive", "target_tags": ["L1", "L2"]}).json()["detail"]["code"],
                             "TOO_MANY_TARGETS")
            self.assertEqual(client.post(endpoint, json={"method": "recursive", "target_tags": ["L2"], "layers": 5}).status_code, 422)
            self.assertEqual(client.post(endpoint, json={"method": "recursive", "target_tags": ["L9"]}).status_code, 422)
            created = client.post(endpoint, json={"method": "recursive", "target_tags": ["L2"], "layers": 2})
            self.assertEqual(created.status_code, 202, created.text)
            child = self.wait(client, f"/api/v1/jobs/{created.json()['job_id']}", "succeeded")
            self.assertEqual(child["engine_id"], "qwen")
            self.assertEqual(child["derivation"], {"parent_id": parent["job_id"], "method": "recursive", "target_tags": ["L2"], "layers": 2})
            operation, options = self.qwen.derivations[0]
            self.assertEqual((operation["layers"], options["layers"]), (2, 3))
            manifest = client.get(f"/api/v1/jobs/{child['job_id']}/results/base/layers").json()
            self.assertEqual([layer["tag"] for layer in manifest["layers"]], ["L1", "L2.1", "L2.2"])
            self.assertEqual(manifest["derivation"]["split"], ["L2"])
            retried = client.post(f"/api/v1/jobs/{child['job_id']}/retry")
            self.assertEqual(retried.json()["derivation"]["layers"], 2)
            # A child that cannot be split fails without touching the parent result.
            failed = client.post(endpoint, json={"method": "recursive", "target_tags": ["L1"], "layers": 4}).json()
            failed = self.wait(client, f"/api/v1/jobs/{failed['job_id']}", "failed")
            self.assertEqual(failed["error"]["code"], "NO_SPLIT")
            # Children that do not recompose the parent are rejected the same way, with their own code.
            rejected = client.post(endpoint, json={"method": "recursive", "target_tags": ["L1"], "layers": 3}).json()
            rejected = self.wait(client, f"/api/v1/jobs/{rejected['job_id']}", "failed")
            self.assertEqual(rejected["error"]["code"], "SPLIT_REJECTED")
            self.assertEqual(len(client.get(f"/api/v1/jobs/{parent['job_id']}/results/base/layers").json()["layers"]), 2)

    def test_seethrough_split_rejects_engine_specific_child_counts(self):
        with self.client(FakeQwen()) as client:
            job = self.wait(client, self.submit(client, 10), "succeeded")
            folder = self.root / job["job_id"] / "result"
            (folder / "preview").mkdir()
            (folder / "preview" / "manifest.json").write_text(json.dumps({"layers": [{"tag": "face"}]}), encoding="utf-8")
            endpoint = f"/api/v1/jobs/{job['job_id']}/results/base/postprocess"
            response = client.post(endpoint, json={"method": "left_right", "target_tags": ["face"], "layers": 3})
            self.assertEqual(response.json()["detail"]["code"], "INVALID_OPTIONS")
            self.assertEqual(client.post(endpoint, json={"method": "recursive", "target_tags": ["face"]}).status_code, 503)

    def test_qwen_runner_contract_accepts_wide_canvases_and_rejects_misaligned_layers(self):
        sys.path.insert(0, str(WORKSPACE / "engine" / "adapter"))
        self.addCleanup(sys.path.remove, str(WORKSPACE / "engine" / "adapter"))
        import qwen_run

        source = Image.new("RGBA", (12, 6), (200, 10, 10, 255))
        frames = [Image.new("RGBA", (12, 6), (200, 10, 10, 255)), Image.new("RGBA", (12, 6)),
                  Image.new("RGBA", (12, 6))]
        frames[2].paste((0, 0, 255, 255), (3, 2, 7, 5))
        parts, dropped = qwen_run.collect(frames, "L")
        self.assertEqual(([part["tag"] for part in parts], dropped), (["L1", "L3"], ["L2"]))
        self.assertEqual(parts[1]["xyxy"], [3, 2, 7, 5])
        qwen_run.write_contract(self.root, (12, 6), source, parts, {"prompt": "red with a blue block"})
        qwen_run.verify(self.root)
        self.assertGreater(qwen_run.reconstruction_error(parts, (12, 6), source), 0)
        manifest = json.loads((self.root / "input.psd.json").read_text(encoding="utf-8"))
        manifest["parts"]["L3"]["xyxy"] = [0, 0, 4, 3]
        (self.root / "input.psd.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "offsets"):
            qwen_run.verify(self.root)

    def test_startup_settles_jobs_interrupted_by_a_crash(self):
        store = Store(self.root)
        store.setup()
        for job_id in ("a" * 32, "b" * 32, "c" * 32):
            (self.root / job_id).mkdir()
            store.enqueue(job_id, "x.png", "input.png", {}, engine_id="qwen")
            with store.connect() as db:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        (self.root / ("b" * 32) / "result").mkdir()
        store.cancel("c" * 32)
        store.setup()
        self.assertEqual([(store.get(job_id)["status"], store.get(job_id)["error"]) for job_id in ("a" * 32, "b" * 32, "c" * 32)],
                         [("failed", "ENGINE_INTERRUPTED"), ("succeeded", None), ("cancelled", None)])

