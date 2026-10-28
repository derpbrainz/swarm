"""Supported attacks and monotonic data-defined encounter phases, not a boss engine."""

import math
import uuid

from backend.app.content import CATALOG, RULES
from backend.app.engine.combat import faction


def melee(enemy, target, profile, projectiles, damage):
    damage(target, profile["damage"], enemy, kind="melee")


def projectile(enemy, target, profile, projectiles, damage):
    angle = enemy["angle"]
    speed = profile["projectile_speed"]
    projectiles.append({"id": uuid.uuid4().hex[:8], "owner_id": enemy["id"], "faction": faction(enemy),
                        "x": enemy["x"] + math.cos(angle) * RULES["ai"]["projectile_offset"],
                        "y": enemy["y"] + math.sin(angle) * RULES["ai"]["projectile_offset"],
                        "vx": math.cos(angle) * speed, "vy": math.sin(angle) * speed,
                        "damage": profile["damage"], "ttl": RULES["ai"]["projectile_ttl"],
                        "color": RULES["ai"]["projectile_color"], "radius": RULES["ai"]["projectile_radius"]})


HANDLERS = {"melee": melee, "projectile": projectile}


def profiles(enemy, config, *, catalog=CATALOG):
    ids = config["attacks"]
    if config.get("encounter_id"):
        phases = catalog.definitions["encounters"][config["encounter_id"]]["phases"]
        current = enemy.get("phase_index", 0)
        ratio = enemy["hp"] / enemy["max_hp"]
        for index, phase in enumerate(phases):
            if ratio <= phase["health_at_most"]:
                current = max(current, index)
        enemy["phase_index"] = current
        enemy["encounter_phase"] = phases[current]["id"] if enemy["hp"] > 0 else "dead"
        ids = phases[current]["attacks"]
    enemy["attack_profiles"] = list(ids)
    return [(identity, catalog.definitions["attacks"][identity]) for identity in ids]


def perform(enemy, target, available, distance, projectiles, damage, emit_sound):
    eligible = [(identity, profile) for identity, profile in available if distance <= profile["range"]]
    if not eligible:
        return False
    # Declared order is priority (close melee before ranged); one shared cooldown.
    if enemy["attack_timer"] <= 0:
        _, profile = eligible[0]
        enemy["attack_timer"] = profile["cooldown"]
        if emit_sound:
            emit_sound(enemy["x"], enemy["y"], 0, "alien_attack", sound_type=RULES["audio"]["alien_attack_sound"], source_id=enemy["id"])
        HANDLERS[profile["handler"]](enemy, target, profile, projectiles, damage)
    return True
