#!/usr/bin/env python3
"""
Walking-distance access to public parks and playgrounds, per barrio, for
Las Palmas de Gran Canaria -- plus a report for one specific address.

Method
------
1. Pedestrian street network from OpenStreetMap (osmnx), projected to
   EPSG:32628 (UTM 28N, metres).
2. Parks and playgrounds from OSM (or from municipal layers you supply).
   Private/customer-only features and anything inside a golf course are dropped.
3. Residential buildings are the "origins". Each carries a weight:
   - with --pop-file (census sections with a population column): the section's
     population is shared among its residential buildings by floor area;
   - otherwise: floor area (footprint x levels) as a population proxy.
4. A multi-source Dijkstra from park/playground entry nodes gives every origin
   its walking distance to the nearest one. Distances run to the park's EDGE
   (nodes within 30 m of the polygon), so they are slightly optimistic -- any
   error works against your claim, not for it.
5. Per barrio: weighted share of people within 300 / 500 / 1000 m, weighted
   median and 90th-percentile distance, and a rank (1 = worst).
6. Sensitivity: the barrio ranking is repeated with minimum park sizes of
   1,000 / 2,500 / 5,000 m2, so you can see whether the conclusion depends on
   what you count as "a park".
7. The target address gets its own report (nearest parks/playgrounds, and what
   share of the city is closer to a park than it is).

Install
-------
    pip install "osmnx>=1.9" geopandas networkx scipy matplotlib pandas numpy

Typical run
-----------
    python green_access.py --barrios barrios.kmz --barrio-name-col NOMBRE \
        --address "Carretera de Chile 37, Las Palmas de Gran Canaria, Spain"

For real resident numbers add --catastro A.ES.SDGC.BU.<code>.zip (Catastro
INSPIRE buildings: dwellings per building) and optionally --household-size.

Get the barrios layer from the municipal GIS server or from ISTAC's WMS (layer
Barrios-2018, requested as GeoJSON); any file covering Las Palmas works and is
clipped to the municipality. If Nominatim cannot find the address, use
--latlon LAT LON.

Caveats (also printed at the end)
---------------------------------
OSM is incomplete, playgrounds especially. Before asserting "there is nothing
within 1 km", confirm on site / street-level imagery, and prefer the official
municipal layers via --parks-file / --playgrounds-file when you have them.
"""
import argparse
import functools
import re
import sys
import tempfile
import time
import warnings
import zipfile
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
import numpy as np  # noqa: E402
import osmnx as ox  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402
from shapely.geometry import LineString, Point, box as rect  # noqa: E402
from shapely.ops import linemerge, unary_union  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning)

# ----------------------------------------------------------------------------
# Settings you may want to edit
# ----------------------------------------------------------------------------
CRS_M = "EPSG:32628"  # UTM zone 28N, correct for Gran Canaria
DEFAULT_PLACE = "Las Palmas de Gran Canaria, Canarias, Spain"
DEFAULT_ADDRESS = "Carretera de Chile 37, Las Palmas de Gran Canaria, Spain"
EDGE_BUFFER_M = 1500  # pull in network/parks just outside the boundary
ENTRY_BUFFERS_M = (30, 150)  # nodes this close to a polygon count as entrances
DENSIFY_M = 20  # extra network node every this many metres along each street
THRESHOLDS_M = (300, 500, 1000)
DEFAULT_MIN_PARK_AREA = 2500  # m2
SENSITIVITY_AREAS = (1000, 2500, 5000)
RANK_METRIC = "park_pct_within_500m"

# OSM tags that count as a park. Plazas are deliberately NOT included: add
# {"place": "square"} / {"leisure": "pitch"} etc. if you want to test them.
PARK_TAGS = {
    "leisure": ["park", "garden"],
    "landuse": ["recreation_ground", "village_green"],
}
PLAYGROUND_TAGS = {"leisure": "playground"}
PRIVATE_ACCESS = {"private", "no", "customers", "permit"}

RESIDENTIAL_KINDS = {
    "residential", "apartments", "house", "detached", "terrace",
    "semidetached_house", "dormitory", "bungalow", "yes",
}
# Maps: these streets are always named; other names come from the longest main roads in view.
KEY_STREETS = ("Carretera de Chile", "Calle Doctor Alfonso Chiscano Díaz")
MAIN_ROADS = {"trunk", "primary", "secondary", "tertiary"}
STREET_ABBREVIATIONS = (("Calle ", "C/ "), ("Avenida ", "Av. "), ("Carretera ", "Ctra. "),
                        ("Doctor ", "Dr. "))
DEFAULT_LEVELS = {"apartments": 5, "residential": 4, "dormitory": 4, "yes": 3}
NAME_COLUMN_CANDIDATES = ("nombre", "name", "barrio", "nom_barrio", "descripcio",
                          "etiqueta", "label", "title", "titulo")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def empty_gdf():
    return gpd.GeoDataFrame(geometry=[], crs=CRS_M)


def union(series):
    return series.union_all() if hasattr(series, "union_all") else series.unary_union


def to_wgs84(geom_m):
    return gpd.GeoSeries([geom_m], crs=CRS_M).to_crs(4326).iloc[0]


def read_vector(path):
    """Read shp/gpkg/geojson/kml/kmz and return it in metres."""
    path = Path(path)
    if path.suffix.lower() == ".kmz":
        tmp = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        path = next(tmp.rglob("*.kml"))
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(4326)
    return gdf.to_crs(CRS_M)


def osm_features(poly_m, tags, required=False, tries=3):
    """Fetch OSM features. 'Nothing matched' returns an empty frame; any other
    failure (Overpass timeout, rate limit, network) is retried and, for a
    required layer, stops the run instead of silently producing zeros."""
    last = None
    for attempt in range(1, tries + 1):
        try:
            g = ox.features_from_polygon(to_wgs84(poly_m), tags)
            return g.reset_index(drop=True).to_crs(CRS_M)
        except Exception as exc:
            if exc.__class__.__name__ == "InsufficientResponseError":
                print(f"  (no OSM features match {tags})")
                return empty_gdf()
            last = exc
            print(f"  OSM query {tags} failed (attempt {attempt}/{tries}): "
                  f"{exc.__class__.__name__}: {str(exc)[:200]}")
            time.sleep(20 * attempt)
    if required:
        sys.exit(f"Could not download {tags} from OpenStreetMap: {last}. "
                 "Overpass may be busy; try again later.")
    return empty_gdf()


def osm_features_split(poly_m, tags, required=False):
    """One Overpass query per tag value: smaller queries time out less often."""
    parts = []
    for key, values in tags.items():
        for value in (values if isinstance(values, list) else [values]):
            g = osm_features(poly_m, {key: value}, required=required)
            print(f"  {key}={value}: {len(g)} features")
            if len(g):
                parts.append(g)
    if not parts:
        return empty_gdf()
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=CRS_M)


def drop_private(g):
    if "access" in g.columns:
        g = g[~g["access"].isin(PRIVATE_ACCESS)]
    return g


def wquantile(values, weights, q):
    """Weighted quantile; +inf values (unreachable) sort to the top."""
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    order = np.argsort(values)
    v, w = values[order], weights[order]
    c = np.cumsum(w)
    if c[-1] <= 0:
        return np.nan
    return v[np.searchsorted(c, q * c[-1])]


def summarise(origins, dist_col, label):
    """Weighted per-barrio access metrics from an origins table."""
    rows = []
    for name, g in origins.groupby("barrio"):
        w = g["weight"].to_numpy(dtype=float)
        d = g[dist_col].to_numpy(dtype=float)
        total = w.sum()
        if total <= 0:
            continue
        row = {"barrio": name, "n_buildings": len(g)}
        for t in THRESHOLDS_M:
            row[f"{label}_pct_within_{t}m"] = 100.0 * w[d <= t].sum() / total
        row[f"{label}_median_m"] = wquantile(d, w, 0.5)
        row[f"{label}_p90_m"] = wquantile(d, w, 0.9)
        rows.append(row)
    return pd.DataFrame(rows).set_index("barrio")


def rank_worst(table, column, min_buildings):
    """1 = worst (lowest share within threshold); small barrios are unranked."""
    ok = table[table["n_buildings"] >= min_buildings]
    return ok[column].rank(method="min", ascending=True)


# ----------------------------------------------------------------------------
# Walking network
# ----------------------------------------------------------------------------
class Net:
    def __init__(self, poly_m):
        print("Downloading pedestrian network (can take a few minutes)...")
        G = ox.graph_from_polygon(to_wgs84(poly_m), network_type="walk", simplify=True)
        Gp = ox.project_graph(G, to_crs=CRS_M)
        self.G = Gp  # kept for street-name lookups
        self.coords = {n: (d["x"], d["y"]) for n, d in Gp.nodes(data=True)}
        # One undirected edge per street segment, shortest parallel edge wins.
        best = {}
        for u, v, d in Gp.edges(data=True):
            if u == v:
                continue
            key = (u, v) if u < v else (v, u)
            length = float(d.get("length", 0.0))
            if key in best and best[key][0] <= length:
                continue
            geom = d.get("geometry")
            if geom is None:
                geom = LineString([self.coords[u], self.coords[v]])
            if (u, v) != key:
                geom = LineString(list(geom.coords)[::-1])
            best[key] = (length, geom)
        # OSMnx keeps nodes only at junctions and dead ends, so a park beside the
        # middle of a long street would find no entrance and a building there would
        # snap to a junction far away. Add a node every DENSIFY_M along each street.
        self.H = nx.Graph()
        next_id = -1
        for (u, v), (length, geom) in best.items():
            n_mid = int(length // DENSIFY_M)
            if n_mid < 1:
                self.H.add_edge(u, v, length=length)
                continue
            step = length / (n_mid + 1)
            prev = u
            for i in range(1, n_mid + 1):
                pnt = geom.interpolate(i / (n_mid + 1), normalized=True)
                self.coords[next_id] = (pnt.x, pnt.y)
                self.H.add_edge(prev, next_id, length=step)
                prev = next_id
                next_id -= 1
            self.H.add_edge(prev, v, length=step)
        self.ids = np.array(list(self.H.nodes))
        self.xy = np.array([self.coords[n] for n in self.ids])
        self.nodes = gpd.GeoDataFrame(
            {"node": self.ids},
            geometry=gpd.points_from_xy(self.xy[:, 0], self.xy[:, 1]),
            crs=CRS_M,
        )
        self.tree = cKDTree(self.xy)
        print(f"  {self.H.number_of_nodes():,} nodes, {self.H.number_of_edges():,} edges")

    def snap(self, x, y):
        d, i = self.tree.query(np.column_stack([x, y]))
        return self.ids[i], d

    def entry_nodes(self, features):
        """One set of entrance nodes per feature (index-aligned)."""
        sidx = self.nodes.sindex
        out, missing = [], 0
        for geom in features.geometry:
            found = set()
            for buf in ENTRY_BUFFERS_M:
                hit = sidx.query(geom.buffer(buf), predicate="intersects")
                if len(hit):
                    found = set(self.ids[hit].tolist())
                    break
            missing += not found
            out.append(found)
        if missing:
            print(f"  {missing} feature(s) had no network node nearby and are ignored")
        return out

    def dist_field(self, node_sets):
        src = set().union(*node_sets) if node_sets else set()
        src &= set(self.H.nodes)
        if not src:
            return {}
        return nx.multi_source_dijkstra_path_length(self.H, src, weight="length")


# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------
def norm_code(value):
    """'2018_35016_TORRES' and '2024_35016_TORRES' both -> '35016_TORRES'."""
    return re.sub(r"^(?:19|20)\d{2}_", "", str(value).strip()).upper()


def attach_names(b, path):
    """Replace barrio codes by names using a code list (year prefix ignored)."""
    names = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
    lower = {c.lower(): c for c in names.columns}
    code_col = next((lower[c] for c in ("code", "codigo", "id", "geocode") if c in lower), None)
    name_col = next((lower[c] for c in ("name#es", "name", "nombre", "label") if c in lower), None)
    if not code_col or not name_col:
        sys.exit(f"Cannot find code/name columns in {path}. Columns: {list(names.columns)}")
    mapping = {norm_code(c): n.strip() for c, n in zip(names[code_col], names[name_col])
               if isinstance(c, str) and isinstance(n, str)}
    keys = b["barrio"].map(norm_code)
    hit = keys.isin(mapping)
    print(f"Barrio names matched for {int(hit.sum())} of {len(b)} polygons")
    if not hit.any():
        sys.exit("No codes matched. Layer codes look like: "
                 f"{b['barrio'].head(3).tolist()}; code list has: "
                 f"{list(names[code_col].dropna().iloc[:3])}")
    b["barrio"] = [mapping.get(k, orig) for k, orig in zip(keys, b["barrio"])]
    return b


def load_barrios(args, boundary):
    if args.barrios:
        b = read_vector(args.barrios)
        col = args.barrio_name_col
        if not col:
            lower = {c.lower(): c for c in b.columns}
            col = next((lower[c] for c in NAME_COLUMN_CANDIDATES if c in lower), None)
        if not col and "geocode" in b.columns:
            col = "geocode"  # ISTAC layers carry a code, not a name
            print("No name column; using 'geocode'. Pass --names-csv to translate codes.")
        if not col or col not in b.columns:
            sys.exit(f"Cannot tell which column holds barrio names. Columns: "
                     f"{list(b.columns)}. Pass --barrio-name-col.")
    else:
        print("No --barrios file: trying OSM admin_level=10 boundaries (patchy).")
        b = osm_features(boundary, {"boundary": "administrative", "admin_level": "10"})
        b = b[b.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        col = "name"
        if len(b) < 5 or col not in b.columns:
            sys.exit("OSM has too few barrio polygons. Download the municipal "
                     "'Barrios' layer (KMZ) and pass it with --barrios.")
    b = b[[col, "geometry"]].rename(columns={col: "barrio"})
    b = b[b.geometry.is_valid & ~b.geometry.is_empty]
    # Some layers repeat each barrio once per time period: keep one shape per barrio.
    if b["barrio"].duplicated().any():
        b = b.drop_duplicates(subset="barrio").reset_index(drop=True)
    # Regional layers (e.g. ISTAC) cover all of Canarias: keep only barrios whose
    # representative point lies inside the municipality.
    b = b[b.geometry.representative_point().within(boundary)]
    if args.names_csv:
        b = attach_names(b.copy(), args.names_csv)
    if b.empty:
        sys.exit("No barrio polygons fall inside the municipal boundary. Check the "
                 "file's CRS and extent (it must cover Las Palmas de Gran Canaria).")
    return b.reset_index(drop=True)


def clean_green(g):
    g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    g = drop_private(g)
    g["area_m2"] = g.geometry.area
    return g


def load_golf(area):
    golf = osm_features(area, {"leisure": "golf_course"})
    golf = golf[golf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    golf["area_m2"] = golf.geometry.area
    return golf.reset_index(drop=True)


def load_parks(args, area):
    """Returns (parks, golf_courses)."""
    if args.parks_file:
        print("Parks: municipal layer")
        return clean_green(read_vector(args.parks_file)), load_golf(area)
    print("Parks: OSM")
    raw = osm_features_split(area, PARK_TAGS, required=True)
    parks = clean_green(raw)
    # Private gardens are often tagged leisure=garden without an access tag.
    if "garden:type" in parks.columns:
        private = parks["garden:type"].isin(["residential", "private", "community"])
        print(f"  dropped {int(private.sum())} private/residential garden(s)")
        parks = parks[~private]
    golf = load_golf(area)
    if len(golf) and len(parks):
        inside = parks.geometry.representative_point().within(union(golf.geometry))
        print(f"  dropped {int(inside.sum())} 'park' polygon(s) inside golf courses")
        parks = parks[~inside]
    print(f"  {len(parks)} public park polygons kept "
          f"({int((parks['area_m2'] >= DEFAULT_MIN_PARK_AREA).sum())} of at least "
          f"{DEFAULT_MIN_PARK_AREA:,} m2)")
    if parks.empty:
        sys.exit("No parks survived loading (OSM returned "
                 f"{len(raw)} raw features). Stopping rather than reporting zeros.")
    return parks.reset_index(drop=True), golf


def load_playgrounds(args, area):
    if args.playgrounds_file:
        print("Playgrounds: municipal layer")
        g = read_vector(args.playgrounds_file)
    else:
        print("Playgrounds: OSM")
        g = osm_features(area, PLAYGROUND_TAGS)
    g = drop_private(g)
    return g.reset_index(drop=True)


def find_col(df, *needles):
    """First column whose name contains one of the needles (case-insensitive)."""
    for needle in needles:
        for c in df.columns:
            if needle.lower() in str(c).lower():
                return c
    return None


@functools.lru_cache(maxsize=None)  # extract the zip once, even when read twice
def catastro_building_file(path):
    """Accept the Catastro BU zip, a folder, or the *.building.gml itself."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        path = tmp
    if path.is_dir():
        hits = [p for p in path.rglob("*") if p.name.lower().endswith("building.gml")]
        if not hits:
            sys.exit(f"No '*.building.gml' found in {path}. Pass the Catastro BU zip "
                     "(A.ES.SDGC.BU.<code>.zip) or the building.gml inside it.")
        path = hits[0]
    return path


def split_by_area(values, keys, areas):
    """Share one value per key across that key's polygons, by area."""
    total = areas.groupby(keys).transform("sum").replace(0, np.nan)
    return (values * areas / total).fillna(0.0)


def catastro_dwellings(path, boundary):
    """Residential buildings from Catastro INSPIRE with their number of dwellings."""
    gml = catastro_building_file(path)
    print(f"Loading Catastro buildings from {gml.name} ...")
    b = gpd.read_file(gml)
    if b.crs is None:
        sys.exit("The Catastro file has no coordinate system; cannot place it.")
    b = b.to_crs(CRS_M)
    b = b[b.geometry.notna() & ~b.geometry.is_empty]
    b = b[b.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()

    dw_col = find_col(b, "numberofdwellings")
    use_col = find_col(b, "currentuse")
    id_col = find_col(b, "localid", "gml_id", "reference")
    area_col = find_col(b, "value")  # officialArea/value = gross floor area (m2)
    if dw_col is None:
        sys.exit(f"No numberOfDwellings column in {gml.name}. Columns: {list(b.columns)}")
    print(f"  columns used: dwellings={dw_col}, use={use_col}, id={id_col}, area={area_col}")

    b["dwellings_raw"] = pd.to_numeric(b[dw_col], errors="coerce").fillna(0)
    b["fp_m2"] = b.geometry.area
    # numberOfDwellings is per cadastral parcel: split it over that parcel's polygons.
    if id_col is not None:
        b["dwellings"] = split_by_area(b["dwellings_raw"], b[id_col], b["fp_m2"])
    else:
        b["dwellings"] = b["dwellings_raw"]
    if area_col is not None:
        b["floor_m2"] = pd.to_numeric(b[area_col], errors="coerce")
    else:
        b["floor_m2"] = np.nan
    b["floor_m2"] = b["floor_m2"].fillna(b["fp_m2"])

    b = b[b["dwellings"] > 0]
    b = b[b.geometry.representative_point().within(boundary)]
    uses = b[use_col].value_counts().head(4).to_dict() if use_col else {}
    print(f"  {len(b):,} buildings with dwellings, {b['dwellings'].sum():,.0f} dwellings"
          f" in the municipality (dominant uses: {uses})")
    return b


def building_footprints(path):
    """Every Catastro building footprint, of any use: drawn on the maps for orientation."""
    b = gpd.read_file(catastro_building_file(path)).to_crs(CRS_M)
    return b.loc[b.geometry.geom_type.isin(["Polygon", "MultiPolygon"]), ["geometry"]]


def apportion_population(o, pop_file, pop_col):
    """Share each census section's population over its buildings, in proportion
    to their current weight (dwellings or floor area)."""
    pop = read_vector(pop_file)
    if pop_col not in pop.columns:
        sys.exit(f"No '{pop_col}' column in {pop_file}. Columns: {list(pop.columns)}")
    pop[pop_col] = pd.to_numeric(pop[pop_col], errors="coerce")
    pop = pop[[pop_col, "geometry"]].reset_index(drop=True)
    pop["_pid"] = pop.index
    j = gpd.sjoin(o, pop, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    total = j.groupby("_pid")["weight"].transform("sum")
    o["weight"] = (j[pop_col] * j["weight"] / total).fillna(0.0).to_numpy()
    print(f"  population distributed over buildings: {o['weight'].sum():,.0f}")
    return o


def residential_origins(boundary, net, pop_file, pop_col, catastro=None, household=2.5):
    if catastro:
        b = catastro_dwellings(catastro, boundary)
        o = gpd.GeoDataFrame(
            {"weight": (b["dwellings"] * household).to_numpy(),
             "dwellings": b["dwellings"].to_numpy(),
             "floor_m2": b["floor_m2"].to_numpy(),
             "osm_street": np.full(len(b), None, dtype=object)},
            geometry=b.geometry.representative_point().to_numpy(),
            crs=CRS_M,
        )
        if pop_file:  # census sections: share each section's population by dwellings
            o = apportion_population(o, pop_file, pop_col)
        else:
            print(f"  residents = dwellings x {household} people per household")
        o["node"], o["snap_m"] = net.snap(o.geometry.x, o.geometry.y)
        o = o[(o["snap_m"] < 300) & (o["weight"] > 0)].reset_index(drop=True)
        print(f"  {len(o):,} residential origins")
        return o

    print("Loading residential buildings...")
    b = osm_features(boundary, {"building": True})
    b = b[b.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    b = b[b["building"].astype(str).isin(RESIDENTIAL_KINDS)].copy()
    if "building:levels" in b.columns:
        levels = pd.to_numeric(b["building:levels"], errors="coerce")
    else:
        levels = pd.Series(np.nan, index=b.index)
    levels = levels.fillna(b["building"].map(DEFAULT_LEVELS)).fillna(2).clip(1, 40)
    b["weight"] = b.geometry.area * levels
    o = gpd.GeoDataFrame(
        {"weight": b["weight"].to_numpy(),
         "floor_m2": b["weight"].to_numpy(),
         "osm_street": (b["addr:street"].to_numpy() if "addr:street" in b.columns
                        else np.full(len(b), None, dtype=object))},
        geometry=b.geometry.representative_point().to_numpy(),
        crs=CRS_M,
    )

    if pop_file:
        o = apportion_population(o, pop_file, pop_col)
    else:
        print("  no --pop-file: using residential floor area as population proxy")

    o["node"], o["snap_m"] = net.snap(o.geometry.x, o.geometry.y)
    o = o[(o["snap_m"] < 300) & (o["weight"] > 0)].reset_index(drop=True)
    print(f"  {len(o):,} residential origins")
    return o


# ----------------------------------------------------------------------------
# Target address report
# ----------------------------------------------------------------------------
def nearest_features(net, node, snap_m, features, node_sets, k=5):
    dd = nx.single_source_dijkstra_path_length(net.H, node, cutoff=4000, weight="length") \
        if node in net.H else {}
    names = features["name"] if "name" in features.columns else pd.Series("", index=features.index)
    areas = features["area_m2"] if "area_m2" in features.columns else pd.Series(np.nan, index=features.index)
    rows = []
    for idx, nodes in zip(features.index, node_sets):
        d = min((dd[n] for n in nodes if n in dd), default=np.inf)
        rows.append((d + snap_m, str(names[idx]) if pd.notna(names[idx]) else "(unnamed)", areas[idx]))
    rows.sort(key=lambda r: r[0])
    return rows[:k]


def format_rows(rows):
    out = []
    for d, name, area in rows:
        dist = f"{d:6.0f} m" if np.isfinite(d) else "   >4 km"
        size = f"{area:,.0f} m2" if pd.notna(area) else "n/a"
        out.append(f"    {dist}  {name}  [{size}]")
    return "\n".join(out)


def target_report(a, net, origins_all, barrios, parks_big, parks_sets,
                  play, play_sets, d_park_all, d_play_all, table, sens, min_area):
    if a.latlon:
        lat, lon = a.latlon
    else:
        print(f"Geocoding: {a.address}")
        lat, lon = ox.geocode(a.address)
    pt = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(CRS_M).iloc[0]
    node, snap_m = net.snap([pt.x], [pt.y])
    node, snap_m = node[0], float(snap_m[0])

    dd = nx.single_source_dijkstra_path_length(net.H, node, cutoff=4000, weight="length") \
        if node in net.H else {}
    d_park = min((dd[n] for ns in parks_sets for n in ns if n in dd), default=np.inf) + snap_m
    d_play = min((dd[n] for ns in play_sets for n in ns if n in dd), default=np.inf) + snap_m

    w = origins_all["weight"].to_numpy()
    closer_park = 100.0 * w[origins_all[d_park_all] < d_park].sum() / w.sum()
    closer_play = 100.0 * w[origins_all[d_play_all] < d_play].sum() / w.sum()

    lines = [
        f"TARGET: {a.address if not a.latlon else a.latlon}  ({lat:.5f}, {lon:.5f})",
        f"Walking distance to nearest park >= {min_area:,} m2:   "
        f"{d_park:,.0f} m" if np.isfinite(d_park) else "Nearest park: none within 4 km",
        f"Walking distance to nearest playground:           "
        f"{d_play:,.0f} m" if np.isfinite(d_play) else "Nearest playground: none within 4 km",
        f"Share of the city's residents with a park CLOSER than this address:       {closer_park:5.1f}%",
        f"Share of the city's residents with a playground CLOSER than this address: {closer_play:5.1f}%",
        "",
        "Five nearest parks (walking distance to the park edge):",
        format_rows(nearest_features(net, node, snap_m, parks_big, parks_sets)),
        "Five nearest playgrounds:",
        format_rows(nearest_features(net, node, snap_m, play, play_sets)),
    ]

    hit = gpd.sjoin(gpd.GeoDataFrame(geometry=[pt], crs=CRS_M), barrios, how="left", predicate="within")
    barrio = hit["barrio"].iloc[0] if len(hit) and pd.notna(hit["barrio"].iloc[0]) else None
    if barrio is not None and barrio in table.index:
        lines += ["", f"BARRIO: {barrio}",
                  table.loc[[barrio]].T.to_string(float_format=lambda x: f"{x:,.1f}")]
        if barrio in sens.index:
            lines += ["", f"Rank on '{RANK_METRIC}' (1 = worst), by minimum park size:",
                      sens.loc[[barrio]].T.to_string()]
    elif barrio is not None:
        lines += ["", f"BARRIO: {barrio} (too few buildings to be ranked)"]
    else:
        lines += ["", "Target point is not inside any barrio polygon."]
    return "\n".join(lines), (pt, barrio)


# ----------------------------------------------------------------------------
# Pocket analysis: the under-served cluster around the target + golf scenario
# ----------------------------------------------------------------------------
def crow_distance(points, features):
    """Straight-line distance from each point to the nearest feature edge."""
    if features.empty:
        return pd.Series(np.inf, index=points.index)
    j = gpd.sjoin_nearest(points[["geometry"]], features[["geometry"]],
                          how="left", distance_col="crow")
    return j[~j.index.duplicated()]["crow"].reindex(points.index)


def connected_cluster(points_xy, seed_xy, link_m, seed_max_m=300):
    """Indices of the cluster (buildings chained within link_m of each other)
    that contains the point nearest to seed_xy."""
    if len(points_xy) == 0:
        return np.array([], dtype=int)
    tree = cKDTree(points_xy)
    d, start = tree.query(seed_xy)
    if d > seed_max_m:
        return np.array([], dtype=int)
    parent = np.arange(len(points_xy))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in tree.query_pairs(link_m):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
    roots = np.array([find(i) for i in range(len(points_xy))])
    return np.flatnonzero(roots == roots[start])


def street_names(net, points):
    """Name of the nearest walkable street for each point (OSM)."""
    try:
        edges = ox.distance.nearest_edges(net.G, points.geometry.x.to_numpy(),
                                          points.geometry.y.to_numpy())
    except Exception as exc:
        print(f"  (street-name lookup skipped: {exc.__class__.__name__})")
        return pd.Series("(unknown)", index=points.index)
    names = []
    for u, v, k in edges:
        n = net.G.edges[u, v, k].get("name", "(unnamed)")
        names.append(" / ".join(n) if isinstance(n, list) else str(n))
    return pd.Series(names, index=points.index)


def entrance_points(net, nodes, label):
    """The street nodes a scenario uses as entrances, as points with street names."""
    ids = [n for n in nodes if n in net.coords]
    if not ids:
        return gpd.GeoDataFrame({"scenario": [], "street": []}, geometry=[], crs=CRS_M)
    pts = gpd.GeoDataFrame(
        {"scenario": label, "node": ids},
        geometry=gpd.points_from_xy([net.coords[n][0] for n in ids],
                                    [net.coords[n][1] for n in ids]),
        crs=CRS_M,
    )
    pts["street"] = street_names(net, pts).to_numpy()
    return pts


def describe_entrances(pts):
    if pts.empty:
        return "none found"
    counts = pts["street"].value_counts()
    return f"{len(pts)} street point(s): " + ", ".join(f"{s} ({n})" for s, n in counts.items())


def share_within(d, w, t):
    return 100.0 * w[d <= t].sum() / w.sum() if w.sum() > 0 else np.nan


def pocket_analysis(a, net, origins, parks_big, play, golf, pt):
    print("Pocket analysis...")
    o = origins
    o["crow_park"] = crow_distance(o, parks_big)
    o["crow_play"] = crow_distance(o, play)
    o["detour_park"] = (o["d_park"] / o["crow_park"].clip(lower=25)).replace(np.inf, np.nan)
    o["nearest_any"] = np.minimum(o["d_park"], o["d_play"])

    # --- define the pocket -------------------------------------------------
    if a.pocket_file:
        poly = union(read_vector(a.pocket_file).geometry)
        in_pocket = o.geometry.within(poly).to_numpy()
        how = f"buildings inside {a.pocket_file}"
    else:
        under = np.flatnonzero((o["nearest_any"] > a.pocket_threshold).to_numpy())
        xy = np.column_stack([o.geometry.x.to_numpy()[under], o.geometry.y.to_numpy()[under]])
        if a.pocket_link is None:
            # Catastro gives one point per parcel: big complexes sit further apart.
            a.pocket_link = 150.0 if a.catastro else 80.0
        members = under[connected_cluster(xy, (pt.x, pt.y), a.pocket_link)]
        in_pocket = np.zeros(len(o), dtype=bool)
        in_pocket[members] = True
        how = (f"connected cluster of residential buildings (linked within "
               f"{a.pocket_link:.0f} m) whose nearest park AND nearest playground are both "
               f"more than {a.pocket_threshold:.0f} m away on foot, seeded at the target")
    o["in_pocket"] = in_pocket
    p = o[in_pocket].copy()
    lines = ["POCKET DEFINITION", f"  {how}", ""]
    if p.empty:
        lines.append("  No under-served buildings found next to the target with these settings.")
        return "\n".join(lines), None, golf.iloc[0:0], None

    # --- who lives there ---------------------------------------------------
    def people(df):
        """Residents in a set of buildings, by the best source available."""
        if a.pop_file or a.catastro:
            return df["weight"].sum()
        return df["floor_m2"].sum() / a.m2_per_resident

    floor = p["floor_m2"].sum()
    residents = people(p)
    if a.pop_file and a.catastro:
        res_note = "census-section population shared by Catastro dwellings"
    elif a.pop_file:
        res_note = "census-section population shared by floor area"
    elif a.catastro:
        res_note = (f"Catastro dwellings x {a.household_size} people per household; "
                    "includes empty and second homes")
    else:
        res_note = (f"ROUGH estimate: residential floor area / {a.m2_per_resident:.0f} m2 "
                    "per resident")
    p["street"] = p["osm_street"].where(p["osm_street"].notna(), street_names(net, p))
    streets = p.groupby("street").size().sort_values(ascending=False)
    w = p["weight"].to_numpy()

    def wmed(col):
        v = p[col].to_numpy(dtype=float)
        return wquantile(v, w, 0.5)

    lines += [
        "WHO LIVES IN THE POCKET",
        f"  residential buildings:            {len(p):,}",
        f"  residential floor area:           {floor:,.0f} m2",
        *([f"  dwellings (Catastro):             {p['dwellings'].sum():,.0f}"]
          if "dwellings" in p.columns else []),
        f"  residents:                        ~{residents:,.0f}  ({res_note})",
        "  streets (nearest walkable street per building):",
        *[f"    {n:4d}  {s}" for s, n in streets.head(15).items()],
        "",
        "ACCESS FROM THE POCKET (weighted medians unless stated)",
        f"  walk to nearest park >= {a.min_park_area:,.0f} m2:      {wmed('d_park'):,.0f} m"
        f"   (worst building: {p['d_park'].max():,.0f} m)",
        f"  walk to nearest playground:         {wmed('d_play'):,.0f} m"
        f"   (worst building: {p['d_play'].max():,.0f} m)",
        "",
    ]

    hull = union(p.geometry.buffer(40)).buffer(-15)
    no_strips = gpd.GeoDataFrame({"width_m": []}, geometry=[], crs=CRS_M)
    extras = {"entrances": [], "proposals": None}

    # --- golf course scenario ---------------------------------------------
    near = golf[golf.geometry.distance(hull) <= a.golf_max_dist].copy() if len(golf) else golf
    if near.empty:
        lines.append(f"GOLF SCENARIO: no golf course within {a.golf_max_dist:.0f} m of the pocket.")
        return "\n".join(lines), hull, near, no_strips, extras
    gsets = net.entry_nodes(near)
    gfield = net.dist_field(gsets)
    o["d_golf"] = o["node"].map(gfield).fillna(np.inf) + o["snap_m"]
    o["d_park_if_golf_open"] = np.minimum(o["d_park"], o["d_golf"])
    p = o[o["in_pocket"]]
    w = p["weight"].to_numpy()
    wall = o["weight"].to_numpy()
    gname = near["name"].fillna("(unnamed)").tolist() if "name" in near.columns else ["(unnamed)"] * len(near)
    newly_res = people(o[(o["d_park"] > 500) & (o["d_park_if_golf_open"] <= 500)])
    lines += [
        "GOLF SCENARIO (what if the golf course were a public park?)",
        "  golf course(s): " + "; ".join(f"{n} ({ar / 1e4:,.1f} ha)"
                                         for n, ar in zip(gname, near["area_m2"])),
        f"  total golf area:                    {near['area_m2'].sum() / 1e4:,.1f} ha"
        f"  (= {near['area_m2'].sum() / max(parks_big['area_m2'].median(), 1):,.0f}x the"
        " median park in this study)",
        "  assumption: reachable from every street node on or within 30 m of its edge",
        "",
        "                                      now     if golf were a park",
        f"  pocket median walk to a park     {wquantile(p['d_park'].to_numpy(float), w, .5):7,.0f} m"
        f"   {wquantile(p['d_park_if_golf_open'].to_numpy(float), w, .5):7,.0f} m",
        f"  pocket within 300 m of a park    {share_within(p['d_park'], w, 300):6.1f} %"
        f"   {share_within(p['d_park_if_golf_open'], w, 300):6.1f} %",
        f"  pocket within 500 m of a park    {share_within(p['d_park'], w, 500):6.1f} %"
        f"   {share_within(p['d_park_if_golf_open'], w, 500):6.1f} %",
        f"  city residents within 500 m      {share_within(o['d_park'], wall, 500):6.1f} %"
        f"   {share_within(o['d_park_if_golf_open'], wall, 500):6.1f} %",
        f"  people citywide who would gain a park within 500 m: ~{newly_res:,.0f}"
        f"  ({res_note})",
        "",
    ]

    # --- partial conversion: a strip of the course on the side facing the pocket
    gunion = union(near.geometry)
    gap = hull.distance(gunion)
    rows, strips = [], []
    for width in a.strip_widths:
        strip = gunion.intersection(hull.buffer(gap + width))
        if strip.is_empty or strip.area < 100:
            continue
        sgdf = gpd.GeoDataFrame(geometry=[strip], crs=CRS_M)
        snodes = net.entry_nodes(sgdf)
        extras["entrances"].append(entrance_points(net, set().union(*snodes), f"strip {width:.0f} m"))
        sfield = net.dist_field(snodes)
        d_strip = o["node"].map(sfield).fillna(np.inf) + o["snap_m"]
        d_scn = np.minimum(o["d_park"], d_strip)
        ds = d_scn[o["in_pocket"]]
        gain = (o["d_park"] > 500) & (d_scn <= 500)
        gained = people(o[gain])
        rows.append((width, strip.area, 100 * strip.area / gunion.area,
                     wquantile(ds.to_numpy(float), w, .5),
                     share_within(ds, w, 300), share_within(ds, w, 500), gained))
        strips.append({"width_m": width, "area_m2": strip.area, "geometry": strip})
        o[f"d_park_if_strip_{int(width)}m"] = d_scn
    strips_gdf = (gpd.GeoDataFrame(strips, crs=CRS_M) if strips else no_strips)

    lines += [
        "PARTIAL CONVERSION (a strip of the course on the side facing the pocket)",
        "  strip = golf land within the given width of its edge nearest the homes;",
        "  entrances anywhere along the strip's edge (it borders the residential streets)",
        "",
        "  width   area     % of course   pocket median   pocket <=300 m   pocket <=500 m   people gaining",
        "                                  walk to park                                      a park <=500 m",
    ]
    for wd, ar, pct, med, s3, s5, gn in rows:
        lines.append(f"  {wd:4.0f} m  {ar / 1e4:5.2f} ha  {pct:8.0f} %    {med:9,.0f} m"
                     f"   {s3:12.1f} %   {s5:12.1f} %     ~{gn:,.0f}")
    lines += [
        f"  (today: pocket median {wquantile(p['d_park'].to_numpy(float), w, .5):,.0f} m, "
        f"{share_within(p['d_park'], w, 500):.0f} % within 500 m)",
        "",
        "  entrances assumed (street points within 30 m of the strip's edge):",
        *[f"    {e['scenario'].iloc[0] if len(e) else '':10s} {describe_entrances(e)}"
          for e in extras["entrances"]],
        "",
    ]

    # --- a specific proposed park area, drawn by hand ----------------------
    if a.proposal_file:
        prop = read_vector(a.proposal_file)
        prop = prop[prop.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
        name_col = find_col(prop, "name", "nombre", "label")
        lines += ["PROPOSED PARK AREA(S) from " + str(a.proposal_file),
                  "  entrances: street nodes within 30 m of the area's edge (draw the area so it",
                  "  touches the street you want the entrance on)",
                  "",
                  "  area                     size      pocket median   pocket <=300 m   pocket <=500 m   people gaining",
                  "                                     walk to park                                      a park <=500 m"]
        feats = [(str(prop.loc[i, name_col]) if name_col else f"area {i + 1}", prop.geometry.iloc[[i]])
                 for i in range(len(prop))]
        prop["label"] = [f[0] for f in feats]
        gu = union(near.geometry)
        prop["area_m2"] = prop.geometry.area
        prop["share_on_golf"] = [100 * g.intersection(gu).area / max(g.area, 1) for g in prop.geometry]
        extras["proposals"] = prop[["label", "area_m2", "share_on_golf", "geometry"]]
        if len(prop) > 1:
            feats.append(("all areas together", prop.geometry))
        ent_lines = []
        for label, geom in feats:
            g = gpd.GeoDataFrame(geometry=list(geom), crs=CRS_M)
            pnodes = net.entry_nodes(g)
            if label != "all areas together":
                e = entrance_points(net, set().union(*pnodes), label)
                extras["entrances"].append(e)
                share = prop.loc[prop["label"] == label, "share_on_golf"].iloc[0]
                ent_lines.append(f"    {label[:22]:22s} {share:3.0f} % on the golf course; entrances: "
                                 f"{describe_entrances(e)}")
            field = net.dist_field(pnodes)
            d_new = np.minimum(o["d_park"], o["node"].map(field).fillna(np.inf) + o["snap_m"])
            dp = d_new[o["in_pocket"]]
            gained = people(o[(o["d_park"] > 500) & (d_new <= 500)])
            lines.append(f"  {label[:22]:22s} {g.geometry.area.sum() / 1e4:6.2f} ha   "
                         f"{wquantile(dp.to_numpy(float), w, .5):9,.0f} m   "
                         f"{share_within(dp, w, 300):12.1f} %   {share_within(dp, w, 500):12.1f} %"
                         f"     ~{gained:,.0f}")
        lines += ["", "  where each area sits and which streets it opens onto:", *ent_lines,
                  "  (an area only helps if it opens onto the streets the homes use; see map_proposals.png)",
                  ""]

    lines += [
        "CAVEATS",
        "  - Walking routes come from OSM. A missing staircase or path would shorten the",
        "    real distances; check the routes on the ground before quoting them.",
        "  - The full-course scenario is an upper bound. The strip rows assume an entrance",
        "    on the residential side; a single gate elsewhere would give smaller gains.",
        "  - Strip shapes follow the pocket outline, not the course layout (greens, tees).",
        "    Check them on the map before proposing a specific area.",
    ]
    ents = [e for e in extras["entrances"] if len(e)]
    extras["entrances"] = (pd.concat(ents, ignore_index=True) if ents else
                           gpd.GeoDataFrame({"scenario": [], "street": []}, geometry=[], crs=CRS_M))
    return "\n".join(lines), hull, near, strips_gdf, extras


def street_edges(net):
    """The walking network's streets (OSM) with name and road class, for drawing on maps."""
    e = ox.graph_to_gdfs(net.G, nodes=False).reset_index(drop=True)
    for c in ("name", "highway"):
        if c not in e.columns:
            e[c] = None
        e[c] = e[c].apply(lambda v: v[0] if isinstance(v, list) else v)
    return e[["name", "highway", "geometry"]]


def draw_base(ax, box, streets=None, footprints=None):
    """Building footprints in light grey, then streets as grey lines (main roads thicker)."""
    if footprints is not None:
        footprints.cx[box[0]:box[2], box[1]:box[3]].plot(
            ax=ax, facecolor="#e4e4e4", edgecolor="#c2c2c2", linewidth=0.3, zorder=1)
    if streets is None:
        return
    s = streets.cx[box[0]:box[2], box[1]:box[3]]
    main = s["highway"].isin(MAIN_ROADS)
    s[~main].plot(ax=ax, color="#d0d0d0", linewidth=0.7, zorder=1)
    s[main].plot(ax=ax, color="#a8a8a8", linewidth=2, zorder=1)


def label_streets(ax, streets, box, n_main=0, fontsize=8, avoid=()):
    """Names along KEY_STREETS and the n_main longest main roads in view.

    Call last, after the legend: each name slides along its street, or to one side of it, until it
    clears the legend, the text already on the map, the other names and the `avoid` shapes.
    Other names that find no free spot are left out; key streets are placed anyway.
    """
    if streets is None:
        return
    s = streets.cx[box[0]:box[2], box[1]:box[3]]
    if s.empty:
        return
    fig = ax.figure
    fig.canvas.draw()
    to_data = ax.transData.inverted()
    per_pt = (box[2] - box[0]) / ax.bbox.width * fig.dpi / 72  # map metres per font point

    def extent(artist):
        (x0, y0), (x1, y1) = to_data.transform(artist.get_window_extent().get_points())
        return rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    blocked = [g for g in avoid if g is not None and not g.is_empty]
    blocked += [extent(t) for t in ax.texts]
    if ax.get_legend() is not None:
        blocked.append(extent(ax.get_legend()))
    blocked = unary_union(blocked) if blocked else None

    w, h = box[2] - box[0], box[3] - box[1]
    inner = rect(box[0] + 0.03 * w, box[1] + 0.03 * h, box[2] - 0.03 * w, box[3] - 0.03 * h)
    pieces = {}  # name -> its continuous pieces in view, longest first
    for name, g in s.dropna(subset=["name"]).groupby("name"):
        merged = unary_union(list(g.geometry)).intersection(inner)
        if merged.is_empty:
            continue
        if merged.geom_type == "MultiLineString":
            merged = linemerge(merged)
        parts = [p for p in getattr(merged, "geoms", [merged]) if p.geom_type == "LineString"]
        if parts:
            pieces[name] = sorted(parts, key=lambda p: -p.length)[:4]
    mains = set(s.loc[s["highway"].isin(MAIN_ROADS), "name"].dropna())
    others = sorted((n for n in pieces if n in mains and n not in KEY_STREETS),
                    key=lambda n: -pieces[n][0].length)

    def spot(name, text, fs):
        """Least-blocked place for `text` along the street: (overlap area, footprint, xy, angle)."""
        lines = text.count("\n") + 1
        half = max(len(t) for t in text.split("\n")) * 0.3 * fs * per_pt  # metres
        tall = 1.3 * fs * per_pt * lines
        fits = [p for p in pieces[name] if p.length >= 2 * half] or [pieces[name][0]]
        best = None
        for line in fits:
            for f in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8):
                d = min(max(f * line.length, half), line.length - half) if line.length > 2 * half \
                    else line.length / 2
                p0 = line.interpolate(max(d - half, 0))
                p1 = line.interpolate(min(d + half, line.length))
                dx, dy = p1.x - p0.x, p1.y - p0.y
                norm = np.hypot(dx, dy) or 1.0
                for off in (0.0, 0.9, -0.9):
                    ox_, oy_ = -dy / norm * off * tall, dx / norm * off * tall
                    foot = LineString([(p0.x + ox_, p0.y + oy_),
                                       (p1.x + ox_, p1.y + oy_)]).buffer(tall / 2)
                    hit = foot.intersection(blocked).area if blocked is not None else 0.0
                    hit += sum(foot.intersection(q).area for q in placed)
                    if best is None or hit < best[0]:
                        best = (hit, foot, ((p0.x + p1.x) / 2 + ox_, (p0.y + p1.y) / 2 + oy_),
                                np.degrees(np.arctan2(dy, dx)))
                    if hit == 0:
                        return best
        return best

    placed = []
    for name in [n for n in KEY_STREETS if n in pieces] + others[:n_main]:
        key = name in KEY_STREETS
        fs = fontsize * (1.2 if key else 1)
        label = name
        for a, b in STREET_ABBREVIATIONS:
            label = label.replace(a, b)
        texts = [label]
        if key and " " in label:  # a long key name may fit on two lines
            words = label.split(" ")
            cut = min(range(1, len(words)),
                      key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
            texts.append(" ".join(words[:cut]) + "\n" + " ".join(words[cut:]))
        if not key and pieces[name][0].length < len(label) * 0.6 * fs * per_pt:
            continue  # too short to carry its name
        options = []
        for text in texts:
            options.append((spot(name, text, fs), text))
            if options[-1][0][0] == 0:
                break
        (hit, foot, (x, y), angle), text = min(options, key=lambda o: o[0][0])
        if hit > 0 and not key:
            continue
        ax.text(x, y, text, rotation=(angle + 90) % 180 - 90, rotation_mode="anchor",
                ha="center", va="center", multialignment="center", fontsize=fs,
                color="#222222" if key else "#555555", weight="bold" if key else "normal",
                zorder=8, linespacing=1.0,
                path_effects=[pe.withStroke(linewidth=3, foreground="white")])
        placed.append(foot)


def north_arrow(ax, box):
    w, h = box[2] - box[0], box[3] - box[1]
    x, y = box[2] - 0.05 * w, box[1] + 0.05 * h
    ax.annotate("N", xy=(x, y + 0.07 * h), xytext=(x, y), ha="center", va="top", fontsize=12,
                weight="bold", arrowprops=dict(arrowstyle="-|>", color="black", lw=2), zorder=9)


def thin_points(pts, spacing):
    """A subset of points at least `spacing` apart, so markers don't pile up along a street."""
    if pts.empty:
        return pts
    pts = pts.iloc[np.lexsort((pts.geometry.x, pts.geometry.y))]
    xy = np.column_stack([pts.geometry.x, pts.geometry.y])
    keep = []
    for i in range(len(xy)):
        if all(np.hypot(*(xy[i] - xy[j])) >= spacing for j in keep):
            keep.append(i)
    return pts.iloc[keep]


def plot_proposals(out, origins, hull, golf, extras, pt, streets=None, footprints=None,
                   target_label="target"):
    """Close-up of the pocket, the west of the golf course and the hand-drawn areas.

    Each area is labelled with the part of its name before ':' (e.g. "A: abandoned plot" -> "A").
    """
    if hull is None:
        return
    prop, ents = extras.get("proposals"), extras.get("entrances")
    shapes = [hull] + ([] if prop is None else list(prop.geometry))
    minx, miny, maxx, maxy = gpd.GeoSeries(shapes, crs=CRS_M).total_bounds
    pad = 120
    box = (minx - pad, miny - pad, maxx + pad, maxy + pad)
    fig, ax = plt.subplots(figsize=(10, 10 * (box[3] - box[1]) / (box[2] - box[0])))
    draw_base(ax, box, streets, footprints)
    if len(golf):
        golf.plot(ax=ax, facecolor="#e8dcb5", edgecolor="#8a7a3f", linewidth=1, zorder=2)
        visible = union(golf.geometry).intersection(rect(*box))
        if prop is not None and len(prop):
            visible = visible.difference(union(prop.geometry).buffer(25))
        if not visible.is_empty:
            c = visible.representative_point()
            ax.text(c.x, c.y, "Las Palmeras Golf", ha="center", va="center", fontsize=11,
                    style="italic", color="#5c4f1f", zorder=8)
    if prop is not None and len(prop):
        prop.plot(ax=ax, facecolor="#2b6cb0", alpha=0.35, edgecolor="#1a4480", linewidth=2, zorder=3)
        for _, r in prop.iterrows():
            c = r.geometry.representative_point()
            ax.text(c.x, c.y, str(r["label"]).split(":")[0], ha="center", va="center", fontsize=22,
                    weight="bold", color="#10305a", zorder=8,
                    path_effects=[pe.withStroke(linewidth=4, foreground="white")])
    o = origins.cx[box[0]:box[2], box[1]:box[3]]
    sc = ax.scatter(o.geometry.x, o.geometry.y, c=o["d_park"].clip(upper=1500), s=30,
                    cmap="RdYlGn_r", vmin=0, vmax=1500, edgecolor="white", linewidth=0.5, zorder=4)
    if ents is not None and len(ents) and prop is not None:
        # Every street point within 30 m of an area counts as an entrance; show one per 60 m.
        styles = [("^", "#f5c518"), ("v", "#ffffff"), ("D", "#9ad0f5"), ("s", "#f59a9a")]
        areas = [lab for lab in prop["label"] if lab in set(ents["scenario"])]
        for i, lab in enumerate(areas):
            e = thin_points(ents[ents["scenario"] == lab], 60)
            marker, face = styles[i % len(styles)]
            ax.scatter(e.geometry.x, e.geometry.y, marker=marker, s=60, facecolor=face,
                       edgecolor="#111111", linewidth=1.3, zorder=6,
                       label=f"entrance to {str(lab).split(':')[0]}")
    ax.plot(pt.x, pt.y, marker="*", color="black", ms=18, ls="", label=target_label, zorder=7)
    ax.set_xlim(box[0], box[2])
    ax.set_ylim(box[1], box[3])
    x0, y0 = box[0] + 30, box[1] + 30
    ax.plot([x0, x0 + 100], [y0, y0], color="black", linewidth=3, zorder=9)
    ax.text(x0 + 50, y0 + 8, "100 m", ha="center", zorder=9)
    north_arrow(ax, box)
    fig.colorbar(sc, ax=ax, shrink=0.5, label="walking distance to nearest park today (m)")
    ax.legend(loc="upper left", fontsize=9, framealpha=0.95)
    ax.set_axis_off()
    avoid = [] if prop is None else list(prop.geometry)
    avoid += list(o.geometry.buffer(6))
    if ents is not None and len(ents):
        avoid += list(ents.geometry.buffer(8))
    label_streets(ax, streets, box, n_main=4, fontsize=9, avoid=avoid)
    ax.set_title("Proposed park areas and their street entrances")
    fig.savefig(out / "map_proposals.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


def plot_pocket(out, origins, hull, parks_big, play, golf, pt, proposals=None, streets=None,
                footprints=None, target_label="target"):
    if hull is None:
        return
    minx, miny, maxx, maxy = hull.bounds
    pad = 1300
    box = (minx - pad, miny - pad, maxx + pad, maxy + pad)
    o = origins.cx[box[0]:box[2], box[1]:box[3]]
    fig, ax = plt.subplots(figsize=(11, 11))
    draw_base(ax, box, streets, footprints)
    if len(golf):
        golf.plot(ax=ax, facecolor="#e8dcb5", edgecolor="#8a7a3f", hatch="//", linewidth=1, zorder=2)
        for _, g in golf[golf["area_m2"] >= 1e4].iterrows():  # skip slivers
            gx0, gy0, gx1, gy1 = g.geometry.bounds  # name beside the course, clear of the streets
            ax.text(gx1 + 40, (gy0 + gy1) / 2, f"Las Palmeras Golf\n{g['area_m2'] / 1e4:,.1f} ha",
                    ha="left", va="center", fontsize=10, color="#5c4f1f", weight="bold", zorder=8,
                    path_effects=[pe.withStroke(linewidth=3, foreground="white")])
    if proposals is not None and len(proposals):
        proposals.plot(ax=ax, facecolor="#2b6cb0", alpha=0.6, edgecolor="#1a4480", linewidth=1, zorder=4)
        for _, r in proposals.iterrows():
            c = r.geometry.representative_point()
            ax.text(c.x + 25, c.y, str(r["label"]).split(":")[0], ha="left", va="center", fontsize=11,
                    weight="bold", color="#10305a", zorder=8,
                    path_effects=[pe.withStroke(linewidth=3, foreground="white")])
    parks_big.cx[box[0]:box[2], box[1]:box[3]].plot(ax=ax, color="#2e8b57", alpha=0.7, zorder=2)
    sc = ax.scatter(o.geometry.x, o.geometry.y, c=o["d_park"].clip(upper=1500), s=9,
                    cmap="RdYlGn_r", vmin=0, vmax=1500, zorder=3)
    if len(play):
        pp = play.geometry.representative_point()
        pp = pp.cx[box[0]:box[2], box[1]:box[3]]
        ax.scatter(pp.x, pp.y, marker="^", s=55, c="royalblue", edgecolor="white",
                   label="playground", zorder=4)
    ax.plot(pt.x, pt.y, marker="*", color="black", ms=18, ls="", label=target_label, zorder=6)
    ax.set_xlim(box[0], box[2])
    ax.set_ylim(box[1], box[3])
    # 500 m scale bar
    x0, y0 = box[0] + 150, box[1] + 150
    ax.plot([x0, x0 + 500], [y0, y0], color="black", linewidth=3, zorder=9)
    ax.text(x0 + 250, y0 + 40, "500 m", ha="center", zorder=9)
    north_arrow(ax, box)
    fig.colorbar(sc, ax=ax, shrink=0.6, label="walking distance to nearest park (m, capped at 1500)")
    handles, _ = ax.get_legend_handles_labels()
    if proposals is not None and len(proposals):
        handles.insert(0, Patch(facecolor="#2b6cb0", alpha=0.6, edgecolor="#1a4480",
                                label="proposed park (A, B)"))
    ax.legend(handles=handles, loc="upper right", framealpha=0.95)
    ax.set_axis_off()
    avoid = [hull.buffer(40)]
    if proposals is not None and len(proposals):
        avoid.append(union(proposals.geometry).buffer(40))
    label_streets(ax, streets, box, n_main=8, fontsize=8, avoid=avoid)
    ax.set_title("The pocket: walking distance to the nearest park, per building")
    fig.savefig(out / "map_pocket.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


# ----------------------------------------------------------------------------
# Plots
# ----------------------------------------------------------------------------
def make_plots(out, barrios, table, parks_big, play, pt, barrio, min_buildings):
    b = barrios.join(table[RANK_METRIC], on="barrio")
    fig, ax = plt.subplots(figsize=(11, 11))
    b.plot(column=RANK_METRIC, cmap="RdYlGn", vmin=0, vmax=100, legend=True, ax=ax,
           edgecolor="white", linewidth=0.3, missing_kwds={"color": "lightgrey"},
           legend_kwds={"label": "% of residents within 500 m walk of a park"})
    if len(parks_big):
        parks_big.plot(ax=ax, color="darkgreen", alpha=0.6)
    if len(play):
        pp = play.geometry.representative_point()
        ax.scatter(pp.x, pp.y, s=14, c="royalblue", label="playground", zorder=3)
    ax.plot(pt.x, pt.y, marker="*", color="black", ms=20, ls="", label="target", zorder=4)
    ax.legend(loc="lower left")
    ax.set_axis_off()
    ax.set_title("Walking access to parks, by barrio")
    fig.savefig(out / "map_park_access.png", dpi=170, bbox_inches="tight")
    plt.close(fig)

    # Many barrios tie at 0 % within 500 m, so rank by median walk instead and
    # print the 500 m share on each bar.
    t = (table[table["n_buildings"] >= min_buildings]
         .sort_values("park_median_m", ascending=False).head(20))
    fig, ax = plt.subplots(figsize=(9, 7))
    colors = ["crimson" if n == barrio else "steelblue" for n in t.index]
    ax.barh(t.index[::-1], t["park_median_m"][::-1], color=colors[::-1])
    for y, (med, pct) in enumerate(zip(t["park_median_m"][::-1], t[RANK_METRIC][::-1])):
        ax.text(med + 40, y, f"{pct:.0f} % within 500 m", va="center", fontsize=8)
    ax.set_xlim(0, t["park_median_m"].max() * 1.3)
    ax.set_xlabel("median walk to the nearest park (m)")
    ax.set_title("20 barrios with the longest walk to a park")
    fig.savefig(out / "ranking_worst20.png", dpi=170, bbox_inches="tight")
    plt.close(fig)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--place", default=DEFAULT_PLACE)
    p.add_argument("--address", default=DEFAULT_ADDRESS)
    p.add_argument("--latlon", nargs=2, type=float, metavar=("LAT", "LON"))
    p.add_argument("--barrios", help="municipal barrios layer (kmz/kml/shp/gpkg/geojson)")
    p.add_argument("--barrio-name-col")
    p.add_argument("--names-csv",
                   help="code list mapping barrio codes to names (e.g. ISTAC "
                        "CL_BARRIOS_ES70_2024 codes.csv)")
    p.add_argument("--parks-file", help="municipal parks layer (overrides OSM)")
    p.add_argument("--playgrounds-file", help="municipal playgrounds layer (overrides OSM)")
    p.add_argument("--pop-file", help="census-section polygons with a population column")
    p.add_argument("--pop-col", default="population")
    p.add_argument("--catastro",
                   help="Catastro INSPIRE buildings for the municipality: the "
                        "A.ES.SDGC.BU.<code>.zip, its folder, or its *.building.gml. "
                        "Replaces OSM buildings; weights each building by its dwellings")
    p.add_argument("--household-size", type=float, default=2.5,
                   help="people per dwelling when --catastro is used without --pop-file")
    p.add_argument("--min-park-area", type=float, default=DEFAULT_MIN_PARK_AREA)
    p.add_argument("--min-buildings", type=int, default=30,
                   help="barrios with fewer residential buildings are not ranked")
    p.add_argument("--pocket-threshold", type=float, default=750,
                   help="a building is under-served if both its nearest park and nearest "
                        "playground are farther than this on foot (m)")
    p.add_argument("--pocket-link", type=float, default=None,
                   help="buildings closer than this (m) are chained into one pocket "
                        "(default 80 with OSM buildings, 150 with --catastro)")
    p.add_argument("--proposal-file",
                   help="polygon(s) drawn by hand (e.g. in QGIS) of a proposed new park; "
                        "each is evaluated as if it were a public park")
    p.add_argument("--pocket-file",
                   help="polygon (e.g. drawn in QGIS) to use as the pocket instead")
    p.add_argument("--m2-per-resident", type=float, default=45,
                   help="gross residential floor area per resident, for the rough "
                        "resident estimate when no --pop-file is given")
    p.add_argument("--golf-max-dist", type=float, default=1500,
                   help="golf courses within this distance (m) of the pocket are used "
                        "in the what-if scenario")
    p.add_argument("--strip-widths", type=lambda s: [float(x) for x in s.split(",")],
                   default=[30.0, 60.0, 100.0, 150.0],
                   help="comma-separated widths (m) of the golf strip to test as a park, "
                        "measured from the course edge nearest the pocket")
    p.add_argument("--outdir", default="green_access_out")
    return p.parse_args()


def main():
    a = parse_args()
    out = Path(a.outdir)
    out.mkdir(exist_ok=True)

    place = ox.geocode_to_gdf(a.place).to_crs(CRS_M)
    boundary = union(place.geometry)
    area = boundary.buffer(EDGE_BUFFER_M)

    barrios = load_barrios(a, boundary)
    parks, golf = load_parks(a, area)
    play = load_playgrounds(a, area)
    print(f"{len(barrios)} barrios, {len(parks)} park polygons, {len(play)} playgrounds")

    net = Net(area)
    origins = residential_origins(boundary, net, a.pop_file, a.pop_col,
                                  catastro=a.catastro, household=a.household_size)

    # Playgrounds: computed once.
    play_sets = net.entry_nodes(play)
    dist_play = net.dist_field(play_sets)
    origins["d_play"] = origins["node"].map(dist_play).fillna(np.inf) + origins["snap_m"]

    # Parks: once for the chosen size, then a sensitivity sweep.
    def park_distances(min_area):
        big = parks[parks["area_m2"] >= min_area]
        sets = net.entry_nodes(big)
        field = net.dist_field(sets)
        return big, sets, origins["node"].map(field).fillna(np.inf) + origins["snap_m"]

    parks_big, parks_sets, origins["d_park"] = park_distances(a.min_park_area)

    inside = gpd.sjoin(origins, barrios, how="inner", predicate="within")
    inside = inside.drop(columns="index_right")
    inside = inside[~inside.index.duplicated()]  # overlapping barrio polygons
    table = summarise(inside, "d_park", "park").join(summarise(inside, "d_play", "playground").drop(columns="n_buildings"))
    table["rank_worst_park"] = rank_worst(table, RANK_METRIC, a.min_buildings)
    table["rank_worst_playground"] = rank_worst(table, "playground_pct_within_500m", a.min_buildings)
    table = table.sort_values(RANK_METRIC)

    sens = pd.DataFrame(index=table.index)
    for min_area in SENSITIVITY_AREAS:
        _, _, d = park_distances(min_area)
        tmp = inside.assign(d_tmp=d.loc[inside.index].to_numpy())
        s = summarise(tmp, "d_tmp", "park")
        sens[f"min_{int(min_area)}m2"] = rank_worst(s, RANK_METRIC, a.min_buildings)

    table.round(2).to_csv(out / "barrio_access.csv")
    sens.to_csv(out / "sensitivity_ranks.csv")

    report, (pt, barrio) = target_report(
        a, net, origins, barrios, parks_big, parks_sets, play, play_sets,
        "d_park", "d_play", table, sens, a.min_park_area)
    (out / "target_report.txt").write_text(report, encoding="utf-8")
    print("\n" + report)

    make_plots(out, barrios, table, parks_big, play, pt, barrio, a.min_buildings)

    pocket_txt, hull, golf_near, strips, extras = pocket_analysis(
        a, net, origins, parks_big, play, golf, pt)
    (out / "pocket_report.txt").write_text(pocket_txt, encoding="utf-8")
    print("\n" + pocket_txt)
    streets = street_edges(net)
    footprints = building_footprints(a.catastro) if a.catastro else None
    target_label = "target" if a.latlon else a.address.split(",")[0]
    plot_pocket(out, origins, hull, parks_big, play, golf_near, pt, extras.get("proposals"),
                streets, footprints, target_label)
    plot_proposals(out, origins, hull, golf_near, extras, pt, streets, footprints, target_label)

    gpkg = out / "layers.gpkg"
    barrios.join(table, on="barrio").to_file(gpkg, layer="barrios", driver="GPKG")
    keep = [c for c in ("name", "area_m2", "geometry") if c in parks_big.columns]
    parks_big[keep].to_file(gpkg, layer="parks", driver="GPKG")
    play[[c for c in ("name", "geometry") if c in play.columns]].to_file(gpkg, layer="playgrounds", driver="GPKG")
    bcols = [c for c in ("floor_m2", "dwellings", "weight", "d_park", "crow_park", "detour_park",
                         "d_play", "crow_play", "nearest_any", "in_pocket", "d_golf",
                         "d_park_if_golf_open", "geometry") if c in origins.columns]
    origins[bcols].replace([np.inf, -np.inf], np.nan).to_file(gpkg, layer="buildings", driver="GPKG")
    if hull is not None:
        gpd.GeoDataFrame(geometry=[hull], crs=CRS_M).to_file(gpkg, layer="pocket", driver="GPKG")
        # Streets and building outlines around the pocket, for other maps (analysis/site_map.py).
        around = gpd.GeoDataFrame(geometry=[rect(*hull.buffer(1500).bounds)], crs=CRS_M)
        streets.clip(around).to_file(gpkg, layer="streets", driver="GPKG")
        if footprints is not None:
            footprints.clip(around).to_file(gpkg, layer="footprints", driver="GPKG")
    if len(golf_near):
        gk = [c for c in ("name", "area_m2", "geometry") if c in golf_near.columns]
        golf_near[gk].to_file(gpkg, layer="golf", driver="GPKG")
    if strips is not None and len(strips):
        strips.to_file(gpkg, layer="golf_strips", driver="GPKG")
    if extras.get("proposals") is not None and len(extras["proposals"]):
        extras["proposals"].to_file(gpkg, layer="proposals", driver="GPKG")
    if len(extras["entrances"]):
        extras["entrances"].drop(columns="node").to_file(gpkg, layer="entrances", driver="GPKG")

    print(f"\nWrote results to {out}/")
    print("\nREAD BEFORE USING THESE NUMBERS:\n"
          "  - OSM is incomplete (playgrounds especially). Check the target's surroundings on\n"
          "    site or in street-level imagery, and swap in the municipal layers if you can.\n"
          "  - Without --pop-file, 'residents' means residential floor area, not padron data.\n"
          "  - Distances are to the park edge and so slightly flatter the city than reality.\n"
          "  - Report the sensitivity table, not only the most favourable threshold.")


if __name__ == "__main__":
    main()
