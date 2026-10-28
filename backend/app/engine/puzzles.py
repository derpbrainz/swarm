"""Bounded mechanics. Definitions are immutable; data/session are runtime state."""

import random
import uuid
from pydantic import TypeAdapter

from backend.app.content import CATALOG, PuzzleDefinition, mutable_copy

DEFINITION_ADAPTER = TypeAdapter(PuzzleDefinition)


def power_generate(definition, rng):
    count = definition["node_count"]
    return {"type": "power_reroute", "nodes": [rng.choice([1, 2, 3]) for _ in range(count)], "target": [0] * count}


def signal_generate(definition, rng):
    symbols = list(definition["symbols"])
    return {"type": "signal_decrypt", "target_sequence": [rng.choice(symbols) for _ in range(definition["sequence_length"])], "progress": 0, "options": symbols}


def timed_generate(definition, rng):
    return {"type": "timed_input", "input": definition["input"], "duration": definition["duration"]}


GENERATORS = {"power_reroute": power_generate, "signal_decrypt": signal_generate, "timed_input": timed_generate}


def generate(puzzle_id, *, rng=None, catalog=CATALOG):
    definition = catalog.definitions["puzzles"][puzzle_id]
    return GENERATORS[definition["handler"]](definition, rng if rng is not None else random.Random(100))


def runtime_puzzle(puzzle, *, catalog=CATALOG):
    # Concrete persisted expanded layouts/answers remain authoritative and untouched.
    if "definition" not in puzzle:
        if puzzle["type"] not in catalog.definitions["puzzles"]:
            raise ValueError(f"Unknown puzzle definition {puzzle['type']}")
        puzzle["definition"] = mutable_copy(catalog.definitions["puzzles"][puzzle["type"]])
        puzzle["definition_id"] = puzzle["type"]
        if puzzle["definition"]["handler"] == "power_reroute":
            puzzle["definition"]["node_count"] = len(puzzle["data"]["nodes"])
        elif puzzle["definition"]["handler"] == "signal_decrypt":
            puzzle["definition"].update(sequence_length=len(puzzle["data"]["target_sequence"]), symbols=list(puzzle["data"]["options"]))
    definition = DEFINITION_ADAPTER.validate_python(puzzle["definition"])
    handler = definition.handler
    if handler not in GENERATORS:
        raise ValueError(f"Unknown puzzle handler {handler}")
    puzzle.setdefault("session", {"id": uuid.uuid4().hex, "status": "solved" if puzzle.get("solved") else "ready"})
    return puzzle


def power_action(puzzle, action, player_id, now):
    if action["action"] != "rotate":
        raise ValueError("Incompatible puzzle action")
    data = puzzle["data"]
    index = action["node_index"]
    if index >= len(data["nodes"]):
        raise ValueError("Unknown node index")
    data["nodes"][index] = (data["nodes"][index] + 1) % 4
    return data["nodes"] == data["target"], False


def signal_action(puzzle, action, player_id, now):
    if action["action"] != "input_symbol" or action["symbol"] not in puzzle["data"]["options"]:
        raise ValueError("Incompatible puzzle action or symbol")
    data = puzzle["data"]
    if action["symbol"] == data["target_sequence"][data["progress"]]:
        data["progress"] += 1
        return data["progress"] == len(data["target_sequence"]), False
    data["progress"] = 0
    return False, True


def timed_action(puzzle, action, player_id, now):
    session = puzzle["session"]
    if action["action"] == "start_attempt":
        if session["status"] != "ready":
            raise ValueError("Attempt already started or consumed")
        session.update(id=uuid.uuid4().hex, status="active", player_id=player_id, started_at=now,
                       deadline=now + puzzle["definition"]["duration"])
        return False, False
    if action["action"] != "timed_input":
        raise ValueError("Incompatible puzzle action")
    if action["attempt_id"] != session["id"] or player_id != session.get("player_id"):
        raise ValueError("Wrong attempt session or player")
    if session["status"] == "expired" or (session["status"] == "active" and now >= session["deadline"]):
        session["status"] = "expired"
        raise ValueError("Attempt expired")
    if session["status"] != "active":
        raise ValueError("Attempt replayed or consumed")
    session["status"] = "consumed"
    return action["symbol"] == puzzle["definition"]["input"], False


ACTIONS = {"power_reroute": power_action, "signal_decrypt": signal_action, "timed_input": timed_action}


def act(puzzle, action, player, now):
    definition = puzzle["definition"]
    if action.get("puzzle_session_id") is not None and action["puzzle_session_id"] != puzzle["session"]["id"]:
        raise ValueError("Wrong puzzle session")
    if action["action"] == "tech_bypass":
        if player["class_id"] != "tech" or not definition["allow_tech_bypass"]:
            raise ValueError("Hardware bypass capability unavailable")
        return True, False
    return ACTIONS[definition["handler"]](puzzle, action, player["id"], now)


def expire(puzzle, now):
    session = puzzle.get("session", {})
    if session.get("status") == "active" and now >= session["deadline"]:
        session["status"] = "expired"
        return True
    return False
