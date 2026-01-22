
# **`LiteBuild`**

>
> ⚠️ **THIS IS BETA SOFTWARE**
>
> `LiteBuild` is under active development. APIs and configuration formats may change.

`LiteBuild` is a lightweight, intelligent build system designed specifically for **data processing 
pipelines and shell workflows**.

While many build tools focus on compiling source code, LiteBuild is optimized for workflows where 
the primary actions are **running templated shell commands** to transform data files, manipulate 
images, or execute scientific computing tasks. It provides a clean, declarative way to orchestrate 
complex pipelines without the overhead of heavy enterprise orchestration tools.

---

## **Why Use LiteBuild?**

You might currently be managing your workflows with a series of Bash scripts, Makefiles, or manual 
execution. `LiteBuild` bridges the gap between simple scripts and complex orchestration platforms.

**Use LiteBuild if you need to:**
*   **Stop Re-running Expensive Tasks:** If you change a parameter in the middle of a pipeline, LiteBuild knows exactly which steps need 
to re-run and which are still valid, saving you hours of processing time.
*   **Separate Data from Logic:** You want to run the same sequence of operations (The Workflow) on different datasets (The Profiles) without duplicating settings.
*   **Tame Parameter Chaos:** You have complex command-line tools that require dozens of flags. LiteBuild organizes these hierarchically, keeping 
your commands readable and reusable.
*   **Self-Documenting Pipelines:** You need to hand off your work to others. LiteBuild auto-generates visual diagrams and documentation of exactly 
what your pipeline does.

---

## **Key Features & Benefits**

### **1. Declarative Workflow in a Single File**
The entire build process is defined in one structured `LB_config.yml` file.
*   **The Benefit:** There is no "hidden magic" or scattered logic. Your infrastructure is defined as code, making your workflow version-controllable, 
fully reproducible, and easy for new team members to read and understand.

### **2. Powerful Parameter Management**
LiteBuild treats command-line arguments as first-class citizens.
*   **Templated Commands:** Construct complex shell commands dynamically using a straightforward syntax.
*   **Hierarchical Configuration:** A three-tiered system (**Step** overrides **Profile**, which overrides **General**) allows you to define defaults once 
and override them only when necessary.
*   **Flexible Parameter Styles:** Whether your tools use single dashes (`-v`), double dashes (`--verbose`), or positional arguments, LiteBuild handles the 
formatting natively.
*   **The Benefit:** Drastically reduces boilerplate in your configuration. You define the logic of *how* a tool runs once, and feed it different parameters 
based on the context.

### **3. Intelligent Build Engine**
The engine is designed to ensure correctness and speed.
*   **True Incremental Builds:**  LiteBuild tracks **hashes of commands, inputs, and parameters**.
    *   If you change a command-line flag (e.g., changing a threshold from 0.5 to 0.6), LiteBuild knows the output is stale and re-runs 
    the step, even if the input files haven't been touched.
*   **Automatic Parallel Execution:** The engine builds a dependency graph and automatically runs independent branches of your workflow simultaneously.
    *   Maximizes resource utilization and reduces total build time without manual threading logic.
*   **Atomic Outputs:** Each step must produce a single, primary output file.
    *   *Why this matters:* This enforces a clean architecture where every file on your disk can be traced back to a specific build step, preventing "zombie files" 
    from corrupting your results.

### **4. Automatic Workflow Documentation**
LiteBuild includes a "describe" function (`build --describe`).
*   **The Benefit:** Documentation often goes stale the moment code changes. LiteBuild generates a Markdown file containing a **Mermaid diagram** of the 
workflow and a complete, ordered list of every shell command that *would* be executed. It serves as dynamic, always-accurate documentation for your project.

### **5. Flexible Invocation**
*   **GUI:** For users who prefer a visual interface.
*   **Command Line:** For integration into scripts and servers.
*   **Embedded:** Can be imported as a Python library to add build capabilities to  applications.

---

## **Configuration Overview**

These are the key sections in the configuration.
`configuration.md` provides a detailed description of each.

1.  **WORKFLOW:**
    This defines the the steps to run and provides a template of the command to run for the step.
    *   **Rule:** The name of the step and the command template to run.  The parameters in the template will be filled in 
    by LiteBuild. Example command template:
    `gdalwarp {INPUTS[0]} {OUTPUT} {PARAMETERS} `
    *   **Requires:** The steps that must finish before running this step.
    *   **Output:** The target file this step creates.
    *   **Inputs:** The files this step reads.  You can specify that the Input is an Output from a specific step without
    putting in the actual filename.  This makes creating chains of commands straightforward.

The parameters in the command template are filled in using the following sections:

2. **GENERAL:**
    This defines the "world" the build runs in.
    *   Set global parameters (e.g., `PROJECT_ROOT`, `DEBUG_MODE`) available to every step.
    *   Define default parameters that apply to every rule unless overridden.
    
3.  **PROFILES:**
    *   A Profile is a specific "run scenario" or "dataset."
    *   For example, you might have profiles named `Germany`, `France`, or `Test_Run`.
    *   Profiles contain the variable data (like source file paths) that are fed into the workflow.

---

## **Installation**

The `LiteBuild` package and all its dependencies are directly installable from PyPI via `pip`:

```bash
pip install litebuild
```