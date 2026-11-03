(() => {
  // The answer as a big label, with one line on what it means.
  const ANSWERS = {
    signed_ai:     { label: "Signed as AI-made",
                     meaning: "A valid signed record in this file says AI was used to make or edit it." },
    signed_camera: { label: "Signed as a camera capture",
                     meaning: "A valid signed record in this file says a camera captured it. The scene itself is not proven." },
    concerns:      { label: "Reasons to look closer",
                     meaning: "Clues worth a closer look: unsigned labels or signals, or a signed record that failed its checks. A reason to check further, not a verdict." },
    cant_tell:     { label: "Can't tell",
                     meaning: "No signed record settles it, and no clue is strong enough. That is an honest answer, not a failure." },
  };
  const RUNGS = { ladder: "Ladder", c2pa: "C2PA", iptc: "IPTC", exif: "EXIF", watermark: "Watermark",
                  classifier: "Classifier", origin: "Origin", claim: "Claim check", context: "Caption context" };
  const STRENGTH = { signed: "Signed", metadata: "Unsigned metadata", signal: "Signal", none: "Nothing found" };
  // The concern headline is worded by the ladder's own count (the response's concern_cards), never "concern(s)".
  const COUNT = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"];
  const concernLabel = (n) => !(n > 0) ? ANSWERS.concerns.label
    : (COUNT[n] || n.toLocaleString("en")) + (n === 1 ? " reason" : " reasons") + " to look closer";

  const $ = (id) => document.getElementById(id);
  const drop = $("drop"), input = $("file"), status = $("status"), answerStatus = $("answer-status"), result = $("result");
  let previewUrl = null, runId = 0, lastFile = null;
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  // The answer first (W11 design pass): once an answer is on screen the inputs fold into one bar,
  // "<file> · Check another", and the answer sits at the top. desk.js sends "secondlook:answered" too.
  const intake = $("intake"), bar = $("again-bar");
  function collapse(label) {
    $("again-label").textContent = label;
    intake.hidden = true;
    bar.hidden = false;
    bar.scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "start" });
  }
  function expand() {
    intake.hidden = false;
    bar.hidden = true;
  }
  $("again-go").addEventListener("click", () => {
    expand();
    input.focus({ preventScroll: true });
    intake.scrollIntoView({ behavior: reduced() ? "auto" : "smooth", block: "start" });
  });
  document.addEventListener("secondlook:answered", (e) => collapse((e.detail && e.detail.label) || ""));

  // The caption box (W10 part 2): sent as the optional `caption` field. The server counts characters
  // after trimming (Python len, so code points); Array.from counts the same way.
  const MAX_CAPTION = 2000;
  const caption = $("caption"), captionError = $("caption-error"), captionCount = $("caption-count"), again = $("caption-again");
  // Trimmed as the server trims (Python str.strip): JS trim() would also drop U+FEFF and keep U+001C-1F and U+0085.
  const captionText = () => caption.value.replace(/^(?:[^\S\uFEFF]|[\x1c-\x1f\x85])+|(?:[^\S\uFEFF]|[\x1c-\x1f\x85])+$/g, "");
  const captionLength = () => Array.from(captionText()).length;
  function captionProblem(message) {
    captionError.textContent = message || "";
    captionError.hidden = !message;
    if (message) caption.setAttribute("aria-invalid", "true"); else caption.removeAttribute("aria-invalid");
  }
  function captionCheck() {
    const n = captionLength();
    captionCount.textContent = n ? n.toLocaleString("en") + " of 2,000 characters." : "Up to 2,000 characters.";
    captionCount.className = n > MAX_CAPTION ? "over" : "";
    captionProblem(n > MAX_CAPTION ? "That caption is over 2,000 characters. Shorten it to check the image with it." : "");
    return n <= MAX_CAPTION;
  }
  caption.addEventListener("input", captionCheck);
  again.addEventListener("click", () => runFile(lastFile));

  // The deep check (investigate.js) listens for these: a check started, and an answer is on screen.
  const tell = (name, detail) => document.dispatchEvent(new CustomEvent("secondlook:" + name, { detail }));

  // The strip under the headline never shows a match without its CC BY credit (DAT-850, Codex FAIL on PR #43).
  // The credit is the feed's own text, desk.py's FEED_CREDIT, as /api/health reports it. Until health has answered,
  // a match is held back (the strip stays hidden); health is asked again once if it fails or takes over 6 s; if it
  // still fails, this fixed credit stands in, so the strip never appears without one.
  const FALLBACK_CREDIT = "Fact-check data: Data Commons ClaimReview feed (CC BY). Each review is its publisher's own work.";
  let feedCredit = "", creditKnown = false, shownMatch = null;
  function creditLine() {
    const line = $("fc-credit");
    line.textContent = shownMatch ? (feedCredit || FALLBACK_CREDIT) + " This one: " + (shownMatch.publisher || "a publisher") + "." : "";
    $("factcheck").hidden = !shownMatch || !creditKnown;
  }
  const askHealth = () => {
    const stop = new AbortController(), timer = setTimeout(() => stop.abort(), 6000);
    return fetch("/api/health", { signal: stop.signal })
      .then(r => { if (!r.ok) throw new Error("health " + r.status); return r.json(); })
      .finally(() => clearTimeout(timer));
  };
  // The stub banner shows exactly when the answer on screen came from the stub engine, whatever health said or failed
  // to say, and a ladder answer that replaces a canned one hides it again: no latch (A273, Codex FAIL on PR #55). With
  // no answer on screen it follows health, and stays hidden while health is unknown, so it never flashes "canned" on
  // the ladder engine (A262, persona snag 7).
  let healthStub = false, answerStub = null;   // null: no answer on screen
  const stubBanner = () => { $("stub").hidden = !(answerStub === null ? healthStub : answerStub); };
  const answerGone = () => { answerStub = null; stubBanner(); };
  // The PDF receipt button shows only when health says the PDF writer imports here; the JSON one needs no extra.
  askHealth().catch(() => new Promise(done => setTimeout(done, 400)).then(askHealth)).then(h => {
    healthStub = h.engine === "stub";
    stubBanner();
    $("receipt-pdf").hidden = !(h.capabilities && h.capabilities.receipt === true);
    const idx = (h.details && h.details.claims_index) || {};
    if (typeof idx.credit === "string") feedCredit = idx.credit.trim();
  }).catch(() => {}).then(() => { creditKnown = true; creditLine(); });

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  // The build's commit as a short hash that never breaks across lines, with the full one in its title
  // (W11 stage 2: the 40-character hash left one character on a line of its own). investigate.js uses it too.
  function version(v) {
    const full = typeof v === "string" ? v : "", hex = /^[0-9a-f]{40}$/.test(full);
    const n = el("span", "version", hex ? full.slice(0, 12) : full || "unknown");
    if (hex) n.title = "Commit " + full;
    return n;
  }
  window.secondlookVersion = version;

  // A262 (persona snag, owner row 13): a trusted C2PA card leads with one sentence, the rung's own words up to the
  // declaration: "C2PA manifest signed by X on D; it declares ...". The signing tool and the trust-list sentence the
  // rung adds go into the "Source" disclosure, word for word; the trust line below restates the list in view.
  // Any other shape keeps its finding whole.
  function c2paHeadline(ev) {
    const raw = ev.raw || {}, f = typeof ev.finding === "string" ? ev.finding : "", gen = raw.claim_generator;
    if (ev.rung !== "c2pa" || raw.trusted !== true || !raw.trust_list || typeof gen !== "string" || !gen) return null;
    const via = " via " + gen + "; it ", at = f.indexOf(via), end = f.lastIndexOf(". The signer is ");
    if (!f.startsWith("C2PA manifest ") || at < 0 || end < at + via.length) return null;
    return f.slice(0, at) + "; it " + f.slice(at + via.length, end) + ".";
  }

  // The list's source as a link a keyboard can reach and follow, only for an http(s) URL; anything else stays text
  // (A270, Codex FAIL on PR #55). It opens in a new tab, so the answer stays on screen.
  function sourceLink(v) {
    let u = null;
    try { u = new URL(v); } catch (e) { return String(v); }
    if (u.protocol !== "http:" && u.protocol !== "https:") return String(v);
    const a = el("a", "", String(v));
    a.href = u.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  }

  // The trust list a signed record was checked against, from the C2PA rung's raw.trust_list. Its source URL and
  // sha256 (and the rung's full line, when the card shows it shortened) sit under "Source", closed by default.
  function trustLine(ev, fullFinding) {
    const raw = ev.raw || {}, t = raw.trust_list;
    if (!t || typeof t !== "object") return null;
    const box = el("div", "trust"), p = el("p", "trust-line");
    const verdict = raw.valid === false ? "not applied, because the record failed validation"
                  : raw.trusted === true ? "the signer is on this list"
                  : "the signer is not on this list";
    p.append(el("b", "", "Trust list: "), (t.name || "unnamed list") + (t.date ? ", dated " + t.date : "") + "; " + verdict + "."
             + (t.id === "interim" ? " C2PA describes this list as temporary." : ""));
    box.append(p);
    const rows = [fullFinding ? ["The record's full line", fullFinding] : null,
                  t.source ? ["Source", t.source] : null, t.sha256 ? ["sha256", t.sha256] : null].filter(Boolean);
    if (rows.length) {
      const more = el("details", "trust-src");
      more.append(el("summary", "", "Source"));
      rows.forEach(([k, v]) => {
        const r = el("p", "src");
        r.append(el("b", "", k + ": "), k === "Source" ? sourceLink(v) : v);
        more.append(r);
      });
      box.append(more);
    }
    return box;
  }

  // A262 (claims-handler snag 5): an AI-made record labels the file, so its card adds this to its limit.
  const AI_EXTENT = "It also does not show how much of the picture was changed.";
  const aiMade = (ev) => ev.rung === "c2pa" && (ev.raw || {}).valid === true && (ev.raw || {}).source_class === "ai";

  // Text with each match of one capturing pattern kept whole when the line wraps. Inline style, so this lane adds
  // no CSS rule. Each docs/....md path, so "docs/RESULTS-v2.md" never breaks after its hyphen (PR #33 design
  // check); each ISO date in a card's headline, so "2024-12-14" never breaks after "2024-" at 390 (A272).
  const withWhole = (tag, cls, text, pattern) => {
    const n = el(tag, cls);
    String(text || "").split(pattern).forEach((part, i) => {
      if (!part) return;
      if (i % 2) { const s = el("span", "", part); s.style.whiteSpace = "nowrap"; n.append(s); }
      else n.append(document.createTextNode(part));
    });
    return n;
  };
  const withWholePaths = (tag, text) => withWhole(tag, "", text, /(docs\/[A-Za-z0-9._-]+\.md)/);
  const WHOLE_DATE = /(\b\d{4}-\d{2}-\d{2}\b)/;

  // One row per clue: the clue in bold, then what it proves and what it does not prove side by side.
  // A row the ladder counts as a concern is marked as one of the headline's reasons.
  function clue(ev, isConcern) {
    const li = el("li", "clue" + (isConcern ? " concern" : ""));
    const tags = el("div", "tags");
    // A classifier score above a threshold that has not passed its gate raises no concern (PR #33):
    // its pill says so, instead of the "Signal" a concern-raising score gets.
    const scoreOnly = ev.rung === "classifier" && ev.strength === "signal" && (ev.raw || {}).raises_concern === false;
    tags.append(el("span", "tag", RUNGS[ev.rung] || ev.rung),
                el("span", "tag strength " + (ev.strength || ""),
                   scoreOnly ? "Score only" : (STRENGTH[ev.strength] || ev.strength)));
    if (isConcern) tags.append(el("span", "tag reason-tag", "Reason to look closer"));
    const headline = c2paHeadline(ev);
    li.append(tags, withWhole("p", "finding", headline || ev.finding, WHOLE_DATE));
    const trust = trustLine(ev, headline ? ev.finding : "");
    if (trust) li.append(trust);
    const dl = el("dl", "pd");
    dl.append(el("dt", "", "Proves"), withWholePaths("dd", ev.proves || ""));
    const given = (ev.does_not_prove || "").trim();
    const limit = given && aiMade(ev) ? given + " " + AI_EXTENT : given;
    dl.append(el("dt", "limit", "Does not prove"),
              limit ? el("dd", "", limit)
                    : el("dd", "missing", "Missing: this card did not say what it does not prove. Treat it as unchecked."));
    li.append(dl);
    return li;
  }

  // Clues with a signal (or that the ladder counts as a concern) show; checks that found nothing fold
  // into "Checked, nothing found (N)", each row still with its limit. The concern rows come first, in
  // the ladder's order, so the headline's reasons are the first rows under it, not in check order.
  function render(evidence, concernCards) {
    const concern = new Set(Array.isArray(concernCards) ? concernCards : []);
    const first = [], shown = [], quiet = [];
    evidence.forEach((ev, i) => (concern.has(i) ? first : (ev.strength && ev.strength !== "none") ? shown : quiet).push(ev));
    const list = $("clues");
    list.replaceChildren(...first.map(ev => clue(ev, true)), ...shown.map(ev => clue(ev, false)));
    if (!list.children.length) list.append(el("li", "clue empty", "None of the checks found a signal in this file."));
    $("quiet-list").replaceChildren(...quiet.map(ev => clue(ev, false)));
    $("quiet-title").textContent = "Checked, nothing found (" + quiet.length + ")";
    $("quiet").hidden = !quiet.length;
    $("quiet").open = false;
  }

  // A published fact-check that matched: the publisher's rating word for word, who, when, and a working link,
  // with its limit. From the answer's own cards, or from the deep check when it finds one.
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const isLink = (u) => typeof u === "string" && /^https?:\/\//i.test(u);
  const quoted = (t) => "\u201c" + t + "\u201d";
  function day(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    return m && +m[2] >= 1 && +m[2] <= 12 ? (+m[3]) + " " + MONTHS[+m[2] - 1] + " " + m[1] : (iso || "date not given in the feed");
  }
  const factChecks = (evidence) => (Array.isArray(evidence) ? evidence : [])
    .filter(ev => ev && ev.raw && typeof ev.raw === "object" && ev.raw.review_url && "rating" in ev.raw);
  window.secondlookFactChecks = factChecks;
  // recorded: the label of the recorded run the match came from (DAT-850), or "" for a live one. picked: the caption
  // words Muse Glimmer searched with that found a match in that run, shown as the model's pick (PR #48 S2).
  function showFactCheck(list, recorded, picked) {
    const from = $("fc-recorded");
    from.textContent = recorded && list.length ? recorded + " This fact-check was found in that run." : "";
    from.hidden = !from.textContent;
    const words = (Array.isArray(picked) ? picked : []).filter(t => typeof t === "string" && t.trim());
    const pick = $("fc-pick");
    pick.textContent = recorded && list.length && words.length
      ? "Searched with the words Muse Glimmer picked from the caption: " + words.map(quoted).join("; ") : "";
    pick.hidden = !pick.textContent;
    shownMatch = list.length ? list[0].raw : null;
    creditLine();  // shows the strip once the credit is known, hides it with no match
    if (!list.length) return false;
    const ev = list[0], raw = ev.raw, line = $("fc-line");
    line.replaceChildren();
    line.append(el("span", "fc-rating", raw.rating ? quoted(raw.rating) : "No rating word published"));
    const who = el("span", "fc-who");
    who.append("Rating in the publisher's own words: " + (raw.publisher || "a publisher")
               + (raw.is_listed_fact_checker ? ", a fact-checker on the IFCN list" : ", a site not on the IFCN list")
               + (raw.claim_reviewed ? ", on the claim " + quoted(raw.claim_reviewed) : "") + " \u00b7 " + day(raw.date_published));
    if (isLink(raw.review_url)) {
      let host = raw.review_url;
      try { host = new URL(raw.review_url).hostname.replace(/^www\./, ""); } catch (e) { /* keep the URL */ }
      // On its own line with a 44 px target (design review, PR #48 N1).
      const a = el("a", "fc-read", "Read it at " + host);
      a.href = raw.review_url;
      a.rel = "noopener noreferrer";
      a.target = "_blank";
      line.append(who, a);
    } else line.append(who);
    $("fc-limit").textContent = (ev.does_not_prove || "").trim()
      || "Missing: this card did not say what it does not prove. Treat it as unchecked.";
    const more = list.length - 1;
    $("fc-more").hidden = !more;
    $("fc-more").textContent = more ? more + " more published " + (more === 1 ? "fact-check" : "fact-checks") + " matched; each is listed below." : "";
    return true;
  }
  // The deep check reports what it found; a fact-check it matched goes in the strip under the headline. A recorded
  // run's match goes there too, under the recording's label, and its receipt_run is null, so no picks follow it.
  document.addEventListener("secondlook:deep-evidence", (e) => {
    const rec = e.detail && typeof e.detail.recorded === "string" ? e.detail.recorded : "";
    const found = showFactCheck(factChecks(e.detail && e.detail.evidence), rec, e.detail && e.detail.picked);
    if (e.detail) e.detail.factcheck = found;
    receiptPicks(e.detail);
  });

  // Shown when no rung returned anything, so "can't tell" still carries its limit (review F3).
  function limitCard(rungs) {
    return {
      rung: "ladder", strength: "none",
      finding: "No rung found anything in this file.",
      proves: rungs.length ? "Only that the checks that ran (" + rungs.join(", ") + ") found no signed record, label or signal in this copy."
                           : "Nothing: no checks are installed, so nothing was read.",
      does_not_prove: "That the image was made by a camera or by AI. Most images carry no record, and many sites strip it on upload.",
    };
  }

  // The headline's reason, word for word from the first concern card the ladder counted, so the answer
  // names it on the first screen. More than one: the first, then how many more are listed below.
  function concernReason(evidence, concernCards) {
    const cards = (Array.isArray(concernCards) ? concernCards : []).map(i => evidence[i])
      .filter(ev => ev && typeof ev.finding === "string" && ev.finding.trim());
    if (!cards.length) return "";
    const more = cards.length - 1;
    return cards[0].finding.trim() + (more ? " And " + more + " more " + (more === 1 ? "reason" : "reasons") + ", listed first below." : "");
  }

  // The explanation comes from Second Look's fixed templates (secondlook/explain.py), never a model.
  // Plain text only. Its summary shows; its per-card lines are not repeated here, because each clue's row
  // below already says what it found, and a second wording of the same score or count would disagree.
  function showExplanation(x) {
    const ok = !!x && typeof x.summary === "string" && x.summary.trim() !== "";
    $("explain").hidden = !ok;
    $("explain-summary").textContent = ok ? x.summary : "";
    return ok;
  }

  function show(r, preview) {
    const a = Object.assign({}, ANSWERS[r.answer] || { label: r.answer, meaning: "" });
    if (r.answer === "concerns") a.label = concernLabel(Array.isArray(r.concern_cards) ? r.concern_cards.length : 0);
    const ans = $("answer");
    ans.className = "answer " + r.answer;
    ans.textContent = a.label;
    const reason = r.answer === "concerns" ? concernReason(r.evidence, r.concern_cards) : "";
    $("reason").textContent = reason;
    $("reason").hidden = !reason;
    $("meaning").textContent = a.meaning;
    const demo = !(r.engine === "ladder" && r.demo === false);
    // The ladder's summary and the plain-language summary say the same answer twice ("We can't tell how this
    // image was made" and "Second Look can't tell how this was made"), so the page shows it once: the
    // plain-language box when there is one. The demo stub keeps its own line, which says it is a demo.
    const explained = showExplanation(r.explanation);
    $("summary").textContent = r.summary;
    $("summary").hidden = explained && !demo;
    $("preview").src = preview.src;
    $("preview").alt = preview.alt;
    const credit = $("sample-credit"), s = r.sample;
    credit.hidden = !s;
    credit.textContent = s ? "Sample " + s.id + ", " + s.title + ": " + s.kind + ". Credit: " + s.credit + ", " + s.licence
                             + ". Source: " + s.source + "." : "";
    const rungs = Array.isArray(r.rungs) ? r.rungs : [];
    const failed = Array.isArray(r.rungs_failed) ? r.rungs_failed : [];
    const why = Array.isArray(r.rungs_failed_why) ? r.rungs_failed_why : [];
    $("demo-tag").hidden = !demo;
    answerStub = r.engine !== "ladder";   // any engine but the ladder is canned
    stubBanner();
    // The one place the page counts the checks: the checks this answer ran, as the server reports them.
    // Worded apart from the deep check's count, which is the checks Muse Glimmer called (investigate.js).
    // A check that raised is not counted as run; it is named plainly after the count, never hidden.
    // W11 stage 2 (S-a): the ran line stays grey; only the warning (nothing ran, or a check could not run)
    // takes the rust weight, and the could-not-run line sits on its own line. (S-b) Each failed check carries
    // the server's reason from its fixed list; with no reason, the line says where the cause is.
    const ranLine = rungs.length ? "This answer ran " + rungs.length + (rungs.length === 1 ? " check" : " checks")
                                   + " on the file: " + rungs.map(n => RUNGS[n] || n).join(", ") + "."
                  : failed.length ? "No check could run on this file."
                  : (r.engine === "ladder" ? "No checks are installed, so this answer is \"can't tell\" by default." : "");
    // With some reasons given and some not, the server-log pointer says it covers only the rest.
    const unexplained = failed.filter((n, i) => !why[i]).length;
    const failedLine = failed.length ? failed.length + (failed.length === 1 ? " check" : " checks") + " could not run: "
                                       + failed.map((n, i) => (RUNGS[n] || n) + (why[i] ? " (" + why[i] + ")" : "")).join(", ")
                                       + "." + (!unexplained ? "" : unexplained < failed.length
                                                ? " The cause of the rest is in the server log."
                                                : " The cause is in the server log.") : "";
    const rl = $("rungs");
    rl.className = "rungs";
    rl.replaceChildren(...[ranLine && el("span", rungs.length ? "" : "warn", ranLine),
                           failedLine && el("span", "warn failed", failedLine)].filter(Boolean));
    render(r.evidence.length ? r.evidence : [limitCard(rungs)], r.concern_cards);
    showFactCheck(factChecks(r.evidence));
    $("meta").replaceChildren("Engine: " + r.engine + " · secondlook ", version(r.version));
    tell("result", { file: preview.file || null, caption: preview.caption || "", sample: !!r.sample,
                     sampleId: (r.sample && r.sample.id) || null,
                     evidence: r.evidence, engine: r.engine, explained: explained ? r.explanation.summary : "",
                     labels: Object.fromEntries(Object.entries(ANSWERS).map(([k, v]) => [k, v.label])) });
    shown = { file: preview.file || null, caption: preview.caption || "", sample: (r.sample && r.sample.id) || null,
              answer: r.answer, deepRun: null };
    receiptReset();
    result.hidden = false;
    result.removeAttribute("aria-busy");
    // Announce the answer once, then move focus to it so keyboard and screen-reader users land on the result.
    // The answer is already on screen, so this line is for screen readers only.
    // The "Checking..." line under the drop zone is done with; the spoken answer goes to #answer-status, which
    // stays outside the folded inputs.
    status.className = "";
    status.textContent = "";
    answerStatus.textContent = "Answer: " + a.label + ". " + (reason ? reason + " " : "") + a.meaning;
    ans.focus({ preventScroll: true });
    collapse(preview.bar || preview.alt);
  }

  // "Download receipt" (W11 stage 2): the evidence receipt of the file the answer came from, as the PDF or the
  // canonical JSON. The server checks the file again to make it (nothing from the first check is kept), so the
  // receipt's own answer comes back in a header and the page says so if it differs from the one on screen.
  let shown = null, receiptRun = 0;
  const receiptStatus = $("receipt-status"), receiptPicked = $("receipt-picked");
  function receiptReset() {
    receiptRun++;
    receiptStatus.className = "";
    receiptStatus.textContent = "";
    receiptPicked.hidden = true;
    receiptPicked.textContent = "";
    // A superseded request's finally skips this clear (its id is stale), so the reset must do it, or the busy
    // guard in downloadReceipt swallows every later click (DAT-850 round 2).
    $("receipt").removeAttribute("aria-busy");
    document.querySelectorAll("#receipt button").forEach(b => b.removeAttribute("aria-disabled"));
  }
  // DAT-850 (TF-722 Beat 7): when the deep check on the file and caption on screen found a published fact-check
  // with clauses Muse Glimmer picked from the caption, the server keeps those clauses and returns a run id. Only
  // that id goes with the receipt request, never the words: the server takes the clauses from its own record,
  // after checking the file and the caption, so the receipt runs the same lookup and keeps the fact-check. Each
  // deep check on this answer replaces the last one's id, so a re-run that found nothing sends nothing.
  const PICKED_LINE = "The receipt also looks up the words Muse Glimmer picked from your caption.";
  function receiptPicks(d) {
    if (!shown || !shown.file || !shown.caption || !d || d.file !== shown.file || d.caption !== shown.caption) return;
    shown.deepRun = null;
    receiptPicked.hidden = true;
    receiptPicked.textContent = "";
    if (typeof d.receipt_run !== "string" || !d.receipt_run) return;
    shown.deepRun = d.receipt_run;
    receiptPicked.textContent = PICKED_LINE;
    receiptPicked.hidden = false;
  }
  async function downloadReceipt(fmt) {
    if (!shown || $("receipt").getAttribute("aria-busy") === "true") return;
    const id = ++receiptRun, of = shown, buttons = document.querySelectorAll("#receipt button");
    let url = "/api/samples/" + encodeURIComponent(of.sample || "") + "/receipt?format=" + fmt, init = { method: "POST" };
    if (of.file) {
      const fd = new FormData();
      fd.append("file", of.file, of.file.name);
      if (of.caption) fd.append("caption", of.caption);
      if (of.deepRun) fd.append("deep_run", of.deepRun);
      url = "/api/receipt?format=" + fmt;
      init = { method: "POST", body: fd };
    }
    $("receipt").setAttribute("aria-busy", "true");
    buttons.forEach(b => b.setAttribute("aria-disabled", "true"));
    receiptStatus.className = "";
    receiptStatus.textContent = "Making the " + fmt.toUpperCase() + " receipt: checking the file again on this computer.";
    try {
      const res = await fetch(url, init);
      if (id !== receiptRun) return;  // a newer check started; drop this one
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : "The receipt failed (HTTP " + res.status + ").");
      }
      const blob = await res.blob();
      if (id !== receiptRun) return;
      const m = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "");
      const name = m ? m[1] : "receipt." + fmt;
      const a = el("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      const answer = res.headers.get("x-secondlook-answer") || "";
      const label = (ANSWERS[answer] || { label: answer }).label;
      // W11 stage 2 (F3): a receipt that disagrees with the screen is a warning, not a success: "Saved name." stays
      // grey and the clause that differs takes the rust warning look, in the same polite status region.
      // DAT-850: the deep check's picked words were asked for but are not in this receipt; the server says why.
      const notAdded = res.headers.get("x-secondlook-picks") === "not-added"
        ? (res.headers.get("x-secondlook-picks-reason") || "The picked claim words are not in this receipt.") : "";
      receiptStatus.replaceChildren("Saved " + name + ".", ...(answer && answer !== of.answer
        ? [" ", el("span", "warn", "Its answer, \u201c" + label + "\u201d, differs from the one on screen: the receipt"
                                     + " records the second check it ran.")]
        : []), ...(notAdded ? [" ", el("span", "warn", notAdded)] : []));
    } catch (e) {
      if (id !== receiptRun) return;
      receiptStatus.className = "error";
      receiptStatus.textContent = e.message || "The receipt failed.";
    } finally {
      if (id === receiptRun) {
        $("receipt").removeAttribute("aria-busy");
        buttons.forEach(b => b.removeAttribute("aria-disabled"));
      }
    }
  }
  document.querySelectorAll("#receipt button").forEach(b => b.addEventListener("click", () => downloadReceipt(b.dataset.format)));

  // Every path that starts a new check, or refuses one, supersedes the answer on screen through here: a newer
  // runId drops a check still in flight, and the receipt reset drops a receipt still in flight, so it can never
  // save the previous image's receipt (Codex on PR #32, DAT-850 round 3).
  function supersede() {
    shown = null;
    receiptReset();
    return ++runId;
  }

  async function check(label, request, preview) {
    const id = supersede();
    tell("checking");
    answerStatus.textContent = "";
    status.className = "";
    status.textContent = "Checking " + label + "…";
    result.setAttribute("aria-busy", "true");
    setBusy(true);
    try {
      const res = await request();
      const body = await res.json().catch(() => ({}));
      if (id !== runId) return;  // a newer check started; drop this one
      if (!res.ok) {
        const d = typeof body.detail === "string" ? body.detail : "";
        // A refused caption is shown beside the box as well as in the status line.
        if (/caption/i.test(d)) { captionProblem(d); caption.focus(); }
        throw new Error(d || ("The check failed (HTTP " + res.status + ")."));
      }
      show(body, preview);
    } catch (e) {
      if (id !== runId) return;
      result.hidden = true;
      answerGone();
      status.className = "error";
      status.textContent = e.message;
    } finally {
      if (id === runId) setBusy(false);
    }
  }

  function runFile(file) {
    if (!file) return;
    lastFile = file;
    again.hidden = false;
    markSample(null);
    if (!captionCheck()) {
      // Nothing is sent: the server would refuse it, and a check without the caption would not be the one asked for.
      supersede();
      tell("checking");
      result.hidden = true;
      answerGone();
      status.className = "error";
      status.textContent = "The caption is over 2,000 characters, so " + file.name + " was not checked. Shorten the caption, then check again.";
      caption.focus();
      return;
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    const text = captionText();
    const fd = new FormData();
    fd.append("file", file, file.name);
    if (text) fd.append("caption", text);
    check(file.name + (text ? " with its caption" : ""), () => fetch("/api/check", { method: "POST", body: fd }),
          { src: previewUrl, alt: "The image you chose: " + file.name, file, caption: text,
            bar: file.name + (text ? " \u00b7 with its caption" : "") });
  }

  // "Try a sample": the server lists only the N1 files whose sha256 matches their manifest.
  const gallery = $("gallery");
  function setBusy(on) { gallery.querySelectorAll("button").forEach(b => { b.disabled = on; }); }
  function markSample(sid) {
    gallery.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.id === sid)));
  }
  function runSample(s) {
    markSample(s.id);
    lastFile = null;  // "check again" reruns a file you chose, so it goes away while a sample is shown
    again.hidden = true;
    check("sample " + s.id + ", " + s.title,
          () => fetch("/api/samples/" + encodeURIComponent(s.id) + "/check", { method: "POST" }),
          { src: s.image, alt: "Sample " + s.id + ": " + s.title, bar: "Sample " + s.id + ", " + s.title });
  }
  fetch("/api/samples").then(r => r.ok ? r.json() : { samples: [] }).then(d => {
    const list = Array.isArray(d.samples) ? d.samples : [];
    list.forEach(s => {
      const li = el("li"), b = el("button");
      b.type = "button";
      b.dataset.id = s.id;
      b.setAttribute("aria-pressed", "false");
      const img = el("img");
      img.src = s.image;
      img.alt = "";
      img.loading = "lazy";
      const text = el("span", "g-text"), title = el("span", "g-title");
      title.append(el("span", "g-num", s.id), s.title);
      text.append(title, el("span", "g-kind", s.kind));
      b.append(img, text);
      b.addEventListener("click", () => runSample(s));
      li.append(b);
      gallery.append(li);
    });
    $("samples").hidden = !list.length;
  }).catch(() => {});

  input.addEventListener("change", () => { runFile(input.files[0]); input.value = ""; });
  ["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => runFile(e.dataTransfer.files[0]));
})();
