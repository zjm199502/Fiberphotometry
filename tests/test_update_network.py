import hashlib
import io
import json
import socket
import ssl
from urllib.error import HTTPError, URLError
from zipfile import ZipFile

import pytest

from fiberphoto import updates


class Response(io.BytesIO):
    def __init__(self, data, url, size=None):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data) if size is None else size)}

    def geturl(self):
        return self.url


def transport(monkeypatch, *responses):
    calls = []
    replies = iter(responses)

    def open_request(request, timeout, context):
        calls.append(request)
        assert 0 < timeout <= 12
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, bytes):
            return Response(reply, request.full_url)
        return reply

    monkeypatch.setattr(updates, "urlopen", open_request)
    monkeypatch.setattr(updates.time, "sleep", lambda _: None)
    return calls


manifest = {"version": "0.6.6", "base": "0.6.0", "sha256": "a" * 64}
raw_host = "raw.githubusercontent.com"
api_host = "api.github.com"


def test_healthy_primary_uses_only_one_request(monkeypatch):
    calls = transport(monkeypatch, json.dumps(manifest).encode())
    assert updates.check("0.6.5", "0.6.0") == ("patch", manifest)
    assert [r.host for r in calls] == [raw_host]


@pytest.mark.parametrize("primary", [
    URLError(socket.gaierror()),
    b"<html>sign in</html>",
    b'{"message":"API rate limit exceeded"}',
    b'{"version":"0.6.4","base":"0.6.0","sha256":[]}',
    Response(b"{}", "http://example.test/login"),
])
def test_bad_primary_manifest_falls_back(monkeypatch, primary):
    calls = transport(monkeypatch, primary, json.dumps(manifest).encode())
    assert updates.check("0.6.5", "0.6.0") == ("patch", manifest)
    assert calls[1].full_url == "https://api.github.com/repos/zjm199502/Fiberphotometry/contents/channel.json?ref=updates"
    assert calls[1].get_header("Accept") == "application/vnd.github.raw+json"


@pytest.mark.parametrize("primary", [b"proxy error", Response(b"partial", updates.CHANNEL, size=2000)])
def test_corrupt_or_interrupted_primary_patch_falls_back(monkeypatch, tmp_path, primary):
    with io.BytesIO() as buf:
        with ZipFile(buf, "w") as archive:
            archive.writestr("fiberphoto/__init__.py", '__version__ = "0.6.6"\n')
        payload = buf.getvalue()
    sha = hashlib.sha256(payload).hexdigest()
    calls = transport(monkeypatch, primary, payload)
    monkeypatch.setattr(updates, "patch_root", lambda _: tmp_path)
    assert updates.stage(dict(manifest, sha256=sha), "0.6.5", "0.6.0") == "0.6.6"
    assert (tmp_path / "0.6.6" / ".sha256").read_text() == sha
    assert calls[1].full_url.endswith(f"/fiberlume-0.6.6-{sha}.zip?ref=updates")


def test_both_invalid_payloads_leave_the_old_installation_intact(monkeypatch, tmp_path):
    installed = tmp_path / "0.6.5"
    installed.mkdir()
    calls = transport(monkeypatch, b"bad zip", b"bad zip")
    monkeypatch.setattr(updates, "patch_root", lambda _: tmp_path)
    with pytest.raises(updates.UpdateSourceError, match="SHA-256"):
        updates.stage(manifest, "0.6.5", "0.6.0")
    assert [r.host for r in calls] == [raw_host, api_host]
    assert list(tmp_path.iterdir()) == [installed]


def test_retry_only_transient_failures_and_hide_proxy_credentials(monkeypatch):
    secret = "https://user:password@proxy.test/path?sensitive=1"
    limited = HTTPError(secret, 429, secret, {}, None)
    calls = transport(monkeypatch, URLError(TimeoutError(secret)), limited, ConnectionError(secret))
    with pytest.raises(updates.UpdateSourceError) as result:
        updates.check("0.6.5", "0.6.0")
    assert result.value.failures == {raw_host: "Connection failed or interrupted", api_host: "HTTP 429"}
    assert "password" not in str(result.value)
    assert [r.host for r in calls] == [raw_host, api_host, raw_host]


def test_permanent_failures_are_sanitized_without_retry(monkeypatch):
    secret = "sensitive proxy credentials"
    calls = transport(monkeypatch, ValueError(secret), URLError(ssl.SSLCertVerificationError(secret)))
    with pytest.raises(updates.UpdateSourceError) as result:
        updates.check("0.6.5", "0.6.0")
    assert "sensitive" not in str(result.value)
    assert result.value.failures[api_host] == "TLS"
    assert len(calls) == 2


def test_retry_after_two_temporary_failures(monkeypatch):
    unavailable = HTTPError(updates.CHANNEL, 503, "", {}, None)
    calls = transport(monkeypatch, unavailable, TimeoutError(), json.dumps(manifest).encode())
    assert updates.check("0.6.6", "0.6.0") == ("current", manifest)
    assert [r.host for r in calls] == [raw_host, api_host, raw_host]
