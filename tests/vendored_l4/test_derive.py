#!/usr/bin/env python3
"""Self-test of derive.py on synthetic images made here (no real image, no network).  Run: python3 scripts/test_derive.py"""
import io, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from PIL import Image
import derive


def synth(size, fmt="JPEG", exif_orient=None, icc=False, mode="RGB"):
    im = Image.new(mode, size)
    px = im.load()
    for x in range(0, size[0], 7):
        for y in range(0, size[1], 7): px[x, y] = (x % 256, y % 256, (x + y) % 256) if mode == "RGB" else (x % 256)
    kw = {}
    if exif_orient:
        e = Image.Exif(); e[0x0112] = exif_orient; e[0x010F] = "TestCam"; kw["exif"] = e
    if icc:
        from PIL import ImageCms
        kw["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    b = io.BytesIO(); im.save(b, fmt, **kw); return b.getvalue()


class T(unittest.TestCase):
    def test_shrinks_long_side_only_and_keeps_aspect(self):
        out, rec = derive.derive(synth((4000, 3000)), "format_matched"); self.assertEqual(rec["out_size"], [1536, 1152]); self.assertTrue(rec["resized"])
        out, rec = derive.derive(synth((4000, 3000)), "compressed_q70_1080"); self.assertEqual(rec["out_size"], [1080, 810])
    def test_never_upscales(self):
        for arm in ("format_matched", "compressed_q70_1080"):
            out, rec = derive.derive(synth((512, 512), "PNG"), arm); self.assertEqual(rec["out_size"], [512, 512]); self.assertFalse(rec["resized"])
    def test_metadata_removed_and_orientation_applied(self):
        src = synth((600, 400), exif_orient=6, icc=True)
        out, rec = derive.derive(src, "format_matched"); im = Image.open(io.BytesIO(out))
        self.assertEqual(im.size, (400, 600))                      # orientation 6 turned the pixels
        for k in ("exif", "icc_profile", "comment", "xmp"): self.assertNotIn(k, im.info)
        self.assertNotIn(b"TestCam", out)
    def test_deterministic(self):
        s = synth((2000, 1500)); self.assertEqual(derive.derive(s, "compressed_q70_1080")[0], derive.derive(s, "compressed_q70_1080")[0])
    def test_encoder_settings(self):
        out, _ = derive.derive(synth((800, 600)), "format_matched"); im = Image.open(io.BytesIO(out))
        self.assertEqual(im.format, "JPEG"); self.assertFalse(im.info.get("progressive")); self.assertEqual(im.layer[0][1:3], (2, 2))   # 4:2:0
    def test_quality_differs_by_arm(self):
        s = synth((1600, 1200)); self.assertGreater(len(derive.derive(s, "format_matched")[0]), len(derive.derive(s, "compressed_q70_1080")[0]))
    def test_size_matched_square_512(self):
        out, rec = derive.derive(synth((3000, 2000)), "size_matched_512"); self.assertEqual(rec["out_size"], [512, 512])
        out, rec = derive.derive(synth((512, 700), "PNG"), "size_matched_512"); self.assertEqual(rec["out_size"], [512, 512])
    def test_refuses_small_for_size_matched(self):
        with self.assertRaises(ValueError): derive.derive(synth((400, 600)), "size_matched_512")
    def test_png_to_jpeg_and_grey(self):
        out, rec = derive.derive(synth((600, 600), "PNG"), "format_matched"); self.assertEqual(rec["source_format"], "PNG")
        out, rec = derive.derive(synth((600, 600), mode="L"), "format_matched"); self.assertEqual(Image.open(io.BytesIO(out)).mode, "RGB")
    def test_transparent_png_refused(self):
        im = Image.new("RGBA", (600, 600), (0, 0, 0, 0)); b = io.BytesIO(); im.save(b, "PNG")
        with self.assertRaises(ValueError): derive.derive(b.getvalue(), "format_matched")


if __name__ == "__main__": unittest.main(verbosity=1)
