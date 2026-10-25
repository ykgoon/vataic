"""Server-rendered SVG for the futures view: per-factor fan chart and the
futures map. Pure string templating, no client-side JS, per the current
architecture (openspec 2026-10-25-futures-output, web-ui delta)."""
import html
import re

_JUNK_TIME_RE = re.compile(r"^step-?\d+$", re.IGNORECASE)
BAND_COLORS = {
    "dominant": "#2563eb",
    "plausible": "#d97706",
    "alternative": "#059669",
    "tail": "#7c3aed",
}


def band_color(band: str) -> str:
    return BAND_COLORS.get((band or "tail").lower(), "#64748b")


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _fmt(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _x_labels(max_step: int, time_labels: dict, human: bool, n_ticks=6):
    """Decimated x-axis labels: human time labels when available."""
    junk = _JUNK_TIME_RE
    ticks = sorted({round(k * max_step / n_ticks) for k in range(n_ticks + 1)})
    out = []
    for t in ticks:
        label = ""
        if human:
            cand = time_labels.get(t) or ""
            if cand and not junk.match(cand):
                label = cand
        if not label:
            label = "now" if t == 0 else f"step {t}"
        out.append((t, label))
    return out


def fan_svg(fv: dict, factor_id: str, factor_name: str) -> tuple[str, list[str]]:
    """Per-factor fan chart: one mean line + min/max band per outcome."""
    w, h, l, r, t, b = 1000, 340, 60, 20, 12, 45
    outcomes = [o for o in fv.get("outcomes", []) if factor_id in (o.get("series") or {})]
    max_step = 1
    for o in outcomes:
        s = o["series"][factor_id]
        if s.get("steps"):
            max_step = max(max_step, s["steps"][-1])

    def X(i):
        return l + (i / max_step) * (w - l - r)

    def Y(v):
        return t + (100.0 - v) / 100.0 * (h - t - b)

    parts = [f"<svg viewBox='0 0 {w} {h}' width='100%' role='img' "
             f"aria-label='Factor trajectories for {_esc(factor_name)}' class='fan-svg'>"]
    for gv in range(0, 101, 25):
        parts.append(f"<line x1='{l}' x2='{w - r}' y1='{Y(gv):.1f}' y2='{Y(gv):.1f}' "
                     f"stroke='#eef2f7' stroke-width='1'/>")
        parts.append(f"<text x='{l - 8}' y='{Y(gv) + 4:.1f}' text-anchor='end' "
                     f"font-size='11' fill='#94a3b8'>{gv}</text>")
    base = fv.get("baseline", {})
    base_val = float(base.get(factor_id, fv.get("baseline_default", 50.0)))
    parts.append(f"<line x1='{l}' x2='{w - r}' y1='{Y(base_val):.1f}' y2='{Y(base_val):.1f}' "
                 f"stroke='#94a3b8' stroke-width='1.5' stroke-dasharray='6 4'/>")
    parts.append(f"<text x='{w - r}' y='{Y(base_val) - 6:.1f}' text-anchor='end' "
                 f"font-size='11' fill='#94a3b8'>baseline {_fmt(base_val)}</text>")
    legend = []
    for o in outcomes:
        s = o["series"][factor_id]
        steps, mean, lo, hi = s.get("steps") or [], s.get("mean") or [], s.get("min") or [], s.get("max") or []
        if not steps:
            continue
        color = band_color(o.get("band"))
        upper = " L".join(f"{X(st):.1f},{Y(v):.1f}" for st, v in zip(steps, hi))
        lower = " L".join(f"{X(st):.1f},{Y(v):.1f}" for st, v in zip(reversed(steps), reversed(lo)))
        parts.append(f"<path d='M{upper} L{lower} Z' fill='{color}' fill-opacity='0.13' stroke='none'/>")
        line = " L".join(f"{X(st):.1f},{Y(v):.1f}" for st, v in zip(steps, mean))
        parts.append(f"<path d='M{line}' fill='none' stroke='{color}' stroke-width='3' "
                     f"stroke-linecap='round' stroke-linejoin='round'/>")
        legend.append(f"<li><span class='sw' style='background:{color}'></span>"
                      f"{_esc(o.get('short_name') or o['name'])} &middot; {o['share_pct']}%</li>")
    for st, label in _x_labels(max_step, fv.get("time_labels", {}),
                               fv.get("human_time_labels", False)):
        anchor = "start" if st == 0 else ("end" if st == max_step else "middle")
        x = l if st == 0 else (w - r if st == max_step else X(st))
        parts.append(f"<text x='{x:.1f}' y='{h - 8}' text-anchor='{anchor}' "
                     f"font-size='11' fill='#94a3b8'>{_esc(label)}</text>")
    if not outcomes:
        parts.append(f"<text x='{w / 2}' y='{h / 2}' text-anchor='middle' font-size='14' "
                     f"fill='#94a3b8'>no stored trajectories for this factor</text>")
    parts.append("</svg>")
    return "".join(parts), legend


def _fork_label(bp: dict) -> tuple[str, str]:
    """Short (title, sub) for a fork: recorded events > turning points >
    factor deltas > step number."""
    ev = bp.get("events") or []
    tp = bp.get("turning_points") or []
    if ev:
        return ev[0][:64], ""
    if tp:
        return tp[0][:64], ""
    deltas = bp.get("factor_deltas") or []
    if deltas:
        d = deltas[0]
        return f"{d['name']} splits paths", f"±{_fmt(abs(d['spread']))} pts at this fork"
    return f"paths diverge", f"step {bp.get('step', '?')}"


def map_svg(fv: dict, jid: str) -> tuple[str, list[dict]]:
    """Futures map: one weighted path per outcome from a common origin, with
    branch-point cornerstones. Returns (svg, cornerstones for rationale)."""
    outcomes = fv.get("outcomes", [])
    bps = fv.get("branch_points", [])
    if not outcomes:
        return "<p class='muted'>no outcomes</p>", []
    n = len(outcomes)
    top, lane_h, bottom_pad = 46, 52, 40
    height = top + n * lane_h + bottom_pad
    w, l, r = 1000, 60, 1000 - 20

    max_step = 1
    for o in outcomes:
        for s in (o.get("series") or {}).values():
            if s.get("steps"):
                max_step = max(max_step, s["steps"][-1])
    for bp in bps:
        max_step = max(max_step, int(bp.get("step") or 0))

    def X(step):
        # r is the right EDGE here (unlike fan_svg where it is a margin),
        # so the usable width is r - l.
        return l + (step / max_step) * (r - l)

    lane_y = {o["id"]: top + i * lane_h + lane_h / 2 for i, o in enumerate(outcomes)}
    first_fork = min((int(bp["step"]) for bp in bps), default=None)

    parts = [f"<svg viewBox='0 0 {w} {height}' width='100%' role='img' "
             f"aria-label='Futures map' class='fmap-svg'>"]
    # axis (labels need ~16px below the line, keep inside the viewBox)
    axis_y = height - 30
    parts.append(f"<line x1='{l}' x2='{r}' y1='{axis_y}' y2='{axis_y}' stroke='#cbd5e1'/>")
    for st, label in _x_labels(max_step, fv.get("time_labels", {}),
                               fv.get("human_time_labels", False)):
        anchor = "start" if st == 0 else "middle"
        x = l if st == 0 else X(st)
        parts.append(f"<text x='{x:.1f}' y='{axis_y + 14}' text-anchor='{anchor}' "
                     f"font-size='11' fill='#94a3b8'>{_esc(label)}</text>")

    trunk_y = lane_y[outcomes[0]["id"]]
    if first_fork is None:
        # No detected forks: one straight weighted lane per outcome.
        for o in outcomes:
            color = band_color(o.get("band"))
            share = max(0.0, min(1.0, o.get("probability") or 0.0))
            width = 2.5 + 10 * share
            y = lane_y[o["id"]]
            parts.append(f"<path d='M{l},{y:.1f} L{r},{y:.1f}' stroke='{color}' "
                         f"stroke-width='{width:.1f}' fill='none' stroke-linecap='round' "
                         f"class='fmap-path' hx-get='/results/{jid}/story?oid={o['id']}' "
                         f"hx-target='#story-panel' hx-swap='innerHTML'/>")
            parts.append(f"<text x='{r}' y='{y - 10:.1f}' text-anchor='end' font-size='12.5' "
                         f"font-weight='600' fill='{color}'>{_esc(o.get('short_name') or o['name'])} &middot; "
                         f"{o['share_pct']}%</text>")
        parts.append("</svg>")
        return "".join(parts), []

    # Trunk from origin to the first fork.
    trunk_w = 3 + 12 * 1.0
    parts.append(f"<path d='M{l},{trunk_y} L{X(first_fork):.1f},{trunk_y}' stroke='#64748b' "
                 f"stroke-width='{trunk_w:.1f}' fill='none' stroke-linecap='round'/>")

    # One visible path per outcome: starts at the first fork that involves it.
    first_fork_of: dict[str, int] = {}
    for bp in bps:
        for oid in bp.get("outcomes", []):
            first_fork_of.setdefault(oid, int(bp["step"]))
    for o in outcomes:
        oid = o["id"]
        color = band_color(o.get("band"))
        share = max(0.0, min(1.0, o.get("probability") or 0.0))
        width = 3 + 12 * share
        y = lane_y[oid]
        start_step = first_fork_of.get(oid, first_fork)
        sx = X(start_step)
        mid = sx + min(90, (r - sx) / 3)
        parts.append(
            f"<path d='M{sx:.1f},{trunk_y:.1f} C{mid:.1f},{trunk_y:.1f} {mid:.1f},{y:.1f} "
            f"{min(sx + 140, r):.1f},{y:.1f} L{r},{y:.1f}' stroke='{color}' "
            f"stroke-width='{width:.1f}' fill='none' stroke-linecap='round' class='fmap-path' "
            f"hx-get='/results/{jid}/story?oid={oid}' hx-target='#story-panel' hx-swap='innerHTML'/>")
        parts.append(f"<text x='{r}' y='{y - 10:.1f}' text-anchor='end' font-size='12.5' "
                     f"font-weight='600' fill='{color}'>{_esc(o.get('short_name') or o['name'])} &middot; "
                     f"{o['share_pct']}%</text>")

    # Cornerstone markers at each fork.
    cornerstones = []
    drawn_steps: set[int] = set()
    for bp in sorted(bps, key=lambda b: int(b["step"])):
        step = int(bp["step"])
        title, sub = _fork_label(bp)
        marker_y = trunk_y if step == first_fork else lane_y.get(
            next((o["id"] for o in outcomes if o["id"] in bp.get("outcomes", [])), ""),
            trunk_y)
        x = X(step)
        parts.append(f"<g class='fmap-bp' hx-get='/results/{jid}/rationale?bp={step}' "
                     f"hx-target='#map-rationale' hx-swap='innerHTML'>")
        parts.append(f"<circle cx='{x:.1f}' cy='{marker_y:.1f}' r='7' fill='#fff' "
                     f"stroke='#334155' stroke-width='2.5'/>")
        tx = x + 10 if step != first_fork else x + 10
        ty = marker_y - 34 if step == first_fork else marker_y - 16
        parts.append(f"<text x='{tx:.1f}' y='{ty:.1f}' font-size='12' font-weight='600' "
                     f"fill='#334155'>{_esc(title)}</text>")
        if sub:
            parts.append(f"<text x='{tx:.1f}' y='{ty + 15:.1f}' font-size='11' "
                         f"fill='#64748b'>{_esc(sub)} &middot; "
                         f"{_esc(bp.get('time_label') or f'step {step}')}</text>")
        parts.append("</g>")
        if step not in drawn_steps:
            drawn_steps.add(step)
            cornerstones.append(bp)
    parts.append("</svg>")
    return "".join(parts), cornerstones
