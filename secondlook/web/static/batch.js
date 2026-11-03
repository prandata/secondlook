(function (root) {
  // The batch view (DAT-850): a folder or many files in, one answer row and one receipt per file out.
  // It sends the files to POST /api/batch?receipt=1 on this computer in sets of up to 20 files and 100 MB (the
  // API's limits), and shows each answer in the single-file page's words. tests/test_batch_page.py checks these words match app.js.
  // The words, the headline and the receipt bundle are plain functions, exported as SecondLookBatch so the
  // tests run this file's own code; the page's wiring below runs only in a browser.
  const ANSWERS = {
    signed_ai:     { label: "Signed as AI-made" },
    signed_camera: { label: "Signed as a camera capture" },
    concerns:      { label: "Reasons to look closer" },
    cant_tell:     { label: "Can't tell" },
  };
  const RUNGS = { ladder: "Ladder", c2pa: "C2PA", iptc: "IPTC", exif: "EXIF", watermark: "Watermark",
                  classifier: "Classifier", origin: "Origin", claim: "Claim check", context: "Caption context" };
  const COUNT = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"];
  const concernLabel = (n) => !(n > 0) ? ANSWERS.concerns.label
    : (COUNT[n] || n.toLocaleString("en")) + (n === 1 ? " reason" : " reasons") + " to look closer";

  // The API's limits (secondlook/web/app.py): 20 files and 100 MB a request, 25 MB a file.
  const MAX_FILES = 20, MAX_BYTES = 25 * 1024 * 1024, MAX_SET_BYTES = 100 * 1024 * 1024;
  const IMAGE = [".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".gif", ".tif", ".tiff"];
  const NOT_IMAGE = "Not an image file (JPEG, PNG, WebP, HEIC, AVIF, GIF or TIFF).";

  const suffix = (name) => { const i = name.lastIndexOf("."); return i < 0 ? "" : name.slice(i).toLowerCase(); };
  const plural = (n, one, many) => n.toLocaleString("en") + " " + (n === 1 ? one : many);
  const seconds = (s) => typeof s !== "number" ? "" : s < 0.1 ? "under 0.1 s" : s.toFixed(1) + " s";
  const pad = (n) => String(n).padStart(4, "0");
  const rowList = (ns) => (ns.length === 1 ? "row " : "rows ") + ns.join(", ");

  // A row's near-duplicate tag names at most six other rows, then "and N more"; the list under the rows names
  // every row in the group. (A set of 20 can make a group of 20, which would need 19 numbers on one pill.)
  const DUP_SHOWN = 6;
  const dupTag = (ns) => "Near-duplicate of " + (ns.length <= DUP_SHOWN ? rowList(ns)
    : "rows " + ns.slice(0, DUP_SHOWN).join(", ") + " and " + (ns.length - DUP_SHOWN).toLocaleString("en") + " more");

  // The headline in the single-file page's words: the concern headline counts the ladder's own concern cards.
  function headline(r) {
    if (r.answer === "concerns") return concernLabel(Array.isArray(r.concern_cards) ? r.concern_cards.length : 0);
    return (ANSWERS[r.answer] || { label: r.answer }).label;
  }

  // The first concern card's finding, word for word, as the single-file page shows it under the headline. The
  // single-file page adds ", listed first below" to the count; this page has no list below, so it stops there.
  function concernReason(evidence, concernCards) {
    const cards = (Array.isArray(concernCards) ? concernCards : []).map(i => (evidence || [])[i])
      .filter(ev => ev && typeof ev.finding === "string" && ev.finding.trim());
    if (!cards.length) return "";
    const more = cards.length - 1;
    return cards[0].finding.trim() + (more ? " And " + more + " more " + (more === 1 ? "reason" : "reasons") + "." : "");
  }

  // The bundle's files: each receipt byte for byte as the server wrote it, its .sha256 beside it (sha256sum
  // format, as `secondlook check --receipt` writes), and summary.json with one row per file.
  // entries: [{ n, path, group, result }] (result: the API's entry for that file); groups: [{ group, files: [n] }].
  function bundleFiles(entries, groups) {
    const files = [], rows = [];
    for (const e of entries) {
      const r = e.result || {};
      const name = r.receipt_json ? pad(e.n) + "-" + r.receipt_name : null;
      if (name) {
        files.push({ name: "receipts/" + name, text: r.receipt_json });
        files.push({ name: "receipts/" + name + ".sha256", text: r.receipt_sha256 + "  " + name + "\n" });
      }
      rows.push({ n: e.n, path: e.path, status: r.status, answer: r.status === 200 ? r.answer : null,
                  headline: r.status === 200 ? headline(r) : null, error: r.error || null,
                  checks_ran: r.rungs || [], checks_could_not_run: r.rungs_failed || [],
                  seconds: typeof r.seconds === "number" ? r.seconds : null, near_duplicate_group: e.group || null,
                  receipt: name ? "receipts/" + name : null, receipt_sha256: r.receipt_sha256 || null });
    }
    const summary = { schema: "second-look-batch-page/1", files: rows, near_duplicate_groups: groups || [] };
    files.push({ name: "summary.json", text: JSON.stringify(summary, null, 2) + "\n" });
    return files.map(f => ({ name: "second-look-receipts/" + f.name, text: f.text }));
  }

  // --- sending --------------------------------------------------------------------------------------
  // Sets of at most 20 files and 100 MB. A file over 25 MB or not an image is refused before this, as the API would.
  // list: [{ file: { size } }]; the sets note on the page (batch.html, #sets-note) describes exactly this split.
  function sets(list) {
    const out = [];
    let cur = [], bytes = 0;
    for (const e of list) {
      if (cur.length && (cur.length === MAX_FILES || bytes + e.file.size > MAX_SET_BYTES)) { out.push(cur); cur = []; bytes = 0; }
      cur.push(e);
      bytes += e.file.size;
    }
    if (cur.length) out.push(cur);
    return out;
  }

  root.SecondLookBatch = { headline, concernReason, bundleFiles, dupTag, sets };
  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const intake = $("batch"), drop = $("bdrop"), filesIn = $("files"), folderIn = $("folder");
  const out = $("batch-result"), rowsEl = $("rows"), progress = $("batch-progress"), status = $("batch-status");
  const zipGo = $("zip-go");
  let entries = [], groupsOut = [], links = [], runId = 0, ticker = null;

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  // The stub banner shows exactly when the rows on screen include an answer from the stub engine, whatever health
  // said or failed to say, and nothing latches it: "Check more" and a batch of ladder answers each hide it again
  // (A278, Codex FAIL on PR #55 @ fa8367b; the single-file page's rule, A273). With no answer on screen it follows
  // health, and stays hidden while health is unknown, so it never flashes "canned" on the ladder engine.
  let healthStub = false;
  const stubBanner = () => {
    const answered = out.hidden ? [] : entries.filter(e => e.result && e.result.status === 200);
    $("stub").hidden = !(answered.length ? answered.some(e => e.result.engine !== "ladder") : healthStub);
  };
  fetch("/api/health").then(r => r.json()).then(h => { healthStub = h.engine === "stub"; stubBanner(); }).catch(() => {});

  // --- files in -------------------------------------------------------------------------------------
  // A dropped folder is walked with the entries API; readEntries returns a folder in pieces, so read until empty.
  function walk(entry, path) {
    if (entry.isFile) return new Promise(res => entry.file(f => res([{ file: f, path: path + f.name }]), () => res([])));
    if (!entry.isDirectory) return Promise.resolve([]);
    const reader = entry.createReader(), found = [];
    return new Promise(res => {
      const more = () => reader.readEntries(async batch => {
        if (!batch.length) return res((await Promise.all(found)).flat());
        for (const e of batch) found.push(walk(e, path + entry.name + "/"));
        more();
      }, () => res((Promise.all(found)).then(x => x.flat())));
      more();
    });
  }

  async function fromDrop(dt) {
    const items = Array.from(dt.items || []).map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (!items.length) return Array.from(dt.files || []).map(f => ({ file: f, path: f.name }));
    return (await Promise.all(items.map(e => walk(e, "")))).flat();
  }

  // Hidden files (.DS_Store and the like) are left out silently; anything else that is not an image gets a row.
  function start(picked) {
    picked = picked.filter(p => !p.path.split("/").some(part => part.startsWith(".")))
                   .sort((a, b) => a.path.localeCompare(b.path, "en", { numeric: true }));
    if (!picked.length) {
      status.textContent = "No files found there. Drop images, or a folder that holds them.";
      return;
    }
    const id = ++runId;
    forget();
    entries = picked.map((p, i) => ({ n: i + 1, file: p.file, path: p.path, result: null }));
    rowsEl.replaceChildren(...entries.map(rowFor));
    $("dups").hidden = true;
    $("sets-note").hidden = true;
    $("batch-meta").textContent = "";
    zipGo.disabled = true;
    intake.hidden = true;
    out.hidden = false;
    out.setAttribute("aria-busy", "true");
    $("batch-result-title").focus({ preventScroll: true });
    out.scrollIntoView({ block: "start" });
    run(id);
  }

  // --- the rows -------------------------------------------------------------------------------------
  function rowFor(e) {
    const li = el("li", "row waiting");
    const file = el("div", "row-file");
    file.append(el("span", "row-n", String(e.n)), el("span", "row-name", e.path));
    const answer = el("div", "row-answer");
    answer.append(el("p", "row-headline", "Waiting"));
    const facts = el("p", "row-facts", "");
    const tags = el("div", "row-tags");
    li.append(file, answer, facts, tags);
    e.li = li;
    return li;
  }

  function setRow(e) {
    const r = e.result, li = e.li;
    const head = li.querySelector(".row-headline"), answer = li.querySelector(".row-answer");
    const facts = li.querySelector(".row-facts"), tags = li.querySelector(".row-tags");
    answer.querySelectorAll(".row-reason, .row-warn").forEach(n => n.remove());
    tags.replaceChildren();
    facts.replaceChildren();
    if (!r || r.status !== 200) {
      li.className = "row refused";
      head.textContent = "Not checked";
      answer.append(el("p", "row-reason", (r && r.error) || "The check failed on this file. Nothing was kept."));
      return;
    }
    li.className = "row done " + r.answer;
    head.textContent = headline(r);
    const reason = r.answer === "concerns" ? concernReason(r.evidence, r.concern_cards) : "";
    if (reason) answer.append(el("p", "row-reason", reason));
    const ran = Array.isArray(r.rungs) ? r.rungs : [], failed = Array.isArray(r.rungs_failed) ? r.rungs_failed : [];
    // A272 (A269 design check): each fact stays whole ("under 0.1 s" never splits at 740), and the "·" between
    // them is drawn before the next fact, clipped when that fact starts a line, so no line starts or ends with one.
    const list = el("span", "facts-list");
    [plural(ran.length, "check ran", "checks ran"), seconds(r.seconds)].filter(Boolean)
      .forEach(t => list.append(el("span", "fact", t)));
    const line = el("span", "facts-line");
    line.append(list);
    facts.append(line);
    facts.title = ran.map(n => RUNGS[n] || n).join(", ");
    if (r.receipt_json) {
      const url = URL.createObjectURL(new Blob([r.receipt_json], { type: "application/json" }));
      links.push(url);
      const a = el("a", "receipt-link", "Receipt");
      a.href = url;
      a.download = r.receipt_name;
      a.setAttribute("aria-label", "Receipt for " + e.path + " (JSON)");
      // A262 (persona snag 6): at 1440 the "· Receipt" tail wrapped alone onto a second line, led by its dot. The
      // link now takes a line of its own under the facts, with no separator, so it never wraps alone at any width.
      facts.append(a);
    }
    if (failed.length) {
      const warn = el("p", "row-warn", plural(failed.length, "check", "checks") + " could not run: "
                                       + failed.map(n => RUNGS[n] || n).join(", ") + ".");
      answer.append(warn);
    }
    if (!(r.engine === "ladder" && r.demo === false)) tags.append(el("span", "btag demo", "Demo answer"));
    stubBanner();   // any engine but the ladder is canned
    if (e.dupOf) tags.append(el("span", "btag dup", dupTag(e.dupOf)));
    if (!r.receipt_json && r.receipt_error) answer.append(el("p", "row-warn", r.receipt_error));
  }

  // --- sending --------------------------------------------------------------------------------------
  async function run(id) {
    const t0 = performance.now();
    const send = [];
    for (const e of entries) {
      if (!IMAGE.includes(suffix(e.file.name))) e.result = { status: 415, error: NOT_IMAGE };
      else if (e.file.size > MAX_BYTES) e.result = { status: 413, error: "That file is over 25 MB." };
      else if (!e.file.size) e.result = { status: 400, error: "That file is empty." };
      else { send.push(e); continue; }
      setRow(e);
    }
    const groups = [];
    const parts = sets(send);
    $("sets-note").hidden = parts.length < 2;  // said as soon as a run is known to need more than one set
    let done = 0;
    for (const part of parts) {
      if (id !== runId) return;
      part.forEach(e => { e.li.className = "row checking"; e.li.querySelector(".row-headline").textContent = "Checking"; });
      const what = parts.length > 1
        ? "Checking " + plural(part.length, "file", "files") + ", " + (done + 1) + " to " + (done + part.length) + " of " + send.length + "."
        : "Checking " + plural(part.length, "file", "files") + ".";
      const set0 = performance.now();
      const tick = () => {
        const s = Math.floor((performance.now() - set0) / 1000);
        progress.textContent = what + " " + s.toLocaleString("en") + " s so far. Answers appear together when this set finishes.";
      };
      tick();
      clearInterval(ticker);
      ticker = setInterval(tick, 1000);
      status.textContent = what + " Answers appear together when this set finishes.";
      const form = new FormData();
      part.forEach(e => form.append("files", e.file, e.file.name));
      let body = null, error = "";
      try {
        const res = await fetch("/api/batch?receipt=1", { method: "POST", body: form });
        const data = await res.json().catch(() => ({}));
        if (res.ok) body = data; else error = data.detail || "The check failed on these files. Nothing was kept.";
      } catch (err) {
        error = "Could not reach the checker on this computer. Is it still running?";
      }
      if (id !== runId) return;  // a newer run owns the counter now (start() cleared this one's)
      clearInterval(ticker);
      part.forEach((e, k) => {
        e.result = body ? body.results[k] : { status: 0, error };
      });
      // Groups come from the API, one set at a time; they are numbered across the whole run here.
      for (const g of (body && body.near_duplicate_groups) || []) {
        const n = groups.length + 1;
        const members = g.indexes.map(k => part[k]);
        groups.push({ n, files: members, note: g.note });
        members.forEach(e => { e.group = n; e.dupOf = members.filter(o => o !== e).map(o => o.n); });
      }
      part.forEach(setRow);
      done += part.length;
    }
    finish(groups, parts.length, (performance.now() - t0) / 1000);
  }

  function finish(groups, setCount, took) {
    const checked = entries.filter(e => e.result && e.result.status === 200).length;
    const refused = entries.length - checked;
    const receipts = entries.filter(e => e.result && e.result.receipt_json).length;
    progress.textContent = plural(entries.length, "file", "files") + ": " + checked.toLocaleString("en") + " checked"
      + (refused ? ", " + refused.toLocaleString("en") + " not checked" : "") + ". Took " + seconds(took) + " in all.";
    status.textContent = progress.textContent;
    const list = $("dup-list");
    list.replaceChildren(...groups.map(g => el("li", "", rowList(g.files.map(e => e.n)).replace(/^r/, "R") + ": "
                                                         + g.files.map(e => e.path).join(", "))));
    $("dup-note").textContent = groups.length ? groups[0].note : "";
    $("dups").hidden = !groups.length;
    $("sets-note").hidden = setCount < 2;
    zipGo.disabled = !receipts;
    zipGo.textContent = !receipts ? "Download all receipts (.zip)"
      : receipts === 1 ? "Download the receipt (.zip)" : "Download all " + receipts.toLocaleString("en") + " receipts (.zip)";
    const versions = Array.from(new Set(entries.map(e => e.result && e.result.version).filter(Boolean)));
    const engines = Array.from(new Set(entries.map(e => e.result && e.result.engine).filter(Boolean)));
    $("batch-meta").textContent = engines.length ? "Engine: " + engines.join(", ") + " · secondlook " + versions.join(", ") : "";
    out.removeAttribute("aria-busy");
    groupsOut = groups.map(g => ({ group: g.n, files: g.files.map(e => e.n) }));
  }

  // --- receipts out ---------------------------------------------------------------------------------
  function forget() {
    clearInterval(ticker);
    links.forEach(u => URL.revokeObjectURL(u));
    links = [];
  }

  function save(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = el("a");
    a.href = url;
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  zipGo.addEventListener("click", () => {
    const zip = root.SecondLookZip.zip(bundleFiles(entries, groupsOut));
    save(new Blob([zip], { type: "application/zip" }), "second-look-receipts.zip");
    status.textContent = "Saved second-look-receipts.zip.";
  });

  $("batch-again").addEventListener("click", () => {
    runId++;
    forget();
    out.hidden = true;
    stubBanner();   // no rows on screen now
    intake.hidden = false;
    filesIn.value = folderIn.value = "";
    status.textContent = "";
    filesIn.focus({ preventScroll: true });
    intake.scrollIntoView({ block: "start" });
  });

  $("folder-go").addEventListener("click", () => folderIn.click());
  filesIn.addEventListener("change", () => start(Array.from(filesIn.files).map(f => ({ file: f, path: f.name }))));
  folderIn.addEventListener("change", () => start(Array.from(folderIn.files)
    .map(f => ({ file: f, path: f.webkitRelativePath || f.name }))));
  ["dragenter", "dragover"].forEach(t => drop.addEventListener(t, ev => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "dragend"].forEach(t => drop.addEventListener(t, () => drop.classList.remove("over")));
  drop.addEventListener("drop", async ev => {
    ev.preventDefault();
    drop.classList.remove("over");
    start(await fromDrop(ev.dataTransfer));
  });
})(typeof window !== "undefined" ? window : globalThis);
