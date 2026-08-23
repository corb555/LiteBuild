
# LiteBuild


> THIS PROJECT IS
**EXPERIMENTAL**. Features and Interfaces are still evolving.

`LiteBuild` is a lightweight, configuration-driven build system designed specifically for
**data-processing pipelines and shell workflows**.  The goal is to make complex data pipelines **explicit, reproducible, 
easy to inspect, and easy to modify**
without requiring custom orchestration code.

LiteBuild is optimized for workflows where the
primary actions are **running templated shell commands** to transform data files, manipulate images, or
execute scientific-computing tasks.

The complete workflow remains declarative rather than becoming a programming language. Project-specific
build logic is kept in explicit, file-based configuration, while LiteBuild provides command templating,
dependency tracking, incremental execution, parallel scheduling, profiles, and build-state management.

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

Like most modern build systems, LiteBuild determines what actually needs to run rather than simply executing every 
configured step and executes steps in parallel when possible.

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

### 5. Automatic Workflow Documentation

LiteBuild can generate a description of the configured workflow directly from the build definition.

* It produces Markdown containing a Mermaid dependency diagram and the ordered commands represented by the
  configuration.
* **The Benefit:** Documentation is generated from the same configuration that drives execution, reducing
  the chance that workflow documentation drifts away from the actual build.

### 6. Flexible Invocation

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

### 1. WORKFLOW

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

### 2. GENERAL

`GENERAL` defines the shared environment for the build.

It can:

* define global values such as project paths and common settings;
* provide parameters available to all steps; and
* define defaults that apply unless overridden by a Profile or individual Step.

### 3. PROFILES

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

---

## LiteBuild and Other Build Systems

LiteBuild intentionally focuses on a narrower problem than many general-purpose build and workflow systems.

Its goal is not to provide every possible form of dynamic workflow construction. Instead, it emphasizes
**explicit named steps, templated external commands, file dependencies, profiles, and predictable
incremental builds**.

### LiteBuild

LiteBuild is designed for **readable, file-oriented data-processing pipelines** built around external
commands.

Its strengths include:

* declarative workflow configuration;
* command and parameter templating;
* parameter-aware incremental rebuilds;
* explicit dependency relationships;
* logical step chaining;
* profiles and profile groups;
* automatic dependency-based parallel execution;
* one primary output per step; and
* easy-to-inspect workflow structure.

LiteBuild is a good fit when the goal is to keep a complex data pipeline **explicit, reproducible, and easy
to modify without turning the workflow definition into a programming language**.

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

|                               | LiteBuild                                          | Snakemake                                           |
|-------------------------------|----------------------------------------------------|-----------------------------------------------------|
| Primary focus                 | **Explicit command-driven pipelines**              | **Complex scientific workflows**                    |
| Workflow style                | Explicit named steps and dependencies              | Rules that can generate many jobs                   |
| Command model                 | Templated CLI commands                             | Shell commands within a rich rule system            |
| Dynamic expansion / wildcards | **Deliberately not supported**                     | **Core capability**                                 |
| Profiles / ordered groups     | **First-class concepts**                           | Can be modeled through workflow/config mechanisms   |
| Outputs                       | Mandatory single primary output per step           | Multiple outputs supported                          |
| Parameters                    | **Hierarchical command configuration**             | Rich rule/config/wildcard system                    |
| Execution model               | Dependency DAG + local parallelism                 | Dependency DAG + local/HPC/cloud execution          |
| Design goal                   | **Keep the workflow explicit and easy to inspect** | **Express large and variable scientific workloads** |
