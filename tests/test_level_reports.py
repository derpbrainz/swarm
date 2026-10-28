import copy

import pytest

from backend.app.commands import parse_command
from backend.app.engine.level_store import LevelStore
from backend.app.engine.persistence import SaveError
from backend.app.main import dispatch_command


def mission(room):
    room.add_player("leader")
    room.add_player("peer")
    record = room.level_store.levels["outpost_101"]
    record.update(completed=False, tested=False, broken=False, status="uncompleted")
    assert room.start_mission("leader")
    return record


def test_uncompleted_launch_keeps_enemy_and_loot_spawns(game_room):
    record = mission(game_room)
    assert len(game_room.map_data["enemies"]) == len(record["map_template"]["enemies"]) > 0
    assert game_room.map_data["loot"]
    assert not game_room.map_data["level_completed"]
    assert game_room.get_snapshot("peer")["map"]["can_report_broken"]


def test_any_player_report_retains_layout_and_reason_then_returns_squad(game_room):
    record = mission(game_room)
    layout = copy.deepcopy(record["map_template"])
    world = game_room.map_data["world_id"]
    response = dispatch_command(game_room, "peer", {"type": "report_broken_level", "level_id": record["level_id"],
                                                 "world_id": world, "reason": " Door blocks the only route. "})
    assert response["ok"] and game_room.phase == "lobby" and game_room.map_data is None
    assert set(game_room.players) == {"leader", "peer"}
    assert record["broken"] and not record["completed"] and not record["tested"]
    assert record["status"] == "broken" and record["map_template"] == layout
    assert record["failure_reports"][0]["reason"] == "Door blocks the only route."
    assert record["failure_reports"][0]["player_id"] == "peer"
    reloaded = LevelStore(game_room.level_store.data_file)
    assert reloaded.levels[record["level_id"]]["map_template"] == layout
    assert not reloaded.is_level_unlocked(record["level_id"])[0]
    assert reloaded.is_level_unlocked("outpost_154")[0]
    assert not game_room.report_broken_level("peer", record["level_id"], world, "again")["ok"]


def test_completed_levels_pass_testing_and_reject_failure_reports(game_room):
    record = mission(game_room)
    game_room.level_store.mark_completed(record["level_id"])
    assert record["completed"] and record["tested"] and record["status"] == "verified"
    assert not game_room.report_broken_level("peer", record["level_id"], game_room.map_data["world_id"], "reason")["ok"]
    assert not game_room.get_snapshot("peer")["map"]["can_report_broken"]


@pytest.mark.parametrize("reason", ["", "  ", "x" * 1001])
def test_reason_is_required_and_bounded(reason):
    import json
    with pytest.raises(ValueError):
        parse_command(json.dumps({"type": "report_broken_level", "level_id": "map", "world_id": "world", "reason": reason}))


def test_report_save_failure_leaves_level_and_world_unchanged(game_room, monkeypatch):
    record = mission(game_room)
    before = copy.deepcopy(record)
    world = game_room.map_data
    def fail():
        raise SaveError("disk failed")
    monkeypatch.setattr(game_room.level_store, "save", fail)
    with pytest.raises(SaveError):
        game_room.report_broken_level("peer", record["level_id"], world["world_id"], "stuck")
    assert record == before and game_room.map_data is world
    assert game_room.pending_broken_return is None


def test_saved_report_survives_failed_return_and_retries(game_room, monkeypatch):
    record = mission(game_room)
    original = game_room.reset_to_lobby
    def fail():
        raise SaveError("settlement failed")
    monkeypatch.setattr(game_room, "reset_to_lobby", fail)
    with pytest.raises(SaveError):
        game_room.report_broken_level("peer", record["level_id"], game_room.map_data["world_id"], "stuck")
    assert record["broken"] and game_room.pending_broken_return == record["level_id"]
    monkeypatch.setattr(game_room, "reset_to_lobby", original)
    game_room.step(1 / 30)
    assert game_room.phase == "lobby" and game_room.pending_broken_return is None
    assert len(record["failure_reports"]) == 1


def test_stale_world_or_unknown_player_cannot_report(game_room):
    record = mission(game_room)
    assert not game_room.report_broken_level("peer", record["level_id"], "old-world", "stuck")["ok"]
    assert not game_room.report_broken_level("missing", record["level_id"], game_room.map_data["world_id"], "stuck")["ok"]
    assert not record["broken"]


def test_squad_defeat_is_not_automatically_a_broken_map(game_room):
    record = mission(game_room)
    game_room.phase = "defeat"
    assert not record["broken"]
    assert game_room.get_snapshot("peer")["map"]["can_report_broken"]
    assert game_room.report_broken_level("peer", record["level_id"], game_room.map_data["world_id"], "Spawn is inaccessible")["ok"]
    assert record["broken"] and game_room.phase == "lobby"
