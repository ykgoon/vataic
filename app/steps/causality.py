"""Causal propagation: turn a direct factor movement into downstream effects.

The projection engine records a causal graph (source -> target, signed
strength, delay in days) and then has to *use* it. Without propagation a factor
moves only when a development names it, so every outcome is a direct consequence
and nothing is ever second- or third-order.

This module is the missing step. A development supplies a direct delta on the
factors it names; `propagate` pushes that delta along the graph, one hop at a
time. Hop 0 is the factor the development named, hop 1 is what it pushes, hop 2
is what that pushes in turn. Those hop numbers are the definition of
`outcomes."order"` (migrations/008): 1st order = direct, 2nd = downstream, 3rd
= further downstream.

Deterministic and dependency-free so it runs identically in heuristic mode.
"""
import json

# A contribution smaller than this is rounding noise, not an effect.
MIN_INDUCED_DELTA = 0.5
# Propagation stops here: hop 3 is the deepest order the product names.
MAX_HOPS = 3
# Order of effect is reported as 1st/2nd/3rd (migrations/008); deeper hops are
# real but are folded into "3rd order" rather than inventing a 4th.
MAX_ORDER = 3
# One induced push cannot move a factor further than this in a single step, so
# a high-strength edge across a wide fan cannot fling factors to the extremes.
MAX_INDUCED_DELTA = 25.0


def load_edges(scenario_id: str, db_path=None) -> list[dict]:
    """The stored causal edges for a scenario, newest graph first.

    Returns [] when no graph exists (a recovered scenario, or a run that died
    before the causal phase), in which case propagation is simply a no-op.
    """
    from app.db import get_conn
    with get_conn(db_path) as c:
        r = c.execute(
            "SELECT edges FROM causal_graphs WHERE scenario_id=? ORDER BY id DESC LIMIT 1",
            (scenario_id,)).fetchone()
    if not r or not r["edges"]:
        return []
    try:
        edges = json.loads(r["edges"])
    except Exception:
        return []
    return [e for e in edges if isinstance(e, dict)
            and e.get("source") and e.get("target")]


def _outgoing(edges: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in edges:
        out.setdefault(e["source"], []).append(e)
    return out


def propagate(seed: dict, edges: list[dict], max_hops: int = MAX_HOPS) -> list[dict]:
    """Push `seed` deltas (factor -> delta) along `edges`.

    Returns one entry per (hop, factor) the cascade reaches:
    {"factor", "delta", "hop", "delay_days"}.

    Hooked to real semantics: an inverse edge (negative strength) pushes the
    target the other way, a weak edge pushes less, and `delay_days` accumulates
    along the path so a second-order effect is visibly later than its cause.
    """
    if not seed or not edges:
        return []
    by_source = _outgoing(edges)
    frontier: list[tuple[str, float, int]] = [
        (fid, float(d), 0) for fid, d in seed.items() if abs(float(d)) >= MIN_INDUCED_DELTA]
    applied: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for hop in range(1, max_hops + 1):
        arrivals: dict[str, list[tuple[float, int]]] = {}
        for src, delta, day in frontier:
            for e in by_source.get(src, []):
                try:
                    strength = float(e.get("strength") or 0)
                except (TypeError, ValueError):
                    continue
                if strength == 0:
                    continue
                try:
                    delay = int(e.get("delay_days") or 0)
                except (TypeError, ValueError):
                    delay = 0
                arrivals.setdefault(e["target"], []).append(
                    (strength * delta, day + max(0, delay)))
        if not arrivals:
            break
        nxt: list[tuple[str, float, int]] = []
        for target, pushes in arrivals.items():
            if (hop, target) in seen:
                continue
            seen.add((hop, target))
            total = sum(p for p, _ in pushes)
            total = max(-MAX_INDUCED_DELTA, min(MAX_INDUCED_DELTA, total))
            if abs(total) < MIN_INDUCED_DELTA:
                continue
            latest = max(d for _, d in pushes)
            applied.append({"factor": target, "delta": round(total, 3),
                            "hop": hop, "delay_days": latest})
            nxt.append((target, total, latest))
        if not nxt:
            break
        frontier = nxt
    return applied


def hop_depths(edges: list[dict], roots: list[str] | None = None) -> dict[str, int]:
    """Longest hop distance from a root factor to every reachable factor.

    Roots default to nodes that no edge points at: the top of the graph, which
    is where a decision lands first. Used to explain *why* a factor sits at a
    given order rather than only reporting the number.
    """
    if not edges:
        return {}
    targets = {e["target"] for e in edges}
    nodes = {e["source"] for e in edges} | targets
    if roots is None:
        roots = sorted(nodes - targets) or sorted(nodes)
    depth, frontier = {r: 0 for r in roots}, list(roots)
    by_source = _outgoing(edges)
    for _ in range(MAX_HOPS):
        nxt: list[str] = []
        for src in frontier:
            for e in by_source.get(src, []):
                t = e["target"]
                d = depth.get(src, 0) + 1
                if d <= MAX_HOPS and d > depth.get(t, -1):
                    depth[t] = d
                    nxt.append(t)
        if not nxt:
            break
        frontier = nxt
    return depth


def apply_cascade(factor_state: dict, seed: dict, edges: list[dict],
                  max_hops: int = MAX_HOPS) -> tuple[dict, dict[str, int], list[dict]]:
    """Apply direct deltas then their cascade to a factor state.

    Returns (new_state, hops_by_factor, induced). `hops_by_factor` records 0 for
    directly-named factors and 1..3 for induced ones, which is what makes an
    outcome's order-of-effect a measurement rather than a label.

    The hop map alone is not enough to decide an outcome's order: every outcome
    contains both directly-named and induced factors, so the shallowest hop is
    always 0. What separates a first-order future from a downstream one is where
    the *movement* came from, so `hop_mass()` reports the absolute delta each
    hop contributed.
    """
    state = {k: float(v) for k, v in factor_state.items()}
    hops: dict[str, int] = {}
    for fid, d in seed.items():
        if fid not in state:
            continue
        state[fid] = max(0.0, min(100.0, state[fid] + float(d)))
        hops[fid] = 0
    induced = propagate(seed, edges, max_hops)
    for item in induced:
        fid = item["factor"]
        if fid not in state:
            continue
        state[fid] = max(0.0, min(100.0, state[fid] + item["delta"]))
        # A factor can be named directly and also pushed downstream; the direct
        # reason is the one worth reporting.
        hops.setdefault(fid, item["hop"])
    return state, hops, induced


def split_seed(factor_ids, deltas: dict) -> dict:
    """Filter a raw development delta to known factors with a usable magnitude."""
    out = {}
    for k, v in (deltas or {}).items():
        if k not in factor_ids:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if abs(v) < MIN_INDUCED_DELTA:
            continue
        out[k] = v
    return out


def hop_mass(seed: dict, induced: list[dict]) -> dict[str, float]:
    """Absolute movement contributed by each hop in one step.

    Hop 0 is the delta the development named; hop 1..3 are the cascade. An
    outcome whose movement is mostly at hop 0 is a direct consequence of the
    decision; one whose movement is mostly at hop >= 1 is downstream of it.
    """
    mass: dict[str, float] = {}
    for d in seed.values():
        mass["0"] = mass.get("0", 0.0) + abs(float(d))
    for item in induced:
        k = str(item["hop"])
        mass[k] = mass.get(k, 0.0) + abs(float(item["delta"]))
    return {k: round(v, 3) for k, v in mass.items() if v > 0}
