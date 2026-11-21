"""Process handshake and progress reporting shared by every engine runner."""
import json
import os
from pathlib import Path
import sys
from threading import Thread
from time import sleep


def wait_for_owner():
    if os.name == "nt":
        from ctypes import WinDLL
        from ctypes.wintypes import BOOL, DWORD, HANDLE, LPDWORD, LPVOID
        from msvcrt import get_osfhandle

        # A blocking CRT read can hold the stdin lock while native libraries load.
        peek = WinDLL("kernel32").PeekNamedPipe
        peek.argtypes = (HANDLE, LPVOID, DWORD, LPDWORD, LPDWORD, LPDWORD)
        peek.restype = BOOL
        handle = get_osfhandle(sys.stdin.fileno())
        while peek(handle, None, 0, None, None, None):
            sleep(0.25)
    else:
        os.read(sys.stdin.fileno(), 1)
    os._exit(2)


def guard_owner():
    if os.read(sys.stdin.fileno(), 1) != b"\x01":
        sys.exit("Missing engine owner")
    Thread(target=wait_for_owner, daemon=True).start()


class Reporter:
    def __init__(self, path):
        self.path = Path(path)

    def __call__(self, stage, step=None, total=None, pass_index=None):
        event = {"stage": stage, "step": step, "total_steps": total, "pass_index": pass_index}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")


class Steps:
    def __init__(self, report, stage, total, pass_index):
        self.report = report
        self.stage = stage
        self.total = total
        self.pass_index = pass_index
        self.step = 0

    def __enter__(self):
        self.update(0)
        return self

    def __exit__(self, *_):
        return False

    def update(self, count=1):
        self.step += count
        self.report(self.stage, self.step, self.total, self.pass_index)
