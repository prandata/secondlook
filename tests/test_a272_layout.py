"""A272 (DAT-850): three small layout fixes from the A269 design check of PR #55 (a reviewer, FIX @ 12fe9fb).

One test per fix, each measured in the rendered page and failing with its fix removed:
1. a batch row's facts line keeps each fact whole ("under 0.1 s" never splits at 740), and no line of it starts or
   ends with a visible "·"; the Receipt link still sits alone on its own line at 1440, 740 and 390;
2. the C2PA card's "Source" summary is at least 24 px tall at 390 (and 1440), the WCAG 2.5.8 target size;
3. a C2PA card headline keeps its date whole ("2024-12-14" never breaks after "2024-") at phone widths.

The page runs in headless-run (chrome-headless-shell, muted) against the stub server on 127.0.0.1, with the same
harness and answers as tests/test_a262_page_polish.py. Every request off 127.0.0.1 is refused and counted.
"""
from __future__ import annotations

import json

import pytest

from tests.test_a262_page_polish import AI, AI_HEAD, _run_page, stub_server  # noqa: F401 (stub_server is a fixture)

WIDTHS = [1440, 740, 390]
PHONES = [360, 375, 390, 414]

TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], out = { offHost, batch: {}, source: {}, date: {} };
  await page.context().route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/check") return route.fulfill({ json: __AI__ });
    // The batch: the stub's own rows, as the ladder writes them on the nine samples (4 checks, under 0.1 s).
    if (url.pathname === "/api/batch") {
      const res = await route.fetch(), body = await res.json();
      body.results.forEach(r => { r.rungs = ["c2pa", "iptc", "exif", "watermark"]; r.seconds = 0.04; });
      return route.fulfill({ response: res, json: body });
    }
    return route.continue();
  });

  // 1. The batch facts line at each width.
  for (const width of __WIDTHS__) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(base + "/batch");
    await page.setInputFiles("#files", ["__CAMERA_FILE__", "__AI_FILE__"]);
    await page.waitForFunction(() => document.querySelectorAll("#rows .receipt-link").length === 2, null,
                               { timeout: 60000 });
    const rows = [];
    for (const i of [0, 1]) {
      await page.evaluate(i => document.querySelectorAll("#rows .row-facts")[i].scrollIntoView({ block: "center" }), i);
      rows.push(await page.evaluate(i => {
        const f = document.querySelectorAll("#rows .row-facts")[i], line = f.querySelector(".facts-line");
        const link = f.querySelector(".receipt-link"), lb = link.getBoundingClientRect();
        // Every character of the cell's text (the facts, then the link), with the top of its line.
        const chars = [];
        const walk = document.createTreeWalker(f, NodeFilter.SHOW_TEXT);
        for (let n = walk.nextNode(); n; n = walk.nextNode()) {
          for (let k = 0; k < n.length; k++) {
            if (/\s/.test(n.data[k])) continue;
            const r = document.createRange(); r.setStart(n, k); r.setEnd(n, k + 1);
            chars.push({ c: n.data[k], top: Math.round(r.getBoundingClientRect().top), inLink: link.contains(n) });
          }
        }
        // Each fact: its text's lines, and whether the "·" drawn before it is what the eye sees at that spot.
        const facts = [...f.querySelectorAll(".fact")].map(s => {
          const b = s.getBoundingClientRect(), dot = parseFloat(getComputedStyle(s, "::before").width) || 0;
          const r = document.createRange(); r.selectNodeContents(s.lastChild);
          const lines = new Set([...r.getClientRects()].map(x => Math.round(x.top))).size;
          const hit = document.elementFromPoint(b.left + dot / 2, b.top + b.height / 2);
          const lb2 = line.getBoundingClientRect();
          return { text: s.textContent, lines, dotSeen: hit === s, top: Math.round(b.top),
                   startsLine: b.left < lb2.left - 1, textInside: b.right <= lb2.right + 1 };
        });
        return { text: f.textContent, before: link.previousSibling ? link.previousSibling.textContent : "",
                 linkAlone: chars.filter(c => !c.inLink).every(c => c.top < Math.round(lb.top)),
                 chars, facts };
      }, i));
    }
    out.batch[width] = rows;
  }

  // 2 and 3. The AI-made C2PA card: its "Source" summary and its headline's date.
  for (const width of [...new Set([1440, ...__PHONES__])]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(base + "/");
    await page.setInputFiles("#file", "__AI_FILE__");
    await page.waitForFunction(() => document.getElementById("answer").textContent === "Signed as AI-made",
                               null, { timeout: 30000 });
    Object.assign(out.date, { [width]: await page.evaluate(() => {
      const p = document.querySelector("#clues .clue .finding"), date = "2024-12-14";
      const tops = [];
      const walk = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
      let seen = "";
      for (let n = walk.nextNode(); n; n = walk.nextNode()) {
        for (let k = 0; k < n.length; k++) {
          const g = seen.length + k, start = (seen + n.data).indexOf(date);
          if (start < 0 || g < start || g >= start + date.length) continue;
          const r = document.createRange(); r.setStart(n, k); r.setEnd(n, k + 1);
          tops.push(Math.round(r.getBoundingClientRect().top));
        }
        seen += n.data;
      }
      const lh = parseFloat(getComputedStyle(p).lineHeight) || 24;
      return { text: p.textContent, dateLines: new Set(tops).size, dateChars: tops.length,
               lines: Math.round(p.getBoundingClientRect().height / lh) };
    }) });
    out.source[width] = await page.evaluate(() => {
      const s = document.querySelector("#clues .clue details.trust-src summary");
      return { text: s.textContent, h: s.getBoundingClientRect().height };
    });
  }
  return out;
}
"""


@pytest.fixture(scope="module")
def shot(stub_server, tmp_path_factory):
    task = (TASK.replace("__AI__", json.dumps(AI)).replace("__WIDTHS__", json.dumps(WIDTHS))
            .replace("__PHONES__", json.dumps(PHONES)))
    return _run_page(task, "secondlook-a272-layout", stub_server, tmp_path_factory.mktemp("a272"))


# ---- 1. the batch facts line --------------------------------------------------------------------------------

def test_batch_facts_keep_each_fact_whole_with_no_stray_dot(shot):
    assert shot["offHost"] == []
    for width in map(str, WIDTHS):
        for row in shot["batch"][width]:
            facts = row["facts"]
            assert [f["text"] for f in facts] == ["4 checks ran", "under 0.1 s"], (width, row["text"])
            for f in facts:
                assert f["lines"] == 1, (width, f, "a fact split across lines")
                assert f["textInside"], (width, f, "a fact cut off at the cell's right edge")
                # A fact that starts a line shows no dot; one that follows another on its line shows it.
                assert f["dotSeen"] is (not f["startsLine"]), (width, f)
            assert facts[0]["startsLine"], (width, facts)
            # No "·" in the text itself, so none can end a line or lead the Receipt.
            assert "·" not in row["text"] and not row["before"].rstrip().endswith("·"), (width, row["text"])
            assert row["text"].endswith("Receipt") and row["linkAlone"], (width, row)
    # At 740 the cell is too narrow for both facts: the time moves down whole, with no dot at its line's start.
    narrow = shot["batch"]["740"][0]["facts"]
    assert narrow[1]["top"] > narrow[0]["top"] and narrow[1]["startsLine"] and not narrow[1]["dotSeen"], narrow
    # At 1440 they share one line, with the dot between them.
    wide = shot["batch"]["1440"][0]["facts"]
    assert wide[1]["top"] == wide[0]["top"] and wide[1]["dotSeen"], wide


# ---- 2. the "Source" summary --------------------------------------------------------------------------------

def test_source_summary_is_a_24px_target(shot):
    for width in ("390", "1440"):
        s = shot["source"][width]
        assert s["text"] == "Source" and s["h"] >= 24, (width, s)


# ---- 3. the headline's date ---------------------------------------------------------------------------------

def test_card_headline_keeps_its_date_whole(shot):
    for width in map(str, PHONES + [1440]):
        d = shot["date"][width]
        assert d["text"] == AI_HEAD, (width, d["text"])
        assert d["dateChars"] == 10 and d["dateLines"] == 1, (width, d)
    # The headline does wrap at phone widths, so the date's line break is really exercised there.
    assert all(shot["date"][str(w)]["lines"] >= 3 for w in PHONES), {w: shot["date"][str(w)]["lines"] for w in PHONES}
