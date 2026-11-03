// DAT-850 classifier gate status: the classifier card with the committed v2 FAIL status. Run through headless-run (muted).
// Server: python -m secondlook.web --port 8897 from the classifier-gate-status worktree (the real ladder, the committed
// classifier_threshold.json and classifier_gate.json). Inputs: one Stable Diffusion 1.x image from the L4 dev split (DiffusionDB) and two stripped demo samples, copied into DIR.
import fs from "node:fs";

const DIR = "/tmp/second-look-gate-cap";
const OUT = DIR + "/shots";
const BASE = "http://127.0.0.1:8897";
const log = { offHost: [], states: {} };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const text = (page, sel) => page.$eval(sel, n => n.innerText).catch(() => null);

async function cardShots(page, name) {
  for (const [w, h, tag] of [[1280, 800, "desktop"], [390, 844, "phone"]]) {
    await page.setViewportSize({ width: w, height: h });
    await sleep(500);
    await page.screenshot({ path: `${OUT}/dat850-${name}-${tag}.png`, fullPage: true });
    const card = page.locator("li.clue", { hasText: "Community Forensics" }).first();
    await card.scrollIntoViewIfNeeded();
    await card.screenshot({ path: `${OUT}/dat850-${name}-card-${tag}.png` });
  }
  await page.setViewportSize({ width: 1280, height: 800 });
}

export async function run({ page }) {
  fs.mkdirSync(OUT, { recursive: true });
  page.on("request", req => { const u = new URL(req.url()); if (u.protocol !== "blob:" && u.hostname !== "127.0.0.1") log.offHost.push(req.url()); });
  for (const [name, file] of [["sd-above", "sd-dev-00f19154.png"],
                              ["ai-sample", "07-stripped-firefly-mountain-landscape.jpg"],
                              ["camera-sample", "06-stripped-pixel10pro-atlantic-sign.jpg"]]) {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(BASE + "/", { waitUntil: "load" });
    await sleep(800);
    await page.setInputFiles("#file", `${DIR}/${file}`);
    await page.waitForFunction(() => !document.getElementById("result").hidden
                                     && !document.getElementById("result").hasAttribute("aria-busy"), null, { timeout: 110000 });
    await sleep(800);
    // A card under "Checked, nothing found" is folded; open it so the classifier card is on screen wherever it sits.
    await page.evaluate(() => { const q = document.getElementById("quiet"); if (q && !q.hidden) q.open = true; });
    log.states[name] = { file, answer: await text(page, "#answer"), reason: await text(page, "#reason"),
                         explain: await text(page, "#explain-summary"), clues: await text(page, "#clues"),
                         quiet: await text(page, "#quiet") };
    await cardShots(page, name);
  }
  fs.writeFileSync(`${OUT}/dat850-capture.json`, JSON.stringify(log, null, 2));
  return { offHost: log.offHost.length, answers: Object.fromEntries(Object.entries(log.states).map(([k, v]) => [k, v.answer])) };
}
