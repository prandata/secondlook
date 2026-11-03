# C2PA trust lists used by the C2PA rung

| File | What | Source | Date | sha256 |
|---|---|---|---|---|
| `C2PA-TRUST-LIST.pem` | The C2PA Trust List: 32 CA certificates of conforming signers (among them Google, Adobe, DigiCert, SSL.com, Xiaomi, vivo, Huawei) | `https://raw.githubusercontent.com/c2pa-org/conformance-public/47009eedfe6721edd0ce3e69b89c1101cd6f03e5/trust-list/C2PA-TRUST-LIST.pem` | commit 47009ee, 2026-10-17 ("Automated Sync: Update trust lists"); fetched 2026-10-28 | `b3949271a07b007e92ef1d18050b55388f5e0d2686a16fd864176c48421af8ae` |
| `eku.cfg` | Extended key usages a signing certificate may carry | the EKU list in `https://contentcredentials.org/trust/store.cfg` (fetched 2026-10-28), minus timeStamping and OCSPSigning, which do not sign claims | 2026-10-28 | `63c994ff9148b15e6401751ecbcb7807912300856adf56e2e396435ed47f0706` |

Licence: c2pa-org/conformance-public is CC BY 4.0 (GitHub repository licence).
The PEM is copied unmodified. The C2PA Trust List is published by the C2PA
(https://c2pa.org/conformance); the repo README says the trust lists live in
its `trust-list` directory.

## Interim list (fetched, not committed)

| File | What | Source | Date | sha256 |
|---|---|---|---|---|
| `interim/anchors.pem` | 27 root certificates of the C2PA interim trust list (among them Adobe, Leica, Microsoft) | `https://contentcredentials.org/trust/anchors.pem` | fetched 2026-10-28; the file states no date | `db57e50b9bd4e4c786cbb91f38fd85e2af6a9d5412438a8cddcc42688df07ae5` |
| `interim/allowed.sha256.txt` | SHA-256 hashes of the end-entity certificates in `https://contentcredentials.org/trust/allowed.pem` | `https://contentcredentials.org/trust/allowed.sha256.txt` | fetched 2026-10-28 | `3597b20c9c20a0daadc7fab0d8c63534bd333ec6a715ef7117c7a4588caa15bb` |

`scripts/fetch_trust.sh` fetches both into `secondlook/trust/interim/` (gitignored)
and stops if a hash differs. The files state no licence, so they are not
committed. The rung loads them only when both hashes match the pins in
`INTERIM_LIST` (`secondlook/rungs/c2pa.py`), and records the list as not loaded
otherwise.

What the files say about themselves (quoted from the fetched copies):
- `anchors.pem`: "This interim trust list is now frozen. C2PA has published an
  official trust list, and new anchor certificates should be added to that list.
  NOTE: Content Credentials are still valid which were signed using certificates
  chaining back to root certs on this list. Validators can still refer to this
  trust list, but should distinguish between Content Credentials signed with certs
  tracing back to these and those signed with certs tracing back to root certs on
  the official C2PA trust list."
- `allowed.sha256.txt`: "The C2PA intends to publish an official C2PA Public
  Trust List. Until that time, this temporary known certificate list for use by
  the verifier at https://contentcredentials.org/verify. The list is subject to
  change, and will be deprecated."

That is why the card names the list, and for the interim list adds "which C2PA
describes as temporary". The files are served from contentcredentials.org and do
not name their author; "C2PA" in the card follows the wording the project lead ruled
for DAT-847.

Not loaded: the C2PA TSA Trust List (time-stamp authorities). The Google Pixel
time-stamp authority chains to Google C2PA Root CA G3, which is on the official
list, so Pixel time stamps are trusted without it.

Refresh: replace the PEM from a newer conformance-public commit, update the
commit, date and sha256 above and `TRUST_LIST` in `secondlook/rungs/c2pa.py`,
then run the tests.
