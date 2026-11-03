import json

from secondlook import cli, ladder
from secondlook.rungs.stub import make


def test_cli_prints_result_json(tmp_path, capsys):
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    assert cli.main(["check", str(f)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["answer"] in {"signed_ai", "signed_camera", "concerns", "cant_tell"}
    assert set(out) == {"answer", "evidence", "summary", "version", "explanation"}


def test_cli_survives_non_json_values_in_raw(tmp_path, capsys, monkeypatch):
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    card = make("iptc", ai_label=True, blob=b"\x00\xff", when=object())
    monkeypatch.setattr(ladder.rung_registry, "load", lambda *a, **k: [("x", lambda p: [card])])
    assert cli.main(["check", str(f)]) == 0
    out = json.loads(capsys.readouterr().out)   # whole document parses: no partial output
    assert out["answer"] == "concerns" and "blob" in out["evidence"][0]["raw"]


def test_cli_cant_tell_prints_the_limit_card(tmp_path, capsys, monkeypatch):
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    monkeypatch.setattr(ladder.rung_registry, "load", lambda *a, **k: [])
    cli.main(["check", str(f)])
    out = json.loads(capsys.readouterr().out)
    assert out["answer"] == "cant_tell" and out["evidence"][0]["raw"]["limit"] is True
