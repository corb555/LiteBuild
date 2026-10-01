import hashlib
import json
import re
import shlex
from typing import Any, List

from LiteBuild.build_logger import get_logger


# command_generator.py

class CommandGenerator:
    """Resolve LiteBuild workflow configuration into executable command specs.

    Command generation is intentionally quiet during normal operation. Invalid
    configuration or unresolved templates raise descriptive exceptions so the
    orchestration layer can log the failure once. The one non-fatal validation
    case is when declared inputs are used only for dependency/freshness tracking
    and are not consumed by the command string.
    """

    class SafeFormatter(dict):
        """A dict subclass that returns the key itself if the key is missing."""

        def __missing__(self, key):
            return f"{{{key}}}"

    def __init__(self, parameters_config: dict, profile_config: dict):
        self.parameters_config = parameters_config
        self.profile_config = profile_config
        self.logger = get_logger()

    def generate_for_node(
            self, node_name: str, node_data: dict, context: dict, resolved_outputs: dict
    ) -> dict:
        # ... (Validation logic remains the same) ...
        late_bound_placeholders = ["{OUTPUT}", "{INPUTS}", "{PARAMETERS}", "{POSITIONAL_FILENAMES}"]
        step_params = node_data.get("PARAMETERS", {})
        for key, value in step_params.items():
            for placeholder in late_bound_placeholders:
                if placeholder in str(value):
                    raise ValueError(
                        f"WORKFLOW step '{node_name}': placeholder '{placeholder}' "
                        "is not allowed inside the PARAMETERS block."
                    )

        final_params = self._merge_parameters(node_name, node_data, context)

        all_resolved_inputs = self._resolve_all_inputs(node_name, node_data, context,
            resolved_outputs, self.profile_config)

        positional_filenames_templates = node_data.get("POSITIONAL_FILENAMES", [])
        if isinstance(positional_filenames_templates, str):
            positional_filenames_templates = [positional_filenames_templates]

        command_template = node_data["RULE"]["COMMAND"]
        rule_name = node_data['RULE']['NAME']

        if "{OUTPUT}" not in command_template:
            raise ValueError(
                f"WORKFLOW step '{node_name}': command template for rule "
                f"'{rule_name}' is missing required {{OUTPUT}} placeholder."
            )

        # Validate {INPUTS} and {POSITIONAL_FILENAMES}
        no_inputs_flag = node_data["RULE"]["NO_INPUTS"]

        has_inputs_placeholder = (
                "{INPUTS}" in command_template or re.search(r'{INPUTS\[\d+\]}', command_template))

        has_positional_placeholder = "{POSITIONAL_FILENAMES}" in command_template

        if not no_inputs_flag and not has_inputs_placeholder and not has_positional_placeholder:
            self.logger.warning(
                f"WORKFLOW step '{node_name}' declares inputs but its command does not "
                "reference {INPUTS} or {POSITIONAL_FILENAMES}. Inputs will still be "
                "used for dependency and freshness tracking."
            )

        if final_params and "{PARAMETERS}" not in command_template:
            raise ValueError(
                f"WORKFLOW step '{node_name}': parameters are defined but the command "
                "template does not contain {PARAMETERS}."
            )

        if positional_filenames_templates and "{POSITIONAL_FILENAMES}" not in command_template:
            raise ValueError(
                f"WORKFLOW step '{node_name}': positional filenames are defined but "
                "the command template does not contain {POSITIONAL_FILENAMES}."
            )

        resolved_output_file = self._resolve_nested_template(node_name, node_data["OUTPUT"],
                                                             context)
        resolved_outputs[node_name] = resolved_output_file

        command_hash = self._get_hash(command_template)
        inputs_hash = self._get_hash(sorted(all_resolved_inputs))
        params_hash = self._get_hash(final_params)

        local_context = {**context, 'INPUTS': all_resolved_inputs, 'OUTPUT': resolved_output_file}
        resolved_positional_filenames = self._resolve_nested_template(node_name,
            positional_filenames_templates, local_context)

        command_str = self._build_command_string(node_name, rule_data=node_data["RULE"],
            inputs=all_resolved_inputs, output=resolved_output_file, params=final_params,
            positional_filenames=resolved_positional_filenames, context=context)

        return {
            "cmd_string": command_str, "input_files": all_resolved_inputs,
            "output": resolved_output_file,
            "hashes": {"command": command_hash, "inputs": inputs_hash, "params": params_hash}
        }

    def _merge_parameters(self, node_name: str, node_data: dict, context: dict) -> dict:
        rule_name = node_data["RULE"]["NAME"]
        default_params = self.parameters_config.get(rule_name, {})
        profile_params = self.profile_config.get("PARAMETERS", {}).get(rule_name, {})
        workflow_params = node_data.get("PARAMETERS", {})

        merged = {**default_params, **profile_params, **workflow_params}
        return self._resolve_nested_template(node_name, merged, context)

    def _resolve_all_inputs(
            self, node_name: str, node_data: dict, context: dict, resolved_outputs: dict,
            profile_config: dict
    ) -> List[str]:
        all_inputs = []
        input_templates = node_data.get("INPUTS", [])
        if isinstance(input_templates, str):
            input_templates = [input_templates]

        requires_list = node_data.get("REQUIRES", [])

        for tmpl in input_templates:
            match = re.fullmatch(r"{REQUIRES\[(\d+)\]}", tmpl)
            if match:
                dep_index = int(match.group(1))
                if dep_index >= len(requires_list):
                    raise ValueError(
                        f"WORKFLOW step '{node_name}': REQUIRES index [{dep_index}] "
                        "is out of range."
                    )
                dep_name = requires_list[dep_index]
                all_inputs.append(resolved_outputs[dep_name])
                continue

            if tmpl == "{INPUT_FILES}":
                all_inputs.extend(context.get("INPUT_FILES", []))
                continue

            resolved_item = self._resolve_nested_template(node_name, tmpl, context)
            if isinstance(resolved_item, list):
                all_inputs.extend(resolved_item)
            else:
                all_inputs.append(resolved_item)

        return all_inputs

    def _build_command_string(
            self, node_name: str, rule_data: dict, inputs: List[str], output: str, params: dict,
            positional_filenames: List[str], context: dict
    ) -> str:
        template = rule_data["COMMAND"]

        if "{INPUTS}" in template and not re.search(r'{INPUTS\[\d+\]}', template):
            inputs_str = self._format_inputs_string(rule_data, inputs)
            params_str = self._format_shell_params(params, rule_data.get("DASH", "-"),
                rule_data.get("UNQUOTED_PARAMS", []))
            positional_filenames_str = ""
        else:
            unquoted_positionals = rule_data.get("UNQUOTED_POSITIONALS", False)
            positional_filenames_str = " ".join(
                [f for f in positional_filenames] if unquoted_positionals else [shlex.quote(p) for p
                                                                                in
                                                                                positional_filenames])
            params_str = self._format_shell_params(params, rule_data.get("DASH", "-"),
                rule_data.get("UNQUOTED_PARAMS", []))
            inputs_str = " ".join([shlex.quote(p) for p in inputs])

        template_context = {
            **context, 'OUTPUT': output, 'INPUTS': inputs_str, 'PARAMS': params,
            'PARAMETERS': params_str, 'POSITIONAL_FILENAMES': positional_filenames_str
        }

        def resolve_input_index(match: re.Match) -> str:
            input_index = int(match.group(1))
            if input_index >= len(inputs):
                raise ValueError(
                    f"WORKFLOW step '{node_name}': INPUTS index [{input_index}] "
                    "is out of range."
                )
            return shlex.quote(inputs[input_index])

        final_template = re.sub(r'{INPUTS\[(\d+)\]}', resolve_input_index, template)

        # ---  ITERATIVE RESOLUTION ---
        safe_ctx = self.SafeFormatter(template_context)
        resolved_command = final_template

        # Loop to handle nested variables (e.g. {BUILD_DIR} -> build{PREVIEW})
        for i in range(5):
            prev = resolved_command
            try:
                resolved_command = resolved_command.format_map(safe_ctx)
            except ValueError as e:
                self._raise_formatting_error(e, node_name, final_template)

            if prev == resolved_command:
                break

        resolved_command = resolved_command.strip().replace('  ', ' ')

        # --- VALIDATE UNRESOLVED PLACEHOLDERS ---
        # Look for tokens that start with Uppercase chars inside braces.
        # This catches {PAR...}, {TYPO}, {MISSING_VAR}
        # It ignores {}, ${VAR}, and {awk_logic}
        unresolved = re.search(r'\{[A-Z][A-Za-z0-9_:,\.&]*\}', resolved_command)

        if unresolved:
            bad_token = unresolved.group(0)
            raise ValueError(
                f"WORKFLOW step '{node_name}': generated command contains unresolved "
                f"placeholder {bad_token}.\n"
                f"  Command template: \"{template}\""
            )

        try:
            shlex.split(resolved_command)
        except ValueError as e:
            raise ValueError(
                f"WORKFLOW step '{node_name}': generated command is not valid shell syntax.\n"
                f"  Error: {e}\n"
                f"  Generated command: {resolved_command}\n"
                f"  Command template: {template}"
            ) from e
        return resolved_command

    def _format_inputs_string(self, rule_data: dict, inputs: List[str]) -> str:
        style = rule_data.get('INPUT_STYLE', 'positional')
        quoted = rule_data.get('INPUT_QUOTED', True)
        formatted_inputs = [shlex.quote(f) if quoted else f for f in inputs]
        if style == 'positional':
            return " ".join(formatted_inputs)
        if style == 'switch':
            switch = rule_data.get('INPUT_SWITCH_NAME')
            if not switch:
                raise ValueError(
                    "RULE must define 'INPUT_SWITCH_NAME' when using 'switch' INPUT_STYLE.")
            parts = []
            for f in formatted_inputs:
                parts.extend([switch, f])
            return " ".join(parts)
        return ""

    @staticmethod
    def _format_shell_params(
            params_dict: dict, dash_style: str, unquoted_params: List[str]
    ) -> str:
        flags = []
        for key, value in params_dict.items():
            if value is None:
                continue
            flag = f"{dash_style}{key}"
            if key in unquoted_params:
                flags.extend([flag, str(value)])
            elif isinstance(value, bool):
                if value:
                    flags.append(shlex.quote(flag))
            elif isinstance(value, list):
                for item in value:
                    flags.extend([shlex.quote(flag), shlex.quote(str(item))])
            else:
                flags.extend([shlex.quote(flag), shlex.quote(str(value))])
        return " ".join(flags)

    def _resolve_nested_template(self, node_name: str, data: Any, context: dict) -> Any:
        if isinstance(data, str):
            safe_context = self.SafeFormatter(context)
            templated_string = data
            for i in range(5):
                prev_string = templated_string
                try:
                    try:
                        templated_string = templated_string.format_map(safe_context)
                    except ValueError as e:
                        self._raise_formatting_error(e, node_name, prev_string)
                except (FileNotFoundError, ValueError) as exc:
                    if isinstance(exc, ValueError): raise exc
                    # This catches the iterative safety check failure
                    missing_key = exc.args[0]
                    raise ValueError(
                        f"WORKFLOW step '{node_name}': could not resolve template "
                        f"variable '{missing_key}'.\n"
                        f"  Context: \"{prev_string}\""
                    ) from exc

            # --- FINAL VALIDATION ---
            try:
                return templated_string.format_map(context)
            except KeyError as e:
                missing_key = e.args[0]
                #  User-Facing Error Message
                raise ValueError(
                    f"WORKFLOW step '{node_name}': parameter '{{{missing_key}}}' is not "
                    "defined in GENERAL or PROFILE settings.\n"
                    f"  Template: \"{data}\""
                ) from e

        if isinstance(data, list):
            return [self._resolve_nested_template(node_name, item, context) for item in data]
        if isinstance(data, dict):
            return {k: self._resolve_nested_template(node_name, v, context) for k, v in
                    data.items()}
        return data

    @staticmethod
    def _get_hash(data: Any) -> str:
        canonical_string = json.dumps(data, sort_keys=True)
        return hashlib.sha256(canonical_string.encode('utf-8')).hexdigest()

    @staticmethod
    def _raise_formatting_error(
            e: ValueError, node_name: str, original_template: str, current_state: str = None
            ):
        """Translate Python formatting errors into useful configuration errors."""
        msg = str(e)

        if "Invalid format specifier" in msg:
            explanation = (
                "The command template contains an invalid placeholder format. "
                "A colon ':' appears inside a curly-brace expression."
            )
        elif "Unmatched" in msg:
            explanation = (
                "The command template contains unbalanced curly braces."
            )
        elif "KeyError" in msg:
            explanation = (
                "The command references a template variable that does not exist."
            )
        else:
            explanation = "LiteBuild could not process the command template."

        report = (
            f"WORKFLOW step '{node_name}': invalid command template.\n"
            f"  {explanation}\n"
            f"  Original template: \"{original_template}\""
        )

        if current_state and current_state != original_template:
            report += f"\n  Processed state: \"{current_state}\""

        report += f"\n  Python error: {msg}"

        raise ValueError(report) from e
