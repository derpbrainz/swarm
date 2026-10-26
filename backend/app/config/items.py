"""
backend/app/config/items.py

Defines item templates, carry capacity, minimum loadouts, and item instancing helpers.
"""

import uuid

CLASS_CARRY_CAPACITY = {"vanguard": 24, "tech": 18, "medic": 15, "recon": 12}

ITEM_TEMPLATES = {
    "breaching_shotgun": {"id": "breaching_shotgun", "name": "Breaching Shotgun", "category": "primary_weapon", "weight": 6, "description": "Close-range shotgun with high burst damage.", "weapon": {"damage": 14, "fire_rate": 1.2, "projectile_speed": 28.0, "range": 10.0, "pellets": 5, "spread": 0.22, "sound_radius": 14.0, "mag_size": 6, "reload_time": 2.2, "color": "#E74C3C"}},
    "compact_smg": {"id": "compact_smg", "name": "Compact SMG", "category": "primary_weapon", "weight": 4, "description": "Fast-firing compact submachine gun.", "weapon": {"damage": 8, "fire_rate": 4.0, "projectile_speed": 34.0, "range": 16.0, "pellets": 1, "spread": 0.04, "sound_radius": 10.0, "mag_size": 30, "reload_time": 2.5, "color": "#3498DB"}},
    "combat_carbine": {"id": "combat_carbine", "name": "Combat Carbine", "category": "primary_weapon", "weight": 5, "description": "Balanced carbine for sustained fire.", "weapon": {"damage": 10, "fire_rate": 3.0, "projectile_speed": 38.0, "range": 18.0, "pellets": 1, "spread": 0.03, "sound_radius": 10.0, "mag_size": 20, "reload_time": 2.0, "color": "#2ECC71"}},
    "suppressed_rifle": {"id": "suppressed_rifle", "name": "Suppressed Rifle", "category": "primary_weapon", "weight": 5, "description": "Quiet precision rifle for scouting.", "weapon": {"damage": 18, "fire_rate": 1.5, "projectile_speed": 45.0, "range": 24.0, "pellets": 1, "spread": 0.01, "sound_radius": 4.5, "mag_size": 10, "reload_time": 2.8, "color": "#9B59B6"}},
    "plasma_lmg": {"id": "plasma_lmg", "name": "Plasma LMG", "category": "primary_weapon", "weight": 8, "description": "Heavy plasma machine gun with sustained output.", "weapon": {"damage": 12, "fire_rate": 5.0, "projectile_speed": 30.0, "range": 20.0, "pellets": 1, "spread": 0.06, "sound_radius": 12.0, "mag_size": 40, "reload_time": 3.5, "color": "#F39C12"}},
    "rail_carbine": {"id": "rail_carbine", "name": "Rail Carbine", "category": "primary_weapon", "weight": 6, "description": "High-velocity rail weapon for long-range shots.", "weapon": {"damage": 22, "fire_rate": 1.0, "projectile_speed": 55.0, "range": 28.0, "pellets": 1, "spread": 0.005, "sound_radius": 6.0, "mag_size": 8, "reload_time": 3.0, "color": "#1ABC9C"}},
    "service_pistol": {"id": "service_pistol", "name": "Service Pistol", "category": "secondary_weapon", "weight": 2, "description": "Reliable backup sidearm.", "weapon": {"damage": 7, "fire_rate": 2.0, "projectile_speed": 32.0, "range": 12.0, "pellets": 1, "spread": 0.05, "sound_radius": 8.0, "mag_size": 12, "reload_time": 1.8, "color": "#BDC3C7"}},
    "machine_pistol": {"id": "machine_pistol", "name": "Machine Pistol", "category": "secondary_weapon", "weight": 3, "description": "Rapid-fire compact pistol.", "weapon": {"damage": 6, "fire_rate": 5.0, "projectile_speed": 30.0, "range": 10.0, "pellets": 1, "spread": 0.07, "sound_radius": 9.0, "mag_size": 22, "reload_time": 2.0, "color": "#95A5A6"}},
    "flare_revolver": {"id": "flare_revolver", "name": "Flare Revolver", "category": "secondary_weapon", "weight": 3, "description": "High-damage revolver with flare rounds.", "weapon": {"damage": 16, "fire_rate": 1.0, "projectile_speed": 26.0, "range": 14.0, "pellets": 1, "spread": 0.03, "sound_radius": 11.0, "mag_size": 6, "reload_time": 2.4, "color": "#E67E22"}},
    "shockwave_shield": {"id": "shockwave_shield", "name": "Shockwave Shield", "category": "special_ability", "weight": 3, "description": "Projects a concussive barrier that blocks projectiles and pushes enemies back.", "effect": {"cooldown": 18.0, "duration": 4.0, "push_radius": 6.0, "block_projectiles": True}},
    "auto_turret": {"id": "auto_turret", "name": "Auto Turret", "category": "special_ability", "weight": 4, "description": "Deploys a short-lived turret that automatically targets nearby enemies.", "effect": {"cooldown": 22.0, "duration": 8.0, "range": 10.0, "damage": 6, "fire_rate": 2.5}},
    "nano_field": {"id": "nano_field", "name": "Nano Field", "category": "special_ability", "weight": 3, "description": "Creates a healing field that restores health to allies inside it.", "effect": {"cooldown": 20.0, "duration": 6.0, "radius": 5.0, "heal_per_second": 8.0}},
    "sonar_pulse": {"id": "sonar_pulse", "name": "Sonar Pulse", "category": "special_ability", "weight": 2, "description": "Emits a quiet pulse that reveals enemies in a wide radius.", "effect": {"cooldown": 16.0, "duration": 3.0, "radius": 18.0, "reveal_enemies": True}},
    "medkit": {"id": "medkit", "name": "Medkit", "category": "quick_use", "weight": 2, "description": "Consumable medical kit that restores health.", "value": {"heal": 45}, "consumable": True},
    "stim_injector": {"id": "stim_injector", "name": "Stim Injector", "category": "quick_use", "weight": 1, "description": "Quick stimulant that restores a small amount of health.", "value": {"heal": 25}, "consumable": True},
    "tactical_light": {"id": "tactical_light", "name": "Tactical Light", "category": "quick_use", "weight": 2, "description": "Mounted light that improves vision range.", "value": {"sight_boost": 4.5}, "consumable": False},
    "portable_turret": {"id": "portable_turret", "name": "Portable Turret", "category": "quick_use", "weight": 4, "description": "Deployable turret that targets nearby enemies.", "value": {"deploy_turret": True}, "consumable": True},
    "sonar_beacon": {"id": "sonar_beacon", "name": "Sonar Beacon", "category": "quick_use", "weight": 2, "description": "Deployable beacon that reveals enemies in a radius.", "value": {"deploy_sonar": True}, "consumable": True},
    "ammo_pack": {"id": "ammo_pack", "name": "Ammo Pack", "category": "storage", "weight": 2, "description": "Standard ammunition pack.", "value": {"ammo": 30}, "consumable": False},
    "heavy_ammo": {"id": "heavy_ammo", "name": "Heavy Ammo", "category": "storage", "weight": 4, "description": "Heavy ammunition pack for larger weapons.", "value": {"ammo": 60}, "consumable": False},
    "credits": {"id": "credits", "name": "Credits", "category": "storage", "weight": 2, "description": "Stored currency loot.", "value": {"loot": 150}, "consumable": False},
    "isotope_cell": {"id": "isotope_cell", "name": "Isotope Cell", "category": "storage", "weight": 4, "description": "Valuable energy cell loot.", "value": {"loot": 300}, "consumable": False},
}

CLASS_MIN_LOADOUT = {
    "vanguard": {"primary_weapon": "breaching_shotgun", "secondary_weapon": "service_pistol", "special_ability": "shockwave_shield", "quick_use": "medkit", "storage": "ammo_pack"},
    "tech": {"primary_weapon": "compact_smg", "secondary_weapon": "machine_pistol", "special_ability": "auto_turret", "quick_use": "portable_turret", "storage": "ammo_pack"},
    "medic": {"primary_weapon": "combat_carbine", "secondary_weapon": "service_pistol", "special_ability": "nano_field", "quick_use": "medkit", "storage": "ammo_pack"},
    "recon": {"primary_weapon": "suppressed_rifle", "secondary_weapon": "flare_revolver", "special_ability": "sonar_pulse", "quick_use": "sonar_beacon", "storage": "ammo_pack"},
}

def make_item(item_type: str, item_id: str | None = None) -> dict:
    template = ITEM_TEMPLATES.get(item_type, ITEM_TEMPLATES["credits"])
    item = dict(template)
    item["id"] = item_id or uuid.uuid4().hex[:8]
    item["type"] = template["id"]
    item["template_id"] = template["id"]
    item["item_type"] = template["id"]
    return item