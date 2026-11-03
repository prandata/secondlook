# S2 stage 3 (DAT-847): the 9 demo samples, offline, with both trust lists

- **Code:** `s2-rungs` at `00b4ea2` (C2PA, IPTC and EXIF rungs), checked out fresh in a temporary worktree.
- **Ladders:** this branch's ladder (S1 at `a0ae4e8`) and the `s1-hardening` ladder at `614e116`, which needs `raw.valid is True and raw.trusted is True`. Both gave the same answers.
- **SDK:** c2pa-python 0.38.2 (c2pa-rs 0.91.6).
- **Samples:** the demo sample set, kept outside this repo (sources, licences and sha256 in its manifest). The sha256 of every file matched that manifest.
- **Offline:** the run used `sandbox-exec` with `(deny network*)` and `env -i PATH HOME=<temp> LANG`. A `curl` to contentcredentials.org inside the same sandbox failed (`Could not resolve host`).
- **Trust lists:**
  - official: C2PA Trust List, sha256 `b3949271…af8ae`;
  - interim: `anchors.pem` `db57e50b…7ae5` and `allowed.sha256.txt` `3597b20c…15bb`, both matching the pins.
- **Raw output:** `docs/S2-STAGE3-DEMO.json`. Same run: `pytest` 76 passed, 0 skipped.

| # | File | Expected | Answer | `trusted_by` | C2PA strength | With no list loaded |
|---|---|---|---|---|---|---|
| 01 | Pixel 10 Pro capture, Atlantic sign | signed_camera | signed_camera | official | signed | Valid (`timeStamp.untrusted`) |
| 02 | Pixel 10 Pro capture, Utah sign | signed_camera | signed_camera | official | signed | **Invalid: `signingCredential.expired`** (`timeStamp.untrusted`) |
| 03 | Adobe Firefly landscape | signed_ai | signed_ai | interim | signed | Valid |
| 04 | Adobe Firefly typography | signed_ai | signed_ai | interim | signed | Valid |
| 05 | Photoshop with Firefly retouch | signed_ai | signed_ai | interim | signed | Valid |
| 06 | stripped copy of 01 | cant_tell | cant_tell | n/a (no manifest) | n/a | no manifest |
| 07 | stripped copy of 03 | cant_tell | cant_tell | n/a (no manifest) | n/a | no manifest |
| 08 | 07 plus an IPTC AI label | concerns | concerns | n/a (no manifest) | n/a | no manifest |
| 09 | stripped 04 plus an IPTC AI label | concerns | concerns | n/a (no manifest) | n/a | no manifest |

For 03 to 05 the finding ends: "The signer is on the C2PA interim trust list, which C2PA describes as temporary."
For 01 and 02 it ends: "The signer is on the C2PA Trust List (2026-10-17)."

## Sample 02: why it reads as expired with no list, and as trusted with the official list

**What was observed:** the certificates embedded in the manifest, read with `cryptography`, and the SDK's status codes.

1. **The signing certificate was short-lived.** It is "Pixel Camera", Google LLC, issued by Google C2PA Mobile A 1P ICA G3 L3 and valid from **2025-11-09 to 2026-02-06**. The claim was signed on **2025-12-30**, inside that window. The certificate has since expired. 01's certificate runs from 2026-09-05 to 2026-12-03, so it is still in date, and 01 reads Valid even with no list.
2. **The manifest carries an RFC 3161 time stamp** from the "Google Pixel Time Stamping Authority". Its chain runs through Google C2PA Pixel Time-Stamping ICA G3 to **Google C2PA Root CA G3**.
3. **With no list loaded,** the SDK reports `timeStamp.validated` but also `timeStamp.untrusted`. It then fails the signer with `signingCredential.expired`. That code means the certificate is out of date now. The signing time is not the problem: the same read also reports `claimSignature.insideValidity`.
4. **With the official list loaded,** Google C2PA Root CA G3 is one of its 32 anchors. The same read then reports `timeStamp.trusted`, `signingCredential.trusted` and `claimSignature.insideValidity`, with no failures. State: Trusted.
5. **With only the interim list,** 02 still reads `signingCredential.untrusted` and `signingCredential.expired`. The interim anchors hold no Google root, so the time stamp stays untrusted.

**Inference (consistent with every reading above, not traced in the c2pa-rs source):** the SDK checks the signing certificate at the time-stamped signing time only when the time stamp chains to a trusted anchor. Otherwise it checks against the current time. A short-lived device certificate therefore reads as expired once it lapses, unless a list that anchors its time-stamp authority is loaded.

This is also why the rung reads the official list first. Under the interim list, 02 would be read as invalid.

**Tests:** `tests/test_s2_fixes.py::test_expired_short_lived_cert_trusted_by_official_time_stamp` pins both readings.
