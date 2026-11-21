"""Resumable, verified downloads of pinned Hugging Face files. Standard library only.

Each file is tried against hf-mirror.com, huggingface.co and ModelScope in turn, resumed from
its .part file after a dropped connection, and accepted only when its size and LFS sha256 (or
git blob sha1 for small files) match the pinned revision's manifest. Set LAYERLENS_HF_HOSTS to
a comma-separated subset (for example "huggingface") to change the order.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
MODELS = ROOT / "data" / "cache" / "models"
CHUNK_SIZE = 4 * 1024 * 1024
PROGRESS_INTERVAL = 1024 * 1024 * 1024
HOSTS = {
    "mirror": "https://hf-mirror.com/{owner}/{repo}/resolve/{revision}/{key}",
    "huggingface": "https://huggingface.co/{owner}/{repo}/resolve/{revision}/{key}",
    "modelscope": "https://www.modelscope.cn/models/{owner}/{repo}/resolve/master/{key}",
}


def hosts():
    names = [name.strip() for name in os.environ.get("LAYERLENS_HF_HOSTS", "mirror,huggingface,modelscope").split(",")]
    return [HOSTS[name] for name in names if name in HOSTS] or list(HOSTS.values())


def siblings(owner, repo, revision):
    """File manifest (name, size, hashes) of a pinned revision."""
    for host in ("https://huggingface.co", "https://hf-mirror.com"):
        try:
            with urlopen(f"{host}/api/models/{owner}/{repo}/revision/{revision}?blobs=true", timeout=30) as source:
                metadata = json.load(source)
            break
        except OSError as error:
            print(f"Metadata from {host} failed: {error}", flush=True)
    else:
        raise SystemExit(f"Could not read the file list of {owner}/{repo}. Check the network or proxy and run again.")
    if metadata["sha"] != revision:
        raise SystemExit(f"{owner}/{repo} returned revision {metadata['sha']}, expected {revision}")
    for file in metadata["siblings"]:
        name = PurePosixPath(file["rfilename"])
        if name.is_absolute() or ".." in name.parts or ":" in str(name):
            raise SystemExit(f"Unexpected file name {name} in {owner}/{repo}")
    return metadata["siblings"]


def urls(owner, repo, revision, name):
    key = quote(str(name), safe="/")
    return [template.format(owner=owner, repo=repo, revision=revision, key=key) for template in hosts()]


def verify(path, file):
    if not path.is_file() or path.stat().st_size != file["size"]:
        return False
    with path.open("rb") as stream:
        if file.get("lfs"):
            return hashlib.file_digest(stream, "sha256").hexdigest() == file["lfs"]["sha256"]
        digest = hashlib.sha1(f"blob {file['size']}\0".encode(), usedforsecurity=False)
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
        return digest.hexdigest() == file["blobId"]


def fetch(path, sources, file, attempts=12):
    label = str(path.relative_to(ROOT))
    expected_size = file["size"]
    if verify(path, file):
        return label
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.part")
    if expected_size >= PROGRESS_INTERVAL:
        print(f"Downloading {label} ({expected_size / 1024 ** 3:.2f} GiB)", flush=True)
    last_error = None
    for attempt in range(attempts):
        try:
            resume_at = partial.stat().st_size if partial.exists() else 0
            if resume_at == expected_size and verify(partial, file):
                partial.replace(path)
                return label
            if resume_at >= expected_size:
                resume_at = 0
            headers = {"Accept-Encoding": "identity"}
            if resume_at:
                headers["Range"] = f"bytes={resume_at}-"
            with urlopen(Request(sources[attempt % len(sources)], headers=headers), timeout=60) as source:
                if source.status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", source.headers.get("Content-Range", ""))
                    if not match or int(match[1]) != resume_at or int(match[3]) != expected_size:
                        raise IOError("Unexpected Content-Range")
                elif source.status == 200:
                    resume_at = 0
                else:
                    raise IOError(f"Unexpected HTTP status {source.status}")
                written = resume_at
                next_report = written + PROGRESS_INTERVAL
                with partial.open("ab" if resume_at else "wb") as target:
                    while chunk := source.read(CHUNK_SIZE):
                        written += len(chunk)
                        if written > expected_size:
                            raise IOError("Download exceeds manifest size")
                        target.write(chunk)
                        if written >= next_report:
                            print(f"{label}: {written / 1024 ** 3:.2f}/{expected_size / 1024 ** 3:.2f} GiB", flush=True)
                            next_report = written + PROGRESS_INTERVAL
            if not verify(partial, file):
                raise IOError(f"Size or digest check failed ({written}/{expected_size} bytes)")
            partial.replace(path)
            return label
        except (OSError, ValueError) as error:
            last_error = error
            print(f"Retry {attempt + 1} for {label}: {error}", flush=True)
            time.sleep(min(attempt + 1, 8))
    raise RuntimeError(f"Could not download {label}: {last_error}")


def run(plan, title):
    """Download (path, urls, manifest entry) triples four at a time; exit non-zero on any failure."""
    total = sum(entry[2]["size"] for entry in plan)
    print(f"{title}: {len(plan)} files, {total / 1024 ** 3:.1f} GiB", flush=True)
    failures = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        downloads = [pool.submit(fetch, *entry) for entry in plan]
        for download in as_completed(downloads):
            try:
                print(f"Verified {download.result()}", flush=True)
            except RuntimeError as error:
                failures.append(str(error))
    if failures:
        raise SystemExit("\n".join(failures) + "\nRun the same command again to resume.")
    print(f"{title}: all files verified", flush=True)
