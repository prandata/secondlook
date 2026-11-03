// W10 desk, part 2 (DAT-850): the Muse Glimmer investigator (M2, POST /api/investigate) under an image answer.
// It posts the same file and caption the answer came from and shows the desk note M2 returns, as text:
// the template lines, the checks the model called, the model's readings under their label, and any
// card the deep check added. Nothing here writes a sentence about the image; every line is M2's.
// DAT-850: when no Glimmer server answers and a run of these exact bytes was recorded (samples/recorded/,
// matched by sha256 on the server), it offers that run instead. Its label, from the server, comes first; it is
// never mixed with a live run and never touches the answer above. A captioned recording is offered only for its
// own caption (the server matches it by sha256); its caption is shown in full, and a fact-check it found goes in
// the strip under the answer with the recording's label, with no receipt run id, so no receipt takes its picks.
// The line above the button and the button itself follow /api/health's capabilities.glimmer, asked for each answer
// (judge persona test, 30 Oct): until it answers the line says Glimmer is being looked for and no button shows;
// with no Glimmer server it says so and no live button shows; only when one answers does the page say it runs here.
// DAT-850 A228: "not running" only when health's details.glimmer.reason says not_running (nothing listens there).
// Any other no (another model, a slow server, health failing or passing 6 s) gets a neutral line, and no button.
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const panel = $("investigate"), go = $("inv-go"), status = $("inv-status"), timer = $("inv-time");
  const setup = $("inv-setup"), note = $("inv-note");
  const offer = $("inv-recorded-offer"), recGo = $("inv-recorded-go"), recLabel = $("inv-recorded-label");
  // The line above the button says Glimmer runs here only when /api/health says a Glimmer server answers. While a
  // recording is offered it does not, so the line says so (design review, PR #44 S3).
  const local = $("inv-local"), LOCAL_ASKING = local.textContent;
  // "The model picks ... The lines below ..." shows only when lines can follow: a live model, or a recording on offer.
  const about = $("inv-about");
  const LOCAL_LIVE = "Muse Glimmer runs on this computer. Second Look reaches it only at a loopback address (127.0.0.1), "
                   + "so the image and caption you send it stay on this computer.";
  // A262 (persona snags 3 and 6): with no model and no recording the box says plainly that the deep check is off,
  // with no README pointer an editor can't act on. "Not running" still only when health says not_running (A228).
  const LOCAL_OFF = "The deep check is off on this computer: its model is not running here. "
                  + "The answer above comes from the file checks alone.";
  const LOCAL_UNSURE = "The deep check is off on this computer just now: Second Look could not confirm its model. "
                     + "The answer above comes from the file checks alone.";
  const LOCAL_STUB = "This page is running the demo stub, so the deep check is off here. It runs on the file checks only.";
  const LOCAL_RECORDED = "Muse Glimmer is not running on this computer now, so nothing is sent to it. "
                       + "A file you drop is compared with our recordings by sha256 here, on this computer.";
  const LOCAL_RECORDED_UNSURE = "Second Look could not confirm Muse Glimmer on this computer now, so nothing is sent "
                              + "to it. A file you drop is compared with our recordings by sha256 here, on this computer.";
  // The offer's first sentence, as index.html has it, says no Glimmer server is running; the neutral one does not.
  const OFFER_OFF = offer.textContent.replace(/\s+/g, " ").trim();
  const OFFER_UNSURE = "Second Look could not confirm Muse Glimmer here now, so the deep check cannot run live. "
                     + "We recorded one on this exact file (the same sha256 fingerprint), and you can read it here.";
  // app.js gives up on a health request after 6 s; the deep check's ask does the same, and then reads as no Glimmer.
  const HEALTH_MS = 6000;
  // From docs/M2-INVESTIGATOR.md: the 12-image smoke run took 52.72 to 126.42 s an image, and one
  // request stops its deep check at 300 s (MAX_SECONDS). Worded as that one run's range, so a quicker
  // run (the 29 Oct showcase took 42 s) does not read as wrong.
  const EXPECT = "In our 12-image test run a deep check took 53 to 126 seconds an image; yours may be quicker or slower. "
               + "It stops itself at 300 seconds.";
  const TOOLS = {
    run_ladder: "File checks", read_text_in_image: "Read printed words", extract_claim_facts: "Caption facts",
    lookup_claim: "Fact-check lookup", context_check: "Context check", unknown: "Unknown tool",
  };
  const FACTS = { place: "Place", device: "Device" };
  let current = null, runId = 0, tick = null, busy = false, recording = null;

  function stopTimer() {
    if (tick) clearInterval(tick);
    tick = null;
  }

  function reset() {
    runId++;
    stopTimer();
    busy = false;
    go.removeAttribute("aria-disabled");
    panel.removeAttribute("aria-busy");
    timer.hidden = true;
    status.className = "";
    status.textContent = "";
    setup.hidden = true;
    note.hidden = true;
    recording = null;
    local.textContent = LOCAL_ASKING;
    about.hidden = true;
    go.hidden = true;
    $("inv-sample").hidden = true;
    offer.hidden = true;
    recGo.hidden = true;
    recLabel.hidden = true;
  }

  // A new check started, or an answer is on screen: any deep check still running belongs to the old one.
  document.addEventListener("secondlook:checking", () => { current = null; reset(); });
  document.addEventListener("secondlook:result", (e) => {
    current = e.detail || null;
    reset();
    deepCheck(current, runId);
  });

  // Ask /api/health whether a Glimmer server answers, and set the line and the button from that alone. Fail
  // closed: no answer from health reads as no Glimmer. Any newer check, or a live run started meanwhile,
  // changes runId, and then nothing here touches the page.
  async function deepCheck(of, id) {
    // The investigator runs on the file checks only (the stub gets a 409), so the stub's answers need no health ask.
    if (of && of.engine === "stub") { local.textContent = LOCAL_STUB; return; }
    let caps = {}, why = "";
    const stop = new AbortController(), limit = setTimeout(() => stop.abort(), HEALTH_MS);
    try {
      const h = await (await fetch("/api/health", { signal: stop.signal })).json();
      caps = (h && h.capabilities) || {};
      why = String((h && h.details && h.details.glimmer && h.details.glimmer.reason) || "");
    } catch (e) {
      caps = {};
    } finally {
      clearTimeout(limit);
    }
    if (id !== runId) return;
    if (caps.glimmer === true) {
      const canRun = !!(of && of.file);
      local.textContent = LOCAL_LIVE;
      about.hidden = false;
      go.hidden = !canRun;
      $("inv-sample").hidden = canRun;
      return;
    }
    const notRunning = why === "not_running";
    local.textContent = notRunning ? LOCAL_OFF : LOCAL_UNSURE;
    if (caps.recorded) offerRecording(of, id, notRunning);
  }

  // Only when no Glimmer server answers: ask the server for a recorded run of the same bytes.
  async function offerRecording(of, id, notRunning) {
    if (!of || of.engine !== "ladder" || !(of.file || of.sampleId)) return;
    let res;
    try {
      if (of.file) {
        const fd = new FormData();
        fd.append("file", of.file, of.file.name);
        if (of.caption) fd.append("caption", of.caption);
        res = await fetch("/api/recorded", { method: "POST", body: fd });
      } else {
        res = await fetch("/api/samples/" + encodeURIComponent(of.sampleId) + "/recorded");
      }
    } catch (e) {
      return;
    }
    // 204: the server has no recording of this file (and caption). Nothing to offer, and nothing to read.
    if (res.status === 204 || !res.ok || id !== runId) return;
    const rec = await res.json().catch(() => null);
    if (id !== runId || !rec || rec.recorded !== true || rec.live !== false || typeof rec.label !== "string"
        || !rec.label.startsWith("Recorded run, not live.") || !rec.note || typeof rec.note !== "object") return;
    recording = rec;
    go.hidden = true;
    $("inv-sample").hidden = true;
    local.textContent = notRunning ? LOCAL_RECORDED : LOCAL_RECORDED_UNSURE;
    offer.textContent = notRunning ? OFFER_OFF : OFFER_UNSURE;
    offer.hidden = false;
    recGo.hidden = false;
    about.hidden = false;
  }

  function showSetup(why, stub) {
    $("inv-setup-why").textContent = why;
    $("inv-setup-ladder").hidden = !stub;
    setup.hidden = false;
  }

  function line(list, text) { if (typeof text === "string" && text.trim()) list.append(el("li", "", text)); }

  // The caption clauses Muse Glimmer picked to search published fact-checks (lookup_claim steps it asked for,
  // with search words it took from the caption), word for word, once each. found: only the searches that found a
  // published fact-check (the lookup_claim.found template, as receipt.py reads them).
  function picks(steps, found) {
    const out = [];
    (Array.isArray(steps) ? steps : []).forEach(st => {
      const a = st && st.arguments;
      if (!st || st.tool !== "lookup_claim" || st.by !== "model" || st.search_source !== "model" || !a) return;
      if (found && !(st.ok && st.template === "lookup_claim.found")) return;
      if (typeof a.text === "string" && a.text.trim() && !out.includes(a.text.trim())) out.push(a.text.trim());
    });
    return out;
  }

  // rec: the recorded run r came from (DAT-850), or undefined for a live run. Its label goes first.
  function showNote(r, rec) {
    const labels = current.labels || {};
    recLabel.textContent = rec ? rec.label : "";
    recLabel.hidden = !rec;
    const recCaption = $("inv-recorded-caption");
    recCaption.textContent = rec && typeof rec.caption === "string" && rec.caption
      ? "Caption in the recorded run, in full: \u201c" + rec.caption + "\u201d" : "";
    recCaption.hidden = !recCaption.textContent;
    // The label scrolls away on a long note, so the eyebrow and the footnote say it too (PR #44 S2).
    $("inv-eyebrow").textContent = rec ? "Desk note · recorded run, not live" : "Desk note";
    $("inv-answer").textContent = (rec ? "Answer in the recorded run, from Second Look's fixed rules: "
                                       : "Answer from Second Look's fixed rules, over every card found: ") + (labels[r.answer] || r.answer);
    $("inv-explain-label").textContent = r.explanation_label || "";
    // M2's step lines are shown once, in "Checks Muse Glimmer called" below, and its per-card lines restate the
    // cards (in a second wording of the same score and count), so only its summary line shows here. If that line
    // is the plain-language summary the answer above already shows, word for word, it is not shown again.
    const lines = $("inv-lines");
    lines.replaceChildren();
    let summary = r.explanation;
    if (Array.isArray(r.explanation_lines) && r.explanation_lines.length) {
      const l = r.explanation_lines.find(l => l && typeof l.template === "string" && !l.template.startsWith("step."));
      summary = l && l.text;
    }
    const shownAbove = typeof summary === "string" && !!current.explained && summary.trim() === current.explained.trim();
    if (!shownAbove) line(lines, summary);
    $("inv-explain-label").hidden = !lines.children.length;

    // The checks it called, in order: the tool, who asked for it, and M2's template line for what came back.
    const steps = $("inv-steps");
    steps.replaceChildren();
    (Array.isArray(r.steps) ? r.steps : []).forEach(st => {
      const li = el("li", st.ok ? "" : "refused");
      const head = el("span", "step-head");
      head.append(el("code", "", st.tool || "unknown"),
                  " " + (TOOLS[st.tool] || TOOLS.unknown)
                  + (st.by === "rules" ? ", run by Second Look's rules" : ", asked for by the model")
                  + (st.ok ? "" : ", refused or failed")
                  + (Number.isFinite(st.seconds) ? ", " + st.seconds.toFixed(1) + " s" : ""));
      li.append(head, el("span", "step-result", st.result || ""));
      steps.append(li);
    });
    if (!steps.children.length) steps.append(el("li", "", "The model called no checks."));
    $("inv-steps-title").textContent = "Checks Muse Glimmer called (" + (Array.isArray(r.steps) ? r.steps.length : 0) + ")";

    // The model's readings: printed words and caption facts, under M2's own label.
    const reading = $("inv-reading"), list = $("inv-reading-list");
    list.replaceChildren();
    picks(r.steps).forEach(t => line(list, "Muse Glimmer's pick from the caption, searched in published fact-checks: “" + t + "”"));
    (Array.isArray(r.image_text) ? r.image_text : []).forEach(t => line(list, "Printed in the image: “" + t + "”"));
    (Array.isArray(r.facts) ? r.facts : []).forEach(f => {
      // Place and device are copied from the caption word for word; the date is the calendar day code parsed from it.
      if (f && f.date) line(list, "Date in the caption, as a calendar day: " + f.date);
      ["place", "device"].forEach(k => { if (f && f[k]) line(list, FACTS[k] + " in the caption, in its own words: “" + f[k] + "”"); });
    });
    $("inv-reading-title").textContent = "Model readings (" + (r.label || "model reading, not evidence") + ")";
    reading.hidden = !list.children.length;

    // Cards the deep check found that the answer above does not already list, each with its limit. A card from
    // a check the answer above already ran (the deep check reruns the file checks) folds into a details element,
    // so the page does not show two readings of the same check side by side.
    const seen = new Set((current.evidence || []).map(ev => ev.rung + "\u0000" + ev.finding));
    const ran = new Set((current.evidence || []).map(ev => ev.rung));
    const extra = (Array.isArray(r.evidence) ? r.evidence : []).filter(ev => !seen.has(ev.rung + "\u0000" + ev.finding));
    // The strip under the answer shows the first published fact-check whole, with its link and its limit (app.js),
    // so it is not repeated here as a card with its raw URL; one line says where it is (design review, PR #48 N2).
    const inStrip = extra.indexOf(window.secondlookFactChecks(r.evidence)[0]);
    if (inStrip >= 0) extra.splice(inStrip, 1);
    const fresh = extra.filter(ev => !ran.has(ev.rung)), rerun = extra.filter(ev => ran.has(ev.rung));
    const cards = $("inv-cards"), again = $("inv-rerun-list");
    cards.replaceChildren();
    again.replaceChildren();
    if (inStrip >= 0) cards.append(el("p", "inv-fc-pointer", "The published fact-check this deep check found is shown "
      + "in the strip under the answer, with its link and what it does not prove."));
    extra.forEach(ev => {
      const c = el("article", "inv-card");
      c.append(el("p", "finding", ev.finding));
      const dl = el("dl");
      dl.append(el("dt", "", "Proves"), el("dd", "", ev.proves || ""));
      const limit = (ev.does_not_prove || "").trim();
      dl.append(el("dt", "limit", "Does not prove"),
                limit ? el("dd", "", limit)
                      : el("dd", "missing", "Missing: this card did not say what it does not prove. Treat it as unchecked."));
      c.append(dl);
      (ran.has(ev.rung) ? again : cards).append(c);
    });
    $("inv-cards-wrap").hidden = !fresh.length && inStrip < 0;
    $("inv-rerun").hidden = !rerun.length;
    $("inv-rerun").open = false;
    $("inv-rerun-title").textContent = "The same checks, run again by the deep check (" + rerun.length + ")";

    const secs = (n) => Number.isFinite(n) ? n.toFixed(1) + " s" : "";
    const meta = $("inv-meta");
    meta.replaceChildren((rec ? "Recorded run, not live · Model: " : "Model: ") + (r.model || "muse-glimmer") + " · model time " + secs(r.model_seconds)
                         + " · whole deep check " + secs(r.seconds) + " · secondlook ", window.secondlookVersion(r.version));
    if (rec && rec.model) {
      const sha = typeof rec.model.sha256 === "string" ? rec.model.sha256 : "";
      const file = el("span", "", (rec.model.file || "") + (sha ? " (sha256 " + sha.slice(0, 12) + "…)" : ""));
      if (sha) file.title = "sha256 " + sha;
      meta.append(" · recorded with ", file, " · llama.cpp " + (rec.llama_cpp_build || "unknown"));
    }
    note.hidden = false;
  }

  recGo.addEventListener("click", () => {
    if (!recording || !current || busy) return;
    offer.hidden = true;
    recGo.hidden = true;
    status.className = "";
    status.textContent = "Showing a recorded run of this exact file. It is not live, and it does not change the answer above. "
                       + "To run deep checks live, see \u201cMeta Muse Glimmer features\u201d in the README.";
    showNote(recording.note, recording);
    // A fact-check the recorded run found goes in the strip under the answer, under the recording's label. The
    // answer above is not touched, and the run id is null: a recording is never a server run, so a receipt
    // asked for after it carries no picked words (the project lead, DAT-850 PR #44).
    // picked: the caption words Muse Glimmer searched with that found it, shown in the strip as its pick.
    const detail = { evidence: recording.note.evidence, receipt_run: null, file: current.file, caption: current.caption || "",
                     factcheck: false, recorded: recording.label, picked: picks(recording.note.steps, true) };
    document.dispatchEvent(new CustomEvent("secondlook:deep-evidence", { detail }));
    // The strip is the moment a judge came for, so the view lands on it and focus goes to its title (design review,
    // PR #48 S1). It opens with the recording's own "Recorded run, not live" band. With no strip, the note's label.
    const strip = $("factcheck"), onStrip = detail.factcheck && !strip.hidden;
    (onStrip ? $("fc-title") : recLabel).focus({ preventScroll: true });
    (onStrip ? strip : recLabel).scrollIntoView({
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  });

  go.addEventListener("click", async () => {
    if (busy || !current || !current.file) return;
    const id = ++runId, start = performance.now();
    busy = true;
    // aria-disabled, not disabled: the button keeps keyboard focus while the check runs.
    go.setAttribute("aria-disabled", "true");
    panel.setAttribute("aria-busy", "true");
    setup.hidden = true;
    note.hidden = true;
    status.className = "";
    status.textContent = "Deep check running with Muse Glimmer on this computer.";
    const seconds = () => Math.round((performance.now() - start) / 1000);
    const show = () => { timer.textContent = "Running for " + seconds() + " s. " + EXPECT; };
    show();
    timer.hidden = false;
    tick = setInterval(show, 1000);

    const fd = new FormData();
    fd.append("file", current.file, current.file.name);
    if (current.caption) fd.append("caption", current.caption);
    let res, body;
    try {
      res = await fetch("/api/investigate", { method: "POST", body: fd });
      body = await res.json().catch(() => ({}));
    } catch (e) {
      res = null;
    }
    if (id !== runId) return;  // a newer check started; drop this one
    stopTimer();
    timer.hidden = true;
    busy = false;
    go.removeAttribute("aria-disabled");
    panel.removeAttribute("aria-busy");
    const detail = body && typeof body.detail === "string" ? body.detail : "";

    if (res && res.status === 409) {
      status.className = "error";
      status.textContent = "The investigator is off on this page.";
      // The server's own reason names the ladder; the page says it in its own fixed words.
      showSetup("This page is running the demo stub. The investigator runs on the file checks only, not on the stub's canned answers.", true);
    } else if (!res || !res.ok) {
      status.className = "error";
      status.textContent = detail || (res ? "The deep check failed (HTTP " + res.status + ")." : "The page could not reach the local server.");
    } else if (body.glimmer === "unavailable") {
      status.className = "error";
      status.textContent = "Muse Glimmer is not set up on this computer, so there was no deep check.";
      showSetup("No Muse Glimmer server answered at its loopback address. The answer above stands: it comes from the file checks alone.", false);
    } else {
      const n = Array.isArray(body.steps) ? body.steps.length : 0;
      // Worded apart from the answer's own count ("This answer ran N checks on the file"): these are the checks
      // Muse Glimmer called, one after another, and its file-check step reruns several at once.
      status.textContent = "Deep check done in " + seconds() + " s. Muse Glimmer called " + n + " of Second Look's checks, "
                         + "listed under \u201cChecks Muse Glimmer called\u201d. Fixed rules gave the answer, not the model.";
      showNote(body);
      // A published fact-check the deep check matched goes in the strip under the headline (app.js), and
      // focus goes there: it is the line a newsroom came for. Otherwise focus goes to the note's answer.
      const found = { evidence: body.evidence, receipt_run: body.receipt_run || null, file: current.file,
                      caption: current.caption || "", factcheck: false };
      document.dispatchEvent(new CustomEvent("secondlook:deep-evidence", { detail: found }));
      const target = found.factcheck ? $("fc-title") : $("inv-answer");
      if (found.factcheck) status.textContent += " A published fact-check was found; it is shown under the answer.";
      target.focus({ preventScroll: true });
      (found.factcheck ? $("result") : target).scrollIntoView({
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    }
  });
})();
