// DAT-850 batch view captures. Run through ~/bin/headless-run (muted). Inputs: the nine N1 demo samples.
// Nothing is held or slowed: the running shot is taken while the real request is in flight, so every "Took"
// line is the real time. Two servers on 127.0.0.1, both the real ladder: SL_BASE as installed, and SL_BASE_NOWM
// started with SECONDLOOK_VIDEOSEAL_WEIGHTS pointing at no file, so the watermark check could not run there.
// States, each at 1280, 740 and 390 px: the single-file page with its link, empty, running, done (nine files), a
// check that could not run (three files on the second server), a run of 23 files sent as two sets, and a folder
// whose one file has a long path (three or more lines at 1280 px) beside a copy of it, so both rows carry a tag.
// The capture fails (throws, after writing batch-capture.json with the reasons under "failures") when the sets note
// is missing at the start of a two-set run, a "Receipt" link's box is under 44 px, a row with tags has a file cell
// that does not span both grid rows (the three-column widths), or the first tag sits more than 10 px under the
// answer on any row, or the "·" before "Receipt" sits on a different line from the word (checked at the three shot
// widths and at 721, 760 and 800 px, where the facts line once wrapped and left the "·" dangling). The done state is
// also shot once at 760 px (`batch-3-done-760.png`).
import fs from "node:fs";

const OUT = process.env.SL_OUT || "/tmp/second-look-sl-shots";
const BASE = process.env.SL_BASE || "http://127.0.0.1:8851";
const BASE_NOWM = process.env.SL_BASE_NOWM || "http://127.0.0.1:8852";
const DIR = process.env.SL_SAMPLES || "/tmp/second-look-sl-in";  // a copy of the 9 demo sample files
const LONG = process.env.SL_LONG || "/tmp/second-look-sl-long";   // made here: the folder for the long-path run
const LONG_DIR = "sydney/day-03-harbour-bridge-and-opera-house-at-dusk";
const LONG_NAME = "IMG_20240114_183522_HDR_edited-final-v2.jpg";
const SETS_NOTE = "Files are sent in sets of up to 20 files and 100 MB, so near-duplicate copies are grouped only within each set.";
const WIDTHS = [[1280, 800, "desktop"], [740, 900, "tablet"], [390, 844, "phone"]];
// Widths between the tablet and the 800 px design edge: checked, not shot (the done state is shot once at 760 px).
// From 721 to 800 px the facts line wrapped and left a dangling "·" before "Receipt" (a reviewer, PR #34).
const CHECK_ONLY_WIDTHS = [[721, 900], [760, 900], [800, 900]];
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const log = { offHost: [], states: {}, failures: [] };
const fail = (why) => log.failures.push(why);

async function shots(page, name) {
  for (const [w, h, tag] of WIDTHS) {
    await page.setViewportSize({ width: w, height: h });
    await sleep(400);
    await page.screenshot({ path: `${OUT}/${name}-${tag}.png`, fullPage: true, timeout: 90000 });
    log.states[`${name}-${tag}`] = await page.evaluate(() => document.documentElement.scrollHeight);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
}

const asUpload = (path, name) => ({ name: name || path.split("/").pop(), mimeType: "image/jpeg", buffer: fs.readFileSync(path) });
const finished = (page) => page.waitForFunction(
  () => !document.getElementById("batch-result").hasAttribute("aria-busy"), null, { timeout: 600000 });
const rowsText = (page) => page.$$eval(".row", rows => rows.map(r => r.innerText.replace(/\s+/g, " ").trim()));

// Every visible control's height at the current width: links, buttons and the drop zone's label.
const targets = (page) => page.$$eval("a[href], button, label[for]", els => els
  .filter(e => e.offsetParent !== null && !e.classList.contains("skip"))
  .map(e => ({ what: (e.id || e.className || e.tagName) + ": " + e.textContent.trim().slice(0, 40),
               height: Math.round(e.getBoundingClientRect().height * 10) / 10 })));

// Each checked row's layout at the current width, in px from the row's top: where the first line of the file name,
// the headline and the facts line ("5 checks ran ...") start, and the gap from the lowest line of the answer and
// facts text (the cells the tags sit under) to the first tag. Also how many lines the name wraps to, the height of
// the row's "Receipt" box, whether the grid has more than one column, whether the "·" before "Receipt" sits on a
// different line from the word, and, on a row with tags, whether the file cell spans both grid rows.
const rowGeometry = (page) => page.$$eval(".row.done", rows => rows.map(r => {
  const rect = (t) => { const g = document.createRange(); g.selectNodeContents(t); return g.getClientRects()[0]; };
  const lines = (t) => { const g = document.createRange(); g.selectNodeContents(t);
                         return new Set([...g.getClientRects()].map(x => Math.round(x.top))).size; };
  const firstText = (n) => [...n.childNodes].find(c => c.nodeType === 3 && c.textContent.trim());
  const top = r.getBoundingClientRect().top, round = (x) => Math.round(x * 10) / 10;
  const walker = document.createTreeWalker(r, NodeFilter.SHOW_TEXT);
  let lowest = 0;
  for (let t = walker.nextNode(); t; t = walker.nextNode())
    if (t.textContent.trim() && t.parentElement.closest(".row-answer, .row-facts")) {
      const g = document.createRange(); g.selectNodeContents(t);
      lowest = Math.max(lowest, g.getBoundingClientRect().bottom);  // every line of a wrapped text, not the first
    }
  const tag = r.querySelector(".btag"), file = r.querySelector(".row-file"), link = r.querySelector(".receipt-link");
  const bullet = link && link.previousSibling, word = link && link.firstChild;
  const dangling = link && bullet && word ? Math.abs(rect(bullet).top - rect(word).top) > 3 : null;
  return { n: r.querySelector(".row-n").textContent,
           nameLines: lines(firstText(r.querySelector(".row-name"))),
           columns: getComputedStyle(r).gridTemplateColumns.split(" ").length,
           fileSpan: tag ? getComputedStyle(file).gridRowStart + " / " + getComputedStyle(file).gridRowEnd : null,
           receiptHeight: link ? round(link.getBoundingClientRect().height) : null,
           dangling,
           nameTop: round(rect(firstText(r.querySelector(".row-name"))).top - top),
           headlineTop: round(rect(firstText(r.querySelector(".row-headline"))).top - top),
           factsTop: round(rect(firstText(r.querySelector(".row-facts"))).top - top),
           gapAboveTag: tag ? round(tag.getBoundingClientRect().top - lowest) : null };
}));

// The widest near-duplicate tag against its row's width.
// The checks that fail the capture, on every checked row of the state at every width; returns the geometry.
async function checkRows(page, state) {
  const out = {};
  for (const [w, h] of [...WIDTHS, ...CHECK_ONLY_WIDTHS]) {
    await page.setViewportSize({ width: w, height: h });
    await sleep(300);
    const rows = out[w] = await rowGeometry(page);
    if (!rows.length) fail(`${state} at ${w} px: no checked rows`);
    for (const r of rows) {
      const at = `${state} at ${w} px, row ${r.n}`;
      if (r.receiptHeight === null) fail(`${at}: no Receipt link`);
      else if (r.receiptHeight < 44) fail(`${at}: the Receipt box is ${r.receiptHeight} px tall, under 44`);
      if (r.dangling) fail(`${at}: the "·" before Receipt is on a different line from the word`);
      if (r.gapAboveTag !== null && r.gapAboveTag > 10) fail(`${at}: the first tag is ${r.gapAboveTag} px under the answer, over 10`);
      if (r.fileSpan !== null && r.columns > 1 && !/\bspan 2\b/.test(r.fileSpan))  // grid-row: span 2 sets the start
        fail(`${at}: the row has tags but its file cell does not span both grid rows (grid-row ${r.fileSpan})`);
    }
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  return out;
}

const dupWidths = (page) => page.$$eval(".btag.dup", tags => tags.map(t => ({
  text: t.textContent, width: Math.round(t.getBoundingClientRect().width * 10) / 10,
  row: Math.round(t.closest(".row").getBoundingClientRect().width * 10) / 10 })));

export async function run({ page }) {
  fs.mkdirSync(OUT, { recursive: true });
  page.on("request", req => { const u = new URL(req.url()); if (u.protocol !== "blob:" && u.hostname !== "127.0.0.1") log.offHost.push(req.url()); });
  const files = fs.readdirSync(DIR).filter(f => f.endsWith(".jpg")).sort().map(f => `${DIR}/${f}`);
  log.files = files.map(f => f.split("/").pop());
  // The extra runs use the smallest samples (under 1 MB each): every file crosses the browser pipe as a buffer.
  const small = [...files].sort((a, b) => fs.statSync(a).size - fs.statSync(b).size).slice(0, 5);
  await page.setViewportSize({ width: 1280, height: 800 });

  // The single-file page, with its one link to the batch view.
  await page.goto(BASE + "/", { waitUntil: "load" });
  await sleep(600);
  await shots(page, "batch-0-main");
  log.mainLink = await page.$eval('a[href="/batch"]', a => a.textContent.trim());
  await page.setViewportSize({ width: 390, height: 844 });
  log.mainLinkHeight390 = await page.$eval('a[href="/batch"]', a => a.getBoundingClientRect().height);
  await page.setViewportSize({ width: 1280, height: 800 });

  await page.goto(BASE + "/batch", { waitUntil: "load" });
  await sleep(600);
  await shots(page, "batch-1-empty");
  await page.keyboard.press("Tab"); await page.keyboard.press("Tab");
  log.tab2 = await page.evaluate(() => document.activeElement.id);
  await page.keyboard.press("Tab");
  log.tab3 = await page.evaluate(() => document.activeElement.id);
  await page.setViewportSize({ width: 390, height: 844 });
  log.targetsEmpty390 = await targets(page);
  await page.setViewportSize({ width: 1280, height: 800 });

  // Nine files. The running shot is taken once the counter has passed 2 s, with the request still in flight.
  const t0 = Date.now();
  await page.setInputFiles("#files", files.map(f => asUpload(f)), { timeout: 600000 });
  await page.waitForFunction(() => /\b([2-9]|\d\d+) s so far/.test(document.getElementById("batch-progress").textContent),
                             null, { timeout: 30000 });
  log.runningText = await page.$eval("#batch-progress", n => n.innerText);
  log.runningStatus = await page.$eval("#batch-status", n => n.textContent);
  await shots(page, "batch-2-running");
  log.stillRunningAfterShots = await page.evaluate(() => document.getElementById("batch-result").hasAttribute("aria-busy"));
  await finished(page);
  log.nineSecondsInBrowser = (Date.now() - t0) / 1000;
  await sleep(500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "batch-3-done");
  await page.setViewportSize({ width: 760, height: 900 });
  await sleep(400);
  await page.screenshot({ path: `${OUT}/batch-3-done-760.png`, fullPage: true, timeout: 90000 });
  log.states["batch-3-done-760"] = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width: 1280, height: 800 });
  log.doneText = await page.$eval("#batch-progress", n => n.innerText);
  log.zipLabel = await page.$eval("#zip-go", n => n.textContent);
  log.rows = await rowsText(page);
  log.focus = await page.evaluate(() => document.activeElement.id);
  log.signedAiColour = await page.$eval(".row.signed_ai .row-headline", n => getComputedStyle(n).color);
  log.doneGeometry = await checkRows(page, "done");
  await page.setViewportSize({ width: 390, height: 844 });
  log.targetsDone390 = await targets(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  log.footerVsMain1280 = await page.evaluate(() => [document.querySelector("main").getBoundingClientRect().width,
                                                     document.querySelector("footer").getBoundingClientRect().width]);

  // headless-run's context does not accept downloads, so the page's own blob is read back and saved.
  await page.evaluate(() => {
    const make = URL.createObjectURL.bind(URL);
    URL.createObjectURL = (blob) => { window.__lastBlob = blob; return make(blob); };
  });
  await page.click("#zip-go");
  const b64 = await page.evaluate(async () => {
    const bytes = new Uint8Array(await window.__lastBlob.arrayBuffer());
    let s = ""; for (const x of bytes) s += String.fromCharCode(x);
    return btoa(s);
  });
  fs.writeFileSync(`${OUT}/second-look-receipts.zip`, Buffer.from(b64, "base64"));
  log.zipStatus = await page.$eval("#batch-status", n => n.textContent);
  log.rowReceiptLink = await page.$eval(".row .receipt-link", a => ({ text: a.textContent, download: a.download,
                                                                      blob: a.href.startsWith("blob:") }));

  // A check that could not run: the second server has no watermark weights.
  await page.goto(BASE_NOWM + "/batch", { waitUntil: "load" });
  await sleep(400);
  await page.setInputFiles("#files", small.slice(0, 3).map(f => asUpload(f)), { timeout: 600000 });
  await finished(page);
  await sleep(500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "batch-4-could-not-run");
  log.couldNotRunRows = await rowsText(page);

  // 23 files: the five smallest samples, then 18 renamed copies of them, so the page sends a set of 20 and a set of 3.
  const many = small.map(f => asUpload(f));
  for (let i = 0; many.length < 23; i++) many.push(asUpload(small[i % small.length], "copy-" + (i + 1 + "").padStart(2, "0")
                                                          + "-" + small[i % small.length].split("/").pop()));
  await page.goto(BASE + "/batch", { waitUntil: "load" });
  await sleep(400);
  const t1 = Date.now();
  await page.setInputFiles("#files", many, { timeout: 600000 });
  await page.waitForFunction(() => /of 23\./.test(document.getElementById("batch-progress").textContent), null, { timeout: 30000 });
  log.multiRunningText = await page.$eval("#batch-progress", n => n.innerText);
  log.multiRunningSetsNote = await page.$eval("#sets-note", n => (n.hidden ? "(hidden) " : "") + n.textContent.replace(/\s+/g, " ").trim());
  log.multiRunningSetsNoteVisible = await page.$eval("#sets-note", n => !n.hidden && n.getBoundingClientRect().height > 0);
  if (log.multiRunningSetsNote !== SETS_NOTE || !log.multiRunningSetsNoteVisible)
    fail(`two sets, at the start of the run: the sets note is missing or wrong ("${log.multiRunningSetsNote}")`);
  await finished(page);
  log.multiSecondsInBrowser = (Date.now() - t1) / 1000;
  await sleep(500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "batch-5-two-sets");
  log.multiDoneText = await page.$eval("#batch-progress", n => n.innerText);
  log.multiSetsNote = await page.$eval("#sets-note", n => (n.hidden ? "(hidden) " : "") + n.textContent.replace(/\s+/g, " ").trim());
  log.multiGroups = await page.$$eval("#dup-list li", lis => lis.map(li => li.textContent));
  log.multiRows = await rowsText(page);
  log.multiGeometry = await checkRows(page, "two sets");
  log.multiDupWidths1280 = await dupWidths(page);
  await page.setViewportSize({ width: 740, height: 900 });
  log.multiDupWidths740 = await dupWidths(page);
  await page.setViewportSize({ width: 390, height: 844 });
  log.multiDupWidths390 = await dupWidths(page);
  log.multiTargets390 = await targets(page);
  await page.setViewportSize({ width: 1280, height: 800 });

  // A folder, chosen with "Choose a folder": one file deep inside it under a long name, and at its top the same
  // picture with its metadata stripped, so the two rows are near-duplicates and both carry a tag. The long-named file
  // is the signed Firefly sample: its answer is one short line ("Signed as AI-made"), so a name that wraps further
  // than the answer would push its tag down if the file cell's extra height went into the first grid row.
  const sample = (name) => files.find(f => f.endsWith("/" + name));
  fs.rmSync(LONG, { recursive: true, force: true });
  fs.mkdirSync(`${LONG}/holiday-2024/${LONG_DIR}`, { recursive: true });
  fs.copyFileSync(sample("03-ai-firefly-mountain-landscape.jpg"), `${LONG}/holiday-2024/${LONG_DIR}/${LONG_NAME}`);
  fs.copyFileSync(sample("07-stripped-firefly-mountain-landscape.jpg"), `${LONG}/holiday-2024/IMG_0001.jpg`);
  await page.goto(BASE + "/batch", { waitUntil: "load" });
  await sleep(400);
  await page.setInputFiles("#folder", `${LONG}/holiday-2024`, { timeout: 600000 });
  await finished(page);
  await sleep(500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await shots(page, "batch-6-long-name");
  log.longRows = await rowsText(page);
  log.longGeometry = await checkRows(page, "long name");
  const longPath = `holiday-2024/${LONG_DIR}/${LONG_NAME}`;
  const longN = await page.$$eval(".row", (rows, p) => rows.findIndex(r => r.querySelector(".row-name").textContent === p) + 1, longPath);
  const longRow = longN ? log.longGeometry[1280].find(r => r.n === String(longN)) : null;
  if (!longRow) fail(`long name: no row shows ${longPath}`);
  else {
    if (longRow.nameLines < 3) fail(`long name at 1280 px: the name wraps to ${longRow.nameLines} lines, not three or more`);
    if (longRow.gapAboveTag === null) fail("long name at 1280 px: the row has no tag");
  }
  fs.rmSync(LONG, { recursive: true, force: true });

  fs.writeFileSync(`${OUT}/batch-capture.json`, JSON.stringify(log, null, 2));
  if (log.failures.length) throw new Error("capture checks failed:\n" + log.failures.join("\n"));
  return { ok: true, offHost: log.offHost.length };
}
