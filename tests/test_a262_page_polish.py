"""A262 lane A (DAT-850): persona polish on the page, from the newsroom-desk and claims-handler runs of 31 Oct.

One test per fix, each failing with its fix removed:
1. a trusted C2PA card leads with one sentence; the trust list's source URL and sha256 sit under "Source", closed;
2. the deep check box with no model and no recording says plainly the deep check is off, with no README pointer,
   no "lines below" promise and no empty padding; with a live model, or a recording on offer, the "lines below"
   sentence shows (A268);
3. the "not installed" lines say what each part is for and what turning it on needs, with no engine names;
4. the stub banner is hidden before any answer while /api/health is unknown, shows once health says the stub runs,
   and shows whenever a stub answer renders, with health held, failing twice or timing out (A270, Codex FAIL on
   PR #55); a real answer never shows it, even after a stub answer or a "stub" health, early or late (A273); the batch
   page follows the rows on screen the same way, and "Check more" clears it (A278); when a refused caption or a
   failed check hides the answer, the banner follows health again (A281);
5. an AI-made C2PA card adds that it does not show how much of the picture was changed;
6. a batch row's Receipt link never wraps alone behind a "·": it starts its own line under the facts, clear of
   their text, at 1440, 800, 740 and 390, and keeps it by its own rule with the facts as inline text (A268);
7. after a press the receipt buttons show a focus ring, not a solid fill;
8. the trust list's Source is a link only for an http(s) URL, and a keyboard reaches and follows it (A270).

The page tests run in headless-run (chrome-headless-shell, muted) against the stub server on 127.0.0.1. The answers
are the task's own (route.fulfill), shaped as the ladder's, with the C2PA rung's own sentences for samples 02 and 05,
so no weights, samples or trust lists are needed. Every request off 127.0.0.1 is refused and counted.
"""
from __future__ import annotations

import json
import os
import re
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
STATIC = ROOT / "secondlook" / "web" / "static"
APP = (STATIC / "app.js").read_text()
DESK = (STATIC / "desk.js").read_text()
INV = (STATIC / "investigate.js").read_text()
HTML = (STATIC / "index.html").read_text()

# The C2PA rung's sentences for samples 02 (official list) and 05 (interim list), as secondlook/rungs/c2pa.py
# writes them (run on 4893292).
CAMERA_FINDING = ("C2PA manifest signed by Google LLC on 2025-12-30 via Google C2PA SDK for Android "
                  "824699194:839895461; it declares a camera capture (computationalCapture). The signer is on the "
                  "C2PA Trust List (2026-10-17).")
CAMERA_HEAD = "C2PA manifest signed by Google LLC on 2025-12-30; it declares a camera capture (computationalCapture)."
CAMERA_LIMIT = "That the scene is as shown or the caption is true; a signed capture can still be staged or mislabelled."
CAMERA_PROVES = ("The signer recorded a device capture. The signer chains to that list and the file has not changed "
                 "since it was signed.")
LIST_URL = ("https://raw.githubusercontent.com/c2pa-org/conformance-public/47009eedfe6721edd0ce3e69b89c1101cd6f03e5/"
            "trust-list/C2PA-TRUST-LIST.pem")
LIST_SHA = "b3949271a07b007e92ef1d18050b55388f5e0d2686a16fd864176c48421af8ae"
AI_FINDING = ("C2PA manifest signed by Adobe Inc. on 2024-12-14 via Adobe_Photoshop/26.1.0 adobe_c2pa/0.12.2 "
              "c2pa-rs/0.32.5; it declares it was made with generative AI (compositeWithTrainedAlgorithmicMedia). The "
              "signer is on the C2PA interim trust list, which C2PA describes as temporary.")
AI_HEAD = ("C2PA manifest signed by Adobe Inc. on 2024-12-14; it declares it was made with generative AI "
           "(compositeWithTrainedAlgorithmicMedia).")
AI_LIMIT = "Who made it or why; only that the signing tool labelled it AI-made."
AI_EXTENT = "It also does not show how much of the picture was changed."
# Not an http(s) URL, so the Source row must stay text (A270).
ODD_SOURCE = "javascript:alert(1)"
LOCAL_OFF = "The deep check is off on this computer: its model is not running here. The answer above comes from the " \
            "file checks alone."


def _answer(answer: str, card: dict) -> dict:
    return {"answer": answer, "summary": "Summary.", "version": "0" * 40, "engine": "ladder", "rungs": ["c2pa"],
            "rungs_failed": [], "rungs_failed_why": [], "concern_cards": [], "demo": False, "sample": None,
            "explanation": None, "evidence": [card]}


CAMERA = _answer("signed_camera", {
    "rung": "c2pa", "strength": "signed", "finding": CAMERA_FINDING, "proves": CAMERA_PROVES,
    "does_not_prove": CAMERA_LIMIT,
    "raw": {"valid": True, "trusted": True, "trusted_by": "official", "source_class": "camera",
            "claim_generator": "Google C2PA SDK for Android 824699194:839895461",
            "trust_list": {"name": "C2PA Trust List", "id": "official", "date": "2026-10-17", "source": LIST_URL,
                           "sha256": LIST_SHA, "loaded": True}}})
AI = _answer("signed_ai", {
    "rung": "c2pa", "strength": "signed", "finding": AI_FINDING,
    "proves": "The signer recorded that this content came from an AI generator. The signer chains to that list and "
              "the file has not changed since it was signed.",
    "does_not_prove": AI_LIMIT,
    "raw": {"valid": True, "trusted": True, "trusted_by": "interim", "source_class": "ai",
            "claim_generator": "Adobe_Photoshop/26.1.0 adobe_c2pa/0.12.2 c2pa-rs/0.32.5",
            "trust_list": {"name": "C2PA interim trust list", "id": "interim", "date": "2026-10-28",
                           "source": "https://contentcredentials.org/trust/", "loaded": True}}})
ODD = json.loads(json.dumps(CAMERA))
ODD["evidence"][0]["raw"]["trust_list"]["source"] = ODD_SOURCE

PAGE_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], opened = [], answers = { "camera.jpg": __CAMERA__, "ai.jpg": __AI__, "odd.jpg": __ODD__ };
  const real = await (await page.request.get(base + "/api/health", { timeout: 60000 })).json();
  const ladder = { ...real, engine: "ladder",
                   capabilities: { ...real.capabilities, engine: true, glimmer: false, recorded: false,
                                   claims_index: false, voice: false, receipt: false },
                   details: { ...real.details, glimmer: { ready: false, reason: "not_running" } } };
  // health: hold (until release), stub (the server's own), fail, timeout (never answered), ladder.
  // checks: "server" (the stub server's canned answer), "ladder" (the task's own answers, by file name) or "fail"
  // (HTTP 500 with no detail).
  let mode = "hold", checks = "server", release, healthAsked = 0, healthServed = 0, stubServed = 0;
  let held = new Promise(r => { release = r; });
  // The context's route, so the tab the Source link opens is caught too: its one URL is recorded, not fetched.
  await page.context().route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.href === "__LIST_URL__") { opened.push(url.href); return route.abort(); }
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health") {
      healthAsked++;
      if (mode === "hold") { await held; healthServed++; return route.fulfill({ json: real }); }
      if (mode === "stub") { await route.fulfill({ json: real }); stubServed++; return; }
      if (mode === "fail") return route.abort();
      if (mode === "timeout") return;   // never answered: app.js gives up on each ask after 6 s
      return route.fulfill({ json: ladder });
    }
    if (url.pathname === "/api/check" && checks === "ladder") {
      const body = (route.request().postDataBuffer() || Buffer.alloc(0)).toString("latin1");
      const name = /filename="([^"]+)"/.exec(body)[1];
      return route.fulfill({ json: answers[name] });
    }
    if (url.pathname === "/api/check" && checks === "fail") return route.fulfill({ status: 500, json: {} });
    return route.continue();
  });
  const vis = sel => page.locator(sel).isVisible();
  const out = { offHost };
  await page.setViewportSize({ width: 1440, height: 900 });

  // An answer for the file, then whether the banner shows and which engine the answer names. It waits for this
  // file's own answer (app.js's "secondlook:result" event, sent once show() has run), so a second answer on the
  // same page is never read from the first one still on screen.
  const answerFor = async file => {
    const seen = await page.evaluate(() => window.__answers || 0);
    await page.setInputFiles("#file", file);
    await page.waitForFunction(n => (window.__answers || 0) > n, seen, { timeout: 30000 });
    return { banner: await vis("#stub"), meta: await page.locator("#meta").textContent() };
  };
  await page.addInitScript(() => {
    document.addEventListener("secondlook:result", () => { window.__answers = (window.__answers || 0) + 1; });
  });
  // A fresh page whose health says the stub runs, then whether the banner shows. It waits for health's reply first,
  // then up to 10 s for the banner, so a page that ignores health fails the banner assertion instead of stopping the
  // whole task on a wait (A281).
  const gotoStubHealth = async () => {
    mode = "stub";
    stubServed = 0;
    await page.goto(base + "/");
    const end = Date.now() + 20000;
    while (!stubServed && Date.now() < end) await page.waitForTimeout(100);
    return page.locator("#stub").waitFor({ state: "visible", timeout: 10000 }).then(() => true, () => false);
  };
  // A281 (a reviewer A279 on PR #55): a stub answer is on screen, then a check that hides the result: one never
  // sent (caption over 2,000 characters, app.js runFile's answerGone) and one that fails (HTTP 500, check's catch).
  // With no answer on screen the banner follows health again. Files alternate, so each pick is a change.
  const statusIs = re => page.waitForFunction(r => new RegExp(r).test(document.getElementById("status").textContent),
                                              re.source, { timeout: 30000 });
  const answerGoes = async () => {
    const r = {};
    checks = "server";
    r.stub = await answerFor("__CAMERA_FILE__");
    if (await vis("#again-go")) await page.click("#again-go");
    await page.fill("#caption", "x".repeat(2001));
    await page.setInputFiles("#file", "__AI_FILE__");
    await statusIs(/^The caption is over 2,000 characters, so /);
    r.caption = { banner: await vis("#stub"), result: await vis("#result") };
    await page.fill("#caption", "");
    r.stubAgain = await answerFor("__CAMERA_FILE__");
    checks = "fail";
    await page.setInputFiles("#file", "__AI_FILE__");
    await statusIs(/^The check failed \(HTTP 500\)\.$/);
    r.failed = { banner: await vis("#stub"), result: await vis("#result") };
    checks = "server";
    return r;
  };

  // 4. The stub banner. Before any answer it stays hidden while health is unknown (no flash on the ladder engine);
  // once a stub answer renders it shows, whatever health said or failed to say (A270).
  await page.goto(base + "/");
  await page.waitForTimeout(600);
  out.stubWhileHeld = await vis("#stub");
  out.stubAnswerHeld = await answerFor("__CAMERA_FILE__");   // health still held
  release();
  await page.waitForTimeout(800);
  out.stubAnswerHeldThenStub = await vis("#stub");   // health's late "stub" keeps it shown
  out.stubOnStub = await gotoStubHealth();
  mode = "fail";
  healthAsked = 0;
  await page.goto(base + "/");
  await page.waitForTimeout(2500);   // app.js asks again once after 400 ms
  out.failAsks = healthAsked;
  out.stubFailedBeforeAnswer = await vis("#stub");
  out.stubAnswerFailed = await answerFor("__CAMERA_FILE__");
  mode = "timeout";
  healthAsked = 0;
  await page.goto(base + "/");
  await page.waitForTimeout(13500);   // two asks of 6 s each, 400 ms apart
  out.timeoutAsks = healthAsked;
  out.stubTimedOutBeforeAnswer = await vis("#stub");
  out.stubAnswerTimedOut = await answerFor("__CAMERA_FILE__");
  // A273 (Codex FAIL on PR #55 @ 4d1d3ba): the banner follows the answer on screen, with no latch. Health fails
  // throughout, so only the answer can show or hide it: a stub answer, a real one in its place, a stub one again.
  mode = "fail";
  await page.goto(base + "/");
  await page.waitForTimeout(2500);
  checks = "server";
  out.stubThenReal = { stub: await answerFor("__CAMERA_FILE__") };
  checks = "ladder";
  out.stubThenReal.real = await answerFor("__AI_FILE__");
  checks = "server";
  out.stubThenReal.stubAgain = await answerFor("__CAMERA_FILE__");
  // A real answer first on a fresh page, then a stub one.
  await page.goto(base + "/");
  await page.waitForTimeout(2500);
  checks = "ladder";
  out.realThenStub = { real: await answerFor("__CAMERA_FILE__") };
  checks = "server";
  out.realThenStub.stub = await answerFor("__AI_FILE__");
  // Health says the stub runs (banner on before any answer), then a real answer renders: off.
  const before = await gotoStubHealth();
  checks = "ladder";
  out.healthStubThenReal = { before, real: await answerFor("__CAMERA_FILE__") };
  // Health held, a real answer renders, then health answers "stub" late: the real answer keeps it off.
  mode = "hold";
  held = new Promise(r => { release = r; });
  healthServed = 0;
  await page.goto(base + "/");
  await page.waitForTimeout(600);
  out.heldRealThenStubHealth = { real: await answerFor("__CAMERA_FILE__") };
  release();
  await page.waitForTimeout(800);
  out.heldRealThenStubHealth.afterHealth = await vis("#stub");
  out.heldRealThenStubHealth.served = healthServed;
  // A281: the answer goes away with health failing (the banner hides), then with health saying stub (it shows).
  mode = "fail";
  await page.goto(base + "/");
  await page.waitForTimeout(2500);
  out.goneHealthFailing = await answerGoes();
  out.goneHealthStub = { before: await gotoStubHealth(), ...(await answerGoes()) };
  checks = "ladder";   // the cards below are the task's own answers

  // 3. The lines for the parts that are off (health: ladder, no claims index, no voice).
  mode = "ladder";
  await page.goto(base + "/");
  await page.locator("#caps-off").waitFor({ state: "visible", timeout: 20000 });
  out.capsOff = await page.locator("#caps-off span").allTextContents();
  out.stubOnLadder = await vis("#stub");

  // 1 and 2. A trusted camera record, then the deep check box with no model and no recording.
  await page.setInputFiles("#file", "__CAMERA_FILE__");
  // Health has answered for this answer once the line is no longer the "looking" one (any wording).
  await page.waitForFunction(() => document.getElementById("answer").textContent.trim() !== ""
                             && !document.getElementById("inv-local").textContent.startsWith("Looking for"),
                             null, { timeout: 30000 });
  await page.waitForTimeout(500);
  out.camera = await page.evaluate(() => {
    const card = [...document.querySelectorAll("#clues .clue")][0];
    const det = card.querySelector("details.trust-src");
    const box = document.getElementById("investigate");
    const shown = [...box.children].filter(c => c.getClientRects().length && c.innerText.trim());
    const last = shown[shown.length - 1];
    return { finding: card.querySelector(".finding").textContent, cardText: card.innerText,
             summary: det && det.querySelector("summary").textContent, open: det && det.open,
             proves: card.querySelector(".pd dd").textContent,
             limit: card.querySelectorAll(".pd dd")[1].textContent,
             boxText: box.innerText, about: document.getElementById("inv-about").hidden,
             links: [...card.querySelectorAll("details.trust-src a")].map(a => a.getAttribute("href")),
             gap: Math.round(box.getBoundingClientRect().bottom - last.getBoundingClientRect().bottom),
             padding: parseFloat(getComputedStyle(box).paddingBottom) + parseFloat(getComputedStyle(box).borderBottomWidth) };
  });
  // 8. The keyboard: Tab to the Source summary, Enter to open it, Tab to the link, Enter to follow it.
  const onSummary = () => page.evaluate(() => document.activeElement
                                         === document.querySelector("#clues .clue details.trust-src summary"));
  let tabs = 0;
  while (!(await onSummary()) && tabs < 40) { await page.keyboard.press("Tab"); tabs++; }
  out.key = { tabs, onSummary: await onSummary() };
  await page.keyboard.press("Enter");
  out.key.open = await page.evaluate(() => document.querySelector("#clues .clue details.trust-src").open);
  out.cameraOpen = await page.locator("#clues .clue details.trust-src").innerText();
  await page.keyboard.press("Tab");
  Object.assign(out.key, await page.evaluate(() => {
    const a = document.activeElement, s = getComputedStyle(a);
    return { tag: a.tagName, href: a.getAttribute("href"), target: a.target, rel: a.rel, text: a.textContent,
             inSource: !!a.closest("details.trust-src p.src"), focusVisible: a.matches(":focus-visible"),
             outline: s.outlineStyle, outlineWidth: s.outlineWidth };
  }));
  // No tab opens when the Source is not a link (text-only), so the run goes on and the link tests fail on their
  // own assertions rather than the whole task stopping here.
  const tab = page.context().waitForEvent("page", { timeout: 15000 }).catch(() => null);
  await page.keyboard.press("Enter");
  const popup = await tab;
  const end = Date.now() + (popup ? 15000 : 0);
  while (!opened.length && Date.now() < end) await page.waitForTimeout(100);
  out.key.opened = opened.slice();
  if (popup) await popup.close();
  await page.bringToFront();

  // 7. Press the JSON receipt button and leave the pointer on it: a ring, not a fill.
  await page.click("#receipt-json");
  await page.waitForFunction(() => /^Saved |failed/.test(document.getElementById("receipt-status").textContent),
                             null, { timeout: 60000 });
  out.pressed = await page.evaluate(() => {
    const b = document.getElementById("receipt-json"), s = getComputedStyle(b);
    return { focused: document.activeElement === b, bg: s.backgroundColor, outline: s.outlineStyle,
             outlineWidth: s.outlineWidth, status: document.getElementById("receipt-status").textContent };
  });

  // 5. An AI-made record (the interim list).
  await page.click("#again-go");
  await page.setInputFiles("#file", "__AI_FILE__");
  await page.waitForFunction(() => document.getElementById("answer").textContent === "Signed as AI-made",
                             null, { timeout: 30000 });
  out.ai = await page.evaluate(() => {
    const card = [...document.querySelectorAll("#clues .clue")][0];
    return { finding: card.querySelector(".finding").textContent,
             trust: card.querySelector(".trust-line").textContent,
             limit: card.querySelectorAll(".pd dd")[1].textContent };
  });

  // 8. A Source that is not an http(s) URL stays text.
  await page.click("#again-go");
  await page.setInputFiles("#file", "__ODD_FILE__");
  await page.waitForFunction(() => document.getElementById("answer").textContent === "Signed as a camera capture",
                             null, { timeout: 30000 });
  out.odd = await page.evaluate(() => {
    const row = [...document.querySelectorAll("#clues .clue details.trust-src p.src")]
      .find(p => p.querySelector("b").textContent === "Source: ");
    return { links: document.querySelectorAll("#clues .clue details.trust-src a").length, text: row && row.textContent };
  });
  return out;
}
"""

BATCH_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], rows = {}, ownRule = {};
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    return route.continue();
  });
  // Each row's facts cell: the text right before the link, the first character of every line the cell lays out,
  // and (A268 fix 6) where the link's own text sits against the facts text: the glyphs' boxes, from Ranges over
  // the text nodes, not the link's 44 px target, which reaches 11 px up into the facts line by design.
  const measure = () => page.evaluate(() => [...document.querySelectorAll("#rows .row-facts")].map(f => {
    const link = f.querySelector(".receipt-link");
    const before = (link.previousSibling && link.previousSibling.textContent) || "";
    const lineStarts = [];
    const walk = document.createTreeWalker(f, NodeFilter.SHOW_TEXT);
    let top = null;
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      for (let i = 0; i < n.length; i++) {
        if (/\s/.test(n.data[i])) continue;
        const r = document.createRange();
        r.setStart(n, i); r.setEnd(n, i + 1);
        const t = Math.round(r.getBoundingClientRect().top);
        if (top === null || t > top + 4) { lineStarts.push(n.data[i]); top = t; }
      }
    }
    const textBox = node => {
      const r = document.createRange();
      r.selectNodeContents(node);
      return r.getBoundingClientRect();
    };
    const facts = [...f.querySelectorAll(".fact")].map(textBox), link_ = textBox(link);
    return { before, lineStarts, text: f.textContent,
             lastFactTop: Math.max(...facts.map(b => b.top)), factsBottom: Math.max(...facts.map(b => b.bottom)),
             linkTop: link_.top, linkLeft: link_.left, factsLeft: Math.min(...facts.map(b => b.left)) };
  }));
  for (const width of [1440, 800, 740, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(base + "/batch");
    await page.setInputFiles("#files", ["__CAMERA_FILE__", "__AI_FILE__"]);
    await page.waitForFunction(() => document.querySelectorAll("#rows .receipt-link").length === 2, null,
                               { timeout: 60000 });
    rows[width] = await measure();
    // Since A272 the facts sit in a block line holding a flex list, and either box alone pushes the link down, so
    // the page can't show the link's own rule at work. Here the facts are laid out as plain inline text, as they
    // were before A272: then only ".row-facts .receipt-link" keeps the link off the facts' last line.
    // (Through the CSSOM: the page's CSP, style-src 'self', refuses an added style tag.)
    const inline = on => page.evaluate(on => document.querySelectorAll("#rows .facts-line, #rows .facts-list")
      .forEach(n => on ? n.style.setProperty("display", "inline", "important") : n.style.removeProperty("display")),
      on);
    await inline(true);
    ownRule[width] = await measure();
    await inline(false);
  }
  return { offHost, rows, ownRule };
}
"""

# The batch page with health failing: hidden before any row, shown once stub rows render (A270).
BATCH_STUB_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [];
  let healthAsked = 0;
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health") { healthAsked++; return route.abort(); }
    return route.continue();
  });
  await page.goto(base + "/batch");
  await page.waitForTimeout(1500);
  const before = await page.locator("#stub").isVisible();
  await page.setInputFiles("#files", ["__CAMERA_FILE__", "__AI_FILE__"]);
  await page.waitForFunction(() => document.querySelectorAll("#rows .receipt-link").length === 2, null,
                             { timeout: 60000 });
  return { offHost, healthAsked, before, after: await page.locator("#stub").isVisible(),
           meta: await page.locator("#batch-meta").textContent() };
}
"""


# A278 (Codex FAIL on PR #55 @ fa8367b): the batch page's banner follows the rows on screen, with no latch, as the
# single-file page's does (A273). The stub server answers every file from the stub engine; "ladder" batches are its
# own answers with the engine rewritten, so each row is shaped as the server writes it.
BATCH_BANNER_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], out = { offHost };
  // health: fail, stub (the stub server's own), or hold (until release, then the same "stub"). batch: stub, ladder,
  // or mixed (the first row ladder, the rest stub).
  let health = "fail", batch = "stub", release, healthServed = 0, healthEngine = null;
  let held = new Promise(r => { release = r; });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health") {
      if (health === "fail") return route.abort();
      if (health === "hold") await held;
      // The server's reply is read here first, so the wait below starts once the page has it.
      const res = await route.fetch();
      const json = await res.json();
      await route.fulfill({ response: res, json });
      healthEngine = json.engine;
      healthServed++;
      return;
    }
    if (url.pathname === "/api/batch" && batch !== "stub") {
      const res = await route.fetch();
      const json = await res.json();
      json.results.forEach((r, k) => { if (batch === "ladder" || k === 0) Object.assign(r, { engine: "ladder", demo: false }); });
      return route.fulfill({ response: res, json });
    }
    return route.continue();
  });
  const vis = () => page.locator("#stub").isVisible();
  // A batch of the two files, then whether the banner shows and which engines the rows name.
  const check = async () => {
    await page.setInputFiles("#files", ["__CAMERA_FILE__", "__AI_FILE__"]);
    await page.waitForFunction(() => document.querySelectorAll("#rows .row.done").length === 2
                               && !document.getElementById("batch-result").hasAttribute("aria-busy"), null,
                               { timeout: 60000 });
    return { banner: await vis(), meta: await page.locator("#batch-meta").textContent() };
  };
  await page.setViewportSize({ width: 1440, height: 900 });

  // Health fails throughout: a stub batch, "Check more", a ladder batch, "Check more", a mixed batch.
  await page.goto(base + "/batch");
  await page.waitForTimeout(1500);
  out.stubThenLadder = { before: await vis(), stub: await check() };
  await page.click("#batch-again");
  out.stubThenLadder.afterAgain = await vis();
  batch = "ladder";
  out.stubThenLadder.ladder = await check();
  await page.click("#batch-again");
  batch = "mixed";
  out.stubThenLadder.mixed = await check();

  // Health held, a ladder batch renders, then health answers "stub" late.
  health = "hold";
  batch = "ladder";
  await page.goto(base + "/batch");
  await page.waitForTimeout(600);
  out.ladderThenLateStub = { ladder: await check(), servedBefore: healthServed };
  release();
  const end = Date.now() + 5000;
  while (!healthServed && Date.now() < end) await page.waitForTimeout(100);
  await page.waitForTimeout(800);
  out.ladderThenLateStub.served = healthServed;
  out.ladderThenLateStub.engine = healthEngine;
  out.ladderThenLateStub.afterHealth = await vis();

  // Health says stub before any row (shown), then a ladder batch renders.
  // It waits for health's reply, then up to 10 s for the banner, so a page that ignores health fails the "before"
  // assertion instead of stopping the task on a wait (A281).
  health = "stub";
  const served = healthServed;
  await page.goto(base + "/batch");
  const until = Date.now() + 20000;
  while (healthServed === served && Date.now() < until) await page.waitForTimeout(100);
  const before = await page.locator("#stub").waitFor({ state: "visible", timeout: 10000 }).then(() => true, () => false);
  out.healthStubThenLadder = { before, ladder: await check() };
  return out;
}
"""

# The deep check box when lines can follow (A268 fix 2): a live model, then a recording on offer. Health is the
# ladder's, with Glimmer up or down; the answer is the task's camera record, and /api/recorded answers with a
# recording of the right shape (investigate.js checks recorded, live, label and note before it offers one).
DEEP_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], out = { offHost };
  const real = await (await page.request.get(base + "/api/health", { timeout: 60000 })).json();
  const health = (glimmer, recorded) => ({ ...real, engine: "ladder",
    capabilities: { ...real.capabilities, engine: true, glimmer, recorded, claims_index: false, voice: false,
                    receipt: false },
    details: { ...real.details, glimmer: glimmer ? { ready: true } : { ready: false, reason: "not_running" } } });
  let mode = "live", recordedAsked = 0;
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health") return route.fulfill({ json: health(mode === "live", mode === "recorded") });
    if (url.pathname === "/api/check") return route.fulfill({ json: __CAMERA__ });
    if (url.pathname === "/api/recorded") {
      recordedAsked++;
      return route.fulfill({ json: { recorded: true, live: false, label: "Recorded run, not live. Test recording.",
                                     note: { steps: [] } } });
    }
    return route.continue();
  });
  const box = () => page.evaluate(() => {
    const about = document.getElementById("inv-about");
    return { aboutHidden: about.hidden, local: document.getElementById("inv-local").textContent,
             offer: !document.getElementById("inv-recorded-offer").hidden };
  });
  await page.setViewportSize({ width: 1440, height: 900 });

  // A live model: the line says Glimmer runs here.
  await page.goto(base + "/");
  await page.setInputFiles("#file", "__CAMERA_FILE__");
  await page.waitForFunction(() => document.getElementById("inv-local").textContent
                             .startsWith("Muse Glimmer runs on this computer"), null, { timeout: 30000 });
  out.live = { ...(await box()), visible: await page.locator("#inv-about").isVisible() };

  // No model, but a recording of this file is offered.
  mode = "recorded";
  await page.goto(base + "/");
  await page.setInputFiles("#file", "__CAMERA_FILE__");
  await page.locator("#inv-recorded-offer").waitFor({ state: "visible", timeout: 30000 });
  out.recorded = { ...(await box()), visible: await page.locator("#inv-about").isVisible(), asked: recordedAsked };
  return out;
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


@pytest.fixture(scope="module")
def stub_server(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("a262-server")
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
    """Runs one page task in headless-run, waiting up to 3 minutes for a free host slot."""
    runner = _headless_run()
    if not runner:
        pytest.skip("headless-run (chrome-headless-shell) is not installed here")
    task = tmp_path / f"{job}.mjs"
    task.write_text(task_text.replace("__PORT__", str(port))
                    .replace("__CAMERA__", json.dumps(CAMERA)).replace("__AI__", json.dumps(AI))
                    .replace("__ODD__", json.dumps(ODD)).replace("__LIST_URL__", LIST_URL)
                    .replace("__ODD_FILE__", _jpeg(tmp_path / "odd.jpg", (120, 160, 90)))
                    .replace("__CAMERA_FILE__", _jpeg(tmp_path / "camera.jpg", (90, 120, 160)))
                    .replace("__AI_FILE__", _jpeg(tmp_path / "ai.jpg", (160, 120, 90))))

    def busy(run, receipt) -> bool:  # both host browser slots are taken by other jobs
        said = run.stderr + str((receipt or {}).get("error"))
        return "capacity" in said and "busy" in said

    end = time.monotonic() + 180
    while True:
        run = subprocess.run([runner, "--job", job, "--owner-task", "DAT-850", "--task", str(task),
                              "--timeout", "150", "--navigation-timeout", "60000"],
                             capture_output=True, text=True, timeout=210)
        lines = [ln for ln in run.stdout.splitlines() if ln.startswith("{")]
        receipt = json.loads(lines[-1]) if lines else None
        if busy(run, receipt) and time.monotonic() < end:
            time.sleep(5)
            continue
        break
    if busy(run, receipt):
        pytest.skip("the host's headless browser slots stayed busy for 3 minutes")
    assert receipt, f"headless-run gave no receipt: {run.stderr[-2000:]}"
    assert receipt["status"] == "completed", (receipt.get("status"), receipt.get("error"), run.stderr[-3000:])
    return receipt["result"]


@pytest.fixture(scope="module")
def page(stub_server, tmp_path_factory):
    return _run_page(PAGE_TASK, "secondlook-a262-page", stub_server, tmp_path_factory.mktemp("a262-page"))


@pytest.fixture(scope="module")
def batch(stub_server, tmp_path_factory):
    return _run_page(BATCH_TASK, "secondlook-a262-batch", stub_server, tmp_path_factory.mktemp("a262-batch"))


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip()) if s]


# ---- 1. the C2PA card ---------------------------------------------------------------------------------------

def test_c2pa_card_leads_with_one_sentence_and_folds_the_list_source(page):
    c = page["camera"]
    assert c["finding"] == CAMERA_HEAD
    # Closed by default: the URL, the sha256 and the rung's full line are not in the card's rendered text.
    assert c["summary"] == "Source" and c["open"] is False
    for hidden in (LIST_URL, LIST_SHA, "Google C2PA SDK for Android"):
        assert hidden not in c["cardText"]
    assert "Trust list: C2PA Trust List, dated 2026-10-17; the signer is on this list." in c["cardText"]
    # Opened, it holds them all, word for word.
    for shown in (LIST_URL, LIST_SHA, CAMERA_FINDING):
        assert shown in page["cameraOpen"]
    # Proves and Does not prove are as the rung wrote them.
    assert c["proves"] == CAMERA_PROVES and c["limit"] == CAMERA_LIMIT
    # The interim list keeps the rung's "temporary" caveat in view.
    assert page["ai"]["finding"] == AI_HEAD
    assert page["ai"]["trust"].endswith("the signer is on this list. C2PA describes this list as temporary.")


def test_c2pa_headline_falls_back_to_the_whole_finding():
    fn = APP[APP.index("function c2paHeadline"):APP.index("function trustLine")]
    # Only a trusted card with its generator and the rung's sentence shape is shortened; any other keeps it whole.
    assert 'raw.trusted !== true || !raw.trust_list' in fn and 'f.startsWith("C2PA manifest ")' in fn
    assert 'withWhole("p", "finding", headline || ev.finding, WHOLE_DATE)' in APP


# ---- 2. the deep check box ----------------------------------------------------------------------------------

def test_deep_check_box_says_plainly_it_is_off(page):
    c = page["camera"]
    assert c["boxText"].split("\n") == ["Deep check", LOCAL_OFF] or \
        [ln for ln in c["boxText"].split("\n") if ln.strip()] == ["Deep check", LOCAL_OFF]
    assert "README" not in c["boxText"] and "lines below" not in c["boxText"]
    assert c["about"] is True, "the 'lines below' sentence is hidden when no lines can follow"
    # No empty padding: the box ends at its last line plus its own padding and border.
    assert c["gap"] <= c["padding"] + 1, (c["gap"], c["padding"])
    assert all(len(s.split()) <= 20 for s in _sentences(LOCAL_OFF))


@pytest.fixture(scope="module")
def deep(stub_server, tmp_path_factory):
    return _run_page(DEEP_TASK, "secondlook-a268-deep", stub_server, tmp_path_factory.mktemp("a268-deep"))


def test_deep_check_box_shows_the_lines_below_sentence_with_a_live_model(deep):
    # A268 fix 2: "The model picks ..." shows when lines can follow; removing its about.hidden = false must fail.
    assert deep["offHost"] == []
    live = deep["live"]
    assert live["local"].startswith("Muse Glimmer runs on this computer"), live
    assert live["aboutHidden"] is False and live["visible"] is True, live


def test_deep_check_box_shows_the_lines_below_sentence_with_a_recording_on_offer(deep):
    rec = deep["recorded"]
    assert rec["asked"] >= 1 and rec["offer"] is True, rec
    assert rec["local"].startswith("Muse Glimmer is not running on this computer now"), rec
    assert rec["aboutHidden"] is False and rec["visible"] is True, rec


def test_deep_check_lines_carry_no_readme_pointer():
    for name in ("LOCAL_OFF", "LOCAL_UNSURE"):
        line = INV[INV.index(f"const {name} = "):]
        line = line[:line.index(";\n")]
        assert "README" not in line and "deep check is off on this computer" in line, name
    # "Not running" only when health says not_running (A228).
    unsure = INV[INV.index("const LOCAL_UNSURE = "):]
    assert "not running" not in unsure[:unsure.index(";\n")]
    assert 'id="inv-about" hidden' in HTML


# ---- 3. the "not installed" lines ---------------------------------------------------------------------------

def test_off_lines_say_what_each_part_is_for_and_needs(page):
    lines = page["capsOff"]
    assert len(lines) == 2, lines
    claim, voice = lines
    assert claim.startswith("Check a claim is off on this computer.") and "published fact-checks" in claim
    assert "one-time download of about 200 MB" in claim
    assert voice.startswith("Voice notes are off on this computer.") and "into text" in voice
    assert "one-time install" in voice and "488 MB" in voice
    for line in lines:
        for jargon in ("Data Commons", "ClaimReview", "whisper", "pinned"):
            assert jargon not in line
        assert all(len(s.split()) <= 20 for s in _sentences(line)), line


def test_off_line_sizes_match_their_sources():
    readme = (ROOT / "README.md").read_text()
    assert "about 200 MB to download" in readme
    voice = (ROOT / "docs" / "W9-voice.md").read_text()
    assert "487,614,201" in voice   # ggml-small.en.bin, 488 MB
    assert "about 200 MB" in DESK and "488 MB" in DESK


# ---- 4. the stub banner -------------------------------------------------------------------------------------

def test_stub_banner_is_hidden_before_any_answer_until_health_says_stub(page):
    assert page["stubWhileHeld"] is False, "no flash of the banner before health answers"
    assert page["stubOnStub"] is True
    assert page["stubOnLadder"] is False
    # Health unknown and no answer yet: nothing canned is on screen, so the banner waits (A270 brief).
    assert page["failAsks"] >= 3 and page["stubFailedBeforeAnswer"] is False   # app.js's two asks and desk.js's
    assert page["timeoutAsks"] >= 3 and page["stubTimedOutBeforeAnswer"] is False


def _stub(answer: dict) -> None:
    assert answer["meta"].startswith("Engine: stub "), answer["meta"]
    assert answer["banner"] is True, "a canned answer must never look like a real reading"


def test_stub_answer_shows_the_banner_while_health_is_held(page):
    _stub(page["stubAnswerHeld"])
    assert page["stubAnswerHeldThenStub"] is True


def test_stub_answer_shows_the_banner_when_health_fails_twice(page):
    _stub(page["stubAnswerFailed"])


def test_stub_answer_shows_the_banner_when_health_times_out(page):
    _stub(page["stubAnswerTimedOut"])


def _real(answer: dict) -> None:
    assert answer["meta"].startswith("Engine: ladder "), answer["meta"]
    assert answer["banner"] is False, "a real answer must never carry the canned banner"


# A273 (Codex FAIL on PR #55 @ 4d1d3ba): the banner follows the answer on screen, with no latch. Each test below fails
# with app.js's banner logic at 12fe9fb (health only) or at 4d1d3ba (health or any stub answer so far, latched).

def test_real_answer_with_failing_health_hides_the_banner_a_stub_answer_showed(page):
    s = page["stubThenReal"]
    _stub(s["stub"])          # 12fe9fb: hidden, since health failed
    _real(s["real"])          # 4d1d3ba: still shown, latched by the stub answer before it
    _stub(s["stubAgain"])


def test_stub_answer_after_a_real_answer_shows_the_banner(page):
    r = page["realThenStub"]
    _real(r["real"])
    _stub(r["stub"])          # 12fe9fb: hidden, since health failed


def test_real_answer_hides_the_banner_health_showed(page):
    h = page["healthStubThenReal"]
    assert h["before"] is True, "before any answer, health saying stub shows it"
    _real(h["real"])          # 12fe9fb and 4d1d3ba: still shown, from health


def test_late_stub_health_never_shows_the_banner_over_a_real_answer(page):
    h = page["heldRealThenStubHealth"]
    _real(h["real"])
    assert h["served"] >= 1, "health answered after the real answer"
    assert h["afterHealth"] is False   # 12fe9fb and 4d1d3ba: health's late "stub" turns it on


# A281 (a reviewer A279 on PR #55 @ 7ec6805): an answer that goes away takes its reason for the banner with it.
# With app.js's answerGone() taken out of runFile's caption refusal (app.js:533) the first test fails on "caption";
# taken out of check's catch (app.js:515), on "failed". The second test fails if a gone answer hides the banner
# whatever health says.

def _gone(seq: dict, banner: bool) -> None:
    _stub(seq["stub"])
    _stub(seq["stubAgain"])
    for step in ("caption", "failed"):
        assert seq[step]["result"] is False, (step, "the result is hidden, so no answer is on screen")
        assert seq[step]["banner"] is banner, (step, seq[step])


def test_answer_gone_with_health_failing_hides_the_banner(page):
    _gone(page["goneHealthFailing"], False)


def test_answer_gone_with_health_stub_keeps_the_banner(page):
    assert page["goneHealthStub"]["before"] is True, "before any answer, health saying stub shows it"
    _gone(page["goneHealthStub"], True)


# ---- 8. the Source link -------------------------------------------------------------------------------------

def test_source_link_is_reached_and_followed_by_keyboard(page):
    k = page["key"]
    assert k["onSummary"] is True, k
    assert k["open"] is True, "Enter on the summary opens the disclosure"
    assert k["tag"] == "A" and k["inSource"] is True, k
    assert k["href"] == LIST_URL and k["text"] == LIST_URL
    assert k["target"] == "_blank" and "noopener" in k["rel"].split() and "noreferrer" in k["rel"].split()
    assert k["focusVisible"] is True and k["outline"] == "solid" and k["outlineWidth"] == "3px", k
    assert k["opened"] == [LIST_URL], "Enter on the link opens the list's source"


def test_source_that_is_not_http_stays_text(page):
    # The same renderer links the https list on the camera card, so this fails with a text-only Source row as well
    # as with one that links any scheme.
    assert page["camera"]["links"] == [LIST_URL], page["camera"]["links"]
    odd = page["odd"]
    assert odd["links"] == 0
    assert odd["text"] == "Source: " + ODD_SOURCE


# ---- 5. the AI-made card's limit ----------------------------------------------------------------------------

def test_ai_made_card_says_it_does_not_show_how_much_changed(page):
    assert page["ai"]["limit"] == AI_LIMIT + " " + AI_EXTENT
    assert page["camera"]["limit"] == CAMERA_LIMIT, "a camera record's limit is unchanged"


# ---- 7. the receipt buttons after a press -------------------------------------------------------------------

def test_pressed_receipt_button_shows_a_ring_not_a_fill(page):
    p = page["pressed"]
    assert p["status"].startswith("Saved "), p
    assert p["focused"] is True
    assert p["bg"] in ("rgba(0, 0, 0, 0)", "transparent"), p["bg"]
    assert p["outline"] == "solid" and p["outlineWidth"] == "3px", p
    assert page["offHost"] == []


# ---- 6. the batch row ---------------------------------------------------------------------------------------

def _receipt_on_its_own_line(width, row) -> None:
    # Starts a line of its own under the facts, at the cell's left edge, with clear space under the facts' glyphs.
    assert row["linkTop"] > row["lastFactTop"] + 4, (width, row)
    assert row["linkTop"] >= row["factsBottom"] + 2, (width, row)
    assert abs(row["linkLeft"] - row["factsLeft"]) <= 1, (width, row)


def test_batch_receipt_never_wraps_alone(batch):
    assert batch["offHost"] == []
    assert sorted(batch["rows"]) == ["1440", "390", "740", "800"]
    for width, rows in batch["rows"].items():
        assert len(rows) == 2, width
        for row in rows:
            assert not row["before"].rstrip().endswith("·"), (width, row)
            assert "·" not in row["lineStarts"], (width, row)
            assert row["text"].endswith("Receipt"), (width, row)
            _receipt_on_its_own_line(width, row)


def test_batch_receipt_keeps_its_own_line_by_its_own_rule(batch):
    # A268 fix 6: removing ".row-facts .receipt-link" must fail. With the facts as plain inline text, the link would
    # follow the time on the same line ("under 0.1 sReceipt") without that rule.
    assert sorted(batch["ownRule"]) == ["1440", "390", "740", "800"]
    for width, rows in batch["ownRule"].items():
        assert len(rows) == 2, width
        for row in rows:
            _receipt_on_its_own_line(width, row)


def test_batch_stub_rows_show_the_banner_when_health_fails(stub_server, tmp_path):
    r = _run_page(BATCH_STUB_TASK, "secondlook-a270-batch-stub", stub_server, tmp_path)
    assert r["offHost"] == [] and r["healthAsked"] >= 1
    assert r["before"] is False, "no banner before any row while health is unknown"
    assert r["meta"].startswith("Engine: stub "), r["meta"]
    assert r["after"] is True, "a canned row must never look like a real reading"


# A278: the batch page's banner, with no latch. Each test fails with batch.js's banner logic at 706b668 (health or
# any stub row so far, latched) or with the matching A278 call taken out of batch.js.

@pytest.fixture(scope="module")
def batch_banner(stub_server, tmp_path_factory):
    r = _run_page(BATCH_BANNER_TASK, "secondlook-a278-batch-banner", stub_server,
                  tmp_path_factory.mktemp("a278-batch-banner"))
    assert r["offHost"] == []
    return r


def test_batch_stub_rows_show_the_banner_with_health_failing(batch_banner):
    s = batch_banner["stubThenLadder"]
    assert s["before"] is False, "no banner before any row while health is unknown"
    assert s["stub"]["meta"].startswith("Engine: stub "), s["stub"]["meta"]
    assert s["stub"]["banner"] is True, "a canned row must never look like a reading of the image"
    assert s["mixed"]["meta"].startswith("Engine: ladder, stub "), s["mixed"]["meta"]
    assert s["mixed"]["banner"] is True, "one stub row among ladder rows shows it"


def test_batch_check_more_then_ladder_rows_hide_the_banner_stub_rows_showed(batch_banner):
    s = batch_banner["stubThenLadder"]
    assert s["stub"]["banner"] is True
    assert s["afterAgain"] is False, "Check more leaves no rows on screen, and health failed"   # 706b668: latched
    assert s["ladder"]["meta"].startswith("Engine: ladder "), s["ladder"]["meta"]
    assert s["ladder"]["banner"] is False, "ladder rows never carry the canned banner"         # 706b668: latched


def test_batch_late_stub_health_never_shows_the_banner_over_ladder_rows(batch_banner):
    late = batch_banner["ladderThenLateStub"]
    assert late["ladder"]["meta"].startswith("Engine: ladder "), late["ladder"]["meta"]
    assert late["ladder"]["banner"] is False
    assert late["servedBefore"] == 0, "health was still held while the rows rendered"
    assert late["served"] >= 1 and late["engine"] == "stub", "health answered stub after the ladder rows"
    assert late["afterHealth"] is False, "health's late stub must not show it over ladder rows"  # 706b668: shown


def test_batch_ladder_rows_hide_the_banner_health_showed(batch_banner):
    h = batch_banner["healthStubThenLadder"]
    assert h["before"] is True, "before any row, health saying stub shows it"
    assert h["ladder"]["meta"].startswith("Engine: ladder "), h["ladder"]["meta"]
    assert h["ladder"]["banner"] is False, "ladder rows never carry the canned banner"         # 706b668: health
