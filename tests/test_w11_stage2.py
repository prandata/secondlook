"""W11 design pass, stage 2 (DAT-850): the gaps the 29 Oct showcase recording found at dfe273f.

1. A "Download receipt" control on the answer view (PDF and JSON), on POST /api/receipt and, for a sample,
   POST /api/samples/{id}/receipt. The JSON is the canonical file `secondlook check --receipt` writes.
2. The commit hash under the answer and the deep check shows short, never split, with the full one in a title.
3. /api/health reports Glimmer's real state, from a loopback-only check with a short timeout.
4. Small cream text on a rust fill (the contrast test for rules that set their own fill is in test_w11_design.py).
5. The deep-check timing line names the one test run its range comes from.
No real Glimmer server is started: every request is answered by a patched opener.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.error
import urllib.request

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from secondlook import cli, ladder  # noqa: E402
from secondlook import glimmer as g  # noqa: E402
from secondlook import receipt as receipts  # noqa: E402
from secondlook.rungs import context as context_rung  # noqa: E402
from secondlook.rungs import exif as exif_rung  # noqa: E402
from secondlook.web import desk  # noqa: E402
from secondlook.web import samples as sample_catalogue  # noqa: E402
from secondlook.web.app import STATIC, create_app  # noqa: E402

HTML = (STATIC / "index.html").read_text()
APP = (STATIC / "app.js").read_text()
INV = (STATIC / "investigate.js").read_text()
CSS = (STATIC / "app.css").read_text()
INV_CSS = (STATIC / "investigate.css").read_text()
ROOT = STATIC.parents[2]
RUNGS = [("exif", exif_rung.check), ("context", context_rung.check)]
CAPTION = "Shot on iPhone 15 Pro."


def canon_jpeg(tmp_path, name="canon.jpg"):
    from PIL import Image

    ex = Image.Exif()
    ex[0x010F] = "Canon"
    ex[0x0110] = "Canon EOS R5"
    p = tmp_path / name
    Image.new("RGB", (8, 8)).save(p, "JPEG", exif=ex)
    return p


@pytest.fixture
def client():
    return TestClient(create_app(stub=False, rungs=RUNGS))


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(receipts, "_now", lambda: "2026-10-29T00:00:00Z")


def post_receipt(client, path, fmt=None, caption=None):
    url = "/api/receipt" + (f"?format={fmt}" if fmt else "")
    return client.post(url, files={"file": (path.name, path.read_bytes(), "image/jpeg")},
                       data={"caption": caption} if caption else None)


# ---- 1. Download receipt: the server ----------------------------------------------------------------

def test_json_receipt_is_the_canonical_file_the_cli_writes(client, tmp_path, fixed_clock):
    path = canon_jpeg(tmp_path)
    res = post_receipt(client, path, "json")
    assert res.status_code == 200 and res.headers["content-type"].startswith("application/json")
    assert res.headers["content-disposition"] == 'attachment; filename="canon.jpg.receipt.json"'
    rc = json.loads(res.content)
    assert res.content == receipts.canonical_json(rc), "sorted keys, two-space indent, one trailing newline"
    # The CLI's receipt for the same file and rungs: byte for byte the same, apart from the version the web layer
    # fills in when the ladder has none (the CLI writes the ladder's own).
    out = tmp_path / "cli"
    out.mkdir()
    assert cli.write_receipt(ladder.run(str(path), rungs=RUNGS), str(path), str(out)) == 0
    from_cli = json.loads((out / "canon.jpg.receipt.json").read_bytes())
    for r in (rc, from_cli):
        r["tool"].pop("version")
    assert rc == from_cli
    assert rc["schema"] == "second-look-receipt/1" and rc["file"]["name"] == "canon.jpg"
    assert rc["file"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert str(tmp_path) not in res.text and "secondlook-" not in res.text, "no temp path in the receipt"
    assert res.headers["x-secondlook-answer"] == rc["answer"]


def test_pdf_receipt_is_still_the_default(client, tmp_path):
    res = post_receipt(client, canon_jpeg(tmp_path))
    assert res.status_code == 200 and res.content.startswith(b"%PDF")
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"] == 'attachment; filename="canon.jpg.receipt.pdf"'
    assert post_receipt(client, canon_jpeg(tmp_path), "pdf").content.startswith(b"%PDF")


def test_an_unknown_receipt_format_is_refused(client, tmp_path):
    res = post_receipt(client, canon_jpeg(tmp_path), "html")
    assert res.status_code == 400 and "format=pdf or format=json" in res.json()["detail"]


def test_receipt_checks_the_file_with_its_caption_as_the_answer_did(client, tmp_path):
    """The page sends the caption the answer was checked with, so the receipt's answer is the same one."""
    path = canon_jpeg(tmp_path)
    shown = client.post("/api/check", files={"file": (path.name, path.read_bytes(), "image/jpeg")},
                        data={"caption": CAPTION}).json()
    with_caption = post_receipt(client, path, "json", caption=CAPTION)
    assert shown["answer"] == "concerns" == json.loads(with_caption.content)["answer"]
    assert with_caption.headers["x-secondlook-answer"] == "concerns"
    rc = json.loads(with_caption.content)
    assert [c["rung"] for c in rc["evidence"]] == [e["rung"] for e in shown["evidence"]]
    without = post_receipt(client, path, "json")
    assert json.loads(without.content)["answer"] == "cant_tell", "no caption, no context check"
    too_long = post_receipt(client, path, "json", caption="x" * 2001)
    assert too_long.status_code == 400 and "caption" in too_long.json()["detail"]


def test_receipt_of_a_stub_answer_is_marked_demo(tmp_path):
    stub = TestClient(create_app(stub=True))
    rc = json.loads(post_receipt(stub, canon_jpeg(tmp_path), "json").content)
    assert rc["demo"] is True


def _fake_samples(tmp_path):
    src = canon_jpeg(tmp_path, "s1.jpg")
    data = src.read_bytes()
    return (sample_catalogue.Sample("01", "s1.jpg", "Title 1", "Kind", "Credit", "CC0", "source",
                                    hashlib.sha256(data).hexdigest()),)


def test_sample_receipt_checks_the_sample_in_place(tmp_path, monkeypatch, fixed_clock):
    monkeypatch.setattr(sample_catalogue, "CATALOGUE", _fake_samples(tmp_path))
    seen = []

    def exif(path):
        seen.append(path)
        return exif_rung.check(path)

    client = TestClient(create_app(stub=False, rungs=[("exif", exif)], samples_dir=str(tmp_path)))
    checked = client.post("/api/samples/01/check").json()
    res = client.post("/api/samples/01/receipt?format=json")
    assert res.status_code == 200 and seen == [str(tmp_path / "s1.jpg")] * 2, "the original file, read in place"
    rc = json.loads(res.content)
    assert rc["file"] == {"name": "s1.jpg", "size_bytes": (tmp_path / "s1.jpg").stat().st_size,
                          "sha256": hashlib.sha256((tmp_path / "s1.jpg").read_bytes()).hexdigest()}
    assert rc["answer"] == checked["answer"] == res.headers["x-secondlook-answer"]
    assert res.headers["content-disposition"] == 'attachment; filename="s1.jpg.receipt.json"'
    pdf = client.post("/api/samples/01/receipt")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    for bad in ("../01", "99", "s1.jpg"):
        assert client.post(f"/api/samples/{bad}/receipt").status_code == 404, bad
    assert client.post("/api/samples/01/receipt?format=txt").status_code == 400


def test_receipt_route_keeps_its_size_limit():
    from secondlook.web.app import MAX_REQUEST, REQUEST_LIMITS

    assert REQUEST_LIMITS["/api/receipt"][0] == MAX_REQUEST


# ---- 1. Download receipt: the page -------------------------------------------------------------------

def test_the_answer_view_has_a_download_receipt_control():
    result = HTML[HTML.index('<section id="result"'):HTML.index('id="investigate"')]
    block = result[result.index('id="receipt"'):result.index('id="meta"')]
    assert '<h3 id="receipt-title">Download receipt</h3>' in block
    assert 'role="group" aria-labelledby="receipt-title"' in block
    assert 'id="receipt-pdf" data-format="pdf" hidden>PDF</button>' in block
    assert 'id="receipt-json" data-format="json">JSON</button>' in block
    assert 'id="receipt-status" role="status" aria-live="polite"' in block
    # It sits under the evidence, above the version line.
    assert result.index('id="g-found"') < result.index('id="receipt"') < result.index('id="meta"')


def test_receipt_copy_is_true_to_the_receipt():
    about = re.sub(r"\s+", " ", HTML[HTML.index('id="receipt-about">'):HTML.index("</p>", HTML.index('id="receipt-about">'))])
    assert "the file's sha256, the answer, and every card with what it does not prove" in about
    assert "Making it checks the file again" in about and "It carries no signature" in about
    rc_doc = (ROOT / "docs" / "CLAIMS.md").read_text()
    assert "it is not signed and proves nothing about the picture" in rc_doc
    assert "—" not in about


FRESH_LINE = ("Each download is a fresh check with its own created_at time, so two downloads can differ in created_at "
              "and in the receipt's sha256.")


def test_the_receipt_panel_says_each_download_is_a_fresh_check():
    """Judge persona test, 30 Oct (DAT-850): the PDF's "SHA-256 of the JSON receipt" never matched the JSON from
    the other button, because each button checks the file again. One short line in the panel says so."""
    block = HTML[HTML.index('id="receipt"'):HTML.index('id="meta"')]
    m = re.search(r'<p class="receipt-about" id="receipt-fresh">(.*?)</p>', block, re.S)
    assert m, "the line is in the receipt panel"
    line = re.sub(r"\s+", " ", re.sub(r"</?code>", "", m.group(1))).strip()
    assert line == FRESH_LINE and len(line.split()) <= 25 and "\u2014" not in line
    assert block.index('id="receipt-about"') < block.index('id="receipt-fresh"') < block.index('id="receipt-status"')


def test_two_downloads_differ_only_in_created_at_and_the_receipts_sha256(client, tmp_path, monkeypatch):
    """What the line says, on the server: two JSON receipts of one file a second apart differ in created_at and in
    their own sha256, and in nothing else; the file's sha256 is the same in both."""
    path = canon_jpeg(tmp_path)
    got = []
    for t in ("2026-10-30T00:00:00Z", "2026-10-30T00:00:01Z"):
        monkeypatch.setattr(receipts, "_now", lambda t=t: t)
        res = post_receipt(client, path, "json")
        assert res.status_code == 200
        got.append(res.content)
    a, b = (json.loads(x) for x in got)
    assert hashlib.sha256(got[0]).hexdigest() != hashlib.sha256(got[1]).hexdigest()
    assert (a["created_at"], b["created_at"]) == ("2026-10-30T00:00:00Z", "2026-10-30T00:00:01Z")
    assert a["file"]["sha256"] == b["file"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    a.pop("created_at"), b.pop("created_at")
    assert a == b

def test_page_posts_the_same_file_and_caption_or_the_sample_for_the_receipt():
    fn = APP[APP.index("async function downloadReceipt"):APP.index("async function check(")]
    assert 'fd.append("file", of.file, of.file.name)' in fn and 'fd.append("caption", of.caption)' in fn
    assert '"/api/receipt?format=" + fmt' in fn
    assert '"/api/samples/" + encodeURIComponent(of.sample || "") + "/receipt?format=" + fmt' in fn
    # The download goes through a Blob link; the page writes text only.
    assert "URL.createObjectURL(blob)" in fn and "a.download = name" in fn and "innerHTML" not in APP
    # A receipt whose answer differs from the screen is said plainly.
    assert 'res.headers.get("x-secondlook-answer")' in fn and "differs from the one on screen" in fn
    # A newer check drops a receipt still on its way; the buttons keep focus while it runs.
    assert "if (id !== receiptRun) return;" in fn and 'b.setAttribute("aria-disabled", "true")' in fn
    # Every supersede path (a new check, or a caption refusal) clears the shown answer and resets the receipt.
    sup = APP[APP.index("function supersede() {"):APP.index("async function check(")]
    assert "shown = null;" in sup and "receiptReset();" in sup and "return ++runId;" in sup
    assert "const id = supersede();" in APP[APP.index("async function check("):APP.index("function runFile")]
    refuse = APP[APP.index("if (!captionCheck()) {"):APP.index("if (previewUrl) URL.revokeObjectURL")]
    assert "supersede();" in refuse and "++runId" not in refuse
    assert APP.count("++runId") == 1, "runId moves only in supersede()"
    # The PDF button shows only when health says the PDF writer imports here.
    assert '$("receipt-pdf").hidden = !(h.capabilities && h.capabilities.receipt === true);' in APP


# ---- 2. the commit hash never breaks badly ----------------------------------------------------------

def test_commit_hash_shows_short_with_the_full_one_in_a_title():
    fn = APP[APP.index("function version("):APP.index("window.secondlookVersion")]
    assert "/^[0-9a-f]{40}$/" in fn and "full.slice(0, 12)" in fn and 'n.title = "Commit " + full' in fn
    assert '$("meta").replaceChildren("Engine: " + r.engine + " · secondlook ", version(r.version));' in APP
    assert "window.secondlookVersion(r.version)" in INV
    assert ".version { white-space: nowrap; }" in CSS
    assert "break-all" not in CSS + INV_CSS, "no rule may split a word at any letter"


# ---- 3. /api/health reports Glimmer's real state ---------------------------------------------------

class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(models=("muse-glimmer",), vision=True, fail=None):
    calls = []

    def open_(req, timeout=None):
        calls.append((req.full_url, timeout))
        if fail:
            raise fail
        if req.full_url.endswith("/v1/models"):
            return _Reply(json.dumps({"data": [{"id": m} for m in models]}).encode())
        return _Reply(json.dumps({"modalities": {"vision": vision}}).encode())

    return open_, calls


def _use(monkeypatch, opener):
    """Every Glimmer request of the health check goes to `opener`, whatever its deadline."""
    monkeypatch.setattr(g, "deadline_open", lambda end, clock=None: opener)


def _health(monkeypatch, opener):
    _use(monkeypatch, opener)
    return TestClient(create_app(stub=False, rungs=[])).get("/api/health").json()


def test_health_says_glimmer_is_up_when_its_server_answers(monkeypatch):
    monkeypatch.delenv(g.URL_ENV, raising=False)
    opener, calls = _opener()
    h = _health(monkeypatch, opener)
    assert h["capabilities"]["glimmer"] is True
    assert h["details"]["glimmer"] == {"ready": True, "model": "muse-glimmer", "vision": True}
    assert [u for u, _ in calls] == ["http://127.0.0.1:8081/v1/models", "http://127.0.0.1:8081/props"]
    assert all(t == desk.GLIMMER_TIMEOUT <= 1.0 for _, t in calls), "a short timeout on every request"


@pytest.mark.parametrize("fail, reason", [
    (urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")), "not_running"),
    (ConnectionRefusedError(61, "Connection refused"), "not_running"),
    (TimeoutError("timed out"), "unavailable"), (OSError("reset"), "unavailable"),
    (urllib.error.URLError(ConnectionResetError(54, "Connection reset by peer")), "unavailable"),
])
def test_health_says_glimmer_is_down_when_nothing_answers(monkeypatch, fail, reason):
    """Only a refused connection (nothing listens there) is not_running, which the page may call "not running"; any
    other failure is unavailable (DAT-850 A228)."""
    monkeypatch.delenv(g.URL_ENV, raising=False)
    opener, calls = _opener(fail=fail)
    h = _health(monkeypatch, opener)
    assert h["capabilities"]["glimmer"] is False
    assert h["details"]["glimmer"] == {"ready": False, "reason": reason}
    assert len(calls) == 1


def test_health_says_not_running_for_a_closed_loopback_port(monkeypatch):
    """The real socket path: nothing listens on the port, so the connection is refused."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setenv(g.URL_ENV, f"http://127.0.0.1:{port}")
    assert desk.glimmer() == {"ready": False, "reason": "not_running"}


def test_health_says_glimmer_is_down_when_another_model_answers(monkeypatch):
    monkeypatch.delenv(g.URL_ENV, raising=False)
    opener, _ = _opener(models=("some-other-model",))
    assert _health(monkeypatch, opener)["capabilities"]["glimmer"] is False


@pytest.mark.parametrize("url", ["http://10.0.0.5:8081", "http://localhost:8081", "https://127.0.0.1:8081",
                                 "http://example.com"])
def test_health_never_asks_off_loopback(monkeypatch, url):
    monkeypatch.setenv(g.URL_ENV, url)
    opener, calls = _opener()
    h = _health(monkeypatch, opener)
    assert h["capabilities"]["glimmer"] is False
    assert h["details"]["glimmer"] == {"ready": False, "reason": "not_loopback"}
    assert calls == [], "no request is made at all"


class _DripServer:
    """A loopback server that accepts, sends a status line, then one byte every `every` seconds until stopped:
    each read finishes inside GLIMMER_TIMEOUT, so only an overall deadline ends the check. It counts the
    connections whose client closed them. It stops by itself after `limit` seconds, so a check with no deadline
    fails the test instead of hanging it."""

    def __init__(self, every=0.3, limit=15.0):
        import socket
        import threading
        self.every, self.limit, self.stop = every, limit, threading.Event()
        self.accepted = self.closed_by_client = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        import time
        conns, end = [], time.monotonic() + self.limit
        while not self.stop.is_set() and time.monotonic() < end:
            try:
                conn, _ = self.sock.accept()
                conn.setblocking(False)
                conn.sendall(b"HTTP/1.1 200 OK\r\n")
                conns.append(conn)
                self.accepted += 1
            except OSError:
                pass
            for c in list(conns):
                try:
                    while c.recv(4096):  # the request; an empty read means the client closed the connection
                        pass
                    self.closed_by_client += 1
                    conns.remove(c)
                    c.close()
                    continue
                except BlockingIOError:
                    pass
                except OSError:
                    self.closed_by_client += 1
                    conns.remove(c)
                    c.close()
                    continue
                try:
                    c.sendall(b"X")
                except OSError:
                    pass
            self.stop.wait(self.every)
        for c in conns:
            c.close()
        self.sock.close()

    def close(self):
        self.stop.set()
        self.thread.join(5)


class _GlimmerServer:
    """A loopback llama-server stand-in that answers both health requests at once."""

    def __init__(self):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = {"/v1/models": {"data": [{"id": "muse-glimmer"}]},
                        "/props": {"modalities": {"vision": True}}}.get(self.path)
                raw = json.dumps(body).encode()
                self.send_response(200 if body else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _wait_for_the_glimmer_slot(seconds=5.0):
    import time
    end = time.monotonic() + seconds
    while desk._glimmer_slot.locked() and time.monotonic() < end:
        time.sleep(0.05)
    assert not desk._glimmer_slot.locked(), f"the health check thread ended within {seconds:.1f} s"


def test_health_check_thread_ends_by_its_deadline_while_the_server_still_drips(monkeypatch):
    """Codex on PR #32 @ 4c29e0c: join() bounded only the caller's wait, so a server that kept dripping held the
    thread, its socket and the slot for good. The worker's own sockets now shut at the deadline: with the server
    still dripping, the thread ends, the slot frees, the connection is closed, and the next probe can succeed."""
    import time
    assert desk.GLIMMER_DEADLINE <= 2.0
    server, good = _DripServer(every=0.3), _GlimmerServer()
    try:
        monkeypatch.setenv(g.URL_ENV, f"http://127.0.0.1:{server.port}")
        # The Glimmer part of /api/health is timed on its own: the rest of health imports modules on first call.
        start = time.monotonic()
        assert desk.glimmer() == {"ready": False, "reason": "unavailable"}
        took = time.monotonic() - start
        assert took < 2.0, f"the Glimmer check took {took:.1f} s against a slow-drip server"
        # The thread ends by itself within one socket operation of the deadline; the server is still dripping.
        _wait_for_the_glimmer_slot(desk.GLIMMER_TIMEOUT + 0.5)
        assert server.thread.is_alive() and not server.stop.is_set(), "the drip server was still running"
        end = time.monotonic() + 2.0
        while server.closed_by_client < server.accepted and time.monotonic() < end:
            time.sleep(0.05)
        assert server.accepted == 1 and server.closed_by_client == 1, "the check closed its connection"
        # A following probe, to a server that answers, succeeds.
        monkeypatch.setenv(g.URL_ENV, f"http://127.0.0.1:{good.port}")
        assert desk.glimmer() == {"ready": True, "model": "muse-glimmer", "vision": True}
        assert not desk._glimmer_slot.locked()
    finally:
        server.close()
        good.close()


def test_health_answers_at_once_while_an_earlier_check_holds_the_slot(monkeypatch):
    import time
    monkeypatch.delenv(g.URL_ENV, raising=False)
    opener, calls = _opener()
    _use(monkeypatch, opener)
    assert desk._glimmer_slot.acquire(blocking=False)
    try:
        start = time.monotonic()
        assert desk.glimmer() == {"ready": False, "reason": "unavailable"}
        assert time.monotonic() - start < 0.5
        h = TestClient(create_app(stub=False, rungs=[])).get("/api/health").json()
        assert h["details"]["glimmer"] == {"ready": False, "reason": "unavailable"}
        assert calls == [], "no new request while the slot is held"
    finally:
        desk._glimmer_slot.release()


def test_health_releases_the_slot_if_its_thread_cannot_start(monkeypatch):
    monkeypatch.delenv(g.URL_ENV, raising=False)
    opener, _ = _opener()
    _use(monkeypatch, opener)

    def refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(desk.threading.Thread, "start", refuse)
    with pytest.raises(RuntimeError):
        desk.glimmer()
    assert not desk._glimmer_slot.locked()


def test_deadline_open_stops_a_drip_by_the_deadline_on_its_own():
    """The transport itself, with no thread around it: a request to a dripping server raises by the deadline,
    and no socket read waits longer than the per-request timeout or the time left."""
    import time
    server = _DripServer(every=0.2)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server.port}/v1/models")
        start = time.monotonic()
        with pytest.raises((urllib.error.URLError, OSError)):
            with g.deadline_open(start + 0.8)(req, timeout=1.0) as resp:
                resp.read()
        took = time.monotonic() - start
        assert 0.7 < took < 1.3, f"the request ended {took:.2f} s after it started, against a 0.8 s deadline"
    finally:
        server.close()


def test_health_deadline_covers_both_requests_together(monkeypatch):
    """Each request takes less than GLIMMER_TIMEOUT, but the two together pass GLIMMER_DEADLINE."""
    import threading
    import time
    monkeypatch.delenv(g.URL_ENV, raising=False)
    monkeypatch.setattr(desk, "GLIMMER_DEADLINE", 0.5)
    release = threading.Event()
    opener, calls = _opener()

    def slow(req, timeout=None):
        release.wait(0.4)  # under GLIMMER_TIMEOUT each; 0.8 s for the two
        return opener(req, timeout=timeout)

    _use(monkeypatch, slow)
    start = time.monotonic()
    assert desk.glimmer() == {"ready": False, "reason": "unavailable"}
    assert time.monotonic() - start < 0.75, "ended by the deadline, before the two requests could finish"
    release.set()
    _wait_for_the_glimmer_slot()
    assert [u for u, _ in calls][:1] == ["http://127.0.0.1:8081/v1/models"]


# ---- 5. the deep-check timing line ------------------------------------------------------------------

def test_deep_check_timing_names_the_run_it_comes_from():
    expect = INV[INV.index("const EXPECT"):INV.index(";", INV.index("It stops itself"))]
    assert "In our 12-image test run a deep check took 53 to 126 seconds an image" in expect
    assert "yours may be quicker or slower" in expect and "It stops itself at 300 seconds." in expect
    assert "In the test run" not in INV
    # The numbers are the smoke run's own (docs/M2-INVESTIGATOR.md): 12 rows, min 52.72 s, max 126.42 s.
    m2 = (ROOT / "docs" / "M2-INVESTIGATOR.md").read_text()
    assert "over the 12 rows (min 52.72, max 126.42)" in m2


# ---- fix round @ 4e755a2 (designer check, comment 6489294102): F3, S-a, S-b ------------------------------

from secondlook.types import FailedToLoad, NotInstalled  # noqa: E402


@pytest.mark.parametrize("exc, why", [
    (NotInstalled("Video Seal weights not found"), "not installed"),
    (ImportError("no torch"), "not installed"),
    (ModuleNotFoundError("No module named 'torch'"), "not installed"),
    (FileNotFoundError("model.bin"), "not installed"),
    (FailedToLoad("sha256 differs"), "failed to load"),
    (MemoryError(), "ran out of memory"),
    (TimeoutError("read timed out"), "timed out"),
    (RuntimeError("weights not fetched"), None),       # not on the list: the page points at the server log
    (ValueError("bad"), None),
    (PermissionError("denied"), None),                 # an OSError, but not a missing file
    (SystemExit(2), None),
])
def test_each_error_type_maps_to_its_fixed_reason(exc, why):
    assert ladder.failure_reason(exc) == why


def test_the_reason_list_is_fixed_and_short():
    assert [w for _, w in ladder.FAILURE_REASONS] == ["not installed", "failed to load", "ran out of memory", "timed out"]


def _raises(exc):
    def check(path):
        raise exc
    return check


def test_run_traced_keeps_one_reason_per_failed_rung_in_order(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(canon_jpeg(tmp_path).read_bytes())
    rungs = [("watermark", _raises(NotInstalled("no weights"))), ("exif", exif_rung.check),
             ("classifier", _raises(RuntimeError("boom"))), ("c2pa", _raises(FailedToLoad("bad hash")))]
    result, ran, failed, why = ladder.run_traced(str(p), rungs=rungs)
    assert ran == ["exif"] and failed == ["watermark", "classifier", "c2pa"]
    assert why == ["not installed", None, "failed to load"]
    assert ladder.run(str(p), rungs=rungs) == result


def test_the_api_sends_the_reason_with_each_failed_check(tmp_path, monkeypatch):
    from secondlook.rungs import watermark as wm

    # The real watermark rung without its weights file: "not installed".
    monkeypatch.setenv("SECONDLOOK_VIDEOSEAL_WEIGHTS", str(tmp_path / "missing.jit"))
    client = TestClient(create_app(stub=False, rungs=[("exif", exif_rung.check), ("watermark", wm.check),
                                                      ("classifier", _raises(RuntimeError("boom")))]))
    data = canon_jpeg(tmp_path).read_bytes()
    body = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}).json()
    assert body["rungs_failed"] == ["watermark", "classifier"]
    assert body["rungs_failed_why"] == ["not installed", None]
    entry = client.post("/api/batch", files=[("files", ("a.jpg", data, "image/jpeg"))]).json()["results"][0]
    assert entry["rungs_failed"] == ["watermark", "classifier"] and entry["rungs_failed_why"] == ["not installed", None]
    ok = TestClient(create_app(stub=False, rungs=[("exif", exif_rung.check)]))
    assert ok.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}).json()["rungs_failed_why"] == []
    stub = TestClient(create_app(stub=True)).post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}).json()
    assert stub["rungs_failed"] == stub["rungs_failed_why"] == []


def test_the_watermark_rung_raises_typed_errors(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    from secondlook.rungs import watermark as wm

    img = tmp_path / "a.png"
    from PIL import Image
    Image.new("RGB", (64, 48)).save(img)
    monkeypatch.setattr(wm, "_model", None)
    monkeypatch.setenv("SECONDLOOK_VIDEOSEAL_WEIGHTS", str(tmp_path / "missing.jit"))
    with pytest.raises(NotInstalled, match="weights not found"):
        wm.check(str(img))
    junk = tmp_path / "w.jit"
    junk.write_bytes(b"not a torchscript file")
    monkeypatch.setenv("SECONDLOOK_VIDEOSEAL_WEIGHTS", str(junk))
    with pytest.raises(FailedToLoad, match="not the pinned"):
        wm.load_model()
    # The pinned hash matches, but the file is not a model: also "failed to load".
    monkeypatch.setattr(wm, "MODEL_SHA256", hashlib.sha256(junk.read_bytes()).hexdigest())
    with pytest.raises(FailedToLoad, match="would not load"):
        wm.load_model()
    assert wm._model is None


def test_the_page_shows_the_reason_or_points_at_the_server_log():
    fn = APP[APP.index("const rungs = Array.isArray(r.rungs)"):APP.index("render(r.evidence.length")]
    assert "const why = Array.isArray(r.rungs_failed_why) ? r.rungs_failed_why : [];" in fn
    assert '(RUNGS[n] || n) + (why[i] ? " (" + why[i] + ")" : "")' in fn
    assert "const unexplained = failed.filter((n, i) => !why[i]).length;" in fn
    assert '(!unexplained ? "" : unexplained < failed.length' in fn
    # Mixed reasons (design NIT on PR #32): the pointer covers only the checks with no reason of their own.
    assert '? " The cause of the rest is in the server log."' in fn
    assert ': " The cause is in the server log.")' in fn
    assert "innerHTML" not in fn


def test_the_receipt_status_line_keeps_a_readable_measure():
    assert re.search(r"#receipt-status \{[^}]*max-width: 68ch;", CSS)


def test_only_the_could_not_run_line_takes_the_warning_weight():
    assert re.search(r"\.rungs \{ color: var\(--soft\);", CSS)
    assert ".rungs .warn { color: var(--rust-text); font-weight: 600; }" in CSS
    assert re.search(r"\.rungs \.failed \{ display: block;", CSS)
    assert ".rungs.warn" not in CSS   # the whole line no longer turns rust


def test_a_receipt_that_differs_from_the_screen_is_a_warning():
    fn = APP[APP.index("async function downloadReceipt"):APP.index("async function check(")]
    assert 'receiptStatus.replaceChildren("Saved " + name + ".",' in fn
    assert 'el("span", "warn", "Its answer, \\u201c" + label + "\\u201d, differs from the one on screen' in fn
    assert "#receipt-status .warn { color: var(--rust-text); font-weight: 600; }" in CSS
    # It stays in the polite status region, so a screen reader hears it once.
    assert 'id="receipt-status" role="status" aria-live="polite"' in HTML
