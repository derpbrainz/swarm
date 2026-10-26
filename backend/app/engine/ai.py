"""
Enemy sensory perception (sight & hearing) and state-machine AI.
"""

from __future__ import annotations

import math
import uuid
from backend.app.config.classes import ENEMY_TYPES
from backend.app.engine.physics import move_with_collisions, has_line_of_sight


def _angle_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


def update_enemies(
    enemies: list[dict],
    players: dict[str, dict],
    walls: list[dict],
    sound_events: list[dict],
    projectiles: list[dict],
    dt: float,
) -> None:
    """
    Updates enemy perception (hearing sound events, checking sight cones & LoS)
    and executes patrol / investigation / combat actions.
    """
    living_players = [
        p for p in players.values() if p.get("hp", 0) > 0 and not p.get("downed", False)
    ]

    for enemy in enemies:
        if enemy.get("hp", 0) <= 0:
            continue

        e_cfg = ENEMY_TYPES.get(enemy["type"], ENEMY_TYPES["stalker"])
        ex = float(enemy["x"])
        ey = float(enemy["y"])

        if enemy.get("attack_timer", 0.0) > 0.0:
            enemy["attack_timer"] = max(0.0, enemy["attack_timer"] - dt)

        # 1. Hearing check against recent sound pulses
        hearing_mult = float(e_cfg.get("hearing_multiplier", 1.0))
        for snd in sound_events:
            dist_snd = math.hypot(snd["x"] - ex, snd["y"] - ey)
            if dist_snd <= float(snd["radius"]) * hearing_mult:
                if enemy["state"] == "patrol":
                    enemy["state"] = "alerted"
                    enemy["investigate_target"] = (float(snd["x"]), float(snd["y"]))
                    enemy["alert_timer"] = 6.0
                elif enemy["state"] == "alerted":
                    enemy["investigate_target"] = (float(snd["x"]), float(snd["y"]))
                    enemy["alert_timer"] = max(float(enemy.get("alert_timer", 0.0)), 6.0)

        # 2. Sight check (vision cone + proximity + wall raycast)
        best_player = None
        best_dist = float("inf")
        sight_range = float(e_cfg["sight_range"])
        half_fov = float(e_cfg["sight_angle"]) * 0.5

        for p in living_players:
            px, py = float(p["x"]), float(p["y"])
            dist = math.hypot(px - ex, py - ey)
            if dist > sight_range:
                continue

            ang_to_p = math.atan2(py - ey, px - ex)
            in_cone = abs(_angle_diff(float(enemy["angle"]), ang_to_p)) <= half_fov
            in_proximity = dist <= 2.2

            if (in_cone or in_proximity) and has_line_of_sight(ex, ey, px, py, walls):
                if dist < best_dist:
                    best_dist = dist
                    best_player = p

        if best_player is not None:
            enemy["state"] = "engaged"
            enemy["target_player_id"] = best_player["id"]
            enemy["investigate_target"] = (float(best_player["x"]), float(best_player["y"]))
            enemy["alert_timer"] = 8.0
            # Alert nearby squadmates within 4.5 units
            for ally in enemies:
                if ally["id"] != enemy["id"] and ally.get("hp", 0) > 0 and ally["state"] == "patrol":
                    if math.hypot(ally["x"] - ex, ally["y"] - ey) <= 4.5:
                        ally["state"] = "alerted"
                        ally["investigate_target"] = (float(best_player["x"]), float(best_player["y"]))
                        ally["alert_timer"] = 5.0

        # 3. State execution
        state = enemy["state"]
        if state == "engaged":
            if best_player is not None:
                tx, ty = float(best_player["x"]), float(best_player["y"])
                dist = best_dist
                enemy["angle"] = math.atan2(ty - ey, tx - ex)
                attack_range = float(e_cfg["attack_range"])

                if dist <= attack_range:
                    # Perform attack
                    if enemy["attack_timer"] <= 0.0:
                        enemy["attack_timer"] = float(e_cfg["attack_cooldown"])
                        proj_speed = float(e_cfg.get("projectile_speed", 0.0))
                        dmg = int(e_cfg["attack_damage"])
                        if proj_speed <= 0.0:
                            # Melee strike (check Vanguard shield reduction)
                            if best_player.get("skill_active") == "shockwave_shield":
                                dmg = max(2, int(dmg * 0.25))
                            elif best_player.get("class_id") == "vanguard":
                                dmg = max(1, int(dmg * 0.8))
                            best_player["hp"] = max(0, best_player["hp"] - dmg)
                            if best_player["hp"] == 0:
                                best_player["downed"] = True
                        else:
                            # Ranged acid/plasma projectile
                            ang = enemy["angle"]
                            projectiles.append(
                                {
                                    "id": uuid.uuid4().hex[:8],
                                    "owner_id": enemy["id"],
                                    "team": "enemy",
                                    "x": ex + math.cos(ang) * 0.6,
                                    "y": ey + math.sin(ang) * 0.6,
                                    "vx": math.cos(ang) * proj_speed,
                                    "vy": math.sin(ang) * proj_speed,
                                    "damage": dmg,
                                    "ttl": 1.6,
                                    "color": "#2ECC71",
                                    "radius": 0.22,
                                }
                            )
                else:
                    # Chase player
                    spd = float(e_cfg["speed"])
                    vx = math.cos(enemy["angle"]) * spd
                    vy = math.sin(enemy["angle"]) * spd
                    nx, ny = move_with_collisions(ex, ey, vx, vy, dt, float(enemy["radius"]), walls)
                    enemy["x"], enemy["y"] = nx, ny
            else:
                # Lost sight of player: move toward last known position
                enemy["alert_timer"] = float(enemy.get("alert_timer", 0.0)) - dt
                if enemy["alert_timer"] <= 0.0 or not enemy.get("investigate_target"):
                    enemy["state"] = "patrol"
                    enemy["target_player_id"] = None
                else:
                    tx, ty = enemy["investigate_target"]
                    dx, dy = tx - ex, ty - ey
                    dist = math.hypot(dx, dy)
                    if dist > 0.4:
                        ang = math.atan2(dy, dx)
                        enemy["angle"] = ang
                        spd = float(e_cfg["speed"]) * 0.85
                        nx, ny = move_with_collisions(
                            ex, ey, math.cos(ang) * spd, math.sin(ang) * spd, dt, float(enemy["radius"]), walls
                        )
                        enemy["x"], enemy["y"] = nx, ny
                    else:
                        enemy["state"] = "alerted"

        elif state == "alerted":
            enemy["alert_timer"] = float(enemy.get("alert_timer", 0.0)) - dt
            target = enemy.get("investigate_target")
            if enemy["alert_timer"] <= 0.0 or not target:
                enemy["state"] = "patrol"
                enemy["investigate_target"] = None
            else:
                tx, ty = target
                dx, dy = tx - ex, ty - ey
                dist = math.hypot(dx, dy)
                if dist > 0.45:
                    ang = math.atan2(dy, dx)
                    enemy["angle"] = ang
                    spd = float(e_cfg["speed"]) * 0.75
                    nx, ny = move_with_collisions(
                        ex, ey, math.cos(ang) * spd, math.sin(ang) * spd, dt, float(enemy["radius"]), walls
                    )
                    enemy["x"], enemy["y"] = nx, ny

        else:
            # Patrol between waypoints
            waypoints = enemy.get("waypoints") or [(ex, ey)]
            wp_idx = int(enemy.get("waypoint_index", 0)) % len(waypoints)
            tx, ty = waypoints[wp_idx]
            dx, dy = tx - ex, ty - ey
            dist = math.hypot(dx, dy)
            if dist <= 0.35:
                enemy["waypoint_index"] = (wp_idx + 1) % len(waypoints)
            else:
                ang = math.atan2(dy, dx)
                enemy["angle"] = ang
                spd = float(e_cfg["patrol_speed"])
                nx, ny = move_with_collisions(
                    ex, ey, math.cos(ang) * spd, math.sin(ang) * spd, dt, float(enemy["radius"]), walls
                )
                enemy["x"], enemy["y"] = nx, ny
