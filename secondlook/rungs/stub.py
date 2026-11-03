"""Stub rung for tests and the demo before real rungs land. Marks every card raw['stub']=True."""
from ..types import Evidence


def make(rung, strength="metadata", **raw):
    raw = {"stub": True, **raw}
    return Evidence(rung=rung, finding=f"stub {rung} finding", proves="nothing (stub)",
                    does_not_prove="anything (stub)", strength=strength, raw=raw)
