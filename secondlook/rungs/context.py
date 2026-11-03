"""Context rung: does the caption's date, place and device agree with the file's own facts?

Verification desks do this by hand: read the capture time, the GPS position and the camera make
out of the file, and hold them against what the caption claims. This rung does the same, offline.

The file's facts:
  - EXIF DateTimeOriginal with OffsetTimeOriginal, GPS position, Make and Model (Pillow);
  - the creation or capture time in a valid C2PA record, if the file has one (c2pa-python).
The claimed facts come from Context.facts, or from the caption through secondlook.extract.

Rules (fixed here, not tuned per file):
  - date: more than 2 days apart. By the clock when both sides give a time with an offset,
    otherwise by calendar day (the file's own wall-clock day against the claimed day);
  - place: countries and first-level regions come first. "Australia" or "Texas" disagrees when
    the GPS position is clearly outside it: near a listed place in another country or region,
    with the nearest listed place inside more than 50 km farther away. A city counts only when
    its name is unambiguous (the largest place of that name has at least 10 times the people of
    the next, or the caption's country or region leaves one), and then disagrees when it is more
    than 50 km away and the GPS position is clearly in another first-level region. A name that
    fits anything near the GPS position agrees; an ambiguous name is not compared. Distances
    allow for the precision the GPS fields were written with and any recorded positioning error;
  - device: the make is clearly different: both makes are known and not of one family
    (Olympus and OM System, Ricoh and Pentax, a co-branded lens maker and its phone maker).

A contradiction is a "metadata" card with raw["contradiction"] True, which the ladder counts as a
concern. Agreement, or nothing to compare, is one "none" card that is never a concern. No card
from this rung is ever "signed". The GPS position itself is never copied out, only the distance.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

from secondlook import extract, places
from secondlook.types import ClaimFacts, Context, Evidence

log = logging.getLogger(__name__)

DATE_TOLERANCE_DAYS = 2
PLACE_TOLERANCE_KM = 50.0
# The GPS position is placed in a country or region only when a listed place is this close to it,
# so a point in empty country or at sea is never said to be outside anywhere.
NEAR_KM = 25.0
# A city counts only when its largest namesake has at least this many times the people of the next.
CITY_DOMINANCE = 10
# When the name is also a region's ("Victoria", "Washington"), a city of that name blocks a
# contradiction only when one this large exists (Victoria, British Columbia; Washington, DC).
CITY_OVER_REGION_POP = 100_000
KM_PER_DEGREE = math.pi * places.EARTH_RADIUS_KM / 180

# EXIF tag ids (EXIF 2.32)
MAKE, MODEL, EXIF_IFD, GPS_IFD = 0x010F, 0x0110, 0x8769, 0x8825
DATETIME_ORIGINAL, OFFSET_TIME_ORIGINAL = 0x9003, 0x9011
GPS_LAT_REF, GPS_LAT, GPS_LON_REF, GPS_LON, GPS_H_ERROR = 1, 2, 3, 4, 31

PROVES = "The caption and the file's facts disagree."
DOES_NOT_PROVE = ("Metadata can be wrong, edited or stripped. A disagreement doesn't show the picture "
                  "is fake, and agreement doesn't show how the picture was made.")

# Device makes. Each alias is a whole word or phrase, matched case-insensitively in the claimed
# device and in the file's EXIF Make. A "Google Nexus" gives no make (makes_in): Nexus phones were
# made, and EXIF-labelled, by other companies, LG among them.
MAKES = {
    "apple": ("apple", "iphone", "ipad"),
    "google": ("google", "pixel"),
    "samsung": ("samsung", "galaxy"),
    "canon": ("canon", "eos", "powershot", "ixus"),
    "nikon": ("nikon", "coolpix"),
    "sony": ("sony", "xperia", "cyber-shot", "cybershot"),
    "fujifilm": ("fujifilm", "fuji"),
    "olympus": ("olympus",),
    "om system": ("om system", "om digital solutions", "om digital"),
    "panasonic": ("panasonic", "lumix"),
    "leica": ("leica",),
    "ricoh": ("ricoh",),
    "pentax": ("pentax",),
    "huawei": ("huawei",),
    "honor": ("honor",),
    "xiaomi": ("xiaomi", "redmi", "poco"),
    "oneplus": ("oneplus",),
    "oppo": ("oppo",),
    "realme": ("realme",),
    "vivo": ("vivo", "iqoo"),
    "motorola": ("motorola", "moto"),
    "nokia": ("nokia", "hmd global", "hmd"),
    "lg": ("lg", "lge", "lg electronics"),
    "dji": ("dji", "mavic"),
    "gopro": ("gopro",),
    "hasselblad": ("hasselblad",),
    "insta360": ("insta360", "arashi vision"),
    "kodak": ("kodak",),
    "casio": ("casio",),
    "sigma": ("sigma",),
    "minolta": ("minolta", "konica minolta"),
}
# Makes that are not "clearly different" from each other: one company's brands, or a lens or
# imaging brand printed on another maker's phone or camera.
FAMILIES = [
    {"olympus", "om system"}, {"ricoh", "pentax"}, {"huawei", "honor", "leica"},
    {"leica", "xiaomi"}, {"leica", "panasonic"}, {"oneplus", "oppo", "realme", "hasselblad"},
    {"hasselblad", "dji"}, {"sony", "minolta"},
]
_ALIAS = [(make, re.compile(rf"(?<![\w-]){re.escape(a)}(?![\w-])", re.I)) for make, aliases in MAKES.items()
          for a in aliases]


_NEXUS = re.compile(r"(?<![\w-])nexus(?![\w-])", re.I)


def makes_in(text: str | None) -> set[str]:
    found = {make for make, rx in _ALIAS if text and rx.search(text)}
    if text and _NEXUS.search(text):
        found.discard("google")  # a Google Nexus says nothing about the make in its EXIF
    return found


def related(a: str, b: str) -> bool:
    return a == b or any(a in f and b in f for f in FAMILIES)


# --- reading the file's own facts ----------------------------------------------------------------

def _clean(v) -> str:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v).replace("\x00", "").strip()[:200]


def _offset(text: str) -> timezone | None:
    m = re.fullmatch(r"([+-])(\d{2}):(\d{2})", text.strip())
    if not m:
        return None
    delta = timedelta(hours=int(m[2]), minutes=int(m[3]))
    return timezone(delta if m[1] == "+" else -delta)


def parse_exif_time(text: str, offset: str = "") -> datetime | None:
    """EXIF "2025:12:30 11:04:29", with an optional "-07:00" offset. Blank or zero dates give None."""
    try:
        when = datetime.strptime(_clean(text)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    tz = _offset(offset) if offset else None
    return when.replace(tzinfo=tz) if tz else when


def parse_iso_time(text: str) -> datetime | None:
    text = _clean(text)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return parse_exif_time(text)


def _number(v) -> Fraction | None:
    """An EXIF rational (Pillow IFDRational, a pair or a float) as an exact fraction."""
    try:
        if isinstance(v, tuple) and len(v) == 2:
            return Fraction(int(v[0]), int(v[1]))
        num, den = getattr(v, "numerator", None), getattr(v, "denominator", None)
        if isinstance(num, int) and isinstance(den, int):
            return Fraction(num, den)
        f = float(v)
        return Fraction(f).limit_denominator(10**6) if math.isfinite(f) else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms(value) -> tuple[float, float] | None:
    """(degrees, resolution in degrees) from GPS degrees, minutes, seconds.

    The resolution is one step of the smallest non-zero part as written: (48, 51, 29.6) to a tenth
    of a second, (48, 51, 0) to a minute, (49, 0, 0) to a degree. A rounded position is widened,
    never narrowed.
    """
    if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 3:
        return None
    parts = [_number(v) for v in value]
    if any(p is None or p < 0 for p in parts):
        return None
    units = (Fraction(1), Fraction(1, 60), Fraction(1, 3600))
    degrees = sum(p * u for p, u in zip(parts, units))
    resolution = units[0] / parts[0].denominator
    for p, u in zip(parts, units):
        if p:
            resolution = u / p.denominator
    return float(degrees), float(resolution)


def read_gps(gps: dict) -> dict | None:
    """Position and its uncertainty in km, or None when the GPS fields are missing or unusable."""
    lat, lon = _dms(gps.get(GPS_LAT)), _dms(gps.get(GPS_LON))
    lat_ref, lon_ref = _clean(gps.get(GPS_LAT_REF, "")).upper(), _clean(gps.get(GPS_LON_REF, "")).upper()
    if not lat or not lon or lat_ref not in ("N", "S") or lon_ref not in ("E", "W"):
        return None
    la = -lat[0] if lat_ref == "S" else lat[0]
    lo = -lon[0] if lon_ref == "W" else lon[0]
    if abs(la) > 90 or abs(lo) > 180 or (la == 0 and lo == 0):  # 0, 0 is a common unset default
        return None
    half_lat = lat[1] / 2 * KM_PER_DEGREE
    half_lon = lon[1] / 2 * KM_PER_DEGREE * math.cos(math.radians(la))
    err = _number(gps.get(GPS_H_ERROR)) if gps.get(GPS_H_ERROR) is not None else None
    err_km = float(err) / 1000 if err is not None and err >= 0 else 0.0
    return {"lat": la, "lon": lo, "uncertainty_km": math.hypot(half_lat, half_lon) + err_km}


def _c2pa_times(path: str) -> list[tuple[str, datetime]]:
    """Times from a valid C2PA record: its capture time (stds.exif DateTimeOriginal) and the time
    on its c2pa.created action. Nothing when there is no record, it does not validate, or
    c2pa-python is not installed."""
    try:
        from secondlook.rungs import c2pa as c2pa_rung

        store, _ = c2pa_rung._read(path, "official")
    except Exception as e:  # no manifest, unsupported file, or the SDK is missing
        log.info("context: no C2PA record read from %s: %s", path, e)
        return []
    if not isinstance(store, dict):
        return []
    state, _, failures = c2pa_rung._state(store)
    if state not in ("Valid", "Trusted") or failures:
        return []
    manifests = store.get("manifests") if isinstance(store.get("manifests"), dict) else {}
    active = manifests.get(store.get("active_manifest") or "")
    if not isinstance(active, dict):
        return []
    out = []
    for a in active.get("assertions") or []:
        data = a.get("data") if isinstance(a, dict) and isinstance(a.get("data"), dict) else {}
        if str(a.get("label", "")).split(".v")[0] in ("stds.exif", "c2pa.exif") and data.get("exif:DateTimeOriginal"):
            when = parse_iso_time(str(data["exif:DateTimeOriginal"]))
            if when:
                out.append(("C2PA record's capture time", when))
    for act in c2pa_rung._actions(active):
        if act.get("action") == "c2pa.created" and act.get("when"):
            when = parse_iso_time(str(act["when"]))
            if when:
                out.append(("C2PA record's creation time", when))
                break
    return out


def file_facts(path: str, c2pa_times: bool = True) -> dict:
    """Everything this rung compares against, read from the file. Never raises.
    c2pa_times False skips reading the C2PA record (no date was claimed)."""
    facts = {"readable": False, "times": [], "make": None, "model": None, "gps": None}
    try:
        from PIL import Image

        with Image.open(path) as im:
            exif = im.getexif()
            sub = exif.get_ifd(EXIF_IFD) if exif else {}
            gps = exif.get_ifd(GPS_IFD) if exif else {}
        facts["readable"] = True
        taken = parse_exif_time(sub.get(DATETIME_ORIGINAL, ""), _clean(sub.get(OFFSET_TIME_ORIGINAL, "")))
        if taken:
            facts["times"].append(("EXIF capture time", taken))
        facts["make"] = _clean(exif.get(MAKE, "")) or None
        facts["model"] = _clean(exif.get(MODEL, "")) or None
        facts["gps"] = read_gps(dict(gps)) if gps else None
    except Exception as e:
        log.info("context: could not read EXIF from %s: %s", path, e)
    if c2pa_times:
        facts["times"].extend(_c2pa_times(path))
    return facts


# --- comparing -----------------------------------------------------------------------------------

def _claimed_time(text: str) -> date | datetime | None:
    text = (text or "").strip()
    try:
        return date.fromisoformat(text) if len(text) == 10 else datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def date_gap_days(claimed: date | datetime, found: datetime) -> tuple[float, str]:
    """(days apart, how they were compared)."""
    if isinstance(claimed, datetime) and claimed.tzinfo and found.tzinfo:
        return abs((claimed - found).total_seconds()) / 86400, "clock"
    claimed_day = claimed.date() if isinstance(claimed, datetime) else claimed
    return float(abs((claimed_day - found.date()).days)), "calendar day"


def say_date(d: date | datetime) -> str:
    if not isinstance(d, datetime):
        return f"{d.day} {d:%B %Y}"
    text = f"{d.day} {d:%B %Y}, {d:%H:%M:%S}"
    if d.tzinfo is None:
        return text + " (no time zone recorded)"
    off = d.utcoffset() or timedelta(0)
    sign, mins = ("+" if off >= timedelta(0) else "-"), abs(int(off.total_seconds())) // 60
    return text + f" (UTC{sign}{mins // 60:02d}:{mins % 60:02d})"


def _quote(text: str, cap: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= cap else text[: cap - 3].rstrip() + "..."


def _card(finding: str, contradiction: bool, raw: dict) -> Evidence:
    return Evidence(
        rung="context",
        finding=finding,
        proves=PROVES if contradiction else (
            "Only that the facts compared here do not disagree." if raw.get("compared")
            else "Nothing: no fact could be compared."),
        does_not_prove=DOES_NOT_PROVE,
        strength="metadata" if contradiction else "none",
        raw={**raw, "contradiction": contradiction},
    )


def _region_status(idx: places.Index, gps: dict, region: places.Region, tol: float) -> tuple[str, float | None]:
    """("in", km): within tol of a listed place in the region, or near a border; ("out", km): near a
    listed place clearly outside it, with the nearest listed place inside more than tol farther
    away; ("unknown", km or None): no listed place near the GPS position, or none in the region."""
    inside = idx.nearest_in(gps["lat"], gps["lon"], region)
    if inside is None:
        return "unknown", None
    if inside <= tol:
        return "in", inside
    outside = idx.nearest_outside(gps["lat"], gps["lon"], region, NEAR_KM)
    if outside is None:
        return "unknown", inside
    return ("out" if inside - outside > tol else "in"), inside


def unambiguous(cities: list[places.Place]) -> bool:
    return len(cities) == 1 or (len(cities) > 1 and cities[0].population >= CITY_DOMINANCE * cities[1].population)


def place_check(text: str, gps: dict | None, db=None) -> tuple[dict, str | None]:
    """(the check, its contradiction finding or None). See the module rules."""
    q = _quote(text, 60)
    try:
        idx = places.Index(db)
    except FileNotFoundError:
        return {"field": "place", "outcome": "no_index",
                "note": "Place: the offline place list is not installed (scripts/fetch_geonames.sh)."}, None
    with idx:
        res = idx.resolve(text)
        if res is None:
            return {"field": "place", "outcome": "unknown_place",
                    "note": f"Place: {q!r} is not in the offline place list (GeoNames)."}, None
        if gps is None:
            return {"field": "place", "outcome": "no_file_fact", "note": "Place: the file has no usable GPS position."}, None
        tol = PLACE_TOLERANCE_KM + gps["uncertainty_km"]
        base = {"field": "place", "file_field": "GPS position", "gps_uncertainty_km": round(gps["uncertainty_km"], 2),
                "candidates": len(res.cities)}

        # Any reading that fits the GPS position agrees: a same-name city within 50 km, or a region
        # the position is in or near. A truthful caption must never be a concern.
        near = min((places.haversine_km(gps["lat"], gps["lon"], p.lat, p.lon) for p in res.cities), default=None)
        if near is not None and near <= tol:
            return {**base, "outcome": "agree", "reading": "city", "distance_km": round(near, 1),
                    "note": f"Place: within {PLACE_TOLERANCE_KM:.0f} km of the file's GPS position (GeoNames)."}, None
        readings = []
        for region in res.regions:
            status, km = _region_status(idx, gps, region, tol)
            if status == "in":
                return {**base, "outcome": "agree", "reading": region.kind, "region": region.label,
                        "note": f"Place: the file's GPS position is in or near {region.label} (GeoNames)."}, None
            readings.append({"reading": region.kind, "name": region.label, "status": status,
                             "distance_km": None if km is None else round(km, 1),
                             "target": f"the nearest listed place in {region.label}"})

        # A city reading, unless the name is a country's, or a region's with no large city of that name.
        if res.cities and not res.country_name and (not res.regions or res.cities[0].population >= CITY_OVER_REGION_POP):
            if not unambiguous(res.cities):
                return {**base, "outcome": "ambiguous", "regions": [r["name"] for r in readings],
                        "note": f"Place: {q!r} is the name of {len(res.cities)} listed places and none has "
                                f"{CITY_DOMINANCE} times the people of the next, so the name can't be compared "
                                f"with the file's GPS position (GeoNames)."}, None
            city = res.cities[0]
            label = idx.label(city)
            dist = places.haversine_km(gps["lat"], gps["lon"], city.lat, city.lon)
            status, _ = _region_status(idx, gps, idx.region_of(city), tol)
            if status == "in":
                return {**base, "outcome": "agree", "reading": "city", "distance_km": round(dist, 1),
                        "nearest_match": {"name": city.name, "country": city.country, "geonameid": city.geonameid},
                        "note": f"Place: {label} is about {dist:,.0f} km from the file's GPS position, in or near "
                                f"the same first-level region (GeoNames)."}, None
            readings.append({"reading": "city", "name": label, "status": status, "distance_km": round(dist, 1),
                             "nearest_match": {"name": city.name, "country": city.country, "geonameid": city.geonameid},
                             "target": f"{label}, in another first-level region"})

        check = {**base, "readings": [{k: v for k, v in r.items() if k != "target"} for r in readings]}
        if readings and all(r["status"] == "out" for r in readings):
            check["outcome"] = "contradiction"
            check["distance_km"] = min(r["distance_km"] for r in readings)
            if len(readings) == 1 and readings[0].get("nearest_match"):
                check["nearest_match"] = readings[0]["nearest_match"]
            where = "; and about ".join(f"{r['distance_km']:,.0f} km from {r['target']}" for r in readings)
            return check, f"The caption says {q}. The file's own GPS position says a point about {where} (GeoNames)."
        empty = [r["name"] for r in readings if r["distance_km"] is None]
        if empty:
            note = f"Place: the offline place list has no place in {' or '.join(empty)}, so it can't be compared (GeoNames)."
        else:
            note = (f"Place: the file's GPS position is not within {NEAR_KM:.0f} km of a listed place, so whether it "
                    f"is in {' or '.join(r['name'] for r in readings)} can't be told (GeoNames).")
        return {**check, "outcome": "cant_place", "note": note}, None


def compare(claim: ClaimFacts, found: dict, db=None, date_from_caption: bool = False
            ) -> tuple[list[dict], list[tuple[str, dict]]]:
    """(checks, contradictions). Each check: field, outcome and its detail. Each contradiction:
    (finding, its check). date_from_caption: the date is the first full date the caption reader
    found, and the card says so."""
    checks, contradictions = [], []
    first = " (the first full date in the caption)" if date_from_caption else ""

    if claim.date:
        when = _claimed_time(claim.date)
        if when is None:
            checks.append({"field": "date", "outcome": "unreadable_claim", "note": "Date: the claimed date could not be read."})
        elif not found["times"]:
            checks.append({"field": "date", "outcome": "no_file_fact", "note": "Date: the file has no capture time."})
        for label, t in found["times"] if when is not None else ():
            gap, how = date_gap_days(when, t)
            check = {"field": "date", "file_field": label, "file_value": t.isoformat(), "days_apart": round(gap, 2),
                     "compared_by": how}
            if gap > DATE_TOLERANCE_DAYS:
                checks.append({**check, "outcome": "contradiction"})
                contradictions.append((f"The caption says {say_date(when)}{first}. The file's own {label} says "
                                       f"{say_date(t)}.", checks[-1]))
            else:
                checks.append({**check, "outcome": "agree",
                               "note": f"Date{first}: within {DATE_TOLERANCE_DAYS} days of the file's {label}."})

    if claim.place:
        check, finding = place_check(claim.place, found["gps"], db)
        checks.append(check)
        if finding:
            contradictions.append((finding, check))

    if claim.device:
        claimed, have = makes_in(claim.device), makes_in(found["make"])
        if not claimed:
            checks.append({"field": "device", "outcome": "unreadable_claim",
                           "note": "Device: the make could not be read from what was claimed."})
        elif not found["make"]:
            checks.append({"field": "device", "outcome": "no_file_fact", "note": "Device: the file has no camera make."})
        elif len(have) != 1:
            checks.append({"field": "device", "outcome": "unknown_file_make",
                           "note": f"Device: the file's camera make ({_quote(found['make'], 40)}) is not one Second Look knows."})
        else:
            (file_make,) = have
            check = {"field": "device", "file_field": "EXIF camera make", "file_value": found["make"],
                     "file_model": found["model"], "claimed_makes": sorted(claimed), "file_make": file_make}
            if not any(related(c, file_make) for c in claimed):
                checks.append({**check, "outcome": "contradiction"})
                model = f", model {found['model']}" if found["model"] else ""
                contradictions.append((f"The caption says {_quote(claim.device, 60)}. The file's own EXIF camera make "
                                       f"says {_quote(found['make'], 40)}{model}.", checks[-1]))
            else:
                checks.append({**check, "outcome": "agree",
                               "note": f"Device: the same make as the file's EXIF camera make ({_quote(found['make'], 40)})."})
    return checks, contradictions


def check(path: str, context: Context | None = None, db=None) -> list[Evidence]:
    """db: the GeoNames index (default $SECONDLOOK_GEONAMES_DB or ~/.cache/secondlook/geonames.sqlite)."""
    if context is None:
        return []  # nothing was claimed about the picture, so this rung has nothing to do
    claim = context.facts or (extract.rule_based(context.caption) if context.caption else None)
    # An all-number date that reads day first and month first is not a fact, and the card says so.
    both_ways = extract.ambiguous_date(context.caption) if context.caption and not (claim and claim.date) else None
    unread = [{"field": "date", "outcome": "ambiguous_claim", "claimed_text": both_ways,
               "note": f"Date: {both_ways} in the caption reads day first or month first, so no date was "
                       "compared."}] if both_ways else []
    if claim is None or not (claim.date or claim.place or claim.device):
        lead = "The caption gives no date, place or device to compare with the file's own facts."
        if unread:
            lead = f"The caption gives no date, place or device that can be compared with the file's own facts. " \
                   f"{unread[0]['note']}"
        return [_card(lead, False,
                      {"compared": False, "claimed": dataclasses.asdict(claim) if claim else None, "checks": unread})]
    found = file_facts(path, c2pa_times=bool(claim.date))
    # The rule-based reader takes the first full date in the caption; say so when that is the date compared.
    from_caption = bool(context.caption and claim.date and claim.source == "model"
                        and claim.date == extract.find_date(context.caption))
    checks, contradictions = compare(claim, found, db, date_from_caption=from_caption)
    checks = unread + checks
    raw = {"claimed": dataclasses.asdict(claim), "checks": checks,
           "rules": {"date_days": DATE_TOLERANCE_DAYS, "place_km": PLACE_TOLERANCE_KM, "device": "make"},
           "compared": any(c["outcome"] in ("agree", "contradiction") for c in checks)}
    if contradictions:
        return [_card(text, True, {**raw, "field": c["field"], "disagreement": c}) for text, c in contradictions]
    notes = " ".join(c["note"] for c in checks if c.get("note"))
    if not found["readable"] and not found["times"]:
        lead = "The file's own facts could not be read, so nothing was compared."
    elif raw["compared"]:
        lead = "No disagreement found between the caption and the file's own facts."
    else:
        lead = "Nothing could be compared between the caption and the file's own facts."
    return [_card(f"{lead} {notes}".strip(), False, raw)]
