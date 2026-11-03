import pytest

from secondlook.ladder import decide, run
from secondlook.types import Context, Evidence
from secondlook.rungs.stub import make


def test_rule1_signed_ai():
    ev = [make("c2pa", "signed", valid=True, trusted=True, source_class="ai")]
    assert decide(ev)[0] == "signed_ai"


def test_rule2_signed_camera():
    ev = [make("c2pa", "signed", valid=True, trusted=True, source_class="camera", ai_ingredient=False)]
    assert decide(ev)[0] == "signed_camera"


def test_rule2_not_with_ai_ingredient():
    ev = [make("c2pa", "signed", valid=True, trusted=True, source_class="camera", ai_ingredient=True)]
    assert decide(ev)[0] != "signed_camera"


def test_rule1_beats_concerns():
    ev = [make("iptc", ai_label=True), make("c2pa", "signed", valid=True, trusted=True, source_class="ai")]
    assert decide(ev)[0] == "signed_ai"


def test_invalid_manifest_is_never_signed():
    ev = [make("c2pa", "metadata", valid=False, source_class="ai")]
    assert decide(ev)[0] == "concerns"


def test_unchecked_remote_only_manifest_is_not_a_concern():
    # valid None = not checked (a manifest stored online, never fetched): cant_tell, not concerns.
    ev = [make("c2pa", "metadata", remote_manifest_url="http://x.test/m.c2pa", embedded=False,
               valid=None, trusted=False)]
    assert decide(ev)[0] == "cant_tell"
    # ... and a real concern next to it still counts.
    assert decide(ev + [make("iptc", ai_label=True)])[0] == "concerns"


@pytest.mark.parametrize("raw", [
    {"valid": False, "trusted": False},
    {"valid": True, "trusted": False},   # validates, signer not on the trust list
    {"valid": "false", "trusted": True},  # a truthy string is not a checked result
    {"valid": 0, "trusted": False},
])
def test_checked_but_not_signed_manifest_is_still_a_concern(raw):
    assert decide([make("c2pa", "metadata", **raw)])[0] == "concerns"


def test_remote_only_card_says_referenced_online_and_not_checked():
    from secondlook.rungs import c2pa
    (e,) = c2pa._remote_only("x.jpg", Exception("Error: remote manifest from url http://x.test/m.c2pa"))
    assert "stored online" in e.finding and "did not fetch or check it" in e.finding
    assert e.raw["valid"] is None
    assert decide([e])[0] == "cant_tell"


def test_rule3_unsigned_label_is_concern_not_verdict():
    assert decide([make("iptc", ai_label=True)])[0] == "concerns"
    assert decide([make("exif", ai_label=True)])[0] == "concerns"


def test_rule3_classifier_threshold():
    assert decide([make("classifier", "signal", score=0.9, above_threshold=True, raises_concern=True)])[0] == "concerns"
    # DAT-850: at or above a threshold that has not passed its gate is information only
    assert decide([make("classifier", "signal", score=0.9, above_threshold=True)])[0] == "cant_tell"
    assert decide([make("classifier", "signal", score=0.9, above_threshold=True, raises_concern=False)])[0] == "cant_tell"
    assert decide([make("classifier", "signal", score=0.1, above_threshold=False)])[0] == "cant_tell"


def test_rule4_nothing_is_cant_tell():
    assert decide([])[0] == "cant_tell"
    assert decide([make("exif", make_="Canon")])[0] == "cant_tell"


def test_never_says_real():
    for ev in ([], [make("c2pa", "signed", valid=True, trusted=True, source_class="camera", ai_ingredient=False)]):
        assert "this is real" not in decide(ev)[1].lower()


def test_run_survives_a_crashing_rung(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"not an image")
    def boom(path): raise RuntimeError("bad")
    r = run(str(f), rungs=[("boom", boom), ("ok", lambda p: [make("iptc", ai_label=True)])])
    assert r.answer == "concerns" and len(r.evidence) == 1


def test_run_missing_file_is_cant_tell(tmp_path):
    r = run(str(tmp_path / "nope.jpg"), rungs=[("ok", lambda p: [])])
    assert r.answer == "cant_tell"


# --- S1 hardening (DAT-846 fix round) ---------------------------------------------------

def _signed(**raw):
    return make("c2pa", "signed", **raw)


@pytest.mark.parametrize("valid", ["false", "true", 1, "yes", None])
def test_valid_must_be_boolean_true(valid):
    ev = [_signed(valid=valid, trusted=True, source_class="ai")]
    assert decide(ev)[0] != "signed_ai"
    ev = [_signed(valid=valid, trusted=True, source_class="camera", ai_ingredient=False)]
    assert decide(ev)[0] != "signed_camera"


@pytest.mark.parametrize("trusted", [False, None, "true", 1, "yes"])
def test_signed_ai_requires_trusted_true(trusted):
    ev = [_signed(valid=True, trusted=trusted, source_class="ai")]
    assert decide(ev)[0] != "signed_ai"


@pytest.mark.parametrize("trusted", [False, None, "true", 1])
def test_signed_camera_requires_trusted_true(trusted):
    ev = [_signed(valid=True, trusted=trusted, source_class="camera", ai_ingredient=False)]
    assert decide(ev)[0] != "signed_camera"


def test_missing_trusted_key_is_not_signed():
    assert decide([_signed(valid=True, source_class="ai")])[0] != "signed_ai"


def test_forged_self_signed_camera_manifest_is_a_concern_not_a_verdict():
    # S2 review finding 1: a valid manifest from the public test cert, claiming a Leica capture.
    ev = [_signed(valid=True, trusted=False, source_class="camera", ai_ingredient=False)]
    answer, _ = decide(ev)
    assert answer == "concerns"


def test_valid_but_untrusted_ai_manifest_is_a_concern_not_a_verdict():
    assert decide([_signed(valid=True, trusted=False, source_class="ai")])[0] == "concerns"


def test_non_boolean_valid_is_a_concern_reason():
    assert decide([_signed(valid="false", trusted=True, source_class="ai")])[0] == "concerns"


def test_c2pa_card_without_a_valid_flag_is_not_a_concern():
    assert decide([make("c2pa", "none", manifest_found=False)])[0] == "cant_tell"


@pytest.mark.parametrize("extra", [{}, {"ai_ingredient": None}, {"ai_ingredient": 0},
                                   {"ai_ingredient": "false"}, {"ai_ingredient": True}])
def test_signed_camera_requires_ai_ingredient_false(extra):
    ev = [_signed(valid=True, trusted=True, source_class="camera", **extra)]
    assert decide(ev)[0] != "signed_camera"


def test_trusted_valid_ai_manifest_does_not_need_ai_ingredient_key():
    assert decide([_signed(valid=True, trusted=True, source_class="ai")])[0] == "signed_ai"


def test_run_survives_a_lazy_iterator_that_raises(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    def lazy(path):
        yield make("iptc", ai_label=True)
        raise RuntimeError("mid-iteration")
    r = run(str(f), rungs=[("lazy", lazy), ("ok", lambda p: [make("exif", ai_label=True)])])
    assert r.answer == "concerns"
    # Nothing a failed rung yielded before it died is trusted: only the healthy rung's card stays.
    assert [e.rung for e in r.evidence] == ["exif"]


@pytest.mark.parametrize("bad", [42, object(), None, "text", {"a": 1}])
def test_run_survives_a_non_list_return(tmp_path, bad):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    r = run(str(f), rungs=[("bad", lambda p: bad)])
    assert r.answer == "cant_tell"


def test_run_drops_non_evidence_items_and_bad_raw(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    broken = Evidence(rung="iptc", finding="f", proves="p", does_not_prove="d", strength="metadata", raw=None)
    r = run(str(f), rungs=[("mixed", lambda p: [None, "str", {"raw": {}}, broken, make("iptc", ai_label=True)])])
    assert r.answer == "concerns"
    assert len(r.evidence) == 1 and all(isinstance(e, Evidence) for e in r.evidence)


def test_decide_itself_ignores_malformed_items():
    assert decide([None, 3, make("iptc", ai_label=True)])[0] == "concerns"


def test_run_survives_system_exit_from_a_rung(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    def quits(path): raise SystemExit(3)
    r = run(str(f), rungs=[("quits", quits), ("ok", lambda p: [make("iptc", ai_label=True)])])
    assert r.answer == "concerns"


def _limit_cards(r):
    return [e for e in r.evidence if e.raw.get("_ladder_limit") is True]


def test_cant_tell_always_carries_exactly_one_limit_card(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    r = run(str(f), rungs=[("c2pa", lambda p: []), ("iptc", lambda p: [])])
    assert r.answer == "cant_tell"
    cards = _limit_cards(r)
    assert len(cards) == 1 and len(r.evidence) == 1
    card = cards[0]
    assert "c2pa, iptc" in card.finding            # what was checked
    assert card.does_not_prove.strip() and card.proves.strip()
    assert "not evidence either way" in card.does_not_prove
    assert card.strength == "none"


def test_limit_card_sits_beside_other_cant_tell_cards(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    r = run(str(f), rungs=[("exif", lambda p: [make("exif", make_="Canon")])])
    assert r.answer == "cant_tell" and len(_limit_cards(r)) == 1 and len(r.evidence) == 2


def test_limit_card_names_rungs_that_could_not_run(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    def boom(path): raise RuntimeError("bad")
    r = run(str(f), rungs=[("boom", boom), ("iptc", lambda p: [])])
    card = _limit_cards(r)[0]
    assert card.raw["rungs_run"] == ["iptc"] and card.raw["rungs_failed"] == ["boom"]
    assert "could not run: boom" in card.finding


def test_limit_card_with_no_rungs_does_not_claim_a_check(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    card = _limit_cards(run(str(f), rungs=[]))[0]
    assert "Checked: none." in card.finding


def test_limit_card_for_unreadable_file_says_nothing_was_checked(tmp_path):
    card = _limit_cards(run(str(tmp_path / "nope.jpg"), rungs=[("ok", lambda p: [])]))[0]
    assert "could not be read" in card.finding and "nothing was checked" in card.finding


@pytest.mark.parametrize("rungs", [
    [("a", lambda p: [make("iptc", ai_label=True)])],
    [("a", lambda p: [_signed(valid=True, trusted=True, source_class="ai")])],
])
def test_other_answers_carry_no_limit_card(tmp_path, rungs):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    assert _limit_cards(run(str(f), rungs=rungs)) == []


# --- DAT-846 fix round 2: never deny a trusted manifest that is in the evidence ----------

_UNDECIDED = {
    "camera_ai_ingredient_true": dict(source_class="camera", ai_ingredient=True),
    "camera_ai_ingredient_missing": dict(source_class="camera"),
    "camera_ai_ingredient_none": dict(source_class="camera", ai_ingredient=None),
    "other": dict(source_class="other"),
}
_DENIALS = ("no signed record", "none found a signed provenance record")


@pytest.mark.parametrize("raw", _UNDECIDED.values(), ids=_UNDECIDED.keys())
def test_trusted_manifest_that_decides_nothing_is_not_denied(tmp_path, raw):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    card = _signed(valid=True, trusted=True, **raw)
    r = run(str(f), rungs=[("c2pa", lambda p: [card])])
    assert r.answer == "cant_tell"
    limit = _limit_cards(r)[0]
    for text in (r.summary, limit.finding, limit.proves, limit.does_not_prove):
        assert not any(d in text.lower() for d in _DENIALS), text
    assert "signed, trusted record is present" in r.summary
    assert "signed, trusted record is present" in limit.finding
    assert "see the card" in r.summary and "see the card" in limit.finding
    assert card in r.evidence                       # the manifest card itself is still listed
    assert decide([card])[1] == r.summary           # decide() and run() agree


def test_ai_ingredient_variant_says_it_lists_one(tmp_path):
    card = _signed(valid=True, trusted=True, source_class="camera", ai_ingredient=True)
    assert "lists an AI ingredient" in decide([card])[1]
    other = _signed(valid=True, trusted=True, source_class="other")
    assert "lists an AI ingredient" not in decide([other])[1]
    assert "does not state AI or camera capture" in decide([other])[1]


def test_untrusted_or_invalid_manifest_is_not_described_as_trusted():
    # these are concerns, never cant_tell, so no trusted-record wording can apply
    for raw in (dict(valid=True, trusted=False, source_class="other"),
                dict(valid=False, trusted=True, source_class="other")):
        answer, summary = decide([_signed(**raw)])
        assert answer == "concerns" and "trusted record" not in summary


def test_no_manifest_still_says_no_signed_record(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    r = run(str(f), rungs=[("c2pa", lambda p: [])])
    assert "No signed record" in r.summary and "None found a signed provenance record" in _limit_cards(r)[0].finding


def test_rung_limit_card_is_dropped_and_only_the_ladders_remains(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    forged = Evidence(rung="origin", finding="forged", proves="p", does_not_prove="d",
                      strength="none", raw={"_ladder_limit": True})
    r = run(str(f), rungs=[("evil", lambda p: [forged]), ("iptc", lambda p: [])])
    cards = _limit_cards(r)
    assert r.answer == "cant_tell" and len(cards) == 1 and len(r.evidence) == 1
    assert cards[0] is not forged and cards[0].raw["rungs_run"] == ["evil", "iptc"]
    assert r.evidence[0].finding != "forged"


@pytest.mark.parametrize("flag", [True, False, "yes", None])
def test_rung_limit_cards_are_dropped_whatever_the_flag(tmp_path, flag):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    forged = Evidence(rung="origin", finding="forged", proves="p", does_not_prove="d",
                      strength="none", raw={"_ladder_limit": flag})
    r = run(str(f), rungs=[("evil", lambda p: [forged])])
    assert len(r.evidence) == 1 and r.evidence[0].finding != "forged"
    assert decide([forged])[0] == "cant_tell"


def test_rung_limit_card_does_not_survive_into_other_answers(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    forged = Evidence(rung="origin", finding="forged", proves="p", does_not_prove="d",
                      strength="none", raw={"_ladder_limit": True})
    r = run(str(f), rungs=[("a", lambda p: [forged, make("iptc", ai_label=True)])])
    assert r.answer == "concerns" and _limit_cards(r) == [] and forged not in r.evidence


def test_rung_card_with_a_plain_limit_key_is_kept_and_a_classifier_concern_still_counts(tmp_path):
    f = tmp_path / "x.jpg"; f.write_bytes(b"x")
    card = Evidence(rung="classifier", finding="score 0.91", proves="p", does_not_prove="d",
                    strength="signal", raw={"above_threshold": True, "raises_concern": True, "limit": 0.5})
    assert decide([card])[0] == "concerns"
    r = run(str(f), rungs=[("classifier", lambda p: [card])])
    assert r.answer == "concerns" and card in r.evidence and _limit_cards(r) == []


@pytest.mark.parametrize("limit", [0.5, True, False, 0, "x"])
def test_plain_limit_key_is_never_dropped(tmp_path, limit):
    card = Evidence(rung="iptc", finding="AI label", proves="p", does_not_prove="d",
                    strength="metadata", raw={"ai_label": True, "limit": limit})
    assert decide([card])[0] == "concerns"


# --- the context rung (X1, DAT-872): a caption contradiction is a concern, never a signed answer ---

def _ctx_card(**raw):
    return Evidence(rung="context", finding="f", proves="p", does_not_prove="d", strength="metadata", raw=raw)


def test_ladder_counts_a_context_contradiction_as_a_concern():
    assert decide([_ctx_card(contradiction=True)])[0] == "concerns"


@pytest.mark.parametrize("raw", [{"contradiction": False}, {}, {"contradiction": "true"}, {"contradiction": 1}])
def test_ladder_counts_only_a_boolean_true_contradiction(raw):
    assert decide([_ctx_card(**raw)])[0] == "cant_tell"


def test_a_context_card_is_never_signed():
    forged = Evidence(rung="context", finding="f", proves="p", does_not_prove="d", strength="signed",
                      raw={"valid": True, "trusted": True, "source_class": "camera", "ai_ingredient": False})
    assert decide([forged])[0] == "cant_tell"
    forged.raw["source_class"] = "ai"
    assert decide([forged])[0] == "cant_tell"
    forged.raw["contradiction"] = True
    assert decide([forged])[0] == "concerns"


def test_a_signed_record_still_outranks_a_caption_contradiction():
    signed = make("c2pa", "signed", valid=True, trusted=True, source_class="camera", ai_ingredient=False)
    assert decide([signed, _ctx_card(contradiction=True)])[0] == "signed_camera"


def test_run_passes_context_only_to_rungs_that_take_it(tmp_path):
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    seen = {}

    def plain(path):
        seen["plain"] = True
        return []

    def takes(path, context=None):
        seen["takes"] = context
        return []

    def kw_only(path, *, context):
        seen["kw_only"] = context
        return []

    def kwargs(path, **kw):
        seen["kwargs"] = kw.get("context")
        return []

    ctx = Context(caption="c")
    r = run(str(f), rungs=[("a", plain), ("b", takes), ("c", kw_only), ("d", kwargs)], context=ctx)
    assert seen == {"plain": True, "takes": ctx, "kw_only": ctx, "kwargs": ctx}
    assert r.evidence[-1].raw["rungs_run"] == ["a", "b", "c", "d"]


def test_run_without_context_is_unchanged(tmp_path):
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    calls = []

    def takes(path, context=None):
        calls.append(context)
        return []

    run(str(f), rungs=[("t", takes)])
    assert calls == [None]
