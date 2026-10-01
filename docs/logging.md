# LiteBuild Logging — High-Level Specification

## 1. Purpose

LiteBuild builds can involve many  steps, multiple external tools, parallel execution, cached outputs, and 
long-running operations. A useful logging design therefore needs to make both the **current state of the build** 
and the **reasoning behind what ran or did not run** easy to understand without forcing users to interpret an 
interleaved stream of tool output.

LiteBuild logging serves two distinct purposes:

1. **Structured build status** for client applications that need to display current build state without parsing 
human-readable output.
2. **Human-readable diagnostic logging** for users investigating build behavior, failures, unexpected rebuilds, 
or unexpected skips.

These are separate outputs with separate contracts.

The structured status stream answers:

> What is the build doing right now?

The text log answers:

> What happened, and why?

Neither output should be derived by parsing the other.

---

## 2. Output Channels

### Structured status

LiteBuild emits structured status messages using the shared `status_message.py` protocol.

For command-line execution:

```text
stderr -> NDJSON structured status
```

Each message is one complete JSON object terminated by LF.

The structured channel must contain only protocol messages when structured status output is enabled.

### Text log

Human-readable logging remains independent:

```text
stdout / configured BuildLogger -> diagnostic text
```

Child-tool stdout and stderr are captured by LiteBuild and routed into this text stream.

The text log may also be written to a persistent logfile.

---

## 3. Structured Status Model

The structured status stream is authoritative for live UI state.

The core event vocabulary is:

```text
BuildStart
GroupStart
ProfileStart
StepsSkipped
StepStart
StepProgress
StepFinish
ProfileFinish
GroupFinish
BuildFinish
```

### Build hierarchy

LiteBuild execution follows:

```text
Build
  Group (optional)
    Profile
      Steps
```

Profiles execute sequentially.

Steps within a profile may execute concurrently when permitted by the DAG.

A build without a profile group must not fabricate a synthetic group.

---

## 4. Concurrency Rules

Structured status must support asynchronous step execution.

Sibling step messages may be interleaved and may complete in any order:

```text
StepStart A
StepStart B
StepStart C
StepFinish B
StepProgress A
StepFinish C
StepFinish A
```

Consumers must treat each step message as an independent update identified by step name within the current profile.

No consumer may infer that an earlier-started step has finished merely because a later step finishes.

For any individual step, its own lifecycle must remain ordered:

```text
start -> zero or more progress -> finish
```

Profiles themselves are sequential, so only one profile is active at a time.

---

## 5. Build and Group Status

`BuildStart` identifies the requested final target.

A group build emits `GroupStart`, including the complete ordered profile list. This allows a consuming UI to create its 
entire group-status display before profile execution begins.

A group finish includes:

* success
* elapsed time
* human-readable `status_text`

The same general finish contract applies to build completion.

---

## 6. Profile Status

`ProfileStart` identifies:

* profile name
* profile position within the group/build
* total profile count
* total steps in the plan
* number of steps requiring execution

The distinction between total steps and steps requiring execution is important.

For example:

```text
43 steps in plan
24 up to date
19 to run
```

Progress such as `10/19` means:

> 10 of the 19 steps requiring execution have completed.

Skipped/up-to-date steps are not counted as completed execution work.

---

## 7. Up-to-Date / Skipped Steps

Up-to-date steps are first-class status information.

LiteBuild should emit an aggregate `StepsSkipped` message containing the ordered names of steps that do not require execution.

For LiteBuild, skipped means:

> The step is part of the selected DAG and LiteBuild considers its output current.

LiteBuild should not invent generalized states such as disabled or not applicable if those concepts do not exist in the build model.

This information is particularly important during development because LiteBuild tracks configured commands, parameters, dependencies, files, and timestamps, but may not know that the implementation of an external tool has changed.

A developer must therefore be able to quickly notice cases such as:

```text
BroadHillshade — up to date
```

when they expected that step to rerun.

---

## 8. Step Start Status

`StepStart` should contain:

* step name
* progress index
* total steps requiring execution
* `status_text`

`status_text` explains why LiteBuild is executing the step.

Examples:

```text
Creating output
Parameters changed
Command changed
Input file list changed
Input 'Sedona_DEM.tif' is newer
Forced
Stale target
```

The reason should originate from LiteBuild's existing planning/freshness logic. Client applications should not 
reconstruct this interpretation.

The planner owns the reason; the executor owns the actual lifecycle transition.

---

## 9. Step Progress

`StepProgress` is optional.

LiteBuild must not manufacture percentage progress when a tool cannot provide meaningful progress.

A long-running tool may provide progress when LiteBuild can obtain it reliably.

Possible sources include:

* progress generated directly by LiteBuild
* a generic progress pattern declared for a tool/step
* stable progress output from a child process

The initial logging/status implementation does not require every tool to support progress.

For tools without progress information, elapsed runtime plus running state is sufficient.

---

## 10. Step Finish Status

`StepFinish` contains:

* step name
* success
* elapsed time
* `status_text`

The structured status should be emitted when the individual step actually finishes, not after every step in its parallel 
DAG generation has completed.

Parallel execution semantics do not change; only observation should become completion-driven.

Examples of finish status text:

```text
Done
Command failed with exit code 1
Output file was not created
```

The boolean `success` remains authoritative for machine state. Consumers must not parse `status_text` to determine 
success or failure.

---

## 11. Human-Readable Text Log

The text log is a forensic transcript rather than the primary build-status UI.

It should therefore favor:

* readability when reviewing long logs
* strong visual landmarks
* consistent terminology
* clear hierarchy
* identifiable tool output
* enough detail to understand unexpected rebuild or skip behavior

It does not need to be visually minimal.

A 200-line log should be easy to scan after a failure.

---

## 12. Standard Text Terminology

Use a small consistent vocabulary:

```text
BUILD
GROUP
PROFILE
PLAN
STEP
TOOL
SUMMARY
WARNING
ERROR
```

Avoid multiple phrases for the same concept.

For example, prefer consistent `STEP` lifecycle terminology instead of alternating among:

```text
Running step
Executing rule
Finished step
Build failed for Step
```

Likewise, use `Group` consistently rather than switching between `Group` and `Profile Group`.

---

## 13. Visual Landmarks

Emoji are appropriate in the diagnostic log because the log is normally hidden and primarily inspected when a user 
needs to locate significant events quickly.

Recommended semantics:

```text
🔵  build / major summary boundary
ℹ️   profile or informational context
⏭️  up-to-date work
▶️   step start
✅  successful completion
⚠️   warning
❌  step/tool failure
🔴  failed profile/group/build
```

Emoji meanings should remain stable.

Blank lines should be used deliberately around semantic boundaries:

* build/group/profile starts
* step starts
* failures
* timing summaries
* final completion

Do not add blank lines between ordinary child-tool output lines.

---

## 14. Indentation and Hierarchy

Indentation should reflect build structure:

```text
BUILD

  GROUP

    PROFILE

      PLAN

      STEP
        Reason:
        Command:

        [Tool output]

      STEP completion
```

Raw child-tool output should not become deeply indented because parallel output already consumes significant 
horizontal space.

A stable step prefix should identify interleaved tool output:

```text
    [BroadHillshade] Broad hillshade: 12/48 [...]
    [AlignLith]      Raster aligned successfully.
    [SlopeMask]      0...10...20...
```

The prefix is especially important when multiple steps execute concurrently.

---

## 15. Suggested INFO-Level Shape

A normal diagnostic log might resemble:

```text
🔵 BUILD
   Target: CreatePMTiles

ℹ️  PROFILE Jemez [4/13]

⏭️  UP TO DATE — 24 steps
    SetupDirs, WarpLith, WarpForestMask, ...

▶️  STEP BroadHillshade [7/19]
    Reason: Input 'Jemez_DEM.tif' is newer
    Command: georaster broad_hillshade ...

    [BroadHillshade] Broad hillshade: 2258x2711, windows=48
    [BroadHillshade] Broad hillshade: 18/48 [01:30<02:28]

✅ STEP BroadHillshade — 8.28s

▶️  STEP AlignLith [8/19]
    Reason: Stale target
    ...

❌ STEP CreateMBTiles
    Command failed with exit code 1

🔴 PROFILE Jemez FAILED
    Failed during CreateMBTiles
    Elapsed: 18:42
```

The exact punctuation is less important than consistent structure.

---

## 16. Planner Debug Logging

The planner's detailed freshness analysis belongs primarily at DEBUG level.

Its output should be structured consistently rather than using ad hoc `RESULT:` messages.

Conceptually:

```text
PLAN BroadHillshade
    output exists:       yes
    tracked:             yes
    command hash:        match
    input-list hash:     match
    parameter hash:      changed
    decision:            rebuild
    reason:              parameters changed
```

INFO should report the resulting decision, not every comparison that led to it.

This keeps normal logs readable while retaining detailed diagnostics when needed.

---

## 17. Child Tool Output and CR/LF Handling

Child-tool output must not be treated as ordinary newline-delimited text without considering terminal control behavior.

Tools such as `tqdm`, GDAL utilities, and other progress reporters may use:

* LF (`\n`) to append a new line
* CR (`\r`) to rewrite the current terminal line
* combinations such as `\r\n`

LiteBuild must preserve enough distinction between CR and LF to handle these correctly.

In particular:

* do not use generic `.strip()` on forwarded output
* do not destroy leading indentation
* do not convert every CR refresh into an unintended permanent log line
* do not lose the final state of a progress line

The live console may interpret CR as replacement of the current line.

The persistent text logfile may normalize transient terminal updates into a useful diagnostic representation rather 
than preserving every screen redraw.

CR/LF handling should be implemented as a deliberate output-processing concern, not incidentally through line trimming.

---

## 18. Logging Ownership

Different components naturally own different information.

### BuildPlanner

Owns:

* up-to-date determination
* rebuild reason
* freshness diagnostics
* plan contents

### BuildExecutor

Owns:

* step submission/start
* child command execution
* child tool output
* actual step completion
* elapsed step time
* step failure

### Build/group orchestration

Owns:

* build lifecycle
* group lifecycle
* profile lifecycle
* elapsed profile/group/build time
* overall success/failure

These components should emit status at the point where they have authoritative knowledge rather than recreating 
information elsewhere.

---

## 19. Client Application Log Usage

Client applications should be intentionally dumb about LiteBuild semantics.

Client apps should:

* deserialize shared status dataclasses
* maintain current state
* display message fields
* use structured `success` values for green/red state
* display `status_text`
* tolerate asynchronous sibling-step messages
* keep the text console collapsed by default
* expose the console for detailed diagnosis

Client apps  should not:

* parse text logs for state
* determine why a step reran
* determine why a step was skipped
* interpret child-tool output
* infer failure from strings
* reconstruct LiteBuild's DAG semantics

---

## 20. Design Principle

The two logging systems should deliberately optimize for different questions:

```text
Structured status:
    What is happening?

Text diagnostic log:
    What happened and why?
```

Keeping those responsibilities separate allows LiteBuild to provide a clean, reliable status interface while retaining 
a rich and highly searchable diagnostic transcript when something goes wrong.
