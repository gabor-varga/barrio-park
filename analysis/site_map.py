"""Simplified orientation map for the campaign website, as inline SVG (Spanish or English labels).

Reads the layers.gpkg of a green_access.py run (it needs the streets, footprints, buildings,
pocket, golf and proposals layers) and draws the pocket, the golf course, areas A and B, the
streets and a landmark. Colours come from CSS variables (--map-*) with light-theme fallbacks, so
the page can switch them for dark mode.

    python analysis/site_map.py green_access_out/layers.gpkg --html docs/index.html
    python analysis/site_map.py green_access_out/layers.gpkg --html docs/en/index.html --lang en
    python analysis/site_map.py green_access_out/layers.gpkg --out mapa.svg

--html replaces everything between <!-- mapa:start --> and <!-- mapa:end --> in that page.
"""
import argparse
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, box as rect
from shapely.ops import linemerge, substring, unary_union

WIDTH = 600  # SVG user units; text sizes below are in the same units
HOSPITAL = Point(456332, 3110645)  # Hospital Dr. Negrín (OSM, via Nominatim), EPSG:32628
MAIN_ROADS = {"trunk", "primary", "secondary", "tertiary"}
START, END = "<!-- mapa:start -->", "<!-- mapa:end -->"

# Every word on the map, per page language (--lang). Street names stay in Spanish.
TEXT = {
    "es": {
        "A": ("Solar abandonado", "0,38 ha"),
        "B": ("Franja del campo", "0,55 ha, el 6% del golf"),
        "golf": ("Las Palmeras Golf", "campo municipal"),
        "homes": ("Nuestras casas", "816 viviendas", "2.000 vecinos"),
        "hospital": "Hospital Dr. Negrín",
        "north": "N",
        "title": "Mapa: las casas de la Carretera de Chile y la Calle Doctor Alfonso Chiscano Díaz, "
                 "el campo de golf Las Palmeras, el solar abandonado (A) y la franja del campo (B) "
                 "que pedimos convertir en parque.",
    },
    "en": {
        "A": ("Abandoned plot", "0.38 ha"),
        "B": ("Strip of the course", "0.55 ha, 6% of the golf"),
        "golf": ("Las Palmeras Golf", "municipal course"),
        "homes": ("Our homes", "816 homes", "2,000 residents"),
        "hospital": "Hospital Dr. Negrín",
        "north": "N",
        "title": "Map: the homes on Carretera de Chile and Calle Doctor Alfonso Chiscano Díaz, "
                 "Las Palmeras golf course, and the abandoned plot (A) and strip of the course (B) "
                 "we ask to turn into a park.",
    },
}

# Street names drawn along the street: (OSM name, text, where along the street 0..1).
STREET_LABELS = [
    ("Carretera de Chile", "Carretera de Chile", 0.5),
    ("Calle Doctor Alfonso Chiscano Díaz", "C/ Dr. Alfonso Chiscano Díaz", 0.5),
]

STYLE = """
.mapa{font-family:var(--body,'Archivo',Arial,sans-serif)}
.mapa .bg{fill:var(--map-bg,#f3f0e8)}
.mapa .bldg{fill:var(--map-bldg,#dcd6ca)}
.mapa .home{fill:var(--map-home,#c2410c)}
.mapa .new{fill:var(--map-new,#f2c4a8);stroke:var(--map-home,#c2410c);stroke-width:2;stroke-dasharray:5 4}
.mapa .golf{fill:var(--map-golf,#e2d4a3)}
.mapa .park{fill:var(--map-park,#0b5a39);stroke:var(--map-park-edge,#f5c518);stroke-width:3;stroke-linejoin:round}
.mapa .casing,.mapa .road{fill:none;stroke-linecap:round;stroke-linejoin:round}
.mapa .casing{stroke:var(--map-casing,#cdc5b4)}
.mapa .road{stroke:var(--map-road,#ffffff)}
.mapa .minor.casing{stroke-width:5}.mapa .minor.road{stroke-width:3}
.mapa .main.casing{stroke-width:10}.mapa .main.road{stroke-width:7.5}
.mapa text{fill:var(--map-text,#15201a)}
.mapa text.halo{fill:var(--map-halo,#f3f0e8);stroke:var(--map-halo,#f3f0e8);stroke-width:4px;stroke-linejoin:round}
.mapa .street{font-size:18px;font-weight:700;letter-spacing:.02em}
.mapa .place{font-size:19px;font-weight:700}
.mapa .note{font-size:16px;font-weight:400;fill:var(--map-muted,#4a554e)}
.mapa .home-label{fill:var(--map-home-text,#9a3412)}
.mapa .letter{font-size:36px;font-weight:800;fill:#ffffff;stroke:none}
.mapa .golf-label{font-style:italic;fill:var(--map-golf-text,#6b5a1e)}
.mapa .bar{stroke:var(--map-text,#15201a);stroke-width:3}
.mapa .arrow{fill:var(--map-text,#15201a)}
"""


def project(box, width=WIDTH):
    x0, y0, x1, y1 = box
    s = width / (x1 - x0)
    return s, (lambda x, y: (round((x - x0) * s, 1), round((y1 - y) * s, 1)))


def path_d(geom, tf):
    """SVG path data for a (multi)polygon or (multi)line."""
    if geom.is_empty:
        return ""
    parts = getattr(geom, "geoms", [geom])
    out = []
    for g in parts:
        if g.geom_type == "Polygon":
            rings = [g.exterior] + list(g.interiors)
            for r in rings:
                pts = [tf(x, y) for x, y in r.coords]
                out.append("M" + " L".join(f"{x},{y}" for x, y in pts) + "Z")
        elif g.geom_type == "LineString":
            pts = [tf(x, y) for x, y in g.coords]
            out.append("M" + " L".join(f"{x},{y}" for x, y in pts))
        else:  # GeometryCollection from a clip
            out.append(path_d(g, tf))
    return "".join(out)


def text(x, y, s, cls, anchor="middle", extra="", halo=True):
    """A label, with a copy underneath in the background colour as a halo (drawn as its own
    element rather than with paint-order, which print renderers ignore)."""
    t = f'<text class="{{}}" x="{x}" y="{y}" text-anchor="{anchor}"{extra}>{s}</text>'
    return (t.format(cls + " halo") if halo else "") + t.format(cls)


def street_label(streets, frame, name, label, where, tf, length_m, idx, avoid):
    """The street's name, centred on a straight stretch of it and turned to follow it.

    Takes the straightest stretch of the street long enough for the text, clear of `avoid`,
    preferring stretches near `where` (0..1 along the street).
    """
    s = streets[streets["name"] == name]
    if s.empty:
        return "", ""
    merged = linemerge(unary_union(list(s.geometry)).intersection(frame))
    line = max(getattr(merged, "geoms", [merged]), key=lambda g: g.length)
    if line.geom_type != "LineString" or line.length < length_m:
        return "", ""
    best = None
    for i in range(1, 40):
        f = i / 40
        a = f * line.length - length_m / 2
        if a < 0 or a + length_m > line.length:
            continue
        piece = substring(line, a, a + length_m)
        chord = LineString([piece.coords[0], piece.coords[-1]])
        score = chord.length / piece.length - 0.3 * abs(f - where)
        if chord.buffer(10).intersects(avoid):
            score -= 1
        if best is None or score > best[0]:
            best = (score, chord)
    chord = best[1]
    (ax, ay), (bx, by) = chord.coords[0], chord.coords[-1]
    steep = abs(by - ay) > abs(bx - ax)
    if (steep and ay > by) or (not steep and ax > bx):  # steep: read upwards; else left to right
        chord = LineString(chord.coords[::-1])
    # Plain rotated text, not textPath: print renderers (WeasyPrint, for the flyer) handle it.
    (x0, y0), (x1, y1) = tf(*chord.coords[0]), tf(*chord.coords[-1])
    angle = round(np.degrees(np.arctan2(y1 - y0, x1 - x0)), 1)
    mx, my = round((x0 + x1) / 2, 1), round((y0 + y1) / 2, 1)
    return "", text(mx, my, label, "street", extra=f' dy="0.35em" transform="rotate({angle} {mx} {my})"')


def build_svg(gpkg, lang="es"):
    words = TEXT[lang]
    rd = lambda layer: gpd.read_file(gpkg, layer=layer)  # noqa: E731
    hull = rd("pocket").geometry.iloc[0]
    prop = rd("proposals")
    golf = unary_union(list(rd("golf").geometry))
    streets, fp, bld = rd("streets"), rd("footprints"), rd("buildings")
    for c in ("name", "highway"):
        streets[c] = streets[c].astype("string")

    x0, y0, x1, y1 = unary_union([hull, *prop.geometry, HOSPITAL]).bounds
    box = (x0 - 205, y0 - 45, x1 + 250, y1 + 55)
    frame = rect(*box)
    s, tf = project(box)
    height = round((box[3] - box[1]) * s)

    fp = fp[fp.intersects(frame)]
    homes_pts = bld[bld["in_pocket"].astype(bool)]
    is_home = fp.geometry.apply(lambda g: homes_pts.intersects(g).any())
    streets = streets[streets.intersects(frame)]
    main = streets["highway"].isin(MAIN_ROADS)

    def clip(g):
        return g.intersection(frame).simplify(0.4)

    lines = []
    lines.append(f'<rect class="bg" width="{WIDTH}" height="{height}"/>')
    lines.append(f'<path class="golf" d="{path_d(clip(golf), tf)}"/>')
    lines.append(f'<path class="bldg" fill-rule="evenodd" '
                 f'd="{path_d(clip(unary_union(list(fp[~is_home].geometry))), tf)}"/>')
    for cls, sel in (("minor", ~main), ("main", main)):
        geom = clip(unary_union(list(streets[sel].geometry)))
        d = path_d(geom, tf)
        lines.append(f'<path class="{cls} casing" d="{d}"/>')
        lines.append(f'<path class="{cls} road" d="{d}"/>')
    lines.append(f'<path class="home" fill-rule="evenodd" '
                 f'd="{path_d(clip(unary_union(list(fp[is_home].geometry))), tf)}"/>')
    try:  # buildings not yet in the Catastro (green_access.py --new-buildings)
        for g in rd("new_buildings").geometry:
            lines.append(f'<path class="new" d="{path_d(clip(g), tf)}"/>')
    except Exception:
        pass
    for _, r in prop.iterrows():
        lines.append(f'<path class="park" d="{path_d(clip(r.geometry), tf)}"/>')

    # Labels
    defs, labels = [], []
    parks = unary_union(list(prop.geometry))
    for i, (name, label, where) in enumerate(STREET_LABELS):
        length_m = len(label) * 18 * 0.6 / s + 10
        p, t = street_label(streets, frame, name, label, where, tf, length_m, i, parks)
        defs.append(p)
        labels.append(t)

    letters = {}
    for _, r in prop.iterrows():
        letter = str(r["label"]).split(":")[0]
        c = r.geometry.representative_point()
        cx, cy = tf(c.x, c.y)
        labels.append(text(cx, cy + 12, letter, "letter", halo=False))
        letters[letter] = r.geometry
    callouts = {k: words[k] for k in ("A", "B")}
    for letter, geom in letters.items():
        if letter not in callouts:
            continue
        gx0, gy0, gx1, gy1 = geom.bounds
        lx, ly = tf(gx1 + 12, (gy0 + gy1) / 2)
        title, note = callouts[letter]
        labels.append(text(lx, ly, f"{letter} · {title}", "place", "start"))
        labels.append(text(lx, ly + 20, note, "note", "start"))

    gx, gy = tf(x1 + 130, 3110790)  # the open south-east of the course, clear of A and B
    labels.append(text(gx, gy, words["golf"][0], "place golf-label"))
    labels.append(text(gx, gy + 20, words["golf"][1], "note golf-label"))

    hx0, hy0, hx1, hy1 = hull.bounds
    px, py = tf(hx0 - 25, (hy0 + hy1) / 2 + 60)
    labels.append(text(px, py, words["homes"][0], "place home-label", "end"))
    labels.append(text(px, py + 21, words["homes"][1], "note", "end"))
    labels.append(text(px, py + 40, words["homes"][2], "note", "end"))

    hx, hy = tf(HOSPITAL.x, HOSPITAL.y)
    labels.append(text(hx, hy + 5, words["hospital"], "place"))

    # Scale bar (100 m) and north arrow
    bx, by = 18, height - 20
    labels.append(f'<line class="bar" x1="{bx}" y1="{by}" x2="{bx + round(100 * s, 1)}" y2="{by}"/>')
    labels.append(text(bx + round(50 * s, 1), by - 8, "100 m", "note"))
    ax, ay = WIDTH - 24, height - 22
    labels.append(f'<path class="arrow" d="M{ax},{ay - 34} l9,22 h-18 z"/>')
    labels.append(text(ax, ay + 4, words["north"], "note"))

    return (f'<svg class="mapa" viewBox="0 0 {WIDTH} {height}" role="img" '
            f'aria-labelledby="mapa-titulo" xmlns="http://www.w3.org/2000/svg">\n'
            f'<title id="mapa-titulo">{words["title"]}</title>\n'
            f'<style>{STYLE}</style>\n<defs>{"".join(defs)}</defs>\n'
            + "\n".join(lines + labels) + "\n</svg>")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gpkg", help="layers.gpkg from a green_access.py run")
    ap.add_argument("--html", help="page to update between the mapa:start/end markers")
    ap.add_argument("--out", help="write the SVG to this file")
    ap.add_argument("--lang", choices=sorted(TEXT), default="es", help="language of the labels")
    a = ap.parse_args()
    svg = build_svg(a.gpkg, a.lang)
    if a.out:
        Path(a.out).write_text(svg, encoding="utf-8")
    if a.html:
        page = Path(a.html)
        html = page.read_text(encoding="utf-8")
        pattern = re.compile(re.escape(START) + ".*?" + re.escape(END), re.S)
        if not pattern.search(html):
            raise SystemExit(f"No {START} ... {END} markers in {page}")
        page.write_text(pattern.sub(lambda m: f"{START}\n{svg}\n{END}", html), encoding="utf-8")
    print(f"map: {len(svg) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
