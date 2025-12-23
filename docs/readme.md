# **`LiteBuild`**

>
> ⚠️ THIS IS BETA SOFTWARE
>
`LiteBuild` is a lightweight build system for **data processing and general-purpose shell workflows**.

LiteBuild is optimized for workflows where the primary actions are
**running templated shell commands** that transform data files,
image manipulation, or scientific computing tasks. It provides a clean and powerful way to
orchestrate complex data pipelines.
---

## **Features**

#### **1. Declarative Workflow in a Single File**

The  build process is fully defined in one structured `LB_config.yml` file. This makes your workflow easy to understand,
version control, and reproduce. LiteBuild has no implicit steps.  Everything is explicitly defined in the config file.

#### **2. Powerful Parameter Management**

* **Templated Commands:** Construct complex shell commands dynamically from a hierarchy of parameters.
* **Hierarchical Configuration:** A three-tiered parameter system (`Step` > `Profile` > `General`) maximizes reusability
  and minimizes boilerplate.
* **Flexible Parameter Styles:** Natively supports single-dash, double-dash, quoted, and unquoted command-line
  arguments.

#### **3. Intelligent Build Engine**

* **True Incremental Builds:** The engine tracks file timestamps as well as hashes of commands, inputs, and parameters,
  ensuring that steps are re-run when anything that affects their output has changed.
* **Automatic Parallel Execution:** LiteBuild analyzes your dependencies and runs independent branches of your workflow
  simultaneously to minimize build times.
* **Atomic Outputs:** Each step must produce a single, primary output file, which serves as a clean and reliable caching key
  for its state.
* **Automatic Workflow Documentation:** A built-in "describe" function generates a Markdown file with a Mermaid diagram
  of your workflow, detailed information about each step, and a complete, ordered list of the shell commands for a full,
  clean build and integrated readme file if provided.
* **Flexible Invocation:**

- GUI - LiteBuild can be run from a simple GUI
- Command Line - LiteBuild can be launched from the command line
- Embedded - The LiteBuild engine library can be incorporated into an app. Configuration can be managed via the
  structured
  YAML file or passed as a dictionary.

## Configuration

The config file is organized into five sections. 
> A detailed description is in configuration.md

1. **OVERVIEW:** A brief description of the workflow's purpose.
2. **README:** The file path to a more detailed Markdown file. The contents of this file will be embedded
   in the `describe` output.
3. **GENERAL:**
    * Defines global parameters (e.g., `PROJECT_NAME`, `PREVIEW`) available for templating throughout the file.
    * Can contain a `PARAMETERS` sub-section to define default parameters for any rule (lowest precedence).

4. **PROFILES:**
    * Each key under `PROFILES` defines a specific set of parameters.
    * Contains the data unique to that profile, such as source file lists.
    * Can contain a `PARAMETERS` sub-section to provide profile-specific parameter overrides (medium precedence).

5. **`WORKFLOW:**
    * Defines the build workflow as a list of steps.
    * Each step defines:
        * `RULE`: The name of the rule.
        * `REQUIRES`: A list of other step names this step depends on.
        * `OUTPUT`: A single, templated output file path.
    * Each step can optionally define:
        * `INPUTS`: A list of input files or the outputs of dependency steps.
        * `PARAMETERS`: A dictionary of parameters that override all other defaults (highest precedence).
          See configuration.md for details.

---

## **Installation:**

The `LiteBuild` package and all its dependencies are directly installable from PyPI via
`pip`.
