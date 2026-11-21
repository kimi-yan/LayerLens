import json
import logging
import os
from shutil import rmtree
import subprocess
from threading import Event, Thread


logger = logging.getLogger(__name__)
POLL_SECONDS = 0.25


def latest_event(path):
    if not path.is_file():
        return None
    parts = path.read_bytes().rsplit(b"\n", 2)
    return parts[-2].decode("utf-8") if len(parts) > 1 else None


def stop_child(process):
    if process.stdin and not process.stdin.closed:
        process.stdin.close()
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            # The Windows venv launcher owns a second Python process.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            process.kill()
        process.wait(timeout=5)


class Worker:
    def __init__(self, store, engines):
        self.store = store
        self.engines = engines
        self.stopping = Event()
        self.changed = Event()
        self.thread = Thread(target=self.run, name="layerlens-worker")

    def close(self):
        self.stopping.set()
        self.changed.set()
        self.thread.join()

    def run(self):
        while not self.stopping.is_set():
            self.changed.clear()
            job = self.store.claim()
            if job is None:
                self.changed.wait(POLL_SECONDS)
                continue
            self.run_job(job)

    def run_job(self, job):
        job_id = job["id"]
        folder = self.store.root / job_id
        work = folder / "work"
        log = folder / "engine.log"
        requeue = False
        try:
            # A previous attempt may have left scratch behind (failed cleanup after a requeue);
            # stale progress events in it would misreport this run.
            if work.is_dir():
                rmtree(work)
            work.mkdir()
            error, requeue = self.infer(job, folder, work, log)
        except (OSError, ValueError) as exception:
            logger.exception("Could not run job %s", job_id)
            with log.open("a", encoding="utf-8") as stream:
                stream.write(f"\nAdapter error: {exception}\n")
            error = "ENGINE_FAILED"

        try:
            self.store.settle(job_id, error, requeue)
        except OSError:
            logger.exception("Could not publish job %s", job_id)
            self.store.settle(job_id, "ENGINE_FAILED")
        settled = self.store.get(job_id, include_deleted=True)
        if settled and settled["status"] in ("queued", "cancelled") and work.is_dir():
            try:
                rmtree(work)
            except OSError:
                logger.exception("Could not clean job %s", job_id)

    def infer(self, job, folder, work, log):
        job_id = job["id"]
        options = json.loads(job["options"])
        engine = self.engines[job["engine_id"]].adapter
        if engine is None or engine.missing():
            return "ENGINE_UNAVAILABLE", False
        if job["derivation"]:
            operation = json.loads(job["derivation"])
            parent = self.store.root / operation["parent_id"] / "result"
            args, cwd, env = engine.launch_derivation(parent, work, operation, options)
        else:
            args, cwd, env = engine.launch(folder / job["input_name"], work, options)
        event_path = work / "progress.ndjson"
        last = None
        with log.open("ab") as output, subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.PIPE,
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:
            try:
                process.stdin.write(b"\x01")
                process.stdin.flush()
                while True:
                    event = latest_event(event_path)
                    if event and event != last:
                        self.store.progress(job_id, event)
                        last = event
                    code = process.poll()
                    if code is not None:
                        break
                    if self.store.get(job_id)["cancel_requested"]:
                        return None, False
                    if self.stopping.wait(POLL_SECONDS):
                        return None, True
            finally:
                if process.poll() is None:
                    stop_child(process)
        if code == 0:
            last = latest_event(event_path)
            if last and json.loads(last)["stage"] == "complete":
                return None, False
        with log.open("rb") as output:
            output.seek(-min(log.stat().st_size, 8192), os.SEEK_END)
            tail = output.read().lower()
        if b"out of memory" in tail or b"cublas_status_alloc_failed" in tail:
            return "CUDA_OOM", False
        if b"layerlens_split_rejected" in tail:
            return "SPLIT_REJECTED", False
        if b"layerlens_no_split" in tail:
            return "NO_SPLIT", False
        return "ENGINE_FAILED", False

