"""Generate fixtures for a WMTS tile matrix set family from GetCapabilities.

    python3 tools/fixtures-wmts.py gibs-polar [capabilities.xml ...]

The capabilities document is the oracle: TopLeftCorner, ScaleDenominator
(cell = scale * 0.28e-3 m, per OGC), TileWidth/Height and MatrixWidth/Height.
"""
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS = {"w": "http://www.opengis.net/wmts/1.0", "o": "http://www.opengis.net/ows/1.1"}
GIBS = {"epsg3031": "https://gibs.earthdata.nasa.gov/wmts/epsg3031/best/wmts.cgi?SERVICE=WMTS&REQUEST=GetCapabilities",
        "epsg3413": "https://gibs.earthdata.nasa.gov/wmts/epsg3413/best/wmts.cgi?SERVICE=WMTS&REQUEST=GetCapabilities"}


def load(src):
    if src.startswith("http"):
        cache = Path.home() / ".cache" / "crsgrid" / re.sub(r"[^\w.]+", "_", src)
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not cache.exists():
            urllib.request.urlretrieve(src, cache)
        src = cache
    return ET.parse(src).getroot()


def main(srcs):
    cases, seen = [], {}
    for inst, url in GIBS.items():
        root = load(srcs.get(inst, url))
        for tms in root.find("w:Contents", NS).findall("w:TileMatrixSet", NS):
            name = tms.find("o:Identifier", NS).text
            for tm in tms.findall("w:TileMatrix", NS):
                z = tm.find("o:Identifier", NS).text
                cell = float(tm.find("w:ScaleDenominator", NS).text) * 0.28e-3
                x0, y0 = map(float, tm.find("w:TopLeftCorner", NS).text.split())
                tw = int(tm.find("w:TileWidth", NS).text)
                mw, mh = int(tm.find("w:MatrixWidth", NS).text), int(tm.find("w:MatrixHeight", NS).text)
                seen.setdefault((inst, z), set()).add(name)
                if len(seen[(inst, z)]) > 1:
                    continue  # same matrix in another set
                size = tw * cell
                for row, col in {(0, 0), (mh - 1, mw - 1), (mh // 2, mw // 2 - 1)}:
                    xmin, ymax = x0 + col * size, y0 - row * size
                    cases.append({"op": "tile_extent", "input": {"id": f"{inst}/{z}/{row}/{col}"},
                                  "expected": {"extent": [round(xmin, 6), round(xmin + size, 6), round(ymax - size, 6), round(ymax, 6)],
                                               "dim": [tw, tw]},
                                  "tolerance": {"extent": 1e-6}, "source": f"{inst} TileMatrixSet {name}, TileMatrix {z}"})
                cases.append({"op": "parse", "input": {"id": f"{inst}/{z}/{mh}/0"}, "expected": {"error": "outside_domain"},
                              "source": f"{inst} TileMatrix {z}: MatrixHeight {mh}"})
    sets = {f"{i}/{z}": sorted(n) for (i, z), n in seen.items()}
    out = {"spec": "examples/gibs-polar.json", "generator": "tools/fixtures-wmts.py gibs-polar",
           "oracle": {"package": "NASA GIBS WMTS GetCapabilities (epsg3031, epsg3413 'best'), retrieved 2026-10-29",
                      "proj": "n/a (no transforms)", "matrix_sets": sets},
           "cases": cases}
    (ROOT / "fixtures" / "gibs-polar.json").write_text(json.dumps(out, indent=2) + "\n")
    print(len(cases), "cases")


if __name__ == "__main__":
    a = sys.argv[2:]
    main(dict(zip(("epsg3031", "epsg3413"), a)))
