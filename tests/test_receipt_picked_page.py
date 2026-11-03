"""DAT-850 (TF-722 Beat 7): the page sends the deep check's run id with the receipt, in a real page.

Runs the page in the bounded headless browser (`headless-run`, chrome-headless-shell, muted) against a local
server on 127.0.0.1 with the real ladder (EXIF rung) and a stand-in investigator. The stand-in replaces only the
model: it "picks" one whole clause of the caption and runs the real S6 lookup on it, as the investigator's
lookup_claim tool does, against a one-review index. The server keeps its own record of the picks; the page sends
only the run id (`deep_run`), never the words. The page then:

1. shows no picked-words line and sends no run id before the deep check;
2. after the deep check, shows the one fixed line before the buttons and sends the run id; the receipt keeps the
   fact-check and the answer on screen;
3. a receipt request whose caption is changed in transit gets a receipt without picks, and the page shows the
   server's plain reason, not an error;
4. a second deep check on the same answer whose lookup fails clears the line and the run id (a reviewer N1);
5. a deep-check event with no run id, as a recorded run or any other client would send, adds nothing;
6. a new check with a caption full of markup drops the picks; after its deep check the line is the fixed text,
   with no element in it, nothing runs and nothing is fetched, and the receipt records the clause as text.

Every request off 127.0.0.1 is refused and counted. SECONDLOOK_SHOTS_DIR=<dir> also saves the shots at 1280 and
390 px wide.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests.test_w11_receipt_page import _free_port, _headless_run, _jpeg

ROOT = Path(__file__).resolve().parent.parent
CAPTION = ("BREAKING: Explosion near the Pentagon, it is under attack!! Photo shows the U.S. "
           "Pentagon is under attack. Stay safe everyone")
PICK = "Photo shows the U.S. Pentagon is under attack"
HOSTILE_PICK = 'Mayor <b>Smith</b> <img src=x onerror="window.__pwned=1"> opened the bridge'
HOSTILE = HOSTILE_PICK + ". Stay calm"
PANEL_LINE = "The receipt also looks up the words Muse Glimmer picked from your caption."

SERVER = r"""
import sys
from pathlib import Path

import uvicorn

from secondlook import glimmer as g
from secondlook import ladder
from secondlook.investigator import CLAIM_CARDS, DeskNote, Step
from secondlook.rungs import exif
from secondlook.verbatim import normalise, split_clauses
from secondlook.web.app import create_app

PICK = sys.argv[2]
RUNGS = [("exif", exif.check)]
calls = {}


def investigator(path, caption):
    # Stands in for the model only: picks one whole clause of the caption, then the real S6 lookup. The second
    # deep check of the Pentagon caption finds the index unreadable, as a failed lookup does.
    r = ladder.run(path, rungs=RUNGS)
    clauses = [c.text for c in split_clauses(normalise(caption or ""))]
    calls[caption] = calls.get(caption, 0) + 1
    steps = [Step(1, "run_ladder", {"path": Path(path).name}, "Ran the file checks.", True, "model", 0.1, "run_ladder")]
    cards = []
    pick = PICK if PICK in clauses else next((c for c in clauses if "<b>" in c), None)
    if pick == PICK and calls[caption] == 2:
        steps.append(Step(2, "lookup_claim", {"text": PICK, "from": "caption"}, "Could not search.", True, "model",
                          0.2, "lookup_claim.unavailable", "model"))
    elif pick:
        cards = g.lookup_candidate(g.CandidateClaim(pick, "caption"), None, CLAIM_CARDS)["cards"]
        steps.append(Step(2, "lookup_claim", {"text": pick, "from": "caption"}, "Found a published fact-check.",
                          True, "model", 0.2, "lookup_claim.found", "model"))
    answer, summary = ladder.decide([*r.evidence, *cards])
    return DeskNote(file=Path(path).name, answer=answer, summary=summary, evidence=[*r.evidence, *cards],
                    steps=steps, explanation=summary, version="test")


uvicorn.run(create_app(stub=False, rungs=RUNGS, investigator=investigator), host="127.0.0.1",
            port=int(sys.argv[1]), log_level="warning")
"""

TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const shots = __SHOTS__;
  const caption = __CAPTION__, hostile = __HOSTILE__;
  const offHost = [], receipts = [], fetched = [];
  let tamper = false;
  await page.route("**/*", async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    fetched.push(url.pathname);
    if (url.pathname === "/api/health") {   // the fake investigator stands in for a Glimmer server that answers
      const res = await route.fetch();
      const h = await res.json();
      h.capabilities.glimmer = true;
      return route.fulfill({ response: res, json: h });
    }
    if (/\/receipt$/.test(url.pathname)) {
      let buf = req.postDataBuffer() || Buffer.alloc(0), body = buf.toString("latin1");
      const field = (name) => [...body.matchAll(new RegExp('name="' + name + '"\r\n\r\n([^\r]*)\r\n', "g"))]
        .map(m => Buffer.from(m[1], "latin1").toString("utf8"));
      const deepRun = field("deep_run"), picks = field("picked_claims"), tampered = tamper;
      if (tamper) {  // 3. the caption changed in transit: the server's record no longer matches
        tamper = false;
        body = body.replace(caption, caption + " Extra");
        buf = Buffer.from(body, "latin1");
      }
      const resp = await route.fetch(tampered ? { postData: buf } : undefined);
      const text = await resp.text();
      receipts.push({ deepRun, picks, tampered, status: resp.status(), body: text,
                      picksHeader: resp.headers()["x-secondlook-picks"] || null,
                      reason: resp.headers()["x-secondlook-picks-reason"] || null });
      return route.fulfill({ response: resp, body: text });
    }
    return route.continue();
  });
  const saved = async (n) => {
    const end = Date.now() + 30000;
    while (receipts.length < n && Date.now() < end) await page.waitForTimeout(100);
    await page.waitForFunction(() => /^Saved /.test(document.getElementById("receipt-status").textContent),
                               null, { timeout: 30000 });
  };
  const download = async (n) => {
    await page.click("#receipt-json");
    await saved(n);
    return page.locator("#receipt-status").textContent();
  };
  const picked = () => page.locator("#receipt-picked").evaluate(e => ({
    hidden: e.hidden, text: e.textContent, children: e.children.length, html: e.innerHTML }));
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(base + "/");
  await page.fill("#caption", caption);
  await page.setInputFiles("#file", "__FIRST__");
  await page.locator("#receipt-json").waitFor({ state: "visible" });
  const answer = await page.locator("#answer").textContent();

  // 1. No deep check yet: no line, no run id sent.
  const before = await picked();
  await download(1);

  // 2. The deep check finds the fact-check with a clause it picked; the fixed line shows before the buttons.
  await page.click("#inv-go");
  await page.locator("#receipt-picked").waitFor({ state: "visible", timeout: 60000 });
  const after = await picked();
  const order = await page.evaluate(() => {
    const p = document.getElementById("receipt-picked"), b = document.getElementById("receipt-json");
    const pr = p.getBoundingClientRect(), br = b.getBoundingClientRect();
    return { lineAbove: pr.bottom <= br.top + 1, domBefore: !!(p.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) };
  });
  const buttons = await page.evaluate(() => [...document.querySelectorAll("#receipt button")]
    .filter(b => !b.hidden).map(b => b.getBoundingClientRect().height));
  // The strip is held until /api/health has answered with its CC BY credit (PR #43), so wait for it, up to 15 s
  // (health waits 6 s, retries once after 0.4 s, then 6 s more). A strip that never shows still reads false below.
  await page.locator("#factcheck").waitFor({ state: "visible", timeout: 15000 }).catch(() => {});
  const factcheck = await page.locator("#factcheck").evaluate(e => !e.hidden);
  const status2 = await download(2);
  if (shots) {
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: width > 600 ? 900 : 844 });
      await page.waitForTimeout(300);
      await page.locator("#factcheck").scrollIntoViewIfNeeded();
      await page.locator("#result").screenshot({ path: shots + "/receipt-picked-" + width + "-result.png" });
      await page.locator("#receipt").screenshot({ path: shots + "/receipt-picked-" + width + "-panel.png" });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
  }

  // 3. The caption is changed in transit: a receipt without picks, and the server's reason on the page.
  tamper = true;
  const status3 = await download(3);
  const status3Error = await page.locator("#receipt-status").evaluate(e => e.className);

  // 4. A second deep check on the same answer whose lookup fails: the line and the run id are cleared.
  await page.click("#inv-go");
  await page.locator("#receipt-picked").waitFor({ state: "hidden", timeout: 60000 });
  await page.waitForFunction(() => !document.getElementById("investigate").hasAttribute("aria-busy"), null,
                             { timeout: 60000 });
  const rerun = await picked();
  await download(4);

  // 5. A deep-check event with no run id (a recorded run, or any other client): nothing is added.
  await page.evaluate((cap) => {
    const step = { n: 1, tool: "lookup_claim", ok: true, template: "lookup_claim.found",
                   arguments: { text: "Photo shows the U.S. Pentagon is under attack", from: "caption" } };
    document.dispatchEvent(new CustomEvent("secondlook:deep-evidence", { detail: {
      evidence: [], steps: [step], file: document.getElementById("file").files[0], caption: cap, factcheck: false } }));
  }, caption);
  const recorded = await picked();
  await download(5);

  // 6. A new check with markup in its caption: the picks go; after its deep check the line is the fixed text.
  // The caption box is folded away under an answer; set it as typing would.
  await page.evaluate((v) => {
    const c = document.getElementById("caption");
    c.value = v;
    c.dispatchEvent(new Event("input", { bubbles: true }));
  }, hostile);
  await page.setInputFiles("#file", "__SECOND__");
  await page.waitForFunction(() => /concern\.jpg/.test(document.getElementById("preview").alt)
                                   && !document.getElementById("result").hasAttribute("aria-busy"));
  const replaced = await picked();
  await page.click("#inv-go");
  await page.locator("#receipt-picked").waitFor({ state: "visible", timeout: 60000 });
  const hostileLine = await picked();
  await download(6);
  const pwned = await page.evaluate(() => window.__pwned === 1 || !!document.querySelector("#receipt img, #receipt b"));
  return { answer, before, after, order, buttons, factcheck, status2, status3, status3Error, rerun, recorded,
           replaced, hostileLine, pwned, offHost, fetchedX: fetched.filter(p => p === "/x"),
           receipts: receipts.map(r => ({ ...r, body: r.status === 200 ? JSON.parse(r.body) : r.body })) };
}
"""


@pytest.fixture
def picked_server(tmp_path):
    feed = {"dataFeedElement": [{"item": [{
        "claimReviewed": "Photo shows the U.S. Pentagon “is under attack.”",
        "url": "https://www.politifact.com/factchecks/2023/may/25/x/",
        "reviewRating": {"alternateName": "Pants on Fire"},
        "author": {"name": "PolitiFact", "url": "https://www.politifact.com"},
        "datePublished": "2023-05-25",
    }]}]}
    from secondlook.rungs.claim import build_index

    (tmp_path / "feed.json").write_text(json.dumps(feed))
    build_index(tmp_path / "feed.json", tmp_path / "claims.sqlite")
    (tmp_path / "server.py").write_text(SERVER)
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT), "SECONDLOOK_CLAIMS_DB": str(tmp_path / "claims.sqlite")}
    env.pop("SECONDLOOK_GLIMMER_URL", None)
    log = open(tmp_path / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, str(tmp_path / "server.py"), str(port), PICK], cwd=ROOT, env=env,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        end = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > end:
                    pytest.fail("server did not start: " + (tmp_path / "server.log").read_text()[-2000:])
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


def test_the_page_sends_the_deep_checks_run_id_with_the_receipt(picked_server, tmp_path):
    runner = _headless_run()
    if not runner:
        pytest.skip("headless-run (chrome-headless-shell) is not installed here")
    shots = os.environ.get("SECONDLOOK_SHOTS_DIR")
    shot_dir = tmp_path / "shots"
    shot_dir.mkdir()
    task = tmp_path / "picked.mjs"
    task.write_text(TASK.replace("__PORT__", str(picked_server))
                    .replace("__SHOTS__", json.dumps(str(shot_dir)) if shots else "null")
                    .replace("__CAPTION__", json.dumps(CAPTION))
                    .replace("__HOSTILE__", json.dumps(HOSTILE))
                    .replace("__FIRST__", _jpeg(tmp_path / "pentagon.jpg", (120, 120, 120)))
                    .replace("__SECOND__", _jpeg(tmp_path / "concern.jpg", (160, 120, 90))))
    end = time.monotonic() + 180
    while True:
        run = subprocess.run([runner, "--job", "secondlook-receipt-picked", "--owner-task", "DAT-850",
                              "--task", str(task), "--timeout", "170", "--navigation-timeout", "60000"],
                             capture_output=True, text=True, timeout=230)
        lines = [ln for ln in run.stdout.splitlines() if ln.startswith("{")]
        receipt = json.loads(lines[-1]) if lines else None
        said = run.stderr + str((receipt or {}).get("error"))
        if "capacity" in said and "busy" in said and time.monotonic() < end:
            time.sleep(5)  # both host browser slots are taken by other jobs
            continue
        break
    if "capacity" in said and "busy" in said:
        pytest.skip("the host's headless browser slots stayed busy for 3 minutes")
    assert receipt and receipt["status"] == "completed", (receipt, run.stderr[-3000:])
    r = receipt["result"]

    assert r["offHost"] == [], "no request leaves 127.0.0.1"
    one, two, three, four, five, six = r["receipts"]
    assert all(x["picks"] == [] for x in r["receipts"]), "the page never sends the words"
    # 1. No deep check: no line, no run id, the receipt records only the caption.
    assert r["before"]["hidden"] is True and one["deepRun"] == []
    assert one["status"] == 200 and one["body"]["inputs"] == {"caption": CAPTION}
    assert "claim" not in [c["rung"] for c in one["body"]["evidence"]]
    # 2. After the deep check: the one fixed line, before the buttons; the run id goes with the request.
    assert r["after"] == {"hidden": False, "text": PANEL_LINE, "children": 0, "html": PANEL_LINE}
    assert r["factcheck"] is True
    assert r["order"] == {"lineAbove": True, "domBefore": True}
    assert r["buttons"] and min(r["buttons"]) >= 44, r["buttons"]
    assert len(two["deepRun"]) == 1 and len(two["deepRun"][0]) >= 20 and two["status"] == 200
    rc = two["body"]
    assert rc["inputs"]["picked_claims"]["clauses"] == [PICK] and rc["inputs"]["picked_claims"]["lookup"] == "found"
    assert two["picksHeader"] == "added"
    assert rc["evidence"][-1]["rung"] == "claim" and rc["evidence"][-1]["raw"]["rating"] == "Pants on Fire"
    assert rc["answer"] == one["body"]["answer"], "the answer stays what the page showed"
    assert r["status2"].startswith("Saved ") and "differs" not in r["status2"]
    # 3. The caption changed in transit: a receipt (200) without picks, and the reason on the page.
    assert three["tampered"] is True and three["deepRun"] == two["deepRun"] and three["status"] == 200
    assert "picked_claims" not in three["body"]["inputs"]
    assert three["picksHeader"] == "not-added" and "different caption" in three["reason"]
    assert r["status3"].startswith("Saved ") and three["reason"] in r["status3"] and r["status3Error"] != "error"
    # 4. A second deep check whose lookup failed: line hidden, nothing sent (N1).
    assert r["rerun"]["hidden"] is True and four["deepRun"] == [] and "picked_claims" not in four["body"]["inputs"]
    # 5. An event with no run id adds nothing.
    assert r["recorded"]["hidden"] is True and five["deepRun"] == [] and "picked_claims" not in five["body"]["inputs"]
    # 6. Markup in the caption: picks dropped on the new check; the line is fixed text; the clause is recorded as text.
    assert r["replaced"]["hidden"] is True
    assert r["hostileLine"] == {"hidden": False, "text": PANEL_LINE, "children": 0, "html": PANEL_LINE}
    assert r["pwned"] is False and r["fetchedX"] == []
    assert len(six["deepRun"]) == 1 and six["deepRun"] != two["deepRun"]
    assert six["body"]["inputs"]["picked_claims"]["clauses"] == [HOSTILE_PICK]
    assert six["body"]["inputs"]["picked_claims"]["lookup"] == "none"
    assert six["body"]["inputs"]["caption"] == HOSTILE
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        for f in sorted(shot_dir.glob("*.png")):
            shutil.copy(f, Path(shots) / f.name)
