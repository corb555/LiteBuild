# LiteBuild YAML Load and External Section Concatenation — High-Level Specification

## Overview

LiteBuild YAML loading uses `ruamel.yaml` so configuration values retain their original **file, line, and 
column location**. When an error occurs during config file validation or during execution due to configuration, it is critical
to be able to get back to the exact file and associated with the error.

LiteBuild supports assembling selected top-level configuration sections from multiple physical YAML files.
`WORKFLOW` is the first section that can use this mechanism, but the loader is section-generic so other sections
such as `PROFILES` can be enabled later without changing the loading architecture.

The loader always produces one logical LiteBuild configuration regardless of how its supported sections are
physically stored.

The main goals are:

- preserve precise source locations for validation errors;
- separate physical YAML layout from LiteBuild's logical configuration model;
- support strict, deterministic concatenation of selected top-level sections;
- make the loader generic enough to support additional externalizable sections later;
- let Cerberus and semantic validators operate on one assembled configuration.

## 1. Ruamel-Based Loading

All LiteBuild YAML files are loaded with `ruamel.yaml`.

The loader preserves:

- YAML structure;
- mapping order;
- source line numbers;
- source column numbers.

LiteBuild additionally records the physical filename associated with loaded nodes.

Conceptually:

```text
physical YAML file
        ↓
ruamel node
        +
filename
        ↓
source-aware configuration
```

A source location contains at least:

```text
file
line
column
```

User-facing line and column numbers are reported using normal 1-based numbering.

## 2. Logical Configuration

Downstream LiteBuild components operate on a single logical configuration.

For example:

```text
PROJECT
GENERAL
PARAMETERS
PROVENANCE
PROFILE_GROUPS
PROFILES
WORKFLOW
```

The planner, validator, reporter, and command generator operate on the assembled logical configuration and do
not need to know whether a supported section originated from one file or several.

Physical file layout is a loading concern, not a section semantic.

## 3. External Section Model

The loader implements external-file support as a generic operation on selected top-level LiteBuild sections.
External sections serve two main purposes:
1. Manage large configuration sections. A WORKFLOW can become long and difficult to navigate. It can be split into separate files, each containing a coherent part of the pipeline.
2. Separate independently changing configuration. Different parts of a LiteBuild project can have separate physical files and version histories while still being assembled into one validated logical configuration. For example, a stable workflow can remain unchanged while new profiles or regions are added over time.
Conceptually, each supported externalizable section has a definition equivalent to:

```text
main selector key
external config_type
logical section name
section schema
duplicate-key policy
```

For the initial implementation:

```text
main selector key:    WORKFLOW_FILES
external config_type: LiteBuildWorkflow
logical section:      WORKFLOW
section schema:       standard WORKFLOW schema
duplicate-key policy: error
```

The public YAML remains section-specific. LiteBuild does not expose a generic `EXTERNAL_SECTIONS` mechanism.

This allows future support for another section, for example `PROFILES`, by registering another section definition
rather than creating a second loading architecture.

### WORKFLOW Configuration Forms

A LiteBuild project has one logical `WORKFLOW`, but that workflow can be stored in the main file or split across
multiple workflow files.

The main configuration uses:

```yaml
config_type: LiteBuild
```

and contains exactly one of:

```yaml
WORKFLOW:
  ...
```

or:

```yaml
WORKFLOW_FILES:
  - workflow/terrain.yml
  - workflow/evt.yml
  - workflow/render.yml
```

`WORKFLOW` and `WORKFLOW_FILES` are mutually exclusive.

When `WORKFLOW` is present, the normal LiteBuild workflow schema applies directly.

When `WORKFLOW_FILES` is present, each referenced file is loaded as an external workflow file and the contained
workflow mappings are appended together to create the project's logical `WORKFLOW`.

External workflow files use:

```yaml
config_type: LiteBuildWorkflow

WORKFLOW:
  CreateDEM:
    ...
  CleanDEM:
    ...
```

A `LiteBuildWorkflow` file contains only:

```text
config_type
WORKFLOW
```

No other top-level LiteBuild sections are permitted in an external workflow file.

This keeps workflow splitting strictly an organizational feature. External workflow files cannot define
`PROJECT`, `GENERAL`, `PARAMETERS`, `PROFILES`, `PROFILE_GROUPS`, `PROVENANCE`, or any other project-level
configuration.

## 4. Section Concatenation

External section concatenation is a **strict append of mappings** for the selected logical section.

`WORKFLOW` is the first section using this mechanism. For `WORKFLOW`, concatenation is a strict append of workflow
step mappings.

Conceptually:

```text
main.yml
    WORKFLOW_FILES:
        workflow/terrain.yml
        workflow/evt.yml
        workflow/render.yml

workflow/terrain.yml
    WORKFLOW:
        CreateDEM
        CleanDEM

workflow/evt.yml
    WORKFLOW:
        WarpEVT
        ReclassifyEVT

workflow/render.yml
    WORKFLOW:
        LandWeaver
        CreateMBTiles
              ↓
              ↓ append
              ↓
logical WORKFLOW:
    CreateDEM
    CleanDEM
    WarpEVT
    ReclassifyEVT
    LandWeaver
    CreateMBTiles
```

There are no override, inheritance, or precedence semantics.

Rules:

- the main `LiteBuild` config contains exactly one of `WORKFLOW` or `WORKFLOW_FILES`;
- each file listed in `WORKFLOW_FILES` must have `config_type: LiteBuildWorkflow`;
- each external workflow file contains only `config_type` and `WORKFLOW`;
- each external `WORKFLOW` uses the standard LiteBuild workflow-step schema;
- step names are globally unique across all workflow files;
- duplicate step names are an error;
- file order does not provide override precedence;
- dependencies may reference steps defined in another workflow file;
- all dependency validation occurs against the fully assembled workflow;
- all variables and parameters continue to come from the main LiteBuild configuration;
- workflow files do not introduce local variable scopes;
- external workflow files cannot include or reference additional workflow files.

The resulting logical workflow behaves exactly like a workflow written in one file.

### Generic Concatenation Rules

The same internal rules apply to every externalizable section:

- the section is either defined inline or supplied through its section-specific file list, never both;
- each external file has a section-specific `config_type`;
- each external file contains only `config_type` and the section it contributes;
- the contributed section uses the same schema as the corresponding section in the main LiteBuild config;
- entries are appended without override, inheritance, or precedence semantics;
- duplicate logical keys are errors unless a future section explicitly defines a different policy;
- external section files cannot recursively include more external section files;
- all source file, line, and column metadata is retained during assembly;
- the assembled section is validated again as part of the complete logical configuration.

Adding another externalizable section therefore requires a new section definition and schema support, not a new
loader implementation.

## 5. Source Location Preservation

Concatenation preserves the original source location of each appended section item and nested field.

For example:

```text
WORKFLOW.CreateDEM
    file: workflow/terrain.yml
    line: 12
    column: 1

WORKFLOW.CreateDEM.INPUTS
    file: workflow/terrain.yml
    line: 27
    column: 3
```

LiteBuild owns the filename association.

`ruamel.yaml` provides line and column information within each loaded document.

The assembled configuration retains enough metadata to map validation paths back to the original physical source.

Source-location tracking is section-generic. The same mechanism used for `WORKFLOW.CreateDEM.INPUTS` can later
track paths such as `PROFILES.Jemez.PARAMETERS` without changing the source-location model.

## 6. Validation

Validation occurs in two stages: physical-file validation followed by assembled-configuration validation.

Processing order:

```text
load main YAML with ruamel
        ↓
record source locations
        ↓
validate main-file structure
        ↓
for each enabled external section:
    load each section file with ruamel
        ↓
    validate each external-file structure
        ↓
    append section mappings
        ↓
    reject duplicate logical keys
        ↓
build one logical LiteBuild configuration
        ↓
validate assembled section structures
        ↓
LiteBuild semantic validation
```

The main-file schema enforces the mutually exclusive inline-vs-files rule for each supported externalizable
section. Initially this means `WORKFLOW` versus `WORKFLOW_FILES`.

Each external-file schema enforces its section-specific `config_type`, permits only `config_type` plus the
contributed section, and applies the normal schema for that section.

Validation of an assembled section detects errors that cannot be determined from an individual file. For
`WORKFLOW`, this includes cross-file dependency errors and dependency cycles.

Validation errors use the logical configuration path to locate the corresponding physical source.

Example:

```text
workflow/evt.yml:84:5
WORKFLOW.PatchEVTClasses.INPUTS

Unknown workflow step: RasterizeEVT
```

The same error format applies whether the configuration originated from one file or several.

## 7. YAML Editor Integration

The YAML editor operates on the logical LiteBuild configuration while retaining the physical origin of each editable section.

When a user selects a configuration item, the editor can determine:

- which file contains the item;
- its source line;
- its source section.

For example, a workflow step may live in `workflow/terrain.yml`, while a future external profile may live in
`profiles/detail.yml`.

Editing remains constrained to the physical file that owns that configuration item.

This allows the editor and validator to share the same source-location model.

## 8. Initial Implementation

The initial ruamel loader and schema are designed around generic external-section concatenation from the
beginning, even if `WORKFLOW` is initially the only section permitted to use it and `WORKFLOW_FILES` is not
immediately exposed as a user-facing feature.

This establishes the internal contract:

> LiteBuild configuration has one logical structure and retained physical source provenance.

The loader can therefore preserve file, line, and column ownership consistently for single-file and multi-file
configuration without changing validation, planning, reporting, or editor architecture when another section is
made externalizable.

## Design Principle

> **Multiple YAML files are an organizational feature, not a new workflow model.**

LiteBuild always validates and executes one assembled `WORKFLOW`; ruamel source metadata keeps every logical item traceable to the physical file and line where it was defined.