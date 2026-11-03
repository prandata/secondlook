"""Rungs: each module exposes check(path) -> list[Evidence]. A rung that cannot run at all may raise:
the ladder catches it and lists the rung under "could not run".

A rung whose check also takes `context` (the context rung) gets the ladder's Context."""
import importlib
import logging

log = logging.getLogger("secondlook.rungs")

# Order matters only for display; the ladder decides on the evidence, not the order.
DEFAULT_RUNGS = ("c2pa", "iptc", "exif", "classifier", "watermark")
# Rungs that read the ladder's Context. The ladder adds them only when a context is given, so a
# check with no caption is exactly what it was.
CONTEXT_RUNGS = ("context",)


def load(names=DEFAULT_RUNGS):
    """Import the rung modules that exist. A missing or broken rung is skipped and logged."""
    loaded = []
    for name in names:
        try:
            mod = importlib.import_module(f"secondlook.rungs.{name}")
        except Exception as exc:  # a lane not merged yet, or a missing optional dependency
            log.info("rung %s not available: %s", name, exc)
            continue
        if callable(getattr(mod, "check", None)):
            loaded.append((name, mod.check))
    return loaded
