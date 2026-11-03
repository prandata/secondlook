"""C2PA rung: reads and validates an embedded C2PA manifest.

Library: c2pa-python (contentauth/c2pa-python), licence MIT OR Apache-2.0,
version recorded in each Evidence.raw["sdk"]. Runs offline: remote manifest
and OCSP fetches are switched off. A file that only points to a manifest
stored online gets one "metadata" Evidence naming the URL; it is not fetched.

The signer is checked against two lists, in this order (source, date and
sha256 of each in secondlook/trust/SOURCE.md and in raw["trust_lists"]):
  official: the C2PA Trust List, pinned in secondlook/trust/;
  interim:  the contentcredentials.org interim list, fetched into
            secondlook/trust/interim/ by scripts/fetch_trust.sh (no licence is
            stated, so it is not committed) and loaded only if its sha256
            matches the pinned one.
raw["trusted_by"] names the first list the signer chains to ("official",
"interim") or is None. Strength "signed" needs both: the manifest validates and
its signer is on one of the lists. A manifest that validates but whose signer
is on neither is strength "metadata": anyone can make a certificate, so its
claims are only as good as unsigned metadata.
"""

import hashlib
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from secondlook.types import Evidence
from secondlook.rungs._sources import (
    AI_SOURCE_TYPES,
    CAPTURE_SOURCE_TYPES,
    ai_tool_in,
    source_term,
)

log = logging.getLogger(__name__)

TRUST_DIR = Path(__file__).resolve().parent.parent / "trust"
TRUST_LIST = {
    "name": "C2PA Trust List",
    "source": (
        "https://raw.githubusercontent.com/c2pa-org/conformance-public/"
        "47009eedfe6721edd0ce3e69b89c1101cd6f03e5/trust-list/C2PA-TRUST-LIST.pem"
    ),
    "date": "2026-10-17",
    "sha256": "b3949271a07b007e92ef1d18050b55388f5e0d2686a16fd864176c48421af8ae",
}
INTERIM_DIR = TRUST_DIR / "interim"
# The interim list states no publication date; "date" is when it was fetched.
# Its anchors.pem header says it "is now frozen", so the pinned hashes hold.
INTERIM_LIST = {
    "name": "C2PA interim trust list",
    "source": "https://contentcredentials.org/trust/",
    "date": "2026-10-28",
    "files": {
        "anchors.pem": "db57e50b9bd4e4c786cbb91f38fd85e2af6a9d5412438a8cddcc42688df07ae5",
        "allowed.sha256.txt": "3597b20c9c20a0daadc7fab0d8c63534bd333ec6a715ef7117c7a4588caa15bb",
    },
}
# The card's words for each list, after "the signer is ".
_ON_LIST = {
    "official": f"on the C2PA Trust List ({TRUST_LIST['date']})",
    "interim": "on the C2PA interim trust list, which C2PA describes as temporary",
}

# Extra trust anchors (PEM) added to the official read. Tests set this to the
# c2pa-python test root; it is never set in normal use.
EXTRA_ANCHORS: str | None = None

_REMOTE = re.compile(r"remote manifests? from url (\S+)", re.I)


@lru_cache(maxsize=1)
def _interim() -> tuple[dict | None, str | None]:
    """(anchors and allowed list, None) if the fetched files match their pins, else (None, why)."""
    out = {}
    for name, pin in INTERIM_LIST["files"].items():
        p = INTERIM_DIR / name
        if not p.is_file():
            return None, f"{name} not fetched (run scripts/fetch_trust.sh)"
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != pin:
            return None, f"{name} does not match its pinned sha256"
        out[name] = data.decode()
    return {"trust_anchors": out["anchors.pem"], "allowed_list": out["allowed.sha256.txt"]}, None


def trust_lists() -> list[dict]:
    """Both lists as recorded on every card: name, source, date, sha256, whether loaded."""
    interim, why = _interim()
    return [
        {**TRUST_LIST, "id": "official", "loaded": True},
        {**INTERIM_LIST, "id": "interim", "loaded": interim is not None, **({"not_loaded": why} if why else {})},
    ]


@lru_cache(maxsize=8)
def _settings(which: str, extra: str | None) -> dict | None:
    if which == "official":
        trust = {"trust_anchors": (TRUST_DIR / "C2PA-TRUST-LIST.pem").read_text()}
        if extra:
            trust["user_anchors"] = extra
    else:
        interim, _ = _interim()
        if interim is None:
            return None
        trust = dict(interim)
    trust["trust_config"] = (TRUST_DIR / "eku.cfg").read_text()
    return {
        "verify": {"remote_manifest_fetch": False, "ocsp_fetch": False, "verify_trust": True},
        "trust": trust,
    }


def _reader(path: str, which: str = "official"):
    import c2pa  # absolute import: the c2pa-python package, not this module

    settings = _settings(which, EXTRA_ANCHORS)
    if settings is None:
        return None, c2pa.sdk_version()
    ctx = c2pa.Context(settings=c2pa.Settings.from_dict(settings))
    return c2pa.Reader.try_create(path, context=ctx), c2pa.sdk_version()


def _read(path: str, which: str) -> tuple[dict | None, bool]:
    """(store, embedded) read with one trust list. Raises what the SDK raises on open."""
    reader, _ = _reader(path, which)
    if reader is None:
        return None, False
    try:
        return json.loads(reader.json()), reader.is_embedded()
    finally:
        try:
            reader.close()
        except Exception:
            pass


def _actions(manifest: dict) -> list[dict]:
    out = []
    for a in manifest.get("assertions") or []:
        if isinstance(a, dict) and str(a.get("label", "")).startswith("c2pa.actions"):
            data = a.get("data") if isinstance(a.get("data"), dict) else {}
            out.extend(x for x in (data.get("actions") or []) if isinstance(x, dict))
    return out


def _generator(manifest: dict) -> str:
    info = manifest.get("claim_generator_info") or []
    names = [
        " ".join(str(i.get(k, "")) for k in ("name", "version")).strip()
        for i in info
        if isinstance(i, dict)
    ]
    return str(manifest.get("claim_generator") or ", ".join(n for n in names if n) or "unknown tool")


def _source_terms(manifest: dict) -> list[str]:
    terms = [source_term(a.get("digitalSourceType")) for a in _actions(manifest)]
    return [t for t in terms if t]


def _classify(active: dict, store: dict) -> tuple[str, bool, str | None]:
    """(source_class, ai_ingredient, ai_tool) for the active manifest.

    source_class: "ai" when the active manifest declares an AI source type,
    or declares no source type and its generator names a known AI image tool;
    "camera" when it declares a capture source type and nothing contradicts
    it; "other" otherwise, including a declared capture whose generator names
    an AI tool (conflicting claims are never a camera answer). A declared
    capture type is never read as AI evidence. ai_ingredient: any other
    manifest in the store (an ingredient's history) declares an AI source type.
    """
    terms = set(_source_terms(active))
    tool = ai_tool_in(_generator(active))
    ai_ingredient = any(
        set(_source_terms(m)) & AI_SOURCE_TYPES
        for m in (store.get("manifests") or {}).values()
        if isinstance(m, dict) and m is not active
    )
    if terms & AI_SOURCE_TYPES:
        return "ai", ai_ingredient, tool
    if terms & CAPTURE_SOURCE_TYPES:
        return ("other" if tool else "camera"), ai_ingredient, tool
    if tool and not terms:
        return "ai", ai_ingredient, tool
    return "other", ai_ingredient, tool


def _remote_only(path: str, err: Exception) -> list[Evidence]:
    m = _REMOTE.search(str(err))
    if not m:
        return []
    url = m.group(1)[:300]
    return [
        Evidence(
            rung="c2pa",
            finding=(
                f"The file points to a C2PA manifest stored online ({url}); Second Look works "
                "offline and did not fetch or check it."
            ),
            proves="The file names an online location for a provenance record.",
            does_not_prove=(
                "Anything about how the image was made: the record was not read, and the link "
                "itself is unsigned text that anyone can add."
            ),
            strength="metadata",
            raw={"remote_manifest_url": url, "embedded": False, "valid": None, "trusted": False},
        )
    ]


def _trusted(store: dict) -> bool:
    state, _, failures = _state(store)
    return state == "Trusted" and not failures


def check(path: str) -> list[Evidence]:
    try:
        return _check(path)
    except Exception as e:  # a malformed store shape must not escape
        log.warning("c2pa: unexpected manifest shape in %s: %s", path, e)
        return []


def _state(store: dict) -> tuple[str, list[str], list[str]]:
    """(validation_state, all status codes, active-manifest failures other than untrusted)."""
    state = store.get("validation_state") or "Unknown"
    codes = [s.get("code") for s in (store.get("validation_status") or []) if isinstance(s, dict) and s.get("code")]
    # Prefer the active manifest's own failure list; validation_status also
    # carries ingredient codes. An untrusted signer is reported via "trusted".
    results = (store.get("validation_results") or {}).get("activeManifest") or {}
    failed = (
        [s.get("code") for s in (results.get("failure") or []) if isinstance(s, dict) and s.get("code")]
        if results
        else codes
    )
    return state, codes, [c for c in failed if c != "signingCredential.untrusted"]


def _check(path: str) -> list[Evidence]:
    import c2pa as sdk_mod

    sdk = sdk_mod.sdk_version()
    try:
        store, embedded = _read(path, "official")
    except Exception as e:  # unsupported type, unreadable file, corrupt JUMBF, remote-only
        log.info("c2pa: no readable manifest in %s: %s", path, e)
        return _remote_only(str(path), e)
    if not isinstance(store, dict):
        return []
    trusted_by = "official" if _trusted(store) else None
    if trusted_by is None:
        # Official first; the interim list is asked only when the official one does
        # not trust the signer, and its reading is used only if it does.
        try:
            alt, alt_embedded = _read(path, "interim")
        except Exception as e:
            log.info("c2pa: interim read failed for %s: %s", path, e)
            alt = None
        if isinstance(alt, dict) and _trusted(alt):
            store, embedded, trusted_by = alt, alt_embedded, "interim"

    manifests = store.get("manifests") if isinstance(store.get("manifests"), dict) else {}
    active = manifests.get(store.get("active_manifest") or "") or {}
    if not isinstance(active, dict):
        active = {}
    state, codes, failures = _state(store)
    valid = state in ("Valid", "Trusted") and not failures
    trusted = valid and state == "Trusted" and trusted_by is not None
    if not trusted:
        trusted_by = None
    lists = trust_lists()
    sig = active.get("signature_info") or {}
    issuer = sig.get("issuer") or sig.get("common_name") or "an unnamed signer"
    when = str(sig.get("time") or "")[:10]
    generator = _generator(active)
    source_class, ai_ingredient, tool = _classify(active, store)
    terms = _source_terms(active)
    raw = {
        "sdk": f"c2pa-python {sdk}",
        "validation_state": state,
        "valid": valid,
        "trusted": trusted,
        "trusted_by": trusted_by,
        # The list that trusted the signer (None when neither did), and both lists as loaded.
        "trust_list": next((t for t in lists if t["id"] == trusted_by), None),
        "trust_lists": lists,
        "failures": failures,
        "status_codes": codes,
        "signer_issuer": sig.get("issuer"),
        "signer_common_name": sig.get("common_name"),
        "sign_time": sig.get("time"),
        "claim_generator": generator,
        "digital_source_types": terms,
        "source_class": source_class,
        "ai_ingredient": ai_ingredient,
        "ai_tool_name": tool,
        # An AI declaration the ladder can't take as signed (untrusted signer,
        # an AI tool name next to a declared capture, or AI only in an
        # ingredient's history) is an AI label, so it raises a concern.
        "ai_label": bool(valid and not trusted and source_class == "ai")
        or bool(valid and tool and terms and set(terms) & CAPTURE_SOURCE_TYPES)
        or bool(valid and ai_ingredient),
        "actions": [a.get("action") for a in _actions(active)],
        "ingredient_count": len(active.get("ingredients") or []),
        "embedded": embedded,
    }

    if not valid:
        return [
            Evidence(
                rung="c2pa",
                finding=(
                    f"The file carries a C2PA manifest from {generator}, but it failed "
                    f"validation ({', '.join(failures) or state})."
                ),
                proves="Someone attached a provenance record to this file at some point.",
                does_not_prove=(
                    "Anything the record says: the file or the record changed after signing, "
                    "or the signature is broken, so its claims can't be relied on."
                ),
                strength="metadata",
                raw=raw,
            )
        ]

    signed_by = f"signed by {issuer}" + (f" on {when}" if when else "")
    ai_terms = [t for t in terms if t in AI_SOURCE_TYPES]
    if source_class == "ai":
        what = (
            f"declares it was made with generative AI ({', '.join(ai_terms)})"
            if ai_terms
            else f"names no source type, and its signing tool is called {generator!r}, an AI image tool"
        )
        proves = "The signer recorded that this content came from an AI generator."
        limit = "Who made it or why; only that the signing tool labelled it AI-made."
    elif source_class == "camera":
        what = f"declares a camera capture ({', '.join(terms)})"
        if ai_ingredient:
            what += ", with an AI-made ingredient in its history"
        proves = "The signer recorded a device capture."
        limit = (
            "That the scene is as shown or the caption is true; a signed capture can still "
            "be staged or mislabelled."
        )
    else:
        what = "records edits or a source that is neither a camera capture nor generative AI"
        if terms:
            what += f" ({', '.join(terms)})"
        if tool and set(terms) & CAPTURE_SOURCE_TYPES:
            what = (
                f"declares a camera capture ({', '.join(terms)}), but its signing tool is called "
                f"{generator!r}, an AI image tool; the two claims conflict"
            )
        if ai_ingredient:
            what += ", with an AI-made ingredient in its history"
        proves = "The signer's record for this file."
        limit = "Where the original content came from, or whether AI was used before signing."

    if trusted:
        return [
            Evidence(
                rung="c2pa",
                finding=f"C2PA manifest {signed_by} via {generator}; it {what}. The signer is {_ON_LIST[trusted_by]}.",
                proves=proves + " The signer chains to that list and the file has not "
                "changed since it was signed.",
                does_not_prove=limit,
                strength="signed",
                raw=raw,
            )
        ]
    return [
        Evidence(
            rung="c2pa",
            finding=(
                f"C2PA manifest {signed_by} via {generator}; it {what}. The signature checks out, "
                f"but the signer is not {_ON_LIST['official']}"
                + (" or on the C2PA interim trust list." if lists[1]["loaded"] else
                   "; the C2PA interim trust list was not loaded.")
            ),
            proves="Whoever holds this signing key wrote this record, and the file has not "
            "changed since.",
            does_not_prove=(
                "Who the signer is: anyone can make a signing certificate, so this record is "
                "worth no more than unsigned metadata. " + limit
            ),
            strength="metadata",
            raw=raw,
        )
    ]
