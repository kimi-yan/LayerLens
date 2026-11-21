"""Check what this machine has for LayerLens: web app, GPU, memory, disk and each engine.

Usage, from the workspace root (any Python 3.10+, no packages needed):
  python scripts/doctor.py                 # report everything
  python scripts/doctor.py --require qwen  # also exit 1 unless that engine is ready
"""
import argparse
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.adapter.catalog import SETUP, missing_parts  # noqa: E402
from engine.adapter.command import Engine  # noqa: E402
from engine.adapter.qwen_command import QwenEngine  # noqa: E402

# RTX 4070 12 GB is the reference card; see docs/engines.md for what was measured on it.
MIN_VRAM_GIB = 12
MIN_RAM_GIB = {"seethrough": 16, "qwen": 32}
NAMES = {"seethrough": "See-through（二次元角色拆层）", "qwen": "Qwen-Image-Layered（通用拆层）"}


def line(ok, label, detail=""):
    mark = {True: "OK  ", False: "缺失", None: "提示"}[ok]
    print(f"  [{mark}] {label}" + (f"：{detail}" if detail else ""))


def gpu():
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True).stdout.splitlines()[0]
        name, total, driver = map(str.strip, output.split(","))
        return name, int(total) / 1024, driver
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def ram_gib():
    if os.name != "nt":
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3

    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("available", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_available", ctypes.c_ulonglong),
                    ("virtual_total", ctypes.c_ulonglong), ("virtual_available", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

    status = Status(length=ctypes.sizeof(Status))
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return status.total / 1024 ** 3


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--require", choices=sorted(SETUP), action="append", default=[])
    args = parser.parse_args()

    print("Web 工作台")
    backend = ROOT / ".local" / "backend-venv" / "Scripts" / "python.exe"
    line(backend.is_file(), "后端环境 .local/backend-venv", "" if backend.is_file() else "首次运行 Start-LayerLens.ps1 会自动创建")
    built = (ROOT / "frontend" / "dist" / "index.html").is_file()
    line(built, "前端构建 frontend/dist", "" if built else "首次运行 Start-LayerLens.ps1 会自动构建")

    print("\n硬件")
    card = gpu()
    if card:
        name, vram, driver = card
        line(vram >= MIN_VRAM_GIB - 0.5 or None, "GPU", f"{name}，{vram:.0f} GB 显存，驱动 {driver}" +
             ("" if vram >= MIN_VRAM_GIB - 0.5 else f"；低于参考配置的 {MIN_VRAM_GIB} GB，可能显存不足"))
    else:
        line(False, "GPU", "没有找到 nvidia-smi。推理需要 NVIDIA 显卡和驱动；没有显卡也能打开工作台、查看和导出已有结果")
    ram = ram_gib()
    line(ram >= MIN_RAM_GIB["qwen"] or None, "内存", f"{ram:.0f} GB" + ("" if ram >= MIN_RAM_GIB["qwen"] else "；通用拆层建议 32 GB 以上"))
    free = shutil.disk_usage(ROOT).free / 1024 ** 3
    line(True, "磁盘剩余", f"{free:.0f} GB（See-through 约需 {SETUP['seethrough']['disk_gib']} GB，Qwen 约需 {SETUP['qwen']['disk_gib']} GB）")

    failed = False
    for engine_id, adapter in (("qwen", QwenEngine(ROOT)), ("seethrough", Engine(ROOT))):
        missing = adapter.missing()
        print(f"\n{NAMES[engine_id]}")
        if not missing:
            line(True, "已就绪")
            if engine_id == "seethrough" and not adapter.depth_split_ready:
                line(None, "前后拆分不可用", "缺少 LaMa 权重，重新运行安装脚本即可补上")
            continue
        failed |= engine_id in args.require
        line(False, "未安装", "缺少" + "、".join(part["label"] for part in missing_parts(missing)))
        for path in missing[:4]:
            print(f"         {path}")
        if len(missing) > 4:
            print(f"         …… 另有 {len(missing) - 4} 项")
        print(f"         安装：{SETUP[engine_id]['command']}")
    print(f"\n安装步骤见 docs/engines.md")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
