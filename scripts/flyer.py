"""Fill in the flyers' QR code, printed web address and map, and optionally print them to PDF.

    python scripts/flyer.py https://parque.barrionegrin.es/ --pdf

Fills flyer/flyer.html (A4, for noticeboards) and flyer/flyer-a5.html (A5, for letterboxes; no
map). With --pdf it writes flyer/flyer.pdf, flyer/flyer-a5.pdf and flyer/flyer-a5-2up.pdf (two
A5 flyers on one A4 sheet, to print and cut in half).

The map is copied from docs/index.html (drawn by analysis/site_map.py), with its CSS
variables replaced by their light-theme values for print. Needs segno; --pdf needs weasyprint.
"""
import argparse
import re
from pathlib import Path

import segno

ROOT = Path(__file__).resolve().parent.parent
FLYER = ROOT / "flyer" / "flyer.html"
FLYER_A5 = ROOT / "flyer" / "flyer-a5.html"
SITE = ROOT / "docs" / "index.html"


def between(html, name, content, path, required=True):
    pattern = re.compile(f"(<!-- {name}:start -->).*?(<!-- {name}:end -->)", re.S)
    if not pattern.search(html):
        if required:
            raise SystemExit(f"No <!-- {name}:start/end --> markers in {path}")
        return html
    return pattern.sub(lambda m: m.group(1) + content + m.group(2), html)


def two_up(html):
    """The A5 flyer twice, side by side on an A4 landscape sheet."""
    html = html.replace("@page{size:A5;margin:0}",
                        "@page{size:A4 landscape;margin:0}\n.page{float:left}")
    body = re.search(r'(<div class="page">.*</div>)\s*</body>', html, re.S).group(1)
    return html.replace(body, body + "\n" + body)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("url", help="address the QR code opens, e.g. https://parque.example.es/")
    ap.add_argument("--pdf", action="store_true", help="also print the flyers to PDF")
    a = ap.parse_args()

    qr = segno.make(a.url, error="m")
    svg = qr.svg_inline(scale=1, border=0, dark="#000000", omitsize=True)
    shown = re.sub(r"^https?://", "", a.url).rstrip("/")

    m = re.search(r"<!-- mapa:start -->(.*?)<!-- mapa:end -->", SITE.read_text(encoding="utf-8"), re.S)
    if not m:
        raise SystemExit(f"No map in {SITE}; run analysis/site_map.py first")
    mapa = re.sub(r"var\(--[\w-]+,\s*([^()]*?)\)", r"\1", m.group(1))  # print: fixed light colours
    mapa = mapa.replace('id="mapa-titulo"', 'id="folleto-mapa-titulo"').replace(
        'aria-labelledby="mapa-titulo"', 'aria-labelledby="folleto-mapa-titulo"')

    for path in (FLYER, FLYER_A5):
        html = path.read_text(encoding="utf-8")
        html = between(html, "qr", "\n" + svg + "\n", path)
        html = between(html, "url", shown, path)
        html = between(html, "mapa", mapa, path, required=False)
        path.write_text(html, encoding="utf-8")
    print(f"QR -> {a.url} (version {qr.version}, {qr.symbol_size(border=0)[0]} modules)")

    if a.pdf:
        from weasyprint import HTML
        out = ROOT / "flyer"
        HTML(filename=str(FLYER)).write_pdf(out / "flyer.pdf")
        HTML(filename=str(FLYER_A5)).write_pdf(out / "flyer-a5.pdf")
        HTML(string=two_up(FLYER_A5.read_text(encoding="utf-8")),
             base_url=str(FLYER_A5)).write_pdf(out / "flyer-a5-2up.pdf")
        print("wrote flyer/flyer.pdf, flyer/flyer-a5.pdf, flyer/flyer-a5-2up.pdf")


if __name__ == "__main__":
    main()
