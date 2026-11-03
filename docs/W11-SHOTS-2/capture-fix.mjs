// W11 stage 2 fix round (DAT-850, designer check @ 4e755a2): F3, S-a and S-b. Inputs: python
// docs/W11-SHOTS/make_inputs.py /tmp/second-look-w11fix-cap. Run through headless-run (muted).
// D: the real ladder with SECONDLOOK_VIDEOSEAL_WEIGHTS pointing at no file, so the watermark rung raises
// NotInstalled ("not installed"). Each state at 1280 px and 390 px, full page, plus a 1280 x 800 and a
// 390 x 844 shot of the line the fix is about.
//   2b  a check could not run, with its reason (S-a, S-b)
//   2d  the same answer with the reason blanked in the browser, as for an error type not on the fixed list:
//       the line ends "The cause is in the server log." (S-b; the server is not changed)
//   2e  the receipt's answer differs from the screen: the X-Secondlook-Answer header forced to "cant_tell"
//       in the browser, as 2c forced the Missing text (F3; the server cannot produce it in practice)
import fs from "node:fs";

const DIR = "/tmp/second-look-w11fix-cap";
const OUT = DIR + "/shots";
const D = "http://127.0.0.1:8893";
const CAPTION = "Taken on an iPhone 15 Pro in Paris, 2026-10-01";
const log = { offHost: [], states: {} };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function shots(page, name, focus) {
  for (const [w, h, tag] of [[1280, 800, "desktop"], [390, 844, "phone"]]) {
    await page.setViewportSize({ width: w, height: h });
    await sleep(400);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `${OUT}/w11s2-${name}-${tag}.png`, fullPage: true });
    await page.$eval(focus, n => n.scrollIntoView({ block: "center" }));
    await sleep(300);
    await page.screenshot({ path: `${OUT}/w11s2-${name}-line-${tag}.png` });
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await sleep(300);
}

async function open(page) {
  await page.goto(D + "/", { waitUntil: "load" });
  await page.waitForFunction(() => !document.getElementById("caps").hidden, null, { timeout: 15000 });
  await sleep(600);
}

async function check(page) {
  await page.fill("#caption", CAPTION);
  await page.setInputFiles("#file", `${DIR}/street-canon.jpg`);
  await page.waitForFunction(() => !document.getElementById("result").hidden
                                   && !document.getElementById("result").hasAttribute("aria-busy"), null, { timeout: 120000 });
  await sleep(800);
}

const text = (page, sel) => page.$eval(sel, n => n.innerText).catch(() => null);
const look = (page, sel) => page.$eval(sel, n => {
  const s = getComputedStyle(n);
  return { color: s.color, weight: s.fontWeight, display: s.display };
}).catch(() => null);

async function rungsLog(page) {
  return { text: await text(page, "#rungs"), paragraphClass: await page.$eval("#rungs", n => n.className),
           paragraph: await look(page, "#rungs"), ranSpan: await look(page, "#rungs span:not(.warn)"),
           failedSpan: await look(page, "#rungs span.failed"),
           failedTop: await page.$eval("#rungs", n => [...n.children].map(c => Math.round(c.getBoundingClientRect().top))) };
}

export async function run({ browser }) {
  fs.mkdirSync(OUT, { recursive: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  page.on("request", req => { const u = new URL(req.url()); if (u.protocol !== "blob:" && u.hostname !== "127.0.0.1") log.offHost.push(req.url()); });
  await page.setViewportSize({ width: 1280, height: 800 });

  // 2b. the watermark rung without its weights, on the caption concern
  await open(page);
  await check(page);
  log.states["2b"] = { answer: await text(page, "#answer"), ...await rungsLog(page) };
  await page.mouse.move(0, 0);
  await shots(page, "2b-check-could-not-run", "#rungs");

  // 2d. the same answer with no reason for the failed check (blanked in the browser only)
  await page.route("**/api/check*", async route => {
    const res = await route.fetch();
    const body = await res.json();
    log.states["2d"] = { serverWhy: body.rungs_failed_why, serverFailed: body.rungs_failed };
    body.rungs_failed_why = body.rungs_failed.map(() => null);
    await route.fulfill({ response: res, json: body });
  });
  await open(page);
  await check(page);
  await page.unroute("**/api/check*");
  Object.assign(log.states["2d"], await rungsLog(page));
  await page.mouse.move(0, 0);
  await shots(page, "2d-check-could-not-run-no-reason", "#rungs");

  // 2e. a receipt whose answer differs from the one on screen (the header forced in the browser only)
  await open(page);
  await check(page);
  await page.route("**/api/receipt*", async route => {
    const res = await route.fetch();
    log.states["2e"] = { serverAnswer: res.headers()["x-secondlook-answer"] };
    await route.fulfill({ response: res, headers: { ...res.headers(), "x-secondlook-answer": "cant_tell" } });
  });
  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 120000 }), page.click("#receipt-json")]);
  await dl.saveAs(`${OUT}/${dl.suggestedFilename()}`);
  await page.waitForFunction(() => !document.getElementById("receipt").hasAttribute("aria-busy"), null, { timeout: 120000 });
  await sleep(500);
  await page.unroute("**/api/receipt*");
  Object.assign(log.states["2e"], {
    answer: await text(page, "#answer"), status: await text(page, "#receipt-status"),
    statusRole: await page.$eval("#receipt-status", n => [n.getAttribute("role"), n.getAttribute("aria-live")]),
    saved: await look(page, "#receipt-status"), warn: await look(page, "#receipt-status .warn"),
    warnText: await text(page, "#receipt-status .warn") });
  await page.mouse.move(0, 0);
  await shots(page, "2e-receipt-differs", "#receipt");

  fs.writeFileSync(`${OUT}/w11s2-capture-fix.json`, JSON.stringify(log, null, 1));
  await context.close();
  return { offHost: log.offHost.length, states: Object.keys(log.states) };
}
