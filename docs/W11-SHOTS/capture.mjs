// W11 design pass, stage 1 captures (DAT-850). Inputs: docs/W11-SHOTS/make_inputs.py /tmp/second-look-w11-cap. Run through headless-run (muted). Seven states, desktop 1280 and
// phone 390, plus the first screen (1280 x 800) for the two states the answer-first fix is about.
// Fix round 2 (a reviewer @ 73b2d61) adds 2b (D: the real ladder with the Video Seal weights path pointing at no
// file, so the watermark rung raises) and 2c (the same answer with one card's does_not_prove blanked in the browser,
// to show the "Missing:" text; the server is not changed).
import fs from "node:fs";

const OUT = "/tmp/second-look-w11-cap/shots";
const DIR = "/tmp/second-look-w11-cap";
const A = "http://127.0.0.1:8794", B = "http://127.0.0.1:8795", C = "http://127.0.0.1:8796", D = "http://127.0.0.1:8891";
const log = { offHost: [], states: {} };

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function shots(page, name, { fold = false } = {}) {
  await page.setViewportSize({ width: 1280, height: 800 });
  await sleep(400);
  await page.screenshot({ path: `${OUT}/w11-${name}-desktop.png`, fullPage: true });
  if (fold) await page.screenshot({ path: `${OUT}/w11-${name}-first-screen.png`, fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  await sleep(400);
  await page.screenshot({ path: `${OUT}/w11-${name}-phone.png`, fullPage: true });
  await page.setViewportSize({ width: 1280, height: 800 });
  await sleep(300);
}

async function open(page, base) {
  await page.goto(base + "/", { waitUntil: "load" });
  await page.waitForFunction(() => !document.getElementById("caps").hidden || document.getElementById("stub").hidden === false,
                             null, { timeout: 15000 });
  await sleep(600);
}

async function checkFile(page, file, caption) {
  await page.fill("#caption", caption || "");
  await page.setInputFiles("#file", `${DIR}/${file}`);
}

async function waitAnswer(page) {
  await page.waitForFunction(() => !document.getElementById("result").hidden
                                   && !document.getElementById("result").hasAttribute("aria-busy"), null, { timeout: 120000 });
  await sleep(800);
}

const text = (page, sel) => page.$eval(sel, n => n.innerText).catch(() => null);

export async function run({ page }) {
  fs.mkdirSync(OUT, { recursive: true });
  page.on("request", req => { const u = new URL(req.url()); if (u.protocol !== "blob:" && u.hostname !== "127.0.0.1") log.offHost.push(req.url()); });
  await page.setViewportSize({ width: 1280, height: 800 });

  // 1. empty
  await open(page, A);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "1-empty", { fold: true });

  // 1b. caption over the limit: realistic pasted text, 2,001 characters
  const sentence = "Residents said the water rose past the second step of the town hall before dawn, and the bridge on Main Street was closed by seven. ";
  let long = "";
  while (Array.from(long.trim()).length < 2001) long += sentence;
  long = Array.from(long.trim()).slice(0, 2001).join("");
  await checkFile(page, "street-canon.jpg", long);
  await sleep(600);
  log.states["1b"] = { status: await text(page, "#status"), error: await text(page, "#caption-error"), count: await text(page, "#caption-count") };
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "1b-caption-over-limit");

  // 2. caption concern
  await open(page, A);
  await checkFile(page, "street-canon.jpg", "Taken on an iPhone 15 Pro in Paris, 2026-10-01");
  await waitAnswer(page);
  log.states["2"] = { bar: await text(page, "#again-bar"), answer: await text(page, "#answer"), reason: await text(page, "#reason"),
                      meaning: await text(page, "#meaning"), summaryHidden: await page.$eval("#summary", n => n.hidden),
                      explain: await text(page, "#explain-summary"),
                      reasonTop: await page.$eval("#reason", n => Math.round(n.getBoundingClientRect().top + window.scrollY)),
                      rungs: await text(page, "#rungs"), clues: await text(page, "#clues"), quiet: await text(page, "#quiet-title"),
                      scrollY: await page.evaluate(() => window.scrollY),
                      answerTop: await page.$eval("#answer", n => Math.round(n.getBoundingClientRect().top)) };
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "2-caption-concern", { fold: true });

  // 3 and 4. the deep check on the Gatlinburg screenshot with its post text, Glimmer on 127.0.0.1:8081
  await page.click("#again-go");
  await sleep(400);
  await checkFile(page, "shot-3-gatlinburg.png", "Photo shows Gatlinburg, Tennessee, flooded on Sept. 30, 2024.");
  await waitAnswer(page);
  await page.click("#inv-go");
  await sleep(8000);
  log.states["3"] = { time: await text(page, "#inv-time"), status: await text(page, "#inv-status") };
  await shots(page, "3-investigator-running");
  const t0 = Date.now();
  await page.waitForFunction(() => !document.getElementById("investigate").hasAttribute("aria-busy"), null, { timeout: 420000 });
  log.states["4"] = { ms: Date.now() - t0 };
  await sleep(1500);
  Object.assign(log.states["4"], {
    status: await text(page, "#inv-status"), factcheck: await text(page, "#factcheck"), answer: await text(page, "#answer"),
    rungs: await text(page, "#rungs"), lines: await text(page, "#inv-lines"), cards: await text(page, "#inv-cards"),
    summaryHidden: await page.$eval("#summary", n => n.hidden), explain: await text(page, "#explain-summary"),
    stepsTitle: await text(page, "#inv-steps-title"),
    cantTell: await page.evaluate(() => (document.body.innerText.match(/can't tell how/gi) || []).length),
    rerun: await text(page, "#inv-rerun-title"), focus: await page.evaluate(() => document.activeElement && document.activeElement.id),
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "4-investigator-done", { fold: true });

  // 5. no Glimmer server answers
  await open(page, B);
  await checkFile(page, "street-canon.jpg", "Taken on an iPhone 15 Pro in Paris, 2026-10-01");
  await waitAnswer(page);
  await page.click("#inv-go");
  await page.waitForFunction(() => !document.getElementById("investigate").hasAttribute("aria-busy"), null, { timeout: 120000 });
  await sleep(800);
  log.states["5"] = { status: await text(page, "#inv-status") };
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "5-investigator-not-set-up");

  // 5b. the demo stub (409)
  await open(page, C);
  await checkFile(page, "street-canon.jpg", "");
  await waitAnswer(page);
  await page.click("#inv-go");
  await page.waitForFunction(() => !document.getElementById("investigate").hasAttribute("aria-busy"), null, { timeout: 60000 });
  await sleep(800);
  log.states["5b"] = { status: await text(page, "#inv-status"), answer: await text(page, "#answer") };
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "5b-investigator-stub-409");

  // 2b. a check that could not run: the watermark rung without its weights, on the caption concern
  await open(page, D);
  await checkFile(page, "street-canon.jpg", "Taken on an iPhone 15 Pro in Paris, 2026-10-01");
  await waitAnswer(page);
  log.states["2b"] = { answer: await text(page, "#answer"), rungs: await text(page, "#rungs"),
                       rungsClass: await page.$eval("#rungs", n => n.className),
                       rungsColour: await page.$eval("#rungs", n => getComputedStyle(n).color),
                       reasonTagColour: await page.$eval(".tag.reason-tag", n => getComputedStyle(n).color),
                       badgeColour: await page.$eval(".badge", n => getComputedStyle(n).color) };
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "2b-check-could-not-run", { fold: true });

  // 2c. the "Missing:" text: the same answer, the EXIF card's does_not_prove blanked in the browser only
  await page.route("**/api/check*", async route => {
    const res = await route.fetch();
    const body = await res.json();
    for (const ev of body.evidence) if (ev.rung === "exif") ev.does_not_prove = "";
    await route.fulfill({ response: res, json: body });
  });
  await open(page, D);
  await checkFile(page, "street-canon.jpg", "Taken on an iPhone 15 Pro in Paris, 2026-10-01");
  await waitAnswer(page);
  log.states["2c"] = { missing: await text(page, ".clue .pd dd.missing"),
                       missingColour: await page.$eval(".clue .pd dd.missing", n => getComputedStyle(n).color) };
  await page.unroute("**/api/check*");
  await page.$eval(".clue .pd dd.missing", n => n.scrollIntoView({ block: "center" }));
  await shots(page, "2c-missing-limit");

  fs.writeFileSync(`${OUT}/w11-capture.json`, JSON.stringify(log, null, 1));
  return { offHost: log.offHost.length, states: Object.keys(log.states) };
}
