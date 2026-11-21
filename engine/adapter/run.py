"""Run the upstream See-through CLI with progress reporting and output verification."""
from functools import wraps
import json
from pathlib import Path
import runpy
import sys

from runner_io import Reporter, Steps, guard_owner


def track(report, function, stage, pipeline_class=None):
    if pipeline_class is not None:
        pass_index = 0

        def progress_bar(_pipeline, *, total):
            nonlocal pass_index
            pass_index += 1
            return Steps(report, stage, total, pass_index)

        pipeline_class.progress_bar = progress_bar

    @wraps(function)
    def run(*args, **kwargs):
        report(stage)
        return function(*args, **kwargs)

    return run


def verify(work, stem, resolution):
    """See-through contract: square color and depth PSDs with matching layers and sidecar."""
    from psd_tools import PSDImage

    color = PSDImage.open(work / f"{stem}.psd")
    depth = PSDImage.open(work / f"{stem}_depth.psd")
    metadata = json.loads((work / f"{stem}.psd.json").read_text(encoding="utf-8"))
    size = (resolution, resolution)
    if color.size != size or depth.size != size or tuple(metadata["frame_size"]) != size:
        raise ValueError("Engine PSD dimensions do not match the requested resolution")
    layers = [(layer.name, layer.bbox) for layer in color]
    if not layers or layers != [(layer.name, layer.bbox) for layer in depth]:
        raise ValueError("Engine PSD layers do not match")
    if len(layers) != len(metadata["parts"]) or any(
        tuple(metadata["parts"][tag]["xyxy"]) != bbox for tag, bbox in layers
    ):
        raise ValueError("Engine PSD offsets do not match the sidecar")


if __name__ == "__main__":
    guard_owner()
    progress_path = Path(sys.argv.pop(1))
    report = Reporter(progress_path)
    sys.argv[0] = "inference/scripts/inference_psd.py"
    report("loading")
    from utils import inference_utils as upstream

    upstream.apply_layerdiff = track(report, upstream.apply_layerdiff, "layerdiff", upstream.KDiffusionStableDiffusionXLPipeline)
    upstream.apply_marigold = track(report, upstream.apply_marigold, "depth", upstream.MarigoldDepthPipeline)
    upstream.further_extr = track(report, upstream.further_extr, "exporting")
    runpy.run_path(sys.argv[0], run_name="__main__")
    report("validating")
    source = Path(sys.argv[sys.argv.index("--srcp") + 1])
    resolution = int(sys.argv[sys.argv.index("--resolution") + 1])
    verify(progress_path.parent, source.stem, resolution)
    report("complete")
