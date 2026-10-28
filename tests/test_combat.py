"""Stage 07 swept hits, target policy, durable down/revival and sonar acceptance."""

import json
import copy
import math
from unittest.mock import patch

import pytest

from backend.app.content import CATALOG, CONTENT_PATH, mutable_copy, validate_content
from backend.app.engine.combat import can_target
from backend.app.engine.inventory import SharedStash, inventory_items, make_item
from backend.app.engine.persistence import SaveError, write_snapshot
from backend.app.engine.physics import segment_intersects_circle, swept_circle_aabb_entry


DT = 1 / 30


def arena(room, classes=("recon", "medic")):
    players = []
    for index, cls in enumerate(classes):
        pid = f"p{index}"
        players.append(room.add_player(pid))
        assert room.select_class(pid, cls)
    assert room.start_mission("p0")
    room.phase = "active"
    room.map_data.update(walls=[], enemies=[], crates=[], puzzles=[], loot=[],
                         extraction_zone={"x": 200, "y": 200, "w": 2, "h": 2, "active": False})
    for index, p in enumerate(players):
        p.update(x=-10.0, y=20.0 + index * 10, angle=0.0)
    return players


def enemy(room, identity="e", x=1.0, y=0.0, radius=0.7, hp=45, faction="swarm", kind="stalker"):
    cfg = CATALOG.definitions["enemies"][kind]
    actor = {"id": identity, "type": kind, "faction": faction, "name": "Arbitrary large actor",
             "color": cfg["color"], "x": x, "y": y, "radius": radius, "hp": hp, "max_hp": hp,
             "angle": math.pi, "state": "patrol", "attack_timer": 100,
             "waypoints": [(x, y)], "waypoint_index": 0}
    room.map_data["enemies"].append(actor)
    return actor


def shot(room, owner=None, x=0.0, y=0.0, vx=55.0, radius=0.16, ttl=1.0, damage=10, **extra):
    owner = owner or room.players["p0"]
    proj = {"id": "shot", "owner_id": owner["id"], "faction": owner["faction"], "x": x, "y": y,
            "vx": vx, "vy": 0.0, "radius": radius, "ttl": ttl, "damage": damage, "color": "#ffffff", **extra}
    room.projectiles.append(proj)
    return proj


def ff(room, self_damage=False):
    room.combat_rules = {**mutable_copy(CATALOG.definitions["rules"]["combat"]), "friendly_fire": True, "self_damage": self_damage}


@pytest.mark.parametrize("wall_x", [None, 1.7])
def test_normal_30hz_55speed_rail_cannot_tunnel_or_hit_wall_behind_first(game_room, wall_x):
    p, _ = arena(game_room)
    target = enemy(game_room, x=55 * DT / 2, radius=0.7)
    if wall_x:
        game_room.map_data["walls"] = [{"id": "wall", "x": wall_x, "y": -1, "w": 0.1, "h": 2}]
    proj = shot(game_room, damage=50)
    game_room.step(DT)
    assert target["hp"] == 0 and p["kills"] == 1
    assert proj["x"] == pytest.approx(target["x"] - 0.86)
    assert not game_room.projectiles


@pytest.mark.parametrize("reverse", [False, True])
def test_nearest_actor_and_stable_actor_tie_not_list_order(game_room, reverse):
    arena(game_room)
    far = enemy(game_room, "far", x=1.6, radius=0.2)
    z = enemy(game_room, "z", x=0.8, radius=0.2)
    a = enemy(game_room, "a", x=0.8, radius=0.2)
    if reverse:
        game_room.map_data["enemies"].reverse()
    shot(game_room)
    game_room.step(DT)
    assert (far["hp"], z["hp"], a["hp"]) == (45, 45, 35)


def test_nearest_wall_and_exact_wall_actor_tie_win(game_room):
    arena(game_room)
    target = enemy(game_room, x=1.0, radius=0.5)
    game_room.map_data["walls"] = [{"id": "near", "x": 0.5, "y": -1, "w": 0.2, "h": 2}]
    shot(game_room, radius=0.125, vx=30)
    game_room.step(DT)
    assert target["hp"] == 45 and not game_room.projectiles
    game_room.map_data["walls"][0].update(x=0.1, active=False)
    shot(game_room)
    game_room.step(DT)
    assert target["hp"] == 35


@pytest.mark.parametrize("radius,y,hit", [(0.16, 0.7, True), (0.05, 0.7, False), (0.16, 3.1, False)])
def test_actor_projectile_footprints_and_arbitrary_large_circle(game_room, radius, y, hit):
    arena(game_room)
    target = enemy(game_room, x=0.9, y=y, radius=0.6)
    shot(game_room, radius=radius)
    game_room.step(DT)
    assert target["hp"] == (35 if hit else 45)
    game_room.map_data["enemies"] = []
    game_room.projectiles.clear()
    large = enemy(game_room, x=3.0, radius=2.5)
    shot(game_room, radius=radius)
    game_room.step(DT)
    assert large["hp"] == 35


def test_exact_rounded_wall_footprint_not_square_corner_false_positive():
    wall = {"x": 1, "y": 1, "w": 1, "h": 1}
    assert swept_circle_aabb_entry(0, 0.8, 1.1, 0.8, 0.25, wall) is not None
    assert swept_circle_aabb_entry(0.76, 0.76, 0.76, 0.76, 0.25, wall) is None
    assert swept_circle_aabb_entry(0.9, 0.9, 0.9, 0.9, 0.25, wall) == 0
    assert segment_intersects_circle(0, 0, 2, 0, 1, 1, 1) == 0.5
    assert segment_intersects_circle(0, 0, 0, 0, 0, 0, 1) == 0
    assert segment_intersects_circle(0, 0, 0, 0, 2, 0, 1) is None


@pytest.mark.parametrize("limit", ["ttl", "range"])
@pytest.mark.parametrize("offset,hit", [(0.0, True), (0.001, False)])
def test_ttl_range_final_endpoint_inclusive_no_overshoot(game_room, limit, offset, hit):
    arena(game_room)
    target = enemy(game_room, x=1.5 + offset, radius=0.25)
    args = {"ttl": 0.02} if limit == "ttl" else {"range_remaining": 1.0}
    shot(game_room, radius=0.25, vx=50, **args)
    game_room.step(DT)
    assert target["hp"] == (35 if hit else 45)
    assert not game_room.projectiles
    game_room.step(DT)
    assert target["hp"] == (35 if hit else 45)


def test_zero_ttl_never_contacts_initial_overlap(game_room):
    arena(game_room)
    target = enemy(game_room, x=0)
    shot(game_room, ttl=0)
    game_room.step(DT)
    assert target["hp"] == 45


def test_default_friendly_fire_passes_allies_to_hostile(game_room):
    p, ally = arena(game_room)
    ally.update(x=0.8, y=0)
    target = enemy(game_room, x=1.6, radius=0.2)
    shot(game_room)
    game_room.step(DT)
    assert ally["hp"] == ally["max_hp"] and target["hp"] == 35
    assert game_room.damage(ally, 1000, p, kind="melee") == 0
    assert game_room.damage(ally, 1000, p, kind="area") == 0
    assert game_room.damage(p, 1000, p, kind="area") == 0


@pytest.mark.parametrize("kind", ["projectile", "melee", "area", "hazard"])
def test_opt_in_allied_damage_down_attribution_idempotent_all_sources(game_room, kind):
    p, ally = arena(game_room)
    ff(game_room)
    ally["hp"] = 1
    assert game_room.damage(ally, 10, p, kind=kind) == 1
    assert ally["downed"] and p["kills"] == 1
    assert ally["last_damage"] == {"owner_id": "p0", "kind": kind}
    assert game_room.damage(ally, 10, p, kind=kind) == 0
    assert p["kills"] == 1
    game_room.step(DT)
    assert ally["body_dropped"] and not inventory_items(ally)


def test_opt_in_projectile_ally_interception_and_owner_self_policy(game_room):
    p, ally = arena(game_room)
    ff(game_room)
    ally.update(x=0.8, y=0)
    target = enemy(game_room, x=1.6, radius=0.2)
    shot(game_room)
    game_room.step(DT)
    assert ally["hp"] == ally["max_hp"] - 10 and target["hp"] == 45
    assert game_room.damage(p, 5, p) == 0
    ff(game_room, self_damage=True)
    proj = shot(game_room, x=p["x"], y=p["y"], vx=0)
    game_room.step(DT)
    assert p["hp"] == p["max_hp"] - 10
    assert proj not in game_room.projectiles


@pytest.mark.parametrize("kind,expected", [("projectile", 0), ("melee", 2), ("area", 10), ("hazard", 10)])
def test_opt_in_shield_and_immunity_shared_for_all_sources(game_room, kind, expected):
    p, ally = arena(game_room)
    ff(game_room, self_damage=True)
    ally["skill_active"] = "shockwave_shield"
    before = ally["hp"]
    assert game_room.damage(ally, 10, p, kind=kind) == expected
    assert ally["hp"] == before - expected
    ally["damage_immunities"] = [kind]
    assert game_room.damage(ally, 10, p, kind=kind) == 0
    ally["damage_immunities"] = []
    ally["invulnerable"] = True
    assert game_room.damage(ally, 10, p, kind=kind) == 0


def test_vanguard_passive_melee_preserves_effective_default(game_room):
    p, ally = arena(game_room, ("recon", "vanguard"))
    ff(game_room)
    assert game_room.damage(ally, 10, p, kind="melee") == 8
    assert game_room.damage(ally, 10, p, kind="projectile") == 10


@pytest.mark.parametrize("source", ["projectile", "melee", "area", "hazard"])
def test_every_enemy_death_source_counts_once(game_room, source):
    p, _ = arena(game_room)
    target = enemy(game_room, hp=1)
    assert game_room.damage(target, 10, p, kind=source) == 1
    assert target["dead"] and p["kills"] == 1
    assert game_room.damage(target, 10, p, kind=source) == 0
    assert p["kills"] == 1


def test_actual_shockwave_allied_optin_and_kills(game_room):
    p, ally = arena(game_room, ("vanguard", "medic"))
    p.update(x=0, y=0)
    ally.update(x=1, y=0, hp=10)
    target = enemy(game_room, x=2, hp=10)
    assert game_room.trigger_skill("p0")
    assert target["hp"] == 0 and p["kills"] == 1 and ally["hp"] == 10
    p["skill_cooldown"] = 0
    ff(game_room)
    assert game_room.trigger_skill("p0")
    assert ally["downed"] and p["kills"] == 2


def test_actual_enemy_melee_and_bullets_use_shared_down_kills(game_room):
    p, _ = arena(game_room)
    p.update(x=0, y=0, hp=1)
    attacker = enemy(game_room, x=0.7)
    attacker["attack_timer"] = 0
    game_room.step(DT)
    assert p["downed"] and p["body_dropped"] and attacker["kills"] == 1
    assert game_room.heal(p, 0, game_room.players["p1"], revive_hp=1)
    attacker["attack_timer"] = 100
    shot(game_room, attacker, x=-1)
    game_room.step(DT)
    assert p["downed"] and attacker["kills"] == 2
    assert p["last_damage"]["owner_id"] == "e"


def test_turret_targets_hostiles_but_allied_intercepts_and_owner_credit(game_room):
    p, ally = arena(game_room, ("tech", "medic"))
    p.update(x=0, y=0)
    ally.update(x=1.5, y=0, hp=1)
    target = enemy(game_room, x=4, hp=1)
    assert game_room.trigger_skill("p0")
    ff(game_room)
    for _ in range(3):
        game_room.step(DT)
    assert ally["downed"] and p["kills"] == 1 and target["hp"] == 1
    for _ in range(15):
        game_room.step(DT)
    assert target["hp"] == 0 and p["kills"] == 2


@pytest.mark.parametrize("method", ["manual", "immediate_field", "tick_field"])
def test_down_revive_recover_down_creates_two_distinct_durable_chests(game_room, method):
    medic, p = arena(game_room, ("medic", "recon"))
    medic.update(x=0, y=0)
    p.update(x=1, y=0)
    item = make_item("rail_carbine")
    p["inventory"]["storage"].append(item)
    game_room.shared_stash.commit_player({"id": p["id"]}, p)
    assert game_room.damage(p, 1000, None) == p["max_hp"]
    game_room.step(DT)
    first = game_room.map_data["crates"][0]
    assert game_room.shared_stash.holdings[item["id"]]["owner"] == "world"
    if method == "manual":
        assert game_room.handle_interact("p0")["type"] == "revived"
    else:
        if method == "tick_field":
            p["x"] = 10  # Outside immediate field, enter for subsequent tick.
        assert game_room.trigger_skill("p0")
        p["x"] = 1
        game_room.step(DT)
    assert not p["downed"] and not p["body_dropped"]
    assert not game_room.trigger_reload("p1")
    assert not game_room.get_snapshot()["players"][1]["capabilities"]["can_fire"]
    assert game_room.take_crate_item("p1", first["id"], item["id"])["ok"]
    assert game_room.shared_stash.holdings[item["id"]]["owner"] == "player:p1"
    assert game_room.damage(p, 1000, None)
    # A field may revive on the next tick, but must first settle the second death.
    game_room.step(DT)
    chests = game_room.map_data["crates"]
    assert len(chests) == 2 and chests[0]["id"] != chests[1]["id"]
    assert not first["items"] and [it["id"] for it in chests[1]["items"]] == [item["id"]]
    assert not inventory_items(p)
    assert game_room.shared_stash.holdings[item["id"]]["owner"] == "world"


@pytest.mark.parametrize("reviver", ["manual", "field", "step"])
def test_body_failure_prevents_revival_then_retries_ledger_once(game_room, reviver):
    medic, p = arena(game_room, ("medic", "recon"))
    medic.update(x=0, y=0)
    p.update(x=1, y=0)
    item = make_item("rail_carbine")
    p["inventory"]["storage"].append(item)
    game_room.shared_stash.commit_player({"id": p["id"]}, p)
    original_count = game_room.shared_stash.stacks.get("rail_carbine", 0)
    assert game_room.damage(p, 1000)
    disk = game_room.shared_stash.path.read_bytes()
    def revive():
        if reviver == "manual":
            return game_room.handle_interact("p0")
        if reviver == "field":
            return game_room.trigger_skill("p0")
        return game_room.step(DT)
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=SaveError("disk failure")):
        with pytest.raises(SaveError):
            revive()
    assert p["downed"] and not p["body_dropped"] and inventory_items(p)
    assert not game_room.map_data["crates"] and game_room.shared_stash.path.read_bytes() == disk
    assert game_room.shared_stash.holdings[item["id"]]["owner"] == "player:p1"
    if reviver == "field":
        assert medic["skill_cooldown"] == 0 and not game_room.deployables
    with patch("backend.app.engine.inventory.write_snapshot", wraps=write_snapshot) as writes:
        revive()
        assert writes.call_count == 1
    assert len(game_room.map_data["crates"]) == 1
    assert not inventory_items(p)
    recovered = SharedStash(game_room.shared_stash.path)
    assert recovered.stacks["rail_carbine"] == original_count + 1
    assert SharedStash(game_room.shared_stash.path).stacks == recovered.stacks


def test_beneficial_healing_separate_from_ff_hostile_neutral_immunity(game_room):
    p, ally = arena(game_room)
    ff(game_room)
    ally.update(hp=1, invulnerable=True)
    target = enemy(game_room, hp=10)
    assert game_room.heal(ally, 5, p) and ally["hp"] == 6
    assert game_room.heal(p, 5, p)
    assert not game_room.heal(target, 5, p)
    neutral_rules = mutable_copy(game_room.combat_rules)
    neutral_rules["factions"]["squad"]["swarm"] = "neutral"
    assert not can_target(p, target, rules=neutral_rules)
    assert not can_target(p, target, rules=neutral_rules, beneficial=True)


def test_sonar_emitter_position_radius_duration_no_global_reveal(game_room):
    p, _ = arena(game_room, ("recon", "medic"))
    p.update(x=0, y=0)
    near = enemy(game_room, "near", x=18)
    outside = enemy(game_room, "outside", x=18.001)
    far = enemy(game_room, "far", x=100)
    assert game_room.trigger_skill("p0")
    emitter = game_room.deployables[0]
    def visible():
        return {e["id"]: e["visible"] for e in game_room.get_snapshot("p0")["enemies"]}
    assert visible() == {"near": True, "outside": False, "far": False}
    p["x"] = -100  # Emitter remains at original position, not current owner location.
    assert visible()["near"]
    game_room.deployables.append({"id": "second", "type": "sonar_pulse", "x": 100, "y": 0, "radius": 1, "ttl": 0.02})
    assert visible()["far"]
    game_room.step(DT)
    assert not visible()["far"]
    game_room.step(emitter["ttl"])
    assert not any(visible().values()) and not game_room.get_snapshot()["sonar_active"]


def test_edited_catalog_sonar_and_authoritative_client_capabilities(game_room, monkeypatch):
    data = json.loads(CONTENT_PATH.read_text())
    data["effects"]["sonar_pulse"].update(radius=24.0, duration=0.05)
    edited = validate_content(data)
    monkeypatch.setattr("backend.app.engine.game_loop.CHARACTER_CLASSES", edited.classes)
    p, _ = arena(game_room, ("recon", "medic"))
    p.update(x=0, y=0)
    enemy(game_room, x=23)
    assert game_room.trigger_skill("p0")
    assert game_room.get_snapshot()["enemies"][0]["visible"]
    assert game_room.deployables[0]["ttl"] == 0.05
    snapshot_p = game_room.get_snapshot()["players"][0]
    assert snapshot_p["sight_range"] == edited.classes["recon"]["sight_range"]
    assert snapshot_p["capabilities"] == {"can_fire": True, "can_reload": False, "can_fire_while_running": False,
                                          "can_move": True, "can_interact": True, "combat": True}
    game_room.step(0.05)
    assert not game_room.get_snapshot()["enemies"][0]["visible"]
    for slot in ("primary_weapon", "secondary_weapon"):
        p["inventory"]["equipped"][slot] = None
    assert not game_room.get_snapshot()["players"][0]["capabilities"]["can_fire"]


@pytest.mark.parametrize("change,error", [
    (lambda d: d["rules"]["combat"].update(friendly_fire=1), "rules.combat.friendly_fire"),
    (lambda d: d["rules"]["combat"].update(self_damage="yes"), "rules.combat.self_damage"),
    (lambda d: d["classes"]["recon"].update(faction="missing"), "classes.recon.faction"),
    (lambda d: d["rules"]["combat"]["factions"]["squad"].update(swarm="everything"), "rules.combat.factions"),
    (lambda d: d["rules"]["combat"]["factions"]["squad"].pop("swarm"), "all faction relations required"),
    (lambda d: d["rules"]["combat"]["factions"]["squad"].update(swarm="neutral"), "relations must be symmetric"),
])
def test_combat_catalog_strict_stable_refs(change, error):
    data = json.loads(CONTENT_PATH.read_text())
    change(data)
    with pytest.raises(ValueError, match=error):
        validate_content(data)


def test_data_config_default_off_and_optin_is_immutable_public(game_room):
    data = json.loads(CONTENT_PATH.read_text())
    assert not data["rules"]["combat"]["friendly_fire"]
    data["rules"]["combat"].update(friendly_fire=True, self_damage=True)
    edited = validate_content(data)
    game_room.combat_rules = edited.definitions["rules"]["combat"]
    p, ally = arena(game_room)
    assert game_room.damage(ally, 5, p) == 5
    assert game_room.damage(p, 5, p) == 5
    assert edited.client_view()["rules"]["combat"]["friendly_fire"]
    with pytest.raises(TypeError):
        edited.definitions["rules"]["combat"]["friendly_fire"] = False


def test_actual_rail_weapon_30hz_hits_between_muzzle_endpoints(game_room):
    p, _ = arena(game_room)
    weapon = make_item("rail_carbine")
    weapon["weapon"]["spread"] = 0  # Controlled aim, not an artificial slow projectile.
    p["inventory"]["equipped"]["primary_weapon"] = weapon
    p.update(x=0, y=0)
    p["input"].update(aim_x=10, aim_y=0, firing=True)
    radius = CATALOG.definitions["enemies"]["stalker"]["radius"]
    muzzle = CATALOG.definitions["rules"]["player"]["projectile_offset"]
    target = enemy(game_room, x=muzzle + 55 * DT / 2, radius=radius, hp=1)
    game_room.map_data["walls"] = [{"id": "behind", "x": muzzle + 55 * DT, "y": -1, "w": 0.1, "h": 2}]
    game_room.step(DT)
    assert weapon["weapon"]["projectile_speed"] == 55
    assert target["hp"] == 0 and p["kills"] == 1 and p["ammo"] == weapon["weapon"]["mag_size"] - 1


def test_enemy_allied_optin_projectile_and_neutral_not_colliders(game_room):
    arena(game_room)
    source = enemy(game_room, "source", x=-10)
    intercept = enemy(game_room, "intercept", x=0.8)
    shot(game_room, source)
    game_room.step(DT)
    assert intercept["hp"] == 45
    game_room.projectiles.clear()
    ff(game_room)
    shot(game_room, source)
    game_room.step(DT)
    assert intercept["hp"] == 35
    game_room.combat_rules["factions"]["swarm"]["swarm"] = "neutral"
    shot(game_room, source)
    game_room.step(DT)
    assert intercept["hp"] == 35


def test_field_heals_explicit_allied_actor_not_hostile_even_with_ff(game_room):
    p, _ = arena(game_room, ("medic", "recon"))
    p.update(x=0, y=0)
    friend = enemy(game_room, "friend", x=1, hp=10, faction="squad")
    friend["max_hp"] = 45
    hostile = enemy(game_room, "hostile", x=2, hp=10)
    hostile["max_hp"] = 45
    ff(game_room)
    assert game_room.trigger_skill("p0")
    game_room.step(DT)
    assert friend["hp"] == 18 and hostile["hp"] == 10


def test_expired_deployables_and_zero_range_stationary_shots_do_nothing(game_room):
    p, _ = arena(game_room, ("medic", "recon"))
    p.update(x=0, y=0, hp=1)
    assert game_room.trigger_skill("p0")
    game_room.deployables[0]["ttl"] = 0
    target = enemy(game_room, x=0)
    shot(game_room, vx=0, range_remaining=0)
    game_room.step(DT)
    assert p["hp"] == 1 and target["hp"] == 45
    assert not game_room.deployables and not game_room.projectiles


def test_owner_selfhit_and_unowned_hazard_immunity_and_attribution(game_room):
    p, _ = arena(game_room)
    ff(game_room, self_damage=True)
    p["damage_immunities"] = ["hazard"]
    assert game_room.damage(p, 5, p) == 0
    p["damage_immunities"] = []
    p["hp"] = 1
    assert game_room.damage(p, 100, p) == 1
    assert p["kills"] == 1 and p["last_damage"]["owner_id"] == "p0"
    assert game_room.heal(p, 0, game_room.players["p1"], revive_hp=1)
    assert game_room.damage(p, 100, None) == 1
    assert p["kills"] == 1 and p["last_damage"] == {"owner_id": None, "kind": "hazard"}


def test_sonar_beacon_item_uses_authored_emitter_and_consumes_durable_claim(game_room):
    p, _ = arena(game_room)
    p.update(x=0, y=0)
    target = enemy(game_room, x=18.001)
    item = make_item("sonar_beacon")
    p["inventory"]["storage"].append(item)
    game_room.shared_stash.commit_player({"id": "p0"}, p)
    assert game_room.handle_inventory_action("p0", {"action": "use_item", "item_id": item["id"]})["ok"]
    effect = CATALOG.items["sonar_beacon"]["effect"]
    dep = game_room.deployables[0]
    assert (dep["radius"], dep["ttl"]) == (effect["radius"], effect["duration"])
    assert item["id"] not in game_room.shared_stash.holdings
    assert not game_room.get_snapshot()["enemies"][0]["visible"]
    target["x"] = dep["radius"]
    assert game_room.get_snapshot()["enemies"][0]["visible"]


def test_lethal_actual_projectile_body_write_failure_retries_no_duplicate_hit_or_kill(game_room):
    p, ally = arena(game_room)
    ally.update(x=0.8, y=0, hp=1)
    item = make_item("medkit")
    ally["inventory"]["storage"].append(item)
    game_room.shared_stash.commit_player({"id": "p1"}, ally)
    ff(game_room)
    shot(game_room)
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=SaveError("body failure")):
        with pytest.raises(SaveError):
            game_room.step(DT)
    assert ally["downed"] and not ally["body_dropped"] and p["kills"] == 1
    assert not game_room.projectiles and not game_room.map_data["crates"]
    assert game_room.shared_stash.holdings[item["id"]]["owner"] == "player:p1"
    assert game_room.handle_interact("p0") is None  # Out of revival range.
    p.update(x=0, y=0)
    assert game_room.handle_interact("p0")["type"] == "revived"
    game_room.step(DT)
    assert p["kills"] == 1 and len(game_room.map_data["crates"]) == 1
    assert game_room.shared_stash.holdings[item["id"]]["owner"] == "world"


def test_active_nano_field_cannot_skip_failed_body_settlement_on_tick(game_room):
    medic, ally = arena(game_room, ("medic", "recon"))
    medic.update(x=0, y=0)
    ally.update(x=1, y=0)
    assert game_room.trigger_skill("p0")
    item = make_item("medkit")
    ally["inventory"]["storage"].append(item)
    game_room.shared_stash.commit_player({"id": "p1"}, ally)
    game_room.damage(ally, 1000)
    dep = game_room.deployables[0]
    with patch("backend.app.engine.inventory.write_snapshot", side_effect=SaveError("field body failure")):
        with pytest.raises(SaveError):
            game_room.step(DT)
    assert dep["ttl"] == dep["effect"]["duration"]  # Retry begins before clock/effect advancement.
    assert ally["downed"] and not ally["body_dropped"] and inventory_items(ally)
    game_room.step(DT)
    assert not ally["downed"] and not ally["body_dropped"]
    assert ally["hp"] == dep["effect"]["tick_revive_hp"]
    assert len(game_room.map_data["crates"]) == 1
    assert game_room.shared_stash.holdings[item["id"]]["owner"] == "world"


def test_snapshot_capabilities_are_pure_and_unknown_faction_not_everything_hostile(game_room):
    p, _ = arena(game_room)
    target = enemy(game_room, faction="unknown")
    assert not game_room.damage(target, 5, p)
    before = copy.deepcopy(p)
    snapshot = game_room.get_snapshot()
    assert p == before and snapshot["enemies"][0]["faction"] == "unknown"
    assert snapshot["players"][0]["capabilities"]["can_fire"]
    p["fire_cooldown"] = 1
    assert not game_room.get_snapshot()["players"][0]["capabilities"]["can_fire"]
    p.update(fire_cooldown=0, ammo=0)
    capabilities = game_room.get_snapshot()["players"][0]["capabilities"]
    assert not capabilities["can_fire"] and capabilities["can_reload"]
