"""
Mission definitions and modular map segment templates.
"""

MISSIONS = {
    "outpost": {
        "id": "outpost",
        "name": "Derelict Outpost",
        "description": "Board the dark orbital station, restore power to the security bulkheads, and extract.",
        "difficulty": "Standard",
        "segment_sequence": [
            "drop_hangar",
            "security_corridor",
            "command_hub",
            "extraction_pad",
        ],
        "enemy_multiplier": 1.0,
    },
    "biolab": {
        "id": "biolab",
        "name": "Bio-Lab Containment",
        "description": "Breach the infested xenobiology wing, decrypt quarantine locks, and reach extraction.",
        "difficulty": "Hard",
        "segment_sequence": [
            "drop_hangar",
            "security_corridor",
            "bio_chamber",
            "command_hub",
            "extraction_pad",
        ],
        "enemy_multiplier": 1.3,
    },
    "reactor": {
        "id": "reactor",
        "name": "Core Meltdown",
        "description": "Fight through heavy swarm presence in the reactor sector, stabilize cooling relays, and evacuate.",
        "difficulty": "Extreme",
        "segment_sequence": [
            "drop_hangar",
            "security_corridor",
            "bio_chamber",
            "reactor_hall",
            "command_hub",
            "extraction_pad",
        ],
        "enemy_multiplier": 1.6,
    },
}

SEGMENT_TEMPLATES = {
    "drop_hangar": {
        "id": "drop_hangar",
        "name": "Docking Bay 01",
        "width": 20.0,
        "height": 16.0,
        "theme": "hangar",
        "interior_walls": [
            {"x": 11.5, "y": 2.5, "w": 1.5, "h": 3.5, "blocks_sight": True},
            {"x": 11.5, "y": 10.0, "w": 1.5, "h": 3.5, "blocks_sight": True},
            {"x": 15.5, "y": 4.5, "w": 1.5, "h": 2.0, "blocks_sight": False},
        ],
        "has_exit_door": False,
        "puzzle": None,
        "enemy_spawns": [
            {"type": "stalker", "x": 16.5, "y": 4.0, "patrol_dx": 0.0, "patrol_dy": 6.0},
        ],
    },
    "security_corridor": {
        "id": "security_corridor",
        "name": "Security Checkpoint",
        "width": 22.0,
        "height": 16.0,
        "theme": "corridor",
        "interior_walls": [
            {"x": 5.5, "y": 3.0, "w": 2.0, "h": 4.0, "blocks_sight": True},
            {"x": 5.5, "y": 9.0, "w": 2.0, "h": 4.0, "blocks_sight": True},
            {"x": 12.5, "y": 5.5, "w": 2.5, "h": 5.0, "blocks_sight": True},
        ],
        "has_exit_door": True,
        "puzzle": {
            "type": "power_reroute",
            "x": 18.5,
            "y": 4.0,
            "label": "Reroute Security Bulkhead Power",
        },
        "enemy_spawns": [
            {"type": "stalker", "x": 9.5, "y": 4.0, "patrol_dx": 0.0, "patrol_dy": 8.0},
            {"type": "stalker", "x": 10.5, "y": 12.0, "patrol_dx": 0.0, "patrol_dy": -8.0},
            {"type": "spitter", "x": 16.5, "y": 10.5, "patrol_dx": -3.0, "patrol_dy": 0.0},
        ],
    },
    "bio_chamber": {
        "id": "bio_chamber",
        "name": "Xenobiology Labs",
        "width": 24.0,
        "height": 16.0,
        "theme": "biolab",
        "interior_walls": [
            {"x": 5.0, "y": 4.0, "w": 3.0, "h": 2.5, "blocks_sight": False},
            {"x": 5.0, "y": 9.5, "w": 3.0, "h": 2.5, "blocks_sight": False},
            {"x": 12.0, "y": 2.0, "w": 1.5, "h": 4.5, "blocks_sight": True},
            {"x": 12.0, "y": 9.5, "w": 1.5, "h": 4.5, "blocks_sight": True},
            {"x": 17.0, "y": 6.0, "w": 2.0, "h": 4.0, "blocks_sight": True},
        ],
        "has_exit_door": True,
        "puzzle": {
            "type": "signal_decrypt",
            "x": 20.0,
            "y": 12.0,
            "label": "Decrypt Bio-Quarantine Airlock",
        },
        "enemy_spawns": [
            {"type": "stalker", "x": 8.5, "y": 8.0, "patrol_dx": 3.0, "patrol_dy": 3.0},
            {"type": "spitter", "x": 15.0, "y": 4.0, "patrol_dx": 0.0, "patrol_dy": 7.0},
            {"type": "spitter", "x": 19.5, "y": 4.5, "patrol_dx": -3.0, "patrol_dy": 2.0},
            {"type": "brute", "x": 15.5, "y": 11.5, "patrol_dx": 3.0, "patrol_dy": -3.0},
        ],
    },
    "reactor_hall": {
        "id": "reactor_hall",
        "name": "Reactor Core Cooling",
        "width": 24.0,
        "height": 16.0,
        "theme": "reactor",
        "interior_walls": [
            {"x": 7.0, "y": 5.5, "w": 3.5, "h": 5.0, "blocks_sight": True},
            {"x": 14.5, "y": 3.0, "w": 2.5, "h": 3.5, "blocks_sight": True},
            {"x": 14.5, "y": 9.5, "w": 2.5, "h": 3.5, "blocks_sight": True},
        ],
        "has_exit_door": True,
        "puzzle": {
            "type": "power_reroute",
            "x": 20.5,
            "y": 3.5,
            "label": "Stabilize Coolant Conduit Array",
        },
        "enemy_spawns": [
            {"type": "stalker", "x": 5.5, "y": 3.5, "patrol_dx": 5.0, "patrol_dy": 0.0},
            {"type": "stalker", "x": 5.5, "y": 12.5, "patrol_dx": 5.0, "patrol_dy": 0.0},
            {"type": "brute", "x": 12.5, "y": 8.0, "patrol_dx": 0.0, "patrol_dy": 4.0},
            {"type": "spitter", "x": 19.0, "y": 11.5, "patrol_dx": -4.0, "patrol_dy": 0.0},
        ],
    },
    "command_hub": {
        "id": "command_hub",
        "name": "Central Command Nexus",
        "width": 22.0,
        "height": 16.0,
        "theme": "command",
        "interior_walls": [
            {"x": 6.0, "y": 3.5, "w": 2.0, "h": 3.0, "blocks_sight": True},
            {"x": 6.0, "y": 9.5, "w": 2.0, "h": 3.0, "blocks_sight": True},
            {"x": 12.0, "y": 6.0, "w": 3.0, "h": 4.0, "blocks_sight": False},
        ],
        "has_exit_door": True,
        "puzzle": {
            "type": "signal_decrypt",
            "x": 18.0,
            "y": 12.0,
            "label": "Transmit Extraction Beacon Telemetry",
        },
        "enemy_spawns": [
            {"type": "stalker", "x": 9.5, "y": 4.5, "patrol_dx": 4.0, "patrol_dy": 6.0},
            {"type": "spitter", "x": 16.0, "y": 4.5, "patrol_dx": 0.0, "patrol_dy": 6.0},
            {"type": "brute", "x": 15.0, "y": 8.0, "patrol_dx": -4.0, "patrol_dy": 0.0},
        ],
    },
    "extraction_pad": {
        "id": "extraction_pad",
        "name": "Extraction Helipad",
        "width": 20.0,
        "height": 16.0,
        "theme": "extraction",
        "interior_walls": [
            {"x": 5.5, "y": 3.0, "w": 1.5, "h": 3.0, "blocks_sight": False},
            {"x": 5.5, "y": 10.0, "w": 1.5, "h": 3.0, "blocks_sight": False},
        ],
        "has_exit_door": False,
        "puzzle": None,
        "enemy_spawns": [
            {"type": "stalker", "x": 10.0, "y": 4.0, "patrol_dx": 0.0, "patrol_dy": 8.0},
            {"type": "spitter", "x": 12.0, "y": 11.5, "patrol_dx": 0.0, "patrol_dy": -7.0},
        ],
    },
}
