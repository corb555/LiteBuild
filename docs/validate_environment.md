# `validate_environment`

validate_environment is a stand-alone CLI tool which verifies that an execution environment contains the  binaries and versions required by a 
project. Requirements and version-discovery rules are declared in YAML.

## Features

- **PEP 440 version constraints:** Requirements use standard PEP 440 specifiers such as `>=3.8,<4`, `~=3.8`, and `==3.8.*`.
- **Configurable version extraction:** Each tool defines an executable, one approved version argument, and the regular expression used to extract its version.
- **Combined or split configuration:** A project may use one self-contained file or separate the shared tool definitions from the project requirements.
- **Strict validation:** Duplicate sections or keys, unknown fields, undefined required tools, malformed patterns, invalid versions, and failed probes are errors; no values are inferred or corrected.
- **Quiet execution:** A successful run produces no terminal output unless verbose mode is enabled. A failed run emits actionable diagnostics and returns a nonzero exit code.
- **Optional marker output:** When a marker path is supplied, successful validation creates an empty marker file for an external build system to use as a target.

## Configuration Model

Across all configuration files supplied in one invocation, there must be exactly one `tools` section and exactly one `requires` section.

- A combined configuration contains both sections.
- A split configuration places `tools` in one file and `requires` in another.
- Defining either section more than once is a fatal error; sections are never merged or overridden.
- Every name in `requires` must have a corresponding definition in `tools`.
- Defining a tool does not make it required. Only tools named in `requires` are validated.
- Duplicate YAML mapping keys are fatal errors, including duplicate tool names.
- Unknown sections and unknown fields are fatal errors.

The `tools` section is a mapping keyed by the canonical tool name. Each definition supplies a `command`, one `version_argument`, and a `match_pattern`.

`command` must be one executable name containing no whitespace, path separators, shell operators, variable references, or expansion syntax. Absolute and relative paths are not accepted. The executable is resolved from `PATH` and invoked directly without a shell.

`version_argument` must be exactly one of:

```text
--version
-version
-V
version
```

No additional arguments are permitted. The short `-v` option is intentionally excluded because it frequently enables verbose operation rather than displaying a version. Tools that support `-v`, including Node.js, commonly also support `--version`.

Each `match_pattern` must contain exactly one named capture group called `version`. Any other parentheses must be noncapturing. The validator searches the bounded output from both `stdout` and `stderr` and requires exactly one match across both streams. The `version` group must contain the complete version string to be parsed and tested.

The validator is intentionally strict. Invalid configuration and validator or system errors terminate the run immediately. 
Once configuration and preflight checks succeed, every required tool is probed and all environment failures are 
reported together. The validator does not infer intent, apply fallback parsing, or silently override duplicate configuration.


## Configuration Methods

### Method A: Single Combined Configuration

A combined file is convenient for a small or portable project:

```yaml
# combined_config.yml
tools:
  gdalwarp:
    command: gdalwarp
    version_argument: --version
    match_pattern: 'GDAL\s+(?P<version>[0-9]+(?:\.[0-9]+)+)'

  rasterio:
    command: rasterio
    version_argument: --version
    match_pattern: '^(?P<version>[0-9]+(?:\.[0-9]+)+)$'

requires:
  gdalwarp: '>=3.8,<4'
  rasterio: '>=1.3,<2'
```


### Method B: Split Configuration

Larger projects can maintain a shared registry of version-discovery rules while keeping project requirements short and explicit.

```yaml
# shared_tools.yml
tools:
  gdalwarp:
    command: gdalwarp
    version_argument: --version
    match_pattern: 'GDAL\s+(?P<version>[0-9]+(?:\.[0-9]+)+)'

  node:
    command: node
    version_argument: --version
    match_pattern: '^v(?P<version>[0-9]+(?:\.[0-9]+)+)$'
```

```yaml
# project_requires.yml
requires:
  gdalwarp: '>=3.8,<4'
  node: '>=20,<21'
```

The files do not form an override stack. Together they must still provide only one `tools` section and one `requires` section.

## Version Constraints

Version requirements are evaluated using PEP 440 semantics. Explicit ranges are often the clearest form:

```yaml
requires:
  gdalwarp: '>=3.8,<4'  # Any 3.x release from 3.8 onward
  node: '>=20,<21'      # Any 20.x release
```

Compatible-release constraints may also be used, but their precision is significant:

- `~=3.8` means `>=3.8` and `<4.0`.
- `~=3.8.0` means `>=3.8.0` and `<3.9.0`.
- `==3.8.*` accepts releases in the 3.8 series.

An extracted version that cannot be parsed as a PEP 440 version is an error. The validator does not rewrite or normalize vendor-specific version strings beyond the configured regular-expression extraction.

## CLI Usage

Pass one combined configuration or the two files of a split configuration with `-c/--config`. Use `-m/--marker` only when an external build system needs a marker target.

### Standard Quiet Run

```bash
# Combined configuration, without a marker
python validate_environment.py -c combined_config.yml

# Combined configuration, with an optional marker
python validate_environment.py \
  -c combined_config.yml \
  -m ./build/.env_valid

# Split configuration
python validate_environment.py \
  -c shared_tools.yml project_requires.yml \
  -m ./build/.env_valid
```

On success, the validator returns exit code `0` and prints nothing to `stdout`.

### Verbose Run

Use `-v/--verbose` to display each command, extracted version, constraint, and result:

```bash
python validate_environment.py \
  -c shared_tools.yml project_requires.yml \
  -m ./build/.env_valid \
  --verbose
```

Example output:

```text
[*] Checking tool 'gdalwarp'...
    -> Command: gdalwarp --version
    -> Extracted version: 3.8.4
    -> Required: >=3.8,<4
    -> Success
[*] Checking tool 'node'...
    -> Command: node --version
    -> Extracted version: 20.11.0
    -> Required: >=20,<21
    -> Success
[+] Environment validated. Created marker: ./build/.env_valid
```

## Marker File Contract

The marker is optional and contains no data. It is an output artifact only: the validator never reads it and does not use it to cache or skip validation.

When `-m/--marker` is supplied, the validator follows this contract:

1. Delete any existing marker immediately on startup.
2. Load and strictly validate the configuration.
3. Probe the required tools.
4. Create an empty marker only after every requirement succeeds.

Therefore:

- Exit code `0` guarantees that the current invocation succeeded and the requested marker was created.
- A failed invocation leaves no marker from either the current run or an earlier run.
- The external build system is responsible for deciding when the validator must run again.

The marker should be created atomically so that an interrupted or concurrent invocation cannot leave a partial success signal.

## Failure Behavior

Failure handling has two distinct phases.

### Configuration and System Errors

Configuration, invocation, and validator or system integrity errors fail immediately. No tool probes are performed after a configuration error, and probing stops if an unexpected system error prevents the validator from operating reliably.

Fail-fast errors include:

- A configuration file cannot be read or parsed.
- A YAML mapping contains a duplicate key.
- `tools` or `requires` is missing or defined more than once.
- A section or field is unknown.
- A required tool has no tool definition.
- A tool definition is incomplete, the command name is invalid, or the version argument is not approved.
- A requirement contains an invalid PEP 440 constraint.
- A regular expression is invalid, does not contain exactly one named `version` group, or contains another capture group.
- The marker cannot be removed or created when a marker path was requested.
- An unexpected operating-system or internal error prevents reliable validation.

These errors indicate that the validator cannot reliably determine the state of the environment. It does not combine conflicting definitions, guess at malformed configuration, or reinterpret invalid syntax.

### Environment Requirement Failures

After configuration and preflight validation succeed, the validator attempts every tool named in `requires`. An ordinary failure of one requirement does not prevent the remaining requirements from being checked.

Collected requirement failures include:

- The executable is not found on `PATH`.
- The version command times out or returns an unsuccessful status.
- The configured regular expression does not match the command output.
- The configured regular expression matches more than once across `stdout` and `stderr`.
- The extracted value is not a valid PEP 440 version.
- The installed version does not satisfy its requirement.

Each tool is still evaluated strictly. The validator does not search for alternative output, try another extraction rule, normalize an invalid version, or weaken a version constraint. It records the failure and continues with the next required tool.

After all required tools have been checked:

- If every requirement succeeds, the validator returns `0` and creates the requested marker.
- If any requirement fails, the validator returns `1`, produces an ordered list of every failed requirement, and leaves no marker.

Example:

```text
ERROR: Environment validation failed
[-] gdalwarp: version 3.2.1 does not satisfy requirement >=3.8,<4
[-] node: executable not found on PATH
[-] rasterio: version output did not match the configured pattern
```

Diagnostics are written to `stderr`. Exit codes distinguish successful validation, an unsatisfied environment, and invalid invocation or configuration:

| Exit code | Meaning |
| ---: | --- |
| `0` | Environment validated successfully. |
| `1` | A required tool could not be validated or did not satisfy its constraint. |
| `2` | Command-line usage or configuration is invalid, or a validator/system error prevents reliable validation. |

## Execution Rules

- A version probe always consists of exactly `[command, version_argument]`.
- The command is executed directly with shell processing disabled.
- `command` is resolved from `PATH`; configuration cannot supply a path or additional arguments.
- Both `stdout` and `stderr` are inspected because utilities differ in where they print version information.
- Exactly one configured version match must exist across both output streams.
- Every command has a finite timeout; a timeout is a validation failure.
- Captured output is bounded; exceeding the fixed limit is a validation failure.
- Verbose output includes the resolved executable path to help diagnose `PATH` differences.
- Configuration files and `PATH` are trusted project inputs. The restricted invocation format reduces accidental or injected arguments, but it is not a security sandbox: an executable resolved from a compromised `PATH` can still run arbitrary code.
