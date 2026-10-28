"""Small shared combat rules for dictionary actors and attributed effects."""

from backend.app.content import CATALOG, RULES


def faction(actor):
    if "faction" in actor:
        return actor["faction"]
    # Retained maps have expanded actors without a faction field.
    if actor.get("class_id") in CATALOG.classes:
        return CATALOG.classes[actor["class_id"]]["faction"]
    return CATALOG.definitions["enemies"].get(actor.get("type"), {}).get("faction")


def allied(source, target, rules=RULES["combat"]):
    return rules["factions"].get(faction(source), {}).get(faction(target)) == "ally"


def can_target(source, target, *, rules=RULES["combat"], beneficial=False, hostile_only=False):
    if target.get("dead") or (not beneficial and (target.get("hp", 0) <= 0 or target.get("downed"))):
        return False
    if source is None:
        return not beneficial  # Unowned environmental hazards have no allegiance.
    relation = rules["factions"].get(faction(source), {}).get(faction(target))
    if beneficial:
        return relation == "ally"
    if hostile_only:
        return relation == "hostile"
    owner_id = source.get("owner_id", source.get("id"))
    if owner_id == target.get("id"):
        return rules["self_damage"]
    return relation == "hostile" or (relation == "ally" and rules["friendly_fire"])


def apply_damage(target, amount, source=None, *, kind="hazard", owner=None, rules=RULES["combat"]):
    """One lethal transition per life; downed bodies are not damageable colliders."""
    if not can_target(source, target, rules=rules):
        return 0
    if target.get("invulnerable") or kind in target.get("damage_immunities", ()):
        return 0
    damage = max(0, int(amount))
    shield = CATALOG.definitions["effects"].get(target.get("skill_active"), {})
    if shield.get("behavior") == "shockwave_shield":
        if kind == "projectile":
            damage = 0
        elif kind == "melee" and damage:
            damage = max(shield["melee_minimum"], int(damage * shield["melee_multiplier"]))
    elif kind == "melee" and damage:
        passive = CATALOG.classes.get(target.get("class_id"), {}).get("passive", {})
        if passive.get("id") == "bulwark":
            damage = max(RULES["player"]["vanguard_melee_minimum"], int(damage * RULES["player"]["vanguard_melee_multiplier"]))
    dealt = min(target["hp"], damage)
    target["hp"] -= dealt
    if not dealt:
        return 0
    attribution = owner
    if attribution is None and source is not None and "owner_id" not in source:
        attribution = source
    target["last_damage"] = {"owner_id": source.get("owner_id", source.get("id")) if source else None, "kind": kind}
    if target.get("state") == "patrol":
        target.update(state="alerted", alert_timer=RULES["ai"]["hearing_alert_duration"])
        if source is not None and "x" in source and "y" in source:
            target["investigate_target"] = (source["x"], source["y"])
    if target["hp"] == 0:
        if "downed" in target:
            target.update(downed=True, vx=0.0, vy=0.0, running=False)
        else:
            target["dead"] = True
        if attribution is not None:
            attribution["kills"] = attribution.get("kills", 0) + 1
    return dealt
