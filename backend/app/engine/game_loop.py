"""
Authoritative multiplayer game room, lobby state, combat, skills, puzzles, and extraction loop.
"""

from __future__ import annotations

import math
import copy
import random
import uuid
from typing import Any
from pydantic import ValidationError
from backend.app.commands import Command, PuzzleAction

from backend.app.config.classes import CHARACTER_CLASSES, ENEMY_TYPES
from backend.app.config.items import ITEM_TEMPLATES, make_item
from backend.app.config.missions import MISSIONS
from backend.app.content import CATALOG, RULES, mutable_copy, public_payload
from backend.app.engine.ai import update_enemies
from backend.app.engine import puzzles as puzzle_handlers, objectives
from backend.app.engine.combat import allied, apply_damage, can_target, faction
from backend.app.engine.inventory import (
    add_item_to_player,
    active_weapon_item,
    bankable,
    apply_min_loadout,
    build_player_inventory,
    create_body_chest_from_player,
    deposit_squad_loot,
    equip_item_from_storage,
    get_active_weapon_config,
    is_light_weapon,
    item_effect,
    sight_boost,
    recalc_inventory,
    remove_item_from_player,
    return_non_starter_items,
    SharedStash,
    sync_weapon_state,
    unequip_slot_to_storage,
)
from backend.app.engine.level_store import LevelStore
from backend.app.engine.persistence import SaveError
from backend.app.engine.map_builder import build_mission_map
from backend.app.engine.audio_events import create_audio_event
from backend.app.engine.sound_occlusion import calculate_occlusion, clear_occlusion_cache
from backend.app.engine.props import world_surfaces
from backend.app.engine.worlds import instantiate_world
from backend.app.engine.physics import (
    circle_vs_aabb,
    has_line_of_sight,
    move_with_collisions,
    point_in_rect,
    segment_intersects_circle,
    swept_circle_aabb_entry,
)


class GameRoom:
    """
    Manages the co-op lobby, leader privileges, and 30Hz authoritative mission simulation.
    """

    def __init__(
        self,
        level_store: LevelStore | None = None,
        shared_stash: SharedStash | None = None,
        *,
        rng: random.Random | None = None,
        catalog=CATALOG,
        hub_world: str | None = None,
    ) -> None:
        self.level_store = level_store if level_store is not None else LevelStore(load=False)
        self.shared_stash = shared_stash if shared_stash is not None else SharedStash(load=False)
        self.rng = rng if rng is not None else random.Random()
        self.catalog = catalog
        if hub_world is not None and (hub_world not in catalog.definitions["worlds"] or catalog.definitions["worlds"][hub_world]["kind"] != "hub"):
            raise ValueError("Unknown hub world")
        self.hub_world = hub_world
        self.combat_rules = RULES["combat"]
        self.players: dict[str, dict[str, Any]] = {}
        self.join_order: list[str] = []
        self.leader_id: str | None = None
        self.selected_mission_id: str = "outpost"
        self.selected_level_id: str | None = next(
            (r["level_id"] for r in self.level_store.levels.values() if r["mission_id"] == "outpost"), None
        )
        self.phase: str = "lobby"  # 'lobby' | 'prep' | 'dropship' | 'active' | 'victory' | 'defeat'
        self.prep_timer: float = 0.0
        self.dropship_timer: float = 0.0
        self.mission_time: float = 0.0
        self._music_start_t: float = 0.0
        self.simulation_time = 0.0
        self.event_sequence = 0
        self._event_namespace = uuid.uuid4().hex
        self._listener_event_floor = {}

        self.map_data: dict[str, Any] | None = None
        self.projectiles: list[dict[str, Any]] = []
        self.sound_events: list[dict[str, Any]] = []
        self.pending_sound_events: list[dict[str, Any]] = []
        self.recent_sounds: list[dict[str, Any]] = []
        self.deployables: list[dict[str, Any]] = []  # turrets, healing fields, sonar pulses
        self.events_log: list[str] = []
        self.pending_disconnects: set[str] = set()
        self.interaction_requests = {}
        self.interaction_revision = 0

    @property
    def world_capabilities(self):
        return self.map_data.get("capabilities", self.catalog.definitions["world_rules"]["mission"]) if self.map_data else {}

    @property
    def world_running(self):
        return self.map_data is not None and self.phase in ("lobby", "prep", "dropship", "active")

    def system_enabled(self, system):
        return self.world_running and self.world_capabilities.get(system, False)

    def _service_error(self, player_id, kind, target_id=None):
        if not self.map_data or self.map_data.get("world_kind") != "hub":
            return None
        target = next((t for t in self.map_data.get("interactions", []) if t["id"] == target_id and t["type"] == kind), None)
        return self.interaction_error(self.players.get(player_id), "service", target)

    def _reset_world_presentation(self):
        self.projectiles.clear()
        self.deployables.clear()
        self.sound_events.clear()
        self.pending_sound_events.clear()
        self.recent_sounds.clear()
        self.interaction_requests.clear()
        self.interaction_revision += 1
        self._music_start_t = self.simulation_time
        self.mission_time = 0.0
        self.dropship_timer = 0.0
        clear_occlusion_cache()
        self._neutral_players()

    def _neutral_players(self):
        for p in self.players.values():
            p["input"].update(dx=0.0, dy=0.0, firing=False, running=False)
            p.update(vx=0.0, vy=0.0, running=False)
            p.pop("input_age", None)

    def initialize(self) -> None:
        """Load default stores explicitly at application startup, never on import."""
        if not self.level_store.loaded:
            self.level_store.load()
        if not self.shared_stash.loaded:
            self.shared_stash.load()
        if self.selected_level_id is None:
            self.selected_level_id = self.level_store.get_level_record("outpost")["level_id"]
        if self.hub_world and self.map_data is None:
            self.reset_to_lobby()

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
            "faction": cls_cfg["faction"],
            "class_chosen": False,
            "ready": False,
            "x": 4.0,
            "y": 7.5,
            "vx": 0.0,
            "vy": 0.0,
            "angle": 0.0,
            "radius": RULES["player"]["radius"],
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
        self._listener_event_floor[player_id] = self.event_sequence
        recalc_inventory(player)
        if player_id not in self.join_order:
            self.join_order.append(player_id)
        if self.leader_id is None or self.leader_id not in self.players:
            self.leader_id = player_id

        # If joining mid-mission, spawn near dropship or a living ally
        if self.world_running:
            spawns = self.map_data["player_spawn_points"]
            sx, sy = spawns[len(self.players) % len(spawns)]
            player["x"], player["y"] = sx, sy

        return player

    def remove_player(self, player_id: str) -> None:
        player = self.players.get(player_id)
        if player:
            try:
                if self.system_enabled("combat"):
                    staged = copy.deepcopy(player)
                    chest = create_body_chest_from_player(staged)
                    self.shared_stash.commit_player(player, staged, world_items=chest["items"] if chest else ())
                    player.update(staged)
                    if chest:
                        self.map_data.setdefault("crates", []).append(chest)
                else:
                    return_non_starter_items(player, self.shared_stash)
                if len(self.players) == 1:
                    self._settle_world_items()
            except SaveError:
                self.pending_disconnects.add(player_id)
                player["input"].update(dx=0.0, dy=0.0, firing=False, running=False)
                return
        self.pending_disconnects.discard(player_id)
        self.players.pop(player_id, None)
        self._listener_event_floor.pop(player_id, None)
        self.interaction_requests.pop(player_id, None)
        if player_id in self.join_order:
            self.join_order.remove(player_id)
        if self.leader_id == player_id:
            self.leader_id = self.join_order[0] if self.join_order else None
        if not self.players and self.phase != "lobby":
            self.reset_to_lobby()

    def select_class(self, player_id: str, class_id: str) -> bool:
        if self.phase not in ("lobby", "prep") or class_id not in CHARACTER_CLASSES or player_id not in self.players:
            return False
        p = self.players[player_id]
        cls_cfg = CHARACTER_CLASSES[class_id]
        if self.phase in ("lobby", "prep"):
            return_non_starter_items(p, self.shared_stash)
        p["class_id"] = class_id
        p["faction"] = cls_cfg["faction"]
        p["class_chosen"] = True
        p["max_hp"] = cls_cfg["max_hp"]
        p["hp"] = cls_cfg["max_hp"]
        apply_min_loadout(p)
        return True

    def prepare_mission(self, player_id: str, target_id=None) -> bool:
        if player_id != self.leader_id or self.phase not in ("lobby", "prep"):
            return False
        if self._service_error(player_id, "mission_selection", target_id):
            return False
        target_id = self.selected_level_id or self.selected_mission_id
        unlocked, reason = self.level_store.is_level_unlocked(target_id)
        if not unlocked:
            self.last_lock_reason = reason
            return False
        self.phase = "prep"
        self._reset_world_presentation()
        self.prep_timer = 0.0
        for p in self.players.values():
            p["ready"] = False
        self.events_log.append("Entered Pre-Mission Loadout Preparation. Equip items from the Shared Loot Crate and click Ready Up!")
        return True

    def restore_min_loadout(self, player_id: str) -> bool:
        p = self.players.get(player_id)
        if not p or self.phase not in ("lobby", "prep"):
            return False
        if self.phase in ("lobby", "prep"):
            return_non_starter_items(p, self.shared_stash)
        apply_min_loadout(p)
        self.events_log.append(f"{p['name']} equipped standard minimum loadout.")
        return True

    def toggle_ready(self, player_id: str, ready_state: bool | None = None) -> bool:
        p = self.players.get(player_id)
        if not p or self.phase not in ("lobby", "prep") or (ready_state is not None and type(ready_state) is not bool):
            return False
        p["ready"] = bool(not p.get("ready", False)) if ready_state is None else bool(ready_state)
        all_ready = bool(self.players) and all(pl.get("ready", False) for pl in self.players.values())
        if self.phase == "prep" and not (self.map_data and self.map_data.get("world_kind") == "hub"):
            if all_ready:
                if self.prep_timer <= 0.0:
                    self.prep_timer = RULES["timing"]["prep_countdown"]
            else:
                self.prep_timer = 0.0
        elif self.phase == "lobby" and not (self.map_data and self.map_data.get("world_kind") == "hub"):
            if all_ready and self.leader_id:
                self.prepare_mission(self.leader_id)
        return True

    def stash_to_player(
        self,
        player_id: str,
        template_id: str,
        target_slot: str | None = None,
        target_player_id: str | None = None,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        recipient_id = target_player_id or player_id
        p = self.players.get(recipient_id)
        if player_id not in self.players or not p or self.phase not in ("lobby", "prep"):
            return {"ok": False, "reason": "Only available during preparation"}
        error = self._service_error(player_id, "stash", target_id)
        if error:
            return {"ok": False, "reason": error}
        if template_id not in ITEM_TEMPLATES:
            return {"ok": False, "reason": "Unknown item template"}
        if self.shared_stash.stacks.get(template_id, 0) <= 0:
            return {"ok": False, "reason": "Item out of stock in shared chest"}
        item = make_item(template_id)
        staged = copy.deepcopy(p)
        ok, msg = add_item_to_player(staged, item, target_slot)
        if not ok:
            return {"ok": False, "reason": msg}
        stacks = dict(self.shared_stash.stacks)
        stacks[template_id] -= 1
        if not stacks[template_id]:
            stacks.pop(template_id)
        self.shared_stash.commit_player(p, staged, stacks=stacks)
        p.update(staged)
        return {"ok": True, "message": msg}

    def player_to_stash(self, player_id: str, item_id: str, target_player_id: str | None = None, target_id=None) -> dict[str, Any]:
        owner_id = target_player_id or player_id
        p = self.players.get(owner_id)
        if player_id not in self.players or not p or self.phase not in ("lobby", "prep"):
            return {"ok": False, "reason": "Only available during preparation"}
        error = self._service_error(player_id, "stash", target_id)
        if error:
            return {"ok": False, "reason": error}
        staged = copy.deepcopy(p)
        removed = remove_item_from_player(staged, item_id)
        if not removed:
            return {"ok": False, "reason": "Item not found on character"}
        if not bankable(removed):
            return {"ok": False, "reason": "Starter items cannot be banked"}
        tid = removed["template_id"]
        stacks = dict(self.shared_stash.stacks)
        stacks[tid] = stacks.get(tid, 0) + 1
        self.shared_stash.commit_player(p, staged, stacks=stacks)
        p.update(staged)
        return {"ok": True, "message": f"Stored {removed.get('name', 'item')} in shared chest"}

    def set_player_name(self, player_id: str, name: str) -> bool:
        if isinstance(name, str) and player_id in self.players and name.strip() and len(name) <= 20:
            self.players[player_id]["name"] = name.strip()[:20]
            return True
        return False

    def select_mission(self, player_id: str, mission_id: str, enforce_unlock: bool = False, target_id=None) -> bool:
        if player_id != self.leader_id or self.phase != "lobby":
            return False
        if self._service_error(player_id, "mission_selection", target_id):
            return False
        if enforce_unlock:
            unlocked, reason = self.level_store.is_level_unlocked(mission_id)
            if not unlocked:
                self.last_lock_reason = reason
                self.events_log.append(f"Locked: {reason}")
                return False
        self.last_lock_reason = None
        if mission_id in self.level_store.levels:
            rec = self.level_store.levels[mission_id]
            self.selected_level_id = rec["level_id"]
            self.selected_mission_id = rec["mission_id"]
            return True
        if mission_id not in MISSIONS:
            return False
        self.selected_mission_id = mission_id
        rec = self.level_store.get_level_record(mission_id)
        self.selected_level_id = rec["level_id"]
        return True

    def generate_level(self, player_id: str, mission_id: str | None = None, seed: int | None = None) -> dict[str, Any] | None:
        if player_id != self.leader_id or self.phase != "lobby":
            return None
        if mission_id is not None and mission_id not in MISSIONS:
            return None
        if seed is not None and (type(seed) is not int or not 0 <= seed <= 2**31 - 1):
            return None
        mid = mission_id or self.selected_mission_id
        rec = self.level_store.generate_level(mid, seed=seed, tested=False)
        self.selected_mission_id = rec["mission_id"]
        self.selected_level_id = rec["level_id"]
        self.events_log.append(f"Generated new WFC level '{rec['name']}' (Status: TESTING REQUIRED).")
        return rec

    def start_mission(self, player_id: str, force_test_mode: bool | None = None, target_id=None) -> bool:
        if player_id != self.leader_id or self.phase not in ("lobby", "prep"):
            return False
        if not self.players:
            return False
        if self.map_data and self.map_data.get("world_kind") == "hub":
            if self._service_error(player_id, "mission_selection", target_id) or self.phase != "prep" or not all(p.get("ready") for p in self.players.values()):
                return False

        target_id = self.selected_level_id or self.selected_mission_id
        unlocked, reason = self.level_store.is_level_unlocked(target_id)
        if not unlocked:
            self.last_lock_reason = reason
            return False
        if force_test_mode is not None and type(force_test_mode) is not bool:
            return False
        candidate = self.level_store.instantiate_map(target_id, force_test_mode=force_test_mode)
        candidate.setdefault("world_kind", "mission")
        candidate.setdefault("world_name", candidate["mission_name"])
        candidate["capabilities"] = mutable_copy(self.catalog.definitions["world_rules"]["mission"])
        # Reject invalid authored/snapshot objective references before publishing a world.
        objectives.map_objectives(copy.deepcopy(candidate), catalog=self.catalog)
        self._settle_world_items()
        self.map_data = candidate
        clear_occlusion_cache()
        self._music_start_t = self.simulation_time
        spawns = self.map_data["player_spawn_points"]
        for idx, p in enumerate(self.players.values()):
            cls_cfg = CHARACTER_CLASSES[p["class_id"]]
            if not p.get("inventory"):
                apply_min_loadout(p)
            recalc_inventory(p)
            sx, sy = spawns[idx % len(spawns)]
            p["x"] = sx
            p["y"] = sy
            p["vx"] = 0.0
            p["vy"] = 0.0
            p["angle"] = 0.0
            p["hp"] = cls_cfg["max_hp"]
            p["max_hp"] = cls_cfg["max_hp"]
            p["fire_cooldown"] = 0.0
            p["skill_cooldown"] = 0.0
            p["skill_timer"] = 0.0
            p["skill_active"] = None
            p["downed"] = False
            p["body_dropped"] = False
            p["extracted"] = False
            p["kills"] = 0
            p.pop("last_damage", None)
            p["loot_score"] = 0
            p["input"] = {"dx": 0.0, "dy": 0.0, "aim_x": sx + 5.0, "aim_y": sy, "firing": False}

        self._reset_world_presentation()
        self.dropship_timer = RULES["timing"]["dropship_countdown"]
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
        candidate = instantiate_world(self.hub_world, self.catalog) if self.hub_world else None
        self._settle_world_items()
        self.phase = "lobby"
        self.prep_timer = 0.0
        self.map_data = candidate
        self._reset_world_presentation()
        for index, p in enumerate(self.players.values()):
            p["ready"] = False
            p["downed"] = False
            p["extracted"] = False
            p["input"].update(dx=0.0, dy=0.0, firing=False, running=False)
            p.update(vx=0.0, vy=0.0, running=False)
            p["hp"] = p["max_hp"]
            p.update(skill_timer=0.0, skill_active=None, body_dropped=False)
            if candidate:
                p["x"], p["y"] = candidate["player_spawn_points"][index % len(candidate["player_spawn_points"])]
                if not any(p["inventory"]["equipped"].values()) and not p["inventory"]["storage"]:
                    apply_min_loadout(p)
        self.events_log = ["Entered " + candidate["world_name"]] if candidate else self.events_log

    def _settle_world_items(self) -> None:
        stacks = dict(self.shared_stash.stacks)
        holdings = copy.deepcopy(self.shared_stash.holdings)
        settled = set()
        for identity, record in list(holdings.items()):
            if record["owner"] == "world":
                tid = record["template_id"]
                stacks[tid] = stacks.get(tid, 0) + 1
                holdings.pop(identity)
                settled.add(identity)
        if holdings != self.shared_stash.holdings:
            self.shared_stash.commit(stacks, holdings)
            # A hub may remain alive after the last disconnect; banked items
            # must no longer be collectible from that same world.
            if self.map_data:
                for item in self.map_data.get("loot", []):
                    if item["id"] in settled:
                        item["collected"] = True
                for crate in self.map_data.get("crates", []):
                    crate["items"] = [item for item in crate.get("items", []) if item["id"] not in settled]
                self.interaction_revision += 1

    def open_spaceship_ramp(self) -> None:
        if not self.map_data:
            return
        ship = self.map_data.get("spaceship")
        if not ship:
            return
        if not ship["ramp_open"]:
            ship["ramp_open"] = True
            for w in self.map_data["walls"]:
                if w["id"] == ship["door_wall_id"]:
                    w["active"] = False
                    w["blocks_sight"] = False
                    self.map_data["geometry_revision"] = self.map_data.get("geometry_revision", 0) + 1
                    self._emit_sound(w["x"] + w["w"] / 2, w["y"] + w["h"] / 2, 0, "door",
                                     sound_type=RULES["audio"]["door_sound"], source_id=w["id"])
            self.events_log.append("Spaceship ramp lowered! Exit the ship and complete mission objectives.")

    def update_input(self, player_id: str, data: dict[str, Any]) -> bool:
        p = self.players.get(player_id)
        if not p:
            return False
        try:
            data = Command.model_validate({**data, "type": "input"}).model_dump(exclude_unset=True)
        except (TypeError, ValidationError):
            return False
        if data.get("firing") and not self.system_enabled("combat"):
            return False
        if p["downed"] or not self.system_enabled("movement"):
            if data.get("dx") != 0 or data.get("dy") != 0 or data.get("firing") is not False or data.get("running") is not False:
                return False
            p["input"].update(dx=0.0, dy=0.0, firing=False, running=False)
            p.update(vx=0.0, vy=0.0, running=False)
            return True
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
        p["input_age"] = 0.0
        return True

    def trigger_reload(self, player_id: str) -> bool:
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.system_enabled("combat"):
            return False
        sync_weapon_state(p)
        if not active_weapon_item(p) or p["reload_timer"] > 0.0:
            return False
        if p["ammo"] < p["max_ammo"]:
            cls_cfg = CHARACTER_CLASSES[p["class_id"]]
            w_cfg = get_active_weapon_config(p, cls_cfg["weapon"])
            p["reload_timer"] = float(w_cfg["reload_time"])
            sync_weapon_state(p)
            self._emit_sound(p["x"], p["y"], RULES["player"]["reload_sound_radius"], "reload", sound_type=w_cfg["reload_sound"], source_id=p["id"])
            return True
        return False

    def trigger_skill(self, player_id: str) -> bool:
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.system_enabled("combat"):
            return False
        if p["skill_cooldown"] > 0.0:
            return False

        cls_cfg = CHARACTER_CLASSES[p["class_id"]]
        skill = cls_cfg["skill"]
        skill_id = skill["id"]
        behavior = skill["behavior"]
        if behavior == "nano_field":
            # A failed previous drop must settle before a field can revive its owner.
            for ally in self.players.values():
                if ally["downed"] and allied(p, ally, self.combat_rules) and math.hypot(ally["x"] - p["x"], ally["y"] - p["y"]) <= skill["radius"]:
                    self._settle_body(ally)
        p["skill_cooldown"] = float(skill["cooldown"])
        p["skill_timer"] = float(skill["duration"])
        p["skill_active"] = skill_id

        if behavior == "shockwave_shield":
            for enemy in self._actors():
                if can_target(p, enemy, rules=self.combat_rules):
                    dx = enemy["x"] - p["x"]
                    dy = enemy["y"] - p["y"]
                    dist = math.hypot(dx, dy)
                    if dist <= skill["radius"]:
                        push = (skill["radius"] + skill["push_offset"] - dist) * skill["push_multiplier"]
                        nx, ny = move_with_collisions(
                            enemy["x"],
                            enemy["y"],
                            (dx / dist) * push if dist else 0.0,
                            (dy / dist) * push if dist else 0.0,
                            1.0,
                            enemy["radius"],
                            world_surfaces(self.map_data),
                        )
                        enemy["x"], enemy["y"] = nx, ny
                        self.damage(enemy, skill["damage"], p, kind="area")
            self._emit_sound(p["x"], p["y"], skill["sound_radius"], "skill")
            self.events_log.append(f"{p['name']} activated Shockwave Shield!")

        elif behavior == "turret":
            tx = p["x"] + math.cos(p["angle"]) * skill["deploy_offset"]
            ty = p["y"] + math.sin(p["angle"]) * skill["deploy_offset"]
            self.deployables.append(
                {
                    "id": uuid.uuid4().hex[:8],
                    "type": "turret",
                    "owner_id": p["id"],
                    "faction": faction(p),
                    "x": tx,
                    "y": ty,
                    "angle": p["angle"],
                    "ttl": float(skill["duration"]),
                    "fire_timer": 0.0,
                    "range": skill["range"],
                    "effect": mutable_copy(skill),
                }
            )
            self.events_log.append(f"{p['name']} deployed an Auto-Turret!")

        elif behavior == "nano_field":
            self.deployables.append(
                {
                    "id": uuid.uuid4().hex[:8],
                    "type": "nano_field",
                    "owner_id": p["id"],
                    "faction": faction(p),
                    "x": p["x"],
                    "y": p["y"],
                    "radius": skill["radius"],
                    "ttl": float(skill["duration"]),
                    "heal_tick": 0.0,
                    "effect": mutable_copy(skill),
                }
            )
            # Immediate revival uses the same authored field radius as healing.
            for ally in self._actors():
                if ally.get("downed") and math.hypot(ally["x"] - p["x"], ally["y"] - p["y"]) <= skill["radius"]:
                    self.heal(ally, 0, p, revive_hp=max(skill["immediate_revive_minimum"], int(ally["max_hp"] * skill["immediate_revive_fraction"])))
            self.events_log.append(f"{p['name']} deployed Nano-Stim Healing Field!")

        elif behavior == "sonar":
            self.deployables.append(
                {
                    "id": uuid.uuid4().hex[:8],
                    "type": "sonar_pulse",
                    "owner_id": p["id"],
                    "x": p["x"],
                    "y": p["y"],
                    "radius": skill["radius"],
                    "ttl": float(skill["duration"]),
                }
            )
            self.events_log.append(f"{p['name']} emitted a Tactical Sonar Pulse!")

        return True

    def interaction_target(self, target_id):
        """Resolve one explicit world identity; crate puzzles retain their parent reach."""
        if not self.map_data:
            return None, None, None
        for kind, collection in (("service", "interactions"), ("puzzle", "puzzles"), ("loot", "loot"), ("crate", "crates")):
            for target in self.map_data.get(collection, []):
                if target["id"] == target_id:
                    return kind, target, target if kind == "crate" else None
                if kind == "crate" and (target.get("puzzle") or {}).get("id") == target_id:
                    return "puzzle", target["puzzle"], target
        ally = self.players.get(target_id)
        return ("revive", ally, None) if ally else (None, None, None)

    def interaction_error(self, player, kind, target, crate=None):
        if not player or player["downed"] or player["hp"] <= 0 or not self.system_enabled("interactions"):
            return "Interaction unavailable in this phase"
        if not target:
            return "Target unavailable or already removed"
        if kind == "puzzle" and target.get("solved"):
            return "Terminal already solved"
        if kind == "loot" and target.get("collected"):
            return "Item already taken"
        if kind == "revive" and (target is player or not target["downed"] or not allied(player, target, self.combat_rules)):
            return "Teammate is not eligible for revival"
        position = crate or target
        radius = RULES["interaction"]["revive_radius"] if kind == "revive" else (
            RULES["interaction"]["pickup_radius"] if kind == "loot" else
            RULES["interaction"]["interact_radius"] if kind == "crate" or crate else target["radius"])
        if math.hypot(position["x"] - player["x"], position["y"] - player["y"]) > radius:
            return "Target out of range"
        if not has_line_of_sight(player["x"], player["y"], position["x"], position["y"], world_surfaces(self.map_data)):
            return "Target blocked by line of sight"
        return None

    def handle_interact(self, player_id: str, target_id: str | None = None) -> dict[str, Any] | None:
        """
        Handles pressing E near a downed teammate (revive), puzzle terminal, ground item, or supply/body chest.
        """
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.system_enabled("interactions"):
            return None

        if target_id is not None:
            kind, target, crate = self.interaction_target(target_id)
            error = self.interaction_error(p, kind, target, crate)
            if error:
                return {"type": "unavailable", "ok": False, "message": error, "target_id": target_id}
            if kind == "service":
                return {"type": "open_service", "service": copy.deepcopy(target), "target_id": target_id}
            if kind == "revive":
                self.heal(target, 0, p, revive_hp=int(target["max_hp"] * RULES["interaction"]["revive_fraction"]))
                self.events_log.append(f"{p['name']} revived {target['name']}!")
                return {"type": "revived", "target_id": target_id}
            if kind == "loot":
                return {"type": "picked_up", "target_id": target_id, **self.pickup_ground_item(player_id, target_id)}
            if kind == "puzzle":
                return {"type": "open_puzzle", "puzzle": copy.deepcopy(target), "target_id": (crate or target)["id"]}
            if target.get("locked"):
                if target.get("puzzle") and not target["puzzle"].get("solved"):
                    return {"type": "open_puzzle", "puzzle": copy.deepcopy(target["puzzle"]), "target_id": target_id}
                return {"type": "unavailable", "ok": False, "message": "Crate is locked", "target_id": target_id}
            return {"type": "open_chest", "crate": target, "target_id": target_id}

        # Generic E intentionally chooses a nearby eligible target, unlike a click.
        for ally in self.players.values():
            if ally["id"] != p["id"] and ally["downed"] and allied(p, ally, self.combat_rules):
                if not self.interaction_error(p, "revive", ally):
                    self.heal(ally, 0, p, revive_hp=int(ally["max_hp"] * RULES["interaction"]["revive_fraction"]))
                    self.events_log.append(f"{p['name']} revived {ally['name']}!")
                    return {"type": "revived", "target_id": ally["id"]}

        # 2-4. Choose the CLOSEST interactable among puzzle terminals, ground items, and chests
        candidates: list[tuple[float, str, dict[str, Any]]] = []
        for target in self.map_data.get("interactions", []):
            if not self.interaction_error(p, "service", target):
                candidates.append((math.hypot(target["x"] - p["x"], target["y"] - p["y"]), "service", target))
        for pz in self.map_data["puzzles"]:
            if not pz["solved"]:
                d = math.hypot(pz["x"] - p["x"], pz["y"] - p["y"])
                if not self.interaction_error(p, "puzzle", pz):
                    candidates.append((d, "puzzle", pz))
        for lt in self.map_data.get("loot", []):
            if not lt.get("collected"):
                d = math.hypot(lt["x"] - p["x"], lt["y"] - p["y"])
                if not self.interaction_error(p, "loot", lt):
                    candidates.append((d, "loot", lt))
        for cr in self.map_data.get("crates", []):
            d = math.hypot(cr["x"] - p["x"], cr["y"] - p["y"])
            if not self.interaction_error(p, "crate", cr, cr):
                candidates.append((d, "crate", cr))
        if not candidates:
            return None
        candidates.sort(key=lambda c: (c[0], c[2]["id"]))
        _, kind, obj = candidates[0]
        return self.handle_interact(player_id, obj["id"])

    def pickup_ground_item(self, player_id: str, item_id: str) -> dict[str, Any]:
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.system_enabled("interactions"):
            return {"ok": False}
        for lt in self.map_data.get("loot", []):
            if lt.get("id") == item_id and not lt.get("collected"):
                error = self.interaction_error(p, "loot", lt)
                if error:
                    return {"ok": False, "message": error}
                if not error:
                    staged = copy.deepcopy(p)
                    ok, msg = add_item_to_player(staged, lt)
                    if ok:
                        self.shared_stash.commit_player(p, staged)
                        p.update(staged)
                        lt["collected"] = True
                        self.interaction_revision += 1
                        self.events_log.append(f"{p['name']} picked up {lt['name']}.")
                    return {"ok": ok, "message": msg}
        return {"ok": False, "message": "Item out of range or already taken"}

    def take_crate_item(self, player_id: str, crate_id: str, item_id: str, target_slot: str | None = None) -> dict[str, Any]:
        p = self.players.get(player_id)
        if not p or p["downed"] or not self.system_enabled("interactions"):
            return {"ok": False}
        crate = next((c for c in self.map_data.get("crates", []) if c["id"] == crate_id), None)
        if not crate or crate.get("locked"):
            return {"ok": False, "message": "Crate is locked or unavailable"}
        error = self.interaction_error(p, "crate", crate, crate)
        if error:
            return {"ok": False, "message": error}
        item_idx = next((i for i, it in enumerate(crate.get("items", [])) if it["id"] == item_id), None)
        if item_idx is None:
            return {"ok": False, "message": "Item already taken by another operative", "crate": crate}
        item = crate["items"][item_idx]
        staged = copy.deepcopy(p)
        ok, msg = add_item_to_player(staged, item, target_slot)
        if ok:
            self.shared_stash.commit_player(p, staged)
            p.update(staged)
            crate["items"].pop(item_idx)
            self.interaction_revision += 1
            self.events_log.append(f"{p['name']} took {item['name']} from {crate['name']}.")
        return {"ok": ok, "message": msg, "crate": crate}

    def handle_inventory_action(self, player_id: str, data: dict[str, Any]) -> dict[str, Any]:
        p = self.players.get(player_id)
        if not p or p["downed"] or self.phase not in ("lobby", "prep", "dropship", "active"):
            return {"ok": False}
        try:
            data = Command.model_validate({**data, "type": "inventory_action"}).model_dump(exclude_unset=True)
        except (TypeError, ValidationError):
            return {"ok": False, "message": "Invalid inventory action"}
        act = data.get("action")
        inv = p.get("inventory")
        if not inv:
            return {"ok": False, "message": "No inventory"}

        if act == "switch_weapon":
            slot = data.get("slot")
            if slot not in ("primary_weapon", "secondary_weapon"):
                slot = "secondary_weapon" if inv.get("active_weapon_slot") == "primary_weapon" else "primary_weapon"
            if inv["equipped"].get(slot):
                sync_weapon_state(p)
                inv["active_weapon_slot"] = slot
                recalc_inventory(p)
                return {"ok": True, "message": f"Switched to {inv['equipped'][slot]['name']}"}
            return {"ok": False, "message": "No weapon in that slot"}

        if act == "equip_item":
            staged = copy.deepcopy(p)
            ok, msg = equip_item_from_storage(staged, str(data.get("item_id", "")), data.get("target_slot"))
            if ok:
                self.shared_stash.commit_player(p, staged)
                p.update(staged)
            return {"ok": ok, "message": msg}

        if act == "unequip_slot":
            staged = copy.deepcopy(p)
            ok, msg = unequip_slot_to_storage(staged, str(data.get("slot", "")))
            if ok:
                self.shared_stash.commit_player(p, staged)
                p.update(staged)
            return {"ok": ok, "message": msg}

        if act == "use_item":
            item_id = str(data.get("item_id", ""))
            slot = data.get("slot")
            target = None
            if slot == "quick_use" and inv["equipped"].get("quick_use"):
                target = inv["equipped"]["quick_use"]
            else:
                target = next((it for it in inv["storage"] if it.get("id") == item_id), None)
                if not target and inv["equipped"].get("quick_use") and inv["equipped"]["quick_use"].get("id") == item_id:
                    target = inv["equipped"]["quick_use"]
            if not target:
                return {"ok": False, "message": "Item not found"}

            effect = item_effect(target)
            behavior = effect.get("behavior")
            if self.map_data and (self.world_capabilities["beneficial_use"] == "disabled" or (behavior == "turret" and not self.system_enabled("combat"))):
                return {"ok": False, "message": "Item use disabled by world rules"}
            usable = behavior in ("heal", "ammo_refill") or (self.map_data and behavior in ("turret", "sonar"))
            if behavior == "ammo_refill" and not active_weapon_item(p):
                return {"ok": False, "message": "No equipped weapon"}
            if behavior == "heal":
                staged = copy.deepcopy(p)
                heal = int(effect["heal"])
                if not self.heal(staged, heal, staged):
                    return {"ok": False, "message": "Healing unavailable"}
                if target.get("consumable", True):
                    remove_item_from_player(staged, target["id"])
                    self.shared_stash.commit_player(p, staged)
                p.update(staged)
                self.events_log.append(f"{p['name']} used {target['name']} (+{heal} HP)!")
                return {"ok": True, "message": f"Used {target['name']}"}
            if usable and target.get("consumable", True):
                staged = copy.deepcopy(p)
                remove_item_from_player(staged, target["id"])
                self.shared_stash.commit_player(p, staged)
                p.update(staged)
            used = False
            if behavior == "ammo_refill":
                p["ammo"] = p["max_ammo"]
                p["reload_timer"] = 0.0
                sync_weapon_state(p)
                self.events_log.append(f"{p['name']} resupplied ammo using {target['name']}!")
                used = True
            elif behavior == "turret":
                if self.map_data:
                    tx = p["x"] + math.cos(p["angle"]) * effect["deploy_offset"]
                    ty = p["y"] + math.sin(p["angle"]) * effect["deploy_offset"]
                    self.deployables.append({
                        "id": uuid.uuid4().hex[:8],
                        "type": "turret",
                        "owner_id": p["id"],
                        "faction": faction(p),
                        "x": tx,
                        "y": ty,
                        "angle": p["angle"],
                        "ttl": effect["duration"],
                        "fire_timer": 0.0,
                        "range": effect["range"],
                        "effect": effect,
                    })
                    self.events_log.append(f"{p['name']} deployed a Portable Turret!")
                    used = True
            elif behavior == "sonar":
                if self.map_data:
                    self.deployables.append({
                        "id": uuid.uuid4().hex[:8],
                        "type": "sonar_pulse",
                        "owner_id": p["id"],
                        "x": p["x"],
                        "y": p["y"],
                        "radius": effect["radius"],
                        "ttl": effect["duration"],
                    })
                    self.events_log.append(f"{p['name']} activated a Sonar Beacon!")
                    used = True

            return {"ok": used, "message": f"Used {target['name']}" if used else "Passive item"}

        if act == "drop_item" and self.map_data:
            staged = copy.deepcopy(p)
            removed = remove_item_from_player(staged, str(data.get("item_id", "")))
            if removed:
                if not bankable(removed):
                    return {"ok": False, "message": "Starter items cannot be dropped"}
                removed.update({
                    "x": round(p["x"] + 0.6, 2),
                    "y": round(p["y"] + 0.6, 2),
                    "radius": RULES["interaction"]["ground_item_radius"],
                    "collected": False,
                })
                self.shared_stash.commit_player(p, staged, world_items=[removed])
                p.update(staged)
                self.map_data.setdefault("loot", []).append(removed)
                self.events_log.append(f"{p['name']} dropped {removed['name']}.")
                return {"ok": True}

        return {"ok": False}

    def handle_puzzle_action(self, player_id: str, puzzle_id: str, action: dict[str, Any]) -> dict[str, Any]:
        """
        Processes interactive puzzle actions from a player (for both bulkhead doors and skill-check supply crates).
        """
        p = self.players.get(player_id)
        if not p or p["downed"] or p["hp"] <= 0 or not self.system_enabled("interactions"):
            return {"ok": False}
        try:
            action = PuzzleAction.model_validate(action).model_dump(exclude_unset=True)
        except ValidationError:
            return {"ok": False, "message": "Invalid puzzle action"}

        puzzle = next((pz for pz in self.map_data["puzzles"] if pz["id"] == puzzle_id), None)
        target_crate = None
        if not puzzle:
            for cr in self.map_data.get("crates", []):
                if cr.get("puzzle") and cr["puzzle"].get("id") == puzzle_id:
                    puzzle = cr["puzzle"]
                    target_crate = cr
                    break
        if not puzzle:
            return {"ok": False, "message": "Target unavailable"}

        if puzzle["solved"]:
            return {"ok": False, "solved": True, "message": "Attempt replayed or terminal already solved"}
        error = self.interaction_error(p, "puzzle", puzzle, target_crate)
        if error:
            return {"ok": False, "reason": error}
        px = target_crate["x"] if target_crate else puzzle["x"]
        py = target_crate["y"] if target_crate else puzzle["y"]

        try:
            puzzle_handlers.runtime_puzzle(puzzle, catalog=self.catalog)
            if "objectives" not in self.map_data:
                objectives.map_objectives(self.map_data, catalog=self.catalog)
            solved, alarm = puzzle_handlers.act(puzzle, action, p, self.simulation_time)
        except ValueError as exc:
            return {"ok": False, "message": str(exc), "puzzle": puzzle}
        self.interaction_revision += 1
        if alarm:
            self._emit_sound(px, py, RULES["interaction"]["alarm_radius"], "alarm")
            self.events_log.append(f"Security alarm triggered at {puzzle['label']}!")
        if solved:
            self._complete_puzzle(puzzle, p["name"])
        return {"ok": True, "solved": puzzle["solved"], "puzzle": puzzle, "unlocked_crate": target_crate if puzzle["solved"] else None}

    def _complete_puzzle(self, puzzle: dict[str, Any], solver_name: str) -> None:
        if not self.map_data or puzzle["solved"]:
            return
        puzzle["solved"] = True
        if "session" in puzzle:
            puzzle["session"]["status"] = "solved"
        if self.system_enabled("objectives"):
            objectives.update(self)
        outcome = "Terminal online!" if puzzle.get("prop_id") else "Interaction completed!"
        self.events_log.append(f"{solver_name} completed '{puzzle['label']}' - {outcome}")

    def _emit_sound(self, x: float, y: float, radius: float, kind: str = "gunfire", sound_type: str = "", source_id: str = "") -> None:
        sequence = self.event_sequence + 1
        world_id = self.map_data["world_id"] if self.map_data else None
        identity = f"{self._event_namespace}:{sequence}"
        evt = {
            "id": identity, "event_id": identity, "sequence": sequence,
            "world_id": world_id, "timestamp": self.simulation_time,
            "expires_at": self.simulation_time + RULES["audio"]["history_duration"],
            "x": x,
            "y": y,
            "radius": radius,
            "kind": kind,
            "ttl": RULES["timing"]["sound_ring_duration"],
            "source_id": source_id,
        }
        if sound_type:
            evt.update(create_audio_event(sound_type, x, y, source_id=source_id, event_id=identity,
                                          sequence=sequence, timestamp=self.simulation_time, world_id=world_id))
        self.event_sequence = sequence
        if radius > 0:
            self.pending_sound_events.append(dict(evt))
        self.sound_events.append(evt)
        self.recent_sounds.append(dict(evt))
        self.sound_events = self.sound_events[-RULES["audio"]["history_limit"]:]
        self.recent_sounds = self.recent_sounds[-RULES["audio"]["history_limit"]:]

    def _actors(self):
        return list(self.players.values()) + (self.map_data["enemies"] if self.map_data else [])

    def damage(self, target, amount, source=None, *, kind="hazard"):
        if not self.system_enabled("combat"):
            return False
        owner_id = source.get("owner_id", source.get("id")) if source else None
        owner = next((actor for actor in self._actors() if actor["id"] == owner_id), None)
        was_alive = target.get("hp", 0) > 0
        result = apply_damage(target, amount, source, kind=kind, owner=owner, rules=self.combat_rules)
        if was_alive and target.get("hp", 0) <= 0 and target.get("type") in self.catalog.definitions["enemies"]:
            self._emit_sound(target["x"], target["y"], 0, "alien_death", sound_type=RULES["audio"]["alien_death_sound"], source_id=target["id"])
        return result

    def _settle_body(self, player):
        if not self.map_data or not player.get("downed") or player.get("body_dropped"):
            return
        staged = copy.deepcopy(player)
        chest = create_body_chest_from_player(staged)
        staged["body_dropped"] = True
        self.shared_stash.commit_player(player, staged, world_items=chest["items"] if chest else ())
        player.update(staged)
        if chest:
            self.map_data.setdefault("crates", []).append(chest)
            self.events_log.append(f"{player['name']} fell! Their body chest dropped with their inventory.")

    def heal(self, target, amount, source, *, revive_hp=None):
        if self.map_data and self.world_capabilities["beneficial_use"] == "disabled":
            return False
        if not can_target(source, target, rules=self.combat_rules, beneficial=True):
            return False
        if target.get("downed"):
            if revive_hp is None:
                return False
            # Publish no revival until the same durable ownership transfer has committed.
            self._settle_body(target)
            target.update(downed=False, body_dropped=False, hp=max(1, min(target["max_hp"], int(revive_hp))))
            target.pop("last_damage", None)
            return True
        if target["hp"] <= 0:
            return False
        target["hp"] = min(target["max_hp"], target["hp"] + max(0, int(amount)))
        return True

    def _snapshot_players(self):
        result = []
        for p in self.players.values():
            armed = active_weapon_item(p) is not None
            active = self.system_enabled("combat") and not p["downed"] and p["hp"] > 0
            light = is_light_weapon(p)
            result.append({
                **p,
                "sight_range": CHARACTER_CLASSES[p["class_id"]]["sight_range"] + sight_boost(p),
                "capabilities": {
                    "can_move": self.system_enabled("movement") and not p["downed"],
                    "can_interact": self.system_enabled("interactions") and not p["downed"],
                    "combat": self.system_enabled("combat"),
                    "can_fire": active and armed and p["ammo"] > 0 and p["reload_timer"] <= 0
                    and p["fire_cooldown"] <= 0 and (not p["running"] or light),
                    "can_reload": active and armed and p["reload_timer"] <= 0 and p["ammo"] < p["max_ammo"],
                    "can_fire_while_running": light,
                },
            })
        return result

    def step(self, dt: float) -> None:
        for player_id in list(self.pending_disconnects):
            self.remove_player(player_id)
        # Presentation ages in terminal phases too. Snapshot reads never advance time.
        self.simulation_time += dt
        self.sound_events = [s for s in self.sound_events if s["expires_at"] > self.simulation_time]
        for s in self.recent_sounds:
            s["ttl"] -= dt
        self.recent_sounds = [s for s in self.recent_sounds if s["ttl"] > 0]
        if self.phase == "prep":
            if (not self.map_data or self.map_data.get("world_kind") != "hub") and self.players and all(pl.get("ready", False) for pl in self.players.values()):
                if self.prep_timer <= 0.0:
                    self.prep_timer = RULES["timing"]["prep_countdown"]
                self.prep_timer = max(0.0, self.prep_timer - dt)
                if self.prep_timer <= 0.0 and self.leader_id:
                    try:
                        launched = self.start_mission(self.leader_id)
                    except ValueError as exc:
                        self.events_log.append(f"Launch failed: {exc}")
                        launched = False
                    if not launched:
                        for player in self.players.values():
                            player["ready"] = False
                        self.prep_timer = 0.0
                    return
            else:
                self.prep_timer = 0.0
            if not self.map_data:
                return

        if not self.world_running:
            return

        if self.system_enabled("combat"):
            for p in self.players.values():
                self._settle_body(p)
        else:
            self.projectiles.clear()
            self.deployables.clear()
        if self.system_enabled("mission_clock"):
            self.mission_time += dt

        # Handle Dropship insertion countdown
        if self.phase == "dropship":
            self.dropship_timer -= dt
            if self.dropship_timer <= 0.0:
                self.phase = "active"
                self.open_spaceship_ramp()

        walls = world_surfaces(self.map_data)
        enemies = self.map_data["enemies"]
        for puzzle in self.map_data["puzzles"] + [c["puzzle"] for c in self.map_data.get("crates", []) if c.get("puzzle")]:
            if puzzle_handlers.expire(puzzle, self.simulation_time):
                self.interaction_revision += 1

        # 1. Update Players
        for p in self.players.values():
            if p["id"] in self.pending_disconnects:
                continue
            cls_cfg = CHARACTER_CLASSES[p["class_id"]]
            sync_weapon_state(p)
            w_cfg = get_active_weapon_config(p, cls_cfg["weapon"])

            if self.system_enabled("cooldowns") and p["skill_cooldown"] > 0.0:
                p["skill_cooldown"] = max(0.0, p["skill_cooldown"] - dt)
            if self.system_enabled("cooldowns") and p["skill_timer"] > 0.0:
                p["skill_timer"] = max(0.0, p["skill_timer"] - dt)
                if p["skill_timer"] == 0.0:
                    p["skill_active"] = None
            if self.system_enabled("cooldowns") and p["fire_cooldown"] > 0.0:
                p["fire_cooldown"] = max(0.0, p["fire_cooldown"] - dt)
            if self.system_enabled("cooldowns") and p["reload_timer"] > 0.0:
                p["reload_timer"] = max(0.0, p["reload_timer"] - dt)
                if p["reload_timer"] == 0.0:
                    p["ammo"] = p["max_ammo"]
                sync_weapon_state(p)

            if p["downed"]:
                p["vx"] = 0.0
                p["vy"] = 0.0
                continue

            inp = p["input"]
            if "input_age" in p:
                p["input_age"] += dt
                if p["input_age"] > RULES["timing"]["input_timeout"]:
                    inp.update(dx=0.0, dy=0.0, firing=False, running=False)
            dx, dy = float(inp["dx"]), float(inp["dy"])
            if not self.system_enabled("movement"):
                dx = dy = 0.0
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
                if want_run and fwd_dot > RULES["player"]["sprint_forward_dot"]:
                    is_running = True
                    sprint_spd = float(cls_cfg["sprint_speed"])
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
                    base_noise = float(cls_cfg["footstep_radius"])
                    if is_running:
                        p["footstep_timer"] = RULES["player"]["run_footstep_interval"]
                        # Running is louder (approx 2.2x noise radius)
                        self._emit_sound(p["x"], p["y"], base_noise * RULES["player"]["run_noise_multiplier"], "footstep", sound_type=RULES["player"]["footstep_sound"], source_id=p["id"])
                    else:
                        p["footstep_timer"] = RULES["player"]["walk_footstep_interval"]
                        # Walking generates less noise (approx 0.5x noise radius)
                        self._emit_sound(p["x"], p["y"], base_noise * RULES["player"]["walk_noise_multiplier"], "footstep", sound_type=RULES["player"]["footstep_sound"], source_id=p["id"])

            # Firing weapon: When running, player can only shoot if using a light weapon (like a pistol)
            can_fire = not is_running or is_light_weapon(p, w_cfg)
            if self.system_enabled("combat") and w_cfg and inp.get("firing") and can_fire and p["fire_cooldown"] <= 0.0 and p["reload_timer"] <= 0.0:
                if p["ammo"] > 0:
                    p["ammo"] -= 1
                    p["fire_cooldown"] = 1.0 / float(w_cfg["fire_rate"])
                    pellets = int(w_cfg["pellets"])
                    spread = float(w_cfg["spread"])
                    proj_spd = float(w_cfg["projectile_speed"])
                    proj_ttl = float(w_cfg["range"]) / proj_spd

                    for _ in range(pellets):
                        ang = p["angle"] + self.rng.uniform(-spread, spread)
                        self.projectiles.append(
                            {
                                "id": uuid.uuid4().hex[:8],
                                "owner_id": p["id"],
                                "faction": faction(p),
                                "x": p["x"] + math.cos(ang) * RULES["player"]["projectile_offset"],
                                "y": p["y"] + math.sin(ang) * RULES["player"]["projectile_offset"],
                                "vx": math.cos(ang) * proj_spd,
                                "vy": math.sin(ang) * proj_spd,
                                "damage": int(w_cfg["damage"]),
                                "ttl": proj_ttl,
                                "range_remaining": float(w_cfg["range"]),
                                "color": w_cfg["color"],
                                "radius": RULES["player"]["projectile_radius"],
                            }
                        )
                    self._emit_sound(p["x"], p["y"], float(w_cfg["sound_radius"]), "gunfire", sound_type=w_cfg["fire_sound"], source_id=p["id"])
                    if p["ammo"] == 0:
                        p["reload_timer"] = float(w_cfg["reload_time"])
                        self._emit_sound(p["x"], p["y"], 0, "reload", sound_type=w_cfg["reload_sound"], source_id=p["id"])
                    sync_weapon_state(p)

        # 2. Update Deployables (Auto-Turret, Nano Healing Field, Sonar)
        for dep in self.deployables:
            active_dt = min(dt, max(0.0, dep["ttl"]))
            if active_dt <= 0:
                continue
            dep["ttl"] -= active_dt
            if dep["type"] == "turret":
                effect = dep["effect"]
                dep["fire_timer"] = max(0.0, dep["fire_timer"] - active_dt)
                if dep["fire_timer"] <= 0.0:
                    # Acquire hostiles only; opted-in allies can intercept its shots.
                    best_e = None
                    best_d = float(dep["range"])
                    for e in sorted(self._actors(), key=lambda actor: actor["id"]):
                        if can_target(dep, e, rules=self.combat_rules, hostile_only=True):
                            d = math.hypot(e["x"] - dep["x"], e["y"] - dep["y"])
                            if d <= best_d and has_line_of_sight(dep["x"], dep["y"], e["x"], e["y"], walls):
                                best_d = d
                                best_e = e
                    if best_e is not None:
                        ang = math.atan2(best_e["y"] - dep["y"], best_e["x"] - dep["x"])
                        dep["angle"] = ang
                        dep["fire_timer"] = effect["fire_interval"]
                        self.projectiles.append(
                            {
                                "id": uuid.uuid4().hex[:8],
                                "owner_id": dep["owner_id"],
                                "faction": faction(dep),
                                "x": dep["x"] + math.cos(ang) * effect["projectile_offset"],
                                "y": dep["y"] + math.sin(ang) * effect["projectile_offset"],
                                "vx": math.cos(ang) * effect["projectile_speed"],
                                "vy": math.sin(ang) * effect["projectile_speed"],
                                "damage": effect["damage"],
                                "ttl": effect["projectile_ttl"],
                                "color": effect["color"],
                                "radius": effect["projectile_radius"],
                            }
                        )
                        self._emit_sound(dep["x"], dep["y"], effect["sound_radius"], "gunfire", sound_type=effect["fire_sound"], source_id=dep["id"])

            elif dep["type"] == "nano_field":
                effect = dep["effect"]
                dep["heal_tick"] -= active_dt
                if dep["heal_tick"] <= 0.0:
                    dep["heal_tick"] = effect["heal_interval"]
                    for p in self._actors():
                        if math.hypot(p["x"] - dep["x"], p["y"] - dep["y"]) <= dep["radius"]:
                            heal_amt = effect["low_health_heal"] if p["hp"] < p["max_hp"] * effect["low_health_threshold"] else effect["heal"]
                            self.heal(p, heal_amt, dep, revive_hp=effect["tick_revive_hp"])

        self.deployables = [d for d in self.deployables if d["ttl"] > 0.0]

        # 3. Update Projectiles
        surviving_proj: list[dict[str, Any]] = []
        for proj in self.projectiles:
            # The final TTL/range segment is inclusive; expired shots never hit later.
            if proj.get("range_remaining", 1) <= 0:
                continue
            travel_dt = min(dt, max(0.0, proj["ttl"]))
            speed = math.hypot(proj["vx"], proj["vy"])
            if "range_remaining" in proj and speed:
                travel_dt = min(travel_dt, max(0.0, proj["range_remaining"]) / speed)
            if travel_dt <= 0.0:
                continue

            ox, oy = proj["x"], proj["y"]
            nx = ox + proj["vx"] * travel_dt
            ny = oy + proj["vy"] * travel_dt
            proj["ttl"] = max(0.0, proj["ttl"] - travel_dt)
            if "range_remaining" in proj:
                proj["range_remaining"] = max(0.0, proj["range_remaining"] - speed * travel_dt)

            impacts = []
            for w in walls:
                if not w.get("active", True) or not w.get("blocks_projectile", True):
                    continue
                t = swept_circle_aabb_entry(ox, oy, nx, ny, proj["radius"], w)
                if t is not None:
                    impacts.append((t, 0, w.get("id", ""), None))
            for actor in self._actors():
                if can_target(proj, actor, rules=self.combat_rules):
                    t = segment_intersects_circle(ox, oy, nx, ny, actor["x"], actor["y"], actor["radius"] + proj["radius"])
                    if t is not None:
                        impacts.append((t, 1, actor["id"], actor))
            if impacts:
                # Exact ties: walls before actors, then stable collider ID, never list order.
                t, _, _, actor = min(impacts, key=lambda hit: hit[:3])
                proj["x"], proj["y"] = ox + (nx - ox) * t, oy + (ny - oy) * t
                if actor is not None:
                    self.damage(actor, proj["damage"], proj, kind="projectile")
            elif proj["ttl"] > 0 and proj.get("range_remaining", 1) > 0:
                proj["x"], proj["y"] = nx, ny
                surviving_proj.append(proj)

        self.projectiles = surviving_proj

        # 4. Update Enemy AI (Sight & Hearing)
        pending, self.pending_sound_events = self.pending_sound_events, []
        if self.system_enabled("ai"):
            update_enemies(enemies, self.players, walls, pending, self.projectiles, dt,
                           damage=self.damage, combat_rules=self.combat_rules, emit_sound=self._emit_sound, catalog=self.catalog)
        if self.system_enabled("objectives"):
            objectives.update(self)

        # 4b. Convert newly downed/dead player inventory into persistent body chest on map
        if self.system_enabled("combat"):
            for p in self.players.values():
                self._settle_body(p)

        living_players = [p for p in self.players.values() if not p["downed"] and p["hp"] > 0]

        # 5. Check Mission Victory / Defeat Conditions
        if self.system_enabled("failure") and self.players and not living_players:
            self.phase = "defeat"
            self._neutral_players()
            self.events_log.append("All squad members were neutralized. Mission failed.")
            return

        ext = self.map_data.get("extraction_zone")
        if self.system_enabled("extraction") and ext and ext["active"] and living_players:
            all_inside = True
            for p in living_players:
                inside = point_in_rect(p["x"], p["y"], ext)
                p["extracted"] = inside
                if not inside:
                    all_inside = False
            if all_inside:
                lvl_id = self.map_data.get("level_id")
                if self.map_data.get("test_mode") and lvl_id:
                    self.level_store.mark_tested(lvl_id)
                    self.level_store.mark_completed(lvl_id)
                    self.map_data["level_tested"] = True
                    self.events_log.append(
                        f"Level '{self.map_data.get('level_name')}' passed testing phase! Marked VERIFIED & next level unlocked."
                    )
                else:
                    banked = deposit_squad_loot(living_players, self.shared_stash)
                    if lvl_id:
                        self.level_store.mark_completed(lvl_id)
                    self.events_log.append(
                        f"Squad extracted! {banked} items deposited into Shared Chest & next level unlocked!"
                    )
                self.phase = "victory"
                self._neutral_players()

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
                "levels": self.level_store.list_levels(),
                "shared_stash": self.shared_stash.get_stacked_list(),
                "players": self._snapshot_players(),
                "events": self.events_log[-5:],
                "world_id": None, "simulation_time": self.simulation_time, "event_sequence": self.event_sequence,
                "world_kind": None, "world_capabilities": {},
                "audio_events": [], "ambience_emitters": [],
                "music": {"state": "lobby", "bpm": CATALOG.definitions["music"]["bpm"], "start_t": self._music_start_t, "simulation_time": self.simulation_time},
            }

        walls = world_surfaces(self.map_data)
        sonar_emitters = [dep for dep in self.deployables if dep["type"] == "sonar_pulse" and dep["ttl"] > 0]
        sonar_active = bool(sonar_emitters)

        serialized_enemies = []
        for e in self.map_data["enemies"]:
            if e["hp"] <= 0:
                continue
            visible = any(math.hypot(e["x"] - dep["x"], e["y"] - dep["y"]) <= dep["radius"] for dep in sonar_emitters)
            if not visible:
                for p in self.players.values():
                    if p["hp"] <= 0:
                        continue
                    cls_cfg = CHARACTER_CLASSES[p["class_id"]]
                    sight = float(cls_cfg["sight_range"]) + sight_boost(p)
                    if math.hypot(e["x"] - p["x"], e["y"] - p["y"]) <= sight:
                        if has_line_of_sight(p["x"], p["y"], e["x"], e["y"], walls):
                            visible = True
                            break
            if not visible:
                for dep in self.deployables:
                    if dep["type"] == "turret" and dep["ttl"] > 0:
                        if math.hypot(e["x"] - dep["x"], e["y"] - dep["y"]) <= dep["range"]:
                            if has_line_of_sight(dep["x"], dep["y"], e["x"], e["y"], walls):
                                visible = True
                                break

            serialized_enemies.append(
                {
                    "id": e["id"],
                    "type": e["type"],
                    "faction": faction(e),
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
                     "sight_range": self.catalog.definitions["enemies"][e["type"]]["sight_range"],
                     "sight_angle": self.catalog.definitions["enemies"][e["type"]]["sight_angle"],
                    **{key: e[key] for key in ("visual_id", "material_id") if key in e},
                    **{key: e[key] for key in ("encounter_phase", "attack_profiles") if key in e},
                }
            )

                # Music State determination (explore vs combat vs extraction)
        music_state = "explore"
        if not self.world_capabilities.get("combat") or self.phase in ("victory", "defeat"):
            music_state = "lobby"
        elif (self.map_data.get("extraction_zone") or {}).get("active", False):
            music_state = "combat"
        else:
            # Check if any alive enemy is actively targeting a player
            for e in self.map_data.get("enemies", []):
                if e.get("hp", 0) > 0 and e.get("state") == "engaged":
                    music_state = "combat"
                    break

        # Process per-listener spatial & occluded audio events for newly created sounds
        processed_audio_events = []
        listener = self.players.get(listener_id) if listener_id else None
        lx, ly = (listener["x"], listener["y"]) if listener else (0.0, 0.0)

        world_id = self.map_data["world_id"]
        floor = self._listener_event_floor.get(listener_id, 0)
        for s in self.sound_events:
            if s["world_id"] != world_id or s["expires_at"] <= self.simulation_time or s["sequence"] <= floor:
                continue
            st = s.get("sound_type")
            if not st:
                continue
            sx, sy = s.get("x", 0.0), s.get("y", 0.0)
            if listener:
                dist = math.hypot(sx - lx, sy - ly)
                rmax = s["radius_max"]
                if dist > rmax:
                    continue  # culled outside hearing range
                if s.get("occlude", True) and self.map_data:
                    gain_mult, lpf, blocks = calculate_occlusion(sx, sy, lx, ly, walls, world_id=world_id,
                                                               geometry_revision=self.map_data.get("geometry_revision", 0))
                else:
                    gain_mult, lpf, blocks = 1.0, 20000.0, 0
            else:
                gain_mult, lpf, blocks = 1.0, 20000.0, 0

            processed_audio_events.append({
                "event_id": s["event_id"], "sequence": s["sequence"], "timestamp": s["timestamp"],
                "world_id": world_id, "expires_at": s["expires_at"],
                "sound_type": st,
                "source_id": s.get("source_id", ""),
                "x": round(sx, 2),
                "y": round(sy, 2),
                "radius_min": s["radius_min"],
                "radius_max": s["radius_max"],
                "base_gain": s["base_gain"],
                "occl_gain": round(gain_mult, 3),
                "lpf_cutoff": round(lpf, 1),
            })

        ambience_emitters = []
        if self.world_running:
            for emitter in self.map_data.get("ambience_emitters", []):
                if not emitter["active"]:
                    continue
                config = CATALOG.definitions["sounds"][emitter["sound_type"]]
                if listener and math.hypot(emitter["x"] - lx, emitter["y"] - ly) >= config["radius_max"]:
                    continue
                gain, cutoff, _ = calculate_occlusion(emitter["x"], emitter["y"], lx, ly, walls, world_id=world_id,
                                                     geometry_revision=self.map_data.get("geometry_revision", 0)) if config["occlude"] and listener else (1, 20000, 0)
                ambience_emitters.append({**emitter, "world_id": world_id, "occl_gain": gain, "lpf_cutoff": cutoff})

        return {
            "type": "state",
            "world_id": world_id, "simulation_time": self.simulation_time, "event_sequence": self.event_sequence,
            "world_kind": self.map_data.get("world_kind", "mission"),
            "world_capabilities": mutable_copy(self.world_capabilities),
            "interaction_revision": self.interaction_revision,
            "ambience_emitters": ambience_emitters,
            "phase": self.phase,
            "prep_timer": round(self.prep_timer, 1),
            "music": {
                "state": music_state,
                "bpm": CATALOG.definitions["music"]["bpm"],
                "start_t": getattr(self, "_music_start_t", 0.0),
                "simulation_time": self.simulation_time,
            },
            "audio_events": processed_audio_events,
            "dropship_timer": round(self.dropship_timer, 1),
            "mission_time": round(self.mission_time, 1),
            "leader_id": self.leader_id,
            "selected_mission_id": self.selected_mission_id,
            "selected_level_id": self.selected_level_id,
            "levels": self.level_store.list_levels(),
            "shared_stash": self.shared_stash.get_stacked_list(),
            "players": self._snapshot_players(),
            "map": {
                "world_id": world_id,
                "world_kind": self.map_data.get("world_kind", "mission"),
                "world_name": self.map_data.get("world_name", self.map_data.get("mission_name")),
                "capabilities": mutable_copy(self.world_capabilities),
                "geometry_revision": self.map_data.get("geometry_revision", 0),
                "level_id": self.map_data.get("level_id"),
                "level_name": self.map_data.get("level_name"),
                "test_mode": bool(self.map_data.get("test_mode", False)),
                "level_tested": bool(self.map_data.get("level_tested", True)),
                **{key: self.map_data[key] for key in ("mission_id", "mission_name", "spaceship", "extraction_zone") if key in self.map_data},
                "width": self.map_data["width"],
                "height": self.map_data["height"],
                "segments": self.map_data["segments"],
                "connectors": self.map_data.get("connectors", []),
                "walls": self.map_data["walls"],
                "props": public_payload(self.map_data.get("props", [])),
                "interactions": public_payload(self.map_data.get("interactions", [])),
                "puzzles": public_payload(self.map_data["puzzles"]),
                "objectives": public_payload(self.map_data.get("objectives", [])),
                "wires": self.map_data.get("wires", []),
                "loot": [lt for lt in self.map_data.get("loot", []) if not lt.get("collected")],
                "crates": public_payload(self.map_data.get("crates", [])),
            },
            "enemies": serialized_enemies,
            "projectiles": self.projectiles,
            "deployables": self.deployables,
            "sounds": self.recent_sounds,
            "sonar_active": sonar_active,
            "events": self.events_log[-6:],
        }
