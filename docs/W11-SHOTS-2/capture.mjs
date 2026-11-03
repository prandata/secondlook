// W11 design pass, stage 2 captures (DAT-850). Inputs: python docs/W11-SHOTS/make_inputs.py /tmp/second-look-w11s2-cap.
// Run through headless-run (muted). A: the real ladder with the S6 index and the N1 samples, Glimmer on 127.0.0.1:8081.
// C: the demo stub. Each state at 1280 px and 390 px, full page, plus a 1280 x 800 shot of the receipt row
// for the states that download a receipt. The downloads are saved and their first bytes logged.
import fs from "node:fs";

const DIR = "/tmp/second-look-w11s2-cap";
const OUT = DIR + "/shots";
const A = "http://127.0.0.1:8799", C = "http://127.0.0.1:8798";
const CAPTION = "Taken on an iPhone 15 Pro in Paris, 2026-10-01";
const log = { offHost: [], states: {} };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function shots(page, name, { receipt = false, fold = false } = {}) {
  await page.setViewportSize({ width: 1280, height: 800 });
  await sleep(400);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${OUT}/w11s2-${name}-desktop.png`, fullPage: true });
  if (fold) await page.screenshot({ path: `${OUT}/w11s2-${name}-first-screen.png` });
  if (receipt) {
    await page.$eval("#receipt", n => n.scrollIntoView({ block: "center" }));
    await sleep(300);
    await page.screenshot({ path: `${OUT}/w11s2-${name}-receipt-desktop.png` });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await sleep(400);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${OUT}/w11s2-${name}-phone.png`, fullPage: true });
  if (receipt) {
    await page.$eval("#receipt", n => n.scrollIntoView({ block: "center" }));
    await sleep(300);
    await page.screenshot({ path: `${OUT}/w11s2-${name}-receipt-phone.png` });
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await sleep(300);
}

async function open(page, base) {
  await page.goto(base + "/", { waitUntil: "load" });
  await page.waitForFunction(() => !document.getElementById("caps").hidden || document.getElementById("stub").hidden === false,
                             null, { timeout: 15000 });
  await sleep(600);
}

async function waitAnswer(page) {
  await page.waitForFunction(() => !document.getElementById("result").hidden
                                   && !document.getElementById("result").hasAttribute("aria-busy"), null, { timeout: 120000 });
  await sleep(800);
}

const text = (page, sel) => page.$eval(sel, n => n.innerText).catch(() => null);
const style = (page, sel, prop) => page.$eval(sel, (n, p) => getComputedStyle(n)[p], prop).catch(() => null);

async function download(page, button) {
  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 120000 }), page.click(button)]);
  const path = `${OUT}/${dl.suggestedFilename()}`;
  await dl.saveAs(path);
  await page.waitForFunction(() => !document.getElementById("receipt").hasAttribute("aria-busy"), null, { timeout: 120000 });
  await sleep(500);
  const head = fs.readFileSync(path).subarray(0, 8).toString("latin1");
  return { file: dl.suggestedFilename(), bytes: fs.statSync(path).size, head, status: await text(page, "#receipt-status") };
}

async function hoverFill(page, sel) {
  await page.hover(sel);
  await sleep(250);
  return { background: await style(page, sel, "backgroundColor"), color: await style(page, sel, "color") };
}

export async function run({ browser }) {
  fs.mkdirSync(OUT, { recursive: true });
  // Its own context in headless-run's muted browser, so the receipt downloads can be saved and checked.
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  page.on("request", req => { const u = new URL(req.url()); if (u.protocol !== "blob:" && u.hostname !== "127.0.0.1") log.offHost.push(req.url()); });
  await page.setViewportSize({ width: 1280, height: 800 });

  // 1. empty
  await open(page, A);
  log.health = await page.evaluate(() => fetch("/api/health").then(r => r.json()).then(h => ({ capabilities: h.capabilities, glimmer: h.details.glimmer })));
  await shots(page, "1-empty", { fold: true });

  // 2. caption concern, then the JSON receipt of that file and caption
  await page.fill("#caption", CAPTION);
  await page.setInputFiles("#file", `${DIR}/street-canon.jpg`);
  await waitAnswer(page);
  log.states["2"] = { answer: await text(page, "#answer"), meta: await text(page, "#meta"),
                      versionTitle: await page.$eval("#meta .version", n => n.title),
                      pdfShown: await page.$eval("#receipt-pdf", n => !n.hidden) };
  log.states["2"].json = await download(page, "#receipt-json");
  log.states["2"].pdf = await download(page, "#receipt-pdf");
  log.states["2"].againHover = await hoverFill(page, "#again-go");
  await page.mouse.move(0, 0);
  await shots(page, "2-caption-concern-receipt", { receipt: true, fold: true });

  // 3. sample 01, a signed camera capture: the signed tag's fill, and the sample's PDF receipt
  await page.click("#again-go");
  await sleep(400);
  await page.click('#gallery button[data-id="01"]');
  await waitAnswer(page);
  log.states["3"] = { answer: await text(page, "#answer"), signedTag: { background: await style(page, ".tag.strength.signed", "backgroundColor"),
                      color: await style(page, ".tag.strength.signed", "color") } };
  log.states["3"].pdf = await download(page, "#receipt-pdf");
  await page.mouse.move(0, 0);
  await shots(page, "3-sample-signed-receipt", { receipt: true });

  // 4. the deep check on the caption concern: the footer's short hash
  await page.click("#again-go");
  await sleep(400);
  await page.fill("#caption", CAPTION);
  await page.setInputFiles("#file", `${DIR}/street-canon.jpg`);
  await waitAnswer(page);
  log.states["4"] = { timerCopy: null, invHover: await hoverFill(page, "#inv-go") };
  await page.mouse.move(0, 0);
  const t0 = Date.now();
  await page.click("#inv-go");
  await sleep(3000);
  log.states["4"].timerCopy = await text(page, "#inv-time");
  await page.waitForFunction(() => !document.getElementById("investigate").hasAttribute("aria-busy"), null, { timeout: 420000 });
  await sleep(1500);
  Object.assign(log.states["4"], { ms: Date.now() - t0, status: await text(page, "#inv-status"), invMeta: await text(page, "#inv-meta"),
    invVersionTitle: await page.$eval("#inv-meta .version", n => n.title).catch(() => null),
    invMetaLines: await page.$eval("#inv-meta", n => Math.round(n.getBoundingClientRect().height / parseFloat(getComputedStyle(n).lineHeight || "18"))) });
  await page.setViewportSize({ width: 390, height: 844 });
  await sleep(400);
  log.states["4"].invMetaPhone = await page.$eval("#inv-meta", n => ({ height: Math.round(n.getBoundingClientRect().height),
    versionTop: Math.round(n.querySelector(".version").getBoundingClientRect().top), versionHeight: Math.round(n.querySelector(".version").getBoundingClientRect().height) }));
  await shots(page, "4-investigator-done");
  await page.$eval("#inv-meta", n => n.scrollIntoView({ block: "center" }));
  await page.screenshot({ path: `${OUT}/w11s2-4-footer-hash-desktop.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.$eval("#inv-meta", n => n.scrollIntoView({ block: "center" }));
  await sleep(300);
  await page.screenshot({ path: `${OUT}/w11s2-4-footer-hash-phone.png` });
  await page.setViewportSize({ width: 1280, height: 800 });

  // 5. the demo stub: the demo tag's fill
  await open(page, C);
  await page.setInputFiles("#file", `${DIR}/street-canon.jpg`);
  await waitAnswer(page);
  log.states["5"] = { answer: await text(page, "#answer"), demoTag: { background: await style(page, "#demo-tag", "backgroundColor"),
                      color: await style(page, "#demo-tag", "color") } };
  log.states["5"].json = await download(page, "#receipt-json");
  await shots(page, "5-demo-stub", { receipt: true });

  fs.writeFileSync(`${OUT}/w11s2-capture.json`, JSON.stringify(log, null, 1));
  await context.close();
  return { offHost: log.offHost.length, states: Object.keys(log.states) };
}
