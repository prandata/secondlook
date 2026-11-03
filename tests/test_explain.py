"""E1 (DAT-848): the plain-language explanation is written by fixed templates, never by a model.

1. Every template, rendered with the slot values of every sample card, passes the banned-word list.
2. Every sentence explain() gives is one of the templates, with each slot filled from its closed
   vocabulary or number format (so nothing else can appear).
3. a reviewer's N3 batteries, recast: wherever a hostile draft sits (any card field, any raw value,
   the ladder summary, the signer's name), it never reaches the Explanation. No signer is ever named.
4. A hostile "model" patched in everywhere a model could hide reaches nothing, and no network is used.
5. The same input gives the same output, also across processes with different hash seeds.
6. a reviewer's E1 FAIL @ 5608354: every C2PA combination read through the real rung matches its
   card's polarity (R1), all-failed checks are not "not installed" (R2), and every IFCN domain is
   printed only as "published by <domain>" (R3).
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from secondlook import cli, explain as ex_mod, ladder
from secondlook.explain import (AI_TOOL_NAMES, BANNED, CHECK_NAMES, CONTEXT_FIELDS, IPTC_AI_FIELDS, LABEL,
                                MODEL_SEARCH_WORDS, MODEL_SEARCH_WORDS_ANY, PNG_KEYS, SUMMARIES, TEMPLATES,
                                TRUST_LISTS, banned_outside_sites, banned_words, card_template, explain, render)
from secondlook.rungs._sources import AI_TOOL_PATTERNS
from secondlook.rungs.factcheckers import DOMAINS
from secondlook.types import Evidence, Result

from . import explain_samples as S
from .explain_batteries import DRAFTS

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tmpfile(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    return f


@pytest.fixture
def cards(tmpfile):
    return S.every_card(tmpfile)


@pytest.fixture
def results(tmpfile):
    return S.results(tmpfile)


# ------------------------------------------------------------------ slot grammar
SLOT_NAMES = {
    "fields": set(IPTC_AI_FIELDS) | {"Make", "Model"} | set(PNG_KEYS) | set(CONTEXT_FIELDS),
    "words": set(MODEL_SEARCH_WORDS.values()) | {MODEL_SEARCH_WORDS_ANY},
    "tool": {name for _, name in AI_TOOL_NAMES},
    "trust_list": set(TRUST_LISTS.values()),
}
CHECK_WORDS = set(CHECK_NAMES.values())
SLOT_RE = {
    "trust_list": r".+?", "fields": r".+?", "tool": r".+?", "words": r".+?", "site": r"[a-z0-9.\-]+",
    "score": r"under 0\.01|over 0\.99|\d\.\d{2,6}", "cutoff": r"\d\.\d{2,6}", "matched": r"\d+", "bits": r"\d+",
    "shared": r"\d+", "typed": r"\d+", "claims": r"\d+ claims?", "days": r"about \d+ days?", "km": r"\d{1,3}(?:,\d{3})*",
    "reasons": r"\d+ reasons", "ran": r"no checks|\d+ checks? \(.+?\)", "failed": r"\d+ checks? \(.+?\)",
}


def _split(listing: str) -> list[str]:
    return [p for p in re.split(r", | and ", listing) if p]


def _slot_ok(name: str, value: str) -> bool:
    if name in SLOT_NAMES:
        parts = [value] if name != "fields" else _split(value)
        return all(p in SLOT_NAMES[name] for p in parts)
    if name == "site":
        return value in DOMAINS
    if name in ("ran", "failed"):
        m = re.fullmatch(r"(\d+) checks? \((.+)\)", value)
        if value == "no checks":
            return name == "ran"
        return bool(m) and all(p in CHECK_WORDS or re.fullmatch(r"\d+ others?", p) for p in _split(m.group(2)))
    return re.fullmatch(SLOT_RE[name], value) is not None


def _pattern(tpl: str) -> re.Pattern:
    out, pos = "", 0
    for m in re.finditer(r"\{(\w+)\}", tpl):
        out += re.escape(tpl[pos:m.start()]) + f"(?P<{m.group(1)}>{SLOT_RE[m.group(1)]})"
        pos = m.end()
    out += re.escape(tpl[pos:])
    return re.compile(out, re.IGNORECASE if tpl[:1] == "{" else 0)


def from_templates(sentence: str, library: dict[str, str]) -> str | None:
    """The key of the template this sentence is, with every slot valid; else None."""
    for key, tpl in library.items():
        body = tpl[0].upper() + tpl[1:] if tpl[:1] != "{" else tpl
        m = _pattern(body).fullmatch(sentence)
        if m and all(_slot_ok(k, v) for k, v in m.groupdict().items()):
            return key
    return None


def plain_ascii(text: str) -> bool:
    """ASCII, except the one no-break space that keeps "either way" together (DAT-850, PR #39)."""
    return text.replace("\u00a0", " ").isascii()


def all_text(ex) -> list[str]:
    return [ex.summary, ex.label] + [c.sentence for c in ex.per_card]


def check_explanation(ex) -> None:
    assert ex.label == LABEL and ex.source == "rules"
    assert from_templates(ex.summary, SUMMARIES) == ex.summary_template, ex.summary
    for c in ex.per_card:
        assert from_templates(c.sentence, TEMPLATES) == c.template, c.sentence
    for t in all_text(ex):
        assert plain_ascii(t) and not banned_outside_sites(t), t
        assert "—" not in t and "–" not in t


# ------------------------------------------------------------------ 1. banned words, every template x every card
def _all_slot_values(card: Evidence) -> dict[str, str]:
    """Every slot a template can take, from this card where it supplies one, else a fixed default."""
    values = {"trust_list": "the C2PA Trust List", "fields": "Make and Model", "words": MODEL_SEARCH_WORDS_ANY,
              "tool": "Midjourney", "site": "fullfact.org", "score": "0.50", "cutoff": "0.80", "matched": "128",
              "bits": "256", "shared": "2", "typed": "4", "claims": "2 claims", "days": "about 3 days", "km": "120",
              "reasons": "2 reasons", "ran": "1 check (C2PA)", "failed": "1 check (detector)"}
    _, slots = card_template(card)
    values.update(slots)
    return values


def test_every_template_on_every_sample_card_passes_the_banned_list(cards):
    rendered = 0
    for card in cards:
        slots = _all_slot_values(card)
        for library in (TEMPLATES, SUMMARIES):
            for key in library:
                used, text = render(key, slots, library)
                assert used == key, (key, slots)
                assert not banned_outside_sites(text), (key, text)
                if "{site}" not in library[key]:
                    assert not banned_words(text), (key, text)
                assert plain_ascii(text) and "—" not in text
                rendered += 1
    assert rendered == len(cards) * (len(TEMPLATES) + len(SUMMARIES))


def test_the_banned_list_holds_the_five_named_words_and_catches_their_forms():
    for word in ("real", "Really", "REAL", "authentic", "authenticity", "genuine", "genuinely", "fake", "faked",
                 "true", "untrue", "truth", "truly", "ｒｅａｌ"):   # last one: full-width letters, NFKC folds them
        assert banned_words(f"The image is {word}."), word
    for ok in ("trust list", "the cut-off", "structure", "construe", "falls", "realm-free"[:0] or "area"):
        assert not banned_words(ok), ok
    assert len(BANNED) >= 5


def test_every_card_template_is_reached_by_a_sample_card(cards):
    reached = {card_template(c, signed_present=False)[0] for c in cards}
    reached |= {card_template(c, signed_present=True)[0] for c in cards}
    assert reached == set(TEMPLATES), set(TEMPLATES) ^ reached


def test_every_summary_template_is_reached(results):
    assert {explain(r).summary_template for r in results} == set(SUMMARIES)


def test_every_card_template_pairs_what_it_shows_with_what_it_does_not():
    for key, tpl in TEMPLATES.items():
        assert re.search(r"\b(but|so|not|nothing)\b", tpl), key
        assert tpl.count(". ") == 0 and tpl.endswith("."), key   # one sentence
    for key, tpl in SUMMARIES.items():
        assert re.search(r"\b(but|so|not|nothing|no)\b", tpl), key


def test_every_sample_card_and_result_renders_from_the_templates(cards, results):
    for r in results + [Result("cant_tell", cards, "x", "t")]:
        ex = explain(r)
        assert len(ex.per_card) == len(r.evidence)
        check_explanation(ex)


def test_ai_tool_names_follow_the_rung_patterns():
    assert tuple(p for p, _ in AI_TOOL_NAMES) == AI_TOOL_PATTERNS


# ------------------------------------------------------------------ 2. structured fields only
def test_no_free_text_reaches_the_explanation(cards, results):
    for r in results + [Result("concerns", cards, f"{S.FREE} summary", "t")]:
        for t in all_text(explain(r)):
            assert S.FREE.lower() not in t.lower(), t


def test_no_signer_is_ever_named_only_the_trust_list(cards):
    """the project lead, DAT-848: signer names stay out of the explanation; the card still shows them."""
    official = explain(Result("signed_ai", [S.c2pa(issuer="Adobe Inc.", cn="Adobe")], "x", "t")).per_card[0]
    assert official.sentence == ("A record signed by a certificate on the C2PA Trust List says AI made this, but not "
                                 "who made it or why.")
    interim = explain(Result("signed_ai", [S.c2pa(trusted_by="interim", issuer="Adobe Inc.")], "x", "t")).per_card[0]
    assert interim.sentence.startswith("A record signed by a certificate on the C2PA interim trust list says AI")
    for name in ("Adobe", "Google", "Pixel", "Truepic", "Photo Is Not AI Generated Ltd", "Trust This Photo Completely"):
        card = S.c2pa(issuer=name, cn=name)
        for answer in ("signed_ai", "concerns", "cant_tell"):
            for t in all_text(explain(Result(answer, [card] + cards, "x", "t"))):
                assert name not in t, (name, t)
    assert "{signer}" not in "".join(TEMPLATES.values())


def test_classifier_numbers_never_land_on_the_wrong_side_of_the_cutoff():
    for score in (0.7996, 0.79999, 0.8, 0.80001, 0.805, 0.0001, 0.9999):
        card = S.classifier(score, 0.80)
        s = explain(Result("concerns", [card], "x", "t")).per_card[0].sentence
        shown = re.search(r"scored this (under 0\.01|over 0\.99|[\d.]+)", s).group(1)
        if shown[0].isdigit():
            assert (float(shown) >= 0.80) == (score >= 0.80), (score, s)
        assert ("at or above" in s) == (score >= 0.80)


# ------------------------------------------------------------------ 3. a reviewer's batteries, recast
def _hostile_copies(card: Evidence, draft: str):
    """The card with the draft placed in each free-text field, one field at a time."""
    for attr in ("finding", "proves", "does_not_prove"):
        yield dataclasses.replace(card, **{attr: draft})

    def strings(obj, path=()):
        if isinstance(obj, str):
            yield path
        elif isinstance(obj, dict):
            for k, v in obj.items():
                yield from strings(v, path + (k,))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from strings(v, path + (i,))

    for path in list(strings(card.raw)):
        c = copy.deepcopy(card)
        node = c.raw
        for p in path[:-1]:
            node = node[p]
        node[path[-1]] = draft
        yield c


@pytest.mark.parametrize("source,draft_id,draft", DRAFTS, ids=[f"{s}:{i}" for s, i, _ in DRAFTS])
def test_no_reviewer_draft_reaches_the_explanation(source, draft_id, draft, cards):
    seen = 0
    for card in cards:
        for hostile in _hostile_copies(card, draft):
            for answer in ("signed_ai", "signed_camera", "concerns", "cant_tell"):
                ex = explain(Result(answer, [hostile], draft, "t"))
                for t in all_text(ex):
                    assert draft.strip().lower() not in t.lower(), (draft_id, t)
                check_explanation(ex)
                seen += 1
    assert seen > 1000


# ------------------------------------------------------------------ 4. a hostile model reaches nothing
HOSTILE = [d for _, _, d in DRAFTS]


def test_explain_imports_no_model_or_network_code():
    tree = ast.parse((ROOT / "secondlook" / "explain.py").read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(("." * node.level) + (node.module or ""))
    assert mods <= {"__future__", "math", "re", "unicodedata", "dataclasses", ".ladder", ".rungs._sources",
                    ".types", ".rungs.factcheckers"}, mods


def test_a_hostile_model_patched_in_everywhere_reaches_nothing_on_the_page(monkeypatch, tmp_path):
    """Patch a hostile model into every place one could hide: the explain module (as model, llm,
    generate, explainer, client), Ollama and llama_cpp modules, urllib, http.client and sockets; and
    give the ladder a rung and a summary that carry the hostile text. The page's explanation is
    still template text only, and the hostile model is never called."""
    import http.client
    import socket
    import types
    import urllib.request

    from fastapi.testclient import TestClient

    from secondlook.web import app as web_app

    calls = []

    def hostile(*a, **k):
        calls.append((a, k))
        return {"response": HOSTILE[0], "text": HOSTILE[0], "summary": HOSTILE[0]}

    for name in ("model", "llm", "generate", "explainer", "client", "MODEL", "complete"):
        monkeypatch.setattr(ex_mod, name, hostile, raising=False)
    for mod in ("ollama", "llama_cpp", "openai", "anthropic", "transformers"):
        monkeypatch.setitem(sys.modules, mod, types.SimpleNamespace(generate=hostile, chat=hostile, Llama=hostile,
                                                                    pipeline=hostile))
    monkeypatch.setattr(urllib.request, "urlopen", hostile)
    monkeypatch.setattr(http.client.HTTPConnection, "request", hostile)
    monkeypatch.setattr(socket, "create_connection", hostile)
    monkeypatch.setattr(socket.socket, "connect", hostile)

    def hostile_rung(path):
        return [Evidence(rung=r, finding=d, proves=d, does_not_prove=d, strength="signed",
                         raw={"valid": True, "trusted": True, "trusted_by": "official", "source_class": "ai",
                              "signer_issuer": d, "software": d, "ai_tool_name": d, "score": 0.9, "threshold": 0.5,
                              "above_threshold": True, "publisher": d, "kind": d})
                for r in ("c2pa", "iptc", "exif", "classifier", "watermark", "claim") for d in HOSTILE[:6]]

    monkeypatch.setattr(ladder, "decide", lambda ev: ("concerns", " ".join(HOSTILE)))
    client = TestClient(web_app.create_app(stub=False, rungs=[("hostile", hostile_rung)]))
    r = client.post("/api/check", files={"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")})
    assert r.status_code == 200, r.text
    x = r.json()["explanation"]
    texts = [x["summary"], x["label"]] + [c["sentence"] for c in x["per_card"]]
    for t in texts:
        for d in HOSTILE:
            assert d.strip().lower() not in t.lower(), (d, t)
        assert not banned_words(t), t
    assert len(x["per_card"]) == 36 and x["source"] == "rules" and x["label"] == LABEL
    assert calls == []


# ------------------------------------------------------------------ 5. determinism
def test_same_input_same_output(results, cards):
    for r in results + [Result("concerns", cards, "x", "t")]:
        a, b = explain(r), explain(copy.deepcopy(r))
        assert a == b and a.as_dict() == b.as_dict()
        shuffled = copy.deepcopy(r)
        for e in shuffled.evidence:
            e.raw = dict(reversed(list(e.raw.items())))
        assert explain(shuffled) == a


def test_same_output_across_processes_and_hash_seeds():
    code = ("import json, sys; sys.path.insert(0, %r); from pathlib import Path; "
            "from tests import explain_samples as S; from secondlook.explain import explain; "
            "f = Path(sys.argv[1]); f.write_bytes(b'x'); "
            "print(json.dumps([explain(r).as_dict() for r in S.results(f)], sort_keys=True))") % str(ROOT)
    outs = set()
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        p = subprocess.run([sys.executable, "-c", code, str(ROOT / "tests" / f".e1-seed-{seed}.jpg")],
                           capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
        (ROOT / "tests" / f".e1-seed-{seed}.jpg").unlink(missing_ok=True)
        assert p.returncode == 0, p.stderr
        outs.add(p.stdout)
    assert len(outs) == 1


class _RaisingRaw(dict):
    def get(self, *a, **k):
        raise RuntimeError("odd raw")


def test_explain_never_raises_on_odd_input():
    odd = [Result("cant_tell", None, "x", "t"), Result(None, [None, 3, "x"], None, None),
           # N2: an unhashable or missing rung name, and a raw whose reads raise
           Result("concerns", [Evidence(["c2pa"], "f", "p", "d", "signed", raw={})], "x", "t"),
           Result("concerns", [Evidence({"r": 1}, "f", "p", "d", "signed", raw={}), S.iptc_source("ai")], "x", "t"),
           Result("concerns", [Evidence(None, "f", "p", "d", "signed", raw={})], "x", "t"),
           Result("concerns", [Evidence("c2pa", "f", "p", "d", "signed", raw=_RaisingRaw(valid=True))], "x", "t"),
           Result("concerns", [Evidence("c2pa", "f", "p", "d", "signed", raw=None)], "x", "t"),
           Result("concerns", [Evidence("classifier", "f", "p", "d", "signal", raw={"score": "0.9"})], "x", "t"),
           Result("concerns", [Evidence("exif", "f", "p", "d", "metadata", raw={"png_keys": "parameters"})], "x", "t")]
    for r in odd:
        ex = explain(r)
        check_explanation(ex)
        assert len(ex.per_card) == len(r.evidence or [])
    ex = explain(odd[3])
    assert [c.rung for c in ex.per_card] == ["unknown", "iptc"]
    assert ex.per_card[0].sentence == TEMPLATES["unknown"] and ex.per_card[1].template == "iptc.source.ai"


# ------------------------------------------------------------------ readability
def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 1 if any(ch.isdigit() for ch in word) else 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups) - (1 if w.endswith("e") and not w.endswith(("le", "ee")) and len(groups) > 1 else 0)
    return max(1, n)


def grade(sentence: str) -> float:
    """Flesch-Kincaid grade level of one sentence (syllables by vowel groups)."""
    words = [w for w in re.split(r"\s+", sentence) if re.search(r"\w", w)]
    syll = sum(_syllables(w) for w in words)
    return 0.39 * len(words) + 11.8 * syll / len(words) - 15.59


def test_reading_level(results, cards):
    sentences = []
    for r in results + [Result("concerns", cards, "x", "t")]:
        ex = explain(r)
        sentences += [ex.summary] + [c.sentence for c in ex.per_card]
    sentences = sorted(set(sentences))
    grades = sorted(grade(s) for s in sentences)
    mean, median = sum(grades) / len(grades), grades[len(grades) // 2]
    assert mean <= 8.5 and median <= 8.0, (mean, median)   # the job: about grade 6 to 8


# ------------------------------------------------------------------ web and CLI
def test_api_check_returns_the_explanation(tmpfile):
    from fastapi.testclient import TestClient

    from secondlook.web.app import create_app

    rungs = [("iptc", lambda p: [S.iptc_source("ai")]), ("classifier", lambda p: [S.classifier(0.91, 0.80)])]
    r = TestClient(create_app(stub=False, rungs=rungs)).post(
        "/api/check", files={"file": ("x.png", b"\x89PNG", "image/png")})
    body = r.json()
    x = body["explanation"]
    assert x["label"] == LABEL and x["source"] == "rules"
    assert x["summary"] == SUMMARIES["concerns"].format(reasons="2 reasons")
    assert [c["rung"] for c in x["per_card"]] == [e["rung"] for e in body["evidence"]]
    assert x["per_card"][1]["sentence"].startswith("A detector scored this 0.91 out of 1, at or above")


def test_stub_engine_explanation_says_demo():
    from fastapi.testclient import TestClient

    from secondlook.web.app import create_app

    r = TestClient(create_app(stub=True)).post("/api/check", files={"file": ("demo-signed-ai.png", b"x", "image/png")})
    x = r.json()["explanation"]
    assert x["summary_template"] == "demo" and all(c["template"] == "demo" for c in x["per_card"])


def test_page_shows_the_labelled_explanation_under_the_answer_as_plain_text():
    static = ROOT / "secondlook" / "web" / "static"
    html, js = (static / "index.html").read_text(), (static / "app.js").read_text()
    assert LABEL in html
    assert html.index('id="answer"') < html.index('id="explain"') < html.index('id="clues"')
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js
    assert "showExplanation(r.explanation)" in js


def test_cli_prints_the_explanation(tmpfile, capsys, monkeypatch):
    monkeypatch.setattr(ladder.rung_registry, "load", lambda *a, **k: [("iptc", lambda p: [S.iptc_source("ai")])])
    assert cli.main(["check", str(tmpfile)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["explanation"]["summary"] == SUMMARIES["concerns.one"]
    assert out["explanation"]["per_card"][0]["template"] == "iptc.source.ai"
    assert cli.main(["check", "--plain", str(tmpfile)]) == 0
    text = capsys.readouterr().out
    assert text.startswith(LABEL + ":\n" + SUMMARIES["concerns.one"]) and "- The file's IPTC label says AI made this" in text


# ------------------------------------------------------------------ 6. a reviewer's E1 FAIL @ 5608354
NS = "http://cv.iptc.org/newscodes/digitalsourcetype/"
TERM_SETS = [(), ("trainedAlgorithmicMedia",), ("digitalCapture",), ("minorHumanEdits",),
             ("digitalCapture", "trainedAlgorithmicMedia"), ("compositeWithTrainedAlgorithmicMedia",)]


def _manifest(terms, generator):
    return {"claim_generator_info": [{"name": generator}],
            "signature_info": {"issuer": "Acme Corp", "common_name": "Acme Signer", "time": "2026-10-01T00:00:00Z"},
            "assertions": [{"label": "c2pa.actions.v2", "data": {"actions": [
                {"action": "c2pa.created", "digitalSourceType": NS + t} for t in terms] or [{"action": "c2pa.edited"}]}}]}


def _store(state, terms, generator, ingredient):
    manifests = {"m1": _manifest(terms, generator)}
    if ingredient:
        manifests["m0"] = _manifest(("trainedAlgorithmicMedia",), "Some Tool")
    failure = [{"code": "claimSignature.mismatch"}] if state == "Invalid" else []
    if state == "Valid":
        failure = [{"code": "signingCredential.untrusted"}]
    return {"manifests": manifests, "active_manifest": "m1", "validation_state": state,
            "validation_results": {"activeManifest": {"failure": failure, "success": [{"code": "x"}]}}}


def _card_polarity(finding: str) -> tuple[str, bool]:
    """(class, ai part) as the rung's own card text says it, read independently of explain.py."""
    if "failed validation" in finding:
        return "invalid", False
    part = "with an AI-made ingredient in its history" in finding
    if "the two claims conflict" in finding:
        return "conflict", part
    if "declares it was made with generative AI" in finding or "an AI image tool" in finding:
        return "ai", part
    if "declares a camera capture" in finding:
        return "camera", part
    assert "records edits or a source" in finding, finding
    return "other", part


LINE_SAYS = {"invalid": "failed its checks", "ai": "says AI made this", "conflict": "names an AI tool",
             "camera": "camera", "other": "lists edits"}


def test_every_c2pa_combination_matches_its_cards_polarity(monkeypatch, tmpfile):
    """R1: the real rung builds each card from a store (validity x trust x 6 term sets x tool x AI
    ingredient = 72), the real ladder decides, and each line says what its card says."""
    pytest.importorskip("c2pa")
    from secondlook.rungs import c2pa as C

    monkeypatch.setattr(C, "trust_lists", lambda: [{"id": "official", "loaded": True},
                                                   {"id": "interim", "loaded": True}])
    seen = 0
    for state in ("Trusted", "Valid", "Invalid"):
        for terms in TERM_SETS:
            for generator in ("Acme Cam", "Midjourney v6"):
                for ingredient in (False, True):
                    store = _store(state, terms, generator, ingredient)
                    monkeypatch.setattr(C, "_read", lambda path, which, st=store: (copy.deepcopy(st), True))
                    [card] = C.check(str(tmpfile))
                    answer, _ = ladder.decide([card])
                    line = explain(Result(answer, [card], "x", "t")).per_card[0]
                    cls, part = _card_polarity(card.finding)
                    where = (state, terms, generator, ingredient, card.finding, line.sentence)
                    assert LINE_SAYS[cls] in line.sentence, where
                    assert line.template.split(".")[-1].split("_")[0] == cls or cls == "invalid", where
                    assert ("AI-made part" in line.sentence) == (part and cls != "ai"), where
                    if cls != "invalid":
                        assert ("not on a" in line.sentence) == (state != "Trusted"), where
                    if cls != "ai":
                        assert "AI made this" not in line.sentence, where
                    if answer == "signed_ai":
                        assert "says AI made this" in line.sentence, where
                    if answer == "signed_camera":
                        assert "says a camera took this" in line.sentence, where
                    seen += 1
    assert seen == 72


@pytest.mark.parametrize("trusted", [True, False])
def test_camera_and_ai_terms_from_an_ai_tool_read_as_ai_made(trusted):
    """R1 at 5608354: this card said "camera ... settles nothing" against an AI card and answer."""
    card = S.c2pa(trusted=trusted, source_class="ai", terms=("digitalCapture", "trainedAlgorithmicMedia"),
                  tool="midjourney")
    answer, _ = ladder.decide([card])
    line = explain(Result(answer, [card], "x", "t")).per_card[0]
    assert line.template == ("c2pa.trusted.ai" if trusted else "c2pa.untrusted.ai")
    assert "says AI made this" in line.sentence and "camera" not in line.sentence
    if trusted:
        assert answer == "signed_ai"


def test_checks_that_all_failed_are_not_called_not_installed(tmpfile):
    """R2: every installed rung raised, so the limit card names them as failed."""
    def boom(path):
        raise RuntimeError("broken")
    res = ladder.run(str(tmpfile), rungs=[("c2pa", boom), ("exif", boom)])
    assert res.answer == "cant_tell"
    lines = explain(res).per_card
    assert [c.template for c in lines] == ["limit.all_failed"]
    assert lines[0].sentence == ("No check could run on this file: 2 checks (C2PA and EXIF) failed, so nothing was "
                                 "read, and that says nothing about the image.")
    assert "installed" not in lines[0].sentence
    assert explain(ladder.run(str(tmpfile), rungs=[])).per_card[0].template == "limit.no_checks"


@pytest.mark.parametrize("domain", sorted(DOMAINS))
def test_every_ifcn_domain_is_printed_as_published_by(domain):
    """R3: a fact-checker's domain is an identity, printed only as "published by <domain>"."""
    for negation in (False, True):
        card = S.claim_match(publisher=domain, negation=negation)
        ex = explain(Result("cant_tell", [card], "x", "t"))
        check_explanation(ex)
        line = ex.per_card[0]
        assert line.template == ("claim.match.listed_negation" if negation else "claim.match.listed"), line
        assert line.sentence.startswith(f"A rating published by {domain}, on the IFCN list, is on a")
        assert not banned_outside_sites(line.sentence), line.sentence
        assert line.sentence.count(domain) == 1


def test_the_banned_rule_stays_whole_word_outside_published_by():
    assert not banned_outside_sites("A rating published by stopfake.org covers a claim.")
    assert not banned_outside_sites("A rating published by fakenews.pl covers a claim.")
    assert banned_outside_sites("A rating published by stopfake.org says it is fake.") == ["fake"]
    assert banned_outside_sites("A rating from fakenews.pl covers a claim.")           # not the fixed form
    assert banned_outside_sites("A rating published by fakenews.example covers it.")   # not on the IFCN list
    assert banned_outside_sites("A rating published by realnews.org covers it.")
    # a listed domain in any other slot or template still fails closed
    assert render("context.agree", {"fields": "fakenews.pl"}) == ("unknown", TEMPLATES["unknown"])
    assert render("claim.match.listed", {"site": "fakenews.pl", "shared": "1", "typed": "2"})[0] == "claim.match.listed"


# ------------------------------------------------------------------ R5 of a reviewer's FAIL @ d793b03
CLAIM_KEYS = {(True, False): "claim.match.listed", (False, False): "claim.match.unlisted",
              (True, True): "claim.match.listed_negation", (False, True): "claim.match.unlisted_negation"}


def _claim_line(card):
    ex = explain(Result("cant_tell", [card], "x", "t"))
    check_explanation(ex)
    return ex.per_card[0]


@pytest.mark.parametrize("listed", [True, False])
@pytest.mark.parametrize("negation", [False, True])
def test_typed_search_words_are_the_users_own(listed, negation):
    """No search_source mark (the user's own words): the typed templates, "your key words"."""
    publisher = "fullfact.org" if listed else "zzq7.example"
    for card in (S.claim_match(listed=listed, negation=negation, publisher=publisher),
                 S.model_claim_match(listed=listed, negation=negation, publisher=publisher)):
        if "search_source" in card.raw:   # any other mark than "model" is not a model reading either
            card.raw = {**card.raw, "search_source": "typed"}
        line = _claim_line(card)
        assert line.template == CLAIM_KEYS[listed, negation], line
        assert "model" not in line.sentence
        if not negation:
            assert line.sentence.endswith("3 of your 4 key words, but it may not be yours."), line.sentence


@pytest.mark.parametrize("listed", [True, False])
@pytest.mark.parametrize("negation", [False, True])
@pytest.mark.parametrize("read_from, words", [
    ("caption", "words a model took from your caption"),
    ("image", "words a model took from your image"),
    (None, "words a model picked"),            # no source noun on the card
    ("ZZQ7 your key words", "words a model picked"),   # not from the closed list: never copied
    (["caption"], "words a model picked"),
])
def test_model_search_words_are_never_called_yours(listed, negation, read_from, words):
    """R5: a card whose search words a model chose says so, names caption or image only from a closed
    list, and never calls them "your key words"."""
    publisher = "fullfact.org" if listed else "zzq7.example"
    line = _claim_line(S.model_claim_match(read_from, listed=listed, negation=negation, publisher=publisher))
    assert line.template == CLAIM_KEYS[listed, negation] + ".model", line
    assert "your key words" not in line.sentence and "ZZQ7" not in line.sentence.upper()
    if negation:
        assert line.sentence.endswith(f"a claim like the {words}, but a \"not\" may flip its meaning."), line.sentence
    else:
        assert line.sentence.endswith(f"a claim with 3 of 4 {words}, so check them."), line.sentence


def test_model_search_sentences_read_as_written():
    assert _claim_line(S.model_claim_match()).sentence == (
        "A rating published by fullfact.org, on the IFCN list, is on a claim with 3 of 4 words a model took from "
        "your caption, so check them.")
    assert _claim_line(S.model_claim_match("image", listed=False, publisher="zzq7.example")).sentence == (
        "A site not on the IFCN list rated a claim with 3 of 4 words a model took from your image, so check them.")
    assert _claim_line(S.model_claim_match("image", negation=True)).sentence == (
        "A rating published by fullfact.org, on the IFCN list, is on a claim like the words a model took from your "
        "image, but a \"not\" may flip its meaning.")


def test_model_marked_cards_from_glimmer_read_as_the_models_words(tmp_path):
    """The real marking path: glimmer.lookup_candidate on a small feed, then explain()."""
    from secondlook import glimmer
    from secondlook.rungs.claim import build_index
    feed = {"dataFeedElement": [{"item": [{
        "claimReviewed": "Photo shows the U.S. Pentagon is under attack.",
        "url": "https://www.politifact.com/factchecks/2023/may/25/x/",
        "reviewRating": {"alternateName": "Pants on Fire"},
        "author": {"name": "PolitiFact", "url": "https://www.politifact.com"},
        "datePublished": "2023-05-25"}]}]}
    (tmp_path / "feed.json").write_text(json.dumps(feed))
    db = tmp_path / "claims.sqlite"
    build_index(tmp_path / "feed.json", db)
    text = "Photo shows the U.S. Pentagon is under attack"
    for source, noun in (("caption", "caption"), ("on-image text", "image")):
        cards = glimmer.lookup_candidate(glimmer.CandidateClaim(text, source), db=db)["cards"]
        assert cards
        for card in cards:
            line = _claim_line(card)
            assert line.template.endswith(".model"), line
            assert f"words a model took from your {noun}" in line.sentence
            assert "your key words" not in line.sentence
    typed = glimmer.lookup_candidate(glimmer.CandidateClaim(text, "caption", by_model=False), db=db)["cards"]
    assert typed and all(not _claim_line(c).template.endswith(".model") for c in typed)
    assert all("your" in _claim_line(c).sentence and "key words" in _claim_line(c).sentence for c in typed)


@pytest.mark.parametrize("agreed, sentence", [
    (["date"], "The caption's date matches the file's own facts"),
    (["place"], "The caption's place matches the file's own facts"),
    (["date", "device"], "The caption's date and device match the file's own facts"),
    (["date", "place", "device"], "The caption's date, place and device match the file's own facts"),
])
def test_context_agreement_verb_agrees_with_the_fields(agreed, sentence):
    card = S.context(False, checks=[{"field": f, "outcome": "agree"} for f in agreed], compared=True)
    line = _claim_line(card)
    assert line.template == ("context.agree.one" if len(agreed) == 1 else "context.agree")
    assert line.sentence.startswith(sentence + ", but those facts are unsigned"), line.sentence


# ------------------------------------------------------------------ DAT-850: a high score is never "finding nothing"
CUT = 0.4319957792758942   # the committed v2 threshold, whose gate FAILed (docs/RESULTS-v2.md)


def _summary(cards, answer="cant_tell"):
    ex = explain(Result(answer, copy.deepcopy(cards), "x", "t"))
    check_explanation(ex)
    return ex


@pytest.mark.parametrize("gate,key,clause", [
    ("FAIL", "cant_tell.gate_failed", "That cut-off failed its test"),
    (None, "cant_tell.untested", "That cut-off has not passed its test"),
    ("INVALID", "cant_tell.untested", "That cut-off has not passed its test"),
])
def test_a_high_score_without_a_pass_is_named_in_the_cant_tell_summary(tmp_path, gate, key, clause):
    """Design SHOULD 4 on PR #33: the card sits under "What we found", so the summary names the score."""
    card = S.classifier(0.71, CUT, gate=gate)
    assert card.raw["above_threshold"] is True and card.raw["raises_concern"] is False
    assert ladder.decide([card])[0] == "cant_tell"
    ex = _summary([card, S.limit(["c2pa", "iptc", "exif", "classifier", "watermark"], tmp=tmp_path)])
    assert ex.summary_template == key
    # a reviewer's SHOULD 1 and 2 on PR #39: three short sentences, ending on "not evidence either way".
    assert ex.summary == ("Second Look can't tell how this was made. A detector scored it 0.71 out of 1, at or above "
                          f"our cut-off of 0.432. {clause}, so we do not count the score as evidence either\u00a0way.")
    assert "finding nothing" not in ex.summary and "raises no concern" not in ex.summary


@pytest.mark.parametrize("key", ["cant_tell.gate_failed", "cant_tell.untested"])
def test_each_sentence_of_the_score_summary_is_20_words_or_fewer(key):
    text = render(key, {"score": "over 0.99", "cutoff": "0.432"}, SUMMARIES)[1]
    sentences = text.split(". ")
    assert len(sentences) == 3
    assert all(len(s.split()) <= 20 for s in sentences), [len(s.split()) for s in sentences]


def test_every_summary_sentence_is_20_words_or_fewer():
    """DAT-850: the claims summary was one 22-word sentence (a reviewer, PR #39); no summary may exceed the rule."""
    slots = {"score": "over 0.99", "cutoff": "0.432", "reasons": "2 reasons"}
    for key in SUMMARIES:
        text = render(key, slots, SUMMARIES)[1]
        counts = [len(s.split()) for s in text.split(". ")]
        assert max(counts) <= 20, (key, counts)


def test_the_claims_summary_keeps_both_facts_in_two_short_sentences():
    text = SUMMARIES["claims"]
    assert text == ("Second Look gives no rating of its own. Any fact-check shown is the publisher's, on a claim that "
                    "may differ from yours.")


@pytest.mark.parametrize("key", ["cant_tell.gate_failed", "cant_tell.untested"])
def test_either_and_way_never_break_apart_in_the_score_summary(key):
    """a reviewer, PR #39: at 390 px "way." stood alone on a line; a no-break space holds the pair together."""
    text = render(key, {"score": "0.71", "cutoff": "0.432"}, SUMMARIES)[1]
    assert text.endswith("evidence either\u00a0way.") and "either way" not in text


def test_the_summary_score_never_reads_below_its_cut_off():
    """Two places would show 0.43 against a cut-off of 0.432; the summary takes a third place instead."""
    ex = _summary([S.classifier(0.43200, CUT, gate="FAIL")])
    assert "scored it 0.432 out of 1, at or above our cut-off of 0.432." in ex.summary, ex.summary
    ex = _summary([S.classifier(0.9999997, CUT, gate="FAIL")])
    assert "scored it over 0.99 out of 1, at or above our cut-off of 0.432." in ex.summary, ex.summary


@pytest.mark.parametrize("score,shown", [(0.4325, "0.432"), (0.43200, "0.432"), (0.71, "0.71"),
                                         (0.9999997, "over 0.99")])
@pytest.mark.parametrize("gate", ["FAIL", None])
def test_the_summary_and_the_card_print_the_same_score(score, shown, gate):
    """a reviewer's non-blocking point on PR #39: --plain and the desk note show both, so one number."""
    ex = _summary([S.classifier(score, CUT, gate=gate)])
    (card,) = ex.per_card
    assert f"scored it {shown} out of 1," in ex.summary, ex.summary
    assert card.sentence.startswith(f"A detector scored this {shown} out of 1, but our cut-off of 0.432 "), card.sentence


def test_a_low_score_or_a_passing_gate_keeps_the_plain_cant_tell_summary():
    assert _summary([S.classifier(0.033, CUT, gate="FAIL")]).summary_template == "cant_tell"
    assert _summary([S.classifier(0.033, CUT, gate="PASS")]).summary_template == "cant_tell"
    assert _summary([S.classifier(0.43)]).summary_template == "cant_tell"   # no cut-off frozen: information only


@pytest.mark.parametrize("concern", [True, False])
def test_a_high_score_with_a_passing_gate_in_a_cant_tell_answer_gets_the_unknown_summary(concern):
    """A passing gate makes a high score a concern, so a can't-tell answer beside it does not read cleanly
    (Codex and a reviewer, non-blocking on PR #39: the guard was tested on a low score only)."""
    card = S.classifier(0.71, CUT, gate="PASS")
    card.raw["raises_concern"] = concern
    ex = _summary([card])
    assert ex.summary_template == "unknown", ex.summary


def test_a_signed_gap_still_leads_the_summary_over_a_high_score(tmp_path):
    cards = [S.c2pa(source_class="other", terms=()), S.classifier(0.71, CUT, gate="FAIL"),
             S.limit(["c2pa"], signed=[S.c2pa()], tmp=tmp_path)]
    assert _summary(cards).summary_template == "cant_tell.signed_gap"


@pytest.mark.parametrize("change", [
    {"score": 0.1},                          # above_threshold says above, the score says below
    {"raises_concern": None},                # not the boolean False
    {"threshold": 2.0},                      # out of range
    {"score": float("nan")},
    {"raises_concern": True},                # a concern raised in a can't-tell answer (Codex, a reviewer on PR #39)
    {"above_threshold": False},              # the stored flag says below, the score says above
    {"above_threshold": 1},                  # not a real boolean
    {"above_threshold": "yes"},
    {"above_threshold": None},
])
def test_a_high_score_card_that_does_not_read_cleanly_gets_the_unknown_summary(change):
    """Never the "finding nothing" line for a card that says it is above the cut-off."""
    card = S.classifier(0.71, CUT, gate="FAIL")
    card.raw.update(change)
    ex = _summary([card])
    assert ex.summary_template == "unknown", ex.summary
