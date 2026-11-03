# Watermark rung: what it does, and how robust our mark is (W8, DAT-795)

## What it is

`secondlook/rungs/watermark.py` looks for **Second Look's own invisible watermark**, and nothing else. `secondlook mark <in> <out>` adds that mark to a copy of an image for the demo.

- **Model:** Meta's Video Seal 1.0 image model, `y_256b_img`, in its published TorchScript form. It comes from [facebookresearch/videoseal](https://github.com/facebookresearch/videoseal) (docs/torchscript.md), checked at commit `870ca7fb` (2 Jul 2026).
- **Licence:** MIT. The repo's LICENSE is MIT, and its README says "The model is licensed under an MIT license".
- **Weights:** `https://dl.fbaipublicfiles.com/videoseal/y_256b_img.jit`, 228,524,815 bytes, last modified 2 Apr 2025. The sha256 is pinned in the rung: `5c7a4581c36fc6090aafdcfb3999123bae5172a4847f22e2da4e7fd1a39d1e1b`. A file with any other hash is refused. To fetch it: `python -m secondlook.rungs.watermark --fetch`.
- **Runtime:** torch only (no `videoseal` package), on MPS on the M5.
- **PixelSeal** is in the same repo, but it is published only as a full checkpoint, which needs the `videoseal` package and its dependencies (opencv, lpips, transformers, and timm pinned to 0.9.16, among others). The TorchScript Video Seal model needs none of these, so we used it, as the job allows.

## How it decides

- `mark` embeds one fixed 256-bit message: the sha256 of a key. The demo key is in the source code. `SECONDLOOK_WATERMARK_KEY` sets another key, and the card shows only a 12-character key id.
- `check` reads 256 bits back and counts how many match our message.
  - With no mark, each bit is close to a coin toss, so the count sits near 128.
  - We report **present** at **175 or more**. This cut-off was fixed in the code before any measurement. For fair, independent coin-toss bits, P(175 or more) = 2.08e-9.
- **Present:** one card with strength `signal`.
- **Absent:** one card with strength `none`. Its `does_not_prove` says: "The absence of a mark says nothing about how the image was made."
- **The ladder ignores the watermark card.** It never changes the answer (signed_ai, signed_camera, concerns or cant_tell). A test covers this.

## What it never claims

- **It does not detect Meta AI images,** or any other company's watermark. It looks only for our own message. Every card says so.
- **A present mark does not mean Second Look made the image.** The demo key is public and the model is open, so anyone can embed the same message. It means only that someone embedded our message into this image or an earlier copy of it.
- **The absence of a mark says nothing about how the image was made.**

## Robustness run (28 Oct 2026, M5, MPS)

**Inputs.** The 9 demo files, kept outside this repo (`scripts/fetch_samples.sh` and `docs/LICENSES.md` give their sources and licences). Five are independent originals and four are copies derived from them, so treat this as **5 independent images**. That's a small demo check, not a measured rate.

**Method.** `scripts/watermark_robustness.py`. For each file:
1. Read the matches on the unmarked image.
2. Embed the mark and save it as PNG.
3. Apply each change in the table, then count the matches.

| Image | Change | n | Min matched | Max matched | Present (175+) |
|---|---|---|---|---|---|
| unmarked | original | 9 | 96 | 135 | 0/9 |
| unmarked | JPEG q70 | 9 | 115 | 136 | 0/9 |
| unmarked | resize, long side 1080 | 9 | 100 | 137 | 0/9 |
| marked | as saved (PNG) | 9 | 255 | 256 | 9/9 |
| marked | JPEG q90 | 9 | 255 | 256 | 9/9 |
| marked | **JPEG q70** | 9 | 255 | 256 | 9/9 |
| marked | JPEG q50 | 9 | 254 | 256 | 9/9 |
| marked | **resize, long side 1080** | 9 | 255 | 256 | 9/9 |
| marked | resize to 1080, then JPEG q70 | 9 | 255 | 256 | 9/9 |
| marked | resize, long side 512 | 9 | 255 | 256 | 9/9 |
| marked | resize, long side 256 | 9 | 255 | 256 | 9/9 |
| marked | rotate 90 degrees | 9 | 255 | 256 | 9/9 |
| marked | **centre crop to 50%** | 9 | 124 | 153 | **0/9** |

**What this shows**
- **The mark survives JPEG q70 and resizing,** including down to 256 px on the long side, with almost every bit intact. That's the re-encode a social platform does.
- **Unmarked images stay far below the cut-off.** The highest count was 137, against a cut-off of 175.
- **Cropping to the centre 50% removes the mark** in every case. Those images then read as "not found", which is the reason an absent mark proves nothing.

**Speed** was not measured: the Mac was busy with other jobs during the run, and the first call on MPS includes warm-up.

**What we did not test:** screenshots, heavy filters, generative edits, mirroring, or a deliberate removal attack. The test suite repeats the JPEG q70 and resize checks on a synthetic image whenever the weights are present (`tests/test_watermark.py`, the `test_real_*` tests).

**Raw output** of this run: `docs/watermark-robustness-28oct.jsonl` (117 lines).

**To rerun:** `PYTHONPATH=. .venv/bin/python scripts/watermark_robustness.py <images...>`
