"""Measure the unmodified See-through CLI on the local Windows GPU."""
import argparse
import ctypes
from ctypes import wintypes
from functools import wraps
import json
import os
from pathlib import Path
import runpy
import sys
from threading import Event, Thread
from time import perf_counter

import torch


class CounterValue(ctypes.Structure):
    _fields_ = [("status", wintypes.DWORD), ("value", ctypes.c_longlong)]


class CounterItem(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("reading", CounterValue)]


def sample_windows_memory(stop, peaks):
    pdh = ctypes.WinDLL("pdh")
    query = wintypes.HANDLE()
    if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)):
        return
    counters = {}
    try:
        for counter_name in ("Dedicated", "Shared"):
            counter = wintypes.HANDLE()
            path = f"\\GPU Process Memory(*)\\{counter_name} Usage"
            if not pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(counter)):
                counters[counter_name.lower()] = counter
        prefix = f"pid_{os.getpid()}_"
        while not stop.is_set():
            if not pdh.PdhCollectQueryData(query):
                for name, counter in counters.items():
                    size, count = wintypes.DWORD(), wintypes.DWORD()
                    args = counter, 0x400, ctypes.byref(size), ctypes.byref(count)
                    pdh.PdhGetFormattedCounterArrayW(*args, None)
                    if not size.value:
                        continue
                    buffer = ctypes.create_string_buffer(size.value)
                    if pdh.PdhGetFormattedCounterArrayW(*args, buffer):
                        continue
                    items = (CounterItem * count.value).from_buffer(buffer)
                    readings = [
                        item.reading.value for item in items
                        if item.name.startswith(prefix) and item.reading.status in (0, 1)
                    ]
                    if readings:
                        peaks[name] = max(peaks.get(name, 0), sum(readings))
            stop.wait(0.75)
    finally:
        pdh.PdhCloseQuery(query)


def memory_stats():
    return {
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
    }


def measure_stage(function, stages):
    @wraps(function)
    def timed(*args, **kwargs):
        torch.cuda.reset_peak_memory_stats()
        start = perf_counter()
        passed = False
        try:
            result = function(*args, **kwargs)
            torch.cuda.synchronize()
            passed = True
            return result
        finally:
            record = {
                "stage": function.__name__,
                "seconds": round(perf_counter() - start, 2),
                "passed": passed,
                **memory_stats(),
            }
            stages.append(record)
            print(f"\nM1 stage {json.dumps(record)}", flush=True)
    return timed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--measurement", type=Path, required=True)
    parser.add_argument("entry", type=Path)
    parser.add_argument("upstream_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    from utils import inference_utils

    sys.argv = [str(args.entry), *args.upstream_arguments]
    stages = []
    for stage in ("apply_layerdiff", "apply_marigold", "further_extr"):
        setattr(inference_utils, stage, measure_stage(getattr(inference_utils, stage), stages))

    stop = Event()
    peaks = {}
    monitor = Thread(target=sample_windows_memory, args=(stop, peaks), daemon=True)
    monitor.start()
    start = perf_counter()
    success = False
    try:
        runpy.run_path(str(args.entry), run_name="__main__")
        success = True
    finally:
        stop.set()
        monitor.join()
        summary = {
            "argv": sys.argv,
            "success": success,
            "seconds": round(perf_counter() - start, 2),
            "windows_process_peak_bytes": peaks,
            "stages": stages,
        }
        args.measurement.parent.mkdir(parents=True, exist_ok=True)
        args.measurement.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nM1 measurement {args.measurement}", flush=True)
