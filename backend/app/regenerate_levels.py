"""Explicit local reset: build every fresh level before replacing either catalogue."""

import tempfile
from pathlib import Path

from backend.app.engine.level_store import DATA_FILE, LevelStore
from backend.app.engine.persistence import runtime_directory, writer_lease


def main():
    destinations = [DATA_FILE, runtime_directory() / "levels.json"]
    with writer_lease(destinations):
        with tempfile.TemporaryDirectory(prefix="swarm-regenerate-") as directory:
            candidate = LevelStore(Path(directory) / "levels.json", load=False)
            for mission, seed in (("outpost", 101), ("outpost", 154), ("biolab", 202),
                                  ("biolab", 255), ("reactor", 303), ("outpost", 418)):
                record = candidate.generate_level(mission, seed)
                print(f"Generated {record['level_id']}: {record['segment_count']} segments, enemies retained")
            for destination in dict.fromkeys(destinations):
                output = LevelStore(destination, load=False)
                output.levels = candidate.levels
                output.save()
                print(f"Replaced {destination}; previous catalogue retained as .bak")


if __name__ == "__main__":
    main()
