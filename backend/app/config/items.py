"""Read-only resolved item definitions and independent mutable instances."""

from types import MappingProxyType
import uuid

from backend.app.content import CATALOG, mutable_copy

CLASS_CARRY_CAPACITY = MappingProxyType({identity: value["carry_capacity"] for identity, value in CATALOG.classes.items()})
CLASS_MIN_LOADOUT = MappingProxyType({identity: CATALOG.definitions["loadouts"][value["loadout_id"]] for identity, value in CATALOG.classes.items()})
ITEM_TEMPLATES = CATALOG.items


def make_item(item_type: str, item_id: str | None = None) -> dict:
    if item_type not in ITEM_TEMPLATES:
        raise ValueError(f"Unknown item template: {item_type}")
    item = mutable_copy(ITEM_TEMPLATES[item_type])
    item["id"] = item_id or uuid.uuid4().hex
    item["type"] = item["template_id"] = item["item_type"] = item_type
    if "weapon" in item:
        item["weapon_state"] = {"ammo": int(item["weapon"]["mag_size"]), "reload_timer": 0.0}
    return item
