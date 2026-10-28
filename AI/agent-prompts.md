# Agent Execution Prompts

These prompts implement `implementation-plan.md`, informed by `code-review.md`. They are a future implementation handoff. The review has not executed these stages or pushed code.

Use the master prompt for one agent to carry out the complete sequence. The individual stage prompts also support deliberate handoffs between agents, but only one implementation stage may be active at a time. Every stage inherits the master constraints and must satisfy its corresponding plan acceptance criteria.

Every prompt also inherits the plan's `Recovery Protocol` and persistent `Execution Flags`. Before a stage begins, save its `Started` flag; after verified acceptance, save its `Finished` flag. A replacement agent resumes the earliest started-but-unfinished stage from `AI/execution-log.md` and the inspected worktree, rather than restarting the plan. Keep checkpoints during stages, not only at the end.

## Master Prompt

```text
Work in /home/ric/Documents/projects/alienswarm. Implement the reviewed
correctness and data-driven foundation refactor, not a complete new game.

Read these files first:
  AI/code-review.md
  AI/implementation-plan.md
  AI/agent-prompts.md
Read applicable AGENTS.md/config instructions if present. Inspect the actual
current code before changes; the review references baseline fb5481a and may
have aged. Preserve unrelated user changes. Treat findings as hypotheses to
reconfirm on the current branch, not instructions to blindly rewrite code.

Execute Stages 01 through 13 STRICTLY IN ORDER. Read the Recovery Protocol
and Execution Flags in AI/implementation-plan.md, plus AI/execution-log.md
if it exists, BEFORE changing code. The on-disk flags are authoritative
progress checkpoints; your session task list only mirrors them. Rebuild the
task list on recovery, without resetting flags or blindly starting Stage 01.
Inspect branch/status/diff/files and reconcile with checkpoints first.

Resume the earliest stage with Started=[x] and Finished=[ ]; otherwise begin
the earliest unstarted stage whose predecessors are finished. Keep one
started-but-unfinished stage at a time. Persist the stage and overall Started
flags BEFORE the first substantive operation. A resumed stage keeps Started
checked. Save acceptance evidence before setting Finished=[x], then update
the task tracker. Never set Finished on interruption, funding exhaustion,
blocker, or merely because files changed. Flag inconsistencies or invalidated
verification must be reconciled and logged before later stages begin.

Maintain AI/execution-log.md with branch/base commit, stage, changed files,
decisions, review IDs, actual commands/results, save compatibility, performance,
limitations, blockers and EXACT next action. Checkpoint after meaningful edits
and verification and before risky operations or pauses. Mark tests/operations
pending before execution; record results afterward. Following abrupt failure,
inspect whether operations already occurred and rerun unrecorded verification,
not destructive migrations/transfers/commits/pushes blindly. Local checkpoints
survive session loss on this workspace, not machine/workspace loss unless
committed/pushed; do not push unfinished work just to synchronize flags.

Follow all stage acceptance gates. Review-only subagents may help, but do not
parallelize implementation stages. Expected-failure tests introduced early
must have an owning stage and become passing regressions before delivery.
Do not silently skip required dependencies or browser acceptance.

Priorities:
  1. Protect existing saves and close verified correctness/security defects.
  2. Establish repeatable isolated tests and truthful verification.
  3. Make tuning/content declarative with stable IDs and strict validation.
  4. Add small behavior/render/world seams proven by minimal fixtures.

Keep current effective gameplay as the default, except intentional defect
fixes. Preserve current storage-only carry-weight semantics. Friendly fire
stays disabled by default. Keep the current lobby/prep flow unless opt-in hub
mode is enabled. Existing levels, progression, stash, and item data have a
concrete migration requirement: never reset/regenerate them silently.

Use JSON for authored catalogues unless a concrete project constraint makes
another format preferable. Separate authored content from runtime saves.
Consolidate classes/loadouts, item weapons/effects, UI capabilities, sounds,
loot rules and tuning; reject unknown references. Behavior implementation,
algorithms and fixed protocol identifiers remain code. Do not put eval,
arbitrary imports, unbounded expressions or scripts in data.

Prefer the smallest correct responsibility boundaries. Do not add an ECS,
plugin framework, generic scripting engine, new frontend framework, account
service, database or build system without a demonstrated need. Preserve
native ES modules and Canvas2D. Define asset/render seams but do not silently
replace the renderer with WebGL or claim GLTF/skeletal models are supported.
True 3D is a later explicit renderer decision. Representative props, a timed
interaction, a boss encounter fixture and a noncombat hub fixture demonstrate
extensibility; production art, complete bosses, narrative hubs and a campaign
are outside this cleanup.

Use temporary stores for every test and experiment; do not modify the real
tracked legacy saves. Back up legacy data through the approved migration
process before an explicit runtime migration. Test error/rollback/retry,
direct domain commands, two-client interactions, and browser state, not just
happy paths. Make simulation clocks/RNG controlled and network queues bounded.

Git delivery is authorized AFTER successful implementation and verification:
inspect status, diff, recent history and upstream first. The review baseline
was main one commit ahead of origin/main; do not push it blindly. Create/use
the named implementation branch refactor/data-driven-foundation unless an
existing user-provided task branch should be used. Never discard user work,
overwrite a branch, stage unrelated files, commit secrets/runtime saves,
amend, bypass hooks or force-push. Before delivery review every commit that
will be pushed, including inherited unpushed commits. Commit intended work
with the repository's message style and push the implementation branch with
upstream using the confirmed origin destination. No PR/merge is requested.

Stage 13 and the OVERALL TASK become COMPLETE only after all required gates
pass, intended changes are committed, the push succeeds, and the remote
upstream is verified to contain the expected commit. If credentials,
protection, tests, or external requirements block delivery, keep the final
stage incomplete/blocked and report the exact blocker; do not claim success.
After verified implementation delivery, record its evidence and set Stage 13
and overall Finished flags. Commit/push the changed plan/log, verify remote
delivery again, THEN mark the mirrored task tracker complete. A failed marker
push is still a delivery blocker even if local flags are checked. On recovery,
verify the remote and pending checkpoint delivery before trusting final flags.

Final report: completed stages, meaningful fixes and extension capabilities,
test commands/results, migration/default behavior notes, branch, commit SHA,
verified remote destination, and honest residual limitations. Use the agent
task tracker to mark completion. If running as a session Goal, also report
the Goal complete only after the same delivery gates. Do not merge to main,
deploy externally or enable production features merely to declare completion.
```

## Stage 01 Prompt

```text
Execute Stage 01 only: Establish Safe, Reproducible Tests.
Apply the Recovery Protocol first. If Stage 01 is already finished, do not
repeat it; continue with the earliest unfinished stage. Save Started before
new work and checkpoint partial progress throughout this stage.
Read the master constraints, plan Stage 01, review R14, existing tests,
application startup, and both store constructors. Inspect Git/dependencies
and record the baseline before editing.

Create small app/room/store construction seams and explicit startup so
tests inject temporary stores and imports do not create or replace saves.
Keep uvicorn backend.app.main:app working. Add appropriate Python dependency
and test manifests plus minimal native-ES-module/browser test setup. Choose
compatible versions, not blindly the review machine's installed versions.
Use explicit required versus optional test groups; do not silently pass when
FastAPI is missing. Exercise application lifespan with controlled time and
deterministic WebSocket message handling.

Preserve existing behavior tests, isolate persistence, and add named fixtures
for upcoming verified regressions. Capture representative baseline snapshot
size, simulation costs and render observations without imposing arbitrary
budgets. Avoid gameplay fixes in this stage unless necessary for test isolation.

Run all applicable baseline tests twice, using actual writes to temporary
stores. Prove legacy save hashes and tracked source outside this stage remain
unchanged. Record installation/test commands and results in execution-log.md.
Mark Stage 01 complete only when its plan gates pass; then begin Stage 02.
```

## Stage 02 Prompt

```text
Execute Stage 02 only after verified completion of Stage 01.
Goal: versioned, recoverable runtime persistence; review R01, R05 and R06.

Separate authored seed/content fixtures from configurable runtime save paths.
Add explicit versioned envelopes and migration of the concrete legacy bare
levels/stash formats. Migrate individual records while preserving IDs,
generated snapshots, completion/testing flags and accumulated stash. Remove
loot-count schema heuristics. Do not regenerate old maps to make migration
easier. Missing files may initialize defaults; malformed/unreadable files must
be retained and reported, not overwritten.

Implement same-directory temporary writes/atomic replacement and a clear
backup/recovery policy. Make failed persistence visible and establish an
explicit single-writer/process guarantee unless concurrency is actually
implemented. Do not assume atomic file replacement makes inventory ownership
transactions safe; those belong to Stage 04.

Test legacy and partially outdated files, unknown/retired IDs, truncated JSON,
invalid stash counts, read-only destinations, injected failures and recovery.
Use temporary files only. Verify unaffected records and originals survive.
Record migration decisions, save location/ignore rules and results. Mark
Stage 02 complete only after all plan gates; continue to Stage 03.
```

## Stage 03 Prompt

```text
Execute Stage 03 only after Stage 02 is complete.
Goal: hardened command boundaries, phase/actor policy and bounded transport.
Review R03, backend/app/main.py and every command's domain method/caller.

Use strict external command schemas and per-message rejection feedback for
malformed JSON/object/action types, unknown IDs, finite bounded numbers,
strict booleans, seeds, sizes and rates. NaN/Infinity must never reach runtime
state or serialization. Validate before mutation, including direct domain
calls for phase/unlock/ownership rules.

Centralize legal phase transitions: reject active class/loadout heal resets
and active mission restarts; enforce unlock/readiness/start/test-mode policy
explicitly. Readiness and destructive loadout changes are self-owned. Keep
intentional squad-wide item assignment supported through an explicit actor/
recipient permission rule instead of blanket self-only inventory changes.

Give each socket one serialized sender with bounded queues/backpressure.
Obsolete snapshots may be replaced; reliable command feedback/events need a
defined bounded policy. Slow sockets must not block peers or simulation.
Make cleanup idempotent, await shutdown cancellation, and use a monotonic
fixed-step schedule with bounded catch-up instead of step+send+sleep drift.
Bound/configure development generation and test-mode entry points without
building an unrelated authentication product.

Close the confirmed roster/preparation player-name innerHTML injection sinks
now with text nodes/safe insertion and regression tests. Stage 09 owns the
broader client/content audit; a known cross-client security defect must not
remain deliberately open while later foundation stages are implemented.

Verify malformed commands, spoofed target_player_id, direct locked/start
calls, denied active resets, legitimate prep flow, slow/failed sockets,
concurrent sends, malicious-name literal rendering, rate/payload limits, and
shutdown. Log policy decisions and
tests; mark complete only after all Stage 03 plan gates, then Stage 04.
```

## Stage 04 Prompt

```text
Execute Stage 04 after Stage 03; review R04/R05 and equipment parts of R08.
Goal: consistent item identity, ammo/capacity invariants and ownership transfers.

Separate template/spawn IDs from unique runtime item instance IDs. Mint new
world-scoped instance IDs when instantiating saved maps while retaining stable
spawn provenance. Nested mutable weapon/effect/value state must not alias a
template or another instance. Unknown templates must fail validation rather
than become credits; handle legacy unknown IDs only in explicit migration.

Track magazine/reload state per weapon instance. Switching to the same slot,
switching back, re-equipping, or changing active selection must not refill
ammo. An empty loadout cannot shoot/reload a fallback class weapon. Preserve
storage-only weight semantics; validate resulting weight and category/slot
compatibility before every accepted inventory transition.

Make nonbankable starter semantics consistent across stash, extraction,
chests, drops and minimum reset paths. Validate, stage and commit each
inventory/stash operation once. Batch extraction; preserve/roll back source
ownership on failures and make retry idempotent. If durable stash withdrawal
leaves items only in memory, add recoverable ownership/reservation settlement
using the smallest store design so a process restart cannot simply erase them.
Return/preserve nonstarter prep gear on disconnect and use recoverable active
world drops without inventing permanent accounts.

Add adversarial invariants: total nonstarter count conserved across failures,
starter stock cannot grow, no duplicate IDs across replayed maps, accepted
swaps remain within weight, rejected mutations change nothing, ammo follows
weapons, disconnect/restart settlement is recoverable. Verify existing loot
and loadout flows. Log results and complete Stage 04 before Stage 05.
```

## Stage 05 Prompt

```text
Execute Stage 05 after Stage 04.
Goal: one validated source of authorable content, preserving effective balance.
Inspect current class/item/mission/effect/audio definitions and all client
fallbacks/heuristics. Read the content boundaries in the review and plan.

Introduce a versioned JSON content catalog separate from saves. Consolidate
classes/capacity/loadouts, weapons, item/ability effects, enemies, missions/
segments, loot tables, rules and sound references; add visual/material entries
when a consumer exists. Classes reference IDs instead of duplicating weapons
and abilities. Move runtime tuning and name-based sound/light-weapon guesses
into explicit fields. Preserve current EFFECTIVE values, not ignored metadata
values accidentally substituted as a balance change.

Use Pydantic or the existing compatible validation stack for strict ranges,
finite numbers, categories, geometry/connector bounds, IDs/cross-references,
known behavior IDs, safe asset paths and prerequisite cycles. Provide a
standalone content validation command and a sanitized client view. Remove
duplicated client balance/capacity tables; render loading/unavailable state
instead of stale fallback stats. Preserve deliberate primitive visual fallback.
No eval/imports/expression language or speculative unused knobs.

Test a catalog edit changing runtime and UI consistently, unknown IDs and
missing/bad assets, invalid geometry/cycles/behavior IDs, and immutable catalog
state. Verify legacy save migration still works and baseline balance has not
changed unexpectedly. Document catalog ownership and commands in the log.
Mark Stage 05 complete before Stage 06.
```

## Stage 06 Prompt

```text
Execute Stage 06 after Stage 05; review R06/R13.
Goal: deterministic generation, truthful saved templates and geometry validation.

Sort every unordered RNG candidate input with a stable key and use explicit
RNG objects. Record requested/resolved seed, generator/content version and
bounded retry diagnostics. Enforce final min/max connected segment counts
and required-room/role constraints, not just a mid-loop maximum.

Instantiation must copy a valid snapshot, mint runtime instance IDs, and NOT
regenerate/save based on loot density. Content/generator upgrades are explicit
versioned operations. Existing saved verified layouts stay intact. Resolve
current geometry issues through a deliberate repair path, preserving progress
and originals rather than silently invalidating testing history.

Consume content loot/placement rules and explicit start/end/template roles;
do not rely on room names. Build ALL walls/cover/prop collision before wire
routing. Strengthen solvability/enclosure/interaction checks using actual
player footprint and swept transitions. Validate output before persistence/
launch, with bounded reject/retry and diagnostic errors.

Run fresh-process determinism tests with multiple PYTHONHASHSEED values,
representative seed sweeps for every mission, sparse/zero-loot fixtures,
min/max constraints, invalid connectors, thin barriers/enclosure, interaction
reachability and wire-wall intersections. Repeated starts must not alter
saved templates. Record results/performance and complete before Stage 07.
```

## Stage 07 Prompt

```text
Execute Stage 07 after Stage 06; review R07/R08 and sonar portion of R12.
Goal: reliable shared combat transitions with optional friendly-fire policy.

Compute swept projectile/collider hits and choose the nearest eligible impact
across walls and actors, with deterministic tie-breaking and explicit radius,
range and TTL rules. Do not assume endpoint distance is sufficient at 30 Hz.

Share damage/mitigation, down/death, kill attribution, revival and body-chest
flags across bullets, melee, shield/shockwave, fields and future hazards.
Transitions must be idempotent. No ghost class weapon when inventory is empty;
field revival resets the same relevant state as manual revival.

Use factions and validated target rules instead of a binary player/other
branch. Friendly fire stays OFF by default; opt-in policy tests cover allies,
owner/self-hit, deployables/area damage, immunity and attribution. Beneficial
effects retain separate targeting. Larger boss hit volumes must use the same
collision/damage code. Sonar visibility uses each emitter's content radius,
position and duration rather than one global all-enemies flag.

Verify rail tunneling, actor-before-wall ordering, nearest impact, default and
enabled friendly fire, every kill source, repeated down/revive/down, empty
equipment, and out-of-range sonar. Keep current missions playable. Log and
complete Stage 07 only when all plan acceptance gates pass, then Stage 08.
```

## Stage 08 Prompt

```text
Execute Stage 08 after Stage 07; review R09/R10.
Goal: exactly-once gameplay event processing and bounded deduplicated audio.

Separate command-created pending gameplay events from bounded presentation
history. Use stable sequence/event IDs, simulation timestamp and world ID.
AI consumes sound events once; per-listener snapshot reads are pure and must
not steal or clear another listener's events. Define late-join/history expiry
and feedback/snapshot delivery interaction, then implement client dedup.

Use the single validated sound catalog end-to-end. Fix supported reload,
door/alarm/alien paths and actual AI/music state classification. Send music
policy on lobby/world transitions. Preserve distinct AI hearing vs audio mix
parameters rather than assuming they must be identical.

Make audio startup readiness/user-gesture aware and retry missing-buffer
states after loading. Teardown/world changes clear obsolete voices, ambience,
music latch and dedup state. Missing real assets have explicit fallback/error
behavior. Connect ambience emitters only where content supplies them.

Occlusion uses explicit active/door/material flags, `is not None` ray hits,
and world/geometry revision cache identity. Stereo pan follows projected
screen orientation. Test open spaceship ramp, inactive/moved geometry and
cross-world cache invalidation.

Verify alarm hearing once, repeated broadcasts/terminal frames, two listeners,
late joins, early music state before load, returning to lobby, every emitted
sound ID, teardown and occlusion. Record actual tests and complete before 09.
```

## Stage 09 Prompt

```text
Execute Stage 09 after Stage 08; review R02/R11 and client R12/R14.
Goal: safe DOM text, authoritative modal state and focus-safe input.

Preserve the existing visual design and native JS stack. Extract only useful
network/state/input/modal responsibility boundaries from main.js. Replace
untrusted name/content HTML interpolation with text nodes/safe attribute APIs;
audit prep, roster, chest and event sinks as well as class/content display.

Add input contexts and neutral-intent resets for blur/hidden-tab/disconnect/
modal/world/phase changes. Suppress unrelated combat hotkeys and unwanted
repeat actions in modal contexts. Reconcile opened puzzle/chest sessions from
authoritative snapshots; correlate target/request IDs and ignore stale replies
after close or world change. Teammate puzzle completion must update the
correct open modal.

Send explicit IDs for clicked interactions and validate target/range/line of
sight server-side. Keep generic nearby interaction as a separate deliberate
action. Replace client Shift/name heuristics with catalog/server capabilities;
stationary/backward Shift must not wrongly block heavy-weapon fire.

Fix responsive layout/modal size for narrow windows and device-pixel-ratio
canvas coordinate consistency without claiming full touch controls. Add
browser regressions plus focused unit tests: malicious name literal display,
focus loss stop, modal combat suppression, late responses, co-op solve, chosen
chest selection/range feedback and Shift firing. Run a real two-client browser
smoke and narrow viewport checks. Log any unavailable browser gate as a
blocker, not a pass. Complete Stage 09 before Stage 10.
```

## Stage 10 Prompt

```text
Execute Stage 10 after Stage 09; review visual/material/prop boundaries and R12.
Goal: replaceable visuals independent of physics, with current Canvas2D intact.

Create a small renderer-facing scene/asset contract using entity instance IDs,
transforms, visual/material IDs, pivots/bounds/layers, orientation/animation,
and independent collider/occluder/interaction metadata. Do not stream assets
in snapshots and do not add unused GLTF/WebGL code. The current procedural
humanoid is 2.5D; describe its limits truthfully.

Add a validated asset cache/loader with readiness, errors, fallbacks and
cleanup. Support surface image textures/directional sprites through tiny
legitimate test assets or available project assets. Missing assets keep the
game usable. A future 3D renderer should have a clear replaceable boundary,
not a speculative second implementation.

Place consoles/crates/loot/props/actors in appropriate shared ordering;
keep labels/markers as HUD overlays. Correct long-wall overlap using tested
geometry/layer handling, and mark exploration only through actual visibility.
Add a tiny data-authored scene with chair/table/console archetypes showing
blocking/nonblocking/interaction metadata without name special cases.

Test texture/reference swaps, missing-asset fallback, asset reuse/disposal,
data-only prop placement, long-wall/actor ordering, hidden exploration and
render-state immutability. Compare measured frame costs to Stage 01, cache
only justified static work, and run browser smoke/narrow viewport checks.
Log supported asset formats and explicit true3D limitation. Complete before 11.
```

## Stage 11 Prompt

```text
Execute Stage 11 after Stage 10.
Goal: bounded interaction/objective/encounter handlers, not a scripting engine.

Move power_reroute and signal_decrypt to small validated handler registries
for generation/server actions/client rendering. Definitions are separate from
runtime sessions/progress. The server owns reach, capability, target and
session authorization. Parameter/content variants use data only.

Map existing missions to explicit objectives/dependencies/completion effects
instead of assuming every puzzle is an extraction-gating door. Reject unknown
effects/dependency cycles. Keep a small supported action set; no eval, imported
plugins or general-purpose expression language.

Add ONE minimal timed interaction/QTE test fixture with authoritative start,
deadline, attempt/session ID, replay protection and tick-driven timeout.
Client reports input, never success or trusted elapsed time. Verify valid,
expired, replayed and wrong-session actions without real-time sleep flakiness.

Separate enemy perception/movement from actual attack profiles enough for
mixed melee/ranged and a data-defined multi-phase boss fixture using shared
damage/events. Thresholds/cooldowns/profile references are data; genuinely
new attacks use localized code handlers. Preserve ordinary AI behavior.

Verify existing puzzles, data-only parameter variants, zero-puzzle/mixed
objective missions, QTE lifecycle and boss fixture phase/objective transitions.
Do not add a production boss roster/campaign or claim artwork is complete.
Record extension recipes and complete Stage 11 before Stage 12.
```

## Stage 12 Prompt

```text
Execute Stage 12 after Stage 11.
Goal: world kind/capabilities independent of mission lifecycle, proven by an
opt-in noncombat intermission fixture without replacing the default lobby.

Add world instance identity/kind and explicit system capabilities separate
from session phases. Reuse movement, collision, props, inventory and interaction
in mission and hub worlds. Combat/AI/objective/extraction/defeat processing is
selected by rules, not assumptions that a map means a mission or that lobby
means no simulation. Snapshots/UI must handle worlds without a ship/extraction.

Add a tiny data-defined noncombat hub scene with a stash console and mission
selection/departure interaction, using existing or placeholder visuals. Keep
the present lobby/prep flow as the default; enable the fixture explicitly.
Hub combat commands are rejected server-side, not just hidden in UI.

Specify entry/departure/return on victory and defeat, late join, disconnect,
reconnect, inventory ownership/settlement and event/render-world reset. Reuse
the verified Stage 04 settlement and Stage 08 event identity; do not create
an account/social/NPC product in this stage.

Verify movement/collision/noncombat interaction, no hostile damage or mission
failure/extraction timers in hub, departure and return without item loss or
duplication, late joins/disconnects and unchanged default mission tests.
Run the real browser fixture smoke. Record opt-in/config instructions and
limitations. Complete Stage 12 before the final delivery stage.
```

## Stage 13 Prompt

```text
Execute Stage 13 only after verified completion of Stages 01-12.
Goal: truthful final verification, documentation, commit, push and task completion.

Run the committed content validation entry point, all Python tests, frontend
unit tests, browser regressions/smoke, legacy migrations and persistence
failure/rollback/restart tests, seed sweeps/fresh-process determinism, and
two-client mission plus opt-in hub-fixture playthroughs. Verify real save hashes
remain unchanged. Remove or resolve every tracked expected failure according
to its owning finding; do not hide failures with relaxed assertions/skips.
Inspect queue bounds, console/server errors, missing assets, resource cleanup
and measured costs against Stage 01. Every required acceptance gate needs
actual evidence; unavailable verification is an honest delivery blocker unless
the user explicitly narrows the scope.

Update README to the real implemented architecture/protocol/setup/test/save
behavior and remove stale/conflicting examples. Explain data vs code vs save
state, extension recipes, versioned migration/backup, supported visuals and
true3D limit, friendly-fire defaults, single-writer limitations and opt-in
fixtures. Add CI running the verified commands with temporary storage.

Finalize execution-log.md with completed implementation/verification and
pending delivery commands before committing. Inspect git status, git diff,
git diff --check, git log --oneline -10, origin/upstream and every commit to
be pushed. Stage only task files. Never include runtime saves, secrets or
unrelated user changes. Keep the chosen implementation branch; do not merge
to main, create a PR, amend, bypass hooks or force-push.

Commit using the repo style. Run required hooks; fix failures and commit
normally. Push the confirmed implementation branch to origin with upstream.
Use git fetch/read-only remote checks as needed and verify the exact expected
commit is present in the remote upstream. A successful local commit alone or
attempted push is NOT completion. If tracked execution-log updates occur after
push, commit and push those too before claiming a fully delivered clean branch.

After successful implementation push verification, record the delivery
evidence and set Stage 13 and overall Finished flags in implementation-plan.md.
Commit/push the changed plan/log and verify that delivery too. Only then mark
the mirrored task tracker COMPLETE; if this is a session Goal, report it
complete on the same evidence. If interrupted between these operations,
reconcile local commits, flags, pending files and the actual remote before
repeating any commit/push or claiming completion. On credentials/protection/
test failure retain an incomplete delivery checkpoint and report the precise
blocker without force; locally checked flags never override failed delivery.

Final response: concise fixes and data-driven seams, actual test results,
save/default compatibility, branch, final commit SHA, verified remote
destination, and explicit remaining renderer/fixture/public-hosting limits.
```
