from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from backend.app.config.items import CLASS_CARRY_CAPACITY, CLASS_MIN_LOADOUT, ITEM_TEMPLATES, make_item

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "shared_stash.json"

DEFAULT_STASH_STACKS = {
    "medkit": 4,
    "stim_injector": 3,
    "ammo_pack": 5,
    "heavy_ammo": 2,
    "tactical_light": 2,
    "portable_turret": 2,
    "sonar_beacon": 2,
    "plasma_lmg": 1,
    "rail_carbine": 1,
    "machine_pistol": 2,
    "flare_revolver": 2,
    "service_pistol": 2,
}


class SharedStash:
    """Server-persisted shared inventory where multiple copies of the same item stack."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATA_PATH
        self.stacks: dict[str, int] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.stacks = {k: int(v) for k, v in data.items() if int(v) > 0 and k in ITEM_TEMPLATES}
                    return
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        self.stacks = dict(DEFAULT_STASH_STACKS)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.stacks, indent=2), encoding="utf-8")

    def add_template(self, template_id: str, count: int = 1) -> None:
        if template_id not in ITEM_TEMPLATES or count <= 0:
            return
        self.stacks[template_id] = self.stacks.get(template_id, 0) + count
        self.save()

    def take_template(self, template_id: str) -> dict | None:
        if self.stacks.get(template_id, 0) <= 0:
            return None
        self.stacks[template_id] -= 1
        if self.stacks[template_id] <= 0:
            self.stacks.pop(template_id, None)
        self.save()
        return make_item(template_id)

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


def build_player_inventory(class_id: str) -> dict[str, Any]:
    loadout = CLASS_MIN_LOADOUT.get(class_id, CLASS_MIN_LOADOUT["vanguard"])
    max_wt = CLASS_CARRY_CAPACITY.get(class_id, 15)
    storage = [_starter(make_item(loadout["storage"])), _starter(make_item("medkit"))]
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
    inv = player.get("inventory")
    if not inv:
        return default_weapon
    slot = inv.get("active_weapon_slot", "primary_weapon")
    item = inv["equipped"].get(slot) or inv["equipped"].get("primary_weapon") or inv["equipped"].get("secondary_weapon")
    if item and "weapon" in item:
        cfg = dict(item["weapon"])
        cfg["name"] = item.get("name", default_weapon.get("name", ""))
        cfg["template_id"] = item.get("template_id", "")
        return cfg
    return default_weapon


def is_light_weapon(player: dict[str, Any], default_weapon: dict[str, Any] | None = None) -> bool:
    """Returns True if the active equipped weapon is a light sidearm (e.g. pistol, revolver)."""
    inv = player.get("inventory")
    if not inv:
        return True
    slot = inv.get("active_weapon_slot", "primary_weapon")
    if slot == "secondary_weapon":
        return True
    item = inv.get("equipped", {}).get(slot)
    if not item:
        cfg = default_weapon or {}
        name = cfg.get("name", "").lower()
        tid = cfg.get("template_id", "").lower()
        return "pistol" in name or "revolver" in name or "pistol" in tid or "revolver" in tid
    w_name = item.get("name", "").lower()
    t_id = item.get("template_id", "").lower()
    cat = item.get("category", "")
    return (
        cat == "secondary_weapon"
        or "pistol" in w_name
        or "revolver" in w_name
        or "pistol" in t_id
        or "revolver" in t_id
        or int(item.get("weight", 99)) <= 3
    )


def has_tactical_light(player: dict[str, Any]) -> bool:
    inv = player.get("inventory")
    if not inv:
        return False
    qu = inv["equipped"].get("quick_use")
    if qu and qu.get("template_id") == "tactical_light":
        return True
    return any(i.get("template_id") == "tactical_light" for i in inv.get("storage", []))


def recalc_inventory(player: dict[str, Any]) -> None:
    inv = player.get("inventory")
    if not inv:
        return
    inv["max_weight"] = CLASS_CARRY_CAPACITY.get(player.get("class_id", "vanguard"), 15)
    inv["current_weight"] = sum(int(i.get("weight", 1)) for i in inv.get("storage", []))
    slot = inv.get("active_weapon_slot", "primary_weapon")
    w_item = inv["equipped"].get(slot) or inv["equipped"].get("primary_weapon") or inv["equipped"].get("secondary_weapon")
    if w_item and "weapon" in w_item:
        mag = int(w_item["weapon"]["mag_size"])
        player["max_ammo"] = mag
        player["ammo"] = min(player.get("ammo", mag), mag)


def apply_min_loadout(player: dict[str, Any]) -> None:
    player["inventory"] = build_player_inventory(player.get("class_id", "vanguard"))
    recalc_inventory(player)
    player["ammo"] = player["max_ammo"]


def add_item_to_player(player: dict[str, Any], item: dict[str, Any], target_slot: str | None = None) -> tuple[bool, str]:
    inv = player.get("inventory")
    if not inv:
        apply_min_loadout(player)
        inv = player["inventory"]

    recalc_inventory(player)
    cat = item.get("category", "storage")
    valid_slots = ("primary_weapon", "secondary_weapon", "special_ability", "quick_use")

    if target_slot in valid_slots and cat == target_slot:
        prev = inv["equipped"].get(target_slot)
        if prev is not None:
            if inv["current_weight"] + int(prev.get("weight", 1)) > inv["max_weight"]:
                return False, "Not enough storage capacity to swap equipped item"
            inv["storage"].append(prev)
        inv["equipped"][target_slot] = item
        recalc_inventory(player)
        if target_slot == inv.get("active_weapon_slot"):
            player["ammo"] = player["max_ammo"]
        return True, f"Equipped {item['name']}"

    if target_slot is None and cat in valid_slots and inv["equipped"].get(cat) is None:
        inv["equipped"][cat] = item
        recalc_inventory(player)
        return True, f"Equipped {item['name']}"

    wt = int(item.get("weight", 1))
    if inv["current_weight"] + wt > inv["max_weight"]:
        return False, f"Too heavy! Requires {wt} wt ({inv['current_weight']}/{inv['max_weight']} wt used)"

    inv["storage"].append(item)
    recalc_inventory(player)
    return True, f"Stored {item['name']} ({wt} wt)"


def remove_item_from_player(player: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    inv = player.get("inventory")
    if not inv:
        return None
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
    slot = target_slot or item.get("category")
    if slot not in ("primary_weapon", "secondary_weapon", "special_ability", "quick_use") or item.get("category") != slot:
        return False, "Item cannot be equipped in that slot"
    prev = inv["equipped"].get(slot)
    inv["storage"].pop(idx)
    if prev is not None:
        inv["storage"].append(prev)
    inv["equipped"][slot] = item
    recalc_inventory(player)
    if slot == inv.get("active_weapon_slot"):
        player["ammo"] = player["max_ammo"]
    return True, f"Equipped {item['name']}"


def unequip_slot_to_storage(player: dict[str, Any], slot: str) -> tuple[bool, str]:
    inv = player.get("inventory")
    if not inv or slot not in inv["equipped"]:
        return False, "Invalid slot"
    item = inv["equipped"].get(slot)
    if not item:
        return False, "Slot is empty"
    recalc_inventory(player)
    wt = int(item.get("weight", 1))
    if inv["current_weight"] + wt > inv["max_weight"]:
        return False, "Storage full (cannot unequip)"
    inv["equipped"][slot] = None
    inv["storage"].append(item)
    recalc_inventory(player)
    return True, f"Moved {item['name']} to storage"


def create_body_chest_from_player(player: dict[str, Any]) -> dict[str, Any] | None:
    inv = player.get("inventory")
    if not inv:
        return None
    items: list[dict[str, Any]] = []
    for slot in ("primary_weapon", "secondary_weapon", "special_ability", "quick_use"):
        if inv["equipped"].get(slot):
            items.append(inv["equipped"][slot])
            inv["equipped"][slot] = None
    items.extend(inv.get("storage", []))
    inv["storage"] = []
    recalc_inventory(player)
    if not items:
        return None
    return {
        "id": f"body_{player['id']}_{uuid.uuid4().hex[:4]}",
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
    inv = player.get("inventory")
    if not inv:
        return 0
    count = 0
    items = list(inv.get("storage", [])) + [it for it in inv.get("equipped", {}).values() if it]
    for item in items:
        if item.get("starter") or item.get("category") == "special_ability":
            continue
        tid = item.get("template_id") or item.get("item_type")
        if tid:
            stash.add_template(tid, 1)
            count += 1
    return count


def deposit_extracted_loot(player: dict[str, Any], stash: SharedStash) -> int:
    inv = player.get("inventory")
    if not inv:
        return 0
    count = 0
    keep: list[dict[str, Any]] = []
    for item in list(inv.get("storage", [])):
        if item.get("starter"):
            keep.append(item)
            continue
        tid = item.get("template_id") or item.get("item_type")
        if tid:
            stash.add_template(tid, 1)
            count += 1
    inv["storage"] = keep
    recalc_inventory(player)
    return count


shared_stash = SharedStash()