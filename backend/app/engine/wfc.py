from collections import deque
from itertools import combinations
from math import log
from random import Random

from backend.app.config.missions import MISSIONS, SEGMENT_TEMPLATES
from backend.app.content import CATALOG

DIRS = {"N": (0, -1, "S"), "S": (0, 1, "N"), "E": (1, 0, "W"), "W": (-1, 0, "E")}
EMPTY = "EMPTY"
GENERATOR_VERSION = "wfc-3"
MAX_ATTEMPTS = 400


class GenerationError(RuntimeError):
    def __init__(self, mission_id, requested_seed, diagnostics):
        self.diagnostics = diagnostics
        super().__init__(f"Generation failed for {mission_id}, requested seed {requested_seed}, "
                         f"{len(diagnostics)} bounded attempts: {diagnostics[-5:]}")


def _placement_key(placement):
    return (*placement[:3], tuple(sorted(placement[3])))


def _footprint(pl):
    tid, ax, ay, _ = pl
    if tid == EMPTY:
        return [(ax, ay)]
    t = SEGMENT_TEMPLATES[tid]
    return [(ax + cx, ay + cy) for cx in range(t["grid_w"]) for cy in range(t["grid_h"])]


def _edge(pl, cell, d):
    if pl[0] == EMPTY:
        return "WALL"
    cx, cy = cell[0] - pl[1], cell[1] - pl[2]
    return "CONN" if (cx, cy, d) in pl[3] else "WALL"


def _status(cell, d, domains):
    dom = domains.get(cell)
    if not dom:
        return None
    statuses = {_edge(p, cell, d) for p in dom}
    return statuses.pop() if len(statuses) == 1 else None


def _propagate(start_cells, domains, assigned):
    queue = deque(start_cells)
    while queue:
        cell = queue.popleft()
        for d, (dx, dy, opp) in DIRS.items():
            nb = (cell[0] + dx, cell[1] + dy)
            if nb not in domains:
                continue
            st = _status(cell, d, domains)
            if st is None:
                continue
            new_dom = {p for p in domains[nb] if _edge(p, nb, opp) == st}
            if new_dom != domains[nb]:
                if not new_dom:
                    return False
                domains[nb] = new_dom
                queue.append(nb)
    return True


def _assign(pl, domains, assigned):
    fp = set(_footprint(pl))
    for cell in fp:
        if cell in assigned and assigned[cell] != pl:
            return False
    # Remove any candidate in other cells whose footprint overlaps `fp`
    touched = sorted(fp)
    for cell, dom in list(domains.items()):
        if cell in fp:
            assigned[cell] = pl
            domains[cell] = {pl}
        else:
            filtered = {cand for cand in dom if not (set(_footprint(cand)) & fp)}
            if filtered != dom:
                if not filtered:
                    return False
                domains[cell] = filtered
                touched.append(cell)
    return _propagate(touched, domains, assigned)


def _valid_placements(tid, ax, ay, gw, gh):
    t = SEGMENT_TEMPLATES[tid]
    in_bounds_ports = []
    for p in t["connector_ports"]:
        gx, gy, d = ax + p["cell_x"], ay + p["cell_y"], p["dir"]
        dx, dy, _ = DIRS[d]
        if 0 <= gx + dx < gw and 0 <= gy + dy < gh:
            in_bounds_ports.append((p["cell_x"], p["cell_y"], d))
    if not in_bounds_ports:
        return []
    if t["role"] in ("start", "end"):
        sizes = [1, 2]
    else:
        sizes = [1, 2, 3]
    res = []
    for k in sizes:
        if k <= len(in_bounds_ports):
            for comb in combinations(in_bounds_ports, k):
                res.append((tid, ax, ay, frozenset(comb)))
    return res


def _try_seed(mission_id, seed, diagnostics=None):
    mission = MISSIONS[mission_id]
    rng = Random(seed)
    gw, gh = mission["grid_w"], mission["grid_h"]
    def reject(reason):
        if diagnostics is not None:
            diagnostics.append({"seed": seed, "reason": reason})
        return None

    allowed = sorted(t for t in mission["allowed_templates"] if SEGMENT_TEMPLATES[t]["role"] == "room")
    start_tid = next(t for t in mission["allowed_templates"] if SEGMENT_TEMPLATES[t]["role"] == "start")
    end_tid = next(t for t in mission["allowed_templates"] if SEGMENT_TEMPLATES[t]["role"] == "end")
    domains = {(x, y): set() for x in range(gw) for y in range(gh)}

    for tid in allowed:
        t = SEGMENT_TEMPLATES[tid]
        for ax in range(gw - t["grid_w"] + 1):
            for ay in range(gh - t["grid_h"] + 1):
                for pl in _valid_placements(tid, ax, ay, gw, gh):
                    for cell in _footprint(pl):
                        domains[cell].add(pl)
    for cell in domains:
        domains[cell].add((EMPTY, cell[0], cell[1], frozenset()))

    assigned = {}
    start_tpl, end_tpl = SEGMENT_TEMPLATES[start_tid], SEGMENT_TEMPLATES[end_tid]
    start_cands = _valid_placements(start_tid, 0, rng.randrange(gh - start_tpl["grid_h"] + 1), gw, gh)
    end_cands = _valid_placements(end_tid, gw - end_tpl["grid_w"], rng.randrange(gh - end_tpl["grid_h"] + 1), gw, gh)
    if not start_cands or not end_cands:
        return reject("no endpoint placements")
    start = rng.choice([c for c in start_cands if len(c[3]) == 1] or start_cands)
    end = rng.choice([c for c in end_cands if len(c[3]) == 1] or end_cands)
    for cell in _footprint(start):
        domains[cell].add(start)
    for cell in _footprint(end):
        domains[cell].add(end)

    if not _assign(start, domains, assigned) or not _assign(end, domains, assigned):
        return reject("endpoint contradiction")
    placed = [start, end]

    while True:
        best_cell, best_score = None, float("inf")
        for cell, dom in domains.items():
            if cell in assigned:
                continue
            non_empty = [p for p in dom if p[0] != EMPTY]
            if not non_empty:
                continue
            open_conn = any(
                _status((cell[0] + dx, cell[1] + dy), opp, domains) == "CONN"
                for d, (dx, dy, opp) in DIRS.items()
                if (cell[0] + dx, cell[1] + dy) in domains
            )
            n = len(non_empty)
            entropy = -sum((1.0 / n) * log(1.0 / n) for _ in range(n)) if n > 1 else 0.0
            score = entropy - (10.0 if open_conn else 0.0) + rng.random() * 0.05
            if score < best_score:
                best_cell, best_score = cell, score

        if best_cell is None or len(placed) >= mission["max_segments"]:
            break

        cands = sorted((p for p in domains[best_cell] if p[0] != EMPTY), key=_placement_key)
        if not cands:
            break
        # Prefer multi-cell segments when we haven't placed enough yet
        weights = [
            (3.0 if SEGMENT_TEMPLATES[p[0]]["grid_w"] * SEGMENT_TEMPLATES[p[0]]["grid_h"] > 1 else 1.0)
            * (4.0 if p[0] in set(mission["required_templates"]) - {pl[0] for pl in placed} else 1.0)
            for p in cands
        ]
        choice = rng.choices(cands, weights=weights, k=1)[0]
        if not _assign(choice, domains, assigned):
            return reject("placement contradiction")
        placed.append(choice)

    # Collapse any remaining unassigned cells to EMPTY if possible
    for cell in list(domains.keys()):
        if cell not in assigned:
            emp = (EMPTY, cell[0], cell[1], frozenset())
            if emp in domains[cell] and _assign(emp, domains, assigned):
                pass
            else:
                cands = sorted(domains[cell], key=_placement_key)
                if not cands or not _assign(rng.choice(cands), domains, assigned):
                    return reject("remaining-cell contradiction")
                if assigned[cell][0] != EMPTY and assigned[cell] not in placed:
                    placed.append(assigned[cell])

    # Build segment adjacency graph over matched CONN edges
    seg_idx_map = {pl: i for i, pl in enumerate(placed)}
    adj = {i: [] for i in range(len(placed))}
    for cell, pl in assigned.items():
        if pl[0] == EMPTY:
            continue
        for d, (dx, dy, opp) in DIRS.items():
            nb = (cell[0] + dx, cell[1] + dy)
            if nb in assigned and assigned[nb][0] != EMPTY and assigned[nb] != pl:
                if _edge(pl, cell, d) == "CONN" and _edge(assigned[nb], nb, opp) == "CONN":
                    u, v = seg_idx_map[pl], seg_idx_map[assigned[nb]]
                    adj[u].append((v, cell, d, nb, opp))

    start_i, end_i = seg_idx_map[start], seg_idx_map[end]
    parent = {start_i: None}
    bfs_order = [start_i]
    q = deque([start_i])
    while q:
        u = q.popleft()
        for v, cell, d, nb, opp in adj[u]:
            if v not in parent:
                parent[v] = (u, cell, d, nb, opp)
                bfs_order.append(v)
                q.append(v)

    if end_i not in parent or len(bfs_order) != len(placed):
        return reject("disconnected final layout")
    if not mission["min_segments"] <= len(placed) <= mission["max_segments"]:
        return reject(f"final segment count {len(placed)} outside {mission['min_segments']}..{mission['max_segments']}")
    if not set(mission["required_templates"]).issubset(p[0] for p in placed):
        return reject("missing required templates")

    # Preserve display ordering while exposing authoritative role indices.
    mid_indices = [i for i in bfs_order if i not in (start_i, end_i)]
    ordered_old = [start_i] + mid_indices + [end_i]
    remap = {old_i: new_i for new_i, old_i in enumerate(ordered_old)}

    # Check that we have multiple segment sizes
    sizes = {
        (SEGMENT_TEMPLATES[placed[old_i][0]]["grid_w"], SEGMENT_TEMPLATES[placed[old_i][0]]["grid_h"])
        for old_i in ordered_old
    }
    if len(sizes) < 2:
        return reject("requires multiple segment sizes")

    connectors = []
    for old_v in ordered_old:
        if parent[old_v] is not None:
            old_u, cell, d, nb, opp = parent[old_v]
            connectors.append(
                {
                    "from_seg": remap[old_u],
                    "to_seg": remap[old_v],
                    "gx": cell[0],
                    "gy": cell[1],
                    "dir": d,
                    "nx": nb[0],
                    "ny": nb[1],
                    "opp_dir": opp,
                }
            )

    seg_list = []
    for new_i, old_i in enumerate(ordered_old):
        tid, ax, ay, _ = placed[old_i]
        seg_list.append(
            {
                "id": tid,
                "role": SEGMENT_TEMPLATES[tid]["role"],
                "index": new_i,
                "ax": ax,
                "ay": ay,
                "cells": _footprint(placed[old_i]),
            }
        )

    return {
        "mission_id": mission_id,
        "seed": seed,
        "resolved_seed": seed,
        "start_segment_index": remap[start_i],
        "end_segment_index": remap[end_i],
        "grid_w": gw,
        "grid_h": gh,
        "segments": seg_list,
        "connectors": connectors,
    }


def run_wfc(mission_id: str, seed: int = 42, *, max_attempts: int = MAX_ATTEMPTS) -> dict:
    if mission_id not in MISSIONS or type(seed) is not int:
        raise ValueError("Known mission and integer seed required")
    if not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError("Invalid generation attempt bound")
    diagnostics = []
    for attempt in range(max_attempts):
        result = _try_seed(mission_id, seed + attempt * 17, diagnostics)
        if result:
            result.update(requested_seed=seed, generator_version=GENERATOR_VERSION,
                          content_version=CATALOG.definitions["content_version"],
                          generation_diagnostics=diagnostics, generation_attempts=attempt + 1)
            return result
    raise GenerationError(mission_id, seed, diagnostics)
