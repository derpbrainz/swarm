# Code Review: Correctness and Extensibility

Review date: 2026-10-27. Baseline: `fb5481a` on `main`.

This is a review and implementation handoff, not an implemented refactor. At review time the working tree was clean and `main` was one commit ahead of `origin/main`. No existing commits or gameplay changes were pushed by this review.

## Findings

Severity: P1 means data loss, exploitable correctness, or cross-client security; P2 means incorrect gameplay or reliability; P3 means lower-impact defects or maintainability. References describe the reviewed baseline and will move during implementation. Architecture limitations are listed separately below.

### R01 - P1: Save loading can erase all levels and progress

References: `backend/app/engine/level_store.py:18-43`, `backend/app/engine/inventory.py:36-50`.

LevelStore treats one level with outdated loot/crate fields as a reason to discard the entire loaded dictionary. It then generates defaults and overwrites the file. Broad exception handling does the same for unreadable or malformed saves. A guarded fixture with six levels and two completed levels, with one level's loot emptied, became five defaults and zero completed levels. SharedStash similarly overwrites unreadable saves with defaults.

Both stores write whole files non-atomically with `write_text`. A crash or disk error can truncate the only copy, and subsequent initialization can overwrite the evidence. Move runtime saves outside shipped content, version them, migrate individual records, retain originals/backups, and distinguish missing saves from damaged saves. Importing engine modules must not create or replace saves.

### R02 - P1: Player names are inserted as executable HTML

References: `frontend/src/js/main.js:387-393`, `frontend/src/js/main.js:652-657`, `backend/app/engine/game_loop.py:227-229`.

Names received from other clients are interpolated into `innerHTML`. The 20-character limit does not make HTML safe: `<svg onload=alert()>` fits it. This is a cross-client script-injection sink, not just a cosmetic issue. Use DOM text nodes or properly escaped text for names and all untrusted display content, and validate any attributes/URLs separately. The sink and payload length were checked; a real-browser exploit was not executed.

### R03 - P1: Commands bypass phase, ownership, and input invariants

References: `backend/app/main.py:152-192`, `backend/app/main.py:239-272`, `backend/app/engine/game_loop.py:138-175`, `backend/app/engine/game_loop.py:263-315`, `backend/app/engine/game_loop.py:342-354`.

Class selection during an active mission heals to maximum, restores starter equipment/ammo, and discards carried nonstarter gear. Minimum-loadout restoration also works outside preparation. Direct mission start checks only leadership/player presence: it can restart an active mission and launch a locked level without the preparation check. Tests must cover direct domain calls as well as WebSocket dispatch.

An unrelated client can supply `target_player_id` to set another player's readiness or reset their loadout. Existing preparation UI intentionally supports squad-wide item assignment; preserve that useful feature through an explicit actor/recipient policy rather than blindly banning every cross-player inventory operation. Readiness and destructive loadout resets should remain self-owned unless explicitly delegated.

Valid JSON `[]` causes an uncaught AttributeError; invalid JSON, null values, malformed nested actions, and failed numeric conversions are also not handled per message. `aim_x=NaN` is accepted, makes player angle nonfinite, and emits `NaN` in JSON state, which browser `JSON.parse` rejects. Validate object shape, strict booleans, finite bounded numbers, IDs, message size/rate, and phase before mutation; reject invalid messages without corrupting the room or disconnecting unrelated players.

### R04 - P1: Inventory provides repeatable duplication, identity, and ammo exploits

References: `backend/app/engine/game_loop.py:214-225`, `backend/app/engine/game_loop.py:565-573`, `backend/app/engine/inventory.py:84-104`, `backend/app/engine/inventory.py:232-251`, `backend/app/config/items.py:43-49`, `backend/app/engine/map_builder.py:315-348`, `backend/app/engine/level_store.py:169-175`.

Banking a free starter weapon and restoring the starter loadout can increase stash stock indefinitely. Switching to an already selected weapon refills its magazine; switching between weapons also loses any independent ammo history. Re-equipping an active weapon refills it too. Magazine/reload state belongs to the weapon instance, not only the player.

Map templates reuse deterministic item IDs across mission instances. Keeping one equipped item between missions and picking up another copy from the same template creates identical inventory IDs. Removal then searches storage first and can remove a different instance from the one selected.

`make_item` shallow-copies nested weapon/effect/value dictionaries, so two items and their template share mutable state. Unknown item types silently become credits, concealing bad content references. Storage-to-equipment swaps also skip their resulting capacity check: a Recon inventory can successfully end up at 13/12 weight. Centralize item creation and transitions; preserve the current storage-only weight rule unless an intentional gameplay change is approved.

### R05 - P1: Inventory transfers and disconnects can lose or double items

References: `backend/app/engine/inventory.py:52-65`, `backend/app/engine/inventory.py:298-330`, `backend/app/engine/game_loop.py:129-136`, `backend/app/engine/game_loop.py:194-225`.

Stash withdrawal mutates and saves before inventory acceptance. Extraction deposits save once per item before removing inventory items. Injecting failure on a later deposit leaves earlier items both banked and still carried; retry doubles them. File replacement alone does not solve these in-memory multi-step transaction failures. Validate first, apply once, persist one transaction, and roll back or retain a recoverable operation record on failure.

Withdrawing nonstarter gear in the lobby and disconnecting removes the only player record without returning or retaining the gear. Define lifecycle settlement: return nonstarter preparation gear to the shared stash; use a body chest or recoverable identity in an active mission, without adding permanent accounts merely for this fix. Two independently instantiated stores can also overwrite each other's whole-file state. The current deployment should explicitly support one writer/process; multiworker deployment needs locking or a transactional store before it is supported.

### R06 - P2: Persisted maps are regenerated repeatedly and seeds are not reproducible

References: `backend/app/engine/level_store.py:169-175`, `backend/app/engine/map_builder.py:306-324`, `backend/app/engine/wfc.py:136-177`, `backend/app/engine/wfc.py:260-264`.

Instantiation requires at least 15 loot objects, while the generator intentionally produces one or two per segment. Five of the six reviewed persisted levels have fewer than 15, so mission starts repeatedly rebuild and save them. Count-based schema detection is incompatible with deliberately changing loot density.

WFC chooses from lists built from unordered sets. The same requested Outpost seed 101 produced different layouts and resolved seeds 254, 339, and 373 under `PYTHONHASHSEED=0`, `1`, and `2`. The map stores the resolved seed while its level record stores the requested seed, without distinguishing them. Remaining-cell collapse can also append segments after the maximum is reached; reviewed Outpost, Biolab, and Reactor examples exceed configured maxima. Use stable candidate ordering, explicit generator/content versions and requested/resolved seeds, and enforce final constraints. Do not silently regenerate saves to adopt new content.

### R07 - P2: Projectile hits tunnel and are resolved in the wrong order

References: `backend/app/engine/game_loop.py:969-1018`, `backend/app/engine/physics.py`.

Walls are checked along the bullet's movement segment, but actors are checked only at its endpoint. A 55-unit/s rail projectile moves about 1.83 units at 30 Hz and can pass completely through a Stalker. Any wall intersecting the segment discards the projectile before checking a nearer actor, so a wall behind the target prevents a valid hit.

Compute swept intersections for all candidate colliders, select the nearest eligible impact, and apply damage through a shared path. Retain deterministic tie-breaking. This is also the appropriate foundation for optional friendly fire, destructible props, and larger boss hit volumes, without requiring a 3D physics engine.

### R08 - P2: Down/revive and weapon-presence rules disagree

References: `backend/app/engine/game_loop.py:439-441`, `backend/app/engine/game_loop.py:469-475`, `backend/app/engine/game_loop.py:960-962`, `backend/app/engine/game_loop.py:1028-1035`, `backend/app/engine/inventory.py:108-119`.

Manual revival resets `body_dropped`, but healing-field revival does not. After a field revive, item recovery, and another down, a new body chest is not created and recovered gear remains on the downed player. An empty weapon loadout also falls back to the class weapon, allowing a revived, unequipped player to shoot a weapon they no longer possess. Shared damage/down/revive transitions should handle chest creation, flags, attribution, and equipment checks consistently. Shockwave kills also skip the projectile kill-accounting path.

### R09 - P2: Event lifetime causes lost alarms and repeated audio

References: `backend/app/engine/game_loop.py:728`, `backend/app/engine/game_loop.py:781-785`, `backend/app/engine/game_loop.py:1026`, `backend/app/engine/game_loop.py:1174-1184`, `frontend/src/js/main.js:179-182`.

An alarm emitted by a puzzle command is cleared at the next step before AI consumes it, so nearby enemies never hear it. Conversely, events left in a victory/defeat frame are not cleared because terminal steps return early; snapshots keep retransmitting them. Additional command-triggered broadcasts can replay events within the same simulation tick. The audio factory creates event IDs/timestamps, but serialization drops them, and the client plays every received event.

Separate pending gameplay events from bounded presentation event history, assign monotonic sequence/ID and simulation time, process AI events exactly once, and deduplicate presentation per client. Snapshot reads should not mutate gameplay or consume events differently for different listeners.

### R10 - P2: Audio definitions, loading, and geometry are inconsistent

References: `backend/app/engine/game_loop.py:365-367`, `backend/app/engine/game_loop.py:1146-1150`, `backend/app/engine/audio_events.py:124-132`, `frontend/src/js/audio.js:413-448`, `frontend/src/js/audio.js:229`, `backend/app/engine/sound_occlusion.py:100-108`, `backend/app/engine/sound_occlusion.py:143-190`.

Reload code emits weapon-specific sound IDs that are absent from the server registry, although their client assets exist. Music classification expects `aggro/chase/attacking`, but AI uses `engaged`, so ordinary combat remains exploration music. Music state can latch before the buffer loads and never retry that state. Lobby snapshots omit music, allowing the previous mission's music to persist after returning early.

Occlusion treats a `0.0` segment hit as a miss, keeps inactive non-door walls blocking, and guesses doors from IDs. The real `spaceship_ramp` is not recognized, so it still muffles sound when open. Cache keys omit geometry positions and inactive-wall changes, risking stale results across regenerated worlds. Stereo pan uses world X rather than the projected screen axis, reversing some left/right cues. Use explicit geometry/material metadata and world/revision-scoped caching, one validated sound catalog, readiness-aware playback, and projected orientation. Ambience loop management exists but is not connected to world emitters.

### R11 - P2: Client input and interaction state can stick or target the wrong object

References: `frontend/src/js/main.js:241-259`, `frontend/src/js/main.js:1165-1278`, `backend/app/engine/game_loop.py:458-504`.

There is no focus-loss/visibility reset for held movement, Shift, or firing. Late puzzle/chest responses can reopen a modal the player closed. Other players solving a puzzle do not reliably reconcile the open local puzzle from authoritative snapshots. Global hotkeys can trigger reload, skills, or items while interacting with a modal.

Heavy-weapon firing is blocked whenever Shift is held, even when stationary or walking backward; the server blocks it only while actually sprinting. Clicking a specific chest sends only generic `interact`, so another nearby interactable can take priority. The client permits a 3.8-unit chest click that the generic server interaction cutoff rejects above 3.5. Add explicit input contexts, reset and neutral-input messages, request/target correlation, server-validated target IDs/ranges, and authoritative capabilities rather than duplicated heuristics.

### R12 - P2: Visibility and render ordering do not reliably represent geometry

References: `frontend/src/js/renderer.js:68-77`, `frontend/src/js/renderer.js:102-159`, `backend/app/engine/game_loop.py:1095-1107`, `backend/app/engine/game_loop.py:445-455`.

Explored cells are marked by radial distance without testing their visibility polygon, revealing floor cells behind walls. Sonar uses one global timer and makes every enemy visible, even 100 units away, despite its configured 18-unit pulse radius. Loot/crates/consoles are drawn outside the main depth-sorted pass; detailed props will worsen incorrect overlap. Wall centroid ordering also fails for long walls: a player geometrically in front of a long wall can be drawn first and covered by it. Fix current visibility/range defects and define explicit render bounds/layers. Canvas depth ordering needs a tested long-wall strategy, not just another `x+y` sort.

### R13 - P2: Map verification and decoration do not use complete collision geometry

References: `backend/app/engine/map_builder.py:227-273`, `backend/app/engine/map_builder.py:375-428`.

Wire routes are computed before interior walls exist; reviewed saved routes cross those walls. Solvability checks test only flood-fill endpoints at one-unit intervals and can jump a thin uninterrupted barrier. They constrain samples to segment interiors rather than verifying actual perimeter enclosure, so a wall-less map can pass. Generation does not automatically require this verifier to pass before storing/advertising a level. Build all collision geometry first, check swept movement between nodes with the player footprint, verify enclosure and reachable interactions/objectives, and store diagnostics or reject invalid output.

### R14 - P2/P3: Test and setup coverage masks important failures

References: `tests/test_game.py:5-12`, `tests/test_game.py:63-145`, `tests/test_game.py:210-218`, `tests/test_game.py:277-402`, `run.sh:5`.

Tests directly change shared singleton state and real save files. A missing FastAPI dependency silently turns HTTP/WebSocket coverage into a successful test return rather than a visible skip/failure. TestClient is not used as a lifespan context, so the background simulation is not exercised in that integration test. There are no committed dependency manifests, frontend tests, or CI configuration in the reviewed tree. Frontend parsing is not evidence of browser correctness.

Use temporary injected stores and app/room factories, explicit dependency/test groups, genuine transport and lifespan tests, fake clock/RNG tests, and a small browser smoke suite. Lockfile/dependency choices should fit the Python-and-native-ES-module project rather than introducing a bundler by habit. Also test mobile/narrow-window layout: fixed lobby columns, body overflow hiding, and minimum-width modals have no responsive rules.

### R15 - P2: Slow-client delivery stalls the shared simulation

References: `backend/app/main.py:32-44`, `backend/app/main.py:61-80`.

Snapshots are awaited serially for every socket, and the simulation awaits that entire broadcast before sleeping for another fixed `dt`. A stalled first socket prevented a healthy second socket from receiving its snapshot in a fake-socket probe. Simulation wall-clock speed also slows with serialization/send cost because each loop advances one fixed step then sleeps, rather than scheduling against a monotonic deadline. The broad send exception handler conflates snapshot/build errors with disconnected clients. Use bounded per-client serialized senders and fixed-step scheduling with bounded catch-up; measure before optimizing payloads. With larger textured/prop-heavy worlds, retransmitting static geometry and level lists every tick will also need a measured static/dynamic protocol split, not an unverified wholesale networking rewrite.

## Fit Against the Requested Direction

### Existing strengths worth keeping

- Authoritative Python simulation and thin WebSocket client are a useful starting point for cooperative play.
- Physics, AI, inventory, generation, and audio already have identifiable module boundaries.
- Classes, enemy stats, missions, item templates, and segment layouts are structured dictionaries, even though they are executable Python rather than validated content files.
- Isometric projection and inverse projection are separated and passed focused checks.
- Generated map templates are deep-copied before use; keep that separation but mint runtime IDs and explicitly migrate versions.
- Eleven existing tests cover useful happy paths and all passed in isolated storage during this review.

### Architecture limitations, not bugs by themselves

The renderer is Canvas2D. Its detailed humanoid drawing is procedural 2.5D geometry, not a general-purpose 3D model importer. There is no image/texture/model asset loader. True GLTF/skeletal models require a later explicit renderer choice; textured surfaces and pre-rendered directional sprite sheets can be supported without changing the simulation. Do not silently choose a full WebGL rewrite in this cleanup.

Balance and content are duplicated across `config/classes.py`, `config/items.py`, frontend fallback classes/capacities, UI strings, sound manifests, and literal code values. For example, shield/turret/healing effects appear in item metadata but runtime skills read class configuration and contain additional radii/rates in code. A numeric data change can leave inventory descriptions, actual effects, and UI disagreeing. Move authorable values to one validated catalog and use references rather than copies.

Puzzle types are branched independently in generation, server action handling, and modal rendering. Every objective currently means solving a door puzzle; every extraction check assumes a mission extraction zone. Enemy AI infers melee versus ranged from projectile speed and has one fixed patrol/alert/engage path. These are real extension costs for QTEs, mixed boss attacks, multi-stage encounters, and noncombat spaces.

The world is embedded in mission lifecycle state: no map in lobby, no world simulation in lobby, and active steps assume combat/extraction. Separate a world's kind and capabilities from session phase. A hub should be able to run movement, collisions, props, inventory, and interactions while disabling combat and mission failure/extraction checks. Keep the existing lobby/preparation experience as the default until a hub is deliberately enabled.

### Recommended boundaries

- Validated content catalog: stable IDs, schema version, classes/loadouts, weapons/effects, enemy definitions, missions/segments, puzzle definitions, world rules, materials, visuals, sounds, and loot tables. Start with JSON; use Pydantic already present through FastAPI. Do not put executable expressions or arbitrary imports in data.
- Runtime state: item/entity instance IDs, transforms, HP, per-weapon ammo/reload, puzzle progress, encounter phase, and world instance/revision. Keep dictionary serialization where useful; use explicit models at external/save boundaries rather than rewriting every entity into a class.
- Small behavior registries: only actual effects, attacks, puzzle handlers, and renderers. New content using an existing behavior is data-only; a genuinely new mechanic still requires well-tested code. Do not build an expression language or generalized plugin platform.
- Shared transitions: command/phase policy, inventory transactions, damage/down/revive, world enter/leave, and tick/event handling. Extract by responsibility, not arbitrary line-count targets.
- Visual definitions independent of collision: `visual_id`, material/sprite references, orientation/animation state, render pivot/bounds, and simple collision/occlusion/interaction metadata. Detailed artwork must not silently change hitboxes.

## Verification and Limitations

The full existing suite passed twice: once with save writes suppressed, then with real store writes redirected into a temporary directory under `/tmp/kilo`. Result: **11 passed**, one FastAPI/TestClient deprecation warning. Existing save files and gameplay source were unchanged.

Review environment: Python 3.14, pytest 9.1.1, FastAPI 0.162.0, Uvicorn 0.54.0, HTTPX 0.28.1, Pydantic 2.14.1, Node 24.19.0. These are observed installed versions, not a recommended dependency pin set.

Focused Python/fake-socket experiments reproduced phase/refill exploits, nonfinite state, malformed commands, cross-player readiness mutation, slow-client blocking, save/migration problems, inventory identity/capacity/transaction failures, projectile defects, event lifetime, sonar, revive, and occlusion issues. Fresh-process WFC runs reproduced hash-seed-dependent generation. Node syntax checks passed all four JavaScript modules; mocked frontend probes confirmed focus/modal/music/projection/render issues.

No real two-browser playthrough, browser XSS execution, mobile-device test, network-load benchmark, asset decode audit, or real process-crash/disk-corruption test was performed. Failure-injection probes used temporary or mocked storage. Snapshot/visibility payload sizes and unauthenticated public level generation should be bounded before public hosting; this review does not claim a measured production throughput or a full security audit.

## Execution Handoff

Follow `implementation-plan.md` in order. `agent-prompts.md` contains a master instruction and one prompt per stage. The work prepares extension points and fixes defects; it does not claim to deliver production artwork, a complete hub, a complete boss roster, or every future puzzle. Friendly fire remains off by default, with its policy tested. Representative QTE, encounter, prop, and hub fixtures demonstrate extension seams without changing the default game.
