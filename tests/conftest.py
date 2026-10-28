import random
import shutil
from pathlib import Path

import pytest

from backend.app.engine.game_loop import GameRoom
from backend.app.engine.inventory import SharedStash
from backend.app.engine.level_store import LevelStore
from backend.app.main import create_app


@pytest.fixture
def game_room(tmp_path):
    source = Path(__file__).resolve().parents[1] / "backend" / "app" / "data"
    shutil.copyfile(source / "levels.json", tmp_path / "levels.json")
    shutil.copyfile(source / "shared_stash.json", tmp_path / "stash.json")
    return GameRoom(LevelStore(tmp_path / "levels.json"), SharedStash(tmp_path / "stash.json"), rng=random.Random(0))


@pytest.fixture
def game_app(game_room):
    return create_app(game_room)
