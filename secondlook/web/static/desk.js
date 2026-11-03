// W10 desk, part 1 (DAT-850): the claim box and voice notes, beside the image drop.
// Each panel is shown only when /api/health says its part can run on this computer (fail closed:
// no answer from health keeps every panel hidden). Every card shows its "does not prove" line, and
// every piece of text a model or a publisher wrote is labelled as theirs.
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const result = $("desk-result"), status = $("desk-status");
  let runId = 0;

  // ---------------------------------------------------------------- what this computer can check
  const LABELS = { engine: "image checks", claims_index: "claim check", voice: "voice notes" };
  const OFF = {
    engine: "Image checks: no checks are installed, so an image gets \"can't tell\" by default.",
    // A262 (persona snag 2): what each part is for and what turning it on needs, with no engine names.
    // Sizes: the README's "about 200 MB to download"; ggml-small.en.bin is 487,614,201 bytes (docs/W9-voice.md).
    claims_index: "Check a claim is off on this computer. It looks up a claim in published fact-checks, offline. "
                  + "Turning it on needs a one-time download of about 200 MB. The README has the steps.",
    voice: "Voice notes are off on this computer. They turn a voice note into text here, then look up each claim heard. "
           + "Turning them on needs a one-time install of a transcriber and a 488 MB speech model. The README links to the steps.",
  };

  function capabilities(h) {
    const caps = (h && h.capabilities) || {}, d = (h && h.details) || {};
    const on = [], off = [];
    if (caps.engine) on.push(LABELS.engine + (Array.isArray(h.rungs) && h.rungs.length ? " (" + h.rungs.join(", ") + ")" : ""));
    else off.push(OFF.engine);
    if (caps.claims_index) {
      const rows = Number((d.claims_index || {}).rows);
      on.push(LABELS.claims_index + (rows ? " (" + rows.toLocaleString("en") + " published reviews)" : ""));
    } else off.push(OFF.claims_index);
    if (caps.voice) {
      const v = d.voice || {};
      on.push(LABELS.voice + (v.transcriber ? " (" + v.transcriber + (v.model ? ", model " + v.model : "") + ")" : ""));
    } else off.push(OFF.voice);
    $("claim-panel").hidden = !caps.claims_index;
    $("voice-panel").hidden = !caps.voice;
    $("desk-panels").classList.toggle("one", !(caps.claims_index && caps.voice));
    $("more-title").hidden = !(caps.claims_index || caps.voice);
    const strip = $("caps");
    strip.textContent = on.length ? "Ready on this computer: " + on.join(" · ") + "." : "";
    strip.hidden = !on.length;
    const offEl = $("caps-off");
    offEl.replaceChildren(...off.map(t => el("span", "", t)));
    offEl.hidden = !off.length;
  }
  fetch("/api/health").then(r => r.json()).then(capabilities).catch(() => {});

  // ---------------------------------------------------------------- cards
  const KIND = {
    how_made: "How it was made", transcript: "Machine transcript", claim: "Publisher's rating",
    no_match: "Nothing found", no_claims: "Nothing to look up", no_speech: "No speech",
    not_installed: "Not installed", unavailable: "Not available", too_long: "Over 10 minutes",
    too_big: "Over 50 MB", unreadable: "Not readable", decoder_unavailable: "No transcript",
    transcript_unavailable: "No transcript", transcript_failed: "No transcript",
  };
  const isLink = (u) => typeof u === "string" && /^https?:\/\//i.test(u);
  const quoted = (s) => "“" + s + "”";
  const clock = (ms) => {
    const s = Math.round(ms / 1000);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  };

  function row(dl, label, value, cls) {
    dl.append(el("dt", cls || "", label));
    const dd = el("dd");
    if (value instanceof Node) dd.append(value); else dd.textContent = value;
    dl.append(dd);
  }

  function limits(dl, ev) {
    row(dl, "Proves", ev.proves);
    const limit = (ev.does_not_prove || "").trim();
    dl.append(el("dt", "limit", "Does not prove"));
    dl.append(limit ? el("dd", "", limit)
                    : el("dd", "missing", "Missing: this card did not say what it does not prove. Treat it as unchecked."));
  }

  // A published review: the rating and the claim they reviewed in the publisher's own words, then who, when and where.
  function matchBody(c, raw) {
    c.append(el("p", "label", "Rating, in the publisher's own words"),
             el("p", "rating", raw.rating ? quoted(raw.rating) : "No rating word published"));
    const dl = el("dl");
    row(dl, "Claim they reviewed", quoted(raw.claim_reviewed));
    row(dl, "Published by", raw.publisher + (raw.is_listed_fact_checker ? ", a fact-checker on the IFCN list"
                                                                       : ", a site not on the IFCN list"));
    row(dl, "Date", raw.date_published || "not given in the feed");
    let link;
    if (isLink(raw.review_url)) {
      link = el("a", "", raw.review_url);
      link.href = raw.review_url;
      link.rel = "noopener noreferrer";
      link.target = "_blank";
    } else link = el("span", "", raw.review_url || "no link");
    row(dl, "Link", link);
    if (raw.claimed_by) row(dl, "Claim made by", raw.claimed_by + " (as the publisher recorded it)");
    c.append(dl);
    if (raw.negation_differs) c.append(el("p", "warn", "Careful: the claim they reviewed differs from yours on a negation (not, no, never, un-, in-). Read their claim before you use the rating."));
  }

  function transcriptBody(c, raw) {
    const t = raw.transcript || {};
    c.append(el("p", "label", "Machine transcript, made on this computer by " + (t.engine || "whisper.cpp")
                              + (t.model ? " (model " + t.model + ")" : "") + ". It can mishear words."));
    c.append(el("blockquote", "transcript", t.text ? quoted(t.text) : "(no words heard)"));
    const claims = Array.isArray(raw.candidate_claims) ? raw.candidate_claims : [];
    if (claims.length) {
      c.append(el("p", "label", "Claims picked out of the machine transcript by a fixed rule, then looked up"));
      const ol = el("ol", "heard");
      claims.forEach(s => ol.append(el("li", "", quoted(s))));
      c.append(ol);
    }
  }

  function card(ev, source) {
    const raw = ev.raw || {};
    const kind = raw.review_url ? "claim" : (raw.kind || raw.status || "");
    const c = el("article", "dcard " + (kind === "claim" ? "match" : kind));
    const tags = el("div", "tags");
    tags.append(el("span", "tag", source), el("span", "tag strength", KIND[kind] || "Note"));
    c.append(tags);
    if (raw.heard && raw.heard.sentence) {
      const at = Number.isFinite(raw.heard.start_ms) ? " at " + clock(raw.heard.start_ms) : "";
      c.append(el("p", "heard-at", "Heard in the machine transcript" + at + ": " + quoted(raw.heard.sentence)));
    }
    if (kind === "claim") matchBody(c, raw);
    else if (kind === "transcript") transcriptBody(c, raw);
    else c.append(el("p", "finding", ev.finding));
    const dl = el("dl", "limits");
    limits(dl, ev);
    c.append(dl);
    return c;
  }

  // ---------------------------------------------------------------- answers
  function credit(feed) {
    if (!feed || !feed.credit) return "";
    const date = (feed.feed_date_modified || "").slice(0, 10);
    return feed.credit + (date ? " Feed dated " + date + "." : "") + (feed.source ? " Source: " + feed.source : "");
  }

  function show(r) {
    const voice = r.kind === "voice";
    $("desk-eyebrow").textContent = voice ? "Voice note: how it was made" : "Claim check";
    const ans = $("desk-answer");
    ans.className = "desk-answer " + (voice ? "cant_tell" : (r.status || ""));
    ans.textContent = voice ? "Can't tell" : r.message;
    $("desk-summary").textContent = voice ? r.summary
      : (r.query ? "You typed: " + quoted(r.query) : "");
    $("desk-note").textContent = voice ? "Any rating below is the publisher's own, on the claim it names. Second Look gives no true or false verdict of its own."
      : (r.status === "not_installed" ? r.hint : r.note);
    const cards = $("desk-cards");
    cards.replaceChildren();
    const evidence = Array.isArray(r.evidence) ? r.evidence : [];
    evidence.forEach(ev => cards.append(card(ev, voice ? "Voice note" : "Claim check")));
    $("desk-credit").textContent = credit(r.feed);
    result.hidden = false;
    result.removeAttribute("aria-busy");
    status.className = "visually-hidden";
    status.textContent = (voice ? "Voice note: can't tell how it was made. " : "Claim check: " + r.message + " ")
                         + evidence.length + " card" + (evidence.length === 1 ? "" : "s") + " below.";
    ans.focus({ preventScroll: true });
    // The answer first: app.js folds the inputs into one bar and scrolls this answer to the top.
    document.dispatchEvent(new CustomEvent("secondlook:answered", {
      detail: { label: voice ? "Voice note" + (lastVoice ? ": " + lastVoice : "") : "Claim: " + quoted(r.query || "") } }));
  }

  function setBusy(on) {
    $("claim-go").disabled = on;
    $("voice-file").disabled = on;
    $("voice-drop").classList.toggle("busy", on);
  }

  async function run(label, request) {
    const id = ++runId;
    const image = $("result");
    if (image) image.hidden = true;  // one answer on screen at a time
    status.className = "";
    status.textContent = label;
    result.setAttribute("aria-busy", "true");
    setBusy(true);
    try {
      const res = await request();
      const body = await res.json().catch(() => ({}));
      if (id !== runId) return;  // a newer check started; drop this one
      if (!res.ok) {
        const d = typeof body.detail === "string" ? body.detail : "";
        throw new Error(d || ("The check failed (HTTP " + res.status + ")."));
      }
      show(body);
    } catch (e) {
      if (id !== runId) return;
      result.hidden = true;
      status.className = "error";
      status.textContent = e.message;
    } finally {
      if (id === runId) setBusy(false);
    }
  }

  $("claim-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("claim-text").value.trim();
    if (!text) {
      status.className = "error";
      status.textContent = "Please type a claim to look up.";
      $("claim-text").focus();
      return;
    }
    run("Looking up the claim…", () => fetch("/api/claim", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }),
    }));
  });

  let lastVoice = "";
  function runVoice(file) {
    if (!file) return;
    lastVoice = file.name;
    const fd = new FormData();
    fd.append("file", file, file.name);
    run("Transcribing " + file.name + " on this computer, then looking up each claim. A long note takes a minute or two…",
        () => fetch("/api/voice", { method: "POST", body: fd }));
  }
  const vin = $("voice-file"), vdrop = $("voice-drop");
  vin.addEventListener("change", () => { runVoice(vin.files[0]); vin.value = ""; });
  ["dragenter", "dragover"].forEach(t => vdrop.addEventListener(t, e => { e.preventDefault(); vdrop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(t => vdrop.addEventListener(t, e => { e.preventDefault(); vdrop.classList.remove("over"); }));
  vdrop.addEventListener("drop", e => { if (!vin.disabled) runVoice(e.dataTransfer.files[0]); });

  // An image check takes the screen: clear this answer so two answers never sit together.
  const clear = () => { runId++; result.hidden = true; status.textContent = ""; setBusy(false); };
  const img = $("file"), imgDrop = $("drop");
  if (img) img.addEventListener("change", clear);
  if (imgDrop) imgDrop.addEventListener("drop", clear);
  const gallery = $("gallery");  // a sample check is an image check too
  if (gallery) gallery.addEventListener("click", e => { if (e.target.closest("button")) clear(); });
})();
