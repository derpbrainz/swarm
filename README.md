# VALKYRIE PROTOCOL

Browser-based co-op PvE extraction for up to four operatives in one authoritative room. Python/FastAPI simulates the world; native JavaScript ES modules render an isometric Canvas2D view. The procedural characters, wall extrusion, image textures, and directional sprite atlases are **2.5D**, not WebGL, GLTF, skeletal animation, or an imported 3D model pipeline.

## Setup and Run

Requirements: Linux/POSIX with `fcntl`, Python **3.11+**, Node **20+**, and Google Chrome for browser tests. CI uses Python 3.12 and Node 22. Run commands from the repository root.

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[test]'
npm ci
./run.sh
```

Open `http://localhost:8000` in each player's browser. `run.sh` starts Uvicorn with development reload, binds `0.0.0.0:8000`, and changes to the repository directory. For a loopback-only server without reload:

```sh
python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

The Python manifest uses bounded dependency ranges, not an exact lock; `package-lock.json` locks frontend test tooling. Some supported Starlette/httpx combinations emit a TestClient deprecation warning. That warning alone is not a game failure; do not suppress actual test errors.

| Configuration | Meaning |
| --- | --- |
| `SWARM_CONTENT_PATH` | Alternate complete JSON catalog, loaded and validated at import; default `backend/app/content.json`. No `SWARM_CONTENT_FILE` setting exists. |
| `SWARM_SAVE_DIR` | Writable runtime directory; default `.swarm-saves` relative to the process working directory. |
| `SWARM_HUB_WORLD=flagship_stub` | Opt into the small authored noncombat flagship placeholder; unset/empty keeps the normal worldless lobby. |

Content is not hot-reloaded in place: restart the application after a catalog edit. Custom save directories outside `.swarm-saves` must also be kept out of Git.

## Playing

The first connected operative leads the squad; leadership transfers when that operative leaves. Choose classes, select an unlocked saved level, manage the shared stash/loadouts, and mark each operative ready. Default preparation counts down only while everyone is ready, then launches a dropship. Every mission keeps its enemies and loot. Completing a level marks it as having passed testing; geometry validation alone does not. On an uncompleted level, any squad member can use **Report Broken Level** beside the inventory, supply a failure reason, and return the whole squad to the lobby. The map, seed, generation metadata and report remain saved as a broken example for repair. Broken maps are not playable and do not block later levels of the same mission. Ordinary squad defeat does not automatically declare a map broken.

Solve required terminal objectives, fight or evade the swarm, revive teammates, and bring every living operative into the activated extraction zone. Crate-lock puzzles are optional, not required extraction objectives. All operatives down means defeat. Victory banks nonstarter storage loot; equipped gear stays carried. Return to the lobby after either outcome. Friendly fire and self-damage are off by default.

| Class | Starter Role |
| --- | --- |
| Vanguard | Breaching shotgun, shockwave shield, reduced melee damage. |
| Tech | Compact SMG, automatic turret, supported terminal hardware bypass. |
| Medic | Combat carbine, healing/revival field with stronger low-health healing. |
| Recon | Suppressed rifle, ranged sonar, quieter movement. |

| Control | Action |
| --- | --- |
| WASD / arrows | Screen-aligned movement. |
| Mouse / left hold | Aim / fire; clicking a target explicitly interacts instead. |
| Shift | Request sprint; actual movement direction and weapon eligibility are server-controlled. |
| R | Reload selected weapon. |
| 1 / 2 | Select primary / secondary weapon. |
| Q / Space | Equipped special ability. |
| F | Equipped quick-use item. |
| E | Nearby interaction/revival; closes an open interaction modal. |
| Escape | Close interaction modal. |

Inventory buttons equip, unequip, use, drop, or transfer items. Capacity counts **storage only**, including an old equipped item moved into storage during a swap. Free starter items cannot be banked, dropped, or recovered from body chests. Magazine/reload state belongs to each weapon instance; switching does not refill it, holstered reloads pause, and an empty weapon slot cannot fire a class fallback. Blur, hidden tabs, disconnects, and modal/world/phase changes neutralize input. Responsive panels and DPR-aware canvas sizing are supported; touch gameplay controls and physical-device certification are not.

Exploration is shared across the squad. Seen floor and wall sections remain revealed but dim outside everyone's current sight; unexplored surfaces remain hidden. Opened doors no longer render as stale blocking panels, and exploration resets on entering a different world.

Newly generated maps mix open passages, proximity-opening doors, and puzzle-locked doors. Automatic doors open when a living operative enters their configured radius, then stay open. Only puzzle-locked thresholds create terminals and trailing wires; open and automatic thresholds add no puzzle requirement to extraction. Previously saved layouts are retained unchanged rather than regenerated automatically.

The optional hub reuses movement, collision, props, and reachable stash/mission consoles. Prepare at the mission console, ready each operative, then the leader explicitly **Departs** when all are ready; there is no hub preparation countdown. Return after victory/defeat creates a new hub world. Hub rules reject combat and beneficial consumable use server-side and disable AI, mission clocks, extraction, cooldowns, and squad-defeat processing. This is a production-configurable integration **stub**, not a narrative hub, NPC/social/account service, campaign, or finished art.

## Architecture

| Boundary | Implementation |
| --- | --- |
| Authored definitions | `backend/app/content.json`; `content.py` validates strict schemas, stable IDs/references, finite bounds, graphs, supported handlers, and local assets, then exposes immutable definitions/resolved views. `config/*.py` are views, not independent balance sources. |
| Transport / startup | `main.py` owns `create_app`, HTTP/WebSocket endpoints, lifespan startup/shutdown, and monotonic fixed-step scheduling. `commands.py` validates intent; `transport.py` serializes each socket's sends with bounded backpressure. |
| Authoritative room | `engine/game_loop.py` owns players, inventory/settlement, world/session lifecycle, ticks, visibility, and snapshots. `world_id`/`world_kind`/capabilities are separate from `lobby`, `prep`, `dropship`, `active`, `victory`, `defeat`. |
| Generation / geometry | `wfc.py`, `map_builder.py`, `physics.py`, `props.py`, `worlds.py`: stable RNG ordering, bounded retries, swept geometry, independent surface channels, and authored hub instantiation. |
| Gameplay handlers | `game_loop.py` uses swept `physics.py` impacts and shared `combat.py` faction/damage/down/death policy, and settles bodies before revival; `ai.py` retains perception/movement; `attacks.py`, `puzzles.py`, `objectives.py` dispatch a small supported mechanic set. |
| Runtime persistence | `level_store.py`, `inventory.py`, `persistence.py`: saved templates/progress, atomic counts plus ownership claims, backups, and application-lifetime writer locks. |
| Client | `main.js` integrates `network`, `input`, `modal`, `ui`, `hub`, `puzzles`, `content`, `audio`, `assets`, `scene`, `iso`, and `renderer` modules under `frontend/src/js`. No frontend bundler/framework is required. |

Imports construct the default app/room and validate content but do not load/write saves or start a loop. The lifespan acquires the writer lease, calls `room.initialize()`, starts simulation, and awaits cleanup. Tests can inject `create_app(room, simulation=...)`; construct `GameRoom(LevelStore(temp_levels_path, load=False), SharedStash(temp_stash_path, load=False), rng=...)` with distinct temporary files and enter the app lifespan to initialize safely. Store constructors default to immediate loading, so use `load=False` when startup must own initialization. Configure `create_app(hub_world="flagship_stub")`, or configure an injected room's `hub_world`, not both.

The default simulation runs at 30 Hz with at most five catch-up steps. Scene adaptation separates visual pivots/bounds/materials from authoritative colliders/occluders; segmented wall/footprint ordering and visibility-polygon exploration improve overlap and fog without changing hitboxes. Small-scene ordering can be quadratic and intersecting artwork is not general 3D depth. Images/audio are HTTP assets, never mesh/texture bytes in snapshots. Audio retries after readiness/user gestures, deduplicates transient IDs, and resets on world teardown.

## API and Protocol

`GET /` serves the game; `/static` serves frontend sources; `/assets` serves local assets. `GET /api/config` returns the sanitized resolved catalog plus `levels` and `development_tools`; `GET /api/levels` returns level summaries. `POST /api/levels/generate` and WebSocket generation require explicit `create_app(development_tools=True)` and are disabled on the normal import target. A WebSocket `test_mode` override is also development-only. Untested-level testing mode remains part of normal progression. There is no authentication/account system; public hosting/security hardening is a separate task, not a property of these development controls.

Connect to `/ws` (`wss` under HTTPS). The server sends `welcome` with `player_id`, `catalog`, `levels`, and `state`, then full `type: "state"` snapshots. State includes roster, leader/selection, shared stash, phase/timers, `world_id`, `world_kind`, `world_capabilities`, `simulation_time`, and `event_sequence`. A world adds `map` geometry/props/interactions/puzzles/objectives/loot/crates, enemies, projectiles, deployables, sounds, audio events, and music. A default worldless lobby has **no `map`**; a hub has no required spaceship/extraction. Player snapshots include inventory, ammo compatibility mirrors, sight range, and authoritative action capabilities. Enemy visibility is shared squad visibility, not hidden-entity anti-cheat filtering.

Failure reporting sends `{"type":"report_broken_level","level_id":"outpost_101","world_id":"current-world-id","reason":"Door blocks the only route"}`. The server validates the active world and uncompleted status, saves the reason before returning the squad, and sends `level_report_feedback`. Empty/oversized/stale reports are rejected; save failures leave the map unmodified or retry a previously saved return safely. A completed level cannot be marked broken through this control. There is no enemy-free test-mode override.

To explicitly discard the current playable catalogue and regenerate fresh maps, stop the game server and run `python3 -m backend.app.regenerate_levels`. All six candidate maps are generated and validated before replacing the packaged and runtime level catalogues; old files remain `.bak` recovery copies, not active examples. This command resets completion/reports for the reset catalogue, but does not touch the shared stash. Do not run it to repair a single broken example: retain that example and generate a separate replacement instead.

All commands are strict JSON objects with a known `type`; unknown/irrelevant fields, duplicate keys, nonfinite numbers, wrong boolean types, and invalid IDs/actions reject. `commands.py` is the definitive field schema. Payloads are capped at 8 KiB; each socket admits 60 commands/second with a 120-command burst. Names are limited to 20 characters, IDs to 128, and seeds to `0..2147483647`.

```json
{"type":"select_class","class_id":"tech"}
{"type":"select_mission","level_id":"outpost_101","prepare":true}
{"type":"toggle_ready","ready":true}
{"type":"input","dx":0.0,"dy":1.0,"aim_x":12.5,"aim_y":8.0,"firing":true,"running":false}
{"type":"inventory_action","action":"switch_weapon","slot":"secondary_weapon"}
{"type":"inventory_action","action":"use_item","slot":"quick_use"}
```

These are separate messages, not one JSON document. Other types are `set_name`, `generate_level`, `prepare_mission`, `start_mission`, `min_loadout`, `stash_to_player`, `player_to_stash`, `pickup_item`, `take_crate_item`, `return_lobby`, `reload`, `skill`, `interact`, and `puzzle_action`. Readiness/minimum-loadout changes are self-owned; intentional squad stash assignment uses `target_player_id`. Leadership, phase, unlock, all-ready launch, capacity, range, LOS, and world rules remain server-authoritative. Active class/loadout resets and mission restarts are denied.

For clicked interactions use the **current** snapshot target/world and welcome player ID, not remembered IDs. `request_id`, `session_id` (the connection's `player_id`), and `world_id` must be supplied together when using correlation; use a fresh request ID for every action. Example shape with placeholders to replace:

```json
{"type":"interact","target_id":"CURRENT_TARGET_ID","request_id":"0:1","session_id":"WELCOME_PLAYER_ID","world_id":"CURRENT_WORLD_ID"}
{"type":"puzzle_action","puzzle_id":"CURRENT_PUZZLE_ID","action":{"action":"rotate","node_index":0,"puzzle_session_id":"CURRENT_PUZZLE_SESSION_ID"},"request_id":"0:2","session_id":"WELCOME_PLAYER_ID","world_id":"CURRENT_WORLD_ID"}
```

Generic nearby `interact` deliberately omits `target_id`. Power actions use `rotate`/`node_index`, signal actions use `input_symbol`/`symbol`, and supported Tech bypass uses `tech_bypass`. Timed fixtures use `start_attempt` then `timed_input` with `symbol` and the server's `attempt_id`; clients never report success or trusted elapsed time. Puzzle-session IDs are supported in nested actions and sent by the shipped UI.

Responses are `interact_result`, `puzzle_update`, `chest_update`, `inventory_feedback`, correlated `service_feedback`, or `command_error` with `message`. Action feedback can contain `ok: false`; sending is not proof of success. Successful correlated results echo request/session/world/target plus `interaction_revision`; errors echo available correlation. Reconcile against current snapshots, ignore stale/closed-target replies, and invalidate old sessions on reconnect/world changes. Private power-puzzle targets are stripped from outgoing data; the signal sequence is intentionally visible for its existing mechanic.

Each socket has one sender, at most 64 queued reliable messages plus one replaceable latest snapshot, and a two-second send timeout. Overflow/slow send disconnects that socket rather than stalling peers. Audio events carry stable `event_id`, room-monotonic `sequence`, world identity, simulation `timestamp`, and `expires_at`; listener mixes do not consume others' events. Default presentation history is **256 events / 0.75 seconds**, with a join floor and client deduplication. This is best-effort transient audio, not a durable acknowledged event log; coalescing or long stalls can miss expired sounds. Gameplay hearing is processed separately once by the next tick.

## Saves, Migration, and Recovery

`backend/app/data/levels.json` and `shared_stash.json` are retained legacy seed fixtures, **not live save targets**. First lifespan startup copies their data into missing runtime files. Existing runtime files win; empty valid saves do not reset. `levels.json` stores `{"schema_version":1,"levels":{...}}`; `shared_stash.json` stores `{"schema_version":1,"shared_stash":{...},"holdings":{...}}`.

Accepted legacy bare dictionaries are normalized record-by-record in memory and written as schema 1 on an explicit/next save. Level IDs, created metadata, tested/completed flags, original layouts, and expanded legacy weapon/value data are preserved. Sparse loot, newer content, or generator versions never silently regenerate old snapshots. New maps record requested/resolved seed, generator/content versions, validation and bounded retry diagnostics. Current-version maps are validated before launch; preserved older layouts are not automatically repaired or certified by the new validator. Generate a **new** saved level to exercise changed segments/placements; catalog edits do not rewrite an existing template. Unknown stash IDs remain stored but are not offered in UI; unknown map item IDs reject instantiation rather than becoming substitute items.

Nonstarter claims are persisted in the **same envelope** as stash counts before publishing transfers. Restart refunds abandoned player/world claims to shared template counts once; it does not resume a personal account, mission, QTE, or exact instance ammo/stats. Prep/hub disconnect returns nonstarter gear; active disconnect creates a durable body drop, settled on world teardown/restart. Retryable failed settlement retains the old world/ownership until it can complete. Level progression is a separate file from inventory settlement, not a database-wide transaction.

Writes fsync same-directory temporary files, atomically replace the target, and retain previous valid bytes as `<name>.json.bak`. A post-replace directory-fsync failure is logged as uncertain durability while published state follows the committed bytes. Malformed/unsupported/unreadable saves stop with `SaveError`; startup never silently restores a backup or replaces damaged data with defaults. `.bak` is only the previous version and is overwritten by later saves, not an archive. Keep an independent backup of the entire runtime directory before operator migration/recovery.

**Stop every server before maintenance.** For explicit in-place schema-1 migration of compatible runtime files, use the actual store APIs under the same writer lease (this also performs normal abandoned-claim refund):

```python
from backend.app.engine.inventory import SharedStash
from backend.app.engine.level_store import LevelStore
from backend.app.engine.persistence import runtime_directory, writer_lease

directory = runtime_directory()
paths = [directory / "levels.json", directory / "shared_stash.json"]
with writer_lease(paths):
    levels = LevelStore(paths[0], load=False)
    stash = SharedStash(paths[1], load=False)
    levels.load()
    stash.load()
    levels.save()
    stash.save()
```

For a damaged file, inspect the backup first and call `recover_backup(path, key)` explicitly; `key` is `"levels"` or `"shared_stash"`. Example for stash recovery:

```python
from backend.app.engine.persistence import recover_backup, runtime_directory, writer_lease

path = runtime_directory() / "shared_stash.json"
with writer_lease([path]):
    recover_backup(path, "shared_stash")
```

Recovery validates the backup envelope, preserves current bytes as `.json.damaged`, and replaces from `.json.bak`; subsequent store loading still performs domain validation. Recovering rolls back to the backup and may lose newer changes. Preserve existing `.damaged`/backups separately before repeated recovery. There is no general repair/regeneration CLI.

Operate **one process/writer per save pair**. Lifespan holds nonblocking Linux/POSIX `fcntl.flock` leases; a second application using either path fails startup. Direct store scripts must acquire leases themselves and must never reload claims against an active room. Multiworker Uvicorn, Windows, distributed/network-filesystem locking, durable accounts, and transactional multi-server hosting are unsupported. Atomic replacement alone is not concurrency control.

## Content and Extension Recipes

Edit a complete catalog, validate it, restart with `SWARM_CONTENT_PATH=/absolute/path/content.json`, and test against disposable `SWARM_SAVE_DIR`. Keep `schema_version: 1`, give meaningful `content_version` changes, and use existing lowercase ID conventions. **Data** owns definitions/tuning/references/placements; **code** owns algorithms, protocol discriminators, and bounded mechanics; **runtime saves** own generated snapshots/progress/counts/claims. Do not edit resolved `config` views or add eval/import/script fields to JSON.

1. **Balance/loadouts/sound:** change `weapons.breaching_shotgun.damage`, `mag_size`, or `fire_sound`; `classes.vanguard.carry_capacity`; `effects.shockwave_shield.radius`; or `sounds.weapon_fire_pistol.asset`/`base_gain`. Classes reference `loadout_id`; loadouts reference item IDs; items reference `weapon_id`/`effect_id`. New instances and sanitized UI resolve the same catalog. Preserved expanded legacy items can retain their original numeric weapon/value fields; edits are not a retroactive save rebalance.
2. **Existing prop instances:** add the following record to `segments.drop_hangar.props` (a list), then generate a new level, or add it to the enabled `worlds.flagship_stub.props` list and restart. Coordinates are local; placement IDs must be unique in that list. `orientation` is only `0`, `90`, `180`, or `270`.

```json
{"id":"chair_a","archetype":"chair","x":8,"y":10,"orientation":90}
```

`chair` is nonblocking; `table` has independent movement/projectile/acoustic rectangles but no sight block; `console` adds an existing terminal handler. New supported archetypes use `visual_id`, independent `geometry` channel rectangles, and optional `interaction` with `type`, `radius`, `label`. Visual changes never imply collision changes. Check bounds, spawn clearance, interaction reach, and generated progression, not just schema validity.

3. **Images/materials/sprites:** `presentation.assets.test_floor.path` is the real local `visual/test-floor.svg`; set `presentation.floor_materials.hangar` to existing `test_tiles` to exercise its texture. Set `props.console.visual_id` to existing `test_detail`, or `presentation.defaults.player` to `test_actor` for the four-direction/two-frame atlas. Image paths resolve under `frontend/src/assets`, require existing normalized contained paths (no URLs/traversal/symlink escapes), and support `.svg`, `.png`, `.jpg`, `.webp`. Tiny self-authored `test-*.svg` files are fixtures, not production art. Sprites use `directions`, `frames`, `fps`, `width`, `height`, and `pivot`; asset errors use the declared procedural fallback.
4. **Puzzle parameter variants:** add this entry under `puzzles`, then reference `short_power` in a segment `puzzle_slots[].type` or a prop `interaction.type`. No new mechanic code is needed; instantiate a new level/world to use the definition. Existing saved puzzle parameters/answers remain preserved.

```json
"short_power": {"handler":"power_reroute","node_count":2,"allow_tech_bypass":true}
```

Signal variants use `handler: "signal_decrypt"`, `sequence_length`, unique `symbols`, and `allow_tech_bypass`. A genuinely new mechanic needs a supported schema/action discriminator, localized `engine/puzzles.py` generation/action handlers and paired `frontend/src/js/puzzles.js` renderer, plus server/session/replay and browser tests. It is not arbitrary plugin loading.

5. **Objectives/attacks/encounters:** mission `objectives` records use `id`, `label`, `condition` (`puzzle_solved`, `enemy_dead`, `complete`), matching `target_id`, `dependencies`, `required`, and `effects`. Effect handlers are only `open_door`, `unlock_crate` (both require `target_id`), or `activate_extraction` (no target). Example for an authored map that actually contains the specified terminal/door:

```json
{"id":"restore_power","label":"Restore power","condition":"puzzle_solved","target_id":"terminal_a","dependencies":[],"required":true,"effects":[{"handler":"open_door","target_id":"door_a"}]}
```

Target IDs must match concrete instantiated records; they are not wildcards or definition IDs. The CLI checks shape/graph; launch checks actual targets before world publication. Graphs are capped at 128 records and reject cycles. Existing terminal mapping plus `missions.<id>.completion_effects` already handles extraction, including zero-puzzle missions. Attack profiles use `handler` (`melee`/`projectile`), `range`, `damage`, `cooldown`, and compatible `projectile_speed`; enemies reference `attacks` and optional `encounter_id`. Encounter `phases` use unique `id`, descending `health_at_most` thresholds starting at `1`, and attack IDs; phase changes do not reset the shared cooldown. New attack mechanics need a bounded `attacks.py` handler/schema and shared combat tests, not boss-name branches.

6. **Combat policy/worlds:** `rules.combat.friendly_fire` and `self_damage` are independent booleans, both default `false`; `factions` requires complete symmetric ally/hostile/neutral relations. Beneficial targeting is separate. `world_rules` explicitly selects movement/interactions/combat/AI/objectives/extraction/failure/mission_clock/cooldowns and `beneficial_use` (`normal`/`disabled`), independent of phase. The bounded authored `worlds.flagship_stub` demonstrates tiles/walls/props/spawns and references `rules_id: "hub"`; arbitrary authored combat campaigns/extraction editors are not implemented.
7. **Thresholds:** configure the top-level `thresholds` registry and each mission's `threshold_weights`. `open_passage` uses `{"mode":"open"}`; `automatic_door` uses `{"mode":"proximity","door_type":"door","sound_type":"door_operate","trigger_radius":2.4}`; `power_lock` uses `{"mode":"puzzle","door_type":"door","sound_type":"door_operate","puzzle_id":"power_reroute"}`. Set weights such as `{"open_passage":3,"automatic_door":3,"power_lock":2,"signal_lock":2}` or restrict them to one policy. Selection uses stable seeded randomness. Generator `wfc-3` / content `foundation-3` identifies these new maps; generate a new level explicitly to use the new distribution without overwriting old saves. Puzzle variants on connector doors are selected through the policy's `puzzle_id`; prop consoles still select their own interaction definitions.

`tests/fixtures/stage10-props.json` demonstrates placements/visual swaps; `stage11-encounter.json` adds power/signal variants, a two-second `timed_input` ACK attempt, and a finite multi-phase melee/ranged boss. **QTE/boss definitions are test fixtures only**, absent from the normal roster/campaign. `tests/browser_server.py` installs them and exposes time/movement/damage controls only in its disposable loopback test executable; production has no `/__test__/fixture` route. Do not run that harness as a game service. The hub, unlike those fixture-only controls, is explicitly enabled through production configuration.

## Tests and CI

After the setup above, install Chrome for Playwright if it is not already available:

```sh
npx playwright install --with-deps chrome
python3 -m backend.app.content
python3 -m pytest -q
npm test
npm run test:browser
git diff --exit-code -- backend/app/data/levels.json backend/app/data/shared_stash.json
git diff --check
```

To validate an alternate catalog without starting the app, use `python3 -m backend.app.content /absolute/path/content.json`. Content validation never loads/writes saves. Pytest fixtures use `tmp_path` copies/injected stores and exercise HTTP/WebSocket lifespan, malformed commands, persistence migration/failure/restart, inventory conservation, swept combat, fresh-process hash-seed determinism/seed sweeps, audio, props, sessions/objectives, and world lifecycle. `npm test` runs native Node ES-module units. Python tests also launch Node subprocesses, so Node is required for the complete pytest suite.

Playwright selects the **system Google Chrome channel**, one worker, and starts `PYTHONPATH=. python3 tests/browser_server.py` at `http://127.0.0.1:8765`. It always refuses server reuse; leave port 8765 free. The harness owns a `TemporaryDirectory` of copied saves and cleans it on exit. Real browser cases cover co-op mission/hub trips, interaction races, focus/input, safe text, narrow/DPR layouts, actual audio/image loading, fog/overlap pixels, and timed fixture feedback. They are not subjective art/audio or all-device certification. `--with-deps` can require administrator access; CI runs on Ubuntu.

`.github/workflows/tests.yml` installs the declared Python dependencies, uses `npm ci`, provisions Chrome, runs content/Python/Node/browser gates with temporary runtime storage, and checks the tracked legacy fixtures remain unchanged. It performs no deployment/push. Local verification evidence and known limitations live in `AI/execution-log.md`; adding this workflow does not claim a remote CI run has passed.
