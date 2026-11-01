"""Scenario chat: conversational deep-dive over one stored scenario.

Replies generate in a background thread (an LLM round-trip can exceed the
gunicorn sync-worker timeout, which aborts in-request generation) while the
panel polls every 2s. Each turn is a cached, recorded LLM call (phase
"chat", strong role) grounded in the scenario's stored context: premise
card, selected factors, causal edges, outcomes with probabilities, recent
cycle summaries. Messages persist in scenario_chat_messages (migrations
004/005) and re-render on reload. Provider failure: pre-send failures
persist nothing; mid-generation failures mark the reply slot with the
error — no invented content.
"""
import json
import threading

from flask import Blueprint, render_template, request

bp = Blueprint("scenario_chat", __name__)

# A pending reply older than this is considered abandoned (e.g. the web
# process restarted mid-generation) and is surfaced as a failure instead of
# an eternal spinner.
PENDING_STALE_MIN = 10


def _messages(jid: str) -> list[dict]:
    from app.db import get_conn
    with get_conn() as c:
        c.execute("""UPDATE scenario_chat_messages SET error=?
          WHERE scenario_id=? AND role='assistant' AND content='' AND error=''
          AND created_at < datetime('now', ?)""",
          ("reply generation was interrupted; ask again", jid,
           f"-{PENDING_STALE_MIN} minutes"))
        return [dict(r) for r in c.execute(
            "SELECT id, role, content, error, created_at FROM scenario_chat_messages"
            " WHERE scenario_id=? ORDER BY id", (jid,))]


def _pending(jid: str) -> bool:
    from app.db import get_conn
    with get_conn() as c:
        r = c.execute("""SELECT COUNT(*) FROM scenario_chat_messages
          WHERE scenario_id=? AND role='assistant' AND content='' AND error=''""",
          (jid,)).fetchone()
    return bool(r and r[0])


def panel_context(jid: str) -> dict:
    """Template context for the chat panel (detail page include + htmx swaps)."""
    return {"scenario_id": jid, "messages": _messages(jid), "pending": _pending(jid),
            "error": ""}


def _panel(jid: str, error: str = "") -> str:
    ctx = panel_context(jid)
    ctx["error"] = error
    return render_template("scenarios/chat_panel.html", chat=ctx)


def _build_context(jid: str) -> str:
    """Compact JSON digest of the scenario's stored data for grounding."""
    from app.db import get_conn
    with get_conn() as c:
        row = c.execute(
            "SELECT premise, scope, reality_mode, horizon, status FROM projection_scenarios WHERE id=?",
            (jid,)).fetchone()
        card = c.execute(
            "SELECT premise_type, axioms, assumptions FROM scenario_inputs WHERE scenario_id=?",
            (jid,)).fetchone()
        frow = c.execute(
            "SELECT factors FROM factor_sets WHERE scenario_id=? ORDER BY id DESC LIMIT 1",
            (jid,)).fetchone()
        grow = c.execute(
            "SELECT edges FROM causal_graphs WHERE scenario_id=? ORDER BY id DESC LIMIT 1",
            (jid,)).fetchone()
        outs = [dict(r) for r in c.execute(
            "SELECT name, probability, band, summary FROM outcomes WHERE scenario_id=?", (jid,))]
        steps = [dict(r) for r in c.execute(
            "SELECT step_index, summary FROM projection_steps WHERE scenario_id=? AND summary != ''"
            " ORDER BY step_index DESC LIMIT 12", (jid,))]
    ctx: dict = {}
    if row:
        ctx["scenario"] = dict(row)
    if card:
        ctx["premise_type"] = card["premise_type"]
        for k in ("axioms", "assumptions"):
            try:
                ctx[k] = json.loads(card[k])
            except Exception:
                pass
    if frow:
        try:
            fs = json.loads(frow["factors"])
            ctx["factors"] = [{"name": f.get("name", f.get("id")), "current": f.get("current")}
                              for f in fs if isinstance(f, dict)]
        except Exception:
            pass
    if grow:
        try:
            edges = json.loads(grow["edges"])
            ctx["causal_edges"] = [{"source": e.get("source"), "target": e.get("target"),
                                    "polarity": e.get("polarity")}
                                   for e in edges if isinstance(e, dict)][:20]
        except Exception:
            pass
    if outs:
        ctx["outcomes"] = [{"name": o.get("name"),
                            "probability_pct": round(100 * (o.get("probability") or 0), 1),
                            "band": o.get("band"), "summary": (o.get("summary") or "")[:300]}
                           for o in outs]
    if steps:
        ctx["recent_cycles"] = [{"cycle": s.get("step_index"), "summary": (s.get("summary") or "")[:200]}
                                for s in steps]
    return json.dumps(ctx)[:6000]


def _reply(jid: str, question: str) -> str:
    """One chat completion over stored context + recent turns."""
    from app.llm.client import LLMClient, model_for_phase
    msgs = [{"role": "system", "content":
             "You are answering questions about one stored scenario projection. "
             "Ground every answer in the provided scenario context; use plain "
             "language and full factor names; say plainly when something is not "
             "recorded in the scenario. Never invent factors or outcomes. "
             "Respond ONLY with JSON of shape: {\"reply\": str}."},
            {"role": "user", "content": "Scenario context:\n" + _build_context(jid)}]
    for m in _messages(jid)[-8:]:
        if m["content"]:
            msgs.append({"role": m["role"], "content": m["content"][:2000]})
    msgs.append({"role": "user", "content": question[:2000]})
    r = LLMClient().chat(jid, "chat", msgs, model=model_for_phase("chat"))["json"]
    return str(r.get("reply") or "")


def _generate(jid: str, question: str) -> None:
    """Background: fill (or fail) the newest empty assistant reply row."""
    from app.db import get_conn
    from app.scenarios.repos import _write_retry

    def _pending_row(c):
        return c.execute("""SELECT id FROM scenario_chat_messages
          WHERE scenario_id=? AND role='assistant' AND content='' AND error=''
          ORDER BY id DESC LIMIT 1""", (jid,)).fetchone()

    try:
        reply = _reply(jid, question)
        if not reply:
            raise RuntimeError("empty reply from provider")
    except Exception as e:
        def _err():
            with get_conn() as c:
                row = _pending_row(c)
                if row:
                    c.execute("UPDATE scenario_chat_messages SET error=? WHERE id=?",
                              (f"reply failed: {e}", row["id"]))
        _write_retry(_err)
        return

    def _save():
        with get_conn() as c:
            row = _pending_row(c)
            if row:
                c.execute("UPDATE scenario_chat_messages SET content=? WHERE id=?",
                          (reply, row["id"]))
    _write_retry(_save)


@bp.get("/scenarios/<jid>/chat")
def chat_view(jid):
    return _panel(jid)


@bp.post("/scenarios/<jid>/chat")
def chat_send(jid):
    from app.scenarios.repos import get_scenario, _write_retry
    q = (request.form.get("message") or "").strip()
    if not q:
        return _panel(jid)
    if not get_scenario(jid):
        return _panel(jid, error="scenario not found")
    if _pending(jid):
        return _panel(jid, error="previous reply is still generating")
    from app.llm.prepared import provider_status
    st = provider_status()
    if not st["ok"]:
        return _panel(jid, error=f"LLM provider unavailable: {st['detail']}")

    def _save():
        from app.db import get_conn
        with get_conn() as c:
            c.execute("INSERT INTO scenario_chat_messages(scenario_id,role,content) VALUES(?, 'user', ?)",
                      (jid, q))
            c.execute("INSERT INTO scenario_chat_messages(scenario_id,role,content) VALUES(?, 'assistant', '')",
                      (jid,))
    _write_retry(_save)

    threading.Thread(target=_generate, args=(jid, q), daemon=True).start()
    return _panel(jid)


@bp.post("/scenarios/<jid>/chat/clear")
def chat_clear(jid):
    from app.scenarios.repos import _write_retry

    def _del():
        from app.db import get_conn
        with get_conn() as c:
            c.execute("DELETE FROM scenario_chat_messages WHERE scenario_id=?", (jid,))
    _write_retry(_del)
    return _panel(jid)
