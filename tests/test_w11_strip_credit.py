"""DAT-850: the published fact-check strip carries the Data Commons credit.

docs/LICENSES.md asks us to credit "Data Commons" and name the fact-checker wherever a match is shown. The claim
box already shows desk.FEED_CREDIT; a reviewer's licence check of the hero case found the strip under
the headline showed none. The strip now ends with that same text, read from /api/health, plus the fact-checker of
the match shown. The page test runs in the bounded headless browser (`headless-run`, chrome-headless-shell, muted)
against the stub server on 127.0.0.1. Its answers are fixtures made for this test: the publisher and review are
invented, not a real fact-check. Set SECONDLOOK_SHOTS_DIR to also save the 1280 px and 390 px screenshots.

app.js gives up on a health request after 6 s by the wall clock and asks once more (askHealth). The page starts that
timer when it calls fetch, before the stub's route sees the request, so on a busy host the 6 s could pass with the
request still on its way and the page would ask again: the counts below then depended on the host's load (DAT-850
A218, 2 of 5 failing at load 67 to 139). The page tasks own that timer instead (OWN_HEALTH_TIMER): the page's 6 s
timers are kept, not started, and fire only when a test fires them, so every count holds at any load.
"""
from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("fastapi")

from secondlook.web import desk  # noqa: E402
from secondlook.web.app import STATIC  # noqa: E402
from tests.test_w11_receipt_page import _jpeg, _run_page, stub_server  # noqa: E402,F401  (stub_server is a fixture)

HTML = (STATIC / "index.html").read_text()
APP = (STATIC / "app.js").read_text()
CSS = (STATIC / "app.css").read_text()


def test_strip_has_a_credit_line_fed_by_the_one_credit_text():
    strip = HTML[HTML.index('<section class="factcheck" id="factcheck"'):]
    strip = strip[:strip.index("</section>")]
    assert '<p class="fc-credit" id="fc-credit"></p>' in strip
    assert strip.index('id="fc-more"') < strip.index('id="fc-credit"')
    # The credit comes from health, which reports desk.FEED_CREDIT. The page writes one credit sentence of its own,
    # the fixed fallback for a health request that fails twice, and nowhere else.
    assert APP.count("Data Commons") == 1, "the fallback is the only sentence of credit text in the page"
    assert ('const FALLBACK_CREDIT = "Fact-check data: Data Commons ClaimReview feed (CC BY). '
            'Each review is its publisher\'s own work.";') in APP
    assert "(h.details && h.details.claims_index) || {}" in APP and "typeof idx.credit === \"string\"" in APP
    assert ".fc-credit" in CSS
    assert "Data Commons" in desk.FEED_CREDIT and "CC BY" in desk.FEED_CREDIT


# app.js's health timeout (askHealth). Each page task returns how many timers of this length the page started, and
# the tests check that count, so a change to the page's limit fails here instead of making the tests timing-bound.
PAGE_HEALTH_TIMEOUT_MS = 6000

# Run before the page's own scripts: every setTimeout of PAGE_HEALTH_TIMEOUT_MS is kept, not started, until the task
# calls window.__healthTimers.fire(). clearTimeout (askHealth's .finally) drops a kept timer as it would a real one.
OWN_HEALTH_TIMER = r"""
  await page.addInitScript(ms => {
    const realSet = window.setTimeout, realClear = window.clearTimeout, kept = new Map();
    let nextId = -1;
    window.__healthTimers = { started: 0, pending: () => kept.size,
      fire() { const due = [...kept.values()]; kept.clear(); due.forEach(f => f()); return due.length; } };
    window.setTimeout = function (fn, delay, ...args) {
      if (delay !== ms || typeof fn !== "function") return realSet.call(this, fn, delay, ...args);
      const id = nextId--;
      kept.set(id, () => fn(...args));
      window.__healthTimers.started++;
      return id;
    };
    window.clearTimeout = function (id) { if (!kept.delete(id)) realClear.call(this, id); };
  }, __HEALTH_MS__);
  const healthTimers = () => page.evaluate(() => ({ started: window.__healthTimers.started,
    pending: window.__healthTimers.pending() }));
"""


TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const credit = __CREDIT__;
  const card = __CARD__;
  const shots = __SHOTS__;
  const offHost = [];
  let healthAnswered = 0;  // app.js and desk.js each ask /api/health once at load
__OWN_HEALTH_TIMER__
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health") {
      // The stub box has no claims index; report one the way desk.claims_index() does, with its credit.
      const res = await route.fetch();
      const h = await res.json();
      h.details = Object.assign({}, h.details, { claims_index: { installed: true, rows: "3", credit } });
      await route.fulfill({ response: res, json: h });
      healthAnswered++;
      return;
    }
    if (url.pathname === "/api/check") {
      const res = await route.fetch();
      const r = await res.json();
      r.evidence = r.evidence.concat([card]);
      return route.fulfill({ response: res, json: r });
    }
    return route.continue();
  });
  const text = (id) => page.locator("#" + id).textContent();
  const hidden = (id) => page.locator("#" + id).evaluate(e => e.hidden);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(base + "/");
  const until = Date.now() + 60000;  // a deadline only for a broken page: the timer is ours, so no answer is late
  while (healthAnswered < 2 && Date.now() < until) await page.waitForTimeout(100);
  await page.waitForTimeout(200);  // and the page has read the answers
  await page.setInputFiles("#file", "__FIRST__");
  await page.locator("#factcheck").waitFor({ state: "visible" });
  const fromAnswer = { credit: await text("fc-credit"), who: await text("fc-line") };
  const strip = page.locator("#factcheck");
  const box = async () => strip.evaluate(e => { const r = e.getBoundingClientRect();
    return { right: r.right, vw: document.documentElement.clientWidth, sw: document.documentElement.scrollWidth }; });
  const wide = await box();
  if (shots) await strip.screenshot({ path: shots + "/dat850-strip-credit-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  const narrow = await box();
  if (shots) await page.screenshot({ path: shots + "/dat850-strip-credit-phone.png", fullPage: true });

  // The deep check (investigate.js) hands its own evidence to the same strip: a different fact-checker.
  const second = Object.assign({}, card, { raw: Object.assign({}, card.raw, { publisher: "Second Example Desk",
    is_listed_fact_checker: false }) });
  await page.evaluate(ev => { document.dispatchEvent(new CustomEvent("secondlook:deep-evidence",
    { detail: { evidence: [ev], factcheck: false } })); }, second);
  const fromDeep = { credit: await text("fc-credit") };
  // No match: the strip and its credit are gone.
  await page.evaluate(() => document.dispatchEvent(new CustomEvent("secondlook:deep-evidence",
    { detail: { evidence: [], factcheck: false } })));
  const none = { credit: await text("fc-credit"), stripHidden: await hidden("factcheck") };
  const timers = await healthTimers();
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { fromAnswer, fromDeep, none, wide, narrow, offHost, healthAnswered, timers };
}
"""

CARD = {
    "rung": "claim", "strength": "none",
    "finding": "Example Fact Check reviewed the claim a made-up claim and rated it False.",
    "proves": "That Example Fact Check published this rating.",
    "does_not_prove": "That the typed claim is the claim they reviewed. Second Look gives no true or false verdict of its own.",
    "raw": {"publisher": "Example Fact Check", "is_listed_fact_checker": True,
            "claim_reviewed": "A made-up claim, written for this layout test", "rating": "False",
            "date_published": "2026-10-01", "review_url": "https://example.org/fact-check/a-made-up-claim",
            "shared_terms": 3, "typed_terms": 3, "reviewed_terms": 3, "negation_differs": False},
}


def _own_health_timer(task: str) -> str:
    return task.replace("__OWN_HEALTH_TIMER__", OWN_HEALTH_TIMER).replace("__HEALTH_MS__", str(PAGE_HEALTH_TIMEOUT_MS))


def test_strip_shows_the_credit_and_the_fact_checker_in_a_real_page(stub_server, tmp_path):
    shots = os.environ.get("SECONDLOOK_SHOTS_DIR", "")
    task = (_own_health_timer(TASK).replace("__CREDIT__", json.dumps(desk.FEED_CREDIT))
            .replace("__CARD__", json.dumps(CARD)).replace("__SHOTS__", json.dumps(shots)))
    r = _run_page(task, "secondlook-strip-credit", stub_server, tmp_path)
    assert r["offHost"] == [], "no request leaves 127.0.0.1"
    # app.js started its one 6 s timer (so the test owns the page's limit) and the answer cleared it.
    assert r["timers"] == {"started": 1, "pending": 0}, r["timers"]
    assert r["healthAnswered"] == 2, "both of the page's health requests were answered with the credit"
    # The same text the claim box shows, then who: the fact-checker of the match in the strip.
    assert r["fromAnswer"]["credit"] == desk.FEED_CREDIT + " This one: Example Fact Check."
    assert "Example Fact Check" in r["fromAnswer"]["who"]
    assert r["fromDeep"]["credit"] == desk.FEED_CREDIT + " This one: Second Example Desk."
    assert r["none"] == {"credit": "", "stripHidden": True}
    for m in (r["wide"], r["narrow"]):
        assert m["right"] <= m["vw"] and m["sw"] <= m["vw"], f"the strip overflows the page: {m}"


HELD = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const mode = "__MODE__";
  const credit = __CREDIT__;
  const card = __CARD__;
  const offHost = [];
  let healthAsked = 0, release;
  const gate = new Promise(r => { release = r; });  // health answers only when the test lets it
__OWN_HEALTH_TIMER__
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health") {
      const n = ++healthAsked;
      await gate;
      // "failed": every health request fails. "flaky": the two asked at load fail (app.js and desk.js one each),
      // app.js's retry, the third, is answered. "delayed": every request is answered, late. "timed_out": the page's
      // 6 s limit passes while health is held, app.js asks again, and then every request is answered.
      // "timed_out_twice": the limit passes on both of app.js's tries; the answers come only after that.
      if (mode === "failed" || (mode === "flaky" && n <= 2)) return route.abort();
      // A request the page gave up on (its 6 s timeout) is gone by now; answering it is not an error.
      try {
        const res = await route.fetch();
        const h = await res.json();
        h.details = Object.assign({}, h.details, { claims_index: { installed: true, rows: "3", credit } });
        return await route.fulfill({ response: res, json: h });
      } catch (e) { return; }
    }
    if (url.pathname === "/api/check") {
      const res = await route.fetch();
      const r = await res.json();
      r.evidence = r.evidence.concat([card]);
      return route.fulfill({ response: res, json: r });
    }
    return route.continue();
  });
  const text = (id) => page.locator("#" + id).textContent();
  const hidden = (id) => page.locator("#" + id).evaluate(e => e.hidden);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(base + "/");
  await page.setInputFiles("#file", "__FIRST__");
  // The answer (with the match in it) is on screen, and health has not answered: the strip is held back.
  await page.locator("#result").waitFor({ state: "visible" });
  await page.waitForTimeout(700);  // longer than the retry's wait, so a bad hold would show by now
  const held = { stripHidden: await hidden("factcheck"), healthAsked };
  let timedOut = null;
  if (mode === "timed_out") {
    // Both load-time requests are with the stub, then app.js's limit passes: it gives up and, 400 ms on, asks again.
    const until = Date.now() + 60000;
    while (healthAsked < 2 && Date.now() < until) await page.waitForTimeout(100);
    const fired = await page.evaluate(() => window.__healthTimers.fire());
    while (healthAsked < 3 && Date.now() < until) await page.waitForTimeout(100);
    await page.waitForTimeout(700);
    timedOut = { fired, healthAsked, stripHidden: await hidden("factcheck") };
  }
  if (mode === "timed_out_twice") {
    // Both of app.js's tries pass their 6 s limit with health still held: the fixed credit stands in, at once.
    const until = Date.now() + 60000;
    while (healthAsked < 2 && Date.now() < until) await page.waitForTimeout(100);
    const first = await page.evaluate(() => window.__healthTimers.fire());
    while (healthAsked < 3 && Date.now() < until) await page.waitForTimeout(100);
    await page.waitForTimeout(200);
    const between = await hidden("factcheck");
    const second = await page.evaluate(() => window.__healthTimers.fire());
    await page.locator("#factcheck").waitFor({ state: "visible", timeout: 60000 });
    timedOut = { fired: [first, second], healthAsked, betweenHidden: between, credit: await text("fc-credit") };
  }
  release();
  await page.locator("#factcheck").waitFor({ state: "visible", timeout: 60000 });
  const shown = { credit: await text("fc-credit"), who: await text("fc-line"), healthAsked };
  // A later match, from the deep check, shows at once: the credit is already known.
  const second = Object.assign({}, card, { raw: Object.assign({}, card.raw, { publisher: "Second Example Desk" }) });
  await page.evaluate(ev => { document.dispatchEvent(new CustomEvent("secondlook:deep-evidence",
    { detail: { evidence: [ev], factcheck: false } })); }, second);
  const later = { credit: await text("fc-credit"), stripHidden: await hidden("factcheck") };
  await page.evaluate(() => document.dispatchEvent(new CustomEvent("secondlook:deep-evidence",
    { detail: { evidence: [], factcheck: false } })));
  const none = { credit: await text("fc-credit"), stripHidden: await hidden("factcheck") };
  const timers = await healthTimers();
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { held, timedOut, shown, later, none, offHost, timers };
}
"""

FALLBACK = ("Fact-check data: Data Commons ClaimReview feed (CC BY). "
            "Each review is its publisher's own work.")


# The test holds health until the page has shown its answer. The page's 6 s limit is the test's (OWN_HEALTH_TIMER),
# so however long the hold lasts on a busy host, it passes only in "timed_out", where the test fires it.
@pytest.mark.parametrize("mode, expected_credit, asked, timers", [
    ("delayed", desk.FEED_CREDIT, 2, 1),    # app.js and desk.js each ask once
    ("flaky", desk.FEED_CREDIT, 3, 2),      # app.js's retry gets the answer the first try missed
    ("failed", FALLBACK, 3, 2),             # two tries from app.js, one from desk.js, all fail: the fixed credit
    ("timed_out", desk.FEED_CREDIT, 3, 2),  # app.js's first try passes its 6 s limit; its retry gets the answer
    ("timed_out_twice", FALLBACK, 3, 2),    # both tries pass the limit: the fixed credit (Codex, PR #50 note)
])
def test_strip_is_held_until_the_credit_is_known(stub_server, tmp_path, mode, expected_credit, asked, timers):
    task = (_own_health_timer(HELD).replace("__MODE__", mode).replace("__CREDIT__", json.dumps(desk.FEED_CREDIT))
            .replace("__CARD__", json.dumps(CARD)))
    r = _run_page(task, f"secondlook-strip-held-{mode}", stub_server, tmp_path)
    assert r["offHost"] == [], "no request leaves 127.0.0.1"
    # The match arrived with the answer, yet the strip stayed hidden: no match without its credit.
    assert r["held"]["stripHidden"] is True, "the strip showed a match before the credit was known"
    # Then it shows with the credit first and the fact-checker of the match after it.
    assert r["shown"]["credit"] == expected_credit + " This one: Example Fact Check."
    assert "Example Fact Check" in r["shown"]["who"]
    if mode == "timed_out":
        # The page gave up on its first request and asked again, and with no answer yet the strip stayed hidden.
        assert r["timedOut"] == {"fired": 1, "healthAsked": 3, "stripHidden": True}, r["timedOut"]
    if mode == "timed_out_twice":
        # One limit passed on each try; between them the strip stayed hidden, and after the second it showed with
        # the fixed credit before health answered. The late answers then changed nothing.
        assert r["timedOut"] == {"fired": [1, 1], "healthAsked": 3, "betweenHidden": True,
                                 "credit": FALLBACK + " This one: Example Fact Check."}, r["timedOut"]
    assert r["shown"]["healthAsked"] == asked, "health was asked once more on a failure, and no more than once"
    # The page's 6 s timers were the test's, one per app.js try, and none was left running.
    assert r["timers"] == {"started": timers, "pending": 0}, r["timers"]
    assert r["later"] == {"credit": expected_credit + " This one: Second Example Desk.", "stripHidden": False}
    assert r["none"] == {"credit": "", "stripHidden": True}
