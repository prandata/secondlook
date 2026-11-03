"""Make the four derived demo samples (06 to 09) from the downloaded originals 01, 03 and 04.

Usage: derive_samples.py <samples dir>   (run by scripts/fetch_samples.sh, which checks every sha256)

Stripped copy: decode and re-encode at JPEG quality 90, keeping only the ICC colour profile, the
way many upload pipelines do. No EXIF, no XMP, no C2PA survive.
IPTC-labelled copy: the stripped re-encode plus one XMP packet with an IPTC AI label. The label text
is copied from the source's own signed C2PA manifest (digitalSourceType) and its Wikimedia Commons
record (author line "Adobe Firefly Image 3"); nothing else is added.
The output bytes depend on Pillow, so fetch_samples.sh pins the Pillow version (requirements/demo.lock).
"""
import os
import sys

from PIL import Image

XMP = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/"
 Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"{extra}/>
</rdf:RDF></x:xmpmeta>
<?xpacket end="w"?>"""


def strip(d, src, dst, xmp=None):
    with Image.open(os.path.join(d, src)) as im:
        icc = im.info.get("icc_profile")
        kw = {"quality": 90, "icc_profile": icc} if icc else {"quality": 90}
        if xmp:
            kw["xmp"] = xmp.encode("utf-8")
        im.convert("RGB").save(os.path.join(d, dst), "JPEG", **kw)


def main(d):
    strip(d, "01-camera-pixel10pro-atlantic-sign.jpg", "06-stripped-pixel10pro-atlantic-sign.jpg")
    strip(d, "03-ai-firefly-mountain-landscape.jpg", "07-stripped-firefly-mountain-landscape.jpg")
    strip(d, "03-ai-firefly-mountain-landscape.jpg", "08-iptc-ai-label-firefly-mountain-landscape.jpg",
          XMP.format(extra=""))
    strip(d, "04-ai-firefly-typography-logo.jpg", "09-iptc-ai-label-firefly-typography-logo.jpg",
          XMP.format(extra='\n Iptc4xmpExt:AISystemUsed="Adobe Firefly" Iptc4xmpExt:AISystemVersionUsed="Image 3"'))


if __name__ == "__main__":
    main(sys.argv[1])
