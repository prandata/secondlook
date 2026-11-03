"""The ladder: turn evidence from every rung into one of four answers (PLAN.md).

1. A valid, trusted-signer C2PA manifest that declares AI     -> signed_ai
2. A valid, trusted-signer C2PA manifest of a camera capture
   that explicitly has no AI ingredient                        -> signed_camera
3. Any AI label in unsigned metadata, a classifier score at or
   above a frozen threshold that passed its gate (the rung's
   raises_concern), a manifest that is invalid or
   from an untrusted signer, or a caption the file's own facts
   contradict (the context rung)                               -> concerns
4. Anything else (always with one limit card)                  -> cant_tell

Unsigned metadata is never a verdict on its own. Nothing here says "real".
"""
from __future__ import annotations

import functools
import inspect
import logging
import subprocess
from pathlib import Path

from . import rungs as rung_registry
from .types import Context, Evidence, FailedToLoad, NotInstalled, Result

log = logging.getLogger("secondlook.ladder")

# Reserved raw key that marks the ladder's own limit card. A rung's raw may use any other key,
# including `limit`, without being dropped.
LADDER_LIMIT_KEY = "_ladder_limit"

# Why a rung could not run, from a fixed list, by the type of what it raised (W11 stage 2, S-b). The page
# shows the reason after the check's name. Anything not listed has no reason here: the page says the
# cause is in the server log, where run_traced logs the error itself. First match wins.
FAILURE_REASONS: tuple[tuple[tuple[type[BaseException], ...], str], ...] = (
    ((NotInstalled, ImportError, FileNotFoundError), "not installed"),
    ((FailedToLoad,), "failed to load"),
    ((MemoryError,), "ran out of memory"),
    ((TimeoutError,), "timed out"),
)


def failure_reason(exc: BaseException) -> str | None:
    """The fixed reason for a rung that raised `exc`, or None when its type is not on the list."""
    for kinds, why in FAILURE_REASONS:
        if isinstance(exc, kinds):
            return why
    return None


# What run_traced returns: the result, the rungs that ran, the rungs that raised, and their reasons.
Traced = tuple[Result, list[str], list[str], list[str | None]]


@functools.lru_cache(maxsize=1)  # one git call per process, not one per image (a gate run scores ~3,000)
def _version() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _valid_c2pa(e: Evidence) -> bool:
    """A manifest counts as signed only when it validated AND its signer is on the trust list.

    Both flags must be the boolean True: a truthy string such as "false" is not enough, and a
    self-signed certificate can make a manifest valid, so `valid` alone is forgeable.
    """
    return (e.rung == "c2pa" and e.strength == "signed"
            and e.raw.get("valid") is True and e.raw.get("trusted") is True)


def _is_concern(e: Evidence) -> bool:
    if e.rung == "classifier":
        # Both flags, as booleans: at or above the frozen threshold, and that threshold passed its gate
        # (DAT-850). A failed, invalid or missing gate status leaves the score as information only.
        return e.raw.get("above_threshold") is True and e.raw.get("raises_concern") is True
    if e.rung in ("iptc", "exif", "c2pa") and e.raw.get("ai_label"):
        return True
    # A C2PA manifest that was checked and is invalid, or valid but not from a trusted signer,
    # is worth listing as a reason, never a verdict. `valid` None means it was not checked
    # (a manifest stored online, which Second Look does not fetch): that is no concern.
    if e.rung == "c2pa" and e.raw.get("valid") is not None and not _valid_c2pa(e):
        return True
    # The caption and the file's own facts disagree: a reason, never a verdict.
    if e.rung == "context" and e.raw.get("contradiction") is True:
        return True
    return False


def _keep(items) -> list[Evidence]:
    """Materialise what a rung returned and keep only well-formed Evidence cards.

    A lazy iterator may raise mid-way (the caller's try handles that); anything that is not an
    Evidence with a dict `raw` is dropped, so the decision code can read `raw` safely. A card
    carrying the reserved `_ladder_limit` key is dropped too: only the ladder makes the limit card.
    A plain `limit` key is a rung's own field (a classifier may report one) and is kept.
    """
    return [e for e in list(items or [])
            if isinstance(e, Evidence) and isinstance(e.raw, dict) and LADDER_LIMIT_KEY not in e.raw]


def _signed_gap(signed: list[Evidence]) -> str:
    """One true sentence about a valid, trusted manifest that maps to neither signed answer."""
    if any(e.raw.get("ai_ingredient") is True for e in signed):
        return ("A signed, trusted record is present and lists an AI ingredient, but it does not "
                "say the image was made with AI; see the card.")
    return ("A signed, trusted record is present but does not state AI or camera capture "
            "without an AI ingredient; see the card.")


def decide(evidence: list[Evidence]) -> tuple[str, str]:
    """Return (answer, summary) from the evidence alone."""
    evidence = _keep(evidence)
    signed = [e for e in evidence if _valid_c2pa(e)]
    for e in signed:
        if e.raw.get("source_class") == "ai":
            return "signed_ai", "A valid Content Credentials manifest says AI was used to make or edit this image. " \
                                "That tells you how it was made, not whether any claim about it is true."
    for e in signed:
        if e.raw.get("source_class") == "camera" and e.raw.get("ai_ingredient") is False:
            return "signed_camera", "A valid Content Credentials manifest says this was captured by a camera. " \
                                    "That is not proof that what it shows, or any claim about it, is true."
    concerns = [e for e in evidence if _is_concern(e)]
    if concerns:
        n = len(concerns)
        return "concerns", f"{n} {'concern' if n == 1 else 'concerns'} found; see the reasons. None of them is proof on its own."
    if signed:
        # Every valid, trusted manifest fell through both signed rules, so it must not be denied.
        return "cant_tell", f"We can't tell how this image was made. {_signed_gap(signed)} " \
                            "No concern was found, and that is not evidence either way."
    return "cant_tell", "We can't tell how this image was made. No signed record and no concern was found, " \
                        "and that is not evidence either way."


def _limit_card(path: str, ran: list[str], failed: list[str], signed: list[Evidence]) -> Evidence:
    """The one card every cant_tell answer carries: what was checked, and what that leaves open.

    `signed` holds the valid, trusted manifests in the evidence; the card must not deny them.
    """
    checked = ", ".join(ran) if ran else "none"
    if signed:
        finding = (f"Checked: {checked}. {_signed_gap(signed)} No AI label in the metadata and no "
                   "classifier score that raises a concern was found.")
        proves = "Only that a signed, trusted record is present and the checks listed here found no concern."
    elif not Path(path).is_file():
        finding = "The file could not be read, so nothing was checked."
        proves = "Only that the checks listed here found nothing on this file."
    else:
        finding = (f"Checked: {checked}. None found a signed provenance record, an AI label in "
                   "the metadata, or a classifier score that raises a concern.")
        proves = "Only that the checks listed here found nothing on this file."
    if failed and (signed or Path(path).is_file()):
        finding += f" These checks could not run: {', '.join(failed)}."
    return Evidence(
        rung="origin",  # inert in the ladder; the contract has no ladder rung. UIs key off raw["limit"]; the ladder keys off raw[LADDER_LIMIT_KEY].
        finding=finding,
        proves=proves,
        does_not_prove="Not finding anything does not show how the image was made: metadata is "
                       "easily stripped, and the classifier is not reliable on every image. It is "
                       "not evidence either way.",
        strength="none",
        raw={"limit": True, LADDER_LIMIT_KEY: True, "rungs_run": list(ran), "rungs_failed": list(failed),
             "file_readable": Path(path).is_file()},  # read by explain.py's templates
    )


def _takes_context(check) -> bool:
    """True when a rung's check has a `context` parameter (or **kwargs)."""
    try:
        params = inspect.signature(check).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "context" and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
               or p.kind is p.VAR_KEYWORD for p in params)


def run(path: str, rungs=None, context: Context | None = None) -> Result:
    """Run every available rung on one file and decide. Never raises on bad input.

    `context` goes only to a rung whose check takes it; every other rung is called as check(path).
    With the default rungs, the context rungs run only when a context is given.
    """
    return run_traced(path, rungs, context)[0]


def run_traced(path: str, rungs=None, context: Context | None = None) -> Traced:
    """`run`, plus the names of the rungs that ran and of those that raised, in order, and for each
    rung that raised its reason from FAILURE_REASONS (None when its error type is not on the list).

    The web page counts only the checks that ran and names the ones that could not, with the reason
    (W11, DAT-850); the limit card carries the same split, but only on a cant_tell answer.
    """
    evidence: list[Evidence] = []
    ran: list[str] = []
    failed: list[str] = []
    why: list[str | None] = []
    if rungs is None:
        rungs = rung_registry.load()
        if context is not None:
            rungs = rungs + rung_registry.load(rung_registry.CONTEXT_RUNGS)
    for name, check in rungs:
        try:
            # Everything a rung does, including walking a lazy result, stays inside the try.
            found = _keep(check(path, context=context) if _takes_context(check) else check(path))
        except (Exception, SystemExit) as exc:  # a rung must not take the ladder down
            log.warning("rung %s failed on %s: %r", name, path, exc)
            failed.append(name)
            why.append(failure_reason(exc))
            continue
        ran.append(name)
        evidence.extend(found)
    answer, summary = decide(evidence)
    if answer == "cant_tell":
        evidence.append(_limit_card(path, ran, failed, [e for e in evidence if _valid_c2pa(e)]))
    return Result(answer=answer, evidence=evidence, summary=summary, version=_version()), ran, failed, why
