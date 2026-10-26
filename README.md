# VALKYRIE PROTOCOL — Co-op Isometric Extraction Game

## 1. Project Overview & Game Concept

VALKYRIE PROTOCOL is a browser-based, real-time, co-op PvE extraction game with an isometric tactical view. Players join a shared lobby, choose character classes, and follow a squad leader through mission segments. The backend is authoritative and runs a continuous simulation loop at 30 Hz. Clients connect over WebSockets, receive shared world state, send input, and render the world using an isometric canvas renderer.

### Core Gameplay Loop

1. Players connect to the server through `/ws`.
2. The first connected player becomes the squad leader.
3. Players choose a class.
4. The leader selects a mission.
5. The leader launches the dropship.
6. The dropship ramp opens after a short docking phase.
7. Players move through mission segments, solve terminal puzzles, and unlock doors.
8. Enemies patrol, hear sounds, see players through sight cones, alert nearby enemies, and attack.
9. Players fight enemies, use class skills, reload weapons, revive downed teammates, and complete objectives.
10. When all objectives are solved, the extraction zone activates.
11. The squad wins when all living players reach the extraction zone.
12. The squad loses if all players are downed.

### Multiplayer / Co-op Nature

The game is designed as a shared co-op session:

- One authoritative `GameRoom` instance manages all players, enemies, projectiles, puzzles, deployables, and mission state.
- Every connected WebSocket client receives the same authoritative state broadcast.
- Shared team vision is implemented through the `visible` flag on enemies.
- An enemy is visible if at least one living player, active sonar, or active turret can see it.
- The frontend renders fog of war based on player visibility polygons.
- Leader privileges are limited to mission selection and mission launch.

### Isometric View

The frontend uses an isometric coordinate system:

- World coordinates are 2D game-space coordinates.
- Screen coordinates are projected using `worldToIso`.
- Mouse screen coordinates are inverse-projected back to world coordinates using `isoToWorld`.
- WASD movement is converted from screen-aligned input into world-space movement.
- Depth sorting is performed by `x + y` so walls, enemies, players, turrets, and projectiles render in a believable 2.5D order.

---

## 2. Repository Structure & File Map

```text
/workspace
├── run.sh
├── backend/
│   └── app/
│       ├── main.py
│       ├── config/
│       │   ├── classes.py
│       │   └── missions.py
│       └── engine/
│           ├── game_loop.py
│           ├── map_builder.py
│           ├── physics.py
│           └── ai.py
├── frontend/
│   └── src/
│       ├── index.html
│       ├── styles.css
│       └── js/
│           ├── main.js
│           ├── iso.js
│           └── renderer.js
└── tests/
    └── test_game.py
```

### File Responsibilities

| File | Responsibility |
|---|---|
| `run.sh` | Launches the FastAPI server with Uvicorn on `http://localhost:8000`. |
| `backend/app/main.py` | FastAPI application, WebSocket endpoint, static file mount, simulation loop startup, broadcast helper, and HTTP config endpoint. |
| `backend/app/config/classes.py` | Defines player classes, weapons, skills, passives, and enemy types. |
| `backend/app/config/missions.py` | Defines mission sequences, segment templates, puzzle definitions, enemy spawns, and difficulty multipliers. |
| `backend/app/engine/game_loop.py` | Authoritative `GameRoom` class. Handles lobby state, player input, combat, skills, puzzles, deployables, projectiles, victory/defeat, and snapshot serialization. |
| `backend/app/engine/map_builder.py` | Builds a mission map from segment templates. Generates walls, doors, puzzles, enemies, spaceship, spawn points, and extraction zone. |
| `backend/app/engine/physics.py` | Circle-vs-AABB collision, sub-stepped movement, line-of-sight, raycasting, and point-in-rect checks. |
| `backend/app/engine/ai.py` | Enemy perception, hearing, sight cones, patrol/alert/engage state machine, squad alerting, melee attacks, and ranged attacks. |
| `frontend/src/index.html` | Lobby UI, game canvas, HUD, puzzle modal, mission end overlay, and script loading. |
| `frontend/src/styles.css` | UI styling for lobby, HUD, cards, modal, buttons, and game screen. Referenced by `index.html`. |
| `frontend/src/js/main.js` | WebSocket client, input loop, UI rendering, HUD updates, puzzle modal logic, keyboard/mouse handling, and render loop. |
| `frontend/src/js/iso.js` | Isometric coordinate math, inverse projection, screen-input-to-world movement, segment intersection, and visibility polygon generation. |
| `frontend/src/js/renderer.js` | Canvas renderer for floor segments, walls, doors, spaceship, extraction zone, puzzles, enemies, players, projectiles, deployables, sound rings, and fog of war. |
| `tests/test_game.py` | Unit and integration tests for map generation, physics, lobby flow, WebSocket behavior, and enemy AI. |

---

## 3. Architecture & Data Flow

### High-Level Architecture

```text
Browser Client
    │
    │ WebSocket /ws
    ▼
FastAPI main.py
    │
    ├── clients: dict[str, WebSocket]
    ├── broadcast_json()
    ├── simulation_loop()
    │
    ▼
GameRoom
    │
    ├── players
    ├── join_order
    ├── leader_id
    ├── phase
    ├── map_data
    ├── projectiles
    ├── sound_events
    ├── recent_sounds
    ├── deployables
    ├── events_log
    │
    ├── game_loop.py
    │   ├── player movement
    │   ├── firing
    │   ├── reload
    │   ├── skills
    │   ├── deployables
    │   ├── projectiles
    │   ├── puzzle completion
    │   ├── victory/defeat
    │   └── snapshot serialization
    │
    ├── ai.py
    │   ├── hearing
    │   ├── sight cone
    │   ├── line-of-sight
    │   ├── patrol/alert/engage
    │   └── enemy attacks
    │
    ├── physics.py
    │   ├── circle-vs-AABB collision
    │   ├── sub-stepped movement
    │   ├── segment intersection
    │   └── raycast
    │
    └── map_builder.py
        ├── segment stitching
        ├── walls
        ├── doors
        ├── puzzles
        ├── enemies
        ├── spaceship
        └── extraction zone
```

---

## 4. Backend Architecture

### FastAPI Server and WebSocket Hub

`backend/app/main.py` creates a FastAPI app and mounts the frontend static files.

Key responsibilities:

- Starts a background simulation loop in the FastAPI lifespan.
- Maintains a global `clients` dictionary mapping player IDs to WebSocket connections.
- Broadcasts JSON state to all connected clients.
- Handles WebSocket connection lifecycle.
- Sends a `welcome` message on connection.
- Processes client messages and mutates the authoritative `GameRoom`.
- Broadcasts updated state after most actions.
- Serves `/api/config`.
- Serves `index.html` at `/`.

### Simulation Loop

The simulation loop runs at 30 Hz:

```python
tick_rate = 30.0
dt = 1.0 / tick_rate
```

Each tick:

1. Calls `room.step(dt)`.
2. Builds a snapshot.
3. Broadcasts the snapshot to all connected clients.

The loop is started in the FastAPI lifespan and cancelled on shutdown.

### State Management

The authoritative state lives in `GameRoom`.

Important fields:

| Field | Purpose |
|---|---|
| `players` | Current player objects. |
| `join_order` | Order players joined, used for leader assignment. |
| `leader_id` | Current squad leader. |
| `selected_mission_id` | Mission selected by the leader. |
| `phase` | `lobby`, `dropship`, `active`, `victory`, or `defeat`. |
| `dropship_timer` | Time until the dropship ramp opens. |
| `mission_time` | Mission elapsed time. |
| `map_data` | Generated mission map. |
| `projectiles` | Active projectiles. |
| `sound_events` | Sounds processed during the current tick. |
| `recent_sounds` | Recent sounds retained for frontend visualization. |
| `deployables` | Turrets, healing fields, and sonar pulses. |
| `sonar_timer` | Active sonar visibility timer. |
| `events_log` | Recent mission events shown in the HUD. |

### Game Phases

| Phase | Meaning |
|---|---|
| `lobby` | Players can choose classes and the leader can choose a mission. |
| `dropship` | Mission has started, but the dropship ramp is still opening. |
| `active` | Players can move, fight, solve puzzles, and extract. |
| `victory` | All living players reached the extraction zone. |
| `defeat` | All players are downed. |

---

## 5. Physics & Line-of-Sight

`backend/app/engine/physics.py` provides collision and visibility primitives.

### Circle vs AABB Collision

`circle_vs_aabb` resolves overlap between a circle and an axis-aligned rectangle.

Used for:

- Player collision with walls.
- Enemy collision with walls.
- Turret placement collision.
- Shockwave shield enemy pushing.

### Sub-Stepped Movement

`move_with_collisions` prevents tunneling through thin walls by splitting movement into smaller steps.

Important behavior:

- Movement is divided into sub-steps based on distance and entity radius.
- Each sub-step resolves collisions against active walls.
- Inactive walls are ignored.

### Line-of-Sight

`has_line_of_sight` checks whether a segment between two points intersects any active sight-blocking wall.

Used for:

- Enemy sight checks.
- Turret target visibility.
- Shared co-op vision.
- Frontend visibility polygons.

### Raycasting

`raycast_walls` casts a ray from an origin in a direction up to a maximum distance.

Used for:

- Visibility polygon generation.
- Optional sight-only raycasting.

### Point-in-Rect

`point_in_rect` checks whether a point lies inside a rectangle.

Used for:

- Extraction zone checks.

---

## 6. Procedural Map Generation

`backend/app/engine/map_builder.py` stitches mission segment templates into a continuous world map.

### Segment Templates

Defined in `backend/app/config/missions.py`.

Each segment can contain:

- Width and height.
- Theme.
- Interior walls.
- Exit door.
- Puzzle terminal.
- Enemy spawns.
- Patrol waypoints.

### Generated Map Structure

`build_mission_map()` returns a dictionary containing:

| Key | Contents |
|---|---|
| `mission_id` | Selected mission ID. |
| `mission_name` | Mission display name. |
| `width` | Total stitched map width. |
| `height` | Fixed map height. |
| `segments` | Segment metadata. |
| `walls` | Perimeter walls, interior walls, doors, spaceship walls. |
| `puzzles` | Puzzle terminals with interactive data. |
| `enemies` | Enemy instances with patrol waypoints. |
| `spaceship` | Dropship position and ramp state. |
| `player_spawn_points` | Spawn locations inside the dropship. |
| `extraction_zone` | Extraction rectangle. |

### Door and Puzzle Logic

Puzzle terminals unlock doors when solved.

When all puzzles are solved:

- The extraction zone becomes active.
- The HUD objective changes to extraction.
- Victory occurs when all living players are inside the extraction zone.

### Puzzle Types

#### `power_reroute`

State:

```json
{
  "type": "power_reroute",
  "nodes": [1, 2, 3, 0],
  "target": [0, 0, 0, 0]
}
```

Action:

```json
{
  "action": "rotate",
  "node_index": 0
}
```

Solving condition:

- All nodes equal `0`.

#### `signal_decrypt`

State:

```json
{
  "type": "signal_decrypt",
  "target_sequence": ["A7", "C4", "F9", "E2"],
  "progress": 0,
  "options": ["A7", "C4", "F9", "E2", "B5", "D8"]
}
```

Action:

```json
{
  "action": "input_symbol",
  "symbol": "A7"
}
```

Solving condition:

- Player enters the target sequence in order.
- Wrong symbol resets progress and emits an alarm sound.

---

## 7. Enemy Swarm AI

`backend/app/engine/ai.py` implements enemy perception and behavior.

### Enemy States

| State | Behavior |
|---|---|
| `patrol` | Moves between waypoints. |
| `alerted` | Investigates a sound or last known position. |
| `engaged` | Attacks or chases a visible player. |

### Perception

Enemies can perceive players through:

1. Sound events.
2. Sight cones.
3. Line-of-sight.
4. Close proximity.

### Hearing

Sound events include:

- Footsteps.
- Gunfire.
- Skills.
- Alarm triggers.

Enemy hearing radius is multiplied by `hearing_multiplier`.

### Sight

Enemy sight checks use:

- `sight_range`.
- `sight_angle`.
- Line-of-sight against walls.
- Close-proximity detection.

### Squad Alerting

When an enemy sees a player, nearby enemies within 4.5 units can become alerted.

### Combat

Enemy behavior depends on `projectile_speed`:

- If `projectile_speed` is `0`, the enemy performs a melee attack.
- If `projectile_speed` is greater than `0`, the enemy fires a projectile.

Vanguard has damage reduction behavior:

- Shockwave shield reduces incoming projectile damage.
- Vanguard passive reduces melee damage when facing enemies directly.

---

## 8. Player Classes, Weapons, Abilities & Progression

`backend/app/config/classes.py` defines player classes and enemy types.

### Player Classes

| Class | Role |
|---|---|
| `vanguard` | Frontline breacher, shotgun, shockwave shield. |
| `tech` | Field engineer, SMG, auto turret, hardware bypass. |
| `medic` | Combat medic, carbine, nano healing field. |
| `recon` | Silent scout, suppressed rifle, sonar pulse. |

### Class Data

Each class defines:

- `id`
- `name`
- `title`
- `color`
- `max_hp`
- `speed`
- `footstep_radius`
- `sight_range`
- `weapon`
- `skill`
- `passive`

### Weapon Data

Each weapon defines:

- `name`
- `damage`
- `fire_rate`
- `projectile_speed`
- `range`
- `pellets`
- `spread`
- `sound_radius`
- `mag_size`
- `reload_time`
- `color`

### Skills

| Skill | Class | Effect |
|---|---|---|
| `shockwave_shield` | Vanguard | Blocks projectiles, reduces melee damage, pushes enemies, damages nearby enemies. |
| `auto_turret` | Tech | Deploys a turret that fires at visible enemies. |
| `nano_field` | Medic | Creates a healing field and revives nearby downed allies. |
| `sonar_pulse` | Recon | Reveals enemies in a wide radius. |

### Passives

Passives are mostly represented in class metadata and some combat logic:

- Vanguard: reduced damage when facing enemies directly.
- Tech: hardware bypass on puzzle terminals.
- Medic: faster healing when allies are below half health.
- Recon: quieter footsteps while moving slowly.

---

## 9. Missions, Objectives & Extraction Logic

`backend/app/config/missions.py` defines missions and segment templates.

### Mission Data

Each mission defines:

- `id`
- `name`
- `description`
- `difficulty`
- `segment_sequence`
- `enemy_multiplier`

### Mission Flow

1. Leader selects mission.
2. Leader launches mission.
3. `GameRoom.start_mission()` builds the map.
4. Players spawn at dropship spawn points.
5. Dropship phase begins.
6. After `dropship_timer` expires, the ramp opens.
7. Players solve puzzles.
8. Solved puzzles unlock doors.
9. When all puzzles are solved, extraction activates.
10. Victory occurs when all living players are inside the extraction zone.

### Victory Conditions

Victory requires:

- At least one living player.
- Extraction zone active.
- All living players inside the extraction zone.

### Defeat Conditions

Defeat occurs when:

- There are players.
- No living players remain.

---

## 10. Frontend Isometric Engine & Client Logic

### `frontend/src/js/iso.js`

Responsibilities:

- Isometric projection.
- Inverse projection from screen to world.
- Screen-aligned WASD movement conversion.
- Segment intersection.
- Visibility polygon generation.

Key constants:

```js
TILE_W = 64
TILE_H = 32
WALL_HEIGHT_PX = 42
```

Key functions:

| Function | Purpose |
|---|---|
| `worldToIso` | Converts world coordinates to screen coordinates. |
| `isoToWorld` | Converts screen coordinates to world coordinates. |
| `screenInputToWorldDelta` | Converts WASD screen input into world-space movement. |
| `segmentIntersectsAABB` | Tests segment intersection against a rectangle. |
| `computeVisibilityPolygon` | Builds a visibility polygon from an origin. |

### `frontend/src/js/renderer.js`

Responsibilities:

- Canvas rendering.
- Depth sorting.
- Fog of war.
- Shared team vision.
- Wall extrusion.
- Door rendering.
- Spaceship rendering.
- Extraction zone rendering.
- Puzzle terminal rendering.
- Enemy rendering.
- Player rendering.
- Projectile rendering.
- Deployable rendering.
- Sound ring rendering.
- Crosshair rendering.

Rendering order:

1. Background.
2. Floor segments.
3. Spaceship.
4. Extraction zone.
5. Sound rings.
6. Ground deployables.
7. Puzzle terminals.
8. Depth-sorted walls, turrets, enemies, players.
9. Projectiles.
10. Fog of war.
11. Crosshair.

### `frontend/src/js/main.js`

Responsibilities:

- WebSocket connection.
- Client state storage.
- Lobby UI rendering.
- Class selection UI.
- Mission selection UI.
- HUD updates.
- Puzzle modal rendering.
- Keyboard input.
- Mouse aim.
- Mouse fire.
- 30 Hz input sender.
- 60 fps render loop.
- Camera smoothing.
- Victory/defeat banner.

Input loop:

```js
setInterval(() => {
  send({
    type: "input",
    dx: move.dx,
    dy: move.dy,
    aim_x: mouseWorld.x,
    aim_y: mouseWorld.y,
    firing: firing && !activePuzzle
  });
}, 1000 / 30);
```

Render loop:

```js
requestAnimationFrame(animate);
```

---

## 11. WebSocket Protocol & State Schema

This section defines the wire contract for developers and AI coding agents.

### Connection

Client connects to:

```text
/ws
```

On connection, the server sends:

```json
{
  "type": "welcome",
  "player_id": "abc12345",
  "classes": {
    "vanguard": {
      "id": "vanguard",
      "name": "Vanguard",
      "title": "Frontline Breacher",
      "color": "#C0392B",
      "max_hp": 120,
      "speed": 4.5,
      "footstep_radius": 3.8,
      "sight_range": 12.0,
      "weapon": {},
      "skill": {},
      "passive": {}
    }
  },
  "missions": {
    "outpost": {
      "id": "outpost",
      "name": "Derelict Outpost",
      "description": "...",
      "difficulty": "Standard",
      "segment_sequence": [],
      "enemy_multiplier": 1.0
    }
  },
  "state": {
    "type": "state",
    "phase": "lobby",
    "leader_id": "abc12345",
    "selected_mission_id": "outpost",
    "players": [],
    "events": []
  }
}
```

---

## 12. Client-to-Server Messages

All client messages are JSON objects sent over the WebSocket.

### `set_name`

```json
{
  "type": "set_name",
  "name": "Alice"
}
```

Effect:

- Updates player name.
- Broadcasts state.

---

### `select_class`

```json
{
  "type": "select_class",
  "class_id": "recon"
}
```

Effect:

- Updates player class.
- Resets HP, ammo, and max ammo.
- Broadcasts state.

---

### `select_mission`

```json
{
  "type": "select_mission",
  "mission_id": "reactor"
}
```

Effect:

- Only leader can select mission.
- Only allowed in lobby phase.
- Broadcasts state.

---

### `start_mission`

```json
{
  "type": "start_mission"
}
```

Effect:

- Only leader can launch.
- Builds mission map.
- Spawns players.
- Sets phase to `dropship`.
- Broadcasts state.

---

### `return_lobby`

```json
{
  "type": "return_lobby"
}
```

Effect:

- Allowed for leader or when phase is `victory` or `defeat`.
- Resets mission state.
- Broadcasts state.

---

### `input`

```json
{
  "type": "input",
  "dx": 0.0,
  "dy": 1.0,
  "aim_x": 12.5,
  "aim_y": 8.0,
  "firing": true
}
```

Effect:

- Updates player movement input.
- Updates aim target.
- Updates firing state.
- Does not immediately broadcast; simulation loop broadcasts every tick.

---

### `reload`

```json
{
  "type": "reload"
}
```

Effect:

- Starts reload if player is not downed and not already reloading.
- Broadcasts state.

---

### `skill`

```json
{
  "type": "skill"
}
```

Effect:

- Activates class skill if cooldown is ready.
- Broadcasts state.

---

### `interact`

```json
{
  "type": "interact"
}
```

Effect:

- Revives nearby downed teammate.
- Opens nearby puzzle terminal.
- Sends `interact_result` to the requesting client.

Example response:

```json
{
  "type": "interact_result",
  "interact_type": "open_puzzle",
  "puzzle": {
    "id": "puzzle_1",
    "type": "power_reroute",
    "label": "Reroute Security Bulkhead Power",
    "x": 18.5,
    "y": 4.0,
    "radius": 2.8,
    "solved": false,
    "door_id": "door_1",
    "segment_index": 1,
    "data": {
      "type": "power_reroute",
      "nodes": [1, 2, 3, 0],
      "target": [0, 0, 0, 0]
    }
  },
  "target_id": null
}
```

---

### `puzzle_action`

For `power_reroute`:

```json
{
  "type": "puzzle_action",
  "puzzle_id": "puzzle_1",
  "action": {
    "action": "rotate",
    "node_index": 0
  }
}
```

For `signal_decrypt`:

```json
{
  "type": "puzzle_action",
  "puzzle_id": "puzzle_2",
  "action": {
    "action": "input_symbol",
    "symbol": "A7"
  }
}
```

For Tech hardware bypass:

```json
{
  "type": "puzzle_action",
  "puzzle_id": "puzzle_2",
  "action": {
    "action": "tech_bypass"
  }
}
```

Effect:

- Validates player range.
- Updates puzzle state.
- Sends `puzzle_update` to the requesting client.
- Broadcasts state.

Example response:

```json
{
  "type": "puzzle_update",
  "ok": true,
  "solved": false,
  "puzzle": {
    "id": "puzzle_1",
    "type": "power_reroute",
    "label": "Reroute Security Bulkhead Power",
    "x": 18.5,
    "y": 4.0,
    "radius": 2.8,
    "solved": false,
    "door_id": "door_1",
    "segment_index": 1,
    "data": {
      "type": "power_reroute",
      "nodes": [1, 2, 3, 0],
      "target": [0, 0, 0, 0]
    }
  }
}
```

---

## 13. Server-to-Client Messages & State Broadcast Schema

### `welcome`

    {"type":"welcome","player_id":"a1b2c3d4","classes":{"vanguard":{"id":"vanguard","name":"Vanguard","skills":["dash"]}},"missions":{"outpost":{"id":"outpost","name":"Outpost","segments":["drop_hangar","puzzle_room","bulkhead","extraction_pad"]}},"state":{"phase":"lobby","players":[{"id":"a1b2c3d4","name":"Player","class_id":"vanguard","x":0,"y":0,"hp":100,"input":{"move_x":0,"move_y":0,"firing":false}}],"enemies":[],"projectiles":[],"turrets":[],"events":[],"map":{"segments":[],"puzzles":[],"enemies":[],"spaceship":{"ramp_open":false},"extraction_zone":{"active":false}},"extraction_active":false,"dropship_timer":null,"mission_timer":null}}

### State broadcast schema

Lobby:

    {"type":"state","phase":"lobby","players":[{"id":"p1","name":"Alice","class_id":"vanguard","x":0,"y":0,"hp":100,"input":{"move_x":0,"move_y":0,"firing":false}}],"enemies":[],"projectiles":[],"turrets":[],"events":[],"map":{"segments":[{"id":"drop_hangar","x":0,"y":0,"w":10,"h":10}],"puzzles":[{"id":"p1","x":0,"y":0,"solved":false,"progress":0}],"enemies":[],"spaceship":{"ramp_open":false},"extraction_zone":{"x":0,"y":0,"active":false}},"extraction_active":false,"dropship_timer":null,"mission_timer":null}

Playing/victory/defeat (`phase` may be `active`, `victory`, or `defeat`):

    {"type":"state","phase":"active","players":[{"id":"p1","name":"Alice","class_id":"vanguard","x":0,"y":0,"hp":100,"input":{"move_x":0,"move_y":0,"firing":false}}],"enemies":[{"id":"e1","x":5,"y":5,"state":"patrol","target_id":null}],"projectiles":[{"id":"pr1","x":1,"y":1,"vx":1,"vy":0,"owner_id":"p1"}],"turrets":[{"id":"t1","x":2,"y":2,"state":"idle","target_id":null}],"events":[{"id":"ev1","type":"kill","actor_id":"p1","target_id":"e1","time":12.3}],"map":{"segments":[{"id":"drop_hangar","x":0,"y":0,"w":10,"h":10}],"puzzles":[{"id":"p1","x":0,"y":0,"solved":false,"progress":0}],"enemies":[{"id":"e1","x":5,"y":5,"state":"patrol"}],"spaceship":{"ramp_open":true},"extraction_zone":{"x":20,"y":20,"active":true}},"extraction_active":true,"dropship_timer":null,"mission_timer":120.0}

### `puzzle_update`

    {"type":"puzzle_update","puzzle_id":"p1","solved":false,"progress":50,"message":"node connected","target_id":"door1"}

## 14. Running, Testing & Agent Extension Guide

### Run

    ./run.sh
    # or
    uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

### Test

    pytest tests/test_game.py

### Test suite coverage

- Map segment stitching and mission map construction.
- Physics collision, movement, and line-of-sight.
- Lobby leader, mission selection, start, dropship, puzzle completion, extraction victory.
- HTTP `/api/config` and index page.
- WebSocket `welcome`, `interact`, and `puzzle_update`.
- Enemy hearing/sight AI, skills, and puzzle interaction.

### Extension checklist

- Classes: add entries to `CHARACTER_CLASSES` with `id`, `name`, skills, cooldowns, and effects; ensure frontend consumes `/api/config`.
- Missions: add entries to `MISSIONS` with `id`, `name`, segments, enemies, puzzles, and extraction zone; ensure `build_mission_map` supports them.
- Map segments: add segment `id`, bounds, doors, terminals, spawn points, and extraction zone; validate segment stitching and active states.
- Puzzles: define `puzzle_id`, type, nodes/actions, completion effects, and `target_id`; ensure `handle_puzzle_action` returns `puzzle_update`.
- AI behaviors: add state transitions (`patrol`, `alerted`, `engaged`), hearing, line-of-sight, turrets, and projectiles; add tests for each behavior.
- Tests: extend `tests/test_game.py` for new classes, missions, map segments, puzzles, and AI states before shipping.