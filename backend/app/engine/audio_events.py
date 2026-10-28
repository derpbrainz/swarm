"""Audio event payloads resolved from the validated authored sound registry."""

from dataclasses import dataclass, asdict
from enum import Enum
from types import MappingProxyType

from backend.app.content import CATALOG


class AudioCategory(str, Enum):
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
    category: AudioCategory
    radius_min: float
    radius_max: float
    base_gain: float
    occlude: bool
    loop: bool
    duration: float | None

    def as_dict(self):
        return {**asdict(self), "category": self.category.value}


AUDIO_CONFIGS = MappingProxyType({identity: AudioConfig(
    category=AudioCategory(value["category"]), radius_min=value["radius_min"],
    radius_max=value["radius_max"], base_gain=value["base_gain"],
    occlude=value["occlude"], loop=value["loop"], duration=value.get("duration"),
) for identity, value in CATALOG.definitions["sounds"].items()})


def get_audio_config(sound_type: str) -> AudioConfig:
    return AUDIO_CONFIGS[sound_type]


def create_audio_event(sound_type: str, x: float, y: float, source_id: str = "", *, event_id: str, sequence: int, timestamp: float, world_id: str) -> dict:
    return {
        **get_audio_config(sound_type).as_dict(),
        "event_id": event_id, "sequence": sequence, "world_id": world_id,
        "sound_type": sound_type, "timestamp": timestamp, "x": x, "y": y, "source_id": source_id,
    }
