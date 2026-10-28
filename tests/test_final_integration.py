"""Final cross-stage lifecycle regressions; always use injected temporary saves."""

import copy

import pytest

from backend.app.content import CATALOG, mutable_copy, validate_content
from backend.app.engine import objectives
from backend.app.engine.inventory import inventory_items
from backend.app.engine.persistence import SaveError


def medkit(room):
    assert room.stash_to_player("leader", "medkit")["ok"]
    return next(item for item in inventory_items(room.players["leader"]) if item["template_id"] == "medkit" and not item.get("starter"))


def test_default_defeat_return_restores_living_preparation_state(game_room):
    player = game_room.add_player("leader")
    assert game_room.start_mission("leader")
    player.update(hp=0, downed=True, body_dropped=True)
    game_room.phase = "defeat"
    game_room.reset_to_lobby()
    assert game_room.map_data is None
    assert player["hp"] == player["max_hp"]
    assert not player["downed"] and not player["body_dropped"]
    item = medkit(game_room)
    player["hp"] = 1
    result = game_room.handle_inventory_action("leader", {"action": "use_item", "item_id": item["id"]})
    assert result["ok"] and player["hp"] > 1
    assert item["id"] not in {i["id"] for i in inventory_items(player)}


def test_failed_heal_preserves_consumable_and_durable_claim(game_room):
    player = game_room.add_player("leader")
    item = medkit(game_room)
    player["hp"] = 0
    before = copy.deepcopy(player)
    holdings = copy.deepcopy(game_room.shared_stash.holdings)
    result = game_room.handle_inventory_action("leader", {"action": "use_item", "item_id": item["id"]})
    assert not result["ok"]
    assert player == before and game_room.shared_stash.holdings == holdings


def test_heal_persistence_failure_does_not_publish_hp_or_consumption(game_room, monkeypatch):
    player = game_room.add_player("leader")
    item = medkit(game_room)
    player["hp"] = 1
    before = copy.deepcopy(player)
    def fail(*args, **kwargs):
        raise SaveError("disk failed")
    monkeypatch.setattr(game_room.shared_stash, "commit_player", fail)
    with pytest.raises(SaveError):
        game_room.handle_inventory_action("leader", {"action": "use_item", "item_id": item["id"]})
    assert player == before


@pytest.mark.parametrize("authored", [True, False])
def test_enemy_free_testing_skips_only_real_enemy_objectives(game_room, authored):
    game_room.add_player("leader")
    record = game_room.level_store.levels["outpost_101"]
    record["tested"] = False
    target = record["map_template"]["enemies"][0]["id"]
    rule = {"id": "boss_gate", "label": "Defeat guard", "condition": "enemy_dead", "target_id": target, "effects": []}
    if authored:
        data = mutable_copy(CATALOG.definitions)
        data["missions"]["outpost"]["objectives"] = [rule]
        game_room.catalog = validate_content(data)
    else:
        record["map_template"]["objective_definitions"] = [rule]
    original = copy.deepcopy(record)
    assert game_room.start_mission("leader")
    assert game_room.map_data["test_mode"] and game_room.map_data["enemies"] == []
    objectives.update(game_room)
    state = next(s for s in game_room.map_data["objectives"] if s["id"] == "boss_gate")
    assert state["completed"] and state["testing_skipped"]
    assert game_room.players["leader"]["kills"] == 0
    assert record == original
    game_room.reset_to_lobby()
    assert game_room.start_mission("leader", force_test_mode=False)
    objectives.update(game_room)
    state = next(s for s in game_room.map_data["objectives"] if s["id"] == "boss_gate")
    assert not state["completed"]
    assert "testing_enemy_ids" not in game_room.map_data


def test_unknown_enemy_objective_still_rejects_testing_launch(game_room):
    game_room.add_player("leader")
    record = game_room.level_store.levels["outpost_101"]
    record["tested"] = False
    record["map_template"]["objective_definitions"] = [{"id": "invalid", "label": "Invalid", "condition": "enemy_dead", "target_id": "missing", "effects": []}]
    with pytest.raises(ValueError, match="unknown objective target"):
        game_room.start_mission("leader")
    assert game_room.phase == "lobby" and game_room.map_data is None


@pytest.mark.parametrize("fail_once", [False, True])
def test_last_hub_disconnect_cannot_refund_and_leave_collectible_drop(game_room, monkeypatch, fail_once):
    game_room.hub_world = "flagship_stub"
    game_room.reset_to_lobby()
    player = game_room.add_player("leader")
    service = next(t for t in game_room.map_data["interactions"] if t["type"] == "stash")
    player.update(x=service["x"], y=service["y"] + 1)
    initial = game_room.shared_stash.stacks.get("medkit", 0)
    assert game_room.stash_to_player("leader", "medkit", target_id=service["id"])["ok"]
    item = next(i for i in inventory_items(player) if i["template_id"] == "medkit" and not i.get("starter"))
    assert game_room.handle_inventory_action("leader", {"action": "drop_item", "item_id": item["id"]})["ok"]
    drop = game_room.map_data["loot"][-1]
    original_commit = game_room.shared_stash.commit
    if fail_once:
        def fail(*args, **kwargs):
            raise SaveError("settlement failed")
        monkeypatch.setattr(game_room.shared_stash, "commit", fail)
        game_room.remove_player("leader")
        assert not drop["collected"] and item["id"] in game_room.shared_stash.holdings
        assert "leader" in game_room.pending_disconnects
        monkeypatch.setattr(game_room.shared_stash, "commit", original_commit)
    game_room.remove_player("leader")
    assert game_room.shared_stash.stacks["medkit"] == initial
    assert drop["collected"] and item["id"] not in game_room.shared_stash.holdings
    joined = game_room.add_player("new")
    joined.update(x=drop["x"], y=drop["y"])
    assert not game_room.pickup_ground_item("new", drop["id"])["ok"]
    game_room.remove_player("new")
    assert game_room.shared_stash.stacks["medkit"] == initial


def test_invalid_automatic_launch_stays_in_preparation_and_clears_readiness(game_room):
    game_room.add_player("leader")
    record = game_room.level_store.levels["outpost_101"]
    record["map_template"]["objective_definitions"] = [{"id": "invalid", "label": "Invalid", "condition": "enemy_dead", "target_id": "missing", "effects": []}]
    assert game_room.prepare_mission("leader")
    assert game_room.toggle_ready("leader", True)
    game_room.step(5)
    assert game_room.phase == "prep" and game_room.map_data is None
    assert game_room.prep_timer == 0 and not game_room.players["leader"]["ready"]
    assert "Launch failed" in game_room.events_log[-1]
    game_room.step(1 / 30)
    assert game_room.phase == "prep"


@pytest.mark.parametrize("method", ["mark_tested", "mark_completed"])
def test_progress_save_failure_does_not_publish_unlock_flags(game_room, monkeypatch, method):
    store = game_room.level_store
    record = store.levels["outpost_101"]
    record.update(tested=False, completed=False, status="testing")
    store.save()
    before = copy.deepcopy(record)
    persisted = store.data_file.read_bytes()
    def fail():
        raise SaveError("progress failed")
    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(SaveError):
        getattr(store, method)(record["level_id"])
    assert record == before and store.data_file.read_bytes() == persisted


def test_generated_level_save_failure_does_not_advertise_unpersisted_record(game_room, monkeypatch):
    store = game_room.level_store
    before = set(store.levels)
    def fail():
        raise SaveError("generation failed")
    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(SaveError):
        store.generate_level("outpost", seed=981)
    assert set(store.levels) == before
