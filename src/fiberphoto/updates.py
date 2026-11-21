"""Read the project's HTTPS channel, verify a code-only bundle and stage it atomically."""
import hashlib
import io
import json
import os
import re
import shutil
import ssl
import tempfile
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen
from zipfile import ZipFile

from fiberlume_boot import compatible, patch_root, version

CHANNEL = "https://raw.githubusercontent.com/zjm199502/Fiberphotometry/updates/"
RELEASES = "https://github.com/zjm199502/Fiberphotometry/releases/tag/v"
MAX_SIZE = 32 * 1024 * 1024


def fetch(name, limit):
    request = Request(CHANNEL + name, headers={"User-Agent": "FiberLume updater", "Cache-Control": "no-cache"})
    import certifi

    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    with urlopen(request, timeout=30, context=context) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Update exceeds size limit")
    return data


def check(current, base):
    manifest = json.loads(fetch("channel.json", 32768))
    tag, target_base = manifest["version"], manifest["base"]
    current_version, target_version = version(current), version(tag)
    if version(target_base)[2] or (tag != target_base and not compatible(tag, target_base)):
        raise ValueError("Invalid update channel")
    if target_version <= current_version:
        return "current", manifest
    if base != target_base or tag == target_base:
        return "installer", manifest
    if not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"]):
        raise ValueError("Invalid patch checksum")
    return "patch", manifest


def stage(manifest, current, base):
    tag, digest = manifest["version"], manifest["sha256"]
    if manifest["base"] != base or not compatible(tag, base) or version(tag) <= version(current):
        raise ValueError("Incompatible update")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid patch checksum")
    payload = fetch(f"fiberlume-{tag}-{digest}.zip", MAX_SIZE)
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("Patch SHA-256 mismatch")
    root = patch_root(base)
    root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="stage-", dir=root))
    target = root / tag
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > 2048 or sum(entry.file_size for entry in entries) > 4 * MAX_SIZE:
                raise ValueError("Invalid patch size")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (not path.parts or path.parts[0] != "fiberphoto" or path.is_absolute()
                        or ".." in path.parts or "\\" in entry.filename or ":" in entry.filename
                        or path.suffix.lower() not in {".py", ".png", ".pdf"}):
                    raise ValueError("Invalid patch file")
                dest = temp.joinpath(*path.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, dest.open("xb") as out:
                    shutil.copyfileobj(source, out)
        init = (temp / "fiberphoto/__init__.py").read_text(encoding="utf-8")
        if not re.search(r'^__version__ = "' + re.escape(tag) + r'"$', init, re.M):
            raise ValueError("Patch version mismatch")
        (temp / ".sha256").write_text(digest, encoding="ascii")
        if (target / ".disabled").exists():
            raise ValueError("This patch failed to start previously. Install a newer update.")
        try:
            os.rename(temp, target)
        except OSError:
            if not (target / ".sha256").is_file() or (target / ".sha256").read_text() != digest:
                raise
        return tag
    finally:
        if temp.exists():
            shutil.rmtree(temp)
