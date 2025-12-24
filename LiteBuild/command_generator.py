# command_generator.py

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

        # 1. Validate that the {OUTPUT} or placeholder is always present.
        if "{OUTPUT}" not in command_template:
            error_msg = (f"❌ Configuration Error in WORKFLOW Step '{node_name}':\n"
                         f"   The COMMAND template for rule '{rule_name}' is missing the required "
                         f"{{OUTPUT}} placeholder.\n\n"
                         f"💡 To fix this, add {{OUTPUT}} to the command to specify where the "
                         f"output file should be written.")
            raise ValueError(error_msg)

        # 2. Validate that an input placeholder is used
        has_inputs_placeholder = (
                "{INPUTS}" in command_template or re.search(r'{INPUTS\[\d+\]}', command_template))

        if not has_inputs_placeholder and "{POSITIONAL_FILENAMES}" not in command_template:
            error_msg = (f"❌ Configuration Warning in WORKFLOW Step '{node_name}':\n"
                         f"  No inputs "
                         f"placeholder like INPUTS, INPUTS[0], or POSITIONAL_FILENAMES was found "
                         f"in the COMMAND template for rule '{rule_name}'.\n\n"
                         f"   To fix this, add an INPUTS  to the COMMAND.")
            print(error_msg)

        if final_params and "{PARAMETERS}" not in command_template:
            error_msg = (f"❌ Configuration Error in WORKFLOW Step '{node_name}':\n"
                         f"   Parameters were defined for the rule '{rule_name}', but the COMMAND "
                         f"template is missing the '{{PARAMETERS}}' placeholder.\n\n"
                         f"💡 The following parameters would be ignored: "
                         f"{list(final_params.keys())}\n\n"
                         f"   To fix this, add {{PARAMETERS}} to the COMMAND for this rule.")
            raise ValueError(error_msg)

        if positional_filenames_templates and "{POSITIONAL_FILENAMES}" not in command_template:
            error_msg = (f"❌ Configuration Error in WORKFLOW Step '{node_name}':\n"
                         f"   'POSITIONAL_FILENAMES' were defined, but the COMMAND template "
                         f"is missing the '{{POSITIONAL_FILENAMES}}' placeholder.\n\n"
                         f"💡 To fix this, add {{POSITIONAL_FILENAMES}} to the COMMAND for this "
                         f"rule.")
            raise ValueError(error_msg)

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

        merged = {**general_params, **workflow_params, **profile_params, }
        return self._deep_template(node_name, merged, context)

        # --- THIS METHOD IS MODIFIED TO HANDLE ALL CASES CORRECTLY ---

    def _resolve_all_inputs(
            self, node_name: str, node_data: dict, context: dict, resolved_outputs: dict,
            profile_config: dict
    ) -> List[str]:
        all_inputs = []
        input_templates = node_data.get("INPUTS", [])
        if isinstance(input_templates, str):
            input_templates = [input_templates]  # Normalize to a list

        requires_list = node_data.get("REQUIRES", [])

        for tmpl in input_templates:
            # Case 1: Handle {REQUIRES[n]} syntax using regex (restored logic)
            match = re.fullmatch(r"{REQUIRES\[(\d+)\]}", tmpl)
            if match:
                dep_index = int(match.group(1))
                if dep_index >= len(requires_list):
                    raise ValueError(
                        f"Error in '{node_name}': REQUIRES index [{dep_index}] is out of range."
                    )
                dep_name = requires_list[dep_index]
                all_inputs.append(resolved_outputs[dep_name])
                continue

            # Case 2: Handle direct list substitution like {INPUT_FILES}
            if tmpl == "{INPUT_FILES}":
                # Use extend to flatten the list of files into the inputs
                all_inputs.extend(context.get("INPUT_FILES", []))
                continue

            # Case 3: Fallback for all other templates
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
        # NOTE: This combines the hybrid logic from a few steps ago with the restored regex logic.
        template = rule_data["COMMAND"]

        # --- HYBRID LOGIC FOR INPUT FORMATTING ---
        if "{INPUTS}" in template and not re.search(r'{INPUTS\[\d+\]}', template):
            # Use the new, safe formatting system
            inputs_str = self._format_inputs_string(rule_data, inputs)
            params_str = self._format_shell_params(
                params, rule_data.get("DASH", "-"), rule_data.get("UNQUOTED_PARAMS", [])
                )
            positional_filenames_str = ""  # Not used in this mode
        else:
            # Use the legacy, advanced system
            unquoted_positionals = rule_data.get("UNQUOTED_POSITIONALS", False)
            positional_filenames_str = " ".join(
                [f for f in positional_filenames] if unquoted_positionals else [shlex.quote(p) for p
                                                                                in
                                                                                positional_filenames]
                )
            params_str = self._format_shell_params(
                params, rule_data.get("DASH", "-"), rule_data.get("UNQUOTED_PARAMS", [])
                )
            inputs_str = " ".join([shlex.quote(p) for p in inputs])

        template_context = {
            **context, 'OUTPUT': output, 'INPUTS': inputs_str, 'PARAMS': params,
            'PARAMETERS': params_str, 'POSITIONAL_FILENAMES': positional_filenames_str
        }

        # --- RESTORED REGEX FOR {INPUTS[n]} ---
        def resolve_input_index(match: re.Match) -> str:
            input_index = int(match.group(1))
            if input_index >= len(inputs):
                raise ValueError(
                    f"Error in '{node_name}': INPUTS index [{input_index}] is out of range."
                )
            return shlex.quote(inputs[input_index])

        final_template = re.sub(r'{INPUTS\[(\d+)\]}', resolve_input_index, template)
        # --- END OF RESTORED LOGIC ---

        resolved_command = final_template.format_map(
            self.SafeFormatter(template_context)
            ).strip().replace('  ', ' ')

        try:
            shlex.split(resolved_command)
        except ValueError as e:
            raise ValueError(
                f"\n❌ Configuration Error in WORKFLOW Step '{node_name}' COMMAND:\n"
                f"   - Error: {e}\n   - Generated Command: \n{resolved_command}\n"
                f"   - Template: \n{template}\n"
            )
        return resolved_command

        # (Add the _format_inputs_string helper method here if it was removed)

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
                    "RULE must define 'INPUT_SWITCH_NAME' when using 'switch' INPUT_STYLE."
                    )
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
            for i in range(5):  # Iterate a max of 5 times to resolve nested templates
                prev_string = templated_string
                try:
                    # try/except block for robust error handling ---
                    templated_string = templated_string.format_map(safe_context)
                except Exception as e:
                    # This catches errors during the safe, iterative replacement.
                    missing_key = e.args[0]
                    raise ValueError(
                        f"❌ Config file error in section '{node_name}':\n    {missing_key}\n"
                        f"  - Original: \"{data}\"\n"
                        f"  - Before Error: \"{prev_string}\""
                    )
            try:
                return templated_string.format_map(context)
            except Exception as e:
                missing_key = e.args[0]
                raise ValueError(
                    f"❌ Error in '{node_name}':  {missing_key} in string: \""
                    f"{data}\""
                )

        if isinstance(data, list):
            return [self._deep_template(node_name, item, context) for item in data]
        if isinstance(data, dict):
            return {k: self._deep_template(node_name, v, context) for k, v in data.items()}
        return data
