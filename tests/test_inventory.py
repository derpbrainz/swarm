"""Stage 04 identity, capacity, atomic ownership, and restart acceptance."""

import copy
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from backend.app.config.items import ITEM_TEMPLATES, make_item
from backend.app.content import mutable_copy
from backend.app.engine.inventory import (
    SharedStash, active_weapon_item, add_item_to_player, create_body_chest_from_player,
    deposit_extracted_loot, equip_item_from_storage, inventory_items,
    recalc_inventory, return_non_starter_items,
)
from backend.app.engine.persistence import SaveError, read_json, write_snapshot


def fail_save(*args, **kwargs):
    raise SaveError("injected disk failure")


def withdraw(room, pid, tid="medkit", slot=None):
    assert room.stash_to_player(pid, tid, target_slot=slot)["ok"]
    return next(it for it in inventory_items(room.players[pid]) if it["template_id"] == tid and not it.get("starter"))


def active(room, pid="p"):
    assert room.start_mission(pid)
    room.phase = "active"
    room.map_data["enemies"] = []
    room.map_data["walls"] = []
    return room.map_data


def restart_counts(room):
    return SharedStash(room.shared_stash.path).stacks


def test_unknown_templates_rejected_and_nested_definitions_unchanged():
    before = mutable_copy(ITEM_TEMPLATES)
    for tid in ("medkit", "plasma_lmg", "auto_turret"):
        first, second = make_item(tid), make_item(tid)
        assert first["id"] != second["id"]
        for key in ("weapon", "effect", "value"):
            if key in first:
                first[key]["test"] = [1]
                assert "test" not in second[key]
    assert ITEM_TEMPLATES == before
    with pytest.raises(ValueError, match="Unknown"):
        make_item("missing")


def test_world_item_ids_unique_with_legacy_stats_and_provenance(game_room):
    store = game_room.level_store
    before = copy.deepcopy(store.levels)
    first, second = store.instantiate_map("outpost_101"), store.instantiate_map("outpost_101")
    def items(world):
        return world["loot"] + [it for cr in world["crates"] for it in cr["items"]]
    assert {it["id"] for it in items(first)}.isdisjoint(it["id"] for it in items(second))
    assert len({it["id"] for it in items(first)}) == len(items(first))
    assert [it["spawn_id"] for it in items(first)] == [it["spawn_id"] for it in items(second)]
    assert all(it["world_id"] == first["world_id"] for it in items(first))
    assert store.levels == before
    first_weapon = next(it for it in items(first) if "weapon" in it)
    first_weapon["weapon"]["damage"] = 0
    assert store.levels == before
    store.levels["outpost_101"]["map_template"]["loot"][0]["template_id"] = "invalid"
    with pytest.raises(ValueError, match="Unknown"):
        store.instantiate_map("outpost_101")


def test_ammo_and_reload_follow_weapon_switch_reequip_and_start(game_room):
    p = game_room.add_player("p")
    primary = active_weapon_item(p)
    p["ammo"] = 1
    assert game_room.handle_inventory_action("p", {"action": "switch_weapon", "slot": "primary_weapon"})["ok"]
    assert primary["weapon_state"]["ammo"] == 1
    assert game_room.handle_inventory_action("p", {"action": "switch_weapon", "slot": "secondary_weapon"})["ok"]
    p["ammo"] = 3
    assert game_room.handle_inventory_action("p", {"action": "switch_weapon", "slot": "primary_weapon"})["ok"]
    assert p["ammo"] == 1
    assert game_room.handle_inventory_action("p", {"action": "unequip_slot", "slot": "primary_weapon"})["ok"]
    assert p["ammo"] == p["max_ammo"] == 0
    assert game_room.handle_inventory_action("p", {"action": "equip_item", "item_id": primary["id"]})["ok"]
    assert p["ammo"] == 1
    active(game_room)
    assert p["ammo"] == 1  # Mission launch cannot refill retained weapons.
    assert game_room.trigger_reload("p")
    timer = p["reload_timer"]
    assert game_room.handle_inventory_action("p", {"action": "switch_weapon", "slot": "secondary_weapon"})["ok"]
    assert p["ammo"] == 3 and p["reload_timer"] == 0
    game_room.step(0.1)
    assert game_room.handle_inventory_action("p", {"action": "switch_weapon", "slot": "primary_weapon"})["ok"]
    assert p["reload_timer"] == timer  # Holstered reload pauses, belongs to that weapon.
    game_room.step(timer + 0.01)
    assert p["ammo"] == p["max_ammo"]


def test_legacy_expanded_weapon_state_initialized_without_fallback(game_room):
    p = game_room.add_player("p")
    primary = active_weapon_item(p)
    primary.pop("weapon_state")
    primary["weapon"]["mag_size"] = 7
    recalc_inventory(p)
    assert p["ammo"] == 7
    active(game_room)
    p["inventory"]["equipped"]["primary_weapon"] = None
    p["inventory"]["equipped"]["secondary_weapon"] = None
    p["ammo"] = 100
    p["input"]["firing"] = True
    assert not game_room.trigger_reload("p")
    game_room.step(0.1)
    assert p["ammo"] == p["max_ammo"] == 0
    assert not game_room.projectiles


def test_resulting_storage_capacity_and_slot_rejection_are_atomic(game_room):
    p = game_room.add_player("p")
    game_room.select_class("p", "recon")
    smg = make_item("compact_smg")
    p["inventory"]["storage"] = [smg] + [make_item("credits") for _ in range(4)]
    recalc_inventory(p)
    before = copy.deepcopy(p)
    assert not equip_item_from_storage(p, smg["id"])[0]
    assert p == before
    assert not add_item_to_player(p, make_item("medkit"), "primary_weapon")[0]
    assert p == before
    p["inventory"]["storage"].pop()
    assert equip_item_from_storage(p, smg["id"])[0]
    assert p["inventory"]["current_weight"] == 11 <= p["inventory"]["max_weight"]


@pytest.mark.parametrize("operation", ["withdraw", "deposit", "reset", "class", "extract"])
def test_failed_stash_transition_preserves_runtime_disk_and_retry(game_room, operation):
    p = game_room.add_player("p")
    if operation != "withdraw":
        item = withdraw(game_room, "p")
    before_p, counts, records = copy.deepcopy(p), dict(game_room.shared_stash.stacks), copy.deepcopy(game_room.shared_stash.holdings)
    disk = game_room.shared_stash.path.read_bytes()
    def run():
        if operation == "withdraw":
            return game_room.stash_to_player("p", "medkit")
        if operation == "deposit":
            return game_room.player_to_stash("p", item["id"])
        if operation == "reset":
            return game_room.restore_min_loadout("p")
        if operation == "class":
            return game_room.select_class("p", "recon")
        return deposit_extracted_loot(p, game_room.shared_stash)
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=fail_save):
        with pytest.raises(SaveError):
            run()
    assert p == before_p
    assert game_room.shared_stash.stacks == counts
    assert game_room.shared_stash.holdings == records
    assert game_room.shared_stash.path.read_bytes() == disk
    with patch("backend.app.engine.inventory.write_snapshot", wraps=write_snapshot) as writes:
        run()
        assert writes.call_count == 1
    if operation == "extract":
        after = game_room.shared_stash.path.read_bytes()
        assert deposit_extracted_loot(p, game_room.shared_stash) == 0
        assert game_room.shared_stash.path.read_bytes() == after


def test_rejected_withdraw_does_not_write_or_decrement(game_room):
    p = game_room.add_player("p")
    p["inventory"]["storage"] = [make_item("isotope_cell") for _ in range(6)]
    before = copy.deepcopy(p)
    counts = dict(game_room.shared_stash.stacks)
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=AssertionError("must validate first")):
        assert not game_room.stash_to_player("p", "medkit")["ok"]
        assert not game_room.stash_to_player("p", "missing")["ok"]
    assert p == before and game_room.shared_stash.stacks == counts


def test_prep_withdrawal_recovered_once_in_fresh_process(game_room):
    game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    item = withdraw(game_room, "p")
    data = read_json(game_room.shared_stash.path)
    assert data["shared_stash"]["medkit"] == initial["medkit"] - 1
    assert data["holdings"][item["id"]]["owner"] == "player:p"
    code = "import json,sys; from pathlib import Path; from backend.app.engine.inventory import SharedStash; print(json.dumps(SharedStash(Path(sys.argv[1])).stacks))"
    result = subprocess.run([sys.executable, "-c", code, str(game_room.shared_stash.path)], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == initial
    assert restart_counts(game_room) == initial
    assert not read_json(game_room.shared_stash.path)["holdings"]


@pytest.mark.parametrize("source", ["ground", "interact", "chest"])
def test_all_pickup_paths_persist_claim_before_publication(game_room, source):
    p = game_room.add_player("p")
    world = active(game_room)
    item = make_item("medkit")
    item.update(x=p["x"], y=p["y"], collected=False)
    world["loot"] = [] if source == "chest" else [item]
    world["puzzles"] = []
    chest = {"id": "chest", "x": p["x"], "y": p["y"], "radius": 2.5, "locked": False, "name": "test", "items": [item]}
    world["crates"] = [chest] if source == "chest" else []
    counts = dict(game_room.shared_stash.stacks)
    before = copy.deepcopy(p), copy.deepcopy(world), game_room.shared_stash.path.read_bytes()
    def run():
        if source == "chest":
            return game_room.take_crate_item("p", "chest", item["id"])
        if source == "interact":
            return game_room.handle_interact("p")
        return game_room.pickup_ground_item("p", item["id"])
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=fail_save):
        with pytest.raises(SaveError):
            run()
    assert (p, world, game_room.shared_stash.path.read_bytes()) == before
    run()
    assert item["id"] in game_room.shared_stash.holdings
    assert len([it for it in inventory_items(p) if it["id"] == item["id"]]) == 1
    counts["medkit"] += 1
    assert restart_counts(game_room) == counts
    assert restart_counts(game_room) == counts


def test_active_drop_transfer_consumption_has_no_restart_refund_duplicates(game_room):
    first = game_room.add_player("p")
    second = game_room.add_player("q")
    initial = dict(game_room.shared_stash.stacks)
    item = withdraw(game_room, "p")
    active(game_room)
    second["x"], second["y"] = first["x"] + 0.6, first["y"] + 0.6
    assert game_room.handle_inventory_action("p", {"action": "drop_item", "item_id": item["id"]})["ok"]
    assert game_room.shared_stash.holdings[item["id"]]["owner"] == "world"
    assert game_room.pickup_ground_item("q", item["id"])["ok"]
    assert game_room.shared_stash.holdings[item["id"]]["owner"] == "player:q"
    before = copy.deepcopy(second), game_room.shared_stash.path.read_bytes()
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=fail_save):
        with pytest.raises(SaveError):
            game_room.handle_inventory_action("q", {"action": "use_item", "item_id": item["id"]})
    assert (second, game_room.shared_stash.path.read_bytes()) == before
    second["hp"] = 1
    assert game_room.handle_inventory_action("q", {"action": "use_item", "item_id": item["id"]})["ok"]
    assert second["hp"] == 46 and item["id"] not in game_room.shared_stash.holdings
    initial["medkit"] -= 1
    assert restart_counts(game_room) == initial


def test_drop_failure_and_world_teardown_settle_once(game_room):
    p = game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    item = withdraw(game_room, "p")
    world = active(game_room)
    before = copy.deepcopy(p), copy.deepcopy(world)
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=fail_save):
        with pytest.raises(SaveError):
            game_room.handle_inventory_action("p", {"action": "drop_item", "item_id": item["id"]})
    assert (p, world) == before
    assert game_room.handle_inventory_action("p", {"action": "drop_item", "item_id": item["id"]})["ok"]
    game_room.reset_to_lobby()
    assert game_room.shared_stash.stacks == initial
    game_room.reset_to_lobby()
    assert restart_counts(game_room) == initial


@pytest.mark.parametrize("phase", ["lobby", "prep", "active"])
def test_disconnect_failure_retry_and_restart_preserve_ownership(game_room, phase):
    game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    item = withdraw(game_room, "p")
    if phase == "active":
        game_room.add_player("q")
        active(game_room)
    else:
        game_room.phase = phase
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=fail_save):
        game_room.remove_player("p")
    assert "p" in game_room.players and "p" in game_room.pending_disconnects
    assert item["id"] in game_room.shared_stash.holdings
    game_room.remove_player("p")
    game_room.remove_player("p")
    assert "p" not in game_room.players and not game_room.pending_disconnects
    if phase == "active":
        chests = [cr for cr in game_room.map_data["crates"] if cr["type"] == "body_chest"]
        assert len(chests) == 1 and [it["id"] for it in chests[0]["items"]] == [item["id"]]
        assert game_room.shared_stash.holdings[item["id"]]["owner"] == "world"
    assert restart_counts(game_room) == initial
    assert restart_counts(game_room) == initial


def test_last_active_disconnect_returns_claimed_drop_not_all_map_loot(game_room):
    game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    withdraw(game_room, "p")
    active(game_room)
    game_room.remove_player("p")
    assert not game_room.players and game_room.phase == "lobby"
    assert game_room.shared_stash.stacks == initial
    assert restart_counts(game_room) == initial


def test_starter_paths_cannot_bank_or_mint_chest_loot(game_room):
    p = game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    for _ in range(4):
        starter = active_weapon_item(p)
        assert not game_room.player_to_stash("p", starter["id"])["ok"]
        assert game_room.restore_min_loadout("p")
    active(game_room)
    starter = active_weapon_item(p)
    assert not game_room.handle_inventory_action("p", {"action": "drop_item", "item_id": starter["id"]})["ok"]
    assert deposit_extracted_loot(p, game_room.shared_stash) == 0
    assert create_body_chest_from_player(p) is None
    assert not inventory_items(p)
    assert game_room.shared_stash.stacks == initial and not game_room.shared_stash.holdings


def test_squad_extraction_one_write_retry_and_restart(game_room):
    p, q = game_room.add_player("p"), game_room.add_player("q")
    initial = dict(game_room.shared_stash.stacks)
    first, second = withdraw(game_room, "p"), withdraw(game_room, "q")
    retained = withdraw(game_room, "p", "rail_carbine", "primary_weapon")
    world = active(game_room)
    for puzzle in world["puzzles"]:
        game_room._complete_puzzle(puzzle, "test")
    ext = world["extraction_zone"]
    for player in (p, q):
        player["x"], player["y"] = ext["x"] + 2, ext["y"] + 2
    before = copy.deepcopy(p["inventory"]), copy.deepcopy(q["inventory"]), game_room.shared_stash.path.read_bytes()
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=fail_save):
        with pytest.raises(SaveError):
            game_room.step(0.1)
    assert game_room.phase == "active"
    assert (p["inventory"], q["inventory"], game_room.shared_stash.path.read_bytes()) == before
    with patch("backend.app.engine.inventory.write_snapshot", wraps=write_snapshot) as writes:
        game_room.step(0.1)
        assert writes.call_count == 1
    assert game_room.phase == "victory"
    assert first["id"] not in game_room.shared_stash.holdings
    assert second["id"] not in game_room.shared_stash.holdings
    assert retained["id"] in game_room.shared_stash.holdings
    game_room.step(0.1)
    assert game_room.shared_stash.stacks["medkit"] == initial["medkit"]
    assert restart_counts(game_room) == initial


def test_return_all_including_nonstarter_ability_is_idempotent(game_room):
    p = game_room.add_player("p")
    ability = make_item("auto_turret")
    assert add_item_to_player(p, ability)[0]
    before = game_room.shared_stash.stacks.get("auto_turret", 0)
    assert return_non_starter_items(p, game_room.shared_stash) == 1
    assert return_non_starter_items(p, game_room.shared_stash) == 0
    assert restart_counts(game_room)["auto_turret"] == before + 1


def test_replacement_committed_then_fsync_error_publishes_transaction(game_room):
    game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    import backend.app.engine.persistence as persistence
    real_replace = persistence._replace_bytes
    def replace_then_error(path, payload):
        real_replace(path, payload)
        if path == game_room.shared_stash.path:
            raise OSError("directory fsync failed after replacement")
    with patch.object(persistence, "_replace_bytes", side_effect=replace_then_error):
        item = withdraw(game_room, "p")
    assert item["id"] in game_room.shared_stash.holdings
    assert restart_counts(game_room) == initial


def test_recovery_write_failure_keeps_records_for_next_start(game_room):
    game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    withdraw(game_room, "p")
    before = game_room.shared_stash.path.read_bytes()
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=fail_save):
        with pytest.raises(SaveError):
            SharedStash(game_room.shared_stash.path)
    assert game_room.shared_stash.path.read_bytes() == before
    assert restart_counts(game_room) == initial
    assert restart_counts(game_room) == initial


def test_last_disconnect_second_commit_failure_does_not_duplicate_chest(game_room):
    game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    withdraw(game_room, "p")
    active(game_room)
    writes = 0
    def fail_second(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise SaveError("world settlement failure")
        write_snapshot(*args, **kwargs)
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=fail_second):
        game_room.remove_player("p")
    assert "p" in game_room.pending_disconnects
    assert len([cr for cr in game_room.map_data["crates"] if cr["type"] == "body_chest"]) == 1
    assert not inventory_items(game_room.players["p"])
    game_room.step(0.1)
    assert not game_room.players and not game_room.pending_disconnects
    assert restart_counts(game_room) == initial


def test_body_chest_failure_retry_transfer_and_deposit_conserve_count(game_room):
    p, q = game_room.add_player("p"), game_room.add_player("q")
    initial = dict(game_room.shared_stash.stacks)
    item = withdraw(game_room, "p")
    active(game_room)
    p["downed"] = True
    p["hp"] = 0
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=fail_save):
        with pytest.raises(SaveError):
            game_room.step(0.1)
    assert not p["body_dropped"]
    assert item["id"] in [it["id"] for it in inventory_items(p)]
    game_room.step(0.1)
    chest = next(cr for cr in game_room.map_data["crates"] if cr["type"] == "body_chest")
    assert [it["id"] for it in chest["items"]] == [item["id"]]
    q["x"], q["y"] = chest["x"], chest["y"]
    assert game_room.take_crate_item("q", chest["id"], item["id"])["ok"]
    assert not chest["items"]
    game_room.reset_to_lobby()
    assert game_room.player_to_stash("q", item["id"])["ok"]
    assert not game_room.player_to_stash("q", item["id"])["ok"]
    assert restart_counts(game_room) == initial


def test_level_save_failure_after_extraction_cannot_repeat_deposit(game_room):
    p = game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    item = withdraw(game_room, "p")
    world = active(game_room)
    for puzzle in world["puzzles"]:
        game_room._complete_puzzle(puzzle, "test")
    ext = world["extraction_zone"]
    p["x"], p["y"] = ext["x"] + 2, ext["y"] + 2
    with patch("backend.app.engine.level_store.write_snapshot", side_effect=fail_save):
        with pytest.raises(SaveError):
            game_room.step(0.1)
    assert game_room.phase == "active"
    assert game_room.shared_stash.stacks == initial
    assert item["id"] not in game_room.shared_stash.holdings
    assert item["id"] not in [it["id"] for it in inventory_items(p)]
    game_room.step(0.1)
    assert game_room.phase == "victory"
    assert restart_counts(game_room) == initial


def test_equipped_replacement_preserves_each_weapon_magazine(game_room):
    p = game_room.add_player("p")
    old = active_weapon_item(p)
    p["ammo"] = 2
    weapon = make_item("rail_carbine")
    weapon["weapon_state"]["ammo"] = 3
    assert add_item_to_player(p, weapon, "primary_weapon")[0]
    assert p["ammo"] == 3
    assert equip_item_from_storage(p, old["id"])[0]
    assert p["ammo"] == 2
    assert equip_item_from_storage(p, weapon["id"])[0]
    assert p["ammo"] == 3


def test_selected_empty_slot_never_uses_other_equipped_weapon(game_room):
    p = game_room.add_player("p")
    active(game_room)
    assert game_room.handle_inventory_action("p", {"action": "unequip_slot", "slot": "primary_weapon"})["ok"]
    assert p["inventory"]["equipped"]["secondary_weapon"] is not None
    p["input"]["firing"] = True
    assert not game_room.trigger_reload("p")
    game_room.step(0.1)
    assert not game_room.projectiles and p["ammo"] == 0


@pytest.mark.parametrize("tid", ["medkit", "portable_turret", "sonar_beacon"])
def test_consumption_removes_recovery_claim_before_effect(game_room, tid):
    p = game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    item = withdraw(game_room, "p", tid)
    active(game_room)
    before = copy.deepcopy(p), copy.deepcopy(game_room.deployables)
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=fail_save):
        with pytest.raises(SaveError):
            game_room.handle_inventory_action("p", {"action": "use_item", "item_id": item["id"]})
    assert (p, game_room.deployables) == before
    assert game_room.handle_inventory_action("p", {"action": "use_item", "item_id": item["id"]})["ok"]
    initial[tid] -= 1
    assert restart_counts(game_room) == initial


def test_active_dropped_withdrawal_restart_refunds_only_one_copy(game_room):
    game_room.add_player("p")
    initial = dict(game_room.shared_stash.stacks)
    item = withdraw(game_room, "p", "rail_carbine")
    active(game_room)
    assert game_room.handle_inventory_action("p", {"action": "drop_item", "item_id": item["id"]})["ok"]
    assert restart_counts(game_room) == initial
    assert restart_counts(game_room) == initial


def test_same_template_retained_items_remain_independently_addressable(game_room):
    p = game_room.add_player("p")
    first = game_room.level_store.instantiate_map("outpost_101")
    second = game_room.level_store.instantiate_map("outpost_101")
    item1 = next(it for cr in first["crates"] for it in cr["items"] if it["template_id"] == "plasma_lmg")
    item2 = next(it for cr in second["crates"] for it in cr["items"] if it["spawn_id"] == item1["spawn_id"])
    assert add_item_to_player(p, item1, "primary_weapon")[0]
    assert add_item_to_player(p, item2)[0]
    game_room.shared_stash.commit_player({"id": "p"}, p)
    assert game_room.player_to_stash("p", item1["id"])["ok"]
    assert p["inventory"]["equipped"]["primary_weapon"] is None
    assert item2["id"] in [it["id"] for it in p["inventory"]["storage"]]


@pytest.mark.parametrize("holdings", [[], {"i": None}, {"i": {"template_id": "medkit"}}, {"i": {"template_id": 1, "owner": "world"}}])
def test_invalid_recovery_records_retained(tmp_path, holdings):
    path = tmp_path / "stash.json"
    path.write_text(json.dumps({"schema_version": 1, "shared_stash": {"medkit": 1}, "holdings": holdings}))
    before = path.read_bytes()
    with pytest.raises(SaveError):
        SharedStash(path)
    assert path.read_bytes() == before
