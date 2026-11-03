"""Print a markdown licence table for a pinned package list (name==version lines).

  licenses-dump.py installed FREEZE_FILE   read each licence from the installed package metadata
  licenses-dump.py pypi FREEZE_FILE        read each licence from https://pypi.org/pypi/<name>/<version>/json

Licence text comes from the package's own metadata: License-Expression first, then
the License field if it is short, then the Trove "License ::" classifiers. A package that
declares nothing prints "(none declared)" and is checked by hand.
"""
import importlib.metadata as md
import json
import sys
import urllib.request


def from_installed(name):
    m = md.metadata(name)
    return (m.get("License-Expression") or "", m.get("License") or "", m.get_all("Classifier") or [])


def from_pypi(name, version):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
        info = json.load(r)["info"]
    return (info.get("license_expression") or "", info.get("license") or "", info.get("classifiers") or [])


def licence(expr, lic, classifiers):
    if expr:
        return expr
    tro = [c.split("::")[-1].strip() for c in classifiers if c.startswith("License ::")]
    if lic and len(lic) <= 60 and "\n" not in lic:
        return lic if not tro else f"{lic} (classifiers: {', '.join(tro)})"
    return ", ".join(tro) or "(none declared)"


def main():
    mode, path = sys.argv[1], sys.argv[2]
    rows = []
    for line in open(path):
        line = line.strip()
        if "==" not in line or line.startswith("#"):
            continue
        name, version = line.split("==")[:2]
        version = version.split()[0]
        if name == "secondlook":
            continue
        rows.append((name, version, licence(*(from_installed(name) if mode == "installed" else from_pypi(name, version)))))
    print("| Package | Version | Licence (from package metadata) |")
    print("|---|---|---|")
    for n, v, l in sorted(rows, key=lambda r: r[0].lower()):
        print(f"| `{n}` | {v} | {l} |")


main()
