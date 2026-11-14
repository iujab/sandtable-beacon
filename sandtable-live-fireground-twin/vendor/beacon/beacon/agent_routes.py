"""HTTP routes of the command agent and the incident report (docs/CONTRACTS.md section 3.7).

    from beacon.agent_routes import register
    register(router, get_session, get_agent)          # returns the report Recorder it uses

adds
    POST /api/agent/run     {"reason"?: str}  -> {"ok": true, "run": "phones#n", "backend"}  (409 while busy; ?wait=1 returns the plan)
    GET  /api/agent         -> {"busy", "backend", "plan": AgentPlan | null, "log": [agent messages of the last run]}
    GET  /api/report        -> the incident report object (beacon/report.py)
    GET  /report.html       -> the same as one printable, self-contained page

`get_session` and `get_agent` are zero-argument callables (plain or async) returning the current session (a
BeaconSession, the server's state, or anything with beacons()) and the BeaconAgent. History for the report: the
server should pass every stream message to `report.RECORDER.feed(msg)`; without that, these routes start a small
poller (2 Hz) on their first request that keeps the latest Beacons object and the server's event list.
"""
from __future__ import annotations

import asyncio
import inspect
import logging

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from . import report

log = logging.getLogger("beacon.agent_routes")


async def _get(fn):
    v = fn() if callable(fn) else fn
    if inspect.isawaitable(v):
        v = await v
    return v


def register(router, get_session, get_agent, recorder: report.Recorder | None = None) -> report.Recorder:
    rec = recorder if recorder is not None else report.RECORDER
    state: dict = {"poller": None, "task": None}

    def ensure_poller() -> None:
        if state["poller"] is not None and not state["poller"].done():
            return

        async def poll():
            while True:
                try:
                    s = await _get(get_session)
                    if s is not None:
                        rec.pull(s)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.debug("report poller", exc_info=True)
                await asyncio.sleep(0.5)
        state["poller"] = asyncio.get_running_loop().create_task(poll())

    async def run_agent(agent, reason: str):
        try:
            plan = await agent.run(reason)
            rec.plan = plan
            return plan
        except Exception:
            log.exception("command agent run failed")
            return None

    @router.post("/api/agent/run")
    async def api_agent_run(request: Request, wait: int = 0):
        ensure_poller()
        agent = await _get(get_agent)
        if agent is None:
            raise HTTPException(503, "no command agent in this server")
        running = state["task"] is not None and not state["task"].done()
        if agent.busy or running:
            raise HTTPException(409, "the command agent is already running")
        reason = "manual"
        try:
            body = await request.json()
            if isinstance(body, dict) and body.get("reason"):
                reason = " ".join(str(body["reason"]).split())[:60]
        except Exception:
            pass
        state["task"] = asyncio.get_running_loop().create_task(run_agent(agent, reason))
        if wait:
            plan = await state["task"]
            return {"ok": plan is not None, "plan": plan}
        return {"ok": True, "backend": agent.llm.backend}

    @router.get("/api/agent")
    async def api_agent():
        ensure_poller()
        agent = await _get(get_agent)
        if agent is None:
            return {"busy": False, "backend": None, "plan": None, "log": []}
        return agent.status()

    async def build():
        ensure_poller()
        session = await _get(get_session)
        agent = await _get(get_agent)
        return report.build(session, getattr(agent, "last", None) if agent is not None else None, rec)

    @router.get("/api/report")
    async def api_report():
        return await build()

    @router.get("/report.html", response_class=HTMLResponse)
    async def report_html():
        return HTMLResponse(report.to_html(await build()))

    return rec
