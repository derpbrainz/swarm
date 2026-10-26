from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.app.engine.map_builder import build_mission_map

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "levels.json"


class LevelStore:
    def __init__(self, data_file: Path = DATA_FILE):
        self.data_file = Path(data_file)
        self.levels = {}

        if self.data_file.exists():
            try:
                loaded = json.loads(self.data_file.read_text(encoding="utf-8"))
                # Auto-upgrade persisted levels if their loot/crate schema is outdated
                outdated = False
                for rec in loaded.values():
                    mt = rec.get("map_template", {})
                    lt_list = mt.get("loot", [])
                    if len(lt_list) < 18 or not mt.get("crates") or (lt_list and "type" not in lt_list[0]):
                        outdated = True
                        break
                if not outdated:
                    self.levels = loaded
            except Exception:
                self.levels = {}

        if not self.levels:
            for mission_id, seed in (
                ("outpost", 101),
                ("outpost", 154),
                ("biolab", 202),
                ("biolab", 255),
                ("reactor", 303),
            ):
                self.generate_level(mission_id, seed, tested=True)
            self.save()

    def save(self) -> None:
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.data_file.write_text(json.dumps(self.levels, indent=2), encoding="utf-8")

    def _level_id(self, mission_id: str, seed: int) -> str:
        base = f"{mission_id}_{seed}"
        if base not in self.levels:
            return base
        suffix = 1
        while f"{base}_{suffix}" in self.levels:
            suffix += 1
        return f"{base}_{suffix}"

    def generate_level(self, mission_id: str, seed: int | None = None, tested: bool = False) -> dict:
        from backend.app.config.missions import MISSIONS

        mission_cfg = MISSIONS.get(mission_id, MISSIONS["outpost"])
        actual_seed = int(seed) if seed is not None else (100 + (len(self.levels) + 1) * 53)
        map_template = build_mission_map(mission_id, seed=actual_seed, test_mode=False)
        level_id = self._level_id(mission_id, actual_seed)
        same_mission_count = sum(1 for r in self.levels.values() if r.get("mission_id") == mission_id) + 1

        record = {
            "level_id": level_id,
            "mission_id": mission_id,
            "name": f"{mission_cfg['name']} #{same_mission_count}",
            "difficulty": mission_cfg.get("difficulty", "Standard"),
            "seed": actual_seed,
            "tested": bool(tested),
            "completed": False,
            "status": "verified" if tested else "testing",
            "segment_count": len(map_template.get("segments", [])),
            "connector_count": len(map_template.get("connectors", [])),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "map_template": map_template,
        }

        self.levels[level_id] = record
        self.save()
        return record

    def is_mission_unlocked(self, mission_id: str) -> tuple[bool, str | None]:
        from backend.app.config.missions import MISSIONS

        m_cfg = MISSIONS.get(mission_id)
        if not m_cfg:
            return False, "Unknown mission"
        prereq = m_cfg.get("prerequisite_mission")
        if not prereq:
            return True, None
        has_completed_easier = any(
            r.get("mission_id") in ("outpost", "biolab") and r.get("completed", False)
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
            m_ok, m_reason = self.is_mission_unlocked(rec["mission_id"])
            if not m_ok:
                return False, m_reason
            same_mission = [r for r in self.levels.values() if r.get("mission_id") == rec["mission_id"]]
            for idx, item in enumerate(same_mission):
                if item["level_id"] == rec["level_id"] and idx > 0:
                    prev = same_mission[idx - 1]
                    if rec["mission_id"] == "outpost":
                        if not prev.get("tested", False) and not prev.get("completed", False):
                            return False, f"Complete '{prev['name']}' testing phase first to unlock {rec['name']}"
                    else:
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
                "tested": bool(record.get("tested", False)),
                "completed": bool(record.get("completed", False)),
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

        candidates = [record for record in self.levels.values() if record.get("mission_id") == level_or_mission_id]
        if candidates:
            unlocked_cands = [r for r in candidates if self.is_level_unlocked(r["level_id"])[0]]
            pool = unlocked_cands or candidates
            return pool[0]

        return self.generate_level(level_or_mission_id)

    def instantiate_map(self, level_or_mission_id: str, force_test_mode: bool | None = None) -> dict:
        record = self.get_level_record(level_or_mission_id)
        map_copy = copy.deepcopy(record.get("map_template", {}))
        if not map_copy.get("crates") or len(map_copy.get("loot", [])) < 15:
            map_copy = build_mission_map(record["mission_id"], seed=record.get("seed", 101), test_mode=False)
            record["map_template"] = copy.deepcopy(map_copy)
            self.save()
        for lt in map_copy.get("loot", []):
            if "type" not in lt:
                lt["type"] = lt.get("template_id") or lt.get("item_type") or "credits"

        is_test = force_test_mode if force_test_mode is not None else (not record["tested"])

        map_copy["level_id"] = record["level_id"]
        map_copy["level_name"] = record["name"]
        map_copy["level_tested"] = record["tested"]
        map_copy["test_mode"] = bool(is_test)

        if is_test:
            map_copy["enemies"] = []

        return map_copy

    def mark_tested(self, level_id: str) -> bool:
        record = self.levels.get(level_id)
        if not record:
            return False

        record["tested"] = True
        record["status"] = "verified"
        self.save()
        return True

    def mark_completed(self, level_id: str) -> bool:
        record = self.levels.get(level_id)
        if not record:
            return False

        record["tested"] = True
        record["completed"] = True
        record["status"] = "verified"
        self.save()
        return True


level_store = LevelStore()