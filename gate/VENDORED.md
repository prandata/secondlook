# Vendored: L2 gate harness

Copied unchanged (gate/*.py, examples/, and its tests under tests/vendored_l2/) from
the private L2 gate-harness repository, branch `main`, commit
`b8679a94791598d4a2ea84700ff0c18d4b87dd71` (be6a423 plus the LEDGER row). Python standard library only.
Authorship and history: that repo's LEDGER.md. The one edit: tests/vendored_l2/test_harness.py finds
`gate/` with `parents[2]` because the tests moved one folder down.

Not in this copy (L4 NOTE-FOR-L2, items 2, 4, 5, 6), and why `secondlook/gate_run.py` does them instead of changing it:
- `--presplit`: L4 fixes dev and test by unit; `gate_run` takes `dev.csv`, `test.csv` and `SPLIT.json` as given and checks their sha256.
- the `size_matched_512` diagnostic arm: the harness already accepts any extra arm name and reports it after the known ones; it is never gated.
- the training-data fields: `gate_run` refuses a threshold file with no `training_data_statement` (PROTOCOL.md F).
- the shortcut `baseline.json`: `gate_run` runs it (`secondlook/gate_baseline.py`) and prints it beside each arm in RESULTS.md.
