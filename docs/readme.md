
# LiteBuild

> **THIS PROJECT IS EXPERIMENTAL.**
>
> **DO NOT USE IN PRODUCTION.**
>
> Features and interfaces are evolving.
>
> **Updates will NOT be backward compatible.**
>
> **Back up your  files before use; bugs may corrupt project files.**

`LiteBuild` is a lightweight, configuration-driven build system designed specifically for
**data-processing pipelines and shell workflows**.  LiteBuild is a good fit for complex data pipelines that 
need to remain explicit, reproducible, and understandable as they grow. It keeps workflow structure, dependencies, 
command templates, and configuration in a declarative build definition rather than distributing that logic 
across custom scripts.

LiteBuild is optimized for workflows where the
primary actions are **running templated shell commands** to transform data files, manipulate images, or
execute scientific-computing tasks.

The complete workflow remains declarative rather than becoming a programming language. Project-specific
build logic is kept in explicit, file-based configuration, while LiteBuild provides command templating,
dependency tracking, incremental execution, parallel scheduling, profiles, provenance tracking, and build-state management.

LiteBuild deliberately constrains artifact flow so that each step has one principal output, which is propagated 
automatically to downstream steps. This keeps DAGs simple, reduces file-wiring boilerplate, and encourages small, 
composable tools rather than multi-purpose build stages.
---

## LiteBuild Key Features & Benefits

### 1. Declarative Workflow in a Single File

The complete project-specific build workflow is defined in one structured configuration file, including
steps, dependencies, commands, parameters, profiles, profile groups, and outputs.

* **The Benefit:** Workflow logic is centralized rather than scattered across shell scripts and application
  code. The build definition is version-controllable, reproducible, and easier to inspect, modify, and
  understand.

### 2. Powerful Parameter Management

LiteBuild treats command parameters as part of the build definition.

* **Templated Commands:** Complex external commands can be constructed dynamically from reusable
  configuration.
* **Hierarchical Configuration:** A three-tier system—**Step overrides Profile, which overrides General**—
  allows common defaults to be defined once and changed only where necessary.
* **Flexible Parameter Styles:** Positional arguments, single-dash options, double-dash options, unquoted
  parameters, and other command conventions can be represented directly.
* **The Benefit:** A tool's invocation logic can be defined once and reused across profiles and workflow
  steps without duplicating command definitions.

### 3.  Dependency-Based Build Engine

Like most modern build systems, LiteBuild determines what actually needs to run rather than executing every configured step. 
Independent steps run in parallel when possible.

* **Parameter-Aware Incremental Builds:** LiteBuild tracks resolved commands, inputs, parameters, outputs,
  and file modification times. Changing an input, command, or processing parameter invalidates the affected
  output and its downstream dependents, while unrelated branches remain untouched.

* **Dependency Checking:** Each step is evaluated against its dependencies and current state.
  Up-to-date branches are skipped automatically.

* **Automatic Parallel Execution:** Independent branches of the dependency graph  run concurrently.

* **Single Primary Output per Step:** Each step identifies _one primary output_ used for dependency tracking
  and incremental build state. This gives every step a clear result and makes the workflow easier to
  understand and troubleshoot.

* **Step Name Chaining:** Steps depend on other **step names**, rather than 
  upstream output filenames. Generic references such as `{REQUIRES[0]}` and `{INPUTS[0]}` resolve upstream
  filenames automatically.

  For example:

  ```yaml
  WarpDEM:
    REQUIRES:
      - DEMVRT
    OUTPUT: "{DEM_SOURCE}"
    RULE:
      NAME: create_dem
      COMMAND: gdalwarp {INPUTS[0]} {OUTPUT} {PARAMETERS}
    INPUTS:
      - "{REQUIRES[0]}"
  ```

`WarpDEM` does not need to know the  filename produced by `DEMVRT`. Removing, replacing, or
inserting an intermediate step often only requires changing  the `REQUIRES` relationship; downstream
commands, file paths, and parameter definitions  remain unchanged.

### 4. Profiles and Profile Groups

LiteBuild can run the same workflow against multiple named parameter sets.

* **Profiles:** Define build-specific inputs and parameter overrides while sharing the same workflow
  definition.
* **Profile Groups:** Collections of **Profiles** that can be executed sequentially as a larger
  build.
* **The Benefit:** A single workflow can support one-off builds, multiple variants, regional datasets, or
  complete production build sets without duplicating the dependency graph or command definitions.

> NOTE: LiteBuild runs profiles serially to prevent any overwrites since the workflow is repeated.
> The individual steps in a workflow can run concurrently and the user must ensure they are safe.

### 5. Automatic Workflow Documentation

LiteBuild can generate a description of the configured workflow directly from the build definition.

* It produces Markdown containing a Mermaid dependency diagram and the ordered commands represented by the
  configuration.
* The report identifies source files that enter the workflow from outside the current dependency graph.
* When provenance declarations are present, source files are grouped into documented data and configuration
  sources. Undocumented source files are listed separately.
* **The Benefit:** Documentation is generated from the same configuration that drives execution, reducing
  the chance that workflow documentation drifts away from the actual build.

### 6. Source Provenance

LiteBuild can document and validate the external files that enter a workflow.

A **source file** is an input used by the resolved workflow that is not produced by another step in that same
workflow. Source files can be data inputs or configuration files.

The optional top-level `PROVENANCE` section associates source files or wildcard groups with human-readable
descriptions:

```yaml
PROJECT:
  PROVENANCE_CHECK: warn

PROVENANCE:
  usgs_dem:
    INPUT: "{INPUT_DIRECTORY}/USGS_13*.tif"
    DESCRIPTION: >
      USGS 3DEP 1/3 arc-second elevation data.

  color_ramps:
    CONFIG: "{CONFIG_DIR}/*_color_ramp.txt"
    DESCRIPTION: >
      Color-ramp definition files used by the rendering pipeline.
```

`PROJECT/PROVENANCE_CHECK` controls provenance coverage validation:

* `off` — provenance coverage is not checked. This is the default.
* `warn` — missing provenance is reported as a warning.
* `fail` — missing provenance causes validation to fail.

Exact provenance paths take precedence over wildcard declarations. Multiple wildcard declarations matching the
same source are treated as ambiguous.

* **The Benefit:** LiteBuild records the external roots of the workflow while the dependency graph already
  provides the transformation lineage for generated files. This avoids maintaining a separate provenance
  database for intermediate artifacts.

### 7. Flexible Invocation

The same LiteBuild workflow can be used in several environments:

* **GUI** — for interactive use.
* **Command Line** — for scripting, automation, and servers.
* **Python API** — the build engine can be invoked directly by another Python application.
* **Subprocess Integration** — applications can invoke the LiteBuild CLI as an isolated external build
  process.

**The Benefit:** The workflow definition remains the same regardless of how the build is initiated.

---

## Configuration Overview

These are the key sections in the configuration. `configuration.md` provides a detailed description of
each.

### 1. PROJECT

`PROJECT` defines project-level LiteBuild behavior.

It contains settings that describe or control the build as a whole, including:

* **OVERVIEW:** Human-readable description used by generated workflow documentation.
* **DEFAULT_WORKFLOW_STEP:** Default target when no workflow step is specified.
* **PROVENANCE_CHECK:** Controls source-provenance coverage checking with `off`, `warn`, or `fail`.

### 2. WORKFLOW

`WORKFLOW` defines the steps in the build and their dependency relationships.

Each step can define:

* **REQUIRES:** Names the upstream steps that must complete before this step can run.
* **OUTPUT:** Identifies the primary file created by the step.
* **INPUTS:** Identifies the files consumed by the step. Inputs may reference the outputs of required steps
  without hard-coding their filenames.
* **RULE:** Defines the command template used to execute the step.

For example:

```text
gdalwarp {INPUTS[0]} {OUTPUT} {PARAMETERS}
```

LiteBuild resolves the input, output, and parameters when the step runs.

This allows workflow steps to be chained by logical dependency rather than by embedding physical filenames
throughout the configuration.

### 3. GENERAL

`GENERAL` defines the shared environment for the build.

It can:

* define global values such as project paths and common settings;
* provide parameters available to all steps; and
* define defaults that apply unless overridden by a Profile or individual Step.

### 4. PROFILES

A Profile represents a particular build scenario or dataset.

For example, a workflow might define profiles such as:

```text
Germany
France
Test_Run
```

Each Profile supplies the variable inputs and parameter overrides needed to run the shared workflow for that
case.

The workflow itself remains unchanged.

I’d make the division explicit right at the start:

### 5. PROVENANCE

LiteBuild automatically determines which files are **source files**: files consumed by the resolved workflow that are not produced by another step in that workflow.

The user provides the provenance description for those source files in the optional `PROVENANCE` section.

Each provenance entry contains:

* exactly one of **INPUT** or **CONFIG**, identifying the source file or files;
* a **DESCRIPTION** supplied by the user explaining the source.

Entries may identify an exact path or a standard Unix-style wildcard pattern. A single declaration can therefore document a logical dataset made up of many physical files, such as a directory of DEM tiles.

LiteBuild then:

* matches the detected source files against the user-provided provenance declarations;
* includes the source files and their descriptions in the generated workflow report;
* identifies source files with no matching provenance declaration; and
* when enabled by `PROJECT/PROVENANCE_CHECK`, warns or fails when provenance coverage is incomplete.

The user does **not** document generated intermediate files. LiteBuild already knows their lineage from the workflow 
, so explicit provenance is only needed for files entering the workflow from outside the workflow.

---

## LiteBuild and Other Build Systems

### LiteBuild

LiteBuild is designed for building explicit, reproducible file-processing pipelines around command-line tools.
It intentionally focuses on a narrower problem than many general-purpose build and workflow systems. Rather 
than supporting every possible form of dynamic workflow construction, LiteBuild emphasizes explicit named steps, 
templated commands, file dependencies, profiles, and predictable incremental builds.

Its strengths include:

* declarative workflow configuration;
* command and parameter templating;
* parameter-aware incremental rebuilds;
* explicit dependency relationships;
* logical step chaining;
* profiles and profile groups;
* source-file provenance reporting and validation;
* automatic dependency-based parallel execution;
* one primary output per step; and
* easy-to-inspect workflow structure.

LiteBuild is a good fit for complex data pipelines that need to remain explicit, reproducible, and understandable 
as they grow. It keeps workflow structure, dependencies, command templates, and configuration in a declarative 
build definition rather than distributing that logic across custom scripts.

### CMake

CMake is primarily designed to configure and generate portable builds for compiled software, especially
C and C++ projects.

It provides extensive compiler, toolchain, library, platform, and build-generator support. LiteBuild has a
different focus: explicit command-driven data-processing pipelines rather than cross-platform software
compilation.

### Snakemake

Snakemake is designed for **large scientific and computational workflows**.

Its strengths include:

* flexible rules and wildcard expansion;
* dynamically generated jobs;
* environment and container management;
* CPU, memory, and resource scheduling;
* cluster, HPC, and cloud execution; and
* management of large numbers of datasets and jobs.

Snakemake is a good fit when the goal is to manage **large, variable, or dynamically expanded scientific
workloads**.

LiteBuild deliberately does not provide wildcard-driven workflow expansion. Its focus is on keeping the
configured dependency graph explicit and easy to inspect.

### LiteBuild vs. Snakemake

|                               | LiteBuild                                                    | Snakemake                                                          |
|-------------------------------|--------------------------------------------------------------|--------------------------------------------------------------------|
| Primary focus                 | **Explicit command-driven pipelines**                        | **Complex scientific workflows**                                   |
| Workflow style                | Explicit named steps and dependencies                        | Rules that can generate many jobs                                  |
| Command model                 | Templated CLI commands                                       | Shell commands within a rich rule system                           |
| Dynamic expansion / wildcards | **Deliberately not supported**                               | **Core capability**                                                |
| Profiles / ordered groups     | **First-class concepts**                                     | Can be modeled through workflow/config mechanisms                  |
| Provenance                    | **Built-in source-file provenance reporting and validation** | Can be modeled through workflow metadata, reports, or custom rules |
| Outputs                       | _Mandatory single primary output per step_                   | Multiple outputs supported                                         |
| Parameters                    | **Hierarchical command configuration**                       | Rich rule/config/wildcard system                                   |
| Execution model               | Dependency DAG + local parallelism                           | Dependency DAG + local/HPC/cloud execution                         |
| Design goal                   | **Keep the workflow explicit and easy to inspect**           | **Express large and variable scientific workloads**                |