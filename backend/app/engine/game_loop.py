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
from backend.app.config.items import CLASS_CARRY_CAPACITY, ITEM_TEMPLATES, make_item
from backend.app.config.missions import MISSIONS
from backend.app.engine.ai import update_enemies
from backend.app.engine.inventory import (
    add_item_to_player,
    apply_min_loadout,
    build_player_inventory,
    create_body_chest_from_player,
    deposit_extracted_loot,
    equip_item_from_storage,
    get_active_weapon_config,
    has_tactical_light,
    is_light_weapon,
    recalc_inventory,
    remove_item_from_player,
    return_non_starter_items,
    shared_stash,
    unequip_slot_to_storage,
)
from backend.app.engine.level_store import level_store
from backend.app.engine.map_builder import build_mission_map
from backend.app.engine.audio_events import create_audio_event
from backend.app.engine.sound_occlusion import calculate_occlusion
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
        self.selected_level_id: str | None = level_store.get_level_record("outpost")["level_id"]
        self.phase: str = "lobby"  # 'lobby' | 'prep' | 'dropship' | 'active' | 'victory' | 'defeat'
        self.prep_timer: float = 0.0
        self.dropship_timer: float = 0.0
        self.mission_time: float = 0.0
        self._music_start_t: float = time.time()

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

        inv = build_player_inventory(default_class)
        w_cfg = get_active_weapon_config({"inventory": inv}, cls_cfg["weapon"])
        player = {
            "id": player_id,
            "name": clean_name,
            "class_id": default_class,
            "class_chosen": False,
            "ready": False,
            "x": 4.0,
            "y": 7.5,
            "vx": 0.0,
            "vy": 0.0,
            "angle": 0.0,
            "radius": 0.42,
            "hp": cls_cfg["max_hp"],
            "max_hp": cls_cfg["max_hp"],
            "ammo": w_cfg["mag_size"],
            "max_ammo": w_cfg["mag_size"],
            "reload_timer": 0.0,
            "fire_cooldown": 0.0,
            "skill_cooldown": 0.0,
            "skill_timer": 0.0,
            "skill_active": None,
            "footstep_timer": 0.0,
            "running": False,
            "downed": False,
            "body_dropped": False,
            "extracted": False,
            "kills": 0,
            "inventory": inv,
            "input": {
                "dx": 0.0,
                "dy": 0.0,
                "aim_x": 10.0,
                "aim_y": 8.0,
                "firing": False,
                "running": False,
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
        if self.phase in ("lobby", "prep"):
            return_non_starter_items(p, shared_stash)
        p["class_id"] = class_id
        p["class_chosen"] = True
        p["max_hp"] = cls_cfg["max_hp"]
        p["hp"] = cls_cfg["max_hp"]
        apply_min_loadout(p)
        return True

    def prepare_mission(self, player_id: str) -> bool:
        if player_id != self.leader_id or self.phase not in ("lobby", "prep"):
            return False
        target_id = self.selected_level_id or self.selected_mission_id
        unlocked, reason = level_store.is_level_unlocked(target_id)
        if not unlocked:
            self.last_lock_reason = reason
            return False
        self.phase = "prep"
        self.prep_timer = 0.0
        for p in self.players.values():
            p["ready"] = False
        self.events_log.append("Entered Pre-Mission Loadout Preparation. Equip items from the Shared Loot Crate and click Ready Up!")
        return True

    def restore_min_loadout(self, player_id: str) -> bool:
        p = self.players.get(player_id)
        if not p:
            return False
        if self.phase in ("lobby", "prep"):
            return_non_starter_items(p, shared_stash)
        apply_min_loadout(p)
        self.events_log.append(f"{p['name']} equipped standard minimum loadout.")
        return True

    def toggle_ready(self, player_id: str, ready_state: bool | None = None) -> bool:
        p = self.players.get(player_id)
        if not p or self.phase not in ("lobby", "prep"):
            return False
        p["ready"] = bool(not p.get("ready", False)) if ready_state is None else bool(ready_state)
        all_ready = bool(self.players) and all(pl.get("ready", False) for pl in self.players.values())
        if self.phase == "prep":
            if all_ready:
                if self.prep_timer <= 0.0:
                    self.prep_timer = 5.0
            else:
                self.prep_timer = 0.0
        elif self.phase == "lobby":
            if all_ready and self.leader_id:
                self.prepare_mission(self.leader_id)
        return True

    def stash_to_player(
        self,
        player_id: str,
        template_id: str,
        target_slot: str | None = None,
        target_player_id: str | None = None,
    ) -> dict[str, Any]:
        recipient_id = target_player_id or player_id
        p = self.players.get(recipient_id)
        if not p or self.phase not in ("lobby", "prep"):
            return {"ok": False, "reason": "Only available during preparation"}
        item = shared_stash.take_template(template_id)
        if not item:
            return {"ok": False, "reason": "Item out of stock in shared chest"}
        ok, msg = add_item_to_player(p, item, target_slot)
        if not ok:
            shared_stash.add_template(template_id, 1)
            return {"ok": False, "reason": msg}
        return {"ok": True, "message": msg}

    def player_to_stash(self, player_id: str, item_id: str, target_player_id: str | None = None) -> dict[str, Any]:
        owner_id = target_player_id or player_id
        p = self.players.get(owner_id)
        if not p or self.phase not in ("lobby", "prep"):
            return {"ok": False, "reason": "Only available during preparation"}
        removed = remove_item_from_player(p, item_id)
        if not removed:
            return {"ok": False, "reason": "Item not found on character"}
        tid = removed.get("template_id") or removed.get("item_type")
        if tid:
            shared_stash.add_template(tid, 1)
        return {"ok": True, "message": f"Stored {removed.get('name', 'item')} in shared chest"}

    def set_player_name(self, player_id: str, name: str) -> None:
        if player_id in self.players and name.strip():
            self.players[player_id]["name"] = name.strip()[:20]

    def select_mission(self, player_id: str, mission_id: str, enforce_unlock: bool = False) -> bool:
        if player_id != self.leader_id or self.phase != "lobby":
            return False
        if enforce_unlock:
            unlocked, reason = level_store.is_level_unlocked(mission_id)
            if not unlocked:
                self.last_lock_reason = reason
                self.events_log.append(f"Locked: {reason}")
                return False
        self.last_lock_reason = None
        if mission_id in level_store.levels:
            rec = level_store.levels[mission_id]
            self.selected_level_id = rec["level_id"]
            self.selected_mission_id = rec["mission_id"]
            return True
        if mission_id not in MISSIONS:
            return False
        self.selected_mission_id = mission_id
        rec = level_store.get_level_record(mission_id)
        self.selected_level_id = rec["level_id"]
        return True

    def generate_level(self, player_id: str, mission_id: str | None = None, seed: int | None = None) -> dict[str, Any] | None:
        if player_id != self.leader_id or self.phase != "lobby":
            return None
        mid = mission_id if (mission_id and mission_id in MISSIONS) else self.selected_mission_id
        rec = level_store.generate_level(mid, seed=seed, tested=False)
        self.selected_mission_id = rec["mission_id"]
        self.selected_level_id = rec["level_id"]
        self.events_log.append(f"Generated new WFC level '{rec['name']}' (Status: TESTING REQUIRED).")
        return rec

    def start_mission(self, player_id: str, force_test_mode: bool | None = None) -> bool:
        if player_id != self.leader_id:
            return False
        if not self.players:
            return False

        target_id = self.selected_level_id or self.selected_mission_id
        self.map_data = level_store.instantiate_map(target_id, force_test_mode=force_test_mode)
        spawns = self.map_data["player_spawn_points"]
        for idx, p in enumerate(self.players.values()):
            cls_cfg = CHARACTER_CLASSES[p["class_id"]]
            if not p.get("inventory"):
                apply_min_loadout(p)
            recalc_inventory(p)
            w_cfg = get_active_weapon_config(p, cls_cfg["weapon"])
            sx, sy = spawns[idx % len(spawns)]
            p["x"] = sx
            p["y"] = sy
            p["vx"] = 0.0
            p["vy"] = 0.0
            p["angle"] = 0.0
            p["hp"] = cls_cfg["max_hp"]
            p["max_hp"] = cls_cfg["max_hp"]
            p["ammo"] = int(w_cfg["mag_size"])
            p["max_ammo"] = int(w_cfg["mag_size"])
            p["reload_timer"] = 0.0
            p["fire_cooldown"] = 0.0
            p["skill_cooldown"] = 0.0
            p["skill_timer"] = 0.0
            p["skill_active"] = None
            p["downed"] = False
            p["body_dropped"] = False
            p["extracted"] = False
            p["kills"] = 0
            p["loot_score"] = 0
            p["input"] = {"dx": 0.0, "dy": 0.0, "aim_x": sx + 5.0, "aim_y": sy, "firing": False}

        self.projectiles.clear()
        self.sound_events.clear()
        self.recent_sounds.clear()
        self.deployables.clear()
        self.sonar_timer = 0.0
        self.mission_time = 0.0
        self.dropship_timer = 2.5
        self.phase = "dropship"
        if self.map_data.get("test_mode"):
            self.events_log = [
                f"[TESTING PHASE] Dropship docked at {self.map_data.get('level_name', self.map_data['mission_name'])}. "
                "No enemies or loot — verify objectives & extraction route!"
            ]
        else:
            self.events_log = [f"Dropship docked at {self.map_data.get('level_name', self.map_data['mission_name'])}. Stand by for ramp release..."]
        return True

    def reset_to_lobby(self) -> None:
        self.phase = "lobby"
        self.prep_timer = 0.0
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
        if "running" in data:
            inp["running"] = bool(data["running"])

    def trigger_reload(self, player_id: str) -> None:
        p = self.players.get(player_id)
        if not p or p["downed"] or p["reload_timer"] > 0.0:
            return
        if p["ammo"] < p["max_ammo"]:
            cls_cfg = CHARACTER_CLASSES[p["class_id"]]
            w_cfg = get_active_weapon_config(p, cls_cfg["weapon"])
            p["reload_timer"] = float(w_cfg.get("reload_time", cls_cfg["weapon"]["reload_time"]))
            w_name = w_cfg.get("name", "").lower()
            rel_type = "weapon_reload_shotgun" if "shotgun" in w_name else ("weapon_reload_plasma" if "plasma" in w_name else ("weapon_reload_pistol" if "pistol" in w_name else "weapon_reload_machinegun"))
            self._emit_sound(p["x"], p["y"], 12.0, "reload", sound_type=rel_type)

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
        Handles pressing E near a downed teammate (revive), puzzle terminal, ground item, or supply/body chest.
        """
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.map_data:
            return None

        # 1. Check downed teammates within 2.2 units
        for ally in self.players.values():
            if ally["id"] != p["id"] and ally["downed"]:
                if math.hypot(ally["x"] - p["x"], ally["y"] - p["y"]) <= 2.2:
                    ally["downed"] = False
                    ally["body_dropped"] = False
                    ally["hp"] = int(ally["max_hp"] * 0.5)
                    self.events_log.append(f"{p['name']} revived {ally['name']}!")
                    return {"type": "revived", "target_id": ally["id"]}

        # 2-4. Choose the CLOSEST interactable among puzzle terminals, ground items, and chests
        candidates: list[tuple[float, str, dict[str, Any]]] = []
        for pz in self.map_data["puzzles"]:
            if not pz["solved"]:
                d = math.hypot(pz["x"] - p["x"], pz["y"] - p["y"])
                if d <= pz["radius"]:
                    candidates.append((d, "puzzle", pz))
        for lt in self.map_data.get("loot", []):
            if not lt.get("collected"):
                d = math.hypot(lt["x"] - p["x"], lt["y"] - p["y"])
                if d <= 3.5:
                    candidates.append((d, "loot", lt))
        for cr in self.map_data.get("crates", []):
            d = math.hypot(cr["x"] - p["x"], cr["y"] - p["y"])
            if d <= 3.5:
                candidates.append((d, "crate", cr))
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0])
        _, kind, obj = candidates[0]

        if kind == "puzzle":
            return {"type": "open_puzzle", "puzzle": obj}

        if kind == "loot":
            lt = obj
            ok, msg = add_item_to_player(p, dict(lt))
            if ok:
                lt["collected"] = True
                self.events_log.append(f"{p['name']} picked up {lt['name']} ({lt.get('weight', 1)} wt).")
                return {"type": "picked_up", "item": lt, "message": msg}
            self.events_log.append(f"{p['name']}: {msg}")
            return {"type": "inventory_full", "message": msg}

        cr = obj
        if cr.get("locked") and cr.get("puzzle") and not cr["puzzle"].get("solved"):
            pz = dict(cr["puzzle"])
            pz["x"] = cr["x"]
            pz["y"] = cr["y"]
            pz["radius"] = cr.get("radius", 2.5)
            return {"type": "open_puzzle", "puzzle": pz}
        return {"type": "open_chest", "crate": cr}

    def pickup_ground_item(self, player_id: str, item_id: str) -> dict[str, Any]:
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.map_data:
            return {"ok": False}
        for lt in self.map_data.get("loot", []):
            if lt.get("id") == item_id and not lt.get("collected"):
                if math.hypot(lt["x"] - p["x"], lt["y"] - p["y"]) <= 3.8:
                    ok, msg = add_item_to_player(p, dict(lt))
                    if ok:
                        lt["collected"] = True
                        self.events_log.append(f"{p['name']} picked up {lt['name']}.")
                    return {"ok": ok, "message": msg}
        return {"ok": False, "message": "Item out of range or already taken"}

    def take_crate_item(self, player_id: str, crate_id: str, item_id: str, target_slot: str | None = None) -> dict[str, Any]:
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.map_data:
            return {"ok": False}
        crate = next((c for c in self.map_data.get("crates", []) if c["id"] == crate_id), None)
        if not crate or crate.get("locked"):
            return {"ok": False, "message": "Crate is locked or unavailable"}
        if math.hypot(crate["x"] - p["x"], crate["y"] - p["y"]) > float(crate.get("radius", 2.5)) + 3.0:
            return {"ok": False, "message": "Too far from chest"}
        item_idx = next((i for i, it in enumerate(crate.get("items", [])) if it["id"] == item_id), None)
        if item_idx is None:
            return {"ok": False, "message": "Item already taken by another operative", "crate": crate}
        item = crate["items"][item_idx]
        ok, msg = add_item_to_player(p, item, target_slot)
        if ok:
            crate["items"].pop(item_idx)
            self.events_log.append(f"{p['name']} took {item['name']} from {crate['name']}.")
        return {"ok": ok, "message": msg, "crate": crate}

    def handle_inventory_action(self, player_id: str, data: dict[str, Any]) -> dict[str, Any]:
        p = self.players.get(player_id)
        if not p:
            return {"ok": False}
        act = data.get("action")
        inv = p.get("inventory")
        if not inv:
            apply_min_loadout(p)
            inv = p["inventory"]

        if act == "switch_weapon":
            slot = data.get("slot")
            if slot not in ("primary_weapon", "secondary_weapon"):
                slot = "secondary_weapon" if inv.get("active_weapon_slot") == "primary_weapon" else "primary_weapon"
            if inv["equipped"].get(slot):
                inv["active_weapon_slot"] = slot
                recalc_inventory(p)
                p["ammo"] = p["max_ammo"]
                return {"ok": True, "message": f"Switched to {inv['equipped'][slot]['name']}"}
            return {"ok": False, "message": "No weapon in that slot"}

        if act == "equip_item":
            ok, msg = equip_item_from_storage(p, str(data.get("item_id", "")), data.get("target_slot"))
            return {"ok": ok, "message": msg}

        if act == "unequip_slot":
            ok, msg = unequip_slot_to_storage(p, str(data.get("slot", "")))
            return {"ok": ok, "message": msg}

        if act == "use_item":
            item_id = str(data.get("item_id", ""))
            slot = data.get("slot")
            target = None
            from_equipped = False
            if slot == "quick_use" and inv["equipped"].get("quick_use"):
                target = inv["equipped"]["quick_use"]
                from_equipped = True
            else:
                target = next((it for it in inv["storage"] if it.get("id") == item_id), None)
                if not target and inv["equipped"].get("quick_use") and inv["equipped"]["quick_use"].get("id") == item_id:
                    target = inv["equipped"]["quick_use"]
                    from_equipped = True
            if not target:
                return {"ok": False, "message": "Item not found"}

            tid = target.get("template_id") or target.get("item_type")
            val = target.get("value") if isinstance(target.get("value"), dict) else {}
            used = False
            if tid in ("medkit", "stim_injector") or "heal" in val:
                heal = int(val.get("heal", 45 if tid == "medkit" else 25))
                p["hp"] = min(p["max_hp"], p["hp"] + heal)
                self.events_log.append(f"{p['name']} used {target['name']} (+{heal} HP)!")
                used = True
            elif tid in ("ammo_pack", "heavy_ammo") or "ammo" in val:
                p["ammo"] = p["max_ammo"]
                p["reload_timer"] = 0.0
                self.events_log.append(f"{p['name']} resupplied ammo using {target['name']}!")
                used = True
            elif tid == "portable_turret" or val.get("deploy_turret"):
                if self.map_data:
                    tx = p["x"] + math.cos(p["angle"]) * 1.1
                    ty = p["y"] + math.sin(p["angle"]) * 1.1
                    self.deployables.append({
                        "id": uuid.uuid4().hex[:8],
                        "type": "turret",
                        "owner_id": p["id"],
                        "x": tx,
                        "y": ty,
                        "angle": p["angle"],
                        "ttl": 10.0,
                        "fire_timer": 0.0,
                        "range": 10.5,
                    })
                    self.events_log.append(f"{p['name']} deployed a Portable Turret!")
                    used = True
            elif tid == "sonar_beacon" or val.get("deploy_sonar"):
                if self.map_data:
                    self.sonar_timer = max(self.sonar_timer, 4.0)
                    self.deployables.append({
                        "id": uuid.uuid4().hex[:8],
                        "type": "sonar_pulse",
                        "owner_id": p["id"],
                        "x": p["x"],
                        "y": p["y"],
                        "radius": 18.0,
                        "ttl": 4.0,
                    })
                    self.events_log.append(f"{p['name']} activated a Sonar Beacon!")
                    used = True

            if used and target.get("consumable", True):
                if from_equipped:
                    inv["equipped"]["quick_use"] = None
                else:
                    inv["storage"] = [it for it in inv["storage"] if it.get("id") != target.get("id")]
                recalc_inventory(p)
            return {"ok": used, "message": f"Used {target['name']}" if used else "Passive item"}

        if act == "drop_item" and self.map_data:
            removed = remove_item_from_player(p, str(data.get("item_id", "")))
            if removed:
                removed.update({
                    "x": round(p["x"] + 0.6, 2),
                    "y": round(p["y"] + 0.6, 2),
                    "radius": 2.0,
                    "collected": False,
                })
                self.map_data.setdefault("loot", []).append(removed)
                self.events_log.append(f"{p['name']} dropped {removed['name']}.")
                return {"ok": True}

        return {"ok": False}

    def handle_puzzle_action(self, player_id: str, puzzle_id: str, action: dict[str, Any]) -> dict[str, Any]:
        """
        Processes interactive puzzle actions from a player (for both bulkhead doors and skill-check supply crates).
        """
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.map_data:
            return {"ok": False}

        puzzle = next((pz for pz in self.map_data["puzzles"] if pz["id"] == puzzle_id), None)
        target_crate = None
        if not puzzle:
            for cr in self.map_data.get("crates", []):
                if cr.get("puzzle") and cr["puzzle"].get("id") == puzzle_id:
                    puzzle = cr["puzzle"]
                    target_crate = cr
                    break
        if not puzzle or puzzle["solved"]:
            return {"ok": False, "solved": True}

        px = target_crate["x"] if target_crate else puzzle["x"]
        py = target_crate["y"] if target_crate else puzzle["y"]
        prad = target_crate.get("radius", 2.5) if target_crate else puzzle.get("radius", 2.8)
        if math.hypot(px - p["x"], py - p["y"]) > prad + 1.5:
            return {"ok": False, "reason": "Too far from terminal"}

        pdata = puzzle["data"]
        action_type = action.get("action")

        def finish_solve():
            if target_crate is not None:
                puzzle["solved"] = True
                target_crate["locked"] = False
                self.events_log.append(f"{p['name']} passed skill check and unlocked {target_crate['name']}!")
            else:
                self._complete_puzzle(puzzle, p["name"])

        if action_type == "tech_bypass" and p["class_id"] == "tech":
            finish_solve()
            return {"ok": True, "solved": True, "puzzle": puzzle, "unlocked_crate": target_crate}

        if puzzle["type"] == "power_reroute":
            idx = int(action.get("node_index", 0))
            if 0 <= idx < len(pdata["nodes"]):
                pdata["nodes"][idx] = (pdata["nodes"][idx] + 1) % 4
                if pdata["nodes"] == pdata["target"]:
                    finish_solve()
                    return {"ok": True, "solved": True, "puzzle": puzzle, "unlocked_crate": target_crate}

        elif puzzle["type"] == "signal_decrypt":
            symbol = str(action.get("symbol", ""))
            prog = int(pdata["progress"])
            target_seq = pdata["target_sequence"]
            if prog < len(target_seq):
                if symbol == target_seq[prog]:
                    pdata["progress"] = prog + 1
                    if pdata["progress"] >= len(target_seq):
                        finish_solve()
                        return {"ok": True, "solved": True, "puzzle": puzzle, "unlocked_crate": target_crate}
                else:
                    pdata["progress"] = 0
                    self._emit_sound(px, py, 14.0, "alarm")
                    self.events_log.append(f"Security alarm triggered at {puzzle['label']}!")

        return {"ok": True, "solved": puzzle["solved"], "puzzle": puzzle, "unlocked_crate": target_crate if puzzle["solved"] else None}

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

    def _emit_sound(self, x: float, y: float, radius: float, kind: str = "gunfire", sound_type: str = "", source_id: str = "") -> None:
        evt = {
            "id": uuid.uuid4().hex[:8],
            "x": x,
            "y": y,
            "radius": radius,
            "kind": kind,
            "ttl": 0.45,
            "source_id": source_id,
        }
        if sound_type:
            try:
                audio_evt = create_audio_event(sound_type, x, y, source_id=source_id)
                evt.update(audio_evt)
            except Exception:
                pass
        self.sound_events.append(evt)
        self.recent_sounds.append(dict(evt))

    def step(self, dt: float) -> None:
        if self.phase == "prep":
            if self.players and all(pl.get("ready", False) for pl in self.players.values()):
                if self.prep_timer <= 0.0:
                    self.prep_timer = 5.0
                self.prep_timer = max(0.0, self.prep_timer - dt)
                if self.prep_timer <= 0.0 and self.leader_id:
                    self.start_mission(self.leader_id)
            else:
                self.prep_timer = 0.0
            return

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
            w_cfg = get_active_weapon_config(p, cls_cfg["weapon"])

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

            # Aim angle toward mouse world coordinates
            aim_dx = float(inp["aim_x"]) - p["x"]
            aim_dy = float(inp["aim_y"]) - p["y"]
            if abs(aim_dx) > 1e-4 or abs(aim_dy) > 1e-4:
                p["angle"] = math.atan2(aim_dy, aim_dx)

            want_run = bool(inp.get("running", False))
            is_running = False

            if mag > 0.05:
                # Forward vector pointing in operative's facing/aim angle
                fwd_x = math.cos(p["angle"])
                fwd_y = math.sin(p["angle"])
                norm_dx = dx / mag
                norm_dy = dy / mag
                fwd_dot = norm_dx * fwd_x + norm_dy * fwd_y

                # Running mechanism: only permitted when moving forward (fwd_dot > 0.20)
                # When running, movement is locked strictly forward along facing direction
                if want_run and fwd_dot > 0.20:
                    is_running = True
                    sprint_spd = float(cls_cfg.get("sprint_speed", float(cls_cfg["speed"]) * 1.7))
                    p["vx"] = fwd_x * sprint_spd
                    p["vy"] = fwd_y * sprint_spd
                else:
                    walk_spd = float(cls_cfg["speed"])
                    p["vx"] = dx * walk_spd
                    p["vy"] = dy * walk_spd
            else:
                p["vx"] = 0.0
                p["vy"] = 0.0

            p["running"] = is_running

            if abs(p["vx"]) > 0.01 or abs(p["vy"]) > 0.01:
                nx, ny = move_with_collisions(p["x"], p["y"], p["vx"], p["vy"], dt, p["radius"], walls)
                p["x"], p["y"] = nx, ny
                p["footstep_timer"] -= dt
                if p["footstep_timer"] <= 0.0:
                    base_noise = float(cls_cfg.get("footstep_radius", 3.0))
                    if is_running:
                        p["footstep_timer"] = 0.28
                        # Running is louder (approx 2.2x noise radius)
                        self._emit_sound(p["x"], p["y"], base_noise * 2.2, "footstep", sound_type="player_footstep")
                    else:
                        p["footstep_timer"] = 0.52
                        # Walking generates less noise (approx 0.5x noise radius)
                        self._emit_sound(p["x"], p["y"], base_noise * 0.5, "footstep", sound_type="player_footstep")

            # Firing weapon: When running, player can only shoot if using a light weapon (like a pistol)
            can_fire = not is_running or is_light_weapon(p, w_cfg)
            if inp.get("firing") and can_fire and p["fire_cooldown"] <= 0.0 and p["reload_timer"] <= 0.0:
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
                    w_name = w_cfg.get("name", "").lower()
                    st = "weapon_fire_shotgun" if "shotgun" in w_name else ("weapon_fire_plasma" if "plasma" in w_name else ("weapon_fire_pistol" if "pistol" in w_name else "weapon_fire_machinegun"))
                    self._emit_sound(p["x"], p["y"], float(w_cfg["sound_radius"]), "gunfire", sound_type=st, source_id=p["id"])
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
                        self._emit_sound(dep["x"], dep["y"], 7.5, "gunfire", sound_type="weapon_fire_machinegun")

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

        # 4b. Convert newly downed/dead player inventory into persistent body chest on map
        for p in self.players.values():
            if p["downed"] and not p.get("body_dropped", False):
                p["body_dropped"] = True
                body_chest = create_body_chest_from_player(p)
                if body_chest is not None:
                    self.map_data.setdefault("crates", []).append(body_chest)
                    self.events_log.append(f"{p['name']} fell! Their body chest dropped with their inventory.")

        living_players = [p for p in self.players.values() if not p["downed"] and p["hp"] > 0]

        # 5. Check Mission Victory / Defeat Conditions
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
                lvl_id = self.map_data.get("level_id")
                if self.map_data.get("test_mode") and lvl_id:
                    level_store.mark_tested(lvl_id)
                    level_store.mark_completed(lvl_id)
                    self.map_data["level_tested"] = True
                    self.events_log.append(
                        f"Level '{self.map_data.get('level_name')}' passed testing phase! Marked VERIFIED & next level unlocked."
                    )
                else:
                    if lvl_id:
                        level_store.mark_completed(lvl_id)
                    banked = 0
                    for p in living_players:
                        banked += deposit_extracted_loot(p, shared_stash)
                    self.events_log.append(
                        f"Squad extracted! {banked} items deposited into Shared Chest & next level unlocked!"
                    )

    def get_snapshot(self, listener_id: str | None = None) -> dict[str, Any]:
        """
        Builds a serialized state snapshot for connected browser clients.
        Shared Co-op Vision: enemies are included with a `visible` flag indicating if at least
        one player (or active Recon Sonar / Tech Turret) currently has line-of-sight to them.
        """
        if not self.map_data:
            return {
                "type": "state",
                "phase": self.phase,
                "prep_timer": round(self.prep_timer, 1),
                "leader_id": self.leader_id,
                "selected_mission_id": self.selected_mission_id,
                "selected_level_id": self.selected_level_id,
                "last_lock_reason": getattr(self, "last_lock_reason", None),
                "levels": level_store.list_levels(),
                "shared_stash": shared_stash.get_stacked_list(),
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
                    sight = float(cls_cfg["sight_range"]) + (4.5 if has_tactical_light(p) else 0.0)
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

                # Music State determination (explore vs combat vs extraction)
        music_state = "explore"
        if self.phase in ("victory", "defeat"):
            music_state = "lobby"
        elif self.map_data.get("extraction_zone", {}).get("active", False):
            music_state = "combat"
        else:
            # Check if any alive enemy is actively targeting a player
            for e in self.map_data.get("enemies", []):
                if e.get("hp", 0) > 0 and e.get("state") in ("aggro", "chase", "attacking"):
                    music_state = "combat"
                    break

        # Process per-listener spatial & occluded audio events for newly created sounds
        processed_audio_events = []
        listener = self.players.get(listener_id) if listener_id else None
        lx, ly = (listener["x"], listener["y"]) if listener else (0.0, 0.0)

        for s in self.sound_events:
            st = s.get("sound_type")
            if not st:
                continue
            sx, sy = s.get("x", 0.0), s.get("y", 0.0)
            if listener:
                dist = math.hypot(sx - lx, sy - ly)
                rmax = s.get("radius_max", 35.0)
                if dist > rmax:
                    continue  # culled outside hearing range
                if s.get("occlude", True) and self.map_data:
                    gain_mult, lpf, blocks = calculate_occlusion(sx, sy, lx, ly, walls)
                else:
                    gain_mult, lpf, blocks = 1.0, 20000.0, 0
            else:
                gain_mult, lpf, blocks = 1.0, 20000.0, 0

            processed_audio_events.append({
                "sound_type": st,
                "source_id": s.get("source_id", ""),
                "x": round(sx, 2),
                "y": round(sy, 2),
                "radius_min": s.get("radius_min", 2.0),
                "radius_max": s.get("radius_max", 25.0),
                "base_gain": s.get("base_gain", 1.0),
                "occl_gain": round(gain_mult, 3),
                "lpf_cutoff": round(lpf, 1),
            })

        return {
            "type": "state",
            "phase": self.phase,
            "music": {
                "state": music_state,
                "bpm": 118,
                "start_t": getattr(self, "_music_start_t", 0.0),
            },
            "audio_events": processed_audio_events,
            "dropship_timer": round(self.dropship_timer, 1),
            "mission_time": round(self.mission_time, 1),
            "leader_id": self.leader_id,
            "selected_mission_id": self.selected_mission_id,
            "selected_level_id": self.selected_level_id,
            "levels": level_store.list_levels(),
            "shared_stash": shared_stash.get_stacked_list(),
            "players": list(self.players.values()),
            "map": {
                "level_id": self.map_data.get("level_id"),
                "level_name": self.map_data.get("level_name", self.map_data["mission_name"]),
                "test_mode": bool(self.map_data.get("test_mode", False)),
                "level_tested": bool(self.map_data.get("level_tested", True)),
                "mission_id": self.map_data["mission_id"],
                "mission_name": self.map_data["mission_name"],
                "width": self.map_data["width"],
                "height": self.map_data["height"],
                "segments": self.map_data["segments"],
                "connectors": self.map_data.get("connectors", []),
                "walls": self.map_data["walls"],
                "puzzles": self.map_data["puzzles"],
                "wires": self.map_data.get("wires", []),
                "loot": [lt for lt in self.map_data.get("loot", []) if not lt.get("collected")],
                "crates": self.map_data.get("crates", []),
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
