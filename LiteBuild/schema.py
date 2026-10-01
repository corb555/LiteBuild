# schema.py
from enum import StrEnum
import re

from cerberus import Validator


class YAMLSection(StrEnum):
    """Enumeration for different sections in the YAML configuration."""
    PROJECT = "PROJECT"
    GENERAL = "GENERAL"
    PARAMETERS = "PARAMETERS"
    PROFILE_GROUPS = "PROFILE_GROUPS"
    PROFILES = "PROFILES"
    PROVENANCE = "PROVENANCE"
    WORKFLOW = "WORKFLOW"


# --- Custom Validator - ensures Case rules for names ---
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
            self._error(field, "must be in MixedCase (e.g., CombineLayers) "
                               "It must start in uppercase and cannot contain underscores.")

    def _check_with_snakecase(self, field, value):
        """
        Validates that a string is in snake_case (e.g., create_dem)
        and contains no uppercase letters.
        """
        if not re.fullmatch(r'^[a-z0-9_]+$', value):
            self._error(field, "must be in lowercase (e.g., create_dem) "
                               "and cannot contain uppercase letters.")

    def _check_with_profile_name(self, field, value):
        """
        Validate a LiteBuild profile name.

        Profile names must begin with a letter or digit. A leading underscore is
        reserved for LiteBuild-managed state contexts such as ``_default``.
        """
        if not re.fullmatch(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', value):
            self._error(
                field,
                "must begin with a letter or digit and may contain only letters, "
                "digits, '.', '-', and '_'. Names beginning with '_' are reserved "
                "by LiteBuild."
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
                self._error(field, f"Rule Name '{rule_name}' must be in lowercase "
                                   f"(e.g., create_dem) and cannot contain uppercase letters.")


    def _check_with_provenance_source(self, field, value):
        """Validate that a provenance item has exactly one source selector."""
        selectors = [name for name in ("INPUT", "CONFIG") if name in value]

        if len(selectors) != 1:
            self._error(
                field,
                "must contain exactly one of INPUT or CONFIG."
            )


# Parameter Section
parameter_set_schema = {
    'type': 'dict', 'valuesrules': {
        'type': ['string', 'number', 'boolean', 'list'], 'schema': {'type': 'string'},
        'nullable': True
    }
}

# Rule Section
templated_rule_schema = {
    'NAME': {'type': 'string', 'required': True, 'check_with': 'snakecase'},
    'DASH': {'type': 'string', 'default': '-'}, 'COMMAND': {'type': 'string', 'required': True},
    'UNQUOTED_PARAMS': {'type': 'list', 'schema': {'type': 'string'}, 'default': []},
    'UNQUOTED_POSITIONALS': {'type': 'boolean', 'default': False},

    'INPUT_STYLE': {
        'type': 'string', 'allowed': ['positional', 'switch'], 'default': 'positional'
    }, 'INPUT_SWITCH_NAME': {'type': 'string'},  # e.g., --file, -i
    'INPUT_QUOTED': {'type': 'boolean', 'default': True},
    'NO_INPUTS': {'type': 'boolean', 'default': False}
}

# Provenance Section
provenance_entry_schema = {
    'type': 'dict',
    'schema': {
        'INPUT': {
            'type': 'string',
            'empty': False,
            'excludes': 'CONFIG',
        },
        'CONFIG': {
            'type': 'string',
            'empty': False,
            'excludes': 'INPUT',
        },
        'DESCRIPTION': {
            'type': 'string',
            'required': True,
            'empty': False,
        },
        'ATTRIBUTION': {
            'type': 'string',
            'required': False,
            'empty': False,
        },
        'CITATION': {
            'type': 'string',
            'required': False,
            'empty': False,
        },
    },
    'allow_unknown': False,
    'check_with': 'provenance_source',
}


# Workflow Section
workflow_step_schema = {
    'RULE': {'type': 'dict', 'required': True, 'schema': templated_rule_schema},
    'DESCRIPTION': {'type': 'string', 'required': False},
    'OUTPUT': {'type': 'string', 'required': True}, 'ENABLED': {'type': 'boolean', 'default': True},
    'REQUIRES': {
        'type': 'list', 'schema': {'type': 'string', 'check_with': 'pascalcase'}, 'default': []
    }, 'INPUTS': {
        'type': ['list', 'string'], 'schema': {'type': 'string'}, 'default': []
    }, 'POSITIONAL_FILENAMES': {
        'type': ['list', 'string'], 'schema': {'type': 'string'}, 'default': []
    }, 'PARAMETERS': {**parameter_set_schema, 'default': {}}
}

# Top Level Section
BUILD_SCHEMA = {
    'config_type': {'type': 'string', 'required': True, 'allowed': ["LiteBuild"]},
    'DEFAULT_WORKFLOW_STEP': {
        'type': 'string', 'required': False,  # This is an optional key
        'check_with': 'pascalcase'  # Enforce naming consistency with WORKFLOW keys
    }, 'PROJECT': {
        'type': 'dict', 'schema': {
            'OVERVIEW': {'type': 'string', 'required': True},
            'DEFAULT_WORKFLOW_STEP': {'type': 'string', 'required': True},
            'PROVENANCE_CHECK': {
                'type': 'string',
                'allowed': ['off', 'warn', 'fail'],
                'default': 'off',
            },
            'GENERATE_WORKFLOW_MARKDOWN': {
                'type': 'boolean',
                'default': False,
            },
            'GENERATE_PROVENANCE_JSON': {
                'type': 'boolean',
                'default': False,
            },
        }, 'allow_unknown': True, 'default': {}
    }, 'GENERAL': {
        'type': 'dict', 'schema': {
            'INPUT_DIRECTORY': {'type': 'string', 'required': True},
            'PROJECT_NAME': {'type': 'string', 'required': True}
        }, 'allow_unknown': True, 'default': {}
    }, 'PARAMETERS': {
        'type': 'dict', 'valuesrules': parameter_set_schema, 'check_with': 'param_rule_names',
        'default': {},
    },

    'PROVENANCE': {
        'type': 'dict',
        'valuesrules': provenance_entry_schema,
        'default': {},
    },

    'PROFILES': {
        'type': 'dict',
        'keysrules': {'type': 'string', 'check_with': 'profile_name'},
        'valuesrules': {
            'type': 'dict', 'schema': {
                'INPUT_DIRECTORY': {'type': 'string', 'required': False},
                'INPUT_FILES': {'type': 'list', 'schema': {'type': 'string'}, 'required': False},
                'PARAMETERS': {
                    'type': 'dict', 'valuesrules': parameter_set_schema,
                    'check_with': 'param_rule_names', 'default': {}
                }
            }, 'allow_unknown': True
        }, 'default': {}
    }, 'PROFILE_GROUPS': {
        'type': 'dict', 'required': False, 'valuesrules': {
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
