"""W11 stage 2 fix round 2 (DAT-850): the receipt buttons in a real page, not a string match on app.js.

a reviewer (PR #32, 4e755a2) found that a receipt request still in flight when a newer check starts left
`aria-busy` on #receipt for good: the stale request's `finally` skips the clear, and the busy guard at the top
of downloadReceipt then swallowed every later click. This test runs the page in the bounded headless browser
(`headless-run`, chrome-headless-shell, muted) against the stub server on 127.0.0.1: hold the first receipt
request, start a new check, let the old request return, then click again and count the requests that reach
the server. Every request off 127.0.0.1 is refused and counted.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [];
  const receipts = [];
  let release;
  const held = new Promise(r => { release = r; });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (/\/receipt$/.test(url.pathname)) {
      receipts.push(url.pathname + url.search);
      if (receipts.length === 1) await held;  // the first receipt is slow
    }
    return route.continue();
  });
  const answer = () => page.locator("#answer").textContent();
  await page.goto(base + "/");
  await page.setInputFiles("#file", "__FIRST__");
  await page.locator("#receipt-json").waitFor({ state: "visible" });
  const first = await answer();

  // 1. Ask for the receipt; the server does not answer yet.
  await page.click("#receipt-json");
  await page.waitForFunction(() => document.getElementById("receipt").getAttribute("aria-busy") === "true");
  // 2. A newer check supersedes it.
  await page.setInputFiles("#file", "__SECOND__");
  await page.waitForFunction(old => {
    const a = document.getElementById("answer").textContent;
    return a && a !== old && !document.getElementById("result").hasAttribute("aria-busy");
  }, first);
  const second = await answer();
  // 3. The old receipt comes back now, after the page has moved on.
  const stale = page.waitForResponse(r => /\/receipt/.test(r.url()));
  release();
  await stale;
  await page.waitForTimeout(300);  // let the page's fetch and its finally block run
  const busyAfterStale = await page.locator("#receipt").getAttribute("aria-busy");
  // 4. The receipt buttons must work for the new answer.
  await page.click("#receipt-json");
  const deadline = Date.now() + 5000;
  while (receipts.length < 2 && Date.now() < deadline) await page.waitForTimeout(100);
  await page.waitForFunction(() => /Saved /.test(document.getElementById("receipt-status").textContent),
                             null, { timeout: 10000 }).catch(() => {});
  return { first, second, busyAfterStale, receipts, offHost,
           status: await page.locator("#receipt-status").textContent(),
           busyAtEnd: await page.locator("#receipt").getAttribute("aria-busy") };
}
"""


# Round 3 (Codex on PR #32 @ 4c29e0c): a caption over 2,000 characters refuses the next file without sending it.
# That path superseded the answer but not a receipt still in flight, which could then save the previous image's
# receipt. Blob downloads are counted at URL.createObjectURL (the preview's File objects are not counted).
OVERLONG_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [];
  const receipts = [];
  let release;
  const held = new Promise(r => { release = r; });
  await page.addInitScript(() => {
    window.__receiptBlobs = 0;
    const make = URL.createObjectURL.bind(URL);
    URL.createObjectURL = b => { if (!(b instanceof File)) window.__receiptBlobs++; return make(b); };
  });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (/\/receipt$/.test(url.pathname)) {
      receipts.push(url.pathname + url.search);
      if (receipts.length === 1) await held;  // the first receipt is slow
    }
    return route.continue();
  });
  await page.goto(base + "/");
  await page.setInputFiles("#file", "__FIRST__");
  await page.locator("#receipt-json").waitFor({ state: "visible" });

  // 1. Ask for the first image's receipt; the server does not answer yet.
  await page.click("#receipt-json");
  await page.waitForFunction(() => document.getElementById("receipt").getAttribute("aria-busy") === "true");
  // 2. Open the inputs, paste an overlong caption and choose another file: the page refuses it unsent.
  await page.click("#again-go");
  await page.fill("#caption", "x".repeat(2001));
  await page.setInputFiles("#file", "__SECOND__");
  await page.waitForFunction(() => /over 2,000 characters/.test(document.getElementById("status").textContent));
  const refused = { status: await page.locator("#status").textContent(),
                    resultHidden: await page.locator("#result").evaluate(e => e.hidden),
                    busy: await page.locator("#receipt").getAttribute("aria-busy") };
  // 3. The first receipt comes back now, after the refusal superseded its answer.
  const stale = page.waitForResponse(r => /\/receipt/.test(r.url()));
  release();
  await stale;
  await page.waitForTimeout(500);  // let the page's fetch, its blob and its finally block run
  const afterStale = { blobs: await page.evaluate(() => window.__receiptBlobs),
                       status: await page.locator("#receipt-status").textContent(),
                       busy: await page.locator("#receipt").getAttribute("aria-busy") };
  // 4. Shorten the caption and check the same file again; its receipt buttons work.
  await page.fill("#caption", "");
  await page.click("#caption-again");
  await page.locator("#receipt-json").waitFor({ state: "visible" });
  await page.click("#receipt-json");
  await page.waitForFunction(() => /Saved /.test(document.getElementById("receipt-status").textContent),
                             null, { timeout: 10000 }).catch(() => {});
  return { refused, afterStale, receipts, offHost,
           blobsAtEnd: await page.evaluate(() => window.__receiptBlobs),
           status: await page.locator("#receipt-status").textContent(),
           busyAtEnd: await page.locator("#receipt").getAttribute("aria-busy") };
}
"""


def _headless_run() -> str | None:
    found = os.environ.get("SECONDLOOK_HEADLESS_RUN") or shutil.which("headless-run")
    home = Path.home() / "bin" / "headless-run"
    return found or (str(home) if home.is_file() else None)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _jpeg(path: Path, colour) -> str:
    from PIL import Image
    Image.new("RGB", (64, 48), colour).save(path, "JPEG")
    return str(path)


@pytest.fixture
def stub_server(tmp_path):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("SECONDLOOK_GLIMMER_URL", None)
    log = open(tmp_path / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "secondlook.web", "--stub", "--port", str(port)], cwd=ROOT,
                            env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        end = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > end:
                    pytest.fail("stub server did not start: " + (tmp_path / "server.log").read_text()[-2000:])
                time.sleep(0.2)
        yield port
    finally:
        os.killpg(proc.pid, signal.SIGTERM)  # the server's own process group, started above
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(10)
        log.close()


def _run_page(task_text: str, job: str, port: int, tmp_path: Path) -> dict:
    """Runs one page task in headless-run against the stub server, waiting up to 3 minutes for a free host slot."""
    runner = _headless_run()
    if not runner:
        pytest.skip("headless-run (chrome-headless-shell) is not installed here")
    # The stub picks its answer from the file name: "camera" gives signed_camera, "concern" gives concerns.
    task = tmp_path / f"{job}.mjs"
    task.write_text(task_text.replace("__PORT__", str(port))
                    .replace("__FIRST__", _jpeg(tmp_path / "camera.jpg", (90, 120, 160)))
                    .replace("__SECOND__", _jpeg(tmp_path / "concern.jpg", (160, 120, 90))))

    def busy(run, receipt) -> bool:  # both host browser slots are taken by other jobs
        said = run.stderr + str((receipt or {}).get("error"))
        return "capacity" in said and "busy" in said

    end = time.monotonic() + 180
    while True:
        run = subprocess.run([runner, "--job", job, "--owner-task", "DAT-850",
                              "--task", str(task), "--timeout", "150",
                              "--navigation-timeout", "60000"],  # a loaded host is slow, not wrong
                             capture_output=True, text=True, timeout=210)
        lines = [ln for ln in run.stdout.splitlines() if ln.startswith("{")]
        receipt = json.loads(lines[-1]) if lines else None
        if busy(run, receipt) and time.monotonic() < end:
            time.sleep(5)  # wait for a slot
            continue
        break
    if busy(run, receipt):
        pytest.skip("the host's headless browser slots stayed busy for 3 minutes")
    assert receipt, f"headless-run gave no receipt: {run.stderr[-2000:]}"
    assert receipt["status"] == "completed", (receipt.get("status"), receipt.get("error"), run.stderr[-3000:])
    return receipt["result"]


def test_receipt_buttons_work_again_after_a_newer_check_drops_a_pending_receipt(stub_server, tmp_path):
    r = _run_page(TASK, "secondlook-receipt-page", stub_server, tmp_path)
    assert r["first"] != r["second"], "the second check replaced the first answer"
    assert r["offHost"] == [], "no request leaves 127.0.0.1"
    assert len(r["receipts"]) == 2, f"the second click sent a receipt request: {r['receipts']}"
    assert r["busyAfterStale"] is None, "the newer check cleared the busy flag the dropped receipt had set"
    assert all(u == "/api/receipt?format=json" for u in r["receipts"])
    assert r["status"].startswith("Saved "), r["status"]
    assert r["busyAtEnd"] is None


def test_an_overlong_caption_drops_a_pending_receipt(stub_server, tmp_path):
    r = _run_page(OVERLONG_TASK, "secondlook-receipt-overlong", stub_server, tmp_path)
    assert r["offHost"] == [], "no request leaves 127.0.0.1"
    assert "over 2,000 characters" in r["refused"]["status"] and r["refused"]["resultHidden"] is True
    assert r["refused"]["busy"] is None, "the refusal cleared the busy flag of the receipt it dropped"
    # The dropped receipt came back but saved nothing: no blob, no "Saved" line for the previous image.
    assert r["afterStale"]["blobs"] == 0, "the previous image's receipt was not downloaded"
    assert r["afterStale"]["status"] == "" and r["afterStale"]["busy"] is None
    # Checked again with the caption shortened, the same file's receipt saves.
    assert len(r["receipts"]) == 2 and all(u == "/api/receipt?format=json" for u in r["receipts"]), r["receipts"]
    assert r["status"].startswith("Saved "), r["status"]
    assert r["blobsAtEnd"] == 1 and r["busyAtEnd"] is None
