import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.engine.inventory import SharedStash
from backend.app.engine.level_store import LevelStore
from backend.app.engine.persistence import SaveError, read_json, recover_backup, write_snapshot, writer_lease
from backend.app.main import create_app


def test_legacy_migration_preserves_siblings_progress_and_sparse_maps(game_room, tmp_path):
    original = copy.deepcopy(game_room.level_store.levels)
    original["outpost_101"]["map_template"]["loot"] = []
    path = tmp_path / "legacy-levels.json"
    path.write_text(json.dumps(original))
    store = LevelStore(path)
    assert store.levels.keys() == original.keys()
    assert store.levels["outpost_101"]["map_template"]["loot"] == []
    for identity in original:
        assert store.levels[identity]["completed"] == original[identity]["completed"]
        assert store.levels[identity]["tested"] == original[identity]["tested"]
    store.save()
    assert read_json(path)["schema_version"] == 1
    assert json.loads(path.with_suffix(".json.bak").read_text()) == original


@pytest.mark.parametrize("contents", ["{", "[]", '{"medkit":null}', '{"medkit":"bad"}', '{"medkit":true}', '{"medkit":-1}', '{"medkit":NaN}', '{"medkit":1,"medkit":2}', '{"schema_version":99,"shared_stash":{}}'])
def test_invalid_stash_is_retained_not_reset(tmp_path, contents):
    path = tmp_path / "stash.json"
    path.write_text(contents)
    with pytest.raises(SaveError):
        SharedStash(path)
    assert path.read_text() == contents


def test_missing_runtime_save_imports_legacy_and_preserves_retired_ids(tmp_path):
    store = SharedStash(tmp_path / "stash.json")
    assert store.path.exists()
    store.stacks["retired_item"] = 3
    store.save()
    reloaded = SharedStash(store.path)
    assert reloaded.stacks["retired_item"] == 3
    assert all(item["template_id"] != "retired_item" for item in reloaded.get_stacked_list())


def test_empty_versioned_saves_do_not_reinitialize_on_startup(tmp_path, game_room):
    path = tmp_path / "empty.json"
    write_snapshot(path, "shared_stash", {})
    game_room.shared_stash = SharedStash(path)
    game_room.initialize()
    assert game_room.shared_stash.stacks == {}


def test_failed_atomic_replace_retains_original_and_cleans_temporary_file(tmp_path):
    path = tmp_path / "stash.json"
    write_snapshot(path, "shared_stash", {"medkit": 1})
    original = path.read_bytes()
    with patch("backend.app.engine.persistence.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(SaveError):
            write_snapshot(path, "shared_stash", {"medkit": 2})
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".stash.json.*"))


def test_unwritable_path_does_not_publish_partial_save(tmp_path):
    parent = tmp_path / "not-a-directory"
    parent.write_text("retained")
    with pytest.raises(SaveError):
        write_snapshot(parent / "stash.json", "shared_stash", {"medkit": 2})
    assert parent.read_text() == "retained"


def test_backup_recovery_is_explicit_and_retains_damaged_input(tmp_path):
    path = tmp_path / "stash.json"
    write_snapshot(path, "shared_stash", {"medkit": 1})
    write_snapshot(path, "shared_stash", {"medkit": 2})
    path.write_text("truncated{")
    with pytest.raises(SaveError):
        SharedStash(path)
    recover_backup(path, "shared_stash")
    assert SharedStash(path).stacks == {"medkit": 1}
    assert path.with_suffix(".json.damaged").read_text() == "truncated{"


def test_writer_lease_blocks_second_application_and_releases_after_shutdown(game_room):
    paths = [game_room.level_store.data_file, game_room.shared_stash.path]
    with writer_lease(paths):
        with pytest.raises(SaveError, match="Another server"):
            with TestClient(create_app(game_room)):
                pass
    with TestClient(create_app(game_room)) as client:
        assert client.get("/api/config").status_code == 200


def test_runtime_directory_is_configurable_without_import_writes(monkeypatch, tmp_path):
    monkeypatch.setenv("SWARM_SAVE_DIR", str(tmp_path))
    store = LevelStore(load=False)
    assert store.data_file == tmp_path / "levels.json"
    assert not store.data_file.exists()


def test_invalid_level_retains_entire_original_file(game_room, tmp_path):
    records = copy.deepcopy(game_room.level_store.levels)
    records["outpost_101"]["map_template"].pop("walls")
    path = tmp_path / "invalid-levels.json"
    path.write_text(json.dumps(records))
    before = path.read_bytes()
    with pytest.raises(SaveError, match="explicit repair"):
        LevelStore(path)
    assert path.read_bytes() == before


def test_unreadable_save_is_reported_not_replaced(tmp_path):
    path = tmp_path / "stash.json"
    path.write_text('{"medkit":7}')
    with patch.object(Path, "read_text", side_effect=PermissionError("read denied")):
        with pytest.raises(SaveError, match="original retained"):
            SharedStash(path)
    assert path.read_text() == '{"medkit":7}'
