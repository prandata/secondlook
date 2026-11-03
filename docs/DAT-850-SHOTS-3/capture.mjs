// DAT-850 SHOULD 4 and FIX 4 (PR #33 follow-up): the plain-language summary names a high detector score that raises no
// concern, and a low score keeps a short Proves line. Run through headless-run (muted), at 1280 px and 390 px.
// Server: python -m secondlook.web --port 8913 from this branch (the real ladder, the committed classifier_threshold.json
// and classifier_gate.json, whose v2 gate FAILed). Inputs, copied into DIR: one Stable Diffusion 1.x image from the L4
// dev split (DiffusionDB, item A036055fd, score 1.000) and the two stripped demo samples from samples/.
import fs from "node:fs";

const DIR = "/tmp/second-look-dat850-cap";
const OUT = DIR + "/shots";
const BASE = "http://127.0.0.1:8913";
const log = { offHost: [], states: {} };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const text = (page, sel) => page.$eval(sel, n => n.innerText).catch(() => null);

async function shots(page, name) {
  for (const [w, h, tag] of [[1280, 800, "desktop"], [390, 844, "phone"]]) {
    await page.setViewportSize({ width: w, height: h });
    await sleep(500);
    await page.screenshot({ path: `${OUT}/dat850s4-${name}-${tag}.png`, fullPage: true });
    // The answer block: the headline, the plain-language summary and the first clue under it.
    const top = page.locator("#result");
    await top.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${OUT}/dat850s4-${name}-top-${tag}.png` });
    const card = page.locator("li.clue", { hasText: "Community Forensics" }).first();
    await card.scrollIntoViewIfNeeded();
    await card.screenshot({ path: `${OUT}/dat850s4-${name}-card-${tag}.png` });
  }
  await page.setViewportSize({ width: 1280, height: 800 });
}

export async function run({ page }) {
  fs.mkdirSync(OUT, { recursive: true });
  page.on("request", req => { const u = new URL(req.url()); if (u.protocol !== "blob:" && u.hostname !== "127.0.0.1") log.offHost.push(req.url()); });
  // Nothing on this page plays sound; mute every media element anyway (owner rule, 16 Oct 2026).
  await page.addInitScript(() => document.addEventListener("play", e => { e.target.muted = true; e.target.pause(); }, true));
  for (const [name, file] of [["sd-above", "sd-dev-A036055fd.png"],
                              ["camera-sample", "06-stripped-pixel10pro-atlantic-sign.jpg"],
                              ["ai-sample", "07-stripped-firefly-mountain-landscape.jpg"]]) {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(BASE + "/", { waitUntil: "load" });
    await sleep(800);
    await page.setInputFiles("#file", `${DIR}/${file}`);
    await page.waitForFunction(() => !document.getElementById("result").hidden
                                     && !document.getElementById("result").hasAttribute("aria-busy"), null, { timeout: 600000 });
    await sleep(800);
    // A card under "Checked, nothing found" is folded; open it so the classifier card is on screen wherever it sits.
    await page.evaluate(() => { const q = document.getElementById("quiet"); if (q && !q.hidden) q.open = true; });
    const where = await page.evaluate(() => {
      const card = [...document.querySelectorAll("li.clue")].find(li => li.innerText.includes("Community Forensics"));
      return !card ? "none" : card.closest("#quiet") ? "Checked, nothing found" : "What we found";
    });
    log.states[name] = { file, answer: await text(page, "#answer"), explain: await text(page, "#explain-summary"),
                         classifierCardUnder: where, clues: await text(page, "#clues"), quiet: await text(page, "#quiet"),
                         meta: await text(page, "#meta") };
    await shots(page, name);
  }
  fs.writeFileSync(`${OUT}/dat850s4-capture.json`, JSON.stringify(log, null, 2));
  return { offHost: log.offHost.length, answers: Object.fromEntries(Object.entries(log.states).map(([k, v]) => [k, v.answer])) };
}
