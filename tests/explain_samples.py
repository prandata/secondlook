"""Sample cards for the E1 explanation tests: one or more per template key.

Cards come from the real builders where the rung has one (claim.to_evidence, the voice cards, the
ladder's own limit card) and otherwise copy the rung's raw shape field for field (c2pa.py, iptc.py,
exif.py, classifier.py, watermark.py, and context.py on origin/x1-context c7dae20).
Every free-text field holds a marker word (FREE) so the tests can show it never reaches a sentence.
"""
from __future__ import annotations

import copy

from secondlook import ladder
from secondlook.rungs import claim as claim_rung
from secondlook.rungs import voice
from secondlook.types import Evidence, Result

FREE = "Zanzibarquux"   # stands in for any text the file, the feed or a transcript supplied
LISTS = [{"id": "official", "loaded": True}, {"id": "interim", "loaded": True}]


def _ev(rung, strength, raw, finding=None):
    return Evidence(rung=rung, finding=finding or f"{FREE} finding", proves=f"{FREE} proves",
                    does_not_prove=f"{FREE} limit", strength=strength, raw=raw)


def c2pa(valid=True, trusted=True, trusted_by="official", source_class="ai", ai_ingredient=False, tool=None,
         terms=("trainedAlgorithmicMedia",), issuer="Google LLC", cn="Pixel Camera"):
    raw = {
        "sdk": "c2pa-python 0.0", "validation_state": "Trusted" if trusted else ("Valid" if valid else "Invalid"),
        "valid": valid, "trusted": trusted, "trusted_by": trusted_by if trusted else None,
        "trust_list": None, "trust_lists": LISTS, "failures": [] if valid else ["claimSignature.mismatch"],
        "status_codes": [], "signer_issuer": issuer, "signer_common_name": cn, "sign_time": "2026-10-01T00:00:00Z",
        "claim_generator": f"{FREE} generator", "digital_source_types": list(terms), "source_class": source_class,
        "ai_ingredient": ai_ingredient, "ai_tool_name": tool,
        "ai_label": bool(valid and not trusted and source_class == "ai") or bool(valid and ai_ingredient),
        "actions": ["c2pa.created"], "ingredient_count": 0, "embedded": True,
    }
    return _ev("c2pa", "signed" if valid and trusted else "metadata", raw)


def c2pa_remote():
    return _ev("c2pa", "metadata", {"remote_manifest_url": f"https://{FREE}.example/m", "embedded": False,
                                    "valid": None, "trusted": False})


def iptc_source(cls):
    term = {"ai": "trainedAlgorithmicMedia", "camera": "digitalCapture", "other": "minorHumanEdits"}[cls]
    return _ev("iptc", "metadata", {"field": "Iptc4xmpExt:DigitalSourceType",
                                    "values": [f"http://cv.iptc.org/newscodes/digitalsourcetype/{term}"],
                                    "source_class": cls, "ai_label": cls == "ai"})


def iptc_ai_fields():
    return _ev("iptc", "metadata", {"fields": {"AISystemUsed": [FREE], "AIPromptInformation": [FREE]},
                                    "ai_label": True})


EXIF_RAW = {"format": "JPEG", "make": f"{FREE} make", "model": f"{FREE} model", "software": f"{FREE} soft",
            "datetime_original": "2026:10:01 10:00:00", "lens": None, "has_gps": False}


def exif_camera():
    return _ev("exif", "metadata", dict(EXIF_RAW, source_class="camera"))


def exif_software(tool=None):
    return _ev("exif", "metadata", dict(EXIF_RAW, ai_tool_name=tool, ai_label=bool(tool)))


def exif_png():
    return _ev("exif", "metadata", dict(EXIF_RAW, format="PNG", png_keys=["parameters"], ai_label=True))


def classifier(score, threshold=None, above=None, gate="PASS"):
    """gate: the gate status for the frozen threshold ("PASS", "FAIL", "INVALID", or None for none on record)."""
    raw = {"score": score, "model": "Community Forensics", "model_repo": "x", "model_revision": "x",
           "weights_sha256": "x", "device": "cpu", "seconds": 0.1}
    if threshold is None:
        raw.update(threshold=None, above_threshold=False, threshold_file_sha256=None, gate_result=None,
                   raises_concern=False)
    else:
        raw.update(threshold=threshold, above_threshold=score >= threshold if above is None else above,
                   gate_result=gate)
        raw["raises_concern"] = raw["above_threshold"] and gate == "PASS"
    return _ev("classifier", "signal" if raw["above_threshold"] or threshold is None else "none", raw)


def watermark(got, present=None):
    raw = {"present": got >= 175 if present is None else present, "matched_bits": got, "bits": 256,
           "min_match": 175, "p_value_if_unmarked": 0.5, "key_id": "x", "demo_key": True, "model": "x",
           "weights_sha256": "x", "device": "cpu", "seconds": 0.1}
    return _ev("watermark", "signal" if raw["present"] else "none", raw)


def limit(ran, failed=(), signed=(), readable=True, tmp=None):
    path = str(tmp) if readable and tmp else "/nonexistent/secondlook-e1.jpg"
    return ladder._limit_card(path, list(ran), list(failed), list(signed))


def claim_match(listed=True, negation=False, publisher="fullfact.org", shared=3, typed=4):
    m = claim_rung.ClaimMatch(
        claim_reviewed=f"{FREE} claim", rating=f"{FREE} rating", rating_explanation=f"{FREE} why",
        publisher=publisher, publisher_url=f"https://{publisher}/", is_listed_fact_checker=listed,
        listed_as=f"{FREE} author", review_url=f"https://{publisher}/{FREE}", date_published="2026-10-01",
        claimed_by=f"{FREE} person", claim_date=None, negation_differs=negation, similarity=0.75,
        shared_terms=shared, typed_terms=typed, reviewed_terms=4)
    lookup = claim_rung.ClaimLookup("found", "1 existing fact-check found.", f"{FREE} query", [m], {})
    return claim_rung.to_evidence(lookup)[0]


def model_claim_match(read_from="caption", **kw):
    """A claim card whose search words a model chose, marked as glimmer.lookup_candidate marks it."""
    card = claim_match(**kw)
    card.raw = {**card.raw, "search_source": "model", "search_from": read_from}
    card.finding = f"{card.finding} Search words came from a model reading of your {FREE}; check them."
    return card


def claim_unavailable():
    return claim_rung.to_evidence(claim_rung.ClaimLookup("unavailable", "x", f"{FREE}", [], {}))[0]


def voice_cards():
    t = voice.Transcript(text=f"{FREE} said this", segments=[], model="small.en", model_sha256="x",
                         engine="whisper.cpp", seconds=12.0)
    out = [voice._how_made_card(), voice._transcript_card(t, [f"{FREE} claim"])]
    for kind, (finding, proves) in voice._NOT_READ.items():
        out.append(voice._status_card(kind, finding, proves))
    out.append(voice._status_card("no_speech", voice.NO_SPEECH, "x"))
    out.append(voice._status_card("no_claims", "x", "x"))
    out.append(voice._status_card("no_match", "x", "x", {"claims": [f"{FREE} one", f"{FREE} two"]}))
    m = claim_match()
    m.raw = m.raw | {"source": "voice", "kind": "claim", "heard": {"sentence": FREE, "start_ms": 0}}
    out.append(m)
    return out


def context(contradiction, field=None, gap=None, checks=(), compared=False):
    raw = {"claimed": {"date": FREE, "place": FREE, "device": FREE, "source": "typed"}, "checks": list(checks),
           "rules": {"date_days": 1, "place_km": 50, "device": "make"}, "compared": compared,
           "contradiction": contradiction}
    if field:
        raw.update(field=field, disagreement=gap)
    return _ev("context", "metadata" if contradiction else "none", raw)


def every_card(tmp) -> list[Evidence]:
    """At least one card per card template, plus a few edge shapes."""
    signed = c2pa()
    return [
        c2pa_remote(), c2pa(valid=False, trusted=False),
        c2pa(), c2pa(trusted_by="interim", issuer="Adobe Inc.", cn="firefly-prod"),
        c2pa(source_class="camera", terms=("digitalCapture",)),
        c2pa(source_class="camera", terms=("digitalCapture",), ai_ingredient=True),
        c2pa(source_class="other", terms=()), c2pa(source_class="other", terms=(), ai_ingredient=True),
        c2pa(source_class="other", terms=("digitalCapture",), tool="adobe_firefly"),
        c2pa(trusted=False), c2pa(trusted=False, source_class="camera", terms=("digitalCapture",)),
        c2pa(trusted=False, source_class="other", terms=()),
        c2pa(trusted=False, source_class="other", terms=("digitalCapture",), tool="midjourney"),
        c2pa(source_class="other", terms=("digitalCapture",), tool="midjourney", ai_ingredient=True),
        c2pa(trusted=False, source_class="camera", terms=("digitalCapture",), ai_ingredient=True),
        c2pa(trusted=False, source_class="other", terms=(), ai_ingredient=True),
        c2pa(trusted=False, source_class="other", terms=("digitalCapture",), tool="midjourney", ai_ingredient=True),
        # R1: a capture term and an AI term from an AI tool reads as AI-made, as the rung's card does
        c2pa(source_class="ai", terms=("digitalCapture", "trainedAlgorithmicMedia"), tool="midjourney"),
        c2pa(trusted=False, source_class="ai", terms=("digitalCapture", "trainedAlgorithmicMedia"), tool="midjourney"),
        c2pa(issuer=f"{FREE} wrote this image", cn=f"the image is {FREE}"),
        iptc_source("ai"), iptc_source("camera"), iptc_source("other"), iptc_ai_fields(),
        exif_camera(), exif_software(), exif_software("midjourney"), exif_software("stable diffusion"), exif_png(),
        classifier(0.000759), classifier(0.43), classifier(0.995), classifier(0.91, 0.80), classifier(0.12, 0.80),
        classifier(0.7996, 0.80), classifier(0.0001, 0.80), classifier(0.91, 0.80, above=False),
        classifier(float("nan")),
        # DAT-850: a frozen cut-off that failed its gate, or has no passing gate, is information only
        classifier(0.71, 0.4319957792758942, gate="FAIL"), classifier(0.12, 0.4319957792758942, gate="FAIL"),
        classifier(0.71, 0.4319957792758942, gate=None), classifier(0.71, 0.4319957792758942, gate="INVALID"),
        watermark(201), watermark(128), watermark(128, present=True),
        limit(["c2pa", "iptc", "exif", "classifier", "watermark"], tmp=tmp),
        limit(["c2pa", "iptc", "exif"], ["classifier", "watermark"], tmp=tmp),
        limit(["c2pa", "exif"], signed=[signed], tmp=tmp),
        limit(["c2pa"], readable=False), limit([], tmp=tmp), limit([], ["c2pa", "exif"], tmp=tmp),
        claim_unavailable(), claim_match(), claim_match(listed=False, publisher=f"{FREE}.example"),
        claim_match(publisher="fakenews.pl", negation=True),   # R3: a listed domain holding a banned stem
        claim_match(negation=True), claim_match(listed=False, negation=True, publisher=f"{FREE}.example"),
        # R5: the search words came from a model reading, not from the user
        model_claim_match(), model_claim_match("image", listed=False, publisher=f"{FREE}.example"),
        model_claim_match(None, negation=True), model_claim_match(f"{FREE}", listed=False, negation=True,
                                                                  publisher=f"{FREE}.example"),
        *voice_cards(),
        context(True, "date", {"days_apart": 41.6}), context(True, "place", {"distance_km": 1234.4}),
        context(True, "device", {"file_make": "canon"}),
        context(False, checks=[{"field": "date", "outcome": "agree"}, {"field": "place", "outcome": "agree"}],
                compared=True),
        context(False, checks=[{"field": "place", "outcome": "agree"}, {"field": "date", "outcome": "no_file_fact"}],
                compared=True),
        context(False, checks=[{"field": "date", "outcome": "no_file_fact"}]),
        _ev("classifier", "none", {"stub": True}),
        _ev("origin", "none", {"something": FREE}),
    ]


def results(tmp) -> list[Result]:
    """One Result per summary template, from the cards above, decided by the real ladder."""
    def res(cards, answer=None):
        cards = copy.deepcopy(cards)
        a, s = ladder.decide(cards)
        return Result(answer=answer or a, evidence=cards, summary=s, version="test")
    lim = [limit(["c2pa", "iptc", "exif", "classifier", "watermark"], tmp=tmp)]
    return [
        res([c2pa()]), res([c2pa(source_class="camera", terms=("digitalCapture",))]),
        res([iptc_source("ai")]), res([iptc_source("ai"), classifier(0.91, 0.80)]),
        res(lim), res([c2pa(source_class="other", terms=()), limit(["c2pa"], signed=[c2pa()], tmp=tmp)]),
        res([classifier(0.71, 0.4319957792758942, gate="FAIL")] + lim),
        res([classifier(0.71, 0.4319957792758942, gate=None)] + lim),
        Result("cant_tell", voice_cards(), voice.SUMMARY, "test"),
        Result("cant_tell", [claim_match(), claim_match(listed=False)], "x", "test"),
        res([_ev("c2pa", "signed", {"stub": True})]),
        Result("bogus", [exif_camera()], "x", "test"),
    ]
