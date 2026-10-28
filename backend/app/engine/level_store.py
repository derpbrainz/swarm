from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config.items import ITEM_TEMPLATES
from backend.app.content import CATALOG, mutable_copy
from backend.app.engine.map_builder import build_mission_map, validate_generated_map
from backend.app.engine.wfc import GENERATOR_VERSION
from backend.app.engine.puzzles import runtime_puzzle
from backend.app.config.missions import MISSIONS
from backend.app.engine.persistence import SaveError, read_snapshot, runtime_directory, write_snapshot

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "levels.json"


class LevelStore:
    def __init__(self, data_file: Path | None = None, *, load: bool = True):
        self.data_file = Path(data_file) if data_file is not None else runtime_directory() / "levels.json"
        self.levels = {}
        self.loaded = False
        if load:
            self.load()

    def load(self) -> None:
        source = self.data_file if self.data_file.exists() else DATA_FILE
        records = copy.deepcopy(read_snapshot(source, "levels"))
        for level_id, record in records.items():
            if not isinstance(record, dict) or record.get("level_id") != level_id:
                raise SaveError(f"{source}: invalid level record {level_id!r}; original retained")
            if record.get("mission_id") not in MISSIONS or type(record.get("seed")) is not int:
                raise SaveError(f"{source}: invalid mission/seed in {level_id}; original retained")
            template = record.get("map_template")
            required = {"segments", "walls", "puzzles", "enemies", "player_spawn_points", "spaceship", "extraction_zone"}
            if not isinstance(template, dict) or not required.issubset(template):
                raise SaveError(f"{source}: incomplete map in {level_id}; explicit repair required")
            for flag in ("completed", "broken"):
                record.setdefault(flag, False)
                if type(record[flag]) is not bool:
                    raise SaveError(f"{source}: invalid {flag} in {level_id}")
            template.setdefault("loot", [])
            template.setdefault("crates", [])
            for item in template["loot"]:
                if "type" not in item:
                    identity = item.get("template_id") or item.get("item_type")
                    if not identity:
                        raise SaveError(f"{source}: missing item type in {level_id}")
                    item["type"] = identity
            record["segment_count"] = len(template["segments"])
            record["connector_count"] = len(template.get("connectors", []))
            record["tested"] = record["completed"] and not record["broken"]
            record["status"] = "broken" if record["broken"] else "verified" if record["completed"] else "uncompleted"
            record.setdefault("failure_reports", [])
            if not isinstance(record["failure_reports"], list) or any(
                not isinstance(report, dict) or not isinstance(report.get("reason"), str) or
                not report["reason"].strip() or len(report["reason"]) > 1000
                for report in record["failure_reports"]
            ):
                raise SaveError(f"{source}: invalid failure reports in {level_id}")
        self.levels = records
        self.loaded = True
        if not self.data_file.exists():
            self.save()

    def save(self) -> None:
        write_snapshot(self.data_file, "levels", self.levels)

    def _level_id(self, mission_id: str, seed: int) -> str:
        base = f"{mission_id}_{seed}"
        if base not in self.levels:
            return base
        suffix = 1
        while f"{base}_{suffix}" in self.levels:
            suffix += 1
        return f"{base}_{suffix}"

    def generate_level(self, mission_id: str, seed: int | None = None) -> dict:
        from backend.app.config.missions import MISSIONS

        mission_cfg = MISSIONS[mission_id]
        actual_seed = int(seed) if seed is not None else (100 + (len(self.levels) + 1) * 53)
        map_template = build_mission_map(mission_id, seed=actual_seed)
        validate_generated_map(map_template)
        level_id = self._level_id(mission_id, actual_seed)
        same_mission_count = sum(1 for r in self.levels.values() if r.get("mission_id") == mission_id) + 1

        record = {
            "level_id": level_id,
            "mission_id": mission_id,
            "name": f"{mission_cfg['name']} #{same_mission_count}",
            "difficulty": mission_cfg.get("difficulty", "Standard"),
            "seed": actual_seed,
            "requested_seed": actual_seed,
            "resolved_seed": map_template["resolved_seed"],
            "generator_version": map_template["generator_version"],
            "content_version": map_template["content_version"],
            "geometry_validated": True,
            "generation_attempts": map_template["generation_attempts"],
            "generation_diagnostics": copy.deepcopy(map_template["generation_diagnostics"]),
            "tested": False,
            "completed": False,
            "broken": False,
            "failure_reports": [],
            "status": "uncompleted",
            "segment_count": len(map_template.get("segments", [])),
            "connector_count": len(map_template.get("connectors", [])),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "map_template": map_template,
        }

        self.levels[level_id] = record
        try:
            self.save()
        except SaveError:
            self.levels.pop(level_id)
            raise
        return record

    def is_mission_unlocked(self, mission_id: str) -> tuple[bool, str | None]:
        from backend.app.config.missions import MISSIONS

        m_cfg = MISSIONS.get(mission_id)
        if not m_cfg:
            return False, "Unknown mission"
        prereq = m_cfg["prerequisites"]
        if not prereq:
            return True, None
        has_completed_easier = any(
            r.get("mission_id") in prereq and r.get("completed", False) and not r.get("broken", False)
            for r in self.levels.values()
        )
        if not has_completed_easier:
            return (
                False,
                m_cfg.get("unlock_requirement")
                or "Complete an easier mission ('Derelict Outpost' or 'Bio-Lab Containment') first to unlock",
            )
        return True, None

    def is_level_unlocked(self, level_or_mission_id: str) -> tuple[bool, str | None]:
        if level_or_mission_id in self.levels:
            rec = self.levels[level_or_mission_id]
            if rec.get("broken", False):
                return False, "Broken level retained for repair; choose another level"
            m_ok, m_reason = self.is_mission_unlocked(rec["mission_id"])
            if not m_ok:
                return False, m_reason
            same_mission = [r for r in self.levels.values() if r.get("mission_id") == rec["mission_id"] and not r.get("broken", False)]
            for idx, item in enumerate(same_mission):
                if item["level_id"] == rec["level_id"] and idx > 0:
                    prev = same_mission[idx - 1]
                    if not prev.get("completed", False):
                        return False, f"Complete easier level '{prev['name']}' first to unlock {rec['name']}"
            return True, None
        return self.is_mission_unlocked(level_or_mission_id)

    def list_levels(self) -> list[dict]:
        summaries = []
        for record in self.levels.values():
            segments = record.get("map_template", {}).get("segments", [])
            unlocked, lock_reason = self.is_level_unlocked(record["level_id"])
            summaries.append({
                "level_id": record["level_id"],
                "mission_id": record["mission_id"],
                "name": record["name"],
                "difficulty": record["difficulty"],
                "seed": record["seed"],
                "tested": bool(record.get("completed", False) and not record.get("broken", False)),
                "completed": bool(record.get("completed", False)),
                "broken": bool(record.get("broken", False)),
                "failure_reports": copy.deepcopy(record.get("failure_reports", [])),
                "unlocked": unlocked,
                "lock_reason": lock_reason,
                "status": record["status"],
                "segment_count": record["segment_count"],
                "connector_count": record["connector_count"],
                "segments": [
                    {
                        "id": segment.get("id"),
                        "grid_x": segment.get("grid_x"),
                        "grid_y": segment.get("grid_y"),
                        "grid_w": segment.get("grid_w"),
                        "grid_h": segment.get("grid_h"),
                    }
                    for segment in segments
                ],
            })
        return summaries

    def get_level_record(self, level_or_mission_id: str) -> dict:
        if level_or_mission_id in self.levels:
            return self.levels[level_or_mission_id]

        candidates = [record for record in self.levels.values() if record.get("mission_id") == level_or_mission_id and not record.get("broken", False)]
        if candidates:
            unlocked_cands = [r for r in candidates if self.is_level_unlocked(r["level_id"])[0]]
            pool = unlocked_cands or candidates
            return pool[0]

        return self.generate_level(level_or_mission_id)

    def instantiate_map(self, level_or_mission_id: str) -> dict:
        record = self.get_level_record(level_or_mission_id)
        if record.get("broken", False):
            raise ValueError("Broken levels are retained for repair, not playable")
        map_copy = copy.deepcopy(record.get("map_template", {}))
        # Legacy snapshots are deliberately not reclassified/repaired at mission start.
        version = record.get("generator_version", map_copy.get("generator_version"))
        content_version = record.get("content_version", map_copy.get("content_version"))
        if version == GENERATOR_VERSION and content_version == CATALOG.definitions["content_version"]:
            validate_generated_map(map_copy)
        world_id = uuid.uuid4().hex
        map_copy["world_id"] = world_id
        map_copy["world_kind"] = "mission"
        map_copy["world_name"] = map_copy["mission_name"]
        map_copy["capabilities"] = mutable_copy(CATALOG.definitions["world_rules"]["mission"])
        map_copy["geometry_revision"] = 0
        # Retained layouts already carry explicit types/references; never infer from names.
        door_ids = {p.get("door_id") for p in map_copy.get("puzzles", [])}
        door_ids.add(map_copy.get("spaceship", {}).get("door_wall_id"))
        for wall in map_copy.get("walls", []):
            wall.setdefault("is_door", wall.get("type") in ("door", "ship_ramp") or wall.get("id") in door_ids)
            wall.setdefault("material", "door" if wall["is_door"] else "solid")
        for item in map_copy.get("loot", []) + [it for cr in map_copy.get("crates", []) for it in cr.get("items", [])]:
            identity = item.get("template_id") or item.get("item_type") or item.get("type")
            if identity not in ITEM_TEMPLATES:
                raise ValueError(f"Unknown item template in {record['level_id']}: {identity}")
            item["spawn_id"] = item.get("id")
            item["world_id"] = world_id
            item["id"] = f"{world_id}_{uuid.uuid4().hex}"
            item["type"] = item["template_id"] = item["item_type"] = identity
            if "weapon" in item:
                item.setdefault("weapon_state", {"ammo": int(item["weapon"]["mag_size"]), "reload_timer": 0.0})
        for puzzle in map_copy.get("puzzles", []) + [c["puzzle"] for c in map_copy.get("crates", []) if c.get("puzzle")]:
            runtime_puzzle(puzzle)

        map_copy["level_id"] = record["level_id"]
        map_copy["level_name"] = record["name"]
        map_copy["level_completed"] = record["completed"]
        map_copy.pop("test_mode", None)
        map_copy.pop("testing_enemy_ids", None)
        map_copy.pop("level_tested", None)

        return map_copy

    def mark_completed(self, level_id: str) -> bool:
        record = self.levels.get(level_id)
        if not record or record.get("broken", False):
            return False

        self._update_progress(record, tested=True, completed=True, status="verified")
        return True

    def mark_broken(self, level_id: str, reason: str, *, player_id: str, player_name: str, world_id: str, simulation_time: float) -> bool:
        record = self.levels.get(level_id)
        if not record or record.get("completed") or record.get("broken"):
            return False
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
            return False
        report = {"reason": reason.strip(), "player_id": player_id, "player_name": player_name,
                  "world_id": world_id, "simulation_time": simulation_time,
                  "reported_at": datetime.now(timezone.utc).isoformat()}
        self._update_progress(record, broken=True, tested=False, status="broken",
                              failure_reports=[*record.get("failure_reports", []), report])
        return True

    def _update_progress(self, record, **changes):
        previous = {key: copy.deepcopy(record[key]) for key in changes}
        record.update(changes)
        try:
            self.save()
        except SaveError:
            record.update(previous)
            raise
