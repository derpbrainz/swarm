"""Strict intent schemas shared by transport and authoritative action boundaries."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identity = Annotated[str, Field(min_length=1, max_length=128)]
Slot = Literal["primary_weapon", "secondary_weapon", "special_ability", "quick_use"]


class PuzzleAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["rotate", "input_symbol", "tech_bypass", "start_attempt", "timed_input"]
    node_index: Annotated[int, Field(ge=0, le=255)] | None = None
    symbol: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    attempt_id: Identity | None = None
    puzzle_session_id: Identity | None = None

    @model_validator(mode="after")
    def valid_fields(self):
        required = {"rotate": {"node_index"}, "input_symbol": {"symbol"}, "tech_bypass": set(), "start_attempt": set(), "timed_input": {"symbol", "attempt_id"}}[self.action]
        fields = self.model_fields_set - {"action", "puzzle_session_id"}
        if "puzzle_session_id" in self.model_fields_set and self.puzzle_session_id is None:
            raise ValueError("puzzle session cannot be null")
        if fields != required or any(getattr(self, field) is None for field in required):
            raise ValueError("invalid fields for puzzle action")
        return self


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    type: Literal["set_name", "select_class", "select_mission", "generate_level", "prepare_mission", "start_mission", "toggle_ready", "min_loadout", "stash_to_player", "player_to_stash", "inventory_action", "pickup_item", "take_crate_item", "return_lobby", "input", "reload", "skill", "interact", "puzzle_action"]
    name: Annotated[str, Field(min_length=1, max_length=20)] | None = None
    class_id: Identity | None = None
    mission_id: Identity | None = None
    level_id: Identity | None = None
    seed: Annotated[int, Field(ge=0, le=2**31 - 1)] | None = None
    prepare: bool = True
    test_mode: bool | None = None
    ready: bool | None = None
    target_player_id: Identity | None = None
    template_id: Identity | None = None
    item_id: Identity | None = None
    crate_id: Identity | None = None
    puzzle_id: Identity | None = None
    target_id: Identity | None = None
    request_id: Identity | None = None
    session_id: Identity | None = None
    world_id: Identity | None = None
    target_slot: Slot | None = None
    slot: Slot | None = None
    action: Literal["switch_weapon", "equip_item", "unequip_slot", "use_item", "drop_item"] | PuzzleAction | None = None
    dx: Annotated[float, Field(ge=-1, le=1)] | None = None
    dy: Annotated[float, Field(ge=-1, le=1)] | None = None
    aim_x: Annotated[float, Field(ge=-100000, le=100000)] | None = None
    aim_y: Annotated[float, Field(ge=-100000, le=100000)] | None = None
    firing: bool | None = None
    running: bool | None = None

    @model_validator(mode="after")
    def valid_fields(self):
        allowed = {
            "set_name": {"name"}, "select_class": {"class_id"},
            "select_mission": {"mission_id", "level_id", "prepare", "target_id"},
            "generate_level": {"mission_id", "seed"}, "prepare_mission": {"target_id"},
            "start_mission": {"test_mode", "target_id"}, "toggle_ready": {"ready", "target_player_id"},
            "min_loadout": {"target_player_id"},
            "stash_to_player": {"template_id", "target_slot", "target_player_id", "target_id"},
            "player_to_stash": {"item_id", "target_player_id", "target_id"},
            "inventory_action": {"action", "item_id", "slot", "target_slot"},
            "pickup_item": {"item_id"}, "take_crate_item": {"crate_id", "item_id", "target_slot"},
            "return_lobby": set(), "input": {"dx", "dy", "aim_x", "aim_y", "firing", "running"},
            "reload": set(), "skill": set(), "interact": {"target_id"},
            "puzzle_action": {"puzzle_id", "action"},
        }[self.type]
        correlation = {"request_id", "session_id", "world_id"}
        if self.type in ("interact", "puzzle_action", "take_crate_item", "select_mission", "prepare_mission", "start_mission", "stash_to_player", "player_to_stash"):
            allowed |= correlation
        if self.model_fields_set & correlation:
            if not correlation.issubset(self.model_fields_set) or any(getattr(self, field) is None for field in correlation):
                raise ValueError("interaction correlation requires request, session and world")
        if "target_id" in self.model_fields_set and self.target_id is None:
            raise ValueError("target ID cannot be null")
        supplied = self.model_fields_set - {"type"}
        if not supplied.issubset(allowed):
            raise ValueError("fields do not belong to this command")
        required = {
            "set_name": {"name"}, "select_class": {"class_id"},
            "stash_to_player": {"template_id"}, "player_to_stash": {"item_id"},
            "inventory_action": {"action"}, "pickup_item": {"item_id"},
            "take_crate_item": {"crate_id", "item_id"}, "puzzle_action": {"puzzle_id", "action"},
        }.get(self.type, set())
        if any(getattr(self, field) is None for field in required):
            raise ValueError("required command field missing")
        if self.type == "select_mission" and not (self.level_id or self.mission_id):
            raise ValueError("mission or level ID required")
        if self.type == "set_name" and not self.name.strip():
            raise ValueError("name is blank")
        if self.type == "input":
            if any(getattr(self, field) is None for field in supplied):
                raise ValueError("input fields cannot be null")
            if ("aim_x" in supplied) != ("aim_y" in supplied):
                raise ValueError("aim requires both coordinates")
        if self.type == "puzzle_action" and not isinstance(self.action, PuzzleAction):
            raise ValueError("puzzle action must be an object")
        if self.type == "inventory_action":
            if not isinstance(self.action, str):
                raise ValueError("inventory action must be a string")
            if self.action in ("equip_item", "drop_item") and not self.item_id:
                raise ValueError("inventory item ID required")
            if self.action == "unequip_slot" and not self.slot:
                raise ValueError("inventory slot required")
            if self.action == "use_item" and not (self.item_id or self.slot == "quick_use"):
                raise ValueError("use_item requires item ID or quick_use slot")
            if self.action == "switch_weapon" and self.slot not in (None, "primary_weapon", "secondary_weapon"):
                raise ValueError("not a weapon slot")
        return self


def parse_command(raw: str, *, max_bytes: int = 8192) -> dict:
    if len(raw.encode("utf-8")) > max_bytes:
        raise ValueError("command too large")

    def reject_constant(value):
        raise ValueError(f"nonfinite value {value}")

    def unique_object(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("duplicate command key")
            data[key] = value
        return data

    try:
        data = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object)
    except RecursionError as exc:
        raise ValueError("command nesting too deep") from exc
    return Command.model_validate(data).model_dump(exclude_unset=True)
