from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

from backend.app.config.items import CLASS_CARRY_CAPACITY, CLASS_MIN_LOADOUT, ITEM_TEMPLATES, make_item
from backend.app.content import mutable_copy
from backend.app.engine.persistence import SaveError, read_json, read_snapshot, runtime_directory, write_snapshot

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "shared_stash.json"

class SharedStash:
    """Server-persisted shared inventory where multiple copies of the same item stack."""

    def __init__(self, path: Path | None = None, *, load: bool = True) -> None:
        self.path = Path(path) if path is not None else runtime_directory() / "shared_stash.json"
        self.stacks: dict[str, int] = {}
        self.holdings: dict[str, dict[str, str]] = {}
        self.loaded = False
        if load:
            self.load()

    def load(self) -> None:
        source = self.path if self.path.exists() else DATA_PATH
        data = read_snapshot(source, "shared_stash")
        for identity, count in data.items():
            if not isinstance(identity, str) or type(count) is not int or count < 0:
                raise SaveError(f"{source}: invalid count for {identity!r}; original retained")
        envelope = read_json(source)
        holdings = envelope.get("holdings", {}) if "schema_version" in envelope else {}
        if not isinstance(holdings, dict) or any(
            not isinstance(identity, str) or not isinstance(record, dict)
            or not isinstance(record.get("template_id"), str) or not isinstance(record.get("owner"), str)
            for identity, record in holdings.items()
        ):
            raise SaveError(f"{source}: invalid holdings; original retained")
        # No permanent accounts: a new server refunds abandoned player/world claims once.
        recovered = dict(data)
        for record in holdings.values():
            tid = record["template_id"]
            recovered[tid] = recovered.get(tid, 0) + 1
        if holdings or not self.path.exists():
            write_snapshot(self.path, "shared_stash", recovered, metadata={"holdings": {}})
        self.stacks = recovered
        self.holdings = {}
        self.loaded = True

    def save(self) -> None:
        write_snapshot(self.path, "shared_stash", self.stacks, metadata={"holdings": self.holdings})

    def commit(self, stacks: dict, holdings: dict) -> None:
        write_snapshot(self.path, "shared_stash", stacks, metadata={"holdings": holdings})
        self.stacks, self.holdings = stacks, holdings

    def commit_player(self, before: dict, after: dict, *, stacks: dict | None = None, world_items=()) -> None:
        holdings = copy.deepcopy(self.holdings)
        for item in inventory_items(before):
            holdings.pop(item["id"], None)
        for item in inventory_items(after):
            if bankable(item):
                holdings[item["id"]] = {"template_id": item["template_id"], "owner": f"player:{after['id']}"}
        for item in world_items:
            if bankable(item):
                holdings[item["id"]] = {"template_id": item["template_id"], "owner": "world"}
        counts = dict(self.stacks) if stacks is None else stacks
        if counts != self.stacks or holdings != self.holdings:
            self.commit(counts, holdings)

    def add_template(self, template_id: str, count: int = 1) -> None:
        if template_id not in ITEM_TEMPLATES or type(count) is not int or count <= 0:
            raise ValueError("Invalid stash template/count")
        stacks = dict(self.stacks)
        stacks[template_id] = stacks.get(template_id, 0) + count
        self.commit(stacks, copy.deepcopy(self.holdings))

    def take_template(self, template_id: str) -> dict | None:
        if template_id not in ITEM_TEMPLATES:
            raise ValueError("Unknown item template")
        if self.stacks.get(template_id, 0) <= 0:
            return None
        item = make_item(template_id)
        stacks = dict(self.stacks)
        stacks[template_id] -= 1
        if not stacks[template_id]:
            stacks.pop(template_id)
        holdings = copy.deepcopy(self.holdings)
        holdings[item["id"]] = {"template_id": template_id, "owner": "withdrawal"}
        self.commit(stacks, holdings)
        return item

    def get_stacked_list(self) -> list[dict]:
        out = []
        for tid, count in self.stacks.items():
            if count <= 0 or tid not in ITEM_TEMPLATES:
                continue
            tpl = ITEM_TEMPLATES[tid]
            out.append({
                "template_id": tid,
                "name": tpl["name"],
                "category": tpl["category"],
                "weight": tpl["weight"],
                "description": tpl["description"],
                "count": count,
            })
        return out


def _starter(item: dict) -> dict:
    item["starter"] = True
    return item


def inventory_items(player: dict) -> list[dict]:
    inv = player.get("inventory") or {}
    return list(inv.get("storage", [])) + [item for item in inv.get("equipped", {}).values() if item]


def bankable(item: dict) -> bool:
    return not item.get("starter", False)


def active_weapon_item(player: dict) -> dict | None:
    inv = player.get("inventory") or {}
    item = inv.get("equipped", {}).get(inv.get("active_weapon_slot", "primary_weapon"))
    return item if item and "weapon" in item else None


def sync_weapon_state(player: dict) -> None:
    # Player ammo remains a compatibility mirror for existing callers/test fixtures.
    previous_id = player.get("_weapon_mirror_id")
    mirror = (player.get("ammo", 0), player.get("reload_timer", 0.0))
    if previous_id and mirror != player.get("_weapon_mirror_values"):
        previous = next((it for it in inventory_items(player) if it["id"] == previous_id), None)
        if previous and "weapon" in previous:
            previous["weapon_state"] = {"ammo": max(0, min(int(mirror[0]), int(previous["weapon"]["mag_size"]))), "reload_timer": max(0.0, float(mirror[1]))}
    item = active_weapon_item(player)
    if item:
        maximum = int(item["weapon"]["mag_size"])
        state = item.setdefault("weapon_state", {"ammo": maximum, "reload_timer": 0.0})
        player["max_ammo"] = maximum
        player["ammo"] = state["ammo"]
        player["reload_timer"] = state["reload_timer"]
    else:
        player["ammo"] = player["max_ammo"] = 0
        player["reload_timer"] = 0.0
    player["_weapon_mirror_id"] = item["id"] if item else None
    player["_weapon_mirror_values"] = (player["ammo"], player["reload_timer"])


def build_player_inventory(class_id: str) -> dict[str, Any]:
    loadout = CLASS_MIN_LOADOUT[class_id]
    max_wt = CLASS_CARRY_CAPACITY[class_id]
    storage = [_starter(make_item(tid)) for tid in loadout["storage"]]
    cur_wt = sum(int(i.get("weight", 1)) for i in storage)
    return {
        "equipped": {
            "primary_weapon": _starter(make_item(loadout["primary_weapon"])),
            "secondary_weapon": _starter(make_item(loadout["secondary_weapon"])),
            "special_ability": _starter(make_item(loadout["special_ability"])),
            "quick_use": _starter(make_item(loadout["quick_use"])),
        },
        "active_weapon_slot": "primary_weapon",
        "storage": storage,
        "current_weight": cur_wt,
        "max_weight": max_wt,
    }


def get_active_weapon_config(player: dict[str, Any], default_weapon: dict[str, Any]) -> dict[str, Any]:
    item = active_weapon_item(player)
    if item:
        cfg = copy.deepcopy(item["weapon"])
        definition = ITEM_TEMPLATES[item["template_id"]]["weapon"]
        for key in ("fire_sound", "reload_sound", "can_fire_while_running"):
            cfg[key] = definition[key]
        cfg["name"] = item.get("name", default_weapon.get("name", ""))
        cfg["template_id"] = item.get("template_id", "")
        return cfg
    return {}


def is_light_weapon(player: dict[str, Any], default_weapon: dict[str, Any] | None = None) -> bool:
    item = active_weapon_item(player)
    return bool(item and ITEM_TEMPLATES[item["template_id"]]["weapon"]["can_fire_while_running"])


def item_effect(item: dict) -> dict:
    """Legacy expanded values survive; new effect tuning comes from stable references."""
    effect = mutable_copy(ITEM_TEMPLATES[item["template_id"]].get("effect", {}))
    legacy = item.get("value")
    if isinstance(legacy, dict):
        for key in ("heal", "sight_boost", "loot"):
            if key in legacy:
                effect[key] = legacy[key]
    return effect


def sight_boost(player: dict) -> float:
    inv = player.get("inventory") or {}
    items = list(inv.get("storage", [])) + [inv.get("equipped", {}).get("quick_use")]
    return max((float(item_effect(it).get("sight_boost", 0)) for it in items if it), default=0.0)


def recalc_inventory(player: dict[str, Any]) -> None:
    inv = player.get("inventory")
    if not inv:
        return
    inv["max_weight"] = CLASS_CARRY_CAPACITY[player["class_id"]]
    inv["current_weight"] = sum(int(i.get("weight", 1)) for i in inv.get("storage", []))
    sync_weapon_state(player)


def apply_min_loadout(player: dict[str, Any]) -> None:
    player["inventory"] = build_player_inventory(player.get("class_id", "vanguard"))
    recalc_inventory(player)


def add_item_to_player(player: dict[str, Any], item: dict[str, Any], target_slot: str | None = None) -> tuple[bool, str]:
    inv = player.get("inventory")
    if not inv:
        return False, "No inventory"

    cat = item.get("category", "storage")
    valid_slots = ("primary_weapon", "secondary_weapon", "special_ability", "quick_use")
    if item.get("template_id") not in ITEM_TEMPLATES:
        return False, "Unknown item template"
    if target_slot is not None and (target_slot not in valid_slots or cat != target_slot):
        return False, "Item cannot be equipped in that slot"
    if any(it["id"] == item["id"] for it in inventory_items(player)):
        return False, "Item already owned"
    current_weight = sum(int(it.get("weight", 1)) for it in inv.get("storage", []))
    maximum = CLASS_CARRY_CAPACITY[player["class_id"]]

    if target_slot in valid_slots and cat == target_slot:
        prev = inv["equipped"].get(target_slot)
        if current_weight + (int(prev.get("weight", 1)) if prev else 0) > maximum:
            return False, "Not enough storage capacity to swap equipped item"
        sync_weapon_state(player)
        if prev is not None:
            inv["storage"].append(prev)
        inv["equipped"][target_slot] = copy.deepcopy(item)
        recalc_inventory(player)
        return True, f"Equipped {item['name']}"

    if target_slot is None and cat in valid_slots and inv["equipped"].get(cat) is None:
        if current_weight > maximum:
            return False, "Storage capacity exceeded"
        sync_weapon_state(player)
        inv["equipped"][cat] = copy.deepcopy(item)
        recalc_inventory(player)
        return True, f"Equipped {item['name']}"

    wt = int(item.get("weight", 1))
    if current_weight + wt > maximum:
        return False, f"Too heavy! Requires {wt} wt ({current_weight}/{maximum} wt used)"

    sync_weapon_state(player)
    inv["storage"].append(copy.deepcopy(item))
    recalc_inventory(player)
    return True, f"Stored {item['name']} ({wt} wt)"


def remove_item_from_player(player: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    inv = player.get("inventory")
    if not inv:
        return None
    if not any(item.get("id") == item_id for item in inventory_items(player)):
        return None
    sync_weapon_state(player)
    for i, item in enumerate(inv["storage"]):
        if item.get("id") == item_id:
            removed = inv["storage"].pop(i)
            recalc_inventory(player)
            return removed
    for slot, item in list(inv["equipped"].items()):
        if item and item.get("id") == item_id:
            inv["equipped"][slot] = None
            recalc_inventory(player)
            return item
    return None


def equip_item_from_storage(player: dict[str, Any], item_id: str, target_slot: str | None = None) -> tuple[bool, str]:
    inv = player.get("inventory")
    if not inv:
        return False, "No inventory"
    idx = next((i for i, it in enumerate(inv["storage"]) if it.get("id") == item_id), None)
    if idx is None:
        return False, "Item not in storage"
    item = inv["storage"][idx]
    if item.get("template_id") not in ITEM_TEMPLATES:
        return False, "Unknown item template"
    slot = target_slot or item.get("category")
    if slot not in ("primary_weapon", "secondary_weapon", "special_ability", "quick_use") or item.get("category") != slot:
        return False, "Item cannot be equipped in that slot"
    prev = inv["equipped"].get(slot)
    resulting_weight = sum(int(it.get("weight", 1)) for it in inv["storage"]) - int(item.get("weight", 1)) + (int(prev.get("weight", 1)) if prev else 0)
    if resulting_weight > CLASS_CARRY_CAPACITY[player["class_id"]]:
        return False, "Not enough storage capacity to swap equipped item"
    sync_weapon_state(player)
    inv["storage"].pop(idx)
    if prev is not None:
        inv["storage"].append(prev)
    inv["equipped"][slot] = item
    recalc_inventory(player)
    return True, f"Equipped {item['name']}"


def unequip_slot_to_storage(player: dict[str, Any], slot: str) -> tuple[bool, str]:
    inv = player.get("inventory")
    if not inv or slot not in inv["equipped"]:
        return False, "Invalid slot"
    item = inv["equipped"].get(slot)
    if not item:
        return False, "Slot is empty"
    wt = int(item.get("weight", 1))
    if sum(int(it.get("weight", 1)) for it in inv["storage"]) + wt > CLASS_CARRY_CAPACITY[player["class_id"]]:
        return False, "Storage full (cannot unequip)"
    sync_weapon_state(player)
    inv["equipped"][slot] = None
    inv["storage"].append(item)
    recalc_inventory(player)
    return True, f"Moved {item['name']} to storage"


def create_body_chest_from_player(player: dict[str, Any]) -> dict[str, Any] | None:
    inv = player.get("inventory")
    if not inv:
        return None
    sync_weapon_state(player)
    items: list[dict[str, Any]] = []
    for slot in ("primary_weapon", "secondary_weapon", "special_ability", "quick_use"):
        if inv["equipped"].get(slot):
            if bankable(inv["equipped"][slot]):
                items.append(inv["equipped"][slot])
            inv["equipped"][slot] = None
    items.extend(item for item in inv.get("storage", []) if bankable(item))
    inv["storage"] = []
    recalc_inventory(player)
    if not items:
        return None
    return {
        "id": f"body_{player['id']}_{uuid.uuid4().hex}",
        "type": "body_chest",
        "name": f"{player['name']}'s Body Chest",
        "x": round(float(player["x"]), 2),
        "y": round(float(player["y"]), 2),
        "radius": 2.5,
        "locked": False,
        "puzzle_id": None,
        "items": items,
    }


def return_non_starter_items(player: dict[str, Any], stash: "SharedStash") -> int:
    """Returns every non-starter item a player carries back to the shared stash."""
    return deposit_extracted_loot(player, stash, all_items=True)


def deposit_extracted_loot(player: dict[str, Any], stash: SharedStash, *, all_items=False) -> int:
    staged = copy.deepcopy(player)
    items = inventory_items(staged) if all_items else list((staged.get("inventory") or {}).get("storage", []))
    deposited = [item for item in items if bankable(item)]
    stacks = dict(stash.stacks)
    for item in deposited:
        if item.get("template_id") not in ITEM_TEMPLATES:
            raise ValueError("Unknown item template")
        stacks[item["template_id"]] = stacks.get(item["template_id"], 0) + 1
        remove_item_from_player(staged, item["id"])
    stash.commit_player(player, staged, stacks=stacks)
    player.update(staged)
    return len(deposited)


def deposit_squad_loot(players: list[dict], stash: SharedStash) -> int:
    staged = [copy.deepcopy(player) for player in players]
    stacks, holdings = dict(stash.stacks), copy.deepcopy(stash.holdings)
    count = 0
    for before, after in zip(players, staged):
        for item in inventory_items(before):
            holdings.pop(item["id"], None)
        for item in list(after["inventory"]["storage"]):
            if bankable(item):
                tid = item["template_id"]
                if tid not in ITEM_TEMPLATES:
                    raise ValueError("Unknown item template")
                stacks[tid] = stacks.get(tid, 0) + 1
                remove_item_from_player(after, item["id"])
                count += 1
        for item in inventory_items(after):
            if bankable(item):
                holdings[item["id"]] = {"template_id": item["template_id"], "owner": f"player:{after['id']}"}
    if stacks != stash.stacks or holdings != stash.holdings:
        stash.commit(stacks, holdings)
    for before, after in zip(players, staged):
        before.update(after)
    return count
