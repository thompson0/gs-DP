import csv
import gzip
import json
import math
import os
import time
import urllib.parse
import urllib.request
from collections import namedtuple, defaultdict
from functools import lru_cache

from routing import haversine_m

W_DENSITY = 1.0
MIN_EDGE_M = 20
LIGHT_PENALTY_NIGHT = 1.5
LIGHT_PENALTY_DAY = 0.2
LIGHT_UNKNOWN_NIGHT = 0.3
ISOLATED_WAYS = {"footway", "path", "steps"}
WAY_PENALTY = 0.5
NOT_ISOLATED_FOOTWAYS = {"sidewalk", "crossing"}
CRIME_SNAP_M = 30
SAFE_SNAP_M = 150

CRIMES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "crimes_sp.csv.gz")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
USER_AGENT = "ChegaBem/1.0 (FIAP student project)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OVERPASS_TIMEOUT_S = 60
WALKABLE = ("primary|secondary|tertiary|unclassified|residential|living_street|pedestrian|"
            "footway|path|steps|service|primary_link|secondary_link|tertiary_link")

Edge = namedtuple("Edge", "length_m risk_day risk_night crimes_day crimes_night lit highway name geometry")
City = namedtuple("City", "nodes graph safe_points crimes")


def _http(url, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


_last_nominatim = [0.0]


@lru_cache(maxsize=256)
def geocode(query):
    q = query.strip()
    if not q:
        return None
    low = q.lower()
    if "são paulo" not in low and "sao paulo" not in low:
        q += ", São Paulo"
    wait = 1.0 - (time.time() - _last_nominatim[0])
    if wait > 0:
        time.sleep(wait)
    _last_nominatim[0] = time.time()
    params = urllib.parse.urlencode({"format": "json", "limit": 1, "countrycodes": "br", "q": q})
    res = _http(f"{NOMINATIM_URL}?{params}")
    if not res:
        return None
    return float(res[0]["lat"]), float(res[0]["lon"]), res[0].get("display_name", q)


def bbox_around(lat1, lon1, lat2, lon2, margin_m):
    dlat = margin_m / 111320
    dlon = margin_m / (111320 * math.cos(math.radians((lat1 + lat2) / 2)))
    s, n = min(lat1, lat2) - dlat, max(lat1, lat2) + dlat
    w, e = min(lon1, lon2) - dlon, max(lon1, lon2) + dlon
    return (math.floor(s * 1000) / 1000, math.floor(w * 1000) / 1000,
            math.ceil(n * 1000) / 1000, math.ceil(e * 1000) / 1000)


def overpass_query(bbox):
    b = "%.3f,%.3f,%.3f,%.3f" % bbox
    return f"""[out:json][timeout:{OVERPASS_TIMEOUT_S}];
way["highway"~"^({WALKABLE})$"]["foot"!="no"]["access"!="private"]({b});
out geom;
(
  nwr["amenity"="police"]({b});
  nwr["amenity"="hospital"]({b});
  nwr["amenity"="pharmacy"]["opening_hours"="24/7"]({b});
  nwr["railway"="station"]({b});
  nwr["public_transport"="station"]({b});
);
out center;"""


def fetch_osm(bbox):
    path = os.path.join(CACHE_DIR, "osm_%.3f_%.3f_%.3f_%.3f.json" % bbox)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    body = urllib.parse.urlencode({"data": overpass_query(bbox)}).encode()
    errors = []
    for url in OVERPASS_URLS:
        try:
            data = _http(url, body, timeout=OVERPASS_TIMEOUT_S + 30)
            if "runtime error" in data.get("remark", ""):
                raise RuntimeError(data["remark"])
            break
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    else:
        raise RuntimeError("Overpass indisponível: " + " | ".join(errors))
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


@lru_cache(maxsize=1)
def all_crimes(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return [(float(r["lat"]), float(r["lon"]), r["period"]) for r in csv.DictReader(f)]


def load_crimes(bbox):
    s, w, n, e = bbox
    return [c for c in all_crimes(CRIMES_PATH) if s <= c[0] <= n and w <= c[1] <= e]


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def assign_crimes(geoms, crimes, max_m=CRIME_SNAP_M):
    counts = [[0, 0] for _ in geoms]
    if not geoms or not crimes:
        return counts
    lat0, lon0 = geoms[0][0]
    kx, ky = 111320 * math.cos(math.radians(lat0)), 111320

    def xy(lat, lon):
        return (lon - lon0) * kx, (lat - lat0) * ky

    cell = float(max_m)
    grid = defaultdict(list)
    for i, g in enumerate(geoms):
        pts = [xy(*p) for p in g]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            for cx in range(int(math.floor(min(ax, bx) / cell)), int(math.floor(max(ax, bx) / cell)) + 1):
                for cy in range(int(math.floor(min(ay, by) / cell)), int(math.floor(max(ay, by) / cell)) + 1):
                    grid[(cx, cy)].append((i, ax, ay, bx, by))

    for lat, lon, period in crimes:
        px, py = xy(lat, lon)
        cx, cy = int(math.floor(px / cell)), int(math.floor(py / cell))
        best, best_d = None, max_m
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for i, ax, ay, bx, by in grid.get((gx, gy), ()):
                    d = _seg_dist(px, py, ax, ay, bx, by)
                    if d <= best_d:
                        best, best_d = i, d
        if best is not None:
            if period != "night":
                counts[best][0] += 1
            if period != "day":
                counts[best][1] += 1
    return counts


def edge_risk(length_m, crimes, lit, isolated, night):
    density = crimes / (max(length_m, MIN_EDGE_M) / 100)
    if lit == "no":
        light = LIGHT_PENALTY_NIGHT if night else LIGHT_PENALTY_DAY
    else:
        light = LIGHT_UNKNOWN_NIGHT if night and lit is None else 0.0
    way = WAY_PENALTY if isolated else 0.0
    return 1 + W_DENSITY * math.log1p(density) + light + way


def _split_ways(ways):
    uses = defaultdict(int)
    for w in ways:
        for nid in w["nodes"]:
            uses[nid] += 1
    raw = []
    for w in ways:
        ids, geom = w["nodes"], [(p["lat"], p["lon"]) for p in w["geometry"]]
        start = 0
        for i in range(1, len(ids)):
            if uses[ids[i]] >= 2 or i == len(ids) - 1:
                if ids[start] != ids[i]:
                    raw.append((ids[start], ids[i], geom[start:i + 1], w.get("tags", {})))
                start = i
    return raw


def _largest_component(raw):
    adj = defaultdict(list)
    for u, v, _, _ in raw:
        adj[u].append(v)
        adj[v].append(u)
    seen, best = set(), set()
    for s in adj:
        if s in seen:
            continue
        comp, stack = {s}, [s]
        while stack:
            for nb in adj[stack.pop()]:
                if nb not in comp:
                    comp.add(nb)
                    stack.append(nb)
        seen |= comp
        if len(comp) > len(best):
            best = comp
    return [r for r in raw if r[0] in best]


def _poi_label(tags):
    a = tags.get("amenity")
    if a == "police":
        kind = "Delegacia"
    elif a == "hospital":
        kind = "Hospital"
    elif a == "pharmacy" and tags.get("opening_hours") == "24/7":
        kind = "Farmácia 24h"
    elif tags.get("railway") == "station" or tags.get("public_transport") == "station":
        kind = "Estação"
    else:
        return None
    name = tags.get("name") or tags.get("brand")
    return f"{kind}: {name}" if name else kind


def path_m(geometry):
    return sum(haversine_m(a, b) for a, b in zip(geometry, geometry[1:]))


def nearest_node(nodes, lat, lon):
    k = math.cos(math.radians(lat)) ** 2
    return min(nodes, key=lambda n: (nodes[n][0] - lat) ** 2 + k * (nodes[n][1] - lon) ** 2)


def city_from_osm(data, crimes):
    elements = data.get("elements", [])
    ways = [e for e in elements if e["type"] == "way" and "geometry" in e and "highway" in e.get("tags", {})]
    raw = _largest_component(_split_ways(ways))

    nodes = {}
    for u, v, g, _ in raw:
        nodes[u], nodes[v] = g[0], g[-1]
    geoms = [g for _, _, g, _ in raw]
    counts = assign_crimes(geoms, crimes)

    graph = {n: [] for n in nodes}

    def add(a, b, e):
        graph[a].append((b, e))
        graph[b].append((a, e))

    next_id = -1
    for (u, v, g, tags), (cd, cn) in zip(raw, counts):
        length = path_m(g)
        lit, hw = tags.get("lit"), tags.get("highway")
        iso = hw in ISOLATED_WAYS and tags.get("footway") not in NOT_ISOLATED_FOOTWAYS
        e = Edge(length, edge_risk(length, cd, lit, iso, False), edge_risk(length, cn, lit, iso, True),
                 cd, cn, lit, hw, tags.get("name", ""), g)
        if all(nb != v for nb, _ in graph[u]):
            add(u, v, e)
            continue
        if len(g) == 2:
            g = [g[0], ((g[0][0] + g[1][0]) / 2, (g[0][1] + g[1][1]) / 2), g[1]]
        mid, m = len(g) // 2, next_id
        next_id -= 1
        nodes[m], graph[m] = g[mid], []
        for a, b, part, share in ((u, m, g[:mid + 1], 1), (m, v, g[mid:], 0)):
            add(a, b, e._replace(length_m=path_m(part), geometry=part,
                                 crimes_day=cd // 2 + share * (cd % 2), crimes_night=cn // 2 + share * (cn % 2)))

    safe = {}
    for el in elements:
        label = _poi_label(el.get("tags", {}))
        pt = el if "lat" in el else el.get("center")
        if label is None or pt is None or not nodes:
            continue
        pos = (pt["lat"], pt["lon"])
        n = nearest_node(nodes, *pos)
        if haversine_m(pos, nodes[n]) > SAFE_SNAP_M:
            continue
        if n not in safe:
            safe[n] = label
        elif label not in safe[n]:
            safe[n] += " / " + label
    return City(nodes, graph, safe, crimes)


def build_city(lat1, lon1, lat2, lon2, margin_m=400):
    bbox = bbox_around(lat1, lon1, lat2, lon2, margin_m)
    crimes = load_crimes(bbox)
    return city_from_osm(fetch_osm(bbox), crimes)
