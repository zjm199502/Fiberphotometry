"""Read the project's HTTPS channel, verify a code-only bundle and stage it atomically."""
import hashlib
import io
import json
import os
import re
import shutil
import socket
import ssl
import tempfile
import time
from http.client import HTTPException
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zipfile import ZipFile

from fiberlume_boot import compatible, patch_root, version

CHANNEL = "https://raw.githubusercontent.com/zjm199502/Fiberphotometry/updates/"
CONTENTS_API = "https://api.github.com/repos/zjm199502/Fiberphotometry/contents/"
RELEASES = "https://github.com/zjm199502/Fiberphotometry/releases/tag/v"
MAX_SIZE = 32 * 1024 * 1024


class UpdateSourceError(OSError):
    """Safe to display. Details contain only fixed source names and reason categories."""

    def __init__(self, failures):
        self.failures = failures
        super().__init__("\n".join(f"{host}: {reason}" for host, reason in failures.items()))


class _InvalidResponse(ValueError):
    """Only constructed with fixed diagnostic text suitable for the GUI."""


def _manifest(data):
    manifest = json.loads(data)
    if not isinstance(manifest, dict):
        raise ValueError("Invalid update channel")
    tag, base = manifest.get("version"), manifest.get("base")
    if version(base)[2] or (tag != base and not compatible(tag, base)):
        raise ValueError("Invalid update channel")
    return manifest


def _read_source(request, limit, digest, context):
    timeout = 12 if digest else 5
    deadline = time.monotonic() + (60 if digest else 10)
    with urlopen(request, timeout=timeout, context=context) as response:
        endpoint = urlsplit(response.geturl())
        if endpoint.scheme != "https" or endpoint.netloc not in ("raw.githubusercontent.com", "api.github.com"):
            raise _InvalidResponse("Unexpected redirect")
        size = response.headers.get("Content-Length")
        if size is not None and (not size.isdecimal() or int(size) > limit):
            raise _InvalidResponse("Invalid content length")
        data = bytearray()
        while time.monotonic() < deadline:
            chunk = response.read1(min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise _InvalidResponse("Update exceeds size limit")
        else:
            raise TimeoutError
        if size is not None and len(data) != int(size):
            raise ConnectionError("Incomplete response")
    if digest:
        if hashlib.sha256(data).hexdigest() != digest:
            raise _InvalidResponse("SHA-256 mismatch")
    else:
        try:
            manifest = _manifest(data)
            if manifest["version"] != manifest["base"] and not re.fullmatch("[0-9a-f]{64}", str(manifest.get("sha256"))):
                raise ValueError
        except (ValueError, RecursionError):
            raise _InvalidResponse("Invalid update manifest") from None
    return bytes(data)


def _network_failure(exc):
    if isinstance(exc, HTTPError):
        exc.close()
        return f"HTTP {exc.code}", exc.code in (408, 500, 502, 503, 504)
    if isinstance(exc, URLError):
        exc = exc.reason
    if isinstance(exc, ssl.SSLError):
        return "TLS", False
    if isinstance(exc, socket.gaierror):
        return "DNS", True
    if isinstance(exc, TimeoutError):
        return "Timeout", True
    return "Connection failed or interrupted", True


def fetch(name, limit):
    import certifi

    digest = None
    if name != "channel.json":
        patch = re.fullmatch(r"fiberlume-\d+\.\d+\.\d+-([0-9a-f]{64})\.zip", name)
        if not patch:
            raise ValueError("Invalid patch filename")
        digest = patch[1]
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    headers = {"User-Agent": "FiberLume updater", "Cache-Control": "no-cache"}
    requests = [Request(CHANNEL + name, headers=headers),
                Request(CONTENTS_API + name + "?ref=updates", headers={
                    **headers, "Accept": "application/vnd.github.raw+json"})]
    failures = {}
    for attempt in range(2):
        retry = []
        for request in requests:
            try:
                return _read_source(request, limit, digest, context)
            except _InvalidResponse as exc:
                failures[request.host] = str(exc)
            except ValueError:
                # urllib can include the proxy configuration in a ValueError.
                failures[request.host] = "Invalid connection or response"
            except (OSError, HTTPException) as exc:
                reason, transient = _network_failure(exc)
                failures[request.host] = reason
                if transient:
                    retry.append(request)
        if not retry or attempt:
            break
        requests = retry
        time.sleep(0.4)
    raise UpdateSourceError(failures)


def check(current, base):
    manifest = _manifest(fetch("channel.json", 32768))
    tag, target_base = manifest["version"], manifest["base"]
    if version(tag) <= version(current):
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
