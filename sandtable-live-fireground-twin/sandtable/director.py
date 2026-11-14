"""SIM-only storyboard autopilot.

Plays the two-minute exercise on the simulated hub so the demo can run hands-free.
Every step waits for a condition, never for a wall-clock time, so the discrete state
the command agent sees is the same on every run (which is what lets the recorded LLM
responses replay). Every wait has a timeout that logs and moves on: it cannot hang.
"""
from __future__ import annotations

import asyncio
import json
import logging

from . import config

log = logging.getLogger("sandtable.director")


class Director:
    def __init__(self, app_state):
        self.s = app_state
        self.running = False
        self.step = "idle"
        self.mode = "auto"
        self.script = "mayday"
        self.beacon = "story"                      # beacon script: "story" (replay a recorded simulated run) or "live"
        self.steps: list[str] = []                 # every step this run went through (the board and the tests read it)
        self._task: asyncio.Task | None = None

    def status(self) -> dict:
        return {"running": self.running, "step": self.step, "mode": self.mode, "script": self.script, "beacon": self.beacon if self.script == "beacon" else None}

    def start(self, mode: str = "auto", script: str = "mayday", beacon: str = "story") -> None:
        """auto: SIM only, the software moves every crew. assist: a person moves the Engine 1 token
        (real boards, or a drag in the UI); the director gives the cues and plays the rest of the script.
        script: "mayday" (the original exercise, unchanged), "scout" (radar pucks, scan, victim rescue) or "beacon" (phones).
        The HTTP API defaults to "beacon"; this Python default stays "mayday" for the recorded replay tests."""
        if self.running:
            return
        self.mode, self.script, self.steps, self.beacon = mode, script, [], beacon
        self._task = asyncio.create_task(self._run_scout() if script == "scout" else self._run_beacon() if script == "beacon" else self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        self.running, self.step = False, "stopped"

    async def _wait(self, cond, timeout: float, what: str) -> bool:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            if cond():
                return True
            await asyncio.sleep(0.1)
        log.warning("storyboard: timed out waiting for %s", what)
        return False

    def _fire_room(self) -> str:
        """A suite whose tenant carries a life hazard, as far from Stair A as the floor allows."""
        inc, twin = self.s.incident, self.s.twin
        floor_no = int(twin.floor()["label"])
        hazard_suites = {t["suite"] for t in self.s.provider.costar.list_tenants(floor_no) if t.get("life_hazard")}
        best = None
        for r in twin.rooms("suite"):
            route = twin.route("stairA", r["id"])
            if not route["ok"]:
                continue
            key = (r.get("suite") in hazard_suites, route["length_m"], r["id"])
            if best is None or key > best[0]:
                best = (key, r["id"])
        return best[1]

    def _hazard_edge(self, rit: dict, down: dict) -> str | None:
        """The corridor edge just ahead of the rescue team, provided another way round exists."""
        twin, inc = self.s.twin, self.s.incident
        nodes = rit["route"]["nodes"]
        here = nodes.index(rit["_logical"]) if rit["_logical"] in nodes else 0
        for i in range(here + 1, len(nodes) - 1):
            a, b = nodes[i], nodes[i + 1]
            if any(twin.node(n)["kind"] != "sweep" or twin.room(twin.node(n)["room"])["kind"] != "corridor" for n in (a, b)):
                continue                                   # only open corridor stretches, never a doorway
            ref = f"{a}-{b}"
            alt = twin.route(rit["_logical"] or rit["node"], inc._unit_node(down), list(inc.blocked_refs()) + [ref])
            if alt["ok"]:
                return ref
        return None

    async def _run(self) -> None:
        s, inc = self.s, self.s.incident
        self.running = True
        slow = 1 if s.agent.backend == "replay" else 6          # a live model (and the CLI in particular) needs patience
        human = 20 if self.mode == "assist" else 1               # a person moving a token needs patience too
        try:
            self.step = "reset"
            await s.reset()
            await asyncio.sleep(1.0)
            self.step = "alarm: the agent builds the pre-plan"
            await s.alarm()
            await self._wait(lambda: s.preplan is not None and inc.phase == "operations", 240 * slow, "the pre-plan")
            await asyncio.sleep(1.0)

            room = self._fire_room()
            label = s.twin.room(room)["label"]
            inc.assign("E1", "attack", room, source="director")
            e1 = inc.units["E1"]
            if self.mode == "assist":
                self.step = f"YOUR MOVE: take the Engine 1 token along the yellow route into {label}, then hold it still"
            else:
                self.step = "Engine 1 advances to the fire room"
                inc.drive("E1")
            await self._wait(lambda: e1["_follow"] is None and e1["room"] == room, 90 * human, "Engine 1 to arrive")

            self.step = f"Engine 1 is in {label}: hold still for {config.NO_MOTION_S:g} s" if self.mode == "assist" else "Engine 1 stops moving"
            if not await self._wait(lambda: inc.mayday is not None, (config.NO_MOTION_S + 20) * human, "the no-motion mayday"):
                inc.declare_mayday("E1", "manual", "director")

            self.step = "mayday: the agent sends the rescue team"
            rit = inc.units["RIT"]
            await self._wait(lambda: (rit["assignment"] or {}).get("task") == "rit_rescue" and rit["route"] is not None, (config.AGENT_FALLBACK_AFTER_S + 120) * slow, "the rescue assignment")
            await self._wait(lambda: not s.agent_busy(), 120 * slow, "the agent to finish the mayday turn")
            f = rit["_follow"]
            if f is None and rit["route"] and not rit["route"].get("done"):
                self.step = "YOUR MOVE: a person carries the rescue team tag. Inject a hazard from the board if you want a re-plan"
            if f:                                          # halt the team exactly at a graph node, about 40% of the way in
                k = next((i for i, c in enumerate(f["cum"]) if c >= 0.4 * f["total"]), len(f["cum"]) - 1)
                await self._wait(lambda: rit["_follow"] is None or rit["_follow"]["i"] >= k, 60 * slow, "the rescue team to get under way")
                if rit["_follow"] is not None and f["nodes"]:
                    rit["_follow"] = None
                    rit["_logical"] = f["nodes"][min(k, len(f["nodes"]) - 1)]
                    inc.move_unit("RIT", *s.twin.node(rit["_logical"])["at"])

            if f and rit["route"] and not rit["route"].get("done"):
                self.step = "inject: a corridor is lost"
                ref = self._hazard_edge(rit, e1)
                if ref:
                    inc.mark_hazard(ref, "collapse", "ceiling collapse across the corridor (exercise inject)", source="inject")
                    await self._wait(lambda: rit["route"] is not None and rit["route"].get("valid") and rit["_follow"] is not None or inc.contact_done,
                                     (config.AGENT_FALLBACK_AFTER_S + 120) * slow, "the re-plan")
                    await self._wait(lambda: not s.agent_busy(), 120 * slow, "the agent to finish the re-plan turn")
                else:
                    inc.drive("RIT")

            self.step = "rescue team re-routed"
            await self._wait(lambda: inc.contact_done, 120 * human, "contact")
            self.step = "contact: the agent plans the way out"
            if self.mode == "assist" and e1["physical"] and not inc.drivable:
                await self._wait(lambda: not s.agent_busy() and rit["assignment"] and rit["assignment"]["task"] == "exit", 120 * slow, "the exit order")
                self.step = "YOUR MOVE: walk the Engine 1 token out with the rescue team to the stair shown"
            await self._wait(lambda: inc.phase == "terminated", (config.AGENT_FALLBACK_AFTER_S + 180) * slow * human, "all crews out")
            self.step = "after-action report"
            await self._wait(lambda: s.aar is not None, 240 * slow, "the after-action report")
            self.step = "done"
        except asyncio.CancelledError:
            self.step = "stopped"
            raise
        except Exception:
            log.exception("storyboard failed")
            self.step = "failed"
        finally:
            self.running = False

    # ------------------------------------------------------------------ the scout exercise
    def _say(self, step: str) -> None:
        self.step = step
        self.steps.append(step)
        log.info("scout script: %s", step)

    def _script_fallback(self, text: str) -> None:
        """The script acts in the agent's place; the timeline says so (kind 'fallback')."""
        self.s.incident._log("fallback", f"Exercise script fallback: {text}")

    async def _rescue_and_out(self, vid: str | None, what: str, slow: float, human: float) -> None:
        """Shared ending of the scout and beacon scripts: the agent's orders on `what` (else the labelled script fallback), the rescue team
        walks to the person `vid`, reached, the way out (agent, else fallback), everybody who went in comes out, all out."""
        s, inc = self.s, self.s.incident
        e1 = inc.units["E1"]

        def person():
            return inc.victim_target(vid) or {}
        if vid:
            def ordered():
                return any((u["assignment"] or {}).get("target") == vid for u in inc.units.values()) or bool(person().get("rescuer"))
            self._say(f"{what}: the agent acts on what the pucks found")
            await self._wait(lambda: ordered() or not s.agent_busy(), (config.AGENT_FALLBACK_AFTER_S + 120) * slow, f"the agent's orders on the {what}")
            await self._wait(ordered, 2.0, "an order after the agent's turn")
            if not ordered():
                self._script_fallback(f"the agent gave no order on the {what}, so the script sends the rescue team to {vid}")
                inc.assign("RIT", "rescue", vid, source="script-fallback")
            await self._wait(lambda: not s.agent_busy(), 120 * slow, f"the agent to finish the {what} turn")
            rescuer = person().get("rescuer") or "RIT"
            r = inc.units[rescuer]
            if r["physical"] and not inc.drivable:
                self._say(f"YOUR MOVE: carry the {r['label']} token to {vid} (the pulsing marker)")
            else:
                self._say(f"{r['label']} walks to {vid}")
                if r["_follow"] is None and r["route"] and not r["route"].get("done"):
                    inc.drive(rescuer)
            await self._wait(lambda: person().get("status") in ("reached", "evacuating", "out"), 120 * human, "the rescue team to reach the person")

            self._say(f"{vid} reached: the agent plans the way out")
            await self._wait(lambda: person().get("status") in ("evacuating", "out") or not s.agent_busy(),
                             (config.AGENT_FALLBACK_AFTER_S + 120) * slow, "the agent's exit order")
            await self._wait(lambda: person().get("status") in ("evacuating", "out"), 2.0, "an exit order after the agent's turn")
            if person().get("status") == "reached":
                rescuer = person().get("rescuer") or rescuer
                self._script_fallback(f"the agent gave no exit order, so the script brings {vid} out with {inc.units[rescuer]['label']}")
                inc.assign(rescuer, "exit", "exit", source="script-fallback")
            await self._wait(lambda: not s.agent_busy(), 120 * slow, "the agent to finish its turn")
        if not vid:                                        # nobody to rescue: let the agent finish its turn on the scan or phones first
            await self._wait(lambda: not s.agent_busy(), (config.AGENT_FALLBACK_AFTER_S + 120) * slow, f"the agent's turn on the {what}")
        for u in inc.units.values():                       # everybody who went in comes out
            if u["_was_interior"] and u["status"] not in ("exiting", "out") and (u["assignment"] or {}).get("task") != "exit":
                self._script_fallback(f"{u['label']} withdraws with the others")
                inc.assign(u["id"], "exit", "exit", source="script-fallback")
        if self.mode == "assist" and e1["physical"] and not inc.drivable:
            self._say("YOUR MOVE: walk the Engine 1 token out to the stair shown")
        else:
            self._say("all crews withdraw")
        if not vid or not person().get("rescuer"):         # nobody was rescued: there is no rescue to end the incident
            await self._wait(lambda: all(u["status"] == "out" for u in inc.units.values() if u["_was_interior"]), 120 * human, "the crews out")
            if inc.phase != "terminated":
                inc.phase = "terminated"
                inc._log("par", "All crews are out of the floor. Accountability complete.")
                inc._situation("all_out", None, {})
        await self._wait(lambda: inc.phase == "terminated", (config.AGENT_FALLBACK_AFTER_S + 180) * slow * human, "all crews out")

    async def _e1_to_door(self, room: str, human: float) -> None:
        from .scout.session import door_outside_node
        s, inc = self.s, self.s.incident
        label = s.twin.room(room)["label"]
        inc.assign("E1", "stage", door_outside_node(s.twin, room) or room, source="director")
        e1 = inc.units["E1"]
        if self.mode == "assist":
            inc.drive("E1", False)                         # a person moves Engine 1 (the stage order alone would walk a simulated tag)
        if self.mode == "assist" and e1["physical"] and not inc.drivable:
            self._say(f"YOUR MOVE: take the Engine 1 token along the yellow route to the door of {label} (corridor side)")
        elif self.mode == "assist":
            self._say(f"YOUR MOVE: drag Engine 1 along the yellow route to the door of {label} (corridor side)")
        else:
            self._say(f"Engine 1 goes to the door of {label}")
            if e1["_follow"] is None:
                inc.drive("E1")
        await self._wait(lambda: e1["_follow"] is None and bool(e1["route"] and e1["route"].get("done")), 90 * human, "Engine 1 at the door")

    def _beacon_pucks(self, room: str) -> dict:
        """The scout simulator's puck positions for this room when the radar engine imports, else a plain throw of three."""
        try:
            from .scout import scene
            sc = scene.build_scene(self.s.twin, room, 5, 310)
            return {"pucks": [{"id": p["id"], "x": round(float(p["x"]), 2), "y": round(float(p["y"]), 2)} for p in sc["pucks"]],
                    "room": room, "floor": self.s.twin.room(room).get("floor")}
        except Exception as exc:
            log.warning("beacon script: no scout puck positions (%s); asking the Beacon server to throw 3", type(exc).__name__)
            return {"n": 3, "room": room}

    async def _run_beacon(self) -> None:
        """Sandtable Beacon, the owner's live setup: TWO pucks placed by hand (tape-measured, clicked onto the plan) in the fire room and
        two victims' phones. Phone 1's mirror candidate falls outside the building and the twin removes it (a fix); phone 2's mirror stays
        in the room (two candidates: cannot choose). Reset, alarm, pre-plan, Engine 1 to the fire room's door, pucks placed, phones heard,
        situation `phone`, the agent routes the rescue team, `phone_reached`, the way out, all out, after-action, report.
        beacon="story" (default, hands-free): replays a recorded run of the simulated Beacon module so every run is identical.
        beacon="live": drives the live module (POST /api/pucks, POST /api/sim/phone), for use with a live model."""
        s, inc = self.s, self.s.incident
        link = s.beacon
        self.running = True
        slow = 1 if s.agent.backend == "replay" else 6
        human = 20 if self.mode == "assist" else 1
        try:
            from .beacon_link import SCENE
            scene = json.loads(SCENE.read_text(encoding="utf-8"))
            self._say("reset" + ("" if self.beacon == "story" else " (here and on the Beacon server)"))
            await s.reset()                                 # also ends any earlier replay
            story = self.beacon == "story" and link.start_story()
            if not story:
                await link.remote("POST", "/api/reset", {})
            await asyncio.sleep(1.0)
            self._say("alarm: the agent builds the pre-plan")
            await s.alarm()
            await self._wait(lambda: s.preplan is not None and inc.phase == "operations", 240 * slow, "the pre-plan")
            await asyncio.sleep(1.0)
            room = scene.get("room") or self._fire_room()
            label = s.twin.room(room)["label"]
            await self._e1_to_door(room, human)

            src = ("replay of a recorded simulated run" if story else "Beacon module live" if link.connected
                   else "Beacon module not connected: SAMPLE phones (hand-made, not a result)")
            if self.mode == "assist":
                self._say(f"YOUR MOVE: place the two pucks in {label} at the marked spots and click them onto the plan ({src})")
                await asyncio.sleep(3.0 * s.scout.pace)
            self._say(f"Engine 1 places two pucks by hand in {label} ({src})")
            body = {"pucks": [{k: p[k] for k in ("id", "x", "y")} | {"placed": "by hand"} for p in scene["pucks"]], "room": room, "floor": scene.get("floor")}
            inc._log("beacon", f"{inc.units['E1']['label']} places two pucks by hand in {label}: tape-measured, clicked onto the plan ({src})", "E1")
            await link.pucks_in(body, remote=not story)
            if story:
                inc.script_hold = True                     # crews the agent orders wait at the stair while the recorded picture forms
                await link.story_step("pucks")
                for step in [k for k in link.story["order"] if k.startswith("phone")]:
                    self._say(f"phones answer: {step.replace('_', ' ')}")
                    await asyncio.sleep(1.0)
                    await link.story_step(step)
                    await self._wait(lambda: not s.agent_busy(), (config.AGENT_FALLBACK_AFTER_S + 120) * slow, f"the agent's turn on {step}")
                inc.script_hold = False
            elif link.connected:
                for ph in scene["phones"]:
                    n = len(inc.phones)
                    self._say(f"phones answer: {ph['key']}")
                    out = await link.remote("POST", "/api/sim/phone", {"key": ph["key"], "x": ph["x"], "y": ph["y"], "floor": scene.get("floor"),
                                                                       "opt_in": True, "uwb": True, "pointing": False, "report": ph["report"]})
                    if not out["ok"]:
                        log.warning("beacon script: the Beacon server refused a simulated phone: %s", out.get("reason"))
                    await self._wait(lambda: len(inc.phones) > n, 30, "the phone to be heard")
                    await self._wait(lambda: not s.agent_busy(), (config.AGENT_FALLBACK_AFTER_S + 120) * slow, "the agent's turn on the phone")
            else:
                self._say("phones answer (SAMPLE)")
            await self._wait(lambda: bool(inc.phones), 30, "a phone")
            vid = self._beacon_target()
            if not vid:
                self._say("no phone answered: crews withdraw")
                inc._log("beacon", "No opt-in phone answered; the exercise ends with the crews withdrawing")
            await self._rescue_and_out(vid, "phone", slow, human)
            if story and "after_rescue" in link.story["frames"]:
                await link.story_step("after_rescue")
            self._say("after-action report")
            await self._wait(lambda: s.aar is not None, 240 * slow, "the after-action report")
            self._say("report ready: /api/report")
            self._say("done")
        except asyncio.CancelledError:
            self.step = "stopped"
            raise
        except Exception:
            log.exception("beacon storyboard failed")
            self.step = "failed"
        finally:
            inc.script_hold = False
            self.running = False

    def _beacon_target(self) -> str | None:
        """Whom the script expects the rescue to go to: the Beacon accountability's first priority when it is a phone here,
        else the first phone with a fix, else the first phone."""
        inc = self.s.incident
        pr = ((self.s.beacon.beacons or {}).get("accountability") or {}).get("priorities") or []
        for q in pr:
            if q.get("target") in inc.phones:
                return q["target"]
        fixes = [p for p in sorted(inc.phones) if inc.phones[p]["geometry"] == "fix"]
        return fixes[0] if fixes else (sorted(inc.phones)[0] if inc.phones else None)

    async def _run_scout(self) -> None:
        """Reset, alarm, pre-plan; Engine 1 to the fire room's door; smoke; Engine 1 throws the pucks; the scan lands and the
        agent acts on it; the rescue team walks to the located person; victim_reached; the way out; all out; after-action.
        Condition-based like the mayday script; every wait has a timeout that logs and moves on."""
        s, inc = self.s, self.s.incident
        self.running = True
        slow = 1 if s.agent.backend == "replay" else 6
        human = 20 if self.mode == "assist" else 1
        try:
            self._say("reset")
            await s.reset()
            await asyncio.sleep(1.0)
            self._say("alarm: the agent builds the pre-plan")
            await s.alarm()
            await self._wait(lambda: s.preplan is not None and inc.phase == "operations", 240 * slow, "the pre-plan")
            await asyncio.sleep(1.0)

            from .scout.session import door_outside_node
            room = self._fire_room()
            label = s.twin.room(room)["label"]
            door_node = door_outside_node(s.twin, room) or room
            inc.assign("E1", "stage", door_node, source="director")
            e1 = inc.units["E1"]
            if self.mode == "assist":
                inc.drive("E1", False)                         # a person moves Engine 1 (the stage order alone would walk a simulated tag)
            if self.mode == "assist" and e1["physical"] and not inc.drivable:
                self._say(f"YOUR MOVE: take the Engine 1 token along the yellow route to the door of {label} (corridor side)")
            elif self.mode == "assist":
                self._say(f"YOUR MOVE: drag Engine 1 along the yellow route to the door of {label} (corridor side)")
            else:
                self._say(f"Engine 1 goes to the door of {label}")
                if e1["_follow"] is None:
                    inc.drive("E1")
            await self._wait(lambda: e1["_follow"] is None and bool(e1["route"] and e1["route"].get("done")), 90 * human, "Engine 1 at the door")

            self._say(f"smoke: {label} fills with smoke (a camera or LiDAR would see nothing)")
            for k in range(10):
                inc.scout["smoke"] = round(0.09 * (k + 1), 2)
                await asyncio.sleep(0.3 * s.scout.pace)

            if self.mode == "assist":
                self._say(f"YOUR MOVE: throw the pucks into {label} (press Throw pucks)")
                if not await self._wait(lambda: s.scout.phase != "idle", 30 * human, "the pucks to be thrown"):
                    inc.log_scout("No throw within the cue time: the exercise script throws the pucks")
            if s.scout.phase == "idle" or (not s.scout.running and s.scout.scan is None):
                self._say(f"Engine 1 throws the pucks into {label}")
                inc.log_scout(f"{e1['label']} throws the scout pucks into {label}", "E1")
                s.scout.start(room)
            self._say("scout: pucks self-locate, register into the twin and scan")
            await self._wait(lambda: s.scout.scan is not None and inc.scan is not None, 90, "the scan")
            victims = [v for v in inc.victims.values()]
            if not victims:
                self._say("the scan located nobody: crews withdraw")
                inc.log_scout("The scan located nobody in the room; the exercise ends with the crews withdrawing")
            await self._rescue_and_out(victims[0]["id"] if victims else None, "scan", slow, human)
            self._say("after-action report")
            await self._wait(lambda: s.aar is not None, 240 * slow, "the after-action report")
            self._say("done")
        except asyncio.CancelledError:
            self.step = "stopped"
            raise
        except Exception:
            log.exception("scout storyboard failed")
            self.step = "failed"
        finally:
            self.running = False
