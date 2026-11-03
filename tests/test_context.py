"""Context rung (X1, DAT-872): the caption's date, place and device against the file's own facts."""
import json
from pathlib import Path

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational as R

from secondlook import cli, extract, places
from secondlook.ladder import decide, run
from secondlook.rungs import context as cx
from secondlook.types import ClaimFacts, Context, Evidence

FIXTURES = Path(__file__).parent / "fixtures" / "geonames"
# Real GeoNames points (from the fixture rows): Paris FR 48.85341, 2.3488; Paris TX 33.66094, -95.55551.
PARIS_FR = ((48, 51, R(1228, 100)), (2, 20, R(5568, 100)))   # 48.85341 N, 2.34880 E
PARIS_TX = ((33, 39, R(3938, 100)), (95, 33, R(1984, 100)))  # 33.66094 N, 95.55551 W
UTAH_SIGN = ((41, 33, R(734, 100)), (112, 4, R(825, 100)))   # the Pixel 10 Pro sample's own GPS
REIMS = ((R(4926526, 100000), 0, 0), (R(402853, 100000), 0, 0))  # 49.26526 N, 4.02853 E, 131 km from Paris


@pytest.fixture(scope="session")
def geodb(tmp_path_factory):
    db = tmp_path_factory.mktemp("geo") / "geonames.sqlite"
    places.build(FIXTURES / "cities-mini.txt", FIXTURES / "countryInfo.txt", FIXTURES / "admin1CodesASCII.txt", db,
                 sha256="fixture")
    return db


@pytest.fixture(autouse=True)
def _use_fixture_db(geodb, monkeypatch):
    monkeypatch.setenv(places.DB_ENV, str(geodb))


def photo(tmp_path, name="p.jpg", make_=None, model=None, taken=None, offset=None, gps=None, ns="N", ew="E",
          h_error=None):
    ex = Image.Exif()
    if make_:
        ex[cx.MAKE] = make_
    if model:
        ex[cx.MODEL] = model
    sub = {}
    if taken:
        sub[cx.DATETIME_ORIGINAL] = taken
    if offset:
        sub[cx.OFFSET_TIME_ORIGINAL] = offset
    if sub:
        ex.get_ifd(cx.EXIF_IFD).update(sub)
    if gps:
        lat, lon = gps
        g = {cx.GPS_LAT_REF: ns, cx.GPS_LAT: tuple(R(x) if isinstance(x, int) else x for x in lat),
             cx.GPS_LON_REF: ew, cx.GPS_LON: tuple(R(x) if isinstance(x, int) else x for x in lon)}
        if h_error is not None:
            g[cx.GPS_H_ERROR] = R(h_error)
        ex.get_ifd(cx.GPS_IFD).update(g)
    p = tmp_path / name
    Image.new("RGB", (8, 8)).save(p, "JPEG", exif=ex)
    return str(p)


def facts(date=None, place=None, device=None, source="typed"):
    return Context(facts=ClaimFacts(date=date, place=place, device=device, source=source))


def contradictions(cards):
    return [e for e in cards if e.raw.get("contradiction") is True]


def assert_card_contract(cards):
    for e in cards:
        assert isinstance(e, Evidence) and e.rung == "context"
        assert e.strength in ("metadata", "none"), "a context card is never signed or a signal"
        assert e.does_not_prove == cx.DOES_NOT_PROVE
        if e.raw["contradiction"]:
            assert e.strength == "metadata" and e.proves == "The caption and the file's facts disagree."
            assert e.finding.startswith("The caption says ") and " The file's own " in e.finding
        else:
            assert e.strength == "none"


# --- each contradiction -----------------------------------------------------------------------------

def test_date_contradiction(tmp_path):
    cards = cx.check(photo(tmp_path, taken="2026:10:01 12:00:00"), facts(date="2026-10-06"))
    assert_card_contract(cards)
    (e,) = contradictions(cards)
    assert e.finding == ("The caption says 6 October 2026. The file's own EXIF capture time says "
                         "1 October 2026, 12:00:00 (no time zone recorded).")
    assert e.raw["field"] == "date" and e.raw["disagreement"]["days_apart"] == 5


@pytest.mark.parametrize("claimed,contradiction", [
    ("2026-10-01", False), ("2026-10-03", False), ("2026-09-29", False),  # within 2 days
    ("2026-10-04", True), ("2026-09-28", True),                           # 3 days: beyond
])
def test_date_tolerance_is_two_days(tmp_path, claimed, contradiction):
    cards = cx.check(photo(tmp_path, taken="2026:10:01 12:00:00"), facts(date=claimed))
    assert bool(contradictions(cards)) is contradiction


def test_place_contradiction(tmp_path):
    # "Paris" is unambiguous: Paris, France has more than 10 times the people of any other Paris.
    cards = cx.check(photo(tmp_path, gps=UTAH_SIGN, ew="W"), facts(place="Paris"))
    assert_card_contract(cards)
    (e,) = contradictions(cards)
    assert e.finding.startswith("The caption says Paris. The file's own GPS position says a point about ")
    assert e.finding.endswith("km from Paris, Île-de-France, France, in another first-level region (GeoNames).")
    d = e.raw["disagreement"]
    assert d["nearest_match"]["country"] == "FR" and 8000 < d["distance_km"] < 8500
    assert [r["reading"] for r in d["readings"]] == ["city"]


def test_device_contradiction(tmp_path):
    cards = cx.check(photo(tmp_path, make_="Canon", model="Canon EOS R5"), facts(device="iPhone 15 Pro"))
    (e,) = contradictions(cards)
    assert e.finding == "The caption says iPhone 15 Pro. The file's own EXIF camera make says Canon, model Canon EOS R5."
    assert e.raw["disagreement"]["claimed_makes"] == ["apple"] and e.raw["disagreement"]["file_make"] == "canon"


def test_every_disagreement_gets_its_own_card(tmp_path):
    p = photo(tmp_path, make_="Canon", taken="2026:10:01 12:00:00", gps=PARIS_FR)
    cards = cx.check(p, facts(date="2026-12-25", place="Sydney", device="Pixel 9"))
    assert sorted(e.raw["field"] for e in contradictions(cards)) == ["date", "device", "place"]
    assert len(cards) == 3
    assert_card_contract(cards)


# --- agreement ----------------------------------------------------------------------------------------

def test_agreement_is_one_none_card_and_never_a_concern(tmp_path):
    p = photo(tmp_path, make_="Apple", model="iPhone 15 Pro", taken="2026:10:02 08:00:00", gps=PARIS_FR)
    cards = cx.check(p, facts(date="2026-10-01", place="Paris, France", device="iPhone 15"))
    assert_card_contract(cards)
    (e,) = cards
    assert e.strength == "none" and e.raw["contradiction"] is False and e.raw["compared"] is True
    assert e.finding.startswith("No disagreement found between the caption and the file's own facts.")
    assert "Date: within 2 days" in e.finding and "Place: within 50 km" in e.finding and "Device: the same make" in e.finding
    assert e.proves == "Only that the facts compared here do not disagree."
    assert decide(cards)[0] == "cant_tell"


@pytest.mark.parametrize("claimed,file_make", [
    ("OM System OM-1", "OLYMPUS IMAGING CORP."), ("Pentax K-3", "RICOH IMAGING COMPANY, LTD."),
    ("Leica", "HUAWEI"), ("Redmi Note 12", "Xiaomi"), ("Nikon Z6", "NIKON CORPORATION"),
    ("Samsung Galaxy S24", "samsung"),
])
def test_same_maker_or_family_is_not_clearly_different(tmp_path, claimed, file_make):
    assert not contradictions(cx.check(photo(tmp_path, make_=file_make), facts(device=claimed)))


@pytest.mark.parametrize("claimed,file_make", [("my phone", "Canon"), ("iPhone", "Acme Imaging"),
                                               ("Google Nexus 5", "LGE")])
def test_a_make_that_cannot_be_read_is_not_compared(tmp_path, claimed, file_make):
    (e,) = cx.check(photo(tmp_path, make_=file_make), facts(device=claimed))
    assert e.strength == "none" and e.raw["compared"] is False


def test_a_place_of_that_name_near_the_gps_position_agrees(tmp_path):
    p = photo(tmp_path, gps=PARIS_TX, ew="W")
    assert not contradictions(cx.check(p, facts(place="Paris")))           # Paris, Texas is right there
    (e,) = contradictions(cx.check(p, facts(place="Paris, France")))       # the qualifier rules Texas out
    assert e.raw["disagreement"]["nearest_match"]["country"] == "FR"


# --- missing EXIF and nothing to compare --------------------------------------------------------------

def test_missing_exif_is_nothing_to_compare_and_never_a_concern(tmp_path):
    (e,) = cx.check(photo(tmp_path), facts(date="2026-10-01", place="Paris", device="iPhone 15"))
    assert_card_contract([e])
    assert e.strength == "none" and e.raw["compared"] is False
    assert e.finding.startswith("Nothing could be compared")
    assert "the file has no capture time" in e.finding and "no usable GPS position" in e.finding
    assert "the file has no camera make" in e.finding
    assert e.proves == "Nothing: no fact could be compared."
    assert decide([e])[0] == "cant_tell"


def test_stripped_real_photo_is_nothing_to_compare(made):
    (e,) = cx.check(made["stripped_py-A.jpg"], facts(date="1999-01-01", device="Nikon"))
    assert e.raw["compared"] is False and not e.raw["contradiction"]


def test_unreadable_file_says_so(made):
    (e,) = cx.check(made["junk"], facts(date="2026-10-01"))
    assert e.strength == "none" and "could not be read" in e.finding


def test_no_context_means_no_card(tmp_path):
    assert cx.check(photo(tmp_path, make_="Canon")) == []
    assert cx.check(photo(tmp_path, make_="Canon"), None) == []


def test_a_caption_with_no_facts_gives_a_none_card(tmp_path):
    (e,) = cx.check(photo(tmp_path, make_="Canon"), Context(caption="what a day"))
    assert e.strength == "none" and "gives no date, place or device" in e.finding


def test_caption_is_read_when_no_facts_are_given(tmp_path):
    p = photo(tmp_path, make_="Canon", taken="2026:10:01 12:00:00")
    cards = cx.check(p, Context(caption="Floods on 20 October 2026, shot on an iPhone 15"))
    assert {e.raw["field"] for e in contradictions(cards)} == {"date", "device"}
    assert cards[0].raw["claimed"]["source"] == "model"


def test_place_list_not_installed_is_said(tmp_path, monkeypatch):
    monkeypatch.setenv(places.DB_ENV, str(tmp_path / "absent.sqlite"))
    (e,) = cx.check(photo(tmp_path, gps=PARIS_FR), facts(place="Paris"))
    assert "offline place list is not installed" in e.finding and not e.raw["contradiction"]


def test_unknown_place_is_not_compared(tmp_path):
    (e,) = cx.check(photo(tmp_path, gps=PARIS_FR), facts(place="Narnia"))
    assert "'Narnia' is not in the offline place list" in e.finding and not e.raw["contradiction"]


# --- time zones -------------------------------------------------------------------------------------

def test_time_zones_compare_by_the_clock_when_both_sides_have_an_offset(tmp_path):
    # 23:30 at UTC-10 on 1 Oct is 09:30 UTC on 2 Oct, under 2 days before 08:00 UTC on 4 Oct.
    p = photo(tmp_path, taken="2026:10:01 23:30:00", offset="-10:00")
    (e,) = cx.check(p, facts(date="2026-10-04T08:00:00+00:00"))
    assert not e.raw["contradiction"]
    assert e.raw["checks"][0]["compared_by"] == "clock" and e.raw["checks"][0]["days_apart"] == pytest.approx(1.94, abs=0.01)
    # The same file against the bare day 4 Oct: by calendar day the file's own day (1 Oct) is 3 days off.
    (e,) = cx.check(p, facts(date="2026-10-04"))
    assert e.raw["contradiction"] and e.raw["disagreement"]["compared_by"] == "calendar day"
    assert "1 October 2026, 23:30:00 (UTC-10:00)" in e.finding


def test_a_file_time_without_an_offset_is_compared_by_calendar_day(tmp_path):
    p = photo(tmp_path, taken="2026:10:01 23:30:00")
    (e,) = cx.check(p, facts(date="2026-10-03T23:00:00+14:00"))
    assert e.raw["checks"][0]["compared_by"] == "calendar day" and not e.raw["contradiction"]


def test_real_pixel_sample_with_offset(tmp_path):
    from tests.conftest import sample

    p = sample("commons-pixel10pro-utah-sign.jpg")
    cards = cx.check(p, facts(date="2025-12-31", device="Pixel 10 Pro"))
    assert not contradictions(cards)
    (e,) = contradictions(cx.check(p, facts(date="2026-10-04")))
    assert "30 December 2025, 11:04:29 (UTC-07:00)" in e.finding


@pytest.mark.parametrize("text", ["0000:00:00 00:00:00", "", "   ", "2026:13:45 99:00:00"])
def test_blank_or_broken_exif_times_are_no_time(text):
    assert cx.parse_exif_time(text) is None


# --- GPS precision ------------------------------------------------------------------------------------

def test_gps_rounded_to_whole_degrees_is_widened_not_trusted(tmp_path):
    # Reims written to whole degrees (49 N 4 E) could be up to about 67 km from the point written,
    # so the allowance widens and no contradiction is made.
    rough = photo(tmp_path, "rough.jpg", gps=((49, 0, 0), (4, 0, 0)))
    (e,) = cx.check(rough, facts(place="Paris, France"))
    assert not e.raw["contradiction"]
    assert e.raw["checks"][0]["gps_uncertainty_km"] > 60
    # Reims written to a hundred-thousandth of a degree is a contradiction (131 km, another region).
    (e,) = contradictions(cx.check(photo(tmp_path, "fine.jpg", gps=REIMS), facts(place="Paris, France")))
    assert e.raw["disagreement"]["gps_uncertainty_km"] < 0.1


def test_recorded_positioning_error_widens_the_allowance(tmp_path):
    assert contradictions(cx.check(photo(tmp_path, "a.jpg", gps=REIMS, h_error=10), facts(place="Paris, France")))
    assert not contradictions(cx.check(photo(tmp_path, "b.jpg", gps=REIMS, h_error=100000), facts(place="Paris, France")))


@pytest.mark.parametrize("gps,ns,ew", [
    (((0, 0, 0), (0, 0, 0)), "N", "E"),     # 0, 0: an unset default
    (PARIS_FR, "", "E"),                     # no hemisphere
    (((95, 0, 0), (2, 0, 0)), "N", "E"),     # not a latitude
])
def test_unusable_gps_is_no_position(tmp_path, gps, ns, ew):
    (e,) = cx.check(photo(tmp_path, gps=gps, ns=ns, ew=ew), facts(place="Sydney"))
    assert "no usable GPS position" in e.finding and not e.raw["contradiction"]


def test_gps_point_is_never_copied_out(tmp_path):
    cards = cx.check(photo(tmp_path, gps=UTAH_SIGN, ew="W"), facts(place="Paris"))
    blob = json.dumps([e.raw for e in cards]) + " ".join(e.finding for e in cards)
    assert "41.55" not in blob and "112.06" not in blob and "lat" not in json.dumps([e.raw for e in cards])


def test_dms_resolution():
    assert cx._dms((R(48), R(51), R(2963, 100)))[1] == pytest.approx(1 / 360000)
    assert cx._dms((R(48), R(51), R(0)))[1] == pytest.approx(1 / 60)
    assert cx._dms((R(49), R(0), R(0)))[1] == 1.0
    assert cx._dms((R(48), R(-1), R(0))) is None


# --- C2PA capture time --------------------------------------------------------------------------------

def test_valid_c2pa_creation_time_is_compared(tmp_path):
    from tests.conftest import _sign, sample

    acts = [{"action": "c2pa.created", "when": "2026-10-01T09:30:00Z",
             "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"}]
    p = _sign(sample("py-A.jpg"), tmp_path / "c.jpg", None, "secondlook-test", actions=acts)
    cards = cx.check(p, facts(date="2026-10-01"))
    # py-A.jpg's own EXIF says 30 July 2011; the signed record's creation time agrees with the claim.
    (e,) = contradictions(cards)
    assert "EXIF capture time" in e.finding and "30 July 2011" in e.finding
    labels = {c.get("file_field"): c["outcome"] for c in e.raw["checks"]}
    assert labels == {"EXIF capture time": "contradiction", "C2PA record's creation time": "agree"}


def test_invalid_c2pa_record_gives_no_time(tmp_path):
    from tests.conftest import _sign, sample

    acts = [{"action": "c2pa.created", "when": "2026-10-01T09:30:00Z",
             "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"}]
    assert cx._c2pa_times(_sign(sample("py-A.jpg"), tmp_path / "ok.jpg", None, "secondlook-test", actions=acts))
    p = Path(_sign(sample("py-A.jpg"), tmp_path / "c.jpg", None, "secondlook-test", actions=acts))
    data = bytearray(p.read_bytes())
    data[-200] ^= 0xFF  # change the image data after signing: the hash no longer matches
    p.write_bytes(bytes(data))
    assert cx._c2pa_times(str(p)) == []


# --- the ladder ---------------------------------------------------------------------------------------

def test_default_ladder_has_no_context_card_without_a_caption(made):
    r = run(made["stripped_py-A.jpg"])
    assert not [e for e in r.evidence if e.rung == "context"]


def test_default_ladder_on_a_signed_capture_keeps_the_signed_answer_and_lists_the_contradiction():
    from tests.conftest import sample

    # The Pixel 10 Pro sample has a trusted signed capture record: rule 2 still decides, and the
    # caption contradiction stays in the evidence for the reader.
    r = run(sample("commons-pixel10pro-utah-sign.jpg"), context=facts(device="iPhone 15 Pro"))
    assert r.answer == "signed_camera"
    assert [e.raw["field"] for e in r.evidence if e.rung == "context"] == ["device"]


def test_context_rung_alone_with_a_contradiction_is_concerns(tmp_path):
    from secondlook.rungs import context as rung

    p = photo(tmp_path, make_="Canon")
    r = run(p, rungs=[("context", rung.check)], context=facts(device="iPhone"))
    assert r.answer == "concerns"


# --- GeoNames lookup on the fixture -----------------------------------------------------------------

def test_geonames_lookup_on_the_fixture(geodb):
    paris = places.lookup("Paris", geodb)
    assert (paris[0].name, paris[0].country) == ("Paris", "FR") and ("Paris", "US") in {(p.name, p.country) for p in paris}
    assert [p.country for p in places.lookup("Paris, France", geodb)] == ["FR"]
    assert [p.admin1 for p in places.lookup("Paris, TX", geodb)] == ["TX"]
    assert [(p.name, p.admin1) for p in places.lookup("Paris, Texas", geodb)] == [("Paris", "TX")]  # a region's name
    assert [p.name for p in places.lookup("Kiev", geodb)] == ["Kyiv"]          # alternate name
    assert [p.name for p in places.lookup("  kyïv ", geodb)] == ["Kyiv"]       # case and accents folded
    assert {p.country for p in places.lookup("Trafalgar Square, London, United Kingdom", geodb)} == {"GB"}
    assert places.lookup("Paris, Atlantis", geodb)[0].country == "FR"          # unknown qualifier ignored
    assert places.lookup("Narnia", geodb) == [] and places.lookup(" , ", geodb) == []
    paris = places.lookup("Paris, FR", geodb)[0]
    assert (paris.geonameid, paris.lat, paris.lon) == (2988507, 48.85341, 2.3488)
    meta = places.meta(geodb)
    assert meta["licence"] == "CC BY 4.0, GeoNames" and meta["places"] == "180" and meta["schema"] == places.SCHEMA


def test_geonames_resolves_countries_and_regions_first(geodb):
    with places.Index(geodb) as idx:
        au = idx.resolve("Australia")
        assert au.country_name and [r.label for r in au.regions] == ["Australia"]
        assert [(p.name, p.country) for p in au.cities] == [("Australia", "CU")]   # the collision F1 found
        assert [r.label for r in idx.resolve("Victoria").regions] == ["Victoria, Australia", "Victoria, Malta"]
        assert [r.label for r in idx.resolve("Georgia, USA").regions] == ["Georgia, United States"]
        assert [r.label for r in idx.resolve("The Netherlands").regions] == ["The Netherlands"]
        assert [r.label for r in idx.resolve("UK").regions] == ["United Kingdom"]   # an alias written in places.py
        assert idx.resolve("Ben") is None or not idx.resolve("Ben").regions         # codes count only in capitals
        assert [r.label for r in idx.resolve("USA").regions] == ["United States"]
        # "Paris, Australia": no Paris is listed in Australia, so the place is Australia.
        assert [r.label for r in idx.resolve("Paris, Australia").regions] == ["Australia"]


def test_an_index_from_the_older_build_is_not_used(tmp_path):
    import sqlite3

    old = tmp_path / "old.sqlite"
    with sqlite3.connect(old) as con:
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    with pytest.raises(FileNotFoundError):
        places.Index(old)


def test_geonames_lookup_without_an_index_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        places.lookup("Paris", tmp_path / "none.sqlite")


def test_haversine():
    # Paris to London, about 344 km.
    assert places.haversine_km(48.85341, 2.3488, 51.50853, -0.12574) == pytest.approx(344, abs=2)


# --- extraction --------------------------------------------------------------------------------------

@pytest.mark.parametrize("caption,expected", [
    ("Flooding on 4 October 2026", "2026-10-04"),
    ("Flooding on October 4, 2026", "2026-10-04"),
    ("Flooding on 4th Oct. 2026", "2026-10-04"),
    ("2026-10-04 flooding", "2026-10-04"),
    ("28/10/2026 storm", "2026-10-28"),
    ("10/28/2026 storm", "2026-10-28"),
    ("03/04/2026 storm", None),         # day first or month first: not read
    ("05/05/2026 storm", None),         # all numbers, both 12 or under: not read either
    ("03/04/2026 storm, posted 28 October 2026", None),   # a later date is not taken in its place
    ("Posted 28 October 2026 of the 03/04/2026 storm", "2026-10-28"),
    ("storm last Tuesday", None),
    ("storm in 2019", None),
    ("31 February 2026", None),
])
def test_rule_based_dates(caption, expected):
    assert extract.rule_based(caption).date == expected


@pytest.mark.parametrize("caption,expected", [
    ("Flooding in Sydney on 4 October 2026", "Sydney"),
    ("Protest at Trafalgar Square, London", "Trafalgar Square, London"),
    ("Riots in Rio de Janeiro, Brazil", "Rio de Janeiro, Brazil"),
    ("Snow in March in Kyiv", "Kyiv"),
    ("In Paris, France on 1 May 2026", "Paris, France"),      # the place word in any case
    ("Visitors from India in Sydney", "Sydney"),              # "from" only when nothing else is found
    ("Footage from Kyiv", "Kyiv"),
    ("Storm in Paris, Texas", "Paris, Texas"),
    ("edited in Photoshop", None),
    ("photo from the event", None),
])
def test_rule_based_places(caption, expected):
    assert extract.rule_based(caption).place == expected


@pytest.mark.parametrize("caption,expected", [
    ("Shot on an iPhone 15 Pro.", "iPhone 15 Pro"),
    ("Taken with my Canon EOS R5 in Paris", "Canon EOS R5"),
    ("captured on Pixel 9, yesterday", "Pixel 9"),
    ("Apple store queue", None),
    # DAT-872 (found by W10 part 2): a place or time after the name ends it, so it is not kept as device
    ("Shot on an iPhone 15 Pro outside the town hall last night.", "iPhone 15 Pro"),
    ("Shot on an iPhone 15 Pro last night.", "iPhone 15 Pro"),
    ("Filmed on a Canon EOS R5 yesterday in Sydney", "Canon EOS R5"),
    ("Taken with a Pixel 9 Pro this morning at the rally", "Pixel 9 Pro"),
    ("Shot on a Samsung Galaxy S24 Ultra inside the stadium, then posted", "Samsung Galaxy S24 Ultra"),
    ("Photographed on a Fujifilm X-T5", "Fujifilm X-T5"),                   # nothing after the name: kept whole
])
def test_rule_based_devices(caption, expected):
    assert extract.rule_based(caption).device == expected


def test_extractors_share_one_type():
    readers: list[extract.Extractor] = [extract.rule_based]
    assert all(isinstance(r("x"), ClaimFacts) for r in readers)
    assert extract.rule_based("x").source == "model" and extract.typed(date="2026-10-01").source == "typed"


def test_typed_facts_win_and_set_the_source():
    typed = extract.typed(date="2026-10-01")
    read = extract.rule_based("in Paris on 5 October 2026, shot on a Pixel 9")
    merged = extract.merge(typed, read)
    assert (merged.date, merged.place, merged.device, merged.source) == ("2026-10-01", "Paris", "Pixel 9", "model")
    all_typed = extract.merge(extract.typed("2026-10-01", "Lyon", "Canon"), read)
    assert all_typed.source == "typed" and all_typed.place == "Lyon"
    assert extract.merge(None, None) is None


# --- CLI and web --------------------------------------------------------------------------------------

def test_cli_typed_facts(tmp_path, capsys):
    p = photo(tmp_path, make_="Canon", taken="2026:10:01 12:00:00")
    assert cli.main(["check", p, "--claimed-device", "iPhone 15", "--claimed-date", "2026-10-02"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"answer", "evidence", "summary", "version", "explanation"}
    ctx = [e for e in out["evidence"] if e["rung"] == "context"]
    assert out["answer"] == "concerns" and [e["raw"]["field"] for e in ctx] == ["device"]
    assert ctx[0]["raw"]["claimed"]["source"] == "typed"


def test_cli_caption(tmp_path, capsys):
    p = photo(tmp_path, gps=PARIS_FR)
    cli.main(["check", p, "--caption", "Riots in Sydney"])
    out = json.loads(capsys.readouterr().out)
    (e,) = [e for e in out["evidence"] if e["rung"] == "context"]
    assert e["raw"]["field"] == "place" and out["answer"] == "concerns"


def test_cli_without_context_flags_adds_no_context_card(tmp_path, capsys):
    cli.main(["check", photo(tmp_path, make_="Canon")])
    out = json.loads(capsys.readouterr().out)
    assert not [e for e in out["evidence"] if e["rung"] == "context"]


def test_cli_refuses_a_bad_date(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["check", photo(tmp_path), "--claimed-date", "last week"])


def test_web_takes_an_optional_caption(tmp_path):
    from fastapi.testclient import TestClient
    from secondlook.web.app import MAX_CAPTION, create_app

    client = TestClient(create_app(stub=False, rungs=[("context", cx.check)]))
    data = Path(photo(tmp_path, make_="Canon")).read_bytes()
    body = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")},
                       data={"caption": "shot on an iPhone 15"}).json()
    assert body["answer"] == "concerns" and body["evidence"][0]["raw"]["field"] == "device"
    body = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}).json()
    assert body["answer"] == "cant_tell" and not [e for e in body["evidence"] if e["rung"] == "context"]
    r = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")},
                    data={"caption": "x" * (MAX_CAPTION + 1)})
    assert r.status_code == 400


def test_public_copy_has_no_em_dash_or_banned_claims():
    import re

    text = Path(cx.__file__).read_text() + Path(extract.__file__).read_text() + Path(places.__file__).read_text()
    assert "—" not in text
    for pattern in (r"this is real", r"detects? ai", r"\baccura"):
        assert not re.search(pattern, text, re.I)


def test_default_rungs_add_context_only_with_a_context(made, capsys):
    from secondlook import rungs

    assert "context" not in rungs.DEFAULT_RUNGS and rungs.CONTEXT_RUNGS == ("context",)
    plain = run(made["stripped_py-A.jpg"])
    assert "context" not in plain.evidence[-1].raw["rungs_run"]
    with_caption = run(made["stripped_py-A.jpg"], context=Context(caption="in Paris"))
    assert with_caption.evidence[-1].raw["rungs_run"][-1] == "context"


def test_web_default_rungs_run_context_only_with_a_caption(tmp_path):
    from fastapi.testclient import TestClient
    from secondlook.web.app import create_app

    client = TestClient(create_app(stub=False))
    assert "context" in client.get("/api/health").json()["rungs"]
    data = Path(photo(tmp_path)).read_bytes()
    body = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}).json()
    assert "context" not in body["evidence"][-1]["raw"]["rungs_run"]
    body = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}, data={"caption": "in Paris"}).json()
    assert [e["rung"] for e in body["evidence"]].count("context") == 1


# --- countries and first-level regions (DAT-872 fix round, a reviewer F1) ------------------------------

# (what the caption names, a listed place inside it, a listed place far outside it). Every row of the
# fixture is copied unchanged from GeoNames cities5000 (28 Oct 2026), including every city that shares
# one of these names ("Australia" in Cuba, "Inđija" for India), so the collisions F1 found are here.
REGION_NAMES = [
    ("Australia", ("Sydney", "AU"), ("London", "GB")),
    ("India", ("Mumbai", "IN"), ("Sydney", "AU")),
    ("Egypt", ("Cairo", "EG"), ("Sydney", "AU")),
    ("Ireland", ("Dublin", "IE"), ("Sydney", "AU")),
    ("Canada", ("Toronto", "CA"), ("Sydney", "AU")),
    ("China", ("Shanghai", "CN"), ("Sydney", "AU")),
    ("Georgia", ("Tbilisi", "GE"), ("Sydney", "AU")),       # the country
    ("Georgia", ("Atlanta", "US"), ("Sydney", "AU")),       # the US state
    ("Jordan", ("Amman", "JO"), ("Sydney", "AU")),
    ("Japan", ("Osaka", "JP"), ("Sydney", "AU")),
    ("Chile", ("Santiago", "CL"), ("Sydney", "AU")),
    ("Mexico", ("Guadalajara", "MX"), ("Sydney", "AU")),
    ("Panama", ("Colón", "PA"), ("Sydney", "AU")),
    ("Germany", ("Hamburg", "DE"), ("Sydney", "AU")),
    ("France", ("Marseille", "FR"), ("Sydney", "AU")),
    ("Brazil", ("Recife", "BR"), ("Sydney", "AU")),
    ("South Africa", ("Durban", "ZA"), ("Sydney", "AU")),
    ("Indonesia", ("Surabaya", "ID"), ("Sydney", "AU")),
    ("Peru", ("Arequipa", "PE"), ("Sydney", "AU")),
    ("Kenya", ("Mombasa", "KE"), ("Sydney", "AU")),
    ("The Netherlands", ("Rotterdam", "NL"), ("Sydney", "AU")),
    ("Victoria, Australia", ("Melbourne", "AU"), ("London", "GB")),
    ("New York", ("Buffalo", "US"), ("Sydney", "AU")),
    ("Washington", ("Seattle", "US"), ("Sydney", "AU")),
    ("Texas", ("Houston", "US"), ("Sydney", "AU")),
    ("California", ("Los Angeles", "US"), ("Sydney", "AU")),
    ("Queensland", ("Brisbane", "AU"), ("London", "GB")),
    ("New South Wales", ("Newcastle", "AU"), ("London", "GB")),
    ("England", ("Manchester", "GB"), ("Sydney", "AU")),
    ("Ontario", ("Ottawa", "CA"), ("Sydney", "AU")),
    ("Bavaria", ("Nuremberg", "DE"), ("Sydney", "AU")),
    ("Punjab", ("Lahore", "PK"), ("Sydney", "AU")),
]


def gps_at(db, name, country):
    """EXIF-style GPS fields for a listed place, written to a hundred-thousandth of a degree."""
    import sqlite3

    with sqlite3.connect(db) as con:
        lat, lon = con.execute("SELECT lat, lon FROM places WHERE name = ? AND country = ? ORDER BY population DESC",
                               (name, country)).fetchone()
    deg = lambda v: (R(round(abs(v) * 100000), 100000), 0, 0)  # noqa: E731
    return {"gps": (deg(lat), deg(lon)), "ns": "N" if lat >= 0 else "S", "ew": "E" if lon >= 0 else "W"}


@pytest.mark.parametrize("name,inside,outside", REGION_NAMES, ids=[f"{n}@{i[0]}" for n, i, _ in REGION_NAMES])
def test_a_country_or_region_on_matching_gps_is_never_a_concern(tmp_path, geodb, name, inside, outside):
    cards = cx.check(photo(tmp_path, **gps_at(geodb, *inside)), Context(caption=f"Taken in {name} last week"))
    assert_card_contract(cards)
    (e,) = cards
    assert not e.raw["contradiction"] and e.raw["checks"][0]["outcome"] == "agree", e.finding


@pytest.mark.parametrize("name,inside,outside", REGION_NAMES, ids=[f"{n}@{o[0]}" for n, _, o in REGION_NAMES])
def test_a_country_or_region_on_far_gps_is_a_concern(tmp_path, geodb, name, inside, outside):
    cards = cx.check(photo(tmp_path, **gps_at(geodb, *outside)), Context(caption=f"Taken in {name} last week"))
    assert_card_contract(cards)
    (e,) = contradictions(cards)
    assert e.raw["field"] == "place" and decide(cards)[0] == "concerns"
    assert all(r["status"] == "out" for r in e.raw["disagreement"]["readings"])


def test_f1_repro_a_truthful_country_caption_on_a_sydney_photo(tmp_path, geodb):
    # a reviewer's repro: EXIF says Sydney, Apple, 1 October 2026 +10:00.
    p = photo(tmp_path, make_="Apple", model="iPhone 15", taken="2026:10:01 09:30:00", offset="+10:00",
              **gps_at(geodb, "Sydney", "AU"))
    r = run(p, rungs=[("context", cx.check)],
            context=Context(caption="Taken in Australia on 1 October 2026 with an iPhone 15"))
    (e,) = [e for e in r.evidence if e.rung == "context"]
    assert r.answer == "cant_tell" and not e.raw["contradiction"]
    assert "Place: the file's GPS position is in or near Australia (GeoNames)." in e.finding


@pytest.mark.parametrize("caption,where", [
    ("Taken in Victoria", ("London", "GB")),       # Victoria, Australia; Victoria, BC; Victoria, Hong Kong
    ("Taken in Springfield", ("Paris", "FR")),     # 15 listed places, none 10 times the next
    ("Taken in Rome", ("Paris", "FR")),            # Rome, Italy and Lomé (another name: "Rome") are near in size
])
def test_an_ambiguous_city_is_never_a_concern(tmp_path, geodb, caption, where):
    (e,) = cx.check(photo(tmp_path, **gps_at(geodb, *where)), Context(caption=caption))
    assert e.strength == "none" and not e.raw["contradiction"]
    assert e.raw["checks"][0]["outcome"] == "ambiguous" and "the name can't be compared" in e.finding


@pytest.mark.parametrize("caption,where", [
    ("Taken in Victoria", ("Victoria", "CA")),     # a region's name, and a large city's
    ("Taken in Washington", ("Washington", "US")), # Washington, DC is not in Washington state
    ("Taken in Canton", ("Canton", "US")),         # Guangzhou is far larger, but Canton, Ohio is right here
    ("Taken in Cologne", ("Köln", "DE")),          # another name of Köln, and a town in Italy
    ("Taken in Mexico", ("Guadalajara", "MX")),
])
def test_a_name_that_fits_the_gps_position_in_any_reading_agrees(tmp_path, geodb, caption, where):
    (e,) = cx.check(photo(tmp_path, **gps_at(geodb, *where)), Context(caption=caption))
    assert e.raw["checks"][0]["outcome"] == "agree" and not e.raw["contradiction"], e.finding


def test_f2_a_region_name_narrows_a_city(tmp_path, geodb):
    (e,) = contradictions(cx.check(photo(tmp_path, gps=PARIS_FR), facts(place="Paris, Texas")))
    assert e.finding == ("The caption says Paris, Texas. The file's own GPS position says a point about 7,783 km "
                         "from Paris, Texas, United States, in another first-level region (GeoNames).")
    assert not contradictions(cx.check(photo(tmp_path, gps=PARIS_TX, ew="W"), facts(place="Paris, Texas")))


def test_a_city_more_than_50_km_away_in_the_same_region_is_no_concern(tmp_path, geodb):
    # Buffalo is about 470 km from New York City, in the same state: rule 2 needs another region.
    (e,) = cx.check(photo(tmp_path, **gps_at(geodb, "Buffalo", "US")), facts(place="New York City"))
    assert not e.raw["contradiction"] and e.raw["checks"][0]["outcome"] == "agree"
    assert "is about 470 km from the file's GPS position, in or near the same first-level region" in e.finding


def test_the_card_says_the_date_is_the_first_full_date_in_the_caption(tmp_path):
    p = photo(tmp_path, taken="2026:10:01 12:00:00")
    (e,) = cx.check(p, Context(caption="Unveiled on 12 May 2019, pictured in 2026"))
    assert e.finding.startswith("The caption says 12 May 2019 (the first full date in the caption). The file's own ")
    (e,) = cx.check(p, Context(caption="Pictured on 2 October 2026"))
    assert "Date (the first full date in the caption): within 2 days" in e.finding
    (e,) = cx.check(p, facts(date="2019-05-12"))           # a typed date is not read from a caption
    assert "first full date" not in e.finding


@pytest.mark.parametrize("caption,expected", [
    ("Storm on 03/04/2026", "03/04/2026"),
    ("Storm on 05.05.2026", "05.05.2026"),
    ("Storm on 28/10/2026", None),
    ("Storm on 13/13/2026", None),
    ("Storm on 4 March 2026", None),
])
def test_ambiguous_date(caption, expected):
    assert extract.ambiguous_date(caption) == expected


def test_the_card_says_an_ambiguous_date_was_not_compared(tmp_path):
    """the project lead on DAT-873: an all-number date that reads both ways keeps no date fact, and the card says so."""
    p = photo(tmp_path, make_="Canon", taken="2026:10:01 12:00:00")
    (e,) = cx.check(p, Context(caption="Storm damage on 03/04/2026"))
    assert e.finding == ("The caption gives no date, place or device that can be compared with the file's own "
                         "facts. Date: 03/04/2026 in the caption reads day first or month first, so no date was compared.")
    assert e.strength == "none" and not e.raw["contradiction"] and e.raw["checks"][0]["outcome"] == "ambiguous_claim"
    cards = cx.check(p, Context(caption="Storm damage on 03/04/2026, shot on an iPhone 15"))
    assert [c.raw["field"] for c in contradictions(cards)] == ["device"]
    assert cards[0].raw["checks"][0]["outcome"] == "ambiguous_claim" and cards[0].raw["claimed"]["date"] is None
    (e,) = cx.check(p, Context(caption="Storm damage on 03/04/2026, shot on a Canon"))
    assert "Date: 03/04/2026 in the caption reads day first or month first" in e.finding
    (e,) = cx.check(p, Context(caption="Storm damage on 03/04/2026", facts=facts(date="2026-10-01").facts))
    assert "reads day first" not in e.finding                # a typed date is compared as given


def test_no_context_card_or_claims_doc_says_genuine():
    import re

    claims = (Path(__file__).parents[1] / "docs" / "CLAIMS.md").read_text()
    section = claims.split("**The context rung**")[1].split("**The claim rung**")[0]
    for text in (cx.DOES_NOT_PROVE, cx.PROVES, section):
        assert not re.search(r"\b(genuine|authentic|real)\b", text, re.I), text
    assert cx.DOES_NOT_PROVE.endswith("and agreement doesn't show how the picture was made.")


# --- the same rules on the real GeoNames index, when it is built (scripts/fetch_geonames.sh) ----------

def _real_index():
    try:
        with places.Index(places.DEFAULT_DB):
            return places.DEFAULT_DB
    except FileNotFoundError:
        return None


REAL = _real_index()
needs_real = pytest.mark.skipif(REAL is None, reason="needs the real GeoNames index (scripts/fetch_geonames.sh)")


@needs_real
def test_real_index_every_country_on_its_largest_place_is_never_a_concern():
    import sqlite3

    with sqlite3.connect(REAL) as con:
        # SQLite takes lat and lon from the row with the largest population in each country.
        largest = {c: (lat, lon) for c, lat, lon, _ in con.execute(
            "SELECT country, lat, lon, MAX(population) FROM places GROUP BY country")}
        names = con.execute("SELECT DISTINCT label, country FROM regions WHERE kind = 'country' AND code = 0").fetchall()
    rows = [(label, *largest[c]) for label, c in names if c in largest]
    assert len(rows) > 200
    bad = []
    for label, lat, lon in rows:
        check, finding = cx.place_check(label, {"lat": lat, "lon": lon, "uncertainty_km": 0.0}, REAL)
        if finding or check["outcome"] != "agree":
            bad.append((label, check["outcome"]))
    assert not bad


@needs_real
@pytest.mark.parametrize("name,inside,outside", REGION_NAMES, ids=[n for n, _, _ in REGION_NAMES])
def test_real_index_country_and_region_names(name, inside, outside):
    import sqlite3

    with sqlite3.connect(REAL) as con:
        at = lambda n, c: dict(zip(("lat", "lon"), con.execute(  # noqa: E731
            "SELECT lat, lon FROM places WHERE name = ? AND country = ? ORDER BY population DESC", (n, c)).fetchone()),
            uncertainty_km=0.0)
        ok, bad = at(*inside), at(*outside)
    assert cx.place_check(name, ok, REAL)[0]["outcome"] == "agree"
    assert cx.place_check(name, bad, REAL)[0]["outcome"] == "contradiction"
