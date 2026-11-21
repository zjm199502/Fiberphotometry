"""Immutable launcher support. Only the fiberphoto package is patchable."""
import os
import re
import sys
from pathlib import Path


def version(text):
    if not isinstance(text, str) or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", text):
        raise ValueError("Invalid version")
    return tuple(map(int, text.split(".")))


def base_version():
    if getattr(sys, "frozen", False):
        return (Path(sys._MEIPASS) / "fiberlume-base.txt").read_text().strip()
    return None


def patch_root(base):
    version(base)
    if sys.platform == "win32":
        parent = Path(os.environ["LOCALAPPDATA"])
    elif sys.platform == "darwin":
        parent = Path.home() / "Library/Application Support"
    else:
        parent = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return parent / "FiberLume" / "patches" / base


def compatible(tag, base):
    v, b = version(tag), version(base)
    return v[:2] == b[:2] and b[2] == 0 and 1 <= v[2] <= 20


def activate(qa_root=None):
    base = base_version()
    if not base:
        return None
    root = qa_root / base if qa_root is not None else patch_root(base)
    if not root.is_dir():
        return None
    candidates = []
    for p in root.iterdir():
        if not re.fullmatch(r"\d+\.\d+\.\d+", p.name):
            continue
        if compatible(p.name, base) and (p / "fiberphoto/__init__.py").is_file() and not (p / ".disabled").exists():
            candidates.append(p)
    if candidates:
        patch = max(candidates, key=lambda p: version(p.name))
        sys.path.insert(0, str(patch))
        return patch
    return None
