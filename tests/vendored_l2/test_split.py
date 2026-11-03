import contextlib
import csv
import hashlib
import io
import json
import os
import pathlib
import tempfile
import unittest

from gate import split as sp


def manifest_rows(n_real=300, n_ai=250):
    rows = [{"item_id": f"real-{i:04d}", "label": "real"} for i in range(n_real)]
    rows += [{"item_id": f"ai-{i:04d}", "label": "ai"} for i in range(n_ai)]
    return rows


class SplitTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self, rows, name="manifest.csv"):
        cols = ["item_id", "label", "path", "group", "phash", "sha256"]
        path = os.path.join(self.dir, name)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})
        return path

    def run_split(self, rows, seed, out="out", extra=()):
        out = os.path.join(self.dir, out)
        with contextlib.redirect_stdout(io.StringIO()):
            code = sp.main(["--manifest", self.write_manifest(rows), "--seed", str(seed),
                            "--out", out, *extra])
        self.assertEqual(code, 0)
        text = lambda n: pathlib.Path(out, n).read_text()
        read = lambda n: list(csv.DictReader(io.StringIO(text(n))))
        return read("dev.csv"), read("test.csv"), read("excluded.csv"), \
            json.loads(text("SPLIT.json"))

    def test_deterministic_by_seed(self):
        rows = manifest_rows()
        a = self.run_split(rows, 30, "a")
        b = self.run_split(rows, 30, "b")
        c = self.run_split(rows, 31, "c")
        self.assertEqual(a[3]["dev"]["sha256"], b[3]["dev"]["sha256"])
        self.assertEqual(a[3]["test"]["sha256"], b[3]["test"]["sha256"])
        self.assertNotEqual(a[3]["dev"]["sha256"], c[3]["dev"]["sha256"])

    def test_row_order_of_manifest_does_not_matter(self):
        rows = manifest_rows()
        a = self.run_split(rows, 7, "a")
        b = self.run_split(list(reversed(rows)), 7, "b")
        self.assertEqual(a[3]["dev"]["sha256"], b[3]["dev"]["sha256"])

    def test_disjoint_and_complete_with_dev_targets(self):
        dev, test, excl, rep = self.run_split(manifest_rows(), 1)
        dev_ids, test_ids = {r["item_id"] for r in dev}, {r["item_id"] for r in test}
        self.assertFalse(dev_ids & test_ids)
        self.assertEqual(len(dev_ids | test_ids), 550)
        self.assertEqual((rep["dev"]["real"], rep["dev"]["ai"]), (100, 100))
        self.assertEqual((rep["test"]["real"], rep["test"]["ai"]), (200, 150))
        self.assertEqual(excl, [])

    def test_near_duplicates_by_phash_stay_together_and_one_is_kept(self):
        rows = manifest_rows(20, 20)
        # 64-bit hashes; real-0000/0001/0002 within 3 bits of each other, real-0003 far.
        # The rest get unrelated hashes (sha256-derived, about 32 bits apart).
        base = 0x8F3C_0A51_77E2_19B4
        for r in rows:
            r["phash"] = hashlib.sha256(r["item_id"].encode()).hexdigest()[:16]
        rows[0]["phash"] = f"{base:016x}"
        rows[1]["phash"] = f"{base ^ 0b1:016x}"
        rows[2]["phash"] = f"{base ^ 0b110:016x}"
        dev, test, excl, _ = self.run_split(rows, 3, extra=("--dev-real", "5", "--dev-ai", "5"))
        kept = {r["item_id"] for r in dev + test}
        cluster_members = {"real-0000", "real-0001", "real-0002"}
        self.assertEqual(len(kept & cluster_members), 1)
        self.assertEqual({r["item_id"] for r in excl}, cluster_members - kept)
        self.assertTrue(all("near-duplicate" in r["reason"] for r in excl))

    def test_group_and_file_hash_clusters(self):
        rows = manifest_rows(30, 30)
        for r in rows[:4]:
            r["group"] = "burst-1"            # one burst: keep one
        rows[10]["sha256"] = rows[11]["sha256"] = "ab" * 32   # exact duplicate files
        dev, test, excl, _ = self.run_split(rows, 9, extra=("--dev-real", "10", "--dev-ai", "10"))
        self.assertEqual(len(excl), 4)
        sides = {r["item_id"]: ("dev" if r in dev else "test") for r in dev + test}
        for r in excl:
            self.assertNotIn(r["item_id"], sides)
        self.assertEqual(len(dev) + len(test), 56)

    def test_mixed_label_cluster_keeps_one_per_label_on_one_side(self):
        rows = manifest_rows(10, 10)
        rows[0]["group"] = rows[10]["group"] = "pair-1"   # a real photo and its AI twin
        dev, test, excl, _ = self.run_split(rows, 4, extra=("--dev-real", "3", "--dev-ai", "3"))
        side = lambda i: "dev" if any(r["item_id"] == i for r in dev) else "test"
        self.assertEqual(side("real-0000"), side("ai-0000"))
        self.assertEqual(excl, [])

    def test_hash_files_finds_exact_duplicates(self):
        rows = manifest_rows(6, 6)
        for i, r in enumerate(rows):
            r["path"] = f"img{i}.bin"
            with open(os.path.join(self.dir, r["path"]), "wb") as f:
                f.write(b"same" if i in (0, 1) else f"img{i}".encode())
        _, _, excl, _ = self.run_split(rows, 2, extra=("--hash-files", "--dev-real", "2",
                                                       "--dev-ai", "2"))
        self.assertEqual(len(excl), 1)
        self.assertIn(excl[0]["item_id"], {"real-0000", "real-0001"})

    def test_bad_manifest(self):
        rows = manifest_rows(3, 3)
        rows[1]["item_id"] = rows[0]["item_id"]
        self.assertEqual(sp.main(["--manifest", self.write_manifest(rows), "--seed", "1",
                                  "--out", os.path.join(self.dir, "x")]), 2)
        rows = manifest_rows(3, 3)
        rows[0]["phash"], rows[1]["phash"] = "abcd", "abcdef"
        self.assertEqual(sp.main(["--manifest", self.write_manifest(rows), "--seed", "1",
                                  "--out", os.path.join(self.dir, "x")]), 2)

    def test_short_dev_is_warned(self):
        _, _, _, rep = self.run_split(manifest_rows(50, 50), 1,
                                      extra=("--dev-real", "80", "--dev-ai", "10"))
        self.assertEqual(rep["dev"]["real"], 50)
        self.assertEqual(rep["warnings"][0], "dev real 50 < 80")

    def test_phash_distance_must_be_in_range(self):
        # Codex 5: -1 put two identical hashes one in dev and one in test.
        rows = [{"item_id": "real-a", "label": "real", "phash": "0" * 16},
                {"item_id": "real-b", "label": "real", "phash": "0" * 16}]
        for d in ("-1", "64", "999"):
            with self.subTest(distance=d), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(sp.main(["--manifest", self.write_manifest(rows), "--seed", "1",
                                          "--out", os.path.join(self.dir, "x"),
                                          "--dev-real", "1", "--dev-ai", "0",
                                          "--phash-distance", d]), 2)
        dev, test, excl, _ = self.run_split(rows, 1, extra=("--dev-real", "1", "--dev-ai", "0",
                                                            "--phash-distance", "0"))
        self.assertEqual((len(dev), len(test), len(excl)), (1, 0, 1))

    def test_missing_and_partial_hash_coverage_is_warned(self):
        rows = manifest_rows(20, 20)
        _, _, _, rep = self.run_split(rows, 1, "none", extra=("--dev-real", "5", "--dev-ai", "5"))
        self.assertIn("no phash given: near-duplicates are not guarded", rep["warnings"])
        self.assertTrue(any("file sha256 known for 0 of 40" in w for w in rep["warnings"]))
        for r in rows[:30]:
            r["phash"] = hashlib.sha256(r["item_id"].encode()).hexdigest()[:16]
            r["sha256"] = hashlib.sha256(r["item_id"].encode()).hexdigest()
        _, _, _, rep = self.run_split(rows, 1, "part", extra=("--dev-real", "5", "--dev-ai", "5"))
        self.assertIn("phash given for 30 of 40 items: the other 10 are not checked for "
                      "near-duplicates", rep["warnings"])
        for r in rows:
            r["phash"] = hashlib.sha256(r["item_id"].encode()).hexdigest()[:16]
            r["sha256"] = hashlib.sha256(r["item_id"].encode()).hexdigest()
        _, _, _, rep = self.run_split(rows, 1, "full", extra=("--dev-real", "5", "--dev-ai", "5"))
        self.assertEqual(rep["warnings"], [])


if __name__ == "__main__":
    unittest.main()
