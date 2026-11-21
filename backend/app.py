from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
from shutil import copyfile, rmtree
import subprocess
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from .assets import ASSET_NAME, batch_zip, png_zip, preview_manifest, safe_stem, thumbnail
from .engines import profiles
from .store import ParentUnavailable, Store
from .worker import Worker


WORKSPACE = Path(__file__).resolve().parents[1]
MAX_UPLOAD = 32 * 1024**2
MAX_PIXELS = 24_000_000
MAX_BATCH = 20
DEFAULT_ENGINE = "seethrough"
FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "BMP": ".bmp"}
ERRORS = {
    "ENGINE_FAILED": "The inference engine could not produce a valid result.",
    "ENGINE_INTERRUPTED": "The service stopped during inference. Submit the image again.",
    "CUDA_OOM": "The GPU ran out of memory. Free VRAM or submit the image at 1024.",
    "NO_SPLIT": "The selected layers did not contain separable parts.",
    "SPLIT_REJECTED": "The split did not recompose the selected layer, so the original layer was kept.",
    "ENGINE_UNAVAILABLE": "The selected engine is not installed or its files are missing.",
}


class Progress(BaseModel):
    stage: str
    step: int | None = None
    total_steps: int | None = None
    pass_index: int | None = None


class JobError(BaseModel):
    code: str
    message: str


class SplitRequest(BaseModel):
    method: Literal["depth", "left_right", "recursive"]
    target_tags: list[str] = Field(min_length=1, max_length=32)
    # Number of child layers for engines that decompose a layer again (Qwen recursive splitting).
    layers: int | None = Field(default=None, ge=2, le=8)


class TaskSelection(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=200)

    def unique_ids(self):
        return list(dict.fromkeys(job_id.hex for job_id in self.ids))


class Job(BaseModel):
    job_id: str
    filename: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: Progress
    queue_position: int | None
    cancel_requested: bool
    created_at: str
    started_at: str | None
    finished_at: str | None
    engine_id: str
    engine_version: str
    requested_options: dict
    effective_options: dict
    result_id: str | None
    exports: dict[str, str]
    error: JobError | None
    batch_id: str | None
    derivation: dict | None
    deleted_at: str | None
    purging: bool


def save_input(upload, folder):
    incoming = folder / "upload"
    size = 0
    with incoming.open("xb") as target:
        while chunk := upload.file.read(1024**2):
            size += len(chunk)
            if size > MAX_UPLOAD:
                raise HTTPException(413, {"code": "IMAGE_TOO_LARGE", "message": "The image exceeds 32 MiB"})
            target.write(chunk)
    try:
        with Image.open(incoming, formats=list(FORMATS)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise HTTPException(413, {"code": "IMAGE_TOO_MANY_PIXELS", "message": "The image exceeds 24 million pixels"})
            if getattr(image, "n_frames", 1) != 1:
                raise HTTPException(422, {"code": "ANIMATED_IMAGE", "message": "Upload a single-frame image"})
            image.load()
            name = "input" + FORMATS[image.format]
    except Image.DecompressionBombError as error:
        raise HTTPException(413, {"code": "IMAGE_TOO_MANY_PIXELS", "message": "The image exceeds 24 million pixels"}) from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise HTTPException(422, {"code": "INVALID_IMAGE", "message": "Upload a readable PNG, JPEG, WebP, or BMP image"}) from error
    incoming.rename(folder / name)
    return name


def system_memory():
    """Physical memory in MiB on Windows; None elsewhere."""
    if os.name != "nt":
        return None
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                    ("page_available", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                    ("virtual_available", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return {"used_mib": (status.total - status.available) // 2**20, "total_mib": status.total // 2**20}


def result_missing():
    return HTTPException(404, {"code": "RESULT_MISSING", "message": "One or more result files are missing"})


def summarize(row, profile):
    job_id = row["id"]
    status = row["status"]
    prefix = f"/api/v1/jobs/{job_id}/results/base/exports"
    progress = json.loads(row["progress"]) if status == "running" and row["progress"] else None
    error = row["error"]
    return Job(
        job_id=job_id, filename=row["filename"], status=status,
        progress=progress or {"stage": "starting" if status == "running" else status},
        queue_position=row["queue_position"], cancel_requested=bool(row["cancel_requested"]),
        created_at=row["created_at"], started_at=row["started_at"], finished_at=row["finished_at"],
        engine_id=row["engine_id"], engine_version=row["engine_version"],
        requested_options=json.loads(row["options"]), effective_options=json.loads(row["options"]),
        result_id="base" if status == "succeeded" else None,
        exports={kind: f"{prefix}/{kind}" for kind in profile.exports} if status == "succeeded" else {},
        error={"code": error, "message": ERRORS[error]} if error else None,
        batch_id=row["batch_id"], derivation=json.loads(row["derivation"]) if row["derivation"] else None,
        deleted_at=row["deleted_at"],
        purging=bool(row["purging"]),
    )


def create_app(root=None, engine=None, qwen=None):
    store = Store(root if root is not None else WORKSPACE / "data" / "jobs")
    engines = profiles(WORKSPACE, engine, qwen)
    export_cache_lock = Lock()

    @asynccontextmanager
    async def lifespan(application):
        with store.worker_lock():
            store.setup()
            worker = Worker(store, engines)
            application.state.worker = worker
            worker.thread.start()
            try:
                yield
            finally:
                await run_in_threadpool(worker.close)

    app = FastAPI(title="LayerLens", version="0.3.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def protect_local_jobs(request: Request, call_next):
        if request.method in ("POST", "DELETE") and request.url.path.startswith("/api/v1/"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
                return JSONResponse({"detail": "Cross-origin job requests are disabled"}, status_code=403)
            length = request.headers.get("content-length")
            limit = MAX_UPLOAD * (MAX_BATCH if request.url.path == "/api/v1/batches" else 1)
            if length and length.isdecimal() and int(length) > limit + 256 * 1024:
                return JSONResponse({"detail": "The upload exceeds the size limit"}, status_code=413)
        return await call_next(request)

    def get_row(job_id):
        row = store.get(job_id.hex)
        if row is None:
            raise HTTPException(404, "Task not found")
        return row

    def get_result(job_id):
        row = get_row(job_id)
        if row["status"] != "succeeded":
            raise HTTPException(409, "The task has no downloadable result")
        result = store.root / job_id.hex / "result"
        if not result.is_dir():
            raise result_missing()
        return row, result

    def get_manifest(result):
        try:
            return preview_manifest(result)
        except FileNotFoundError as error:
            raise result_missing() from error

    def job_payload(row):
        return summarize(row, engines[row["engine_id"]])

    def engine_profile(engine_id):
        profile = engines.get(engine_id)
        if profile is None:
            raise HTTPException(422, {"code": "UNKNOWN_ENGINE", "message": "Unknown inference engine"})
        return profile

    def require_engine(engine_id):
        profile = engine_profile(engine_id)
        if profile.adapter is None or profile.adapter.missing() or not app.state.worker.thread.is_alive():
            raise HTTPException(503, {"code": "ENGINE_UNAVAILABLE", "message": "The inference engine is unavailable"})
        return profile

    def clone_job(row, options, derivation=None):
        child = uuid4().hex
        folder = store.root / child
        source = store.root / row["id"] / row["input_name"]
        if not source.is_file():
            raise HTTPException(404, "The saved input image is missing")
        folder.mkdir()
        try:
            copyfile(source, folder / row["input_name"])
            store.enqueue(child, row["filename"], row["input_name"], options, derivation,
                          engine_id=row["engine_id"], engine_version=engines[row["engine_id"]].version)
        except ParentUnavailable as error:
            rmtree(folder)
            raise HTTPException(409, {"code": "PARENT_UNAVAILABLE", "message": "The parent result has been deleted"}) from error
        except Exception:
            rmtree(folder)
            raise
        app.state.worker.changed.set()
        return job_payload(store.get(child))

    def parse_options(raw, profile):
        try:
            return profile.options.model_validate_json(raw).model_dump()
        except ValidationError as error:
            raise HTTPException(422, {
                "code": "INVALID_OPTIONS",
                "message": "Check the inference options",
                "fields": error.errors(include_input=False, include_context=False, include_url=False),
            }) from error

    def add_uploads(uploads, options, profile, batch_id=None):
        created, records = [], []
        try:
            for index, upload in enumerate(uploads):
                job_id = uuid4().hex
                folder = store.root / job_id
                folder.mkdir()
                created.append(folder)
                filename = re.sub(r"[\x00-\x1f\x7f]", "", re.split(r"[/\\]", upload.filename or "")[-1])
                filename = filename.strip()[:150] or "image"
                try:
                    input_name = save_input(upload, folder)
                except HTTPException as error:
                    raise HTTPException(error.status_code, {**error.detail, "file_index": index, "filename": filename}) from error
                records.append((job_id, filename, input_name))
            store.enqueue_many(records, options, batch_id, engine_id=profile.id, engine_version=profile.version)
        except Exception:
            for folder in created:
                rmtree(folder)
            raise
        app.state.worker.changed.set()
        return [record[0] for record in records]

    def batch_rows(batch_id):
        rows = store.batch(batch_id.hex)
        if not rows:
            raise HTTPException(404, "Batch not found")
        return rows

    def batch_summary(batch_id, rows):
        ready = all(row["status"] not in ("queued", "running") for row in rows)
        return {
            "batch_id": batch_id,
            "jobs": [job_payload(row) for row in rows],
            "export_url": f"/api/v1/batches/{batch_id}/exports/zip" if ready else None,
        }

    @app.get("/api/v1/capabilities")
    def capabilities():
        worker_alive = app.state.worker.thread.is_alive()
        available = {engine_id: profile.capabilities() for engine_id, profile in engines.items()}
        for entry in available.values():
            entry["ready"] = entry["ready"] and worker_alive
        legacy = available[DEFAULT_ENGINE]
        return {
            "engines": list(available.values()),
            "default_engine": DEFAULT_ENGINE,
            "worker_alive": worker_alive,
            "max_upload_bytes": MAX_UPLOAD,
            "max_image_pixels": MAX_PIXELS,
            "image_formats": list(FORMATS),
            "export_formats": sorted({kind for entry in available.values() for kind in entry["exports"]}),
            "max_batch": MAX_BATCH,
            # Deprecated single-engine fields. They describe the default engine only.
            "engine_ready": legacy["ready"],
            "missing": legacy["missing"],
            "defaults": legacy["defaults"],
            "options_schema": legacy["options_schema"],
            "resolutions": legacy["resolutions"],
            "low_vram_modes": ["group_offload"],
            "depth_split_ready": "depth" in legacy["actions"],
        }

    @app.get("/api/v1/system")
    def system():
        report = {"gpu": None, "memory": system_memory()}
        try:
            gpu = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,driver_version", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ).stdout.splitlines()[0]
            name, used, total, driver = map(str.strip, gpu.split(","))
            report["gpu"] = {"name": name, "used_mib": int(used), "total_mib": int(total), "driver": driver}
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
        return report

    @app.post("/api/v1/jobs", status_code=202)
    def submit(response: Response, file: UploadFile = File(), options: str = Form("{}"), engine_id: str = Form("seethrough")) -> Job:
        profile = require_engine(engine_id)
        submitted = parse_options(options, profile)
        (job_id,) = add_uploads([file], submitted, profile)
        response.headers["Location"] = f"/api/v1/jobs/{job_id}"
        return job_payload(store.get(job_id))

    @app.get("/api/v1/jobs")
    def jobs() -> list[Job]:
        return [job_payload(row) for row in store.recent()]

    @app.get("/api/v1/task-center")
    def task_center(
        filter: Literal["all", "active", "succeeded", "issues", "trash"] = "all",
        q: str = Query("", max_length=150),
        page: int = Query(1, ge=1, le=100000),
        engine_id: str | None = None,
    ):
        size = 12
        if engine_id is not None:
            engine_profile(engine_id)
        rows, counts, total = store.task_page(filter, q.strip(), page, size, engine_id)
        return {"jobs": [job_payload(row) for row in rows], "counts": counts, "total": total, "page_size": size}

    @app.post("/api/v1/task-center/trash", status_code=204)
    def trash_tasks(body: TaskSelection):
        if not store.set_trash(body.unique_ids(), True):
            raise HTTPException(409, {"code": "TRASH_CONFLICT", "message": "Only finished tasks can be moved to the recycle bin"})
        return Response(status_code=204)

    @app.post("/api/v1/task-center/restore", status_code=204)
    def restore_tasks(body: TaskSelection):
        if not store.set_trash(body.unique_ids(), False):
            raise HTTPException(409, {"code": "RESTORE_CONFLICT", "message": "The selected tasks are no longer restorable"})
        return Response(status_code=204)

    @app.post("/api/v1/task-center/purge")
    def purge_tasks(body: TaskSelection):
        with export_cache_lock:
            rows, conflict = store.begin_purge(body.unique_ids())
            if conflict:
                raise HTTPException(409, {"code": conflict, "message": "The selected tasks cannot be permanently deleted"})
            root = store.root.resolve()
            purged, failed = [], []
            for row in rows:
                job_id, batch_id = row["id"], row["batch_id"]
                directory = root / job_id
                try:
                    if directory.resolve() != directory:
                        raise OSError("Unexpected job directory link")
                    if batch_id:
                        for archive in root.glob(f"batch-{batch_id}*.zip"):
                            archive.unlink(missing_ok=True)
                    if directory.exists():
                        rmtree(directory)
                except OSError:
                    failed.append(job_id)
                    continue
                store.finish_purge(job_id)
                purged.append(job_id)
        return {"purged": purged, "failed": failed}

    @app.post("/api/v1/batches", status_code=202)
    def submit_batch(files: list[UploadFile] = File(), options: str = Form("{}"), engine_id: str = Form("seethrough")):
        if not 2 <= len(files) <= MAX_BATCH:
            raise HTTPException(422, f"Select 2 to {MAX_BATCH} images")
        profile = require_engine(engine_id)
        submitted = parse_options(options, profile)
        batch_id = uuid4()
        add_uploads(files, submitted, profile, batch_id.hex)
        return batch_summary(batch_id.hex, batch_rows(batch_id))

    @app.get("/api/v1/batches/{batch_id}")
    def get_batch(batch_id: UUID):
        return batch_summary(batch_id.hex, batch_rows(batch_id))

    @app.get("/api/v1/batches/{batch_id}/exports/zip", response_class=FileResponse)
    def download_batch(batch_id: UUID):
        with export_cache_lock:
            rows = batch_rows(batch_id)
            if any(row["status"] in ("queued", "running") for row in rows):
                raise HTTPException(409, "Wait for this batch to finish")
            try:
                path = batch_zip(store.root, batch_id.hex, rows)
            except FileNotFoundError as error:
                raise result_missing() from error
        return FileResponse(path, media_type="application/zip", filename=f"LayerLens-{batch_id.hex[:8]}.zip")

    @app.get("/api/v1/jobs/{job_id}")
    def status(job_id: UUID) -> Job:
        return job_payload(get_row(job_id))

    @app.get("/api/v1/jobs/{job_id}/source", response_class=FileResponse)
    def source_image(job_id: UUID):
        row = get_row(job_id)
        source = store.root / row["id"] / row["input_name"]
        if not source.is_file():
            raise HTTPException(404, "The saved input image is missing")
        return FileResponse(source, headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/v1/jobs/{job_id}/thumbnail", response_class=FileResponse)
    def job_thumbnail(job_id: UUID):
        row = store.get(job_id.hex, include_deleted=True)
        folder = store.root / job_id.hex
        if row is None or row["purging"] or not (folder / row["input_name"]).is_file():
            raise HTTPException(404, "The saved input image is missing")
        return FileResponse(thumbnail(folder / row["input_name"], folder), media_type="image/png",
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.delete("/api/v1/jobs/{job_id}", status_code=204)
    def delete_job(job_id: UUID):
        get_row(job_id)
        if not store.trash(job_id.hex):
            raise HTTPException(409, "Cancel or finish the task before deleting it")
        return Response(status_code=204)

    @app.post("/api/v1/jobs/{job_id}/restore")
    def restore_job(job_id: UUID) -> Job:
        if not store.restore(job_id.hex):
            raise HTTPException(404, "The task is not in the recycle bin")
        return job_payload(get_row(job_id))

    @app.post("/api/v1/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: UUID) -> Job:
        row = get_row(job_id)
        if row["status"] in ("queued", "running"):
            raise HTTPException(409, "Wait for the task to finish")
        profile = require_engine(row["engine_id"])
        derivation = json.loads(row["derivation"]) if row["derivation"] else None
        if derivation and derivation["method"] not in profile.ready_actions():
            raise HTTPException(503, {"code": "ACTION_UNAVAILABLE", "message": "This split method is unavailable for the engine"})
        return clone_job(row, json.loads(row["options"]), derivation)

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel(job_id: UUID) -> Job:
        row = get_row(job_id)
        if row["status"] != "cancelled" and not store.cancel(job_id.hex):
            row = get_row(job_id)
            if row["status"] != "cancelled":
                raise HTTPException(409, "The task already finished")
        return job_payload(get_row(job_id))

    @app.get("/api/v1/jobs/{job_id}/results/{result_id}/exports/{kind}", response_class=FileResponse)
    def download(job_id: UUID, result_id: Literal["base"], kind: Literal["psd", "depth-psd", "png-zip"]):
        row, result = get_result(job_id)
        if kind == "png-zip":
            try:
                path = png_zip(result)
            except FileNotFoundError as error:
                raise result_missing() from error
            return FileResponse(path, media_type="application/zip",
                                filename=f"{safe_stem(row['filename'])}-layers.zip")
        suffix = "_depth.psd" if kind == "depth-psd" else ".psd"
        path = result / f"input{suffix}"
        if not path.is_file():
            raise result_missing()
        filename = Path(row["filename"]).stem + suffix
        return FileResponse(path, media_type="image/vnd.adobe.photoshop", filename=filename)

    @app.get("/api/v1/jobs/{job_id}/results/{result_id}/layers")
    def layers(job_id: UUID, result_id: Literal["base"]):
        _, result = get_result(job_id)
        manifest = get_manifest(result)
        prefix = f"/api/v1/jobs/{job_id.hex}/assets"
        for layer in manifest["layers"]:
            for kind in ("image", "mask", "depth"):
                if kind in layer:
                    layer[kind] = f"{prefix}/{layer[kind]}"
        frame_assets = ("source", "composite", "depth") if manifest.get("has_depth", True) else ("source", "composite")
        return {**manifest, **{kind: f"{prefix}/{kind}.png" for kind in frame_assets}}

    @app.get("/api/v1/jobs/{job_id}/assets/{asset}", response_class=FileResponse)
    def get_asset(job_id: UUID, asset: str):
        if not ASSET_NAME.fullmatch(asset):
            raise HTTPException(404, "Asset not found")
        _, result = get_result(job_id)
        path = result / "preview" / asset
        if not path.is_file():
            get_manifest(result)
        if not path.is_file():
            raise result_missing()
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/api/v1/jobs/{job_id}/results/{result_id}/postprocess", status_code=202)
    def split(job_id: UUID, result_id: Literal["base"], body: SplitRequest) -> Job:
        row, result = get_result(job_id)
        profile = require_engine(row["engine_id"])
        if body.method not in profile.ready_actions():
            raise HTTPException(503, {"code": "ACTION_UNAVAILABLE", "message": "This split method is unavailable for the engine"})
        targets = list(dict.fromkeys(body.target_tags))
        if len(targets) > profile.max_split_targets:
            raise HTTPException(422, {"code": "TOO_MANY_TARGETS",
                                      "message": f"Select at most {profile.max_split_targets} layer(s) for this engine"})
        if set(targets) - {layer["tag"] for layer in get_manifest(result)["layers"]}:
            raise HTTPException(422, "A requested layer is missing")
        operation = {"parent_id": row["id"], "method": body.method, "target_tags": targets}
        if body.layers is not None:
            low, high = profile.split_layers or (0, -1)
            if not low <= body.layers <= high:
                raise HTTPException(422, {"code": "INVALID_OPTIONS", "message": "The child layer count is out of range"})
            operation["layers"] = body.layers
        return clone_job(row, json.loads(row["options"]), operation)

    frontend = WORKSPACE / "frontend" / "dist"
    if (frontend / "index.html").is_file():
        app.mount("/", WorkbenchFiles(directory=frontend, html=True), name="workbench")
    return app


class WorkbenchFiles(StaticFiles):
    """Revalidate the HTML shell on every load so a rebuilt UI never runs against a stale cached page."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.media_type == "text/html" or response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


app = create_app()

