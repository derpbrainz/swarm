"""Validated authored definitions. Runtime saves and instances never own this data."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path, PurePosixPath
import sys
from types import MappingProxyType
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CONTENT_PATH = Path(__file__).with_name("content.json")
ASSET_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "assets"
Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)]
Text = Annotated[str, Field(min_length=1, max_length=500)]
Color = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
Positive = Annotated[float, Field(gt=0, le=10000, allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(ge=0, le=10000, allow_inf_nan=False)]
Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=1, le=10000)]
Coordinate = Annotated[float, Field(ge=-10000, le=10000, allow_inf_nan=False)]
Category = Literal["primary_weapon", "secondary_weapon", "special_ability", "quick_use", "storage"]


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class Display(Definition):
    id: Id
    name: Text
    description: Text


class Character(Definition):
    id: Id
    faction: Id
    name: Text
    title: Text
    color: Color
    max_hp: Count
    speed: Positive
    sprint_speed: Positive
    footstep_radius: Positive
    sight_range: Positive
    carry_capacity: Count
    loadout_id: Id
    passive: Display


class Loadout(Definition):
    primary_weapon: Id
    secondary_weapon: Id
    special_ability: Id
    quick_use: Id
    storage: Annotated[list[Id], Field(min_length=1, max_length=20)]


class Weapon(Definition):
    damage: Count
    fire_rate: Positive
    projectile_speed: Positive
    range: Positive
    pellets: Annotated[int, Field(ge=1, le=100)]
    spread: Annotated[float, Field(ge=0, le=3.141593, allow_inf_nan=False)]
    sound_radius: Positive
    mag_size: Count
    reload_time: Positive
    color: Color
    can_fire_while_running: bool
    fire_sound: Id
    reload_sound: Id


class Shield(Definition):
    behavior: Literal["shockwave_shield"]
    cooldown: Positive
    duration: Positive
    radius: Positive
    push_offset: Nonnegative
    push_multiplier: Positive
    damage: Count
    sound_radius: Positive
    melee_multiplier: Fraction
    melee_minimum: Count


class Turret(Definition):
    behavior: Literal["turret"]
    cooldown: Positive | None = None
    duration: Positive
    deploy_offset: Nonnegative
    range: Positive
    fire_interval: Positive
    damage: Count
    projectile_speed: Positive
    projectile_ttl: Positive
    projectile_radius: Positive
    projectile_offset: Nonnegative
    color: Color
    sound_radius: Positive
    fire_sound: Id


class NanoField(Definition):
    behavior: Literal["nano_field"]
    cooldown: Positive
    duration: Positive
    radius: Positive
    heal_interval: Positive
    heal: Count
    low_health_heal: Count
    low_health_threshold: Fraction
    immediate_revive_minimum: Count
    immediate_revive_fraction: Fraction
    tick_revive_hp: Count


class Sonar(Definition):
    behavior: Literal["sonar"]
    cooldown: Positive | None = None
    duration: Positive
    radius: Positive


class Heal(Definition):
    behavior: Literal["heal"]
    heal: Count


class SightBoost(Definition):
    behavior: Literal["sight_boost"]
    sight_boost: Positive


class AmmoRefill(Definition):
    behavior: Literal["ammo_refill"]


class Loot(Definition):
    behavior: Literal["loot"]
    loot: Count


Effect = Annotated[Shield | Turret | NanoField | Sonar | Heal | SightBoost | AmmoRefill | Loot, Field(discriminator="behavior")]


class Item(Display):
    category: Category
    weight: Count
    weapon_id: Id | None = None
    effect_id: Id | None = None
    consumable: bool = False


class PowerPuzzle(Definition):
    handler: Literal["power_reroute"]
    node_count: Annotated[int, Field(ge=1, le=32)]
    allow_tech_bypass: bool = True


class SignalPuzzle(Definition):
    handler: Literal["signal_decrypt"]
    sequence_length: Annotated[int, Field(ge=1, le=32)]
    symbols: Annotated[list[Annotated[str, Field(min_length=1, max_length=32)]], Field(min_length=2, max_length=32)]
    allow_tech_bypass: bool = True

    @model_validator(mode="after")
    def unique_symbols(self):
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("duplicate symbols")
        return self


class TimedPuzzle(Definition):
    handler: Literal["timed_input"]
    duration: Positive
    input: Annotated[str, Field(min_length=1, max_length=32)]
    allow_tech_bypass: Literal[False] = False


PuzzleDefinition = Annotated[PowerPuzzle | SignalPuzzle | TimedPuzzle, Field(discriminator="handler")]


class Attack(Definition):
    handler: Literal["melee", "projectile"]
    range: Positive
    damage: Count
    cooldown: Positive
    projectile_speed: Nonnegative = 0

    @model_validator(mode="after")
    def compatible_speed(self):
        if (self.handler == "projectile") != (self.projectile_speed > 0):
            raise ValueError("projectile handler requires speed; melee forbids it")
        return self


class EncounterPhase(Definition):
    id: Id
    health_at_most: Fraction
    attacks: Annotated[list[Id], Field(min_length=1, max_length=8)]


class Encounter(Definition):
    phases: Annotated[list[EncounterPhase], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def ordered_phases(self):
        thresholds = [p.health_at_most for p in self.phases]
        if thresholds[0] != 1 or thresholds != sorted(set(thresholds), reverse=True) or len({p.id for p in self.phases}) != len(self.phases):
            raise ValueError("unique phases require descending thresholds starting at one")
        return self


RuntimeId = Annotated[str, Field(min_length=1, max_length=128)]


class ObjectiveEffect(Definition):
    handler: Literal["open_door", "unlock_crate", "activate_extraction"]
    target_id: RuntimeId | None = None

    @model_validator(mode="after")
    def compatible_target(self):
        if (self.handler != "activate_extraction") != (self.target_id is not None):
            raise ValueError("effect requires matching target")
        return self


class Objective(Definition):
    id: RuntimeId
    label: Text
    condition: Literal["puzzle_solved", "enemy_dead", "complete"]
    target_id: RuntimeId | None = None
    dependencies: Annotated[list[RuntimeId], Field(max_length=128)] = []
    effects: Annotated[list[ObjectiveEffect], Field(max_length=128)] = []
    required: bool = True

    @model_validator(mode="after")
    def compatible_target(self):
        if (self.condition != "complete") != (self.target_id is not None):
            raise ValueError("condition requires matching target")
        return self


def validate_objectives(records, *, puzzles, enemies, walls, crates):
    """Validate the small objective graph before any effects can run."""
    if len(records) > 128:
        raise ValueError("objective graph exceeds 128 records")
    parsed = [Objective.model_validate(r) for r in records]
    by_id = {r.id: r for r in parsed}
    if len(by_id) != len(parsed):
        raise ValueError("duplicate objective ID")
    targets = {"puzzle_solved": puzzles, "enemy_dead": enemies}
    visited = set()
    def visit(identity, trail):
        if identity not in by_id:
            raise ValueError(f"unknown objective dependency {identity}")
        if identity in trail:
            raise ValueError(f"objective cycle: {(*trail, identity)}")
        if identity in visited:
            return
        record = by_id[identity]
        if len(set(record.dependencies)) != len(record.dependencies):
            raise ValueError(f"duplicate objective dependency {identity}")
        if record.condition in targets and record.target_id not in targets[record.condition]:
            raise ValueError(f"unknown objective target {record.target_id}")
        for effect in record.effects:
            registry = walls if effect.handler == "open_door" else crates if effect.handler == "unlock_crate" else None
            if registry is not None and effect.target_id not in registry:
                raise ValueError(f"unknown effect target {effect.target_id}")
        for dep in record.dependencies:
            visit(dep, (*trail, identity))
        visited.add(identity)
    for identity in by_id:
        visit(identity, ())
    return [r.model_dump(exclude_none=True) for r in parsed]


class Enemy(Definition):
    id: Id
    faction: Id
    name: Text
    color: Color
    max_hp: Count
    speed: Positive
    patrol_speed: Positive
    radius: Positive
    sight_range: Positive
    sight_angle: Annotated[float, Field(gt=0, le=6.283186, allow_inf_nan=False)]
    hearing_multiplier: Positive
    attacks: Annotated[list[Id], Field(min_length=1, max_length=8)]
    encounter_id: Id | None = None


class OpenThreshold(Definition):
    mode: Literal["open"]


class ProximityThreshold(Definition):
    mode: Literal["proximity"]
    door_type: Literal["door"]
    sound_type: Id
    trigger_radius: Positive


class PuzzleThreshold(Definition):
    mode: Literal["puzzle"]
    door_type: Literal["door"]
    sound_type: Id
    puzzle_id: Id


Threshold = Annotated[OpenThreshold | ProximityThreshold | PuzzleThreshold, Field(discriminator="mode")]


class Mission(Definition):
    id: Id
    name: Text
    description: Text
    difficulty: Text
    prerequisites: list[Id]
    unlock_requirement: Text | None
    enemy_multiplier: Positive
    grid_w: Annotated[int, Field(ge=1, le=50)]
    grid_h: Annotated[int, Field(ge=1, le=50)]
    min_segments: Count
    max_segments: Count
    allowed_templates: Annotated[list[Id], Field(min_length=1)]
    required_templates: list[Id]
    completion_effects: Annotated[list[Literal["activate_extraction"]], Field(max_length=1)]
    objectives: Annotated[list[Objective], Field(max_length=128)] = []
    threshold_weights: Annotated[dict[Id, Nonnegative], Field(min_length=1, max_length=128)]


class Port(Definition):
    cell_x: Annotated[int, Field(ge=0)]
    cell_y: Annotated[int, Field(ge=0)]
    dir: Literal["N", "S", "E", "W"]


class Point(Definition):
    x: Nonnegative
    y: Nonnegative


class Wall(Point):
    w: Positive
    h: Positive
    blocks_sight: bool
    active: bool = True
    is_door: bool = False
    material: Literal["solid", "door", "transparent"] = "solid"
    threshold_id: Id | None = None


class PuzzleSlot(Point):
    type: Id
    label: Text


class EnemySpawn(Point):
    type: Id
    patrol_dx: Coordinate
    patrol_dy: Coordinate


class LootSpawn(Point):
    type: Id
    variety: bool = False


class CrateSpawn(Point):
    locked: bool


class AmbienceEmitter(Point):
    id: Id
    sound_type: Id
    active: bool


class RenderRect(Definition):
    x: Coordinate
    y: Coordinate
    w: Positive
    h: Positive


class ImageAsset(Definition):
    path: Annotated[str, Field(max_length=500, pattern=r"^[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_.-]+)+\.(svg|png|jpg|webp)$")]


class Material(Definition):
    top: Color
    left: Color
    right: Color
    stroke: Color
    texture: Id | None = None


class Visual(Definition):
    fallback: Literal["wall", "player", "enemy", "turret", "loot", "crate", "puzzle", "box"]
    material_id: Id
    footprint: RenderRect
    width: Positive
    height: Positive
    pivot: Annotated[list[Fraction], Field(min_length=2, max_length=2)]
    elevation: Nonnegative
    layer: Annotated[int, Field(ge=-10, le=10)]
    sprite: Id | None = None
    directions: Annotated[int, Field(ge=1, le=16)] = 1
    frames: Annotated[int, Field(ge=1, le=32)] = 1
    fps: Nonnegative = 0


class PropGeometry(Definition):
    movement: RenderRect | None = None
    projectile: RenderRect | None = None
    sight: RenderRect | None = None
    acoustic: RenderRect | None = None
    interaction: RenderRect | None = None
    acoustic_material: Literal["solid", "door", "transparent"] = "solid"


class PropInteraction(Definition):
    type: Id
    radius: Positive
    label: Text


class Prop(Definition):
    visual_id: Id
    geometry: PropGeometry
    interaction: PropInteraction | None = None


class PropPlacement(Point):
    id: Id
    archetype: Id
    orientation: Annotated[int, Field(ge=0, le=270)] = 0

    @model_validator(mode="after")
    def orthogonal_orientation(self):
        if self.orientation not in (0, 90, 180, 270):
            raise ValueError("orientation must be 0, 90, 180 or 270")
        return self


class Presentation(Definition):
    assets: dict[Id, ImageAsset]
    materials: dict[Id, Material]
    visuals: dict[Id, Visual]
    defaults: dict[Literal["wall", "player", "enemy", "turret", "loot", "crate", "puzzle"], Id]
    floor_materials: dict[Literal["hangar", "corridor", "biolab", "reactor", "command", "extraction"], Id]


class Segment(Definition):
    id: Id
    name: Text
    grid_w: Annotated[int, Field(ge=1, le=50)]
    grid_h: Annotated[int, Field(ge=1, le=50)]
    width: Positive
    height: Positive
    theme: Literal["hangar", "corridor", "biolab", "reactor", "command", "extraction"]
    role: Literal["start", "room", "end"]
    crate_spawn: CrateSpawn | None = None
    connector_ports: Annotated[list[Port], Field(min_length=1)]
    interior_walls: list[Wall]
    puzzle_slots: list[PuzzleSlot]
    enemy_spawns: list[EnemySpawn]
    loot_spawns: list[LootSpawn]
    loot_count: Annotated[int, Field(ge=0, le=100)]
    ambience_emitters: Annotated[list[AmbienceEmitter], Field(max_length=100)] = []
    props: Annotated[list[PropPlacement], Field(max_length=100)] = []


class LootTables(Definition):
    supply_crates: Annotated[list[Annotated[list[Id], Field(min_length=1)]], Field(min_length=1)]
    ground_variety: Annotated[list[Id], Field(min_length=1)]


class Sound(Definition):
    category: Literal["weapon_fire", "weapon_reload", "door_operate", "alien_attack", "alien_death", "player_footstep", "ambient_vent", "extraction_countdown"]
    asset: Text
    radius_min: Nonnegative
    radius_max: Positive
    base_gain: Fraction
    occlude: bool
    loop: bool
    duration: Positive | None


class Music(Definition):
    bpm: Positive
    tracks: dict[Literal["explore", "combat"], Text]


class GeometryRules(Definition):
    cell_size: Positive
    connector_offset: Positive
    connector_width: Positive
    wall_thickness: Positive


class TimingRules(Definition):
    tick_rate: Annotated[int, Field(ge=1, le=240)]
    max_catchup: Annotated[int, Field(ge=1, le=60)]
    prep_countdown: Positive
    dropship_countdown: Positive
    input_timeout: Positive
    sound_ring_duration: Positive


class PlayerRules(Definition):
    radius: Positive
    projectile_radius: Positive
    projectile_offset: Nonnegative
    sprint_forward_dot: Fraction
    walk_footstep_interval: Positive
    run_footstep_interval: Positive
    walk_noise_multiplier: Positive
    run_noise_multiplier: Positive
    footstep_sound: Id
    reload_sound_radius: Positive
    vanguard_melee_multiplier: Fraction
    vanguard_melee_minimum: Count


class InteractionRules(Definition):
    revive_radius: Positive
    revive_fraction: Fraction
    interact_radius: Positive
    pickup_radius: Positive
    puzzle_radius: Positive
    crate_radius: Positive
    ground_item_radius: Positive
    alarm_radius: Positive


class AIRules(Definition):
    hearing_alert_duration: Positive
    engaged_alert_duration: Positive
    ally_alert_radius: Positive
    ally_alert_duration: Positive
    proximity_radius: Positive
    lost_target_speed_multiplier: Positive
    investigate_speed_multiplier: Positive
    lost_target_reach: Positive
    investigate_reach: Positive
    waypoint_reach: Positive
    projectile_ttl: Positive
    projectile_radius: Positive
    projectile_offset: Nonnegative
    projectile_color: Color


class CombatRules(Definition):
    factions: dict[Id, dict[Id, Literal["ally", "hostile", "neutral"]]]
    friendly_fire: bool
    self_damage: bool


class AudioRules(Definition):
    history_duration: Positive
    history_limit: Annotated[int, Field(ge=1, le=4096)]
    door_sound: Id
    alien_attack_sound: Id
    alien_death_sound: Id
    asset_retry_interval: Positive
    music_crossfade_duration: Positive
    occlusion_cache_limit: Annotated[int, Field(ge=1, le=10000)]
    material_penalties: dict[Literal["solid", "door", "transparent"], Nonnegative]
    occlusion_tiers: Annotated[list[Annotated[list[Annotated[float, Field(ge=0, le=20000, allow_inf_nan=False)]], Field(min_length=3, max_length=3)]], Field(min_length=1, max_length=20)]
    occlusion_fallback_gain: Fraction
    occlusion_fallback_cutoff: Positive


class Rules(Definition):
    geometry: GeometryRules
    timing: TimingRules
    player: PlayerRules
    interaction: InteractionRules
    ai: AIRules
    combat: CombatRules
    audio: AudioRules


class WorldCapabilities(Definition):
    movement: bool
    interactions: bool
    combat: bool
    ai: bool
    objectives: bool
    extraction: bool
    failure: bool
    mission_clock: bool
    cooldowns: bool
    beneficial_use: Literal["normal", "disabled"]


class WorldTile(RenderRect):
    id: Id
    name: Text
    theme: Literal["hangar", "corridor", "biolab", "reactor", "command", "extraction"]


class AuthoredWorld(Definition):
    kind: Literal["hub", "mission"]
    name: Text
    rules_id: Id
    width: Positive
    height: Positive
    tiles: Annotated[list[WorldTile], Field(min_length=1, max_length=100)]
    walls: Annotated[list[Wall], Field(max_length=100)]
    props: Annotated[list[PropPlacement], Field(max_length=100)]
    spawns: Annotated[list[Point], Field(min_length=1, max_length=4)]


class CatalogSchema(Definition):
    schema_version: Literal[1]
    content_version: Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", max_length=80)]
    classes: dict[Id, Character]
    loadouts: dict[Id, Loadout]
    weapons: dict[Id, Weapon]
    effects: dict[Id, Effect]
    items: dict[Id, Item]
    enemies: dict[Id, Enemy]
    missions: dict[Id, Mission]
    segments: dict[Id, Segment]
    loot_tables: LootTables
    sounds: dict[Id, Sound]
    music: Music
    rules: Rules
    presentation: Presentation
    props: dict[Id, Prop]
    puzzles: dict[Id, PuzzleDefinition]
    thresholds: Annotated[dict[Id, Threshold], Field(min_length=1, max_length=128)]
    attacks: dict[Id, Attack]
    encounters: dict[Id, Encounter]
    world_rules: dict[Id, WorldCapabilities]
    worlds: dict[Id, AuthoredWorld]

    @model_validator(mode="after")
    def references_and_geometry(self):
        def require(condition, path, message):
            if not condition:
                raise ValueError(f"{path}: {message}")

        def reference(identity, registry, path):
            require(identity in registry, path, f"unknown reference {identity!r}")

        for identity, threshold in self.thresholds.items():
            path = f"thresholds.{identity}"
            if threshold.mode != "open":
                reference(threshold.sound_type, self.sounds, path + ".sound_type")
                sound = self.sounds[threshold.sound_type]
                require(sound.category == "door_operate" and not sound.loop, path + ".sound_type", "requires one-shot door sound")
            if threshold.mode == "proximity":
                require(threshold.trigger_radius > self.rules.player.radius, path + ".trigger_radius", "must exceed player collision radius")
            if threshold.mode == "puzzle":
                reference(threshold.puzzle_id, self.puzzles, path + ".puzzle_id")

        def wall_threshold(wall, path):
            if wall.threshold_id is not None:
                reference(wall.threshold_id, self.thresholds, path + ".threshold_id")
                require(self.thresholds[wall.threshold_id].mode == "proximity", path + ".threshold_id", "standalone walls require proximity policy")
                require(wall.is_door and wall.active and wall.blocks_sight and wall.material == "door", path, "threshold wall must be a closed blocking door")

        presentation = self.presentation
        require(set(presentation.defaults) == {"wall", "player", "enemy", "turret", "loot", "crate", "puzzle"}, "presentation.defaults", "all physical kinds required")
        require(set(presentation.floor_materials) == {"hangar", "corridor", "biolab", "reactor", "command", "extraction"}, "presentation.floor_materials", "all floor themes required")
        for identity, material in presentation.materials.items():
            if material.texture:
                reference(material.texture, presentation.assets, f"presentation.materials.{identity}.texture")
        for identity, visual in presentation.visuals.items():
            reference(visual.material_id, presentation.materials, f"presentation.visuals.{identity}.material_id")
            if visual.sprite:
                reference(visual.sprite, presentation.assets, f"presentation.visuals.{identity}.sprite")
            require(visual.frames == 1 or visual.fps > 0, f"presentation.visuals.{identity}.fps", "animated frames require positive fps")
            require(visual.sprite is not None or (visual.directions == 1 and visual.frames == 1 and visual.fps == 0),
                    f"presentation.visuals.{identity}.sprite", "atlas fields require a sprite")
        for kind, identity in presentation.defaults.items():
            reference(identity, presentation.visuals, f"presentation.defaults.{kind}")
            require(presentation.visuals[identity].fallback == kind, f"presentation.defaults.{kind}", "fallback must match physical kind")
        for theme, identity in presentation.floor_materials.items():
            reference(identity, presentation.materials, f"presentation.floor_materials.{theme}")
        for identity, prop in self.props.items():
            reference(prop.visual_id, presentation.visuals, f"props.{identity}.visual_id")
            require(presentation.visuals[prop.visual_id].fallback == "box", f"props.{identity}.visual_id", "props require supported box fallback")
            require(prop.geometry.interaction is None or prop.interaction is not None, f"props.{identity}.geometry.interaction", "interaction bounds require a target")
            if prop.interaction:
                require(prop.interaction.type in self.puzzles or prop.interaction.type in ("stash", "mission_selection"),
                        f"props.{identity}.interaction.type", "unknown interaction handler")
        reference("mission", self.world_rules, "world_rules.mission")
        for identity, world in self.worlds.items():
            path = f"worlds.{identity}"
            reference(world.rules_id, self.world_rules, path + ".rules_id")
            require(len({t.id for t in world.tiles}) == len(world.tiles) and len({p.id for p in world.props}) == len(world.props), path, "duplicate instance ID")
            for tile in world.tiles:
                require(tile.x >= 0 and tile.y >= 0 and tile.x + tile.w <= world.width and tile.y + tile.h <= world.height, path + ".tiles", "tile outside world")
            for wall in world.walls:
                wall_threshold(wall, path + ".walls")
                require(wall.x + wall.w <= world.width and wall.y + wall.h <= world.height, path + ".walls", "wall outside world")
            for placement in world.props:
                reference(placement.archetype, self.props, path + ".props.archetype")
                require(placement.x <= world.width and placement.y <= world.height, path + ".props", "prop outside world")
                for channel in ("movement", "projectile", "sight", "acoustic", "interaction"):
                    rect = getattr(self.props[placement.archetype].geometry, channel)
                    if rect:
                        angle = math.radians(placement.orientation)
                        for x, y in ((rect.x, rect.y), (rect.x + rect.w, rect.y), (rect.x, rect.y + rect.h), (rect.x + rect.w, rect.y + rect.h)):
                            require(-1e-8 <= placement.x + x * math.cos(angle) - y * math.sin(angle) <= world.width + 1e-8 and
                                    -1e-8 <= placement.y + x * math.sin(angle) + y * math.cos(angle) <= world.height + 1e-8,
                                    path + ".props." + channel, "geometry outside world")
            for spawn in world.spawns:
                require(spawn.x <= world.width and spawn.y <= world.height, path + ".spawns", "spawn outside world")
        for identity, enemy in self.enemies.items():
            for attack in enemy.attacks:
                reference(attack, self.attacks, f"enemies.{identity}.attacks")
            if enemy.encounter_id:
                reference(enemy.encounter_id, self.encounters, f"enemies.{identity}.encounter_id")
        for identity, encounter in self.encounters.items():
            for phase in encounter.phases:
                for attack in phase.attacks:
                    reference(attack, self.attacks, f"encounters.{identity}.{phase.id}.attacks")
        for section in ("classes", "items", "enemies", "missions", "segments"):
            for identity, value in getattr(self, section).items():
                require(identity == value.id, f"{section}.{identity}.id", "ID must match its registry key")
        factions = self.rules.combat.factions
        for identity, relations in factions.items():
            require(set(relations) == set(factions), f"rules.combat.factions.{identity}", "all faction relations required")
            require(relations[identity] == "ally", f"rules.combat.factions.{identity}", "self relation must be ally")
            for other, relation in relations.items():
                require(factions[other].get(identity) == relation, f"rules.combat.factions.{identity}.{other}", "relations must be symmetric")
        for section in ("classes", "enemies"):
            for identity, actor in getattr(self, section).items():
                reference(actor.faction, factions, f"{section}.{identity}.faction")
        for identity, item in self.items.items():
            path = f"items.{identity}"
            weapon = item.category in ("primary_weapon", "secondary_weapon")
            require(bool(item.weapon_id) == weapon and bool(item.effect_id) != weapon, path, "category requires exactly one matching weapon/effect reference")
            if weapon:
                reference(item.weapon_id, self.weapons, path + ".weapon_id")
            else:
                reference(item.effect_id, self.effects, path + ".effect_id")
                effect = self.effects[item.effect_id]
                if item.category == "special_ability":
                    require(effect.behavior in ("shockwave_shield", "turret", "nano_field", "sonar") and getattr(effect, "cooldown", None) is not None, path + ".effect_id", "unsupported class ability")
                elif item.category == "quick_use":
                    require(effect.behavior in ("heal", "turret", "sonar", "sight_boost"), path + ".effect_id", "unsupported quick-use effect")
                else:
                    require(effect.behavior in ("ammo_refill", "loot"), path + ".effect_id", "unsupported storage effect")
        for identity, character in self.classes.items():
            reference(character.loadout_id, self.loadouts, f"classes.{identity}.loadout_id")
            require(character.passive.id in ("bulwark", "field_scanner", "triage", "quiet_step"), f"classes.{identity}.passive.id", "unknown passive behavior")
            require(character.speed <= character.sprint_speed, f"classes.{identity}.sprint_speed", "sprint cannot be slower than walking")
        for identity, loadout in self.loadouts.items():
            for slot, values in loadout.model_dump().items():
                for tid in values if isinstance(values, list) else [values]:
                    path = f"loadouts.{identity}.{slot}"
                    reference(tid, self.items, path)
                    require(self.items[tid].category == slot or slot == "storage", path, "incompatible item category")
        for identity, weapon in self.weapons.items():
            for field, category in (("fire_sound", "weapon_fire"), ("reload_sound", "weapon_reload")):
                sid = getattr(weapon, field)
                path = f"weapons.{identity}.{field}"
                reference(sid, self.sounds, path)
                require(self.sounds[sid].category == category, path, "incompatible sound category")
        for identity, effect in self.effects.items():
            if isinstance(effect, Turret):
                reference(effect.fire_sound, self.sounds, f"effects.{identity}.fire_sound")
                require(self.sounds[effect.fire_sound].category == "weapon_fire", f"effects.{identity}.fire_sound", "incompatible sound category")
        reference(self.rules.player.footstep_sound, self.sounds, "rules.player.footstep_sound")
        require(self.sounds[self.rules.player.footstep_sound].category == "player_footstep", "rules.player.footstep_sound", "incompatible sound category")
        for field, category in (("door_sound", "door_operate"), ("alien_attack_sound", "alien_attack"), ("alien_death_sound", "alien_death")):
            sid = getattr(self.rules.audio, field)
            reference(sid, self.sounds, "rules.audio." + field)
            require(self.sounds[sid].category == category and not self.sounds[sid].loop, "rules.audio." + field, "incompatible one-shot category")
        audio = self.rules.audio
        require(set(audio.material_penalties) == {"solid", "door", "transparent"}, "rules.audio.material_penalties", "all acoustic materials required")
        thresholds = [tier[0] for tier in audio.occlusion_tiers]
        require(thresholds == sorted(set(thresholds)) and thresholds[0] == 0, "rules.audio.occlusion_tiers", "strictly ascending thresholds from zero required")
        require(all(gain <= 1 and 350 <= cutoff <= 20000 for _, gain, cutoff in audio.occlusion_tiers) and 350 <= audio.occlusion_fallback_cutoff <= 20000, "rules.audio.occlusion_tiers", "invalid gain/cutoff")
        for identity, sound in self.sounds.items():
            require(sound.radius_min <= sound.radius_max, f"sounds.{identity}.radius_max", "must be at least radius_min")
        require(set(self.music.tracks) == {"explore", "combat"}, "music.tracks", "both currently consumed music states are required")
        for identity, mission in self.missions.items():
            path = f"missions.{identity}"
            require(sum(mission.threshold_weights.values()) > 0, path + ".threshold_weights", "positive total weight required")
            for threshold_id in mission.threshold_weights:
                reference(threshold_id, self.thresholds, path + ".threshold_weights")
            authored_objectives = [o.model_dump(exclude_none=True) for o in mission.objectives]
            validate_objectives(authored_objectives,
                                puzzles={o.target_id for o in mission.objectives if o.condition == "puzzle_solved"},
                                enemies={o.target_id for o in mission.objectives if o.condition == "enemy_dead"},
                                walls={e.target_id for o in mission.objectives for e in o.effects if e.handler == "open_door"},
                                crates={e.target_id for o in mission.objectives for e in o.effects if e.handler == "unlock_crate"})
            require(mission.min_segments <= mission.max_segments <= mission.grid_w * mission.grid_h, path + ".max_segments", "invalid segment bounds")
            for tid in mission.allowed_templates:
                reference(tid, self.segments, path + ".allowed_templates")
                segment = self.segments[tid]
                require(segment.grid_w <= mission.grid_w and segment.grid_h <= mission.grid_h, path + ".allowed_templates", "segment exceeds mission grid")
            for prerequisite in mission.prerequisites:
                reference(prerequisite, self.missions, path + ".prerequisites")
            for role in ("start", "end"):
                require(sum(self.segments[tid].role == role for tid in mission.allowed_templates) == 1, path + ".allowed_templates", f"requires exactly one {role} role")
            require(len(set(mission.allowed_templates)) == len(mission.allowed_templates), path + ".allowed_templates", "duplicate template")
            require(len(set(mission.required_templates)) == len(mission.required_templates), path + ".required_templates", "duplicate required template")
            require(set(mission.required_templates).issubset(mission.allowed_templates), path + ".required_templates", "required template must be allowed")
            require(len(set(mission.required_templates) | {tid for tid in mission.allowed_templates if self.segments[tid].role != "room"}) <= mission.max_segments, path + ".required_templates", "requirements exceed segment maximum")
        visited = set()
        def visit(identity, trail):
            require(identity not in trail, f"missions.{identity}.prerequisites", "prerequisite cycle: " + " -> ".join((*trail, identity)))
            if identity in visited:
                return
            for prerequisite in self.missions[identity].prerequisites:
                visit(prerequisite, (*trail, identity))
            visited.add(identity)
        for identity in self.missions:
            visit(identity, ())
        geometry = self.rules.geometry
        require(geometry.connector_offset * 2 + geometry.connector_width == geometry.cell_size, "rules.geometry.connector_width", "connector and symmetric flanks must fill cell")
        require(geometry.wall_thickness < geometry.connector_width, "rules.geometry.wall_thickness", "wall is wider than connector")
        for identity, segment in self.segments.items():
            path = f"segments.{identity}"
            require(segment.width == segment.grid_w * geometry.cell_size, path + ".width", "must match grid footprint")
            require(segment.height == segment.grid_h * geometry.cell_size, path + ".height", "must match grid footprint")
            require(len({p.id for p in segment.props}) == len(segment.props), path + ".props", "duplicate prop ID")
            for index, placement in enumerate(segment.props):
                pp = f"{path}.props.{index}"
                reference(placement.archetype, self.props, pp + ".archetype")
                require(placement.x <= segment.width and placement.y <= segment.height, pp, "prop outside segment bounds")
                for channel in ("movement", "projectile", "sight", "acoustic", "interaction"):
                    rect = getattr(self.props[placement.archetype].geometry, channel)
                    if rect:
                        angle = math.radians(placement.orientation)
                        for x, y in ((rect.x, rect.y), (rect.x + rect.w, rect.y), (rect.x, rect.y + rect.h), (rect.x + rect.w, rect.y + rect.h)):
                            require(-1e-8 <= placement.x + x * math.cos(angle) - y * math.sin(angle) <= segment.width + 1e-8 and
                                    -1e-8 <= placement.y + x * math.sin(angle) + y * math.cos(angle) <= segment.height + 1e-8,
                                    pp + "." + channel, "geometry outside segment bounds")
            require(len({e.id for e in segment.ambience_emitters}) == len(segment.ambience_emitters), path + ".ambience_emitters", "duplicate emitter ID")
            for index, emitter in enumerate(segment.ambience_emitters):
                pp = f"{path}.ambience_emitters.{index}"
                reference(emitter.sound_type, self.sounds, pp + ".sound_type")
                require(self.sounds[emitter.sound_type].loop, pp + ".sound_type", "ambience requires a loop sound")
                require(emitter.x <= segment.width and emitter.y <= segment.height, pp, "geometry outside segment bounds")
            if segment.crate_spawn is not None:
                require(segment.crate_spawn.x <= segment.width and segment.crate_spawn.y <= segment.height, path + ".crate_spawn", "geometry outside segment bounds")
            seen = set()
            for index, port in enumerate(segment.connector_ports):
                pp = f"{path}.connector_ports.{index}"
                require(port.cell_x < segment.grid_w and port.cell_y < segment.grid_h, pp, "connector cell outside footprint")
                exterior = {"N": port.cell_y == 0, "S": port.cell_y == segment.grid_h - 1, "W": port.cell_x == 0, "E": port.cell_x == segment.grid_w - 1}
                require(exterior[port.dir], pp + ".dir", "connector faces an interior cell")
                key = (port.cell_x, port.cell_y, port.dir)
                require(key not in seen, pp, "duplicate connector")
                seen.add(key)
            for field in ("interior_walls", "puzzle_slots", "enemy_spawns", "loot_spawns"):
                for index, point in enumerate(getattr(segment, field)):
                    pp = f"{path}.{field}.{index}"
                    require(point.x + getattr(point, "w", 0) <= segment.width and point.y + getattr(point, "h", 0) <= segment.height, pp, "geometry outside segment bounds")
                    if isinstance(point, EnemySpawn):
                        reference(point.type, self.enemies, pp + ".type")
                        require(0 <= point.x + point.patrol_dx <= segment.width and 0 <= point.y + point.patrol_dy <= segment.height, pp, "patrol endpoint outside segment")
                    if isinstance(point, Wall):
                        wall_threshold(point, pp)
                    if isinstance(point, PuzzleSlot):
                        reference(point.type, self.puzzles, pp + ".type")
                    if isinstance(point, LootSpawn):
                        reference(point.type, self.items, pp + ".type")
        for index, pool in enumerate(self.loot_tables.supply_crates):
            for tid in pool:
                reference(tid, self.items, f"loot_tables.supply_crates.{index}")
        for tid in self.loot_tables.ground_variety:
            reference(tid, self.items, "loot_tables.ground_variety")
        return self


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def mutable_copy(value):
    """Thaw definitions into independent runtime/JSON dictionaries and lists."""
    if isinstance(value, Mapping):
        return {key: mutable_copy(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [mutable_copy(item) for item in value]
    return value


@dataclass(frozen=True)
class Catalog:
    definitions: Mapping
    classes: Mapping
    items: Mapping

    def client_view(self):
        # Authored layouts and all private puzzle state stay on the server.
        result = {key: mutable_copy(self.definitions[key]) for key in ("schema_version", "content_version", "missions", "enemies", "sounds", "music", "presentation", "world_rules")}
        result["classes"] = mutable_copy(self.classes)
        result["items"] = mutable_copy(self.items)
        result["rules"] = {key: mutable_copy(self.definitions["rules"][key]) for key in ("interaction", "combat", "audio")}
        return result


def validate_content(data, *, asset_root=ASSET_ROOT):
    schema = CatalogSchema.model_validate(data)
    raw = schema.model_dump(exclude_none=True)
    for identity, sound in schema.sounds.items():
        raw["sounds"][identity]["duration"] = sound.duration
    for identity, mission in schema.missions.items():
        raw["missions"][identity]["unlock_requirement"] = mission.unlock_requirement
    for path, asset in [(f"sounds.{identity}.asset", sound.asset) for identity, sound in schema.sounds.items()] + [(f"music.tracks.{identity}", asset) for identity, asset in schema.music.tracks.items()]:
        relative = PurePosixPath(asset)
        if relative.is_absolute() or ".." in relative.parts or "\\" in asset or ":" in asset or asset != str(relative):
            raise ValueError(f"{path}: asset must be a normalized relative path")
        target = (asset_root / asset).resolve()
        if not target.is_relative_to(asset_root.resolve()) or not target.is_file():
            raise ValueError(f"{path}: missing asset {asset!r}")
    for identity, asset in schema.presentation.assets.items():
        path = f"presentation.assets.{identity}.path"
        relative = PurePosixPath(asset.path)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in asset.path or ":" in asset.path or
                asset.path != str(relative) or relative.suffix.lower() not in (".svg", ".png", ".jpg", ".webp")):
            raise ValueError(f"{path}: image must be a normalized relative path")
        target = (asset_root / asset.path).resolve()
        if not target.is_relative_to(asset_root.resolve()) or not target.is_file():
            raise ValueError(f"{path}: missing image asset {asset.path!r}")
    items = {}
    for identity, definition in raw["items"].items():
        item = dict(definition)
        if "weapon_id" in item:
            item["weapon"] = raw["weapons"][item["weapon_id"]]
        if "effect_id" in item:
            item["effect"] = raw["effects"][item["effect_id"]]
            # Concrete legacy item consumers expect value.heal/sight_boost/loot.
            item["value"] = {key: item["effect"][key] for key in ("heal", "sight_boost", "loot") if key in item["effect"]}
        items[identity] = item
    classes = {}
    for identity, definition in raw["classes"].items():
        character = dict(definition)
        loadout = raw["loadouts"][character["loadout_id"]]
        weapon = items[loadout["primary_weapon"]]
        ability = items[loadout["special_ability"]]
        character["weapon"] = {**weapon["weapon"], "name": weapon["name"]}
        character["skill"] = {**ability["effect"], "id": ability["effect_id"], "name": ability["name"], "description": ability["description"]}
        classes[identity] = character
    return Catalog(freeze(raw), freeze(classes), freeze(items))


def load_catalog(path=CONTENT_PATH, *, asset_root=ASSET_ROOT):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        return validate_content(data, asset_root=asset_root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def public_payload(value):
    """Copy outgoing runtime data, hiding only the private power-puzzle answer."""
    result = mutable_copy(value)
    def sanitize(obj):
        if isinstance(obj, dict):
            if obj.get("type") == "power_reroute":
                obj.pop("target", None)
            for child in obj.values():
                sanitize(child)
        elif isinstance(obj, list):
            for child in obj:
                sanitize(child)
    sanitize(result)
    return result


def main():
    parser = argparse.ArgumentParser(description="Validate authored content without loading or writing runtime saves")
    parser.add_argument("path", nargs="?", default=str(CONTENT_PATH))
    args = parser.parse_args()
    try:
        catalog = load_catalog(args.path)
    except (ValueError, ValidationError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Valid content {catalog.definitions['content_version']} (schema {catalog.definitions['schema_version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# CLI execution intentionally validates only the requested file, not the default.
CATALOG = load_catalog(os.environ.get("SWARM_CONTENT_PATH", CONTENT_PATH))
RULES = CATALOG.definitions["rules"]
