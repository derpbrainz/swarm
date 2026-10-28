"""Verified defects, explicitly owned by later sequential implementation stages."""

import copy
import pytest

from backend.app.config.items import make_item


def test_active_class_change_does_not_heal(game_room):
    player = game_room.add_player("player")
    game_room.phase = "active"
    player["hp"] = 1
    assert game_room.select_class("player", "vanguard") is False
    assert player["hp"] == 1


def test_same_slot_switch_preserves_ammo(game_room):
    player = game_room.add_player("player")
    player["ammo"] = 1
    game_room.handle_inventory_action("player", {"action": "switch_weapon", "slot": "primary_weapon"})
    assert player["ammo"] == 1


def test_starter_deposit_does_not_grow_stash(game_room):
    player = game_room.add_player("player")
    starter = player["inventory"]["equipped"]["primary_weapon"]
    before = copy.deepcopy(game_room.shared_stash.stacks)
    game_room.player_to_stash("player", starter["id"])
    assert game_room.shared_stash.stacks == before


def test_item_instance_does_not_alias_nested_definition():
    one, two = make_item("medkit"), make_item("medkit")
    assert one["value"] is not two["value"]


def test_instantiating_saved_map_does_not_write(game_room, monkeypatch):
    def unexpected_save():
        pytest.fail("instantiating a saved map wrote its template")

    monkeypatch.setattr(game_room.level_store, "save", unexpected_save)
    game_room.level_store.instantiate_map("outpost_101")
