"""Fill in the flyer's QR code, printed web address and map, and optionally print it to PDF.

    python scripts/flyer.py https://parque.barrionegrin.es/ --pdf flyer/flyer.pdf

The map is copied from docs/index.html (drawn by analysis/site_map.py), with its CSS
variables replaced by their light-theme values for print. Needs segno; --pdf needs weasyprint.
"""
import argparse
import re
from pathlib import Path

import segno

ROOT = Path(__file__).resolve().parent.parent
FLYER = ROOT / "flyer" / "flyer.html"
SITE = ROOT / "docs" / "index.html"


def between(html, name, content):
    pattern = re.compile(f"(<!-- {name}:start -->).*?(<!-- {name}:end -->)", re.S)
    if not pattern.search(html):
        raise SystemExit(f"No <!-- {name}:start/end --> markers in {FLYER}")
    return pattern.sub(lambda m: m.group(1) + content + m.group(2), html)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("url", help="address the QR code opens, e.g. https://parque.example.es/")
    ap.add_argument("--pdf", help="also write the flyer as a PDF here")
    a = ap.parse_args()

    qr = segno.make(a.url, error="m")
    svg = qr.svg_inline(scale=1, border=0, dark="#15201a", omitsize=True)
    shown = re.sub(r"^https?://", "", a.url).rstrip("/")

    m = re.search(r"<!-- mapa:start -->(.*?)<!-- mapa:end -->", SITE.read_text(encoding="utf-8"), re.S)
    if not m:
        raise SystemExit(f"No map in {SITE}; run analysis/site_map.py first")
    mapa = re.sub(r"var\(--[\w-]+,\s*([^()]*?)\)", r"\1", m.group(1))  # print: fixed light colours
    mapa = mapa.replace('id="mapa-titulo"', 'id="folleto-mapa-titulo"').replace(
        'aria-labelledby="mapa-titulo"', 'aria-labelledby="folleto-mapa-titulo"')

    html = FLYER.read_text(encoding="utf-8")
    html = between(html, "qr", "\n" + svg + "\n")
    html = between(html, "url", shown)
    html = between(html, "mapa", mapa)
    FLYER.write_text(html, encoding="utf-8")
    print(f"QR -> {a.url} (version {qr.version}, {qr.symbol_size(border=0)[0]} modules)")

    if a.pdf:
        from weasyprint import HTML
        HTML(filename=str(FLYER)).write_pdf(a.pdf)
        print(f"wrote {a.pdf}")


if __name__ == "__main__":
    main()
