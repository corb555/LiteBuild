## LiteBuild Configuation File

LiteBuild is a lightweight, intelligent build system designed specifically for data processing pipelines and shell workflows.  The philosophy of LiteBuild is to use explicit file based configurations that leverage
powerful Command templating.

LiteBuild uses a config file to:

* To produce all the commands necessary to build an item. 
* Build a dependency graph to determine the required operations, dependencies, and order of execution.
* Create the exact command for each step by recursively substituting parameters and file names into the command templates 
  you provide.

## Core Concepts

* **Workflow Step**: A `Workflow Step` is the main unit of work. It is a single, atomic *action* in your build process.
  Each step produces one output file and can require other steps. Examples include "CreateDEM," "GenerateHillshade,"
  or "CombineLayers."  The Workflow Section should be rarely updated. Any items that
  might change should utilize command templating and parameters.
* **Rule**: A `Rule` is the part of a `Workflow Step` that defines a template for the actual command to be run. It contains the
  `COMMAND` template and directives for how input files should be formatted.
* **Command Template**: Commands should only contain parameters that will never change.  LiteBuild has a powerful, hierarchical
  templating system that should be leveraged for any items that might change.
* **Profile**: (OPTIONAL) A `profile` is a specific set of parameters for the final product you want to build. For example, you
    might have a `USWest` profile that uses one set of input files and a `Utah` profile that uses another. This allows you
    to manage multiple build variations from a single configuration.

## Config File

> ⚠️**Config file names** must begin with the prefix `LB_` (e.g., `LB_classify.yml`).
> The file must include `config_type: "LiteBuild"`

## Configuration Sections

The config file is organized into three top-level sections: `GENERAL`,  `WORKFLOW`, and `PROFILES`

###  `GENERAL` Section

This section defines global variables available to the entire project.

#### Required Keys:

> * `PROJECT_NAME`: A short identifier for the project (e.g., "WesternUS").
> * `INPUT_DIRECTORY`: **(Special Key)** The base path for INPUT_FILES source data.
>    * *Behavior:* LiteBuild automatically joins this path with every filename listed in a profile's `INPUT_FILES` list.
>    * *Note:* If your files are absolute paths, you can set this to `""` (empty string).

#### Optional Keys:

You can define any custom key-value pairs here (e.g., `BUILD_DIR`, `PREVIEW_MODE`), which become available as template 
variables (e.g., `{BUILD_DIR}`) throughout the rest of the configuration.

* `PARAMETERS`: A sub-section used to define default CLI flags for specific rules. These have the *lowest precedence* and 
are overridden by Profile or Step parameters.
LiteBuild can be started from the command line with switches to define `GENERAL` parameters. These override any matching
parameters in the config file.

**Example:**

```yaml
# ===  GENERAL PARAMETERS  ===
GENERAL:
  PROJECT_NAME: "US"
  DATA_FOLDER: "geodata"
  BUILD_DIR: "build/{profile_name}"

  # The global default directory for INPUT_FILES.
  INPUT_DIRECTORY: "{DATA_FOLDER}/gmted_2010"

  # Default parameters for rules.
  PARAMETERS:
    create_dem:
      r: cubicspline
      co: "COMPRESS=ZSTD"
```

---

###  `WORKFLOW` Section

This section defines all `Workflow Steps`. LiteBuild uses this to construct the dependency
graph, templated command, and run steps in the proper sequence.

#### Configure a `Workflow Step`:

Each key under `WORKFLOW` is the unique name of a `Workflow Step`.
> ⚠️**Workflow Step names** must be in `PascalCase` (e.g., `CombineLayers`).

* `OUTPUT`: **(Required)** The **single**, primary output file that this step creates. Its existence and timestamp are used
  for incremental builds.
* `REQUIRES`: A list of other **Workflow Step names** that must be completed *before* this step can run.
* `INPUTS`: **(Required)** The single source of truth for all file-based inputs. This defines both the dependencies to
  track  changes and the files that will be formatted into the command line.
* `PARAMETERS`: Parameters for this step's command. 
* `DASH`: (Optional, defaults to `-`) The prefix used for parameters in the `{PARAMETERS}` block. Set this to `--` for 
   Python scripts or GNU-style tools.
* `RULE`: Defines the actual command to be run.
    * `NAME`: A descriptive name for the rule used for parameter merging. Must be in `snake_case`.
    * `COMMAND`: A templated string for the shell command.  Templating rules are described later.
    * `INPUT_STYLE`: (Optional, defaults to `positional`) How the `{INPUTS}` placeholder should be formatted. Can be
      `positional` or `switch`.
    * `INPUT_QUOTED`: (Optional, defaults to `true`) Whether to wrap each input filename in shell quotes. Set to `false`
      for tools like `gdalbuildvrt`.
    * `INPUT_SWITCH_NAME`: (Required if `INPUT_STYLE` is `switch`) The command-line flag to precede each input file (
      e.g., `--input`, `-f`).

**Example:**

```yaml
WORKFLOW:
  VRTFile:
    REQUIRES: [ ]
    OUTPUT: "{BUILD_DIR}/{profile_name}.vrt"
    RULE:
      NAME: "create_vrt"
      INPUT_STYLE: "positional"
      INPUT_QUOTED: false # gdalbuildvrt needs unquoted filenames
      COMMAND: |
        mkdir -p $(dirname {OUTPUT}) && \
        gdalbuildvrt -strict -overwrite {PARAMETERS} {OUTPUT} {INPUTS}
    INPUTS: "{INPUT_FILES}"

  DEMFile:
    REQUIRES: [ VRTFile ]
    OUTPUT: "{BUILD_DIR}/{profile_name}_DEM.tif"
    RULE:
      NAME: "create_dem"
      COMMAND: gdalwarp {INPUTS[0]} {OUTPUT} {PARAMETERS} -overwrite -multi
      UNQUOTED_PARAMS: [ "te", "tr" ]
    INPUTS: "{REQUIRES[0]}"
```

---

## Important Rules

1. **Parameter Precedence**: Parameters for a command are merged using the following
   priority:
   1 `PROFILE` `PARAMETERS:` (highest)
   2 `Command-line arguments` 
   3 `GENERAL` `PARAMETERS:` 
   4 `WORKFLOW` Step `PARAMETERS:` (lowest)

2. **General Variables vs. Parameters**:
    * **General Variables** are defined under `GENERAL` or a `PROFILE` (like `PROJECT_NAME`). They can be used in *any*
      template string and are copied as-is.
    * **Parameters** are defined inside a `PARAMETERS` block. They can **only** be used inside a `COMMAND` template via
      the `{PARAMETERS}` placeholder.  Parameters produce a space-separated string of single dashed parameters, safely quoted.
      Other  parameter formats can be specified.

---

## Variables and Placeholders

### 1. General-Purpose Variables

| Variable           | Scope  | Description                                                               |
|--------------------|--------|---------------------------------------------------------------------------|
| `{profile_name}`   | Global | The name of the `PROFILE` you are currently building (e.g., "USWest").    |
| `{<user-defined>}` | Global | Any variable you define directly under `GENERAL` or the active `PROFILE`. |

### 2. Command-Only Placeholders

These are **only available inside the `COMMAND` template** of a `RULE`.

| Placeholder    | Description                                                                            |
|----------------|----------------------------------------------------------------------------------------|
| `{OUTPUT}`     | The step's final, resolved output file path.                                           |
| `{INPUTS}`     | A formatted string of all resolved input files, based on `INPUT_STYLE`.                |
| `{INPUTS[n]}`  | A single, quoted file from the resolved input list by its index (e.g., `{INPUTS[0]}`). |
| `{PARAMETERS}` | A space-separated string of all merged, single dashed parameters, safely quoted.   See |
|                | Advanced to use double-dash                                                            |

### 3. INPUT Templating

These are special instructions **only used inside the `INPUTS` block** to build the file list. 
"{REQUIRES[n]}" will be replaced with the output file of that requirement.  This allows you to change
 filenames in a step without needing to change the next step in the pipeline.

| Token             | Used In  | Description                                                                           |
|-------------------|----------|---------------------------------------------------------------------------------------|
| `"{INPUT_FILES}"` | `INPUTS` | Replaced by the list of files from the active profile with INPUT_DIRECTORY prepended. |
| `"{REQUIRES[n]}"` | `INPUTS` | Replaced by the `OUTPUT` path of a required dependency step.                          |

---

## How Commands Are Built: An Example

This section explains how LiteBuild creates the final command for the `VRTFile` step when building the `USWest` profile.

### 1. Start with the Command Template

LiteBuild begins with the `COMMAND` template from the `VRTFile` step's `RULE`:

- **`gdalbuildvrt {PARAMETERS} {OUTPUT} {INPUTS}`**

### 2. Construct the Unformatted Input File List

1. The `INPUTS` block contains `"{INPUT_FILES}"`.
2. LiteBuild finds the `USWest` profile. It sees no `INPUT_DIRECTORY`, so it falls back to the `GENERAL` section's
   INPUT_DIRECTORY: `geodata/gmted_2010`.
3. It combines this directory with the `INPUT_FILES` list from the profile.
4. The final, unformatted list of files is:
   `['geodata/gmted_2010/10n090w.tif', 'geodata/gmted_2010/10n120w.tif']`

### 3. Format the `{INPUTS}` Placeholder

LiteBuild now looks at the `RULE` directives to format the input files:

- `INPUT_STYLE`: `positional` -> The files will be a space-separated list.
- `INPUT_QUOTED`: `false` -> No quotes will be added.
- The `{INPUTS}` placeholder resolves to the string:
  **`geodata/gmted_2010/10n090w.tif geodata/gmted_2010/10n120w.tif`**

### 4. Resolve Other Placeholders & Parameters

* **`{OUTPUT}`**: The `OUTPUT` template is `"{BUILD_DIR}/{profile_name}.vrt"`. This resolves to *
  *`build/USWest/USWest.vrt`**.
* **`{PARAMETERS}`**: The `RULE` name is `create_vrt`. LiteBuild finds matching `PARAMETERS` in the `GENERAL` section
  and assembles the string: **`-resolution highest -vrtnodata -9999`**.

### 5. Assemble the Final Command

LiteBuild substitutes all resolved pieces into the template to get the final command:

**
`gdalbuildvrt -strict -overwrite -resolution highest -vrtnodata -9999 build/USWest/USWest.vrt geodata/gmted_2010/10n090w.tif`
**

---

## Advanced Techniques

### Multi-Command Steps

If a `Workflow Step` requires multiple shell commands, use the YAML literal block style (`|`) and the shell `&&`
operator. This preserves atomicity; if the first command fails, the second will not run.

```yaml
  MyStep:
    RULE:
      NAME: "my_rule"
      COMMAND: |
        echo "First command" && \
        echo "Second command"
```

### Unquoted Parameters

Some tools require multi-word arguments to be passed without quotes. To support this, add the parameter key to the
`UNQUOTED_PARAMS` list in the `RULE`.

```yaml
  DEMPreview:
    RULE:
      NAME: "create_dem_preview"
      COMMAND: "gdal_translate {PARAMETERS} {INPUTS[0]} {OUTPUT}"
      UNQUOTED_PARAMS: [ "srcwin" ] # The value of 'srcwin' will not be quoted
    PARAMETERS:
      srcwin: "0 0 4000 4000"
```

### Parameter Prefixes (`DASH`)

By default, LiteBuild formats the `{PARAMETERS}` string using a single dash (e.g., `-resolution highest`). Many  tools  
require double dashes (e.g., `--input-csv`).

You can override this behavior by setting the `DASH` key in the `RULE` block.

```yaml
  UpdateColumn:
    RULE:
      NAME: "update_column"
      DASH: "--"  # Parameters will now look like: --target-column "rank"
      COMMAND: "update-column {PARAMETERS} ..."
```

### Explicit Input Ordering

By default, LiteBuild resolves inputs based on the `INPUTS` list order. If a command requires inputs in a specific, 
non-standard order (or needs to mix file inputs with other arguments), you can use the `POSITIONAL_FILENAMES` block.

*   This block creates a specific list of files that will be passed to the `{POSITIONAL_FILENAMES}` token (if used in a 
custom command script) or can be referenced by index.
*   *Note: In the provided YAML, `POSITIONAL_FILENAMES` is not used in the templates shown, but it is a valid key in the 
schema.*

###  `PROFILES` Section

This optional section allows you to define different build variations. Each profile can specify its own unique set of
input files and override default parameters.

#### Configure `PROFILES`:

Each key under `PROFILES` is the unique name of a build profile.

* `INPUT_DIRECTORY`: **(Special Key)** Overrides the `GENERAL` `INPUT_DIRECTORY` for this specific profile.
* `INPUT_FILES`: **(Special Key)** A list of source filenames specific to this profile. The `INPUT_DIRECTORY` path will
  be automatically prepended to each of these.
* `PARAMETERS`: Overrides default parameters for specific rules. **Profile parameters have higher precedence
  than `GENERAL` parameters.**

**Example:**

```yaml
# ===   PROFILES ===
PROFILES:
  USWest:
    # This profile uses the default INPUT_DIRECTORY from GENERAL.
    INPUT_FILES:
      - "10n090w_20101117_gmted_mea075.tif"
      - "10n120w_20101117_gmted_mea075.tif"
    PARAMETERS:
      create_dem:
        te: -13917257.3 2835222.9 -9710737.7 6435460.8

  CanadaRockies:
    # This profile overrides the global INPUT_DIRECTORY.
    INPUT_DIRECTORY: "{DATA_FOLDER}/cdem"
    INPUT_FILES:
      - "cdem_082O.tif"
      - "cdem_082P.tif"
    PARAMETERS:
      create_hillshade:
        z: 2
```

---
### `PROFILE GROUPS` Section

You can also provide  `PROFILE_GROUPS`.  This allows you to run all the
profiles in that group with a single command.
```yaml
PROFILE_GROUPS:
  "ALL": 
     - "USWest"
     - "CanadaRockies"
```
