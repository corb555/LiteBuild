import hashlib
import json
import re
import shlex
from typing import List, Any


class CommandGenerator:
    """Generates commands based on configuration and build context."""

    class SafeFormatter(dict):
        """A dict subclass that returns the key itself if the key is missing."""
        def __missing__(self, key):
            return f"{{{key}}}"

    def __init__(self, general_config: dict, profile_config: dict):
        self.general_config = general_config
        self.profile_config = profile_config

    def generate_for_node(
            self, node_name: str, node_data: dict, context: dict, resolved_outputs: dict
    ) -> dict:
        # ... (Validation logic remains the same) ...
        # Define the late-bound placeholders for validation.
        late_bound_placeholders = ["{OUTPUT}", "{INPUTS}", "{PARAMETERS}", "{POSITIONAL_FILENAMES}"]
        step_params = node_data.get("PARAMETERS", {})
        for key, value in step_params.items():
            for placeholder in late_bound_placeholders:
                if placeholder in str(value):
                    error_msg = (f"❌ Configuration Error in WORKFLOW Step '{node_name}':\n"
                                 f"The placeholder '{placeholder}' is not allowed inside the "
                                 f"'PARAMETERS' block.")
                    raise ValueError(error_msg)

        # --- Resolve all components first ---
        final_params = self._merge_parameters(node_name, node_data, context)

        all_resolved_inputs = self._resolve_all_inputs(
            node_name, node_data, context, resolved_outputs, self.profile_config
        )

        positional_filenames_templates = node_data.get("POSITIONAL_FILENAMES", [])
        if isinstance(positional_filenames_templates, str):
            positional_filenames_templates = [positional_filenames_templates]

        command_template = node_data["RULE"]["COMMAND"]
        rule_name = node_data['RULE']['NAME']

        if "{OUTPUT}" not in command_template:
            error_msg = (f"❌ Configuration Error in WORKFLOW Step '{node_name}':\n"
                         f"   The COMMAND template for rule '{rule_name}' is missing the required "
                         f"{{OUTPUT}} placeholder.")
            raise ValueError(error_msg)

        has_inputs_placeholder = (
                "{INPUTS}" in command_template or re.search(r'{INPUTS\[\d+\]}', command_template))

        if not has_inputs_placeholder and "{POSITIONAL_FILENAMES}" not in command_template:
            print(f"⚠️  Configuration Warning in WORKFLOW Step '{node_name}': No inputs placeholder found.")

        if final_params and "{PARAMETERS}" not in command_template:
            raise ValueError(f"❌ Configuration Error in WORKFLOW Step '{node_name}': Parameters defined but {{PARAMETERS}} missing.")

        if positional_filenames_templates and "{POSITIONAL_FILENAMES}" not in command_template:
            raise ValueError(f"❌ Configuration Error in WORKFLOW Step '{node_name}': Positional filenames defined but placeholder missing.")

        resolved_output_file = self._deep_template(node_name, node_data["OUTPUT"], context)
        resolved_outputs[node_name] = resolved_output_file

        command_hash = self._get_hash(command_template)
        inputs_hash = self._get_hash(sorted(all_resolved_inputs))
        params_hash = self._get_hash(final_params)

        local_context = {**context, 'INPUTS': all_resolved_inputs, 'OUTPUT': resolved_output_file}
        resolved_positional_filenames = self._deep_template(
            node_name, positional_filenames_templates, local_context
        )

        command_str = self._build_command_string(
            node_name, rule_data=node_data["RULE"], inputs=all_resolved_inputs,
            output=resolved_output_file, params=final_params,
            positional_filenames=resolved_positional_filenames, context=context
        )

        return {
            "cmd_string": command_str, "input_files": all_resolved_inputs,
            "output": resolved_output_file,
            "hashes": {"command": command_hash, "inputs": inputs_hash, "params": params_hash}
        }

    @staticmethod
    def _get_hash(data: Any) -> str:
        canonical_string = json.dumps(data, sort_keys=True)
        return hashlib.sha256(canonical_string.encode('utf-8')).hexdigest()

    def _merge_parameters(self, node_name: str, node_data: dict, context: dict) -> dict:
        rule_name = node_data["RULE"]["NAME"]
        general_params = self.general_config.get("PARAMETERS", {}).get(rule_name, {})
        profile_params = self.profile_config.get("PARAMETERS", {}).get(rule_name, {})
        workflow_params = node_data.get("PARAMETERS", {})

        merged = {**general_params, **profile_params, **workflow_params}
        return self._deep_template(node_name, merged, context)

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
                    raise ValueError(f"Error in '{node_name}': REQUIRES index [{dep_index}] is out of range.")
                dep_name = requires_list[dep_index]
                all_inputs.append(resolved_outputs[dep_name])
                continue

            if tmpl == "{INPUT_FILES}":
                all_inputs.extend(context.get("INPUT_FILES", []))
                continue

            resolved_item = self._deep_template(node_name, tmpl, context)
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
            params_str = self._format_shell_params(
                params, rule_data.get("DASH", "-"), rule_data.get("UNQUOTED_PARAMS", [])
            )
            positional_filenames_str = ""
        else:
            unquoted_positionals = rule_data.get("UNQUOTED_POSITIONALS", False)
            positional_filenames_str = " ".join(
                [f for f in positional_filenames] if unquoted_positionals else
                [shlex.quote(p) for p in positional_filenames]
            )
            params_str = self._format_shell_params(
                params, rule_data.get("DASH", "-"), rule_data.get("UNQUOTED_PARAMS", [])
            )
            inputs_str = " ".join([shlex.quote(p) for p in inputs])

        template_context = {
            **context, 'OUTPUT': output, 'INPUTS': inputs_str, 'PARAMS': params,
            'PARAMETERS': params_str, 'POSITIONAL_FILENAMES': positional_filenames_str
        }

        def resolve_input_index(match: re.Match) -> str:
            input_index = int(match.group(1))
            if input_index >= len(inputs):
                raise ValueError(f"Error in '{node_name}': INPUTS index [{input_index}] is out of range.")
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
                f"\n❌ Configuration Error in WORKFLOW Step '{node_name}':\n"
                f"   The generated command contains a placeholder that was not resolved.\n"
                f"   Unresolved Token: {bad_token}\n"
                f"   Command Template: \"{template}\""
            )

        try:
            shlex.split(resolved_command)
        except ValueError as e:
            raise ValueError(
                f"\n❌ Configuration Error in WORKFLOW Step '{node_name}' COMMAND:\n"
                f"   - Error: {e}\n   - Generated COMMAND: \n{resolved_command}\n"
                f"   - Template: \n{template}\n"
            )
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
                raise ValueError("RULE must define 'INPUT_SWITCH_NAME' when using 'switch' INPUT_STYLE.")
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

    def _deep_template(self, node_name: str, data: Any, context: dict) -> Any:
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
                except Exception as e:
                    if isinstance(e, ValueError): raise e
                    # This catches the iterative safety check failure
                    missing_key = e.args[0]
                    raise ValueError(
                        f"❌ Templating Error in '{node_name}':\n"
                        f"   Could not resolve variable: '{missing_key}'\n"
                        f"   Context: \"{prev_string}\""
                    )

            # --- FINAL VALIDATION ---
            try:
                return templated_string.format_map(context)
            except KeyError as e:
                missing_key = e.args[0]
                # Improved User-Facing Error Message
                raise ValueError(
                    f"\n\n❌ Configuration Error in WORKFLOW Step -  '{node_name}':\n"
                    f"   Parameter '{{{missing_key}}}' is not defined\n"
                    f"   in the GENERAL or PROFILE settings.\n"
                    f"   \n"
                    f"   Problematic String: \"{data}\""
                )

        if isinstance(data, list):
            return [self._deep_template(node_name, item, context) for item in data]
        if isinstance(data, dict):
            return {k: self._deep_template(node_name, v, context) for k, v in data.items()}
        return data

    @staticmethod
    def _raise_formatting_error(e: ValueError, node_name: str, original_template: str, current_state: str = None):
        """
        Translates internal formatting errors into helpful LiteBuild configuration messages.
        """
        msg = str(e)

        # Base header
        report = f"\n\n❌ Syntax Error in WORKFLOW Step '{node_name}':\n"

        # 1. Handle the "Colon" error (Invalid format specifier)
        if "Invalid format specifier" in msg:
            report += (
                f"   The command template contains an invalid placeholder format.\n"
                f"   There is a colon ':' inside a curly brace (e.g., {{aaa:bbb}}).\n\n"
            )

        # 2. Handle Missing/Extra Braces
        elif "Unmatched" in msg:
            report += (
                f"   The command template has unbalanced curly braces.\n"
                f"   Reason: You have a missing closing '}}' or an extra opening '{{'.\n"
            )

        # 3. Handle Missing Keys (if SafeFormatter didn't catch it)
        elif "KeyError" in msg:
            report += (
                f"   The command references a variable that does not exist.\n"
                f"   Error Details: {msg}\n"
            )

        # 4. Fallback for other errors
        else:
            report += f"   LiteBuild could not process the template string.\n   Details: {msg}\n"

        # Show the Context
        report += f"\n   --- Context ---\n"
        report += f"   Original Template (YAML): \n     \"{original_template}\"\n"

        if current_state and current_state != original_template:
            report += f"\n   Processed State (Before Crash): \n     \"{current_state}\"\n"

        report += f"\n   Python Error: {msg}\n"

        raise ValueError(report) from e