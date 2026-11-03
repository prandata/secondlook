"""The no-picks receipt, for a byte-for-byte compare with main's (DAT-850, Codex on PR #42).

plain_receipt() returns the JSON `POST /api/receipt?format=json` gives for plain.jpg with no caption and no
deep check, on the EXIF rung, with the clock, the build's git sha and the library versions fixed (those change
from build to build and machine to machine; nothing else in the receipt should). The committed
plain.receipt.json was written by main at e219f4a with this file, and rewritten with it when PR #39 (every
surface prints explain(result).summary) merged main; only its summary line changed:

    python tests/fixtures/dat850/plain_receipt.py tests/fixtures/dat850/plain.receipt.json   # from the checkout root
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXED_VERSIONS = {"package": "fixed", "python": "fixed", "python_implementation": "fixed", "libraries": {}}


def plain_receipt(patch=setattr) -> bytes:
    """`patch(obj, name, value)` fixes the three changing things: setattr, or a test's monkeypatch.setattr."""
    from fastapi.testclient import TestClient

    from secondlook import ladder
    from secondlook import receipt as receipts
    from secondlook.rungs import exif
    from secondlook.web import app as web

    patch(receipts, "_now", lambda: "2026-10-30T00:00:00Z")
    patch(receipts, "_versions", lambda: dict(FIXED_VERSIONS))
    patch(web, "_git_sha", lambda: "fixed")
    patch(ladder, "_version", lambda: "fixed")
    client = TestClient(web.create_app(stub=False, rungs=[("exif", exif.check)]))
    res = client.post("/api/receipt?format=json",
                      files={"file": ("plain.jpg", (HERE / "plain.jpg").read_bytes(), "image/jpeg")})
    assert res.status_code == 200, res.text
    return res.content


if __name__ == "__main__":
    sys.path.insert(0, str(Path.cwd()))
    Path(sys.argv[1]).write_bytes(plain_receipt())
