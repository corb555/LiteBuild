## Overview

LiteBuild reads a configuration file to determine what commands are necessary to build an item and then executes
the commands.
The `LB_config.yml` file is a structured blueprint that LiteBuild uses to:

* Understand all the components of your project (the "workflow").
* Build a dependency graph to determine the correct order of operations.
* Create the command for each step by substituting parameters and file names into the templates you provide.

## Core Concepts

* **Target**: (OPTIONAL) A `Target` is a specific *version* of the final product you want to build. For example, you
  might
  have a `USWest` target that uses one set of input files and a `WA` target that uses another. This allows you to
  manage multiple build variations from a single configuration. Targets are optional for cases where you want to specify
  specific parameters for a type of build.
* **Workflow Step**: A `Workflow Step` is the main unit of work. It is a single, atomic *action* in your build
  process. Each step produces one output file and can depend on other steps. Examples include "CreateDEM,"
  "GenerateHillshade," or "CombineLayers."  Input files from other steps can be specified generically without needing
  to know the specific filename a step produces.
* **Rule**: A `Rule` is the part of a `Workflow Step` that defines the actual command to be run. It contains
  the `COMMAND` template and a `NAME` that is used for merging parameters.

## Config File

Create a YAML configuration file that defines your build workflow.

> ⚠️**Config file names** must begin with the
> prefix `LB_` (e.g., `LB_classify.yml`).  
> This convention makes LiteBuild files easy to identify and
> helps prevent accidentally using the wrong config.

## Configuration Sections

The config file is organized into three top-level sections: `GENERAL`, `TARGETS`, and `WORKFLOW`.

### 1. The `GENERAL` Section

This section is for defining global template parameters that apply to your entire project. It helps
you avoid repeating the same values in multiple places.

#### Configure `GENERAL`:

You can define any key-value pairs you want, which can then be used as template parameters (e.g., `{DATA_FOLDER}`)
throughout the rest of the file.

The `PARAMETERS` sub-section is used to set the *lowest-precedence* template parameters for
a specific rule.

LiteBuild can be started from the command line with switches to define
GENERAL parameters. These override the config file if the parameter is already defined.

**Example:**

```yaml
# ===  GENERAL PARAMETERS  ===
GENERAL:
  PROJECT_NAME: "US"
  PREVIEW: ""
  DATA_FOLDER: "elevation/"
  BUILD_DIR: build/{target_name}
  QUIET: -q
  PUBLISH_DIRECTORY: /Volumes/Mike EXT/Tile_Data/Mapnik/img

  # Default parameters for rules
  PARAMETERS:
    align_raster:
      co:
        - "COMPRESS=ZSTD"
    scale_precip_mask:
      scale: 200 600
```

---

### 2. The `TARGETS` Section

This optional section allows you to define different build variations. Each target can specify its own unique set of
input
files and override default parameters.

#### Configure `TARGETS`:

Each key under `TARGETS` is the unique name of a build target. This is the name you will provide to LiteBuild to
run a specific build.

* `TARGET_FILES`: **(Special Key)** A list of source filenames specific to this target. This list is used by any
  `Workflow Step` that contains the special `{TARGET_FILES}` token.
* `PARAMETERS`: Overrides default parameters for specific rules, but only when this target is being built.
  **Target parameters have higher precedence than `GENERAL` parameters.**

**Example:**

```yaml
# ===   TARGETS ===
USWest:
  TARGET_FILES:
    - 10n090w_20101117_gmted_mea075.tif
    - 10n120w_20101117_gmted_mea075.tif
  PARAMETERS:
    create_dem:
      te: -13917257.3 2835222.9 -9710737.7 6435460.8
    create_hillshade:
      "z": 3
      "igor": true

  Muted:
    TARGET_FILES:
      - 10n090w_20101117_gmted_mea075.tif
      - 10n120w_20101117_gmted_mea075.tif
    PARAMETERS:
      create_dem:
        te: -13917257.3 2835222.9 -9710737.7 6435460.8
      create_hillshade:
        "z": 1
        "igor": true
```

---

### 3. The `WORKFLOW` Section

This section defines the sequence of all possible `Workflow Steps` and how they depend on each other. LiteBuild
uses this section to construct the dependency graph, run the steps in the proper sequence, and skip unnecessary steps.

#### Configure a `Workflow Step`:

Each key under `WORKFLOW` is the unique name of a `Workflow Step`.
> ⚠️**Workflow Step names** must start in Uppercase and not contain underscore (e.g. CombineLayers)

Each step has several properties:

* `OUTPUT`: **(Required)** The single, primary output file that this step creates. Its existence and timestamp
  are used for incremental builds. This can be a dummy marker file if the step has no actual output.
* `REQUIRES`: A list of other **Workflow Step names** that must be completed *before* this step can run.
* `INPUTS`: A list of input files needed including the `OUTPUT` of a previous step defined in `REQUIRES`.
* `POSITIONAL_FILENAMES`: A list of file paths or file path templates to be passed to the command without a
  preceding flag.
* `PARAMETERS`: Parameters for this step rule. **Step parameters have the
  highest precedence.**
* `RULE`: Defines the actual command to be run.
    * `NAME`: A descriptive name for the rule used for parameter merging. Must be lowercase.
    * `COMMAND`: A templated string for the shell command.
    * `DASH`: (Optional, defaults to `-`) The prefix for parameter flags (e.g., `-` or `--`).
    * `UNQUOTED_PARAMS`: A list of parameter keys whose values should **not** be enclosed in quotes.

> ⚠️ **Rule NAME** must be lowercase (e.g. create_hillshade).

**Example:**

```yaml
WORKFLOW:
  VRTFile:
    REQUIRES: [ ]
    OUTPUT: "{BUILD_DIR}/{target_name}.vrt"
    RULE:
      NAME: "create_vrt"
      COMMAND: |
        mkdir -p $(dirname {OUTPUT}) && \
        gdalbuildvrt -strict -overwrite {PARAMETERS} {OUTPUT} {INPUTS}
    INPUTS:
      - "{DATA_FOLDER}{TARGET_FILES}"

  DEMFile:
    REQUIRES: [VRTFile]
    OUTPUT: "{BUILD_DIR}/{target_name}_DEM.tif"
    RULE:
      NAME: "create_dem"
      COMMAND: gdalwarp {INPUTS[0]} {OUTPUT} {PARAMETERS}  -wo INIT_DEST=NO_DATA  -overwrite -multi  --config GDAL_CACHEMAX 30%
      UNQUOTED_PARAMS: ["te","tr"]
    INPUTS: ["{REQUIRES[0]}"]
```

---

## ⚠️Important Rules

1. **Parameter Precedence**: When parameters are defined for the same rule, they are merged
   using the following priority :
    1. `WORKFLOW` `PARAMETERS` (highest priority)
    2. `TARGET` `PARAMETERS`
    3. `GENERAL` `PARAMETERS` set by LiteBuild command arguments
    4. `GENERAL` `PARAMETERS` from the configuration file (lowest priority)

2. **General Variables vs. Parameters**:
    * **General Variables** are variables defined under `GENERAL` or a `TARGET` (like `PROJECT_NAME` or `EXTENT`).
      . They can be used in *any* template string (e.g., `OUTPUT`,  `POSITIONAL_FILENAMES`, and
      `PARAMETERS`).
    * `PARAMETERS` are variables defined inside a PARAMETERS block. They can
      **only** be used inside a `COMMAND` template via the `{PARAMETERS}` placeholder. They cannot be
      used to construct file paths.

3. **Forbidden items in `PARAMETERS`**:
    * Parameters are resolved first, so you **cannot** use placeholders that depend on later steps inside the
      `PARAMETERS` block. The following placeholders are forbidden in `PARAMETERS`:
    * `{OUTPUT}`, `{INPUTS}`, `{POSITIONAL_FILENAMES}`.

---

## Variables and Placeholders

### 1. General-Purpose Variables

| Variable           | Scope  | Description                                                               |
|--------------------|--------|---------------------------------------------------------------------------|
| `{target_name}`    | Global | The name of the `TARGET` you are currently building (e.g., "USWest").     |
| `{<user-defined>}` | Global | Any parameter you define directly under `GENERAL` or the active `TARGET`. |

### 2. Command-Only Placeholders

These are **only available inside the `COMMAND` template** of a `RULE`.

| Placeholder              | Description                                                                    |
|--------------------------|--------------------------------------------------------------------------------|
| `{OUTPUT}`               | The step's final, resolved output file path.                                   |
| `{INPUTS[n]}`            | A single file from the resolved input list by its index (e.g., `{INPUTS[0]}`). |
| `{PARAMETERS}`           | A space-separated string of all merged, dashed parameters, safely quoted.      |
| `{POSITIONAL_FILENAMES}` | A space-separated string of all resolved positional filenames, safely quoted.  |

### 3. List-Resolution Tokens

These are special instructions **only used inside the  `INPUTS` lists** to build the file list.

| Token            | Used In        | Description                                                                             |
|------------------|----------------|-----------------------------------------------------------------------------------------|
| `{TARGET_FILES}` | `INPUTS` lists | A placeholder replaced by each filename from the active `TARGET`'s `TARGET_FILES` list. |
| `{REQUIRES[n]}`  | `INPUTS` lists | A placeholder replaced by the `OUTPUT` path of a step listed in the `REQUIRES` block.   |

---

## How Commands Are Built: An Example

This section explains how LiteBuild creates the final command for the `Hillshade_Layer` step when building
the `USWest` target.

### 1. Start with the Command Template

LiteBuild begins with the `COMMAND` template from the `HillshadeLayer` step:

- **`gdaldem hillshade {INPUTS[0]} {OUTPUT} {PARAMETERS}`**

### 2. Construct the File List

* **`{OUTPUT}`**: The `OUTPUT` template is `"{target_name}_hillshade.tif"`. For the `USWest` target, this resolves to *
  *`USWest_hillshade.tif`**.
* **Input File List**:
    2. **`INPUTS`**: The key is `["{REQUIRES[0]}"]`.
        * The `REQUIRES` list is `["DEM_File"]`.
        * `{REQUIRES[0]}` refers to the `OUTPUT` of the `DEM_File` step, which is `"{target_name}_DEM.tif"`.
        * This resolves to **`USWest_DEM.tif`**.
    3. **Final Input List**: The combined list is `['USWest_DEM.tif']`.
* **`{INPUTS[0]}`**: This placeholder is replaced by the first item from the final list: **`USWest_DEM.tif`**.

### 3. Construct the Parameter String

The `Rule` name is `create_hillshade`. LiteBuild merges the `PARAMETERS`:

1. **From `GENERAL`**: `{"compute_edges": true, "co": ["COMPRESS=JPEG", "JPEG_QUALITY=85"]}`
2. **From `TARGETS` (`USWest`)**: Merges in `z: 2` and `igor: true`.
3. **Final Parameters**: `{"compute_edges": true, "co": [...], "z": 2, "igor": true}`
4. **`{PARAMETERS}`**: This becomes: **`-compute_edges -co "COMPRESS=JPEG" -co "JPEG_QUALITY=85" -z 2 -igor`**

### 4. Assemble the Final Command

LiteBuild substitutes all resolved pieces into the template to get the final command:

**
`gdaldem hillshade USWest_DEM.tif USWest_hillshade.tif -compute_edges -co "COMPRESS=JPEG" -co "JPEG_QUALITY=85" -z 2 -igor`
**

---

### Explanation of the Data Flow

#### 1. Context Creation

* **Inputs:** The `GENERAL` and `TARGETS` sections of your `config.yml`.
* **Process:** LiteBuild gathers all the user-defined parameters from these sections (like `PROJECT_NAME`,
  `DATA_FOLDER`, `EXTENT`, etc.) and the engine-provided `{target_name}` into a single **Global Context** dictionary.
  This dictionary is the foundation for all templating.

#### 2. Input File Resolution

This is a critical two-part process that creates the final, ordered list of input files for the command.

* **`INPUTS` Resolution**
    * **Inputs:** The `INPUTS` and `REQUIRES` lists from the `Workflow Step` and the **Global Context**.
    * **Process:** The engine uses the `{REQUIRES[n]}` tokens to find the `OUTPUT` paths of the steps this step depends
      on.
    * **Output:** A **Resolved Inputs from Dependencies** list. This is the list that the `{INPUTS[n]}` placeholders
      will refer to in the final command.

#### 3. Path and Parameter Resolution

* **`OUTPUT`**: The `OUTPUT` template is resolved using the **Global Context** to create the **Resolved Output Path**.
* **`POSITIONAL_FILENAMES`**: This list is resolved using a *special, richer context* that includes the **Global Context
  ** PLUS the **Final Input List** and the **Resolved Output Path**. This is why you can use placeholders like
  `{INPUTS[0]}` and `{OUTPUT}` here.
* **`PARAMETERS`**: The `PARAMETERS` blocks from `GENERAL`, `TARGETS`, and the `Workflow Step` are merged according to
  precedence. The values are then templated using **only the Global Context**. This is the key restriction: this step
  knows nothing about the `Final Input List` or the `Resolved Output Path`.

#### 4. Final Assembly

* **Inputs:** The `COMMAND` template from the `RULE` and all the resolved strings and placeholders from the previous
  steps.
* **Process:** LiteBuild performs the final substitution. It injects the `PARAMETERS`, `POSITIONAL_FILENAMES`,
  `{OUTPUT}`, and `{INPUTS[n]}` into the `COMMAND` template.
* **Output:** The **Final Executable Command** that gets run in the shell.

## Advanced Techniques

### Multi-Command Steps

If a single `Workflow Step` requires multiple shell commands, use the YAML literal block style (`|`) and the shell `&&`
operator. This preserves atomicity; if the first command fails, the second will not run.

```yaml
  DEM_File:
    RULE:
      NAME: "create_dem"
      COMMAND: |
        gdalbuildvrt -overwrite  build/temp.vrt && \
        gdalwarp build/temp.vrt {OUTPUT} -wo "INIT_DEST=NO_DATA"
```

### Unquoted Parameters

Some command-line tools require multi-word arguments to be passed without quotes. To support this, add the parameter key
to the `UNQUOTED_PARAMS` list in the `RULE`.

```yaml
  DEM_Preview_File:
    RULE:
      NAME: "create_dem_preview"
      COMMAND: "gdal_translate {PARAMETERS} {INPUTS[0]} {OUTPUT}"
      UNQUOTED_PARAMS: [ "srcwin" ] # The value of 'srcwin' will not be quoted
    PARAMETERS:
      srcwin: "0 0 4000 4000"
```

### Known Issues

When an optional parameter is defined in the `config.yml` but is given no value (e.g., `host:`), LiteBuild incorrectly
includes it in the final command with a string value of "None". The current workaround is to handle this defensively in
any custom helper scripts.