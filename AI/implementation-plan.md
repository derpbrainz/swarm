# Sequential Implementation Plan

Companion files: `code-review.md` for baseline evidence; `agent-prompts.md` for execution instructions.

## Objective and Scope

Fix verified defects, centralize authorable content, and establish small tested extension seams for textures/sprites/models, scenery, interactions/QTEs, bosses, friendly fire, and an intermission world. Preserve the present game while doing this incrementally. Do not implement a full 3D rewrite, account service, arbitrary plugin engine, ECS, production boss/hub content, or large asset pipeline as part of this cleanup.

Data-driven does not mean zero constants or zero code. Balance, assets, rules, content references, and tunable timing belong in data. Algorithms, protocol discriminators, mathematical constants, and behavior implementations belong in code. A new combination of existing mechanics should usually be data-only; a new mechanic needs a bounded code handler and tests.

All 13 stages run sequentially. Set a stage's persistent `Started` flag before working on it, then set `Finished` only after implementation, verification, and checkpoint logging pass. Finish a stage before beginning the next. A failing or interrupted stage remains started but unfinished; fix or resume it, or record a concrete blocker without claiming completion. Review-only subagents may assist, but do not run concurrent implementation stages.

## Delivery Rules

The `Execution Flags` table in this file is the persistent source of stage progress; an agent's task tracker is only a mirror and may disappear with its session. Keep one started-but-unfinished stage at a time. Maintain `AI/execution-log.md` with the working branch/base commit, changed files, decisions, commands/results, resolved review IDs, compatibility/migration notes, remaining limitations, blockers, and the exact next action. Save a checkpoint after each meaningful edit or verification, and before a risky operation or planned pause, not just at stage completion. Record a test as pending before running it and record its result afterward, so a disconnect cannot turn an unobserved result into a claimed pass. Do not modify this review's baseline findings to pretend fixes were already present.

Inspect the Git branch/upstream, status, diff, and recent history before changes. The review baseline was a clean `main` one commit ahead; recheck rather than assuming that state still holds. Preserve user changes and existing saves. Work on a named branch such as `refactor/data-driven-foundation` unless the user already provided a task branch; do not overwrite an existing branch or switch away from a dirty worktree by discarding work.

Completion requires verified implementation, saved progress records, and a local commit, not a Git push or remote verification. Stage only intended files, review the local changes, obey hooks, and never amend without separate permission. Do not push automatically after a stage or the overall task; pushing requires a separate explicit user request. GitHub credentials, remote access, and branch protection are not completion gates. No PR creation or merge into main is included unless subsequently requested.

## Recovery Protocol

Flags use `[ ]` for false and `[x]` for true. Both false means not started; started true/finished false means partial, interrupted, or blocked; both true means verified finished. Finished true/started false is invalid. The flags are checkpoints, not locks, evidence of an active agent, or authorization to change scope.

1. At startup or after a disconnect/funding interruption, read this plan's flags and `AI/execution-log.md` before making changes. Rebuild the session task tracker from these files; do not reset existing flags or restart from Stage 01 merely because the conversation is new.
2. Inspect the current branch, status, diff, recent commits, and relevant files. Reconcile them with the checkpoint, preserving partial implementation and unrelated changes. Flags alone are not proof that files, tests, or local commits still match the recorded state.
3. Resume the earliest stage with `Started` checked and `Finished` unchecked. If there is no such stage, begin the earliest unstarted stage whose predecessors are finished. If flags are inconsistent or several stages are unfinished, resolve the earliest incomplete stage and document the discrepancy before proceeding.
4. Continue from the last verified checkpoint. Inspect whether pending edits, migrations, tests, or local commits already happened before repeating them. Rerun pending/unrecorded verification; never blindly repeat a potentially destructive migration, transfer, or commit.
5. Before the first substantive operation, persist the overall and stage `Started` flags and a checkpoint naming the next action. During recovery, leave existing `Started` flags checked and update the checkpoint; do not label recovery as a fresh stage.
6. Record acceptance evidence before checking a stage's `Finished` flag. If later changes invalidate that evidence, uncheck `Finished`, keep `Started` checked, and log why revalidation is necessary. Mirror the persistent status in the task tracker only after saving it.
7. A blocker, lost connection, or depleted budget never sets `Finished`. Save the next action and blocker when possible; abrupt interruptions are recovered from the most recent on-disk checkpoint and current worktree.
8. Stage 13 and the overall `Finished` flags require successful implementation and verification, with evidence saved locally. Include the final flags and log in the local completion commit. If interruption occurs before that commit finishes, inspect pending changes, hook results, and local history before claiming completion. No repeated implementation or commit is needed solely because the previous session failed to report success. Do not contact or push to a remote as a recovery requirement.

These local checkpoints survive session disconnection on the same workspace. Local files and commits do not provide recovery after workspace loss without a separate backup. Remote synchronization is optional and requires an explicit user request; it is not part of stage completion or recovery.

## Stage 01 - Establish Safe, Reproducible Tests

Review coverage: R14; prerequisite for every later stage.

1. Record baseline behavior and current dependencies; add an appropriate Python dependency/test manifest with compatible bounded versions and repeatable installation commands. Add only minimal frontend test tooling needed for native ES modules and browser smoke tests.
2. Create app/room/store construction seams so tests use temporary stores and do not mutate module-global production saves. Move singleton bootstrapping to explicit application startup while keeping the documented Uvicorn import target usable.
3. Replace silent missing-dependency passes with explicit skips only for declared optional tests; required integration dependencies must fail clearly. Run TestClient with the lifespan active and control simulation time to avoid flaky message ordering.
4. Add regression fixtures without changing gameplay yet. Measure baseline snapshots and representative simulation/render costs for later comparison; no arbitrary optimization targets.

Acceptance: existing behavior tests pass against temporary files; imports do not create/replace saves; two test runs leave tracked save hashes unchanged; HTTP/WebSocket/lifespan coverage actually executes; installation and test commands are documented in the execution log. Security/gameplay regressions can initially be explicit expected failures with a recorded owning stage, never silently ignored.

## Stage 02 - Protect and Version Runtime Saves

Review coverage: R01 and persistent-write portions of R05/R06.

1. Separate source-controlled content/seed fixtures from runtime state in a configurable writable save directory. Preserve the concrete legacy `levels.json` and `shared_stash.json` formats through an explicit, tested import/migration path.
2. Introduce save schema versions and record-by-record migrations. Preserve IDs, tested/completed flags, created metadata, and user-generated levels. Invalid/unreadable files must be retained and reported, not overwritten with defaults.
3. Use temporary-file writes in the same directory plus atomic replacement and a defined backup/recovery policy. Initialization of defaults should be one save, not repeated partial files.
4. Make persistence failures observable. Enforce/document single-writer operation; reject unsupported multiworker startup or provide locking/transactional persistence if support is needed. Do not introduce a database solely for fashion.

Acceptance: legacy fixtures survive migration; one outdated record cannot reset siblings; malformed/truncated/read-only/disk-failure fixtures leave originals recoverable; absent saves initialize explicitly; runtime files are ignored by Git; real temporary-file persistence tests pass. No count-based schema upgrade remains.

## Stage 03 - Harden Commands, Phase Policy, and Delivery

Review coverage: R02/R03/R15, transport reliability, public endpoint boundaries.

1. Validate all HTTP/WebSocket commands with strict schemas: object/action shape, known IDs, finite numbers, strict booleans, bounded strings/seeds/payload sizes/rates, and clear error feedback. Clients must tolerate error messages without assuming success.
2. Centralize phase and actor/recipient policy. Self-owned readiness/class/loadout changes cannot target another player. Retain intentional squad item assignment through explicit permissions. Mission start must enforce legal transitions and unlocks, including direct calls and test-mode behavior.
3. Reject active class/loadout resets and active mission restarts. Ensure player actions after downing/terminal mission states follow an explicit rule rather than accidental missing guards.
4. Use one serialized sender per socket with bounded backpressure: snapshots can replace obsolete pending snapshots; reliable feedback/events need a documented bounded policy. Slow clients must not suspend all other clients or simulation ticks. Handle disconnects idempotently; await background-task cancellation on shutdown.
5. Schedule fixed simulation steps using a monotonic clock and bounded catch-up, without making send duration part of the simulated tick. Keep step frequency/tuning in the eventual shared settings catalog.
6. Restrict or explicitly configure development-only level-generation/test-mode endpoints before public hosting; do not add a large authentication system to solve a local development control.
7. Close the confirmed player-name HTML sinks immediately using text nodes/safe insertion. Stage 09 performs the broader client/content audit; do not leave a known cross-client script-injection path open through intervening stages.

Acceptance: malformed JSON/list/null/nested actions and NaN/Infinity cannot corrupt a room; malicious names render as literal text; denied commands do not mutate state; direct locked/restart/heal exploits fail; legitimate preparation flow and squad transfers work; fake slow sockets do not block fast peers or grow unbounded queues; simultaneous feedback/snapshots preserve valid sends; disconnect/shutdown cleanup is tested.

## Stage 04 - Make Inventory State and Transfers Consistent

Review coverage: R04/R05 and equipment-related R08.

1. Distinguish immutable item definitions from runtime instances; deep-copy mutable instance state and reject unknown template IDs. Mint world-scoped globally unique item instance IDs on map instantiation; preserve provenance separately.
2. Put magazine/reload state on weapon instances. Switching/re-equipping must preserve it, including same-slot switches. No equipped weapon means no shooting/reloading; keep compatibility with existing save fixtures explicitly.
3. Validate resulting storage weight and slot/category compatibility before every inventory mutation. Preserve current storage-only capacity semantics.
4. Prevent starter-item banking/duplication consistently across stash, extraction, body chest, drop, and reset paths. Existing free minimum loadout remains available only in allowed preparation states.
5. Make stash/inventory/extraction transitions commit once with rollback or recoverable settlement on persistence failure. Batch extraction deposits, remove source items exactly once, and make retry behavior safe. A persisted stash withdrawal must retain recoverable ownership/reservation state rather than leaving its only remaining copy in process memory.
6. Settle disconnects explicitly: return lobby/preparation nonstarter gear or preserve it; create recoverable in-world drops for active disconnects. Avoid inventing permanent player accounts.

Acceptance: repeat starter-bank/restore does not grow stash; same-slot switching does not refill; two mission instances cannot create colliding retained item IDs; all accepted swaps satisfy capacity; failed transfers leave a consistent total item count; extraction retry cannot double items; disconnects do not erase withdrawn gear; all existing intended loot/loadout behavior still works.

## Stage 05 - Centralize and Validate Authorable Content

Review coverage: duplicated content/effects, R10 registry mismatches, future extension requirements.

1. Establish one JSON content root, separate from runtime saves, with a schema/content version and stable IDs. Start with classes/loadouts/capacity, weapons, ability/item effects, enemies, missions/segments, loot tables, sounds/music, rules, and visual/material definitions when consumed by later stages.
2. Use an existing compatible validation dependency, preferably Pydantic already required by FastAPI. Validate finite/ranged fields, IDs/references, categories, behavior IDs, asset paths, connector/geometry bounds, and prerequisite references/cycles at startup and through a command-line validation entry point.
3. Eliminate duplicated class-versus-item weapons/abilities and frontend fallback balance/capacity tables. Classes reference weapon/ability/loadout IDs; resolve read-only views for existing API/UI consumers rather than keeping duplicate sources. Move runtime radii/rates, loot pools, and sound selection out of name-based guesses.
4. Publish one sanitized client catalog, including capabilities/display/asset metadata; do not leak private puzzle solutions just because a public view is convenient. Existing signal puzzle currently displays its sequence, so preserve that particular mechanic unless deliberately changed.
5. Use only consumed fields and bounded behavior IDs. No arbitrary expressions/eval/imports in content and no speculative configuration for unimplemented features. Keep intentional visual fallback descriptors, not fake balance defaults that hide bad content.

Acceptance: editing a weapon, carry capacity, effect radius, or sound reference changes actual runtime behavior and UI consistently; unknown/broken references fail validation with useful paths; item mutation cannot alter content; no duplicated balance source remains; content validation tests cover bad geometry, unknown behaviors, missing assets, and prerequisite cycles. Legacy saves still load via Stage 02 migration.

## Stage 06 - Make Generation Deterministic and Validated

Review coverage: R06/R13 and generic world construction needs.

1. Sort set-derived WFC candidates and make RNG usage explicit. Store requested seed, resolved seed, generator version, and content version, with diagnostics for bounded failed attempts. Final min/max segment and connectivity constraints must be authoritative.
2. Remove loot-count-triggered map regeneration. Instantiation copies a saved template and mints runtime IDs; content/generator upgrades require an explicit migration/regeneration operation, not a mission-start surprise.
3. Use content loot tables/spawn metadata and explicit template roles instead of ID/name special cases. Remove dead configuration only after checking consumers and persisted needs.
4. Build complete collision geometry before wire/decoration routing. Use shared collision primitives for swept navigation checks, interaction reachability, enclosure, connectors, and extraction access.
5. Validate generated maps before persistence or launch. Rejection/retry must be bounded and diagnostic. Keep test-phase verification distinct from automatically asserted geometry validity.

Acceptance: the same seed/content/generator version yields identical structural output in fresh processes under several hash seeds; final sizes obey constraints; zero/sparse loot maps remain valid without rewrites; repeated starts do not modify persisted templates; thin barriers and unenclosed maps fail; representative seed sweeps for every mission satisfy valid progression; wire routes do not cross blocking geometry. Old saved layouts are preserved unless an explicit versioned repair is necessary.

## Stage 07 - Unify Hits, Damage, Death, Revival, and Target Policy

Review coverage: R07/R08/R12 sonar range; future friendly fire and bosses.

1. Resolve nearest swept wall/entity impacts with deterministic ordering. Account for target and projectile footprints; define range/TTL endpoint behavior explicitly.
2. Share damage, mitigation, down/death, kill attribution, revival, and body-chest state transitions across bullets, melee, shockwaves, healing fields, and future hazards. Keep each transition idempotent.
3. Introduce faction/target rules independent of literal `player` versus everything-else branches. A data-configured friendly-fire policy must cover projectile, melee, area/deployable effects, owner/self-hit, and damage attribution.
4. Keep friendly fire disabled by default; test opt-in behavior. Healing and other beneficial effects must not accidentally use hostile targeting rules. Larger hit volumes should work without special-casing boss names.
5. Apply sonar/vision effects by per-emitter position/radius/duration using catalog values, not a global reveal flag. Publish authoritative vision/capability information where it prevents client drift.

Acceptance: rail shots cannot tunnel; a nearer actor is hit before a farther wall; nearest eligible collider wins; every death source counts consistently; repeated down/revive/down produces the correct chests; empty equipment cannot fire; optional friendly fire hits allies and honors owner/immunity rules while default gameplay remains unchanged; enemies outside sonar radius remain hidden; effects use content values.

## Stage 08 - Repair Event and Audio Lifecycles

Review coverage: R09/R10.

1. Queue command-created gameplay events until a tick consumes them; keep bounded presentation history with stable event IDs/sequence and simulation/world time. Do not mutate events during per-listener snapshot reads.
2. Deliver/deduplicate transient events across extra broadcasts and late joins; expired or previous-world events must not replay. Preserve AI hearing radii separately from optional presentation audio propagation.
3. Connect the validated sound catalog to server emitters and client asset keys. Fix reload/door/alarm/alien event paths as supported by actual assets; do not claim sounds exist for missing assets. Music states must match actual AI/world state and be sent on lobby transitions.
4. Make playback asset-readiness and user-gesture aware, with retryable failure/fallback behavior. Clear voices/ambience/music state appropriately on teardown and world changes.
5. Use explicit active/door/material geometry, correct `is not None` hit tests, and world/geometry-revision-scoped occlusion caching. Pan along the isometric screen axis. Connect ambience only when authored emitters actually exist.

Acceptance: puzzle alarm alerts nearby enemies exactly once; two listeners receive equivalent event IDs and appropriate occlusion; command broadcasts/terminal frames do not replay sounds; normal engagement triggers combat music; early-state-before-load eventually starts music; returning to lobby silences/changes it; all emitted sound IDs validate; open ramp and inactive surfaces do not occlude; moved geometry/world changes invalidate caches.

## Stage 09 - Stabilize Client State, Input, and Responsive UI

Review coverage: R02/R11, client portions of R12/R14.

1. Extract small networking/state, input-context, and modal controllers from the large client file where responsibilities warrant it; do not switch frameworks or redesign the existing UI.
2. Render untrusted text with DOM text APIs; audit roster, prep, event, chest, and future data-driven content sinks. Keep HTML templates only for trusted static structure with safe text/attribute insertion.
3. Reset input and send neutral intent on blur, hidden tab, disconnect, modal/world/phase changes. Modal contexts suppress unrelated combat commands. Keyboard repeat and duplicated action requests must not cause repeated unwanted activation.
4. Reconcile puzzle/chest instances from authoritative state and correlate responses with still-open target/request IDs. Ignore stale responses. Send explicit target IDs for clicked interactions, with server-side range/line-of-sight validation and generic nearby-interact retained as a deliberate separate action.
5. Use server/catalog weapon and movement capabilities; do not suppress firing just because Shift is held. Server remains the authority for actual sprint/action eligibility.
6. Add responsive layout and modal sizing for narrow windows/mobile without promising touch gameplay controls not currently implemented. Verify device-pixel-ratio canvas sizing and input-coordinate consistency.

Acceptance: malicious names display as text; focus loss stops movement/fire; modal keys do not consume combat items; stale replies do not reopen UI; teammate puzzle solves close/update the right modal; clicked chests open the chosen target or return explicit range feedback; stationary/backward Shift shooting matches server rules; two-client browser smoke passes; narrow-window screens have no inaccessible controls or clipped modals.

## Stage 10 - Add Visual, Material, and Prop Extension Seams

Review coverage: render-order/visibility R12; textures, detailed visuals, scenery.

1. Keep Canvas2D working. Define a renderer-facing scene from stable entity IDs, transforms, visual/material IDs, render pivots/bounds/layers, orientation/animation state, and separate collider/occluder metadata. Do not stream mesh or texture bytes over WebSockets.
2. Add a small asset registry/cache with validated relative paths, readiness/error states, sensible missing-asset fallbacks, and resource cleanup. Support image textures/directional sprites when real or tiny test assets are available. Reserve a clear adapter boundary for a future chosen 3D backend, without adding an unused GLTF dependency.
3. Unify props, consoles, crates, loot, actors, and walls in the appropriate scene ordering; HUD markers remain a separate overlay. Fix long-wall ordering using a tested strategy such as wall segmentation/render bounds, and explored visibility using actual visibility results.
4. Add content-defined nonblocking and blocking props with visual, collision, occlusion, and interaction metadata. A chair/table/console test scene demonstrates that adding an instance of an existing archetype is data-only. Keep artwork placeholders explicit.
5. Cache static surfaces/visibility only where baseline measurements justify it. Asset/render changes must not move authoritative hitboxes or modify game state during rendering.

Acceptance: a material/visual reference swap works without gameplay edits; missing assets fall back without crashing; prop addition using supported metadata is data-only; actors/props/long walls overlap correctly in fixtures; hidden cells are not marked explored; render tests do not mutate state; desktop/narrow-window smoke and frame-cost comparisons are recorded. Actual skeletal/GLTF import remains a separately scoped renderer decision.

## Stage 11 - Generalize Interactions, Objectives, and Encounters

Review coverage: puzzle/mission/AI hardcoding; future puzzles/QTEs/bosses.

1. Move the existing two puzzles into bounded generation/action/client-renderer handlers selected by validated IDs. Keep puzzle definition distinct from runtime session/progress, with target/capability/reach checks owned by the server.
2. Replace "all door puzzles solved" as the sole mission rule with explicit objective records/dependencies and completion effects using a small supported effect set. Preserve existing missions through data mappings. Reject cycles/unknown effect IDs; do not build a scripting language.
3. Add one minimal timed-interaction/QTE fixture with server start/deadline, attempt/session ID, replay protection, and tick-based timeout. Use simulated server time, not client-reported success or elapsed time. Keep production QTE difficulty/content out of this cleanup.
4. Separate enemy perception, movement, and supported attacks/encounter transitions enough to allow melee+ranged profiles and a multi-phase boss fixture. Choose bounded behavior IDs and data thresholds/cooldowns; keep ordinary patrol/alert/engage behavior intact. Reuse shared damage/events, not duplicated boss combat code.
5. Prefer code registries of functions/modules for genuinely different mechanics; variations of an existing puzzle/attack/encounter are data. Keep extension counts and modules small.

Acceptance: current puzzles and missions still work; a new instance/parameter variant needs data only; one new handler requires a localized server/client pair and tests, not edits across the simulation; timed fixture accepts valid input and rejects expired/replayed/wrong-session actions; zero-puzzle and mixed-objective missions work; boss fixture changes phases/attack profiles and completes an objective using existing systems; no new production boss or campaign is claimed.

## Stage 12 - Separate World Kind From Mission Lifecycle

Review coverage: hardcoded lobby/mission coupling; future intermission world.

1. Add world instance identity/kind and capabilities separate from session phases. Reuse movement/collision/props/interactions in both mission and hub worlds; choose combat/AI/objective/extraction/failure systems through explicit rules.
2. Extract only the lifecycle assumptions preventing noncombat simulation: lobby implies no world, active implies enemies/extraction, snapshots imply spaceship, and frontend phase implies game visibility. Keep leadership/readiness/preparation and shared inventory coherent.
3. Add an opt-in, data-defined noncombat hub fixture/test scene using existing visuals, stash console, and mission-selection interaction. Keep the current lobby default unless the user deliberately enables hub mode.
4. Specify/test entry, mission departure, victory/defeat return, reconnect/disconnect, loadout/item settlement, event reset, and late join. Hub combat commands must be rejected server-side, not merely hidden in UI.

Acceptance: hub fixture allows movement/collision/stash interactions with no hostile damage, mission timers, extraction, or squad-defeat checks; squad can depart and return without losing/duplicating inventory; mission tests still pass; world events/IDs/render exploration reset correctly; no UI-only combat suppression; production narrative/NPC/social/account features remain out of scope.

## Stage 13 - Verify, Document, Commit, and Complete

Review coverage: every finding and delivery requirement.

1. Resolve every planned regression/expected failure or document why a finding is superseded with evidence. Run content validation, all Python tests, frontend unit tests, browser smoke, save migrations/failure injection, seed sweeps/fresh-process determinism, and two-client mission/hub-fixture playthroughs.
2. Verify test runs do not change real saves. Inspect performance against Stage 01 measurements, logs, memory/queue bounds, missing assets, and browser console. State any test that could not be executed; do not mark its criterion complete without evidence or explicit scope reduction.
3. Update README to the implemented architecture/protocol and real setup/test/save commands. Explain content versus code versus runtime state, extension recipes, save migration/backup, renderer limits, friendly-fire defaults, single-writer limits, and opt-in fixtures. Remove conflicting old protocol examples. Add CI using the verified commands and temporary stores.
4. Record final implementation and verification evidence in the execution log. Inspect `git status`, `git diff`, `git diff --check`, `git log --oneline -10`, and the intended local changes. Stage only task files; never include real saves/secrets or unrelated changes.
5. After all implementation and verification gates pass, check Stage 13 and the overall `Finished` flags and include the updated plan/log in a concise local completion commit. Run required hooks and fix failures normally; never amend without separate permission. If the commit or a hook fails, uncheck the final `Finished` flags and record the blocker before retrying. No automatic push or remote verification is required or authorized by this plan.
6. Verify the local commit contains the intended implementation and final progress documents, then mark the mirrored execution task tracker complete. On recovery, inspect local history and pending changes even when local flags are checked. Give the final branch, local commit SHA, test results, and honest residual limitations; do not claim the work was pushed.

Acceptance: all required gates pass; documentation matches actual code; intended changes and final progress documents are committed locally; no unintended dirty files or runtime saves remain; all stage and overall flags are finished and the mirrored tracker is complete. A failed local commit or required verification remains in progress/blocked and is reported accurately. No Git push, GitHub authentication, or remote verification is necessary for completion.

## Execution Flags

Update this table on disk as work proceeds. Flags began false before implementation; the saved values below are the current recovery checkpoint. The overall row summarizes the whole execution; it is not an additional stage.

| Stage | Task | Started | Finished |
| --- | --- | --- | --- |
| Overall | Complete verified implementation and local commit | [x] | [x] |
| 01 | Safe, reproducible tests | [x] | [x] |
| 02 | Versioned, recoverable saves | [x] | [x] |
| 03 | Validated commands, phase policy, bounded delivery | [x] | [x] |
| 04 | Consistent inventory and transactions | [x] | [x] |
| 05 | Validated central content catalog | [x] | [x] |
| 06 | Deterministic, verified generation | [x] | [x] |
| 07 | Shared combat and target policy | [x] | [x] |
| 08 | Reliable events and audio | [x] | [x] |
| 09 | Safe client state/input/responsive UI | [x] | [x] |
| 10 | Visual/material/prop seams | [x] | [x] |
| 11 | Interaction/objective/encounter seams | [x] | [x] |
| 12 | World-kind and hub fixture | [x] | [x] |
| 13 | Final verification, documentation, local commit, completion | [x] | [x] |
