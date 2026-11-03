"""Tests for POST /api/batch (B1, DAT-871): several files, limits on count and size, the same guard."""
import asyncio
import io
import json
import random
import socket

import pytest
from fastapi.testclient import TestClient

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from secondlook.web import app as webapp  # noqa: E402
from secondlook.web.app import CSP, MAX_BATCH_FILES, MAX_BATCH_REQUEST, create_app  # noqa: E402

client = TestClient(create_app(stub=False, rungs=[]))
stub_client = TestClient(create_app(stub=True))


def png(seed: int, size=(96, 64), fmt="PNG", **kw) -> bytes:
    rnd = random.Random(seed)
    small = Image.new("L", (9, 8))
    small.putdata([rnd.randrange(256) for _ in range(72)])
    buf = io.BytesIO()
    small.resize(size, Image.Resampling.NEAREST).convert("RGB").save(buf, fmt, **kw)
    return buf.getvalue()


def post(files, c=client):
    return c.post("/api/batch", files=[("files", (n, d, "application/octet-stream")) for n, d in files])


def test_several_files_each_get_their_own_answer_and_the_copies_are_grouped():
    base = png(3, (192, 128))
    r = post([("one.png", base), ("copy.jpg", png(3, (192, 128), "JPEG", quality=60)), ("other.png", png(4))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3 and body["checked"] == 3
    assert [x["file"] for x in body["results"]] == ["one.png", "copy.jpg", "other.png"]
    for x in body["results"]:
        assert x["status"] == 200 and x["answer"] == "cant_tell" and x["engine"] == "ladder"
        assert x["demo"] is False and x["evidence"][0]["raw"]["limit"] is True
    (g,) = body["near_duplicate_groups"]
    assert g["files"] == ["one.png", "copy.jpg"] and g["indexes"] == [0, 1] and g["label"] == "near-duplicate copies"
    assert "never change an answer" in g["note"]
    assert [x.get("near_duplicate_group") for x in body["results"]] == ["g1", "g1", None]
    assert body["limits"]["files"] == MAX_BATCH_FILES


def test_two_uploads_with_the_same_name_stay_separate():
    r = post([("photo.png", png(1)), ("photo.png", png(2))])
    assert [x["index"] for x in r.json()["results"]] == [0, 1] and r.json()["near_duplicate_groups"] == []


def test_a_bad_file_is_refused_on_its_own_and_the_others_are_checked():
    r = post([("good.png", png(1)), ("notes.txt", b"hi"), ("empty.png", b""), ("thin.png", png(2, (400, 40)))])
    by = {x["file"]: x for x in r.json()["results"]}
    assert by["good.png"]["status"] == 200
    assert by["notes.txt"]["status"] == 415 and by["empty.png"]["status"] == 400
    assert by["thin.png"]["status"] == 422 and "longer than 8:1" in by["thin.png"]["error"]
    assert r.json()["checked"] == 1


def test_one_file_over_25_mb_is_refused_on_its_own(monkeypatch):
    monkeypatch.setattr(webapp, "MAX_BYTES", 200)
    r = post([("small.png", png(1, (8, 8))[:100] + b"\0" * 10), ("big.png", png(2))])
    by = {x["file"]: x for x in r.json()["results"]}
    assert by["big.png"]["status"] == 413 and "25 MB" in by["big.png"]["error"]


def test_too_many_files_is_refused():
    ok = post([(f"{i}.png", png(i, (8, 8))) for i in range(MAX_BATCH_FILES)])
    assert ok.status_code == 200 and ok.json()["count"] == MAX_BATCH_FILES
    r = post([(f"{i}.png", png(i, (8, 8))) for i in range(MAX_BATCH_FILES + 1)])
    assert r.status_code == 413 and str(MAX_BATCH_FILES) in r.json()["detail"]


def test_the_total_size_is_limited(monkeypatch):
    monkeypatch.setattr(webapp, "MAX_BATCH_BYTES", 600)
    r = post([("a.png", png(1, (8, 8)) * 2), ("b.png", png(2, (8, 8)) * 8)])
    assert r.status_code == 413 and "100 MB" in r.json()["detail"]


def test_no_files_or_the_wrong_field_is_a_400():
    assert client.post("/api/batch", data={"x": "y"}).status_code == 400
    assert client.post("/api/batch", files={"file": ("a.png", png(1), "image/png")}).status_code == 400


def _raw_post(headers, path="/api/batch"):
    """Call the ASGI app directly; fail the test if it tries to read the body."""
    app = create_app(stub=False, rungs=[])
    sent = []

    async def receive():
        raise AssertionError("the body was read")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": path, "headers": headers,
             "query_string": b"", "http_version": "1.1", "scheme": "http", "root_path": "",
             "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 1)}
    asyncio.run(app(scope, receive, send))
    return sent[0]["status"], dict(sent[0]["headers"])


def test_oversize_batch_is_refused_from_content_length_before_the_body_is_read():
    status, headers = _raw_post([(b"content-length", str(MAX_BATCH_REQUEST + 1).encode())])
    assert status == 413 and headers[b"content-security-policy"] == CSP.encode()


def test_missing_or_bad_content_length_is_refused():
    assert _raw_post([])[0] == 411
    assert _raw_post([(b"content-length", b"x")])[0] == 400


def test_check_keeps_its_own_limit_when_batch_has_a_larger_one():
    assert _raw_post([(b"content-length", str(webapp.MAX_REQUEST + 1).encode())], "/api/check")[0] == 413
    assert webapp.MAX_REQUEST < MAX_BATCH_REQUEST


def test_security_headers_and_the_stub_stays_marked_demo():
    r = post([("demo-concerns.png", png(1))], stub_client)
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == CSP and r.headers["x-content-type-options"] == "nosniff"
    x = r.json()["results"][0]
    assert x["engine"] == "stub" and x["demo"] is True


def test_the_server_still_binds_to_loopback_only():
    import inspect

    from secondlook.web import __main__ as entry
    assert 'host="127.0.0.1"' in inspect.getsource(entry)


def test_a_batch_request_makes_no_network_call(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network used")

    for name in ("getaddrinfo", "gethostbyname", "create_connection"):
        monkeypatch.setattr(socket, name, refuse)
    assert post([("a.png", png(1))]).status_code == 200


def test_the_upload_dir_is_deleted_after_the_batch(monkeypatch, tmp_path):
    import tempfile
    made = []
    real = tempfile.TemporaryDirectory

    def spy(*a, **k):
        d = real(*a, **k)
        made.append(d.name)
        return d

    monkeypatch.setattr(webapp.tempfile, "TemporaryDirectory", spy)
    post([("a.png", png(1)), ("b.png", png(2))])
    import os
    assert made and not any(os.path.exists(p) for p in made)
