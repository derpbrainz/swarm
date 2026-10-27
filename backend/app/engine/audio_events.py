"""
backend/app/engine/audio_events.py

Audio event system for Alien Swarm.

Defines sound categories, their tuned propagation/volume configuration,
and a factory function for creating audio event payloads that can be
broadcast to clients and consumed by AI perception systems.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class AudioCategory(str, Enum):
    """High-level classification of audio events in the game world."""

    WEAPON_FIRE = "weapon_fire"
    WEAPON_RELOAD = "weapon_reload"
    DOOR_OPERATE = "door_operate"
    PUZZLE_INTERACT = "puzzle_interact"
    ALIEN_ALERT = "alien_alert"
    ALIEN_ATTACK = "alien_attack"
    ALIEN_DEATH = "alien_death"
    PLAYER_FOOTSTEP = "player_footstep"
    PLAYER_HURT = "player_hurt"
    AMBIENT_VENT = "ambient_vent"
    AMBIENT_GENERATOR = "ambient_generator"
    EXTRACTION_COUNTDOWN = "extraction_countdown"


@dataclass(frozen=True)
class AudioConfig:
    """
    Tuning parameters describing how a sound propagates and plays.

    Attributes:
        category: The AudioCategory this sound belongs to.
        radius_min: Distance (in tiles/meters) within which the sound
            plays at full `base_gain`.
        radius_max: Maximum distance the sound is audible/perceivable.
            Beyond this, both AI perception and client-side replication
            should ignore the event.
        base_gain: Base volume multiplier at the source (0.0 - 1.0),
            before distance falloff, occlusion, or mix ducking.
        occlude: Whether walls/doors/solid geometry attenuate or block
            this sound. If False, the sound ignores level geometry
            (e.g. mission-critical global alarms).
        loop: Whether this is a continuous looping sound (ambience)
            rather than a one-shot event.
        duration: Approximate duration in seconds. None for indefinite/
            looping sounds or sounds whose length is determined by
            the underlying audio asset.
    """

    category: AudioCategory
    radius_min: float
    radius_max: float
    base_gain: float
    occlude: bool = True
    loop: bool = False
    duration: Optional[float] = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["category"] = self.category.value
        return d


# ---------------------------------------------------------------------------
# Tuned configuration table
#
# Keys are specific "sound_type" identifiers (finer-grained than category,
# e.g. distinguishing shotgun vs pistol fire) used by gameplay systems when
# raising an event. Each entry maps to the AudioCategory it belongs to plus
# its tuned propagation parameters.
# ---------------------------------------------------------------------------

AUDIO_CONFIGS: dict[str, AudioConfig] = {
    # --- Weapon fire -------------------------------------------------------
    "weapon_fire_shotgun": AudioConfig(
        category=AudioCategory.WEAPON_FIRE,
        radius_min=4.0,
        radius_max=35.0,
        base_gain=1.0,
        occlude=True,
        loop=False,
        duration=0.3,
    ),
    "weapon_fire_pistol": AudioConfig(
        category=AudioCategory.WEAPON_FIRE,
        radius_min=2.5,
        radius_max=24.0,
        base_gain=0.85,
        occlude=True,
        loop=False,
        duration=0.2,
    ),
    "weapon_fire_machinegun": AudioConfig(
        category=AudioCategory.WEAPON_FIRE,
        radius_min=3.0,
        radius_max=28.0,
        base_gain=0.9,
        occlude=True,
        loop=False,
        duration=0.15,
    ),
    "weapon_fire_plasma": AudioConfig(
        category=AudioCategory.WEAPON_FIRE,
        radius_min=3.0,
        radius_max=30.0,
        base_gain=0.95,
        occlude=True,
        loop=False,
        duration=0.25,
    ),

    # --- Reloads -------------------------------------------------------------
    "weapon_reload": AudioConfig(
        category=AudioCategory.WEAPON_RELOAD,
        radius_min=1.5,
        radius_max=12.0,
        base_gain=0.65,
        occlude=True,
        loop=False,
        duration=1.8,
    ),

    # --- Doors / puzzles -----------------------------------------------------
    "door_operate": AudioConfig(
        category=AudioCategory.DOOR_OPERATE,
        radius_min=2.0,
        radius_max=18.0,
        base_gain=0.7,
        occlude=True,
        loop=False,
        duration=1.2,
    ),
    "puzzle_interact": AudioConfig(
        category=AudioCategory.PUZZLE_INTERACT,
        radius_min=2.0,
        radius_max=18.0,
        base_gain=0.6,
        occlude=True,
        loop=False,
        duration=0.8,
    ),

    # --- Aliens --------------------------------------------------------------
    "alien_alert": AudioConfig(
        category=AudioCategory.ALIEN_ALERT,
        radius_min=2.5,
        radius_max=22.0,
        base_gain=0.85,
        occlude=True,
        loop=False,
        duration=1.0,
    ),
    "alien_attack": AudioConfig(
        category=AudioCategory.ALIEN_ATTACK,
        radius_min=2.5,
        radius_max=22.0,
        base_gain=0.85,
        occlude=True,
        loop=False,
        duration=0.6,
    ),
    "alien_death": AudioConfig(
        category=AudioCategory.ALIEN_DEATH,
        radius_min=2.5,
        radius_max=22.0,
        base_gain=0.85,
        occlude=True,
        loop=False,
        duration=0.9,
    ),

    # --- Player ---------------------------------------------------------------
    "player_footstep": AudioConfig(
        category=AudioCategory.PLAYER_FOOTSTEP,
        radius_min=1.0,
        radius_max=8.0,
        base_gain=0.4,
        occlude=True,
        loop=False,
        duration=0.3,
    ),
    "player_hurt": AudioConfig(
        category=AudioCategory.PLAYER_HURT,
        radius_min=1.5,
        radius_max=10.0,
        base_gain=0.6,
        occlude=True,
        loop=False,
        duration=0.5,
    ),

    # --- Ambience ---------------------------------------------------------------
    "ambient_vent": AudioConfig(
        category=AudioCategory.AMBIENT_VENT,
        radius_min=2.0,
        radius_max=16.0,
        base_gain=0.5,
        occlude=True,
        loop=True,
        duration=None,
    ),
    "ambient_generator": AudioConfig(
        category=AudioCategory.AMBIENT_GENERATOR,
        radius_min=2.0,
        radius_max=16.0,
        base_gain=0.5,
        occlude=True,
        loop=True,
        duration=None,
    ),

    # --- Mission critical / global -----------------------------------------
    "extraction_countdown": AudioConfig(
        category=AudioCategory.EXTRACTION_COUNTDOWN,
        radius_min=8.0,
        radius_max=50.0,
        base_gain=1.0,
        occlude=False,
        loop=False,
        duration=None,
    ),
}


def get_audio_config(sound_type: str) -> AudioConfig:
    """
    Retrieve the tuned AudioConfig for a given sound_type key.

    Raises:
        KeyError: if sound_type is not registered in AUDIO_CONFIGS.
    """
    try:
        return AUDIO_CONFIGS[sound_type]
    except KeyError as exc:
        raise KeyError(
            f"Unknown audio sound_type '{sound_type}'. "
            f"Registered types: {sorted(AUDIO_CONFIGS.keys())}"
        ) from exc


def create_audio_event(
    sound_type: str,
    x: float,
    y: float,
    source_id: str = "",
) -> dict:
    """
    Create a fully-populated audio event payload for broadcast to clients
    and/or consumption by AI perception systems.

    Args:
        sound_type: Key into AUDIO_CONFIGS identifying the specific sound
            (e.g. "weapon_fire_shotgun", "door_operate").
        x: World X coordinate of the sound's origin.
        y: World Y coordinate of the sound's origin.
        source_id: Optional identifier of the entity that produced the
            sound (player id, alien id, door id, etc).

    Returns:
        A dict representing the audio event, suitable for JSON
        serialization and network transport.

    Raises:
        KeyError: if sound_type is not a recognized audio configuration.
    """
    config = get_audio_config(sound_type)

    event = {
        "event_id": str(uuid.uuid4()),
        "sound_type": sound_type,
        "category": config.category.value,
        "timestamp": time.time(),
        "x": x,
        "y": y,
        "source_id": source_id,
        "radius_min": config.radius_min,
        "radius_max": config.radius_max,
        "base_gain": config.base_gain,
        "occlude": config.occlude,
        "loop": config.loop,
        "duration": config.duration,
    }

    return event