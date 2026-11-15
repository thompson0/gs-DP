import heapq
import math
import sys
from functools import lru_cache

INF = math.inf
EARTH_RADIUS_M = 6_371_000


def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def dijkstra(graph, src, dst, weight):
    if src == dst:
        return 0.0, [src]
    dist = {src: 0.0}
    came_from = {}
    heap = [(0.0, src)]
    while heap:
        cost, node = heapq.heappop(heap)
        if cost > dist[node]:
            continue
        if node == dst:
            break
        for neighbor, edge in graph.get(node, []):
            new_cost = cost + weight(edge)
            if new_cost < dist.get(neighbor, INF):
                dist[neighbor] = new_cost
                came_from[neighbor] = node
                heapq.heappush(heap, (new_cost, neighbor))
    if dst not in dist:
        return None
    path = [dst]
    while path[-1] != src:
        path.append(came_from[path[-1]])
    return dist[dst], path[::-1]


def safest_within(graph, nodes, src, dst, max_m, weight, unit_m=10):
    budget = int(max_m // unit_m)
    to_dst = {node: haversine_m(pos, nodes[dst]) for node, pos in nodes.items()}
    sys.setrecursionlimit(max(sys.getrecursionlimit(), budget + 1000))

    @lru_cache(maxsize=None)
    def best(node, units_left):
        if node == dst:
            return 0.0, None, 0
        if to_dst[node] > units_left * unit_m:
            return INF, None, 0
        best_cost, best_next, best_units = INF, None, 0
        for neighbor, edge in graph.get(node, []):
            units = max(1, math.ceil(edge.length_m / unit_m))
            if units > units_left:
                continue
            cost = weight(edge) + best(neighbor, units_left - units)[0]
            if cost < best_cost:
                best_cost, best_next, best_units = cost, neighbor, units
        return best_cost, best_next, best_units

    cost = best(src, budget)[0]
    if cost == INF:
        return None
    path, units_left = [src], budget
    while path[-1] != dst:
        _, nxt, spent = best(path[-1], units_left)
        path.append(nxt)
        units_left -= spent
    return cost, path, best.cache_info()


def nearest_safe_table(graph, safe_nodes, weight):
    safe = set(safe_nodes)
    prev = {v: (0.0, None, v) if v in safe else (INF, None, None) for v in graph}
    rounds = 0
    for rounds in range(1, len(graph) + 1):
        cur = dict(prev)
        for v in graph:
            for u, edge in graph[v]:
                cost = prev[u][0] + weight(edge)
                if cost < cur[v][0]:
                    cur[v] = (cost, u, prev[u][2])
        if cur == prev:
            break
        prev = cur
    table = {v: entry for v, entry in prev.items() if entry[0] < INF}
    return table, rounds


def path_to_safe(table, node):
    path = [node]
    while table[path[-1]][1] is not None:
        path.append(table[path[-1]][1])
    return path


def path_length_m(graph, path):
    return sum(min(e.length_m for n, e in graph[a] if n == b) for a, b in zip(path, path[1:]))
