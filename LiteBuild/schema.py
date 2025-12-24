# schema.py

import re

from cerberus import Validator


# --- Custom Validator with a more advanced rule ---
class LiteBuildValidator(Validator):
    """
    A custom Cerberus Validator that enforces LiteBuild's specific
    naming conventions for Workflow Steps and Rule Names.
    """

    def _check_with_pascalcase(self, field, value):
        """
        Validates that a string is in PascalCase (e.g., CombineLayers)
        and contains no underscores.
        """
        if not re.fullmatch(r'^[A-Z][a-zA-Z0-9]*$', value):
            self._error(
                field, "must be in MixedCase (e.g., CombineLayers) "
                       "It must start in uppercase and cannot contain underscores."
            )

    def _check_with_snakecase(self, field, value):
        """
        Validates that a string is in snake_case (e.g., create_dem)
        and contains no uppercase letters.
        """
        if not re.fullmatch(r'^[a-z0-9_]+$', value):
            self._error(
                field, "must be in lowercase (e.g., create_dem) "
                       "and cannot contain uppercase letters."
            )

    def _check_with_param_rule_names(self, field, value):
        """
        Validates that the top-level keys (Rule Names) within a PARAMETERS
        block are in snake_case.
        """
        for rule_name in value.keys():
            if not re.fullmatch(r'^[a-z0-9_]+$', rule_name):
                # Cerberus doesn't easily support errors on keys, so we
                # attach the error to the parent dictionary field.
                self._error(
                    field, f"Rule Name '{rule_name}' must be in lowercase "
                           f"(e.g., create_dem) and cannot contain uppercase letters."
                )


parameter_set_schema = {
    'type': 'dict', 'valuesrules': {
        'type': ['string', 'number', 'boolean', 'list'], 'schema': {'type': 'string'},
        'nullable': True
    }
}

templated_rule_schema = {
    'NAME': {'type': 'string', 'required': True, 'check_with': 'snakecase'},
    'DASH': {'type': 'string', 'default': '-'}, 'COMMAND': {'type': 'string', 'required': True},
    'UNQUOTED_PARAMS': {'type': 'list', 'schema': {'type': 'string'}, 'default': []},
    'UNQUOTED_POSITIONALS': {'type': 'boolean', 'default': False},

    # --- ADDING  DIRECTIVES FOR THE SAFE INPUT SYSTEM ---
    'INPUT_STYLE': {
        'type': 'string', 'allowed': ['positional', 'switch'], 'default': 'positional'
    }, 'INPUT_SWITCH_NAME': {'type': 'string'},  # e.g., --file, -i
    'INPUT_QUOTED': {'type': 'boolean', 'default': True}
}

workflow_step_schema = {
    'RULE': {'type': 'dict', 'required': True, 'schema': templated_rule_schema},
    'OUTPUT': {'type': 'string', 'required': True}, 'ENABLED': {'type': 'boolean', 'default': True},
    'REQUIRES': {
        'type': 'list', 'schema': {'type': 'string', 'check_with': 'pascalcase'}, 'default': []
    }, 'INPUTS': {
        'type': ['list', 'string'], 'schema': {'type': 'string'}, 'default': []
    }, 'POSITIONAL_FILENAMES': {
        'type': ['list', 'string'], 'schema': {'type': 'string'}, 'default': []
    }, 'PARAMETERS': {**parameter_set_schema, 'default': {}}
}

BUILD_SCHEMA = {
    'config_type': {'type': 'string', 'required': True, 'allowed': ["LiteBuild"]},
    'DEFAULT_WORKFLOW_STEP': {
        'type': 'string', 'required': False,  # This is an optional key
        'check_with': 'pascalcase'  # Enforce naming consistency with WORKFLOW keys
    }, 'GENERAL': {
        'type': 'dict', 'schema': {
            'PREVIEW': {'type': 'string', 'default': ""}, 'PROJECT_NAME': {'type': 'string'},
            # Apply the  smart validator to the whole PARAMETERS block
            'PARAMETERS': {
                'type': 'dict', 'valuesrules': parameter_set_schema,
                'check_with': 'param_rule_names', 'default': {}
            }
        }, 'allow_unknown': True, 'default': {}
    }, 'PROFILES': {
        'type': 'dict', 'valuesrules': {
            'type': 'dict', 'schema': {
                'INPUT_DIRECTORY': {'type': 'string', 'required': False},
                'INPUT_FILES': {'type': 'list', 'schema': {'type': 'string'}, 'required': False},
                # Also apply the  validator here
                'PARAMETERS': {
                    'type': 'dict', 'valuesrules': parameter_set_schema,
                    'check_with': 'param_rule_names', 'default': {}
                }
            }, 'allow_unknown': True
        }, 'default': {}
    }, # --- Added validation for PROFILE_GROUPS ---
    'PROFILE_GROUPS': {
        'type': 'dict', 'required': False,  # A group is optional
        'valuesrules': {
            # Each value under PROFILE_GROUPS must be a list of strings
            'type': 'list', 'schema': {'type': 'string', 'empty': False}
        }, 'default': {}
    }, 'WORKFLOW': {
        'type': 'dict', 'required': True,
        'keysrules': {'type': 'string', 'check_with': 'pascalcase'},
        'valuesrules': {'type': 'dict', 'schema': workflow_step_schema}
    }, 'OVERVIEW': {
        'type': 'string', 'required': False,
    }, 'README': {
        'type': 'string', 'required': False,
    }
}
