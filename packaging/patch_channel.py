"""Build a channel snapshot or exercise a packaged install using an isolated patch root."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fiberlume_boot import compatible, version

SRC = Path(__file__).resolve().parents[1] / "src" / "fiberphoto"


def build(tag, base, probe=False):
    if tag != base and not compatible(tag, base):
        raise ValueError("Patch must be in slots 1–20 of this base")
    init = (SRC / "__init__.py").read_text(encoding="utf-8")
    expected = base if probe else tag
    if f'__version__ = "{expected}"' not in init.splitlines():
        raise ValueError("Source version mismatch")
    manifest = {"version": tag, "base": base}
    if tag == base:
        return manifest, b""
    buf = io.BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as archive:
        for src in sorted(SRC.rglob("*")):
            if src.suffix not in {".py", ".png", ".pdf"} or "__pycache__" in src.parts:
                continue
            name = src.relative_to(SRC.parent).as_posix()
            data = src.read_text(encoding="utf-8").encode("utf-8") if src.suffix == ".py" else src.read_bytes()
            if probe and src == SRC / "__init__.py":
                data = init.replace(f'__version__ = "{base}"', f'__version__ = "{tag}"').encode()
            # Normalize source line endings and fix metadata for reproducible archives.
            entry = ZipInfo(name)
            archive.writestr(entry, data, compress_type=ZIP_DEFLATED)
    payload = buf.getvalue()
    manifest["sha256"] = hashlib.sha256(payload).hexdigest()
    return manifest, payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("publish", "probe", "stage"))
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    init = (SRC / "__init__.py").read_text(encoding="utf-8")
    tag = re.search(r'^__version__ = "(\d+\.\d+\.\d+)"$', init, re.M)[1]
    major, minor, _ = version(tag)
    base = f"{major}.{minor}.0"
    if args.mode == "probe":
        tag = f"{major}.{minor}.1"
    manifest, payload = build(tag, base, probe=args.mode == "probe")
    if args.mode != "publish":
        from fiberphoto import updates
        with patch.object(updates, "fetch", return_value=payload), patch.object(
                updates, "patch_root", return_value=args.folder / "patches" / base):
            updates.stage(manifest, base, base)
        (args.folder / "expected-version.txt").write_text(tag, encoding="ascii")
        return
    args.folder.mkdir(parents=True, exist_ok=True)
    channel = args.folder / "channel.json"
    if channel.exists():
        previous = json.loads(channel.read_text())
        if version(previous["version"]) > version(tag) or (tag == previous["version"] and previous != manifest):
            raise ValueError("Refusing to replace an existing version or move the channel backwards")
    if payload:
        (args.folder / f"fiberlume-{tag}-{manifest['sha256']}.zip").write_bytes(payload)
    channel.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
