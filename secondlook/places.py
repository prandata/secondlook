"""Offline place lookup for the context rung: GeoNames cities5000, countries and first-level regions
in a local SQLite file.

Data: GeoNames (https://www.geonames.org/): every place with a population of 5,000 or more
(cities5000), the country table (countryInfo.txt) and the names of first-level regions such as
states and provinces (admin1CodesASCII.txt). Licence CC BY 4.0: credit "GeoNames" wherever a
looked-up place is shown (docs/LICENSES.md). scripts/fetch_geonames.sh downloads the three files,
checks their sha256 and calls build() here. Nothing is fetched at run time.

A caption's place can name a country ("Australia"), a first-level region ("Victoria", "Texas") or a
city ("Paris"), and one name can be several of these. resolve() returns every reading; the context
rung decides what the readings mean against the GPS position. The GPS position is placed only by
distances to listed places (nearest_in, nearest_outside); there are no boundaries in this index.

Usage:
    python -m secondlook.places --build cities5000.txt countryInfo.txt admin1CodesASCII.txt geonames.sqlite
    python -m secondlook.places --db geonames.sqlite "Paris, France"
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

DB_ENV = "SECONDLOOK_GEONAMES_DB"
DEFAULT_DB = Path.home() / ".cache" / "secondlook" / "geonames.sqlite"
EARTH_RADIUS_KM = 6371.0088  # IUGG mean radius
KM_PER_DEGREE = math.pi * EARTH_RADIUS_KM / 180
MAX_CANDIDATES = 50
SCHEMA = "2"  # 2: first-level regions and country aliases (DAT-872 fix round)
# An admin1 code GeoNames uses when a place's first-level region is not known.
UNKNOWN_ADMIN1 = ("", "00")

# Common English names for countries that countryInfo.txt does not carry. Written here, not GeoNames.
COUNTRY_ALIASES = {
    "uk": "GB", "u k": "GB", "britain": "GB", "great britain": "GB",
    "usa": "US", "u s": "US", "u s a": "US", "united states of america": "US",
    "uae": "AE", "holland": "NL", "czech republic": "CZ", "burma": "MM", "east timor": "TL",
}


@dataclass
class Place:
    geonameid: int
    name: str
    country: str      # ISO 3166-1 alpha-2
    admin1: str       # GeoNames admin1 code ("TX", "08")
    lat: float
    lon: float
    population: int


@dataclass(frozen=True)
class Region:
    kind: str         # "country" or "admin1"
    country: str      # ISO 3166-1 alpha-2
    admin1: str       # GeoNames admin1 code, "" for a country
    label: str        # "Australia", "Victoria, Australia"

    def holds(self, country: str, admin1: str) -> bool:
        return country == self.country and (self.kind == "country" or admin1 == self.admin1)


@dataclass
class Resolved:
    """Every reading of a written place, after its qualifiers ("Paris, Texas")."""
    text: str
    regions: list[Region] = field(default_factory=list)
    cities: list[Place] = field(default_factory=list)   # most populous first
    country_name: bool = False   # the place itself is a country's name, so countries come first


def norm(text: str) -> str:
    """Case-folded, accents removed, punctuation to spaces: "São Paulo" -> "sao paulo"."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def region_key(text: str) -> str:
    """norm() without a leading "the": "The Netherlands" and "Netherlands" are one name."""
    key = norm(text)
    return key[4:] if key.startswith("the ") else key


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def build(cities_txt: str | Path, country_txt: str | Path, admin1_txt: str | Path, db_path: str | Path, *,
          source: str = "", sha256: str = "", downloaded_at: str = "") -> int:
    """Build the SQLite index from the three GeoNames text files. Returns the number of places."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(db_path.suffix + ".part")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript("""
        CREATE TABLE places (geonameid INTEGER PRIMARY KEY, name TEXT, country TEXT, admin1 TEXT,
                             lat REAL, lon REAL, population INTEGER);
        CREATE TABLE names (norm TEXT, geonameid INTEGER);
        CREATE TABLE regions (norm TEXT, kind TEXT, country TEXT, admin1 TEXT, label TEXT, code INTEGER);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    n = 0
    with open(cities_txt, encoding="utf-8") as f:
        for line in f:
            col = line.rstrip("\n").split("\t")
            if len(col) < 15:
                continue
            gid, name, ascii_name, alt = int(col[0]), col[1], col[2], col[3]
            con.execute("INSERT INTO places VALUES (?,?,?,?,?,?,?)",
                        (gid, name, col[8], col[10], float(col[4]), float(col[5]), int(col[14] or 0)))
            keys = {norm(x) for x in [name, ascii_name, *alt.split(",")] if x and "://" not in x}
            con.executemany("INSERT INTO names VALUES (?,?)", [(k, gid) for k in keys if k])
            n += 1
    countries = {}
    with open(country_txt, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            col = line.rstrip("\n").split("\t")
            if len(col) < 5 or not col[0]:
                continue
            iso, iso3, name = col[0], col[1], col[4]
            countries[iso] = name
            rows = [(region_key(name), 0), (norm(iso), 1), (norm(iso3), 1)]
            con.executemany("INSERT INTO regions VALUES (?,?,?,?,?,?)",
                            [(k, "country", iso, "", name, code) for k, code in rows if k])
    for alias, iso in COUNTRY_ALIASES.items():
        if iso in countries:
            con.execute("INSERT INTO regions VALUES (?,?,?,?,?,?)", (alias, "country", iso, "", countries[iso], 0))
    with open(admin1_txt, encoding="utf-8") as f:
        for line in f:
            col = line.rstrip("\n").split("\t")
            if len(col) < 3 or "." not in col[0]:
                continue
            iso, code = col[0].split(".", 1)
            if iso not in countries or code in UNKNOWN_ADMIN1:
                continue
            label = f"{col[1]}, {countries[iso]}"
            keys = {(region_key(col[1]), 0), (region_key(col[2]), 0)}
            if code.isalpha():  # "TX", "ENG"; numeric codes ("02") are never written in a caption
                keys.add((norm(code), 1))
            con.executemany("INSERT INTO regions VALUES (?,?,?,?,?,?)",
                            [(k, "admin1", iso, code, label, c) for k, c in keys if k])
    con.executescript("""
        CREATE INDEX names_norm ON names(norm); CREATE INDEX regions_norm ON regions(norm);
        CREATE INDEX places_lat ON places(lat); CREATE INDEX places_region ON places(country, admin1);
    """)
    con.executemany("INSERT INTO meta VALUES (?,?)", [
        ("source", source or "GeoNames cities5000, countryInfo and admin1CodesASCII"),
        ("licence", "CC BY 4.0, GeoNames"), ("sha256", sha256), ("downloaded_at", downloaded_at),
        ("places", str(n)), ("schema", SCHEMA)])
    con.commit()
    con.close()
    os.replace(tmp, db_path)
    return n


def db_path(db: str | Path | None = None) -> Path:
    return Path(db or os.environ.get(DB_ENV) or DEFAULT_DB)


def available(db: str | Path | None = None) -> bool:
    return db_path(db).is_file()


def meta(db: str | Path | None = None) -> dict:
    with sqlite3.connect(f"file:{db_path(db)}?mode=ro", uri=True) as con:
        return dict(con.execute("SELECT key, value FROM meta"))


def _is_code(raw: str) -> bool:
    """A code counts only when written in capitals ("USA", "TX"), so "Ben" is never Benin."""
    raw = raw.strip()
    return raw.isalpha() and raw.isupper()


class Index:
    """The built index, opened read-only. Raises FileNotFoundError when it is missing, or was built
    by an older version of this file (rerun scripts/fetch_geonames.sh)."""

    def __init__(self, db: str | Path | None = None):
        path = db_path(db)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            schema = dict(self.con.execute("SELECT key, value FROM meta")).get("schema")
        except sqlite3.DatabaseError:
            schema = None
        if schema != SCHEMA:
            self.con.close()
            raise FileNotFoundError(f"{path} was built by an older version; rerun scripts/fetch_geonames.sh")

    def __enter__(self) -> Index:
        return self

    def __exit__(self, *exc) -> None:
        self.con.close()

    def regions(self, raw: str, qualifier: bool = False) -> list[Region]:
        """Countries and first-level regions the text names. A code counts only in capitals; a
        two-letter code ("FR", "TX") only as a qualifier after a comma."""
        key = region_key(raw)
        out = []
        for kind, country, admin1, label, code in self.con.execute(
                "SELECT kind, country, admin1, label, code FROM regions WHERE norm = ?", (key,)):
            if code and not (_is_code(raw) and (qualifier or len(raw.strip()) == 3)):
                continue
            r = Region(kind, country, admin1, label)
            if r not in out:
                out.append(r)
        return out

    def cities(self, raw: str) -> list[Place]:
        rows = self.con.execute(
            "SELECT p.geonameid, p.name, p.country, p.admin1, p.lat, p.lon, p.population FROM names n "
            "JOIN places p ON p.geonameid = n.geonameid WHERE n.norm = ? GROUP BY p.geonameid "
            "ORDER BY p.population DESC LIMIT ?", (norm(raw), MAX_CANDIDATES)).fetchall()
        return [Place(*r) for r in rows]

    def label(self, place: Place) -> str:
        """"Paris, Île-de-France, France" (GeoNames names), to say which of several places was meant."""
        return f"{place.name}, {self.region_of(place).label}"

    def region_of(self, place: Place) -> Region:
        """The first-level region a listed place is in, or its country when GeoNames has no region for it."""
        if place.admin1 not in UNKNOWN_ADMIN1:
            row = self.con.execute("SELECT label FROM regions WHERE kind = 'admin1' AND country = ? AND admin1 = ? "
                                   "LIMIT 1", (place.country, place.admin1)).fetchone()
            if row:
                return Region("admin1", place.country, place.admin1, row[0])
        row = self.con.execute("SELECT label FROM regions WHERE kind = 'country' AND country = ? LIMIT 1",
                               (place.country,)).fetchone()
        return Region("country", place.country, "", row[0] if row else place.country)

    def resolve(self, text: str) -> Resolved | None:
        """Every reading of a written place, or None when nothing in it is a listed place.

        The first comma-separated part that names a country, region or city is the place; a first
        part that is not listed ("Trafalgar Square, London") falls back to the next. Later parts
        that name a country or region narrow it ("Paris, Texas", "Georgia, USA"); a qualifier that
        names nothing is ignored. When a qualifier rules out every reading ("Paris, Australia" with
        no Paris listed in Australia), the place is the qualifier's region.
        """
        raws = [x for x in text.split(",") if norm(x)]
        for i, raw in enumerate(raws):
            regions, cities = self.regions(raw), self.cities(raw)
            if regions or cities:
                break
        else:
            return None
        res = Resolved(text=text, regions=regions, cities=cities,
                       country_name=any(r.kind == "country" for r in regions))
        for raw in raws[i + 1:]:
            within = self.regions(raw, qualifier=True)
            if not within:
                continue
            res.cities = [p for p in res.cities if any(q.holds(p.country, p.admin1) for q in within)]
            res.regions = [r for r in res.regions
                           if any(q.country == r.country and (q.kind == "country" or q == r) for q in within)]
            res.country_name = any(r.kind == "country" for r in res.regions)
            if not res.cities and not res.regions:
                res.regions, res.country_name = within, any(q.kind == "country" for q in within)
        return res

    def nearest_in(self, lat: float, lon: float, region: Region) -> float | None:
        """Distance in km to the nearest listed place in the region, or None when it has none."""
        if region.kind == "country":
            rows = self.con.execute("SELECT lat, lon FROM places WHERE country = ?", (region.country,))
        else:
            rows = self.con.execute("SELECT lat, lon FROM places WHERE country = ? AND admin1 = ?",
                              (region.country, region.admin1))
        return min((haversine_km(lat, lon, a, b) for a, b in rows), default=None)

    def nearest_outside(self, lat: float, lon: float, region: Region, within_km: float) -> float | None:
        """Distance in km to the nearest listed place within within_km that is clearly not in the
        region (another country, or another known first-level region), or None when there is none."""
        dlat = within_km / KM_PER_DEGREE
        sql = "SELECT lat, lon, country, admin1 FROM places WHERE lat BETWEEN ? AND ?"
        args: tuple = (lat - dlat, lat + dlat)
        cos = math.cos(math.radians(lat))
        if cos > 0.05:
            dlon = dlat / cos
            if -180 <= lon - dlon and lon + dlon <= 180:  # no box across the antimeridian
                sql += " AND lon BETWEEN ? AND ?"
                args += (lon - dlon, lon + dlon)
        best = None
        for a, b, country, admin1 in self.con.execute(sql, args):
            if country == region.country and (region.kind == "country" or admin1 in UNKNOWN_ADMIN1
                                              or admin1 == region.admin1):
                continue
            d = haversine_km(lat, lon, a, b)
            if d <= within_km and (best is None or d < best):
                best = d
        return best


def lookup(text: str, db: str | Path | None = None) -> list[Place]:
    """Every listed city the text could name, most populous first, after its qualifiers.

    "Paris" gives every Paris in the list. "Paris, France", "Paris, FR", "Paris, TX" and
    "Paris, Texas" keep only the places in that country or first-level region; a qualifier that
    matches nothing is ignored rather than trusted. Raises FileNotFoundError when the index is not built.
    """
    with Index(db) as idx:
        res = idx.resolve(text)
    return res.cities if res else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="secondlook.places")
    ap.add_argument("--build", nargs=4, metavar=("CITIES_TXT", "COUNTRY_TXT", "ADMIN1_TXT", "DB"))
    ap.add_argument("--source", default="")
    ap.add_argument("--sha256", default="")
    ap.add_argument("--downloaded-at", default="")
    ap.add_argument("--db", help=f"index path (default ${DB_ENV} or {DEFAULT_DB})")
    ap.add_argument("place", nargs="?")
    a = ap.parse_args(argv)
    if a.build:
        n = build(*a.build, source=a.source, sha256=a.sha256, downloaded_at=a.downloaded_at)
        print(json.dumps({"places": n, "db": a.build[3]}))
        return 0
    if not a.place:
        ap.error("give a place to look up, or --build")
    with Index(a.db) as idx:
        res = idx.resolve(a.place)
    out = None if res is None else {"regions": [asdict(r) for r in res.regions],
                                    "cities": [asdict(p) for p in res.cities], "country_name": res.country_name}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
