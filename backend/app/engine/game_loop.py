"""
Authoritative multiplayer game room, lobby state, combat, skills, puzzles, and extraction loop.
"""

from __future__ import annotations

import math
import random
import time
import uuid
from typing import Any

from backend.app.config.classes import CHARACTER_CLASSES, ENEMY_TYPES
from backend.app.config.missions import MISSIONS
from backend.app.engine.ai import update_enemies
from backend.app.engine.map_builder import build_mission_map
from backend.app.engine.physics import (
    circle_vs_aabb,
    has_line_of_sight,
    move_with_collisions,
    point_in_rect,
    segment_intersects_aabb,
)


class GameRoom:
    """
    Manages the co-op lobby, leader privileges, and 30Hz authoritative mission simulation.
    """

    def __init__(self) -> None:
        self.players: dict[str, dict[str, Any]] = {}
        self.join_order: list[str] = []
        self.leader_id: str | None = None
        self.selected_mission_id: str = "outpost"
        self.phase: str = "lobby"  # 'lobby' | 'dropship' | 'active' | 'victory' | 'defeat'
        self.dropship_timer: float = 0.0
        self.mission_time: float = 0.0

        self.map_data: dict[str, Any] | None = None
        self.projectiles: list[dict[str, Any]] = []
        self.sound_events: list[dict[str, Any]] = []
        self.recent_sounds: list[dict[str, Any]] = []
        self.deployables: list[dict[str, Any]] = []  # turrets, healing fields, sonar pulses
        self.sonar_timer: float = 0.0
        self.events_log: list[str] = []

    def add_player(self, player_id: str, name: str = "") -> dict[str, Any]:
        default_class = "vanguard"
        cls_cfg = CHARACTER_CLASSES[default_class]
        clean_name = (name or f"Operative-{len(self.players) + 1}").strip()[:20]

        player = {
            "id": player_id,
            "name": clean_name,
            "class_id": default_class,
            "ready": False,
            "x": 4.0,
            "y": 7.5,
            "vx": 0.0,
            "vy": 0.0,
            "angle": 0.0,
            "radius": 0.42,
            "hp": cls_cfg["max_hp"],
            "max_hp": cls_cfg["max_hp"],
            "ammo": cls_cfg["weapon"]["mag_size"],
            "max_ammo": cls_cfg["weapon"]["mag_size"],
            "reload_timer": 0.0,
            "fire_cooldown": 0.0,
            "skill_cooldown": 0.0,
            "skill_timer": 0.0,
            "skill_active": None,
            "footstep_timer": 0.0,
            "downed": False,
            "extracted": False,
            "kills": 0,
            "input": {
                "dx": 0.0,
                "dy": 0.0,
                "aim_x": 10.0,
                "aim_y": 8.0,
                "firing": False,
            },
        }
        self.players[player_id] = player
        if player_id not in self.join_order:
            self.join_order.append(player_id)
        if self.leader_id is None or self.leader_id not in self.players:
            self.leader_id = player_id

        # If joining mid-mission, spawn near dropship or a living ally
        if self.phase in ("dropship", "active") and self.map_data:
            spawns = self.map_data["player_spawn_points"]
            sx, sy = spawns[len(self.players) % len(spawns)]
            player["x"], player["y"] = sx, sy

        return player

    def remove_player(self, player_id: str) -> None:
        self.players.pop(player_id, None)
        if player_id in self.join_order:
            self.join_order.remove(player_id)
        if self.leader_id == player_id:
            self.leader_id = self.join_order[0] if self.join_order else None
        if not self.players and self.phase != "lobby":
            self.reset_to_lobby()

    def select_class(self, player_id: str, class_id: str) -> bool:
        if class_id not in CHARACTER_CLASSES or player_id not in self.players:
            return False
        p = self.players[player_id]
        cls_cfg = CHARACTER_CLASSES[class_id]
        p["class_id"] = class_id
        p["max_hp"] = cls_cfg["max_hp"]
        p["hp"] = cls_cfg["max_hp"]
        p["max_ammo"] = cls_cfg["weapon"]["mag_size"]
        p["ammo"] = cls_cfg["weapon"]["mag_size"]
        return True

    def set_player_name(self, player_id: str, name: str) -> None:
        if player_id in self.players and name.strip():
            self.players[player_id]["name"] = name.strip()[:20]

    def select_mission(self, player_id: str, mission_id: str) -> bool:
        if player_id != self.leader_id or mission_id not in MISSIONS:
            return False
        if self.phase != "lobby":
            return False
        self.selected_mission_id = mission_id
        return True

    def start_mission(self, player_id: str) -> bool:
        if player_id != self.leader_id:
            return False
        if not self.players:
            return False

        self.map_data = build_mission_map(self.selected_mission_id)
        spawns = self.map_data["player_spawn_points"]
        for idx, p in enumerate(self.players.values()):
            cls_cfg = CHARACTER_CLASSES[p["class_id"]]
            sx, sy = spawns[idx % len(spawns)]
            p["x"] = sx
            p["y"] = sy
            p["vx"] = 0.0
            p["vy"] = 0.0
            p["angle"] = 0.0
            p["hp"] = cls_cfg["max_hp"]
            p["max_hp"] = cls_cfg["max_hp"]
            p["ammo"] = cls_cfg["weapon"]["mag_size"]
            p["max_ammo"] = cls_cfg["weapon"]["mag_size"]
            p["reload_timer"] = 0.0
            p["fire_cooldown"] = 0.0
            p["skill_cooldown"] = 0.0
            p["skill_timer"] = 0.0
            p["skill_active"] = None
            p["downed"] = False
            p["extracted"] = False
            p["kills"] = 0
            p["input"] = {"dx": 0.0, "dy": 0.0, "aim_x": sx + 5.0, "aim_y": sy, "firing": False}

        self.projectiles.clear()
        self.sound_events.clear()
        self.recent_sounds.clear()
        self.deployables.clear()
        self.sonar_timer = 0.0
        self.mission_time = 0.0
        self.dropship_timer = 2.5
        self.phase = "dropship"
        self.events_log = [f"Dropship docked at {self.map_data['mission_name']}. Stand by for ramp release..."]
        return True

    def reset_to_lobby(self) -> None:
        self.phase = "lobby"
        self.map_data = None
        self.projectiles.clear()
        self.sound_events.clear()
        self.recent_sounds.clear()
        self.deployables.clear()
        for p in self.players.values():
            p["ready"] = False
            p["downed"] = False
            p["extracted"] = False

    def open_spaceship_ramp(self) -> None:
        if not self.map_data:
            return
        ship = self.map_data["spaceship"]
        if not ship["ramp_open"]:
            ship["ramp_open"] = True
            for w in self.map_data["walls"]:
                if w["id"] == ship["door_wall_id"]:
                    w["active"] = False
                    w["blocks_sight"] = False
            self.events_log.append("Spaceship ramp lowered! Exit the ship and complete mission objectives.")

    def update_input(self, player_id: str, data: dict[str, Any]) -> None:
        p = self.players.get(player_id)
        if not p:
            return
        inp = p["input"]
        inp["dx"] = max(-1.0, min(1.0, float(data.get("dx", inp["dx"]))))
        inp["dy"] = max(-1.0, min(1.0, float(data.get("dy", inp["dy"]))))
        if "aim_x" in data and "aim_y" in data:
            inp["aim_x"] = float(data["aim_x"])
            inp["aim_y"] = float(data["aim_y"])
        if "firing" in data:
            inp["firing"] = bool(data["firing"])

    def trigger_reload(self, player_id: str) -> None:
        p = self.players.get(player_id)
        if not p or p["downed"] or p["reload_timer"] > 0.0:
            return
        if p["ammo"] < p["max_ammo"]:
            cls_cfg = CHARACTER_CLASSES[p["class_id"]]
            p["reload_timer"] = float(cls_cfg["weapon"]["reload_time"])

    def trigger_skill(self, player_id: str) -> bool:
        p = self.players.get(player_id)
        if not p or p["downed"] or self.phase not in ("dropship", "active") or not self.map_data:
            return False
        if p["skill_cooldown"] > 0.0:
            return False

        cls_cfg = CHARACTER_CLASSES[p["class_id"]]
        skill = cls_cfg["skill"]
        skill_id = skill["id"]
        p["skill_cooldown"] = float(skill["cooldown"])
        p["skill_timer"] = float(skill["duration"])
        p["skill_active"] = skill_id

        if skill_id == "shockwave_shield":
            # Push back nearby enemies and grant temporary front shield
            for enemy in self.map_data["enemies"]:
                if enemy["hp"] > 0:
                    dx = enemy["x"] - p["x"]
                    dy = enemy["y"] - p["y"]
                    dist = math.hypot(dx, dy)
                    if 0.0 < dist <= 4.0:
                        push = (4.2 - dist) * 1.6
                        nx, ny = move_with_collisions(
                            enemy["x"],
                            enemy["y"],
                            (dx / dist) * push,
                            (dy / dist) * push,
                            1.0,
                            enemy["radius"],
                            self.map_data["walls"],
                        )
                        enemy["x"], enemy["y"] = nx, ny
                        enemy["hp"] = max(0, enemy["hp"] - 15)
            self._emit_sound(p["x"], p["y"], 9.0, "skill")
            self.events_log.append(f"{p['name']} activated Shockwave Shield!")

        elif skill_id == "auto_turret":
            tx = p["x"] + math.cos(p["angle"]) * 1.2
            ty = p["y"] + math.sin(p["angle"]) * 1.2
            self.deployables.append(
                {
                    "id": uuid.uuid4().hex[:8],
                    "type": "turret",
                    "owner_id": p["id"],
                    "x": tx,
                    "y": ty,
                    "angle": p["angle"],
                    "ttl": float(skill["duration"]),
                    "fire_timer": 0.0,
                    "range": 10.5,
                }
            )
            self.events_log.append(f"{p['name']} deployed an Auto-Turret!")

        elif skill_id == "nano_field":
            self.deployables.append(
                {
                    "id": uuid.uuid4().hex[:8],
                    "type": "nano_field",
                    "owner_id": p["id"],
                    "x": p["x"],
                    "y": p["y"],
                    "radius": 3.8,
                    "ttl": float(skill["duration"]),
                    "heal_tick": 0.0,
                }
            )
            # Also immediately revive any downed allies within 3.8 units
            for ally in self.players.values():
                if ally["downed"] and math.hypot(ally["x"] - p["x"], ally["y"] - p["y"]) <= 3.8:
                    ally["downed"] = False
                    ally["hp"] = max(35, int(ally["max_hp"] * 0.4))
            self.events_log.append(f"{p['name']} deployed Nano-Stim Healing Field!")

        elif skill_id == "sonar_pulse":
            self.sonar_timer = max(self.sonar_timer, float(skill["duration"]))
            self.deployables.append(
                {
                    "id": uuid.uuid4().hex[:8],
                    "type": "sonar_pulse",
                    "owner_id": p["id"],
                    "x": p["x"],
                    "y": p["y"],
                    "radius": 18.0,
                    "ttl": float(skill["duration"]),
                }
            )
            self.events_log.append(f"{p['name']} emitted a Tactical Sonar Pulse!")

        return True

    def handle_interact(self, player_id: str) -> dict[str, Any] | None:
        """
        Handles pressing E near a downed teammate (revive) or near a puzzle terminal.
        """
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.map_data:
            return None

        # 1. Check downed teammates within 2.0 units
        for ally in self.players.values():
            if ally["id"] != p["id"] and ally["downed"]:
                if math.hypot(ally["x"] - p["x"], ally["y"] - p["y"]) <= 2.2:
                    ally["downed"] = False
                    ally["hp"] = int(ally["max_hp"] * 0.5)
                    self.events_log.append(f"{p['name']} revived {ally['name']}!")
                    return {"type": "revived", "target_id": ally["id"]}

        # 2. Check unsolved puzzle terminals within 2.5 units
        for pz in self.map_data["puzzles"]:
            if not pz["solved"]:
                if math.hypot(pz["x"] - p["x"], pz["y"] - p["y"]) <= pz["radius"]:
                    # Tech class passive: can auto-align one step or solve faster
                    return {"type": "open_puzzle", "puzzle": pz}

        return None

    def handle_puzzle_action(self, player_id: str, puzzle_id: str, action: dict[str, Any]) -> dict[str, Any]:
        """
        Processes interactive puzzle actions from a player.
        """
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.map_data:
            return {"ok": False}

        puzzle = next((pz for pz in self.map_data["puzzles"] if pz["id"] == puzzle_id), None)
        if not puzzle or puzzle["solved"]:
            return {"ok": False, "solved": True}

        # Verify range
        if math.hypot(puzzle["x"] - p["x"], puzzle["y"] - p["y"]) > puzzle["radius"] + 1.5:
            return {"ok": False, "reason": "Too far from terminal"}

        pdata = puzzle["data"]
        action_type = action.get("action")

        if action_type == "tech_bypass" and p["class_id"] == "tech":
            self._complete_puzzle(puzzle, p["name"])
            return {"ok": True, "solved": True, "puzzle": puzzle}

        if puzzle["type"] == "power_reroute":
            idx = int(action.get("node_index", 0))
            if 0 <= idx < len(pdata["nodes"]):
                pdata["nodes"][idx] = (pdata["nodes"][idx] + 1) % 4
                if pdata["nodes"] == pdata["target"]:
                    self._complete_puzzle(puzzle, p["name"])
                    return {"ok": True, "solved": True, "puzzle": puzzle}

        elif puzzle["type"] == "signal_decrypt":
            symbol = str(action.get("symbol", ""))
            prog = int(pdata["progress"])
            target_seq = pdata["target_sequence"]
            if prog < len(target_seq):
                if symbol == target_seq[prog]:
                    pdata["progress"] = prog + 1
                    if pdata["progress"] >= len(target_seq):
                        self._complete_puzzle(puzzle, p["name"])
                        return {"ok": True, "solved": True, "puzzle": puzzle}
                else:
                    # Wrong symbol resets progress and emits an alarm sound ring!
                    pdata["progress"] = 0
                    self._emit_sound(puzzle["x"], puzzle["y"], 14.0, "alarm")
                    self.events_log.append(f"Security alarm triggered at {puzzle['label']}!")

        return {"ok": True, "solved": puzzle["solved"], "puzzle": puzzle}

    def _complete_puzzle(self, puzzle: dict[str, Any], solver_name: str) -> None:
        if not self.map_data or puzzle["solved"]:
            return
        puzzle["solved"] = True
        door_id = puzzle["door_id"]
        for w in self.map_data["walls"]:
            if w["id"] == door_id:
                w["active"] = False
                w["blocks_sight"] = False
        self.events_log.append(f"{solver_name} completed '{puzzle['label']}' — Bulkhead unlocked!")

        # Check if all puzzles are solved -> activate extraction point!
        if all(pz["solved"] for pz in self.map_data["puzzles"]):
            if self.map_data["extraction_zone"]:
                self.map_data["extraction_zone"]["active"] = True
                self.events_log.append("All mission objectives complete! Proceed to the Extraction Pad!")

    def _emit_sound(self, x: float, y: float, radius: float, kind: str = "gunfire") -> None:
        evt = {
            "id": uuid.uuid4().hex[:8],
            "x": x,
            "y": y,
            "radius": radius,
            "kind": kind,
            "ttl": 0.45,
        }
        self.sound_events.append(evt)
        self.recent_sounds.append(dict(evt))

    def step(self, dt: float) -> None:
        if self.phase not in ("dropship", "active") or not self.map_data:
            return

        self.mission_time += dt
        self.sound_events.clear()

        if self.sonar_timer > 0.0:
            self.sonar_timer = max(0.0, self.sonar_timer - dt)

        # Fade visual sound rings
        for s in self.recent_sounds:
            s["ttl"] -= dt
        self.recent_sounds = [s for s in self.recent_sounds if s["ttl"] > 0.0]

        # Handle Dropship insertion countdown
        if self.phase == "dropship":
            self.dropship_timer -= dt
            if self.dropship_timer <= 0.0:
                self.phase = "active"
                self.open_spaceship_ramp()

        walls = self.map_data["walls"]
        enemies = self.map_data["enemies"]

        # 1. Update Players
        for p in self.players.values():
            cls_cfg = CHARACTER_CLASSES[p["class_id"]]
            w_cfg = cls_cfg["weapon"]

            if p["skill_cooldown"] > 0.0:
                p["skill_cooldown"] = max(0.0, p["skill_cooldown"] - dt)
            if p["skill_timer"] > 0.0:
                p["skill_timer"] = max(0.0, p["skill_timer"] - dt)
                if p["skill_timer"] == 0.0:
                    p["skill_active"] = None
            if p["fire_cooldown"] > 0.0:
                p["fire_cooldown"] = max(0.0, p["fire_cooldown"] - dt)
            if p["reload_timer"] > 0.0:
                p["reload_timer"] = max(0.0, p["reload_timer"] - dt)
                if p["reload_timer"] == 0.0:
                    p["ammo"] = p["max_ammo"]

            if p["downed"]:
                p["vx"] = 0.0
                p["vy"] = 0.0
                continue

            inp = p["input"]
            dx, dy = float(inp["dx"]), float(inp["dy"])
            mag = math.hypot(dx, dy)
            if mag > 1.0:
                dx /= mag
                dy /= mag

            spd = float(cls_cfg["speed"])
            p["vx"] = dx * spd
            p["vy"] = dy * spd

            if abs(dx) > 0.01 or abs(dy) > 0.01:
                nx, ny = move_with_collisions(p["x"], p["y"], p["vx"], p["vy"], dt, p["radius"], walls)
                p["x"], p["y"] = nx, ny
                p["footstep_timer"] -= dt
                if p["footstep_timer"] <= 0.0:
                    p["footstep_timer"] = 0.42
                    self._emit_sound(p["x"], p["y"], float(cls_cfg["footstep_radius"]), "footstep")

            # Aim angle toward mouse world coordinates
            aim_dx = float(inp["aim_x"]) - p["x"]
            aim_dy = float(inp["aim_y"]) - p["y"]
            if abs(aim_dx) > 1e-4 or abs(aim_dy) > 1e-4:
                p["angle"] = math.atan2(aim_dy, aim_dx)

            # Firing weapon
            if inp.get("firing") and p["fire_cooldown"] <= 0.0 and p["reload_timer"] <= 0.0:
                if p["ammo"] > 0:
                    p["ammo"] -= 1
                    p["fire_cooldown"] = 1.0 / float(w_cfg["fire_rate"])
                    pellets = int(w_cfg["pellets"])
                    spread = float(w_cfg["spread"])
                    proj_spd = float(w_cfg["projectile_speed"])
                    proj_ttl = float(w_cfg["range"]) / proj_spd

                    for _ in range(pellets):
                        ang = p["angle"] + random.uniform(-spread, spread)
                        self.projectiles.append(
                            {
                                "id": uuid.uuid4().hex[:8],
                                "owner_id": p["id"],
                                "team": "player",
                                "x": p["x"] + math.cos(ang) * 0.5,
                                "y": p["y"] + math.sin(ang) * 0.5,
                                "vx": math.cos(ang) * proj_spd,
                                "vy": math.sin(ang) * proj_spd,
                                "damage": int(w_cfg["damage"]),
                                "ttl": proj_ttl,
                                "color": w_cfg["color"],
                                "radius": 0.16,
                            }
                        )
                    self._emit_sound(p["x"], p["y"], float(w_cfg["sound_radius"]), "gunfire")
                    if p["ammo"] == 0:
                        p["reload_timer"] = float(w_cfg["reload_time"])

        # 2. Update Deployables (Auto-Turret, Nano Healing Field, Sonar)
        for dep in self.deployables:
            dep["ttl"] -= dt
            if dep["type"] == "turret":
                dep["fire_timer"] = max(0.0, dep["fire_timer"] - dt)
                if dep["fire_timer"] <= 0.0:
                    # Find closest visible enemy
                    best_e = None
                    best_d = float(dep["range"])
                    for e in enemies:
                        if e["hp"] > 0:
                            d = math.hypot(e["x"] - dep["x"], e["y"] - dep["y"])
                            if d <= best_d and has_line_of_sight(dep["x"], dep["y"], e["x"], e["y"], walls):
                                best_d = d
                                best_e = e
                    if best_e is not None:
                        ang = math.atan2(best_e["y"] - dep["y"], best_e["x"] - dep["x"])
                        dep["angle"] = ang
                        dep["fire_timer"] = 0.28
                        self.projectiles.append(
                            {
                                "id": uuid.uuid4().hex[:8],
                                "owner_id": dep["owner_id"],
                                "team": "player",
                                "x": dep["x"] + math.cos(ang) * 0.4,
                                "y": dep["y"] + math.sin(ang) * 0.4,
                                "vx": math.cos(ang) * 30.0,
                                "vy": math.sin(ang) * 30.0,
                                "damage": 9,
                                "ttl": 0.5,
                                "color": "#5DADE2",
                                "radius": 0.14,
                            }
                        )
                        self._emit_sound(dep["x"], dep["y"], 7.5, "gunfire")

            elif dep["type"] == "nano_field":
                dep["heal_tick"] -= dt
                if dep["heal_tick"] <= 0.0:
                    dep["heal_tick"] = 0.5
                    for p in self.players.values():
                        if math.hypot(p["x"] - dep["x"], p["y"] - dep["y"]) <= dep["radius"]:
                            if p["downed"]:
                                p["downed"] = False
                                p["hp"] = 30
                            elif p["hp"] < p["max_hp"]:
                                heal_amt = 8 if p["hp"] < p["max_hp"] * 0.5 else 5
                                p["hp"] = min(p["max_hp"], p["hp"] + heal_amt)

        self.deployables = [d for d in self.deployables if d["ttl"] > 0.0]

        # 3. Update Projectiles
        surviving_proj: list[dict[str, Any]] = []
        for proj in self.projectiles:
            proj["ttl"] -= dt
            if proj["ttl"] <= 0.0:
                continue

            ox, oy = proj["x"], proj["y"]
            nx = ox + proj["vx"] * dt
            ny = oy + proj["vy"] * dt

            # Check wall collision
            hit_wall = False
            for w in walls:
                if not w.get("active", True):
                    continue
                if segment_intersects_aabb(ox, oy, nx, ny, w) is not None:
                    hit_wall = True
                    break
            if hit_wall:
                continue

            proj["x"], proj["y"] = nx, ny
            hit_entity = False

            if proj["team"] == "player":
                for e in enemies:
                    if e["hp"] > 0 and math.hypot(e["x"] - nx, e["y"] - ny) <= e["radius"] + proj["radius"]:
                        e["hp"] = max(0, e["hp"] - int(proj["damage"]))
                        if e["state"] == "patrol":
                            e["state"] = "alerted"
                            e["investigate_target"] = (ox, oy)
                            e["alert_timer"] = 6.0
                        if e["hp"] == 0:
                            owner = self.players.get(proj["owner_id"])
                            if owner:
                                owner["kills"] = owner.get("kills", 0) + 1
                        hit_entity = True
                        break
            else:
                for p in self.players.values():
                    if not p["downed"] and math.hypot(p["x"] - nx, p["y"] - ny) <= p["radius"] + proj["radius"]:
                        dmg = int(proj["damage"])
                        if p.get("skill_active") == "shockwave_shield":
                            dmg = 0
                        p["hp"] = max(0, p["hp"] - dmg)
                        if p["hp"] == 0:
                            p["downed"] = True
                        hit_entity = True
                        break

            if not hit_entity:
                surviving_proj.append(proj)

        self.projectiles = surviving_proj

        # 4. Update Enemy AI (Sight & Hearing)
        update_enemies(enemies, self.players, walls, self.sound_events, self.projectiles, dt)

        # 5. Check Mission Victory / Defeat Conditions
        living_players = [p for p in self.players.values() if not p["downed"] and p["hp"] > 0]
        if self.players and not living_players:
            self.phase = "defeat"
            self.events_log.append("All squad members were neutralized. Mission failed.")
            return

        ext = self.map_data["extraction_zone"]
        if ext and ext["active"] and living_players:
            all_inside = True
            for p in living_players:
                inside = point_in_rect(p["x"], p["y"], ext)
                p["extracted"] = inside
                if not inside:
                    all_inside = False
            if all_inside:
                self.phase = "victory"
                self.events_log.append("Squad successfully extracted! Mission Accomplished!")

    def get_snapshot(self) -> dict[str, Any]:
        """
        Builds a serialized state snapshot for connected browser clients.
        Shared Co-op Vision: enemies are included with a `visible` flag indicating if at least
        one player (or active Recon Sonar / Tech Turret) currently has line-of-sight to them.
        """
        if not self.map_data:
            return {
                "type": "state",
                "phase": self.phase,
                "leader_id": self.leader_id,
                "selected_mission_id": self.selected_mission_id,
                "players": list(self.players.values()),
                "events": self.events_log[-5:],
            }

        walls = self.map_data["walls"]
        sonar_active = self.sonar_timer > 0.0

        serialized_enemies = []
        for e in self.map_data["enemies"]:
            if e["hp"] <= 0:
                continue
            visible = sonar_active
            if not visible:
                for p in self.players.values():
                    if p["hp"] <= 0:
                        continue
                    cls_cfg = CHARACTER_CLASSES[p["class_id"]]
                    sight = float(cls_cfg["sight_range"])
                    if math.hypot(e["x"] - p["x"], e["y"] - p["y"]) <= sight:
                        if has_line_of_sight(p["x"], p["y"], e["x"], e["y"], walls):
                            visible = True
                            break
            if not visible:
                for dep in self.deployables:
                    if dep["type"] == "turret":
                        if math.hypot(e["x"] - dep["x"], e["y"] - dep["y"]) <= dep["range"]:
                            if has_line_of_sight(dep["x"], dep["y"], e["x"], e["y"], walls):
                                visible = True
                                break

            serialized_enemies.append(
                {
                    "id": e["id"],
                    "type": e["type"],
                    "name": e["name"],
                    "color": e["color"],
                    "x": round(e["x"], 3),
                    "y": round(e["y"], 3),
                    "angle": round(e["angle"], 3),
                    "hp": e["hp"],
                    "max_hp": e["max_hp"],
                    "radius": e["radius"],
                    "state": e["state"],
                    "visible": visible,
                    "sight_range": ENEMY_TYPES[e["type"]]["sight_range"],
                    "sight_angle": ENEMY_TYPES[e["type"]]["sight_angle"],
                }
            )

        return {
            "type": "state",
            "phase": self.phase,
            "dropship_timer": round(self.dropship_timer, 1),
            "mission_time": round(self.mission_time, 1),
            "leader_id": self.leader_id,
            "selected_mission_id": self.selected_mission_id,
            "players": list(self.players.values()),
            "map": {
                "mission_id": self.map_data["mission_id"],
                "mission_name": self.map_data["mission_name"],
                "width": self.map_data["width"],
                "height": self.map_data["height"],
                "segments": self.map_data["segments"],
                "walls": self.map_data["walls"],
                "puzzles": self.map_data["puzzles"],
                "spaceship": self.map_data["spaceship"],
                "extraction_zone": self.map_data["extraction_zone"],
            },
            "enemies": serialized_enemies,
            "projectiles": self.projectiles,
            "deployables": self.deployables,
            "sounds": self.recent_sounds,
            "sonar_active": sonar_active,
            "events": self.events_log[-6:],
        }
