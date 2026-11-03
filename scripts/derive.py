#!/usr/bin/env python3
"""The frozen preprocessing recipe R1 (PROTOCOL.md part A) as code.  One function makes every derived arm from an ORIGINAL file (never from another arm).

  derive.py --arm format_matched|compressed_q70_1080|size_matched_512  in.jpg|in.png  out.jpg

Needs Pillow (version recorded with --env).  No network, no model.  Orientation, colour, resize and encoder settings are fixed here; do not pass options.
Order of operations: decode -> orientation -> colour (sRGB, no alpha) -> crop (size_matched_512 only) -> resize (shrink only) -> JPEG encode with no metadata.
"""
import argparse, hashlib, io, json, sys
from PIL import Image, ImageCms, ImageOps, features

ARMS = {  # arm: (max long side, or fixed square side for the diagnostic arm, JPEG quality)
    "format_matched": (1536, 90),
    "compressed_q70_1080": (1080, 70),
    "size_matched_512": (512, 90),    # diagnostic only, not gated: centre square crop, then shrink to 512 x 512
}
SRGB = ImageCms.createProfile("sRGB")
META_KEYS = ("exif", "icc_profile", "comment", "xmp", "photoshop", "adobe", "dpi")


def env():
    return {"python": sys.version.split()[0], "pillow": Image.__version__, "libjpeg": features.version("jpg"), "zlib": features.version("zlib")}


def decode_rgb(data: bytes):
    """The decoded ORIGINAL as R1 reads it: orientation applied, colour to sRGB, no alpha, 8-bit RGB (no crop, no resize).
    Returns (image, source_format, source_size, had_icc_profile).  derive() and the screening code (screen.py) both start here, so they see the same pixels."""
    im = Image.open(io.BytesIO(data)); fmt = im.format; im.load()
    src = im.size
    im = ImageOps.exif_transpose(im)                                  # orientation: applied to pixels, then the tag is gone
    prof = im.info.get("icc_profile")
    if prof:                                                          # colour: convert to sRGB if the file carries another profile
        try:
            sp = ImageCms.ImageCmsProfile(io.BytesIO(prof))
            im = ImageCms.profileToProfile(im.convert("RGB") if im.mode not in ("RGB", "CMYK") else im, sp, SRGB, renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, outputMode="RGB")
        except ImageCms.PyCMSError:
            pass                                                      # unreadable profile: treated as sRGB; the caller logs icc=unreadable via info
    if im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info:
        a = im.convert("RGBA").getchannel("A")
        if a.getextrema()[0] != 255: raise ValueError("non-opaque alpha: refused, not composited")
    im = im.convert("RGB")                                            # grey, palette, CMYK -> RGB
    return im, fmt, src, bool(prof)


def derive(data: bytes, arm: str):
    long_side, q = ARMS[arm]
    im, fmt, src, prof = decode_rgb(data)
    w, h = im.size
    if arm == "size_matched_512":
        s = min(w, h)
        if s < 512: raise ValueError("shorter side under 512: refused, never upscaled")
        im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)); w = h = s
    long = max(w, h)
    resized = False
    if arm == "size_matched_512":
        if w > 512: im = im.resize((512, 512), Image.Resampling.LANCZOS); resized = True
    elif long > long_side:                                            # shrink only; aspect kept; never crop, pad or upscale
        sc = long_side / long
        im = im.resize((max(1, round(w * sc)), max(1, round(h * sc))), Image.Resampling.LANCZOS); resized = True
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q, subsampling=2, optimize=False, progressive=False)   # 4:2:0, libjpeg standard tables, no exif/icc/comment passed
    out = buf.getvalue()
    chk = Image.open(io.BytesIO(out))
    bad = [k for k in META_KEYS if k in chk.info and k != "dpi"]
    if bad or chk.format != "JPEG" or chk.size != im.size: raise ValueError(f"derived file fails the metadata/format check: {bad} {chk.format} {chk.size}")
    return out, {"arm": arm, "source_format": fmt, "source_size": list(src), "out_size": list(im.size), "resized": resized, "out_sha256": hashlib.sha256(out).hexdigest(), "icc_source": prof}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--arm", choices=sorted(ARMS)); ap.add_argument("--env", action="store_true"); ap.add_argument("src", nargs="?"); ap.add_argument("dst", nargs="?")
    a = ap.parse_args()
    if a.env: print(json.dumps(env())); sys.exit()
    out, rec = derive(open(a.src, "rb").read(), a.arm); open(a.dst, "wb").write(out); print(json.dumps(rec))
