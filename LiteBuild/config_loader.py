from pathlib import Path

from YMLEditor.yaml_reader import ConfigLoader

from schema import LiteBuildValidator, BUILD_SCHEMA


# Import LiteBuild's specific schema and custom validator


def load_litebuild_config(config_filepath: str | Path) -> dict:
    """
    Loads, validates, and normalizes the LiteBuild configuration file.

    This function is a project-specific wrapper around the generic ConfigLoader.
    It automatically injects LiteBuild's custom schema and validator class,
    and requests a normalized output with default values filled in.

    Args:
        config_filepath (str | Path): The path to the LiteBuild YAML config file.

    Returns:
        dict: The validated and normalized configuration dictionary.

    Raises:
        FileNotFoundError: If the config file does not exist.
        ValueError: If the config file has validation errors.
    """
    try:
        # 1. Instantiate the generic loader with LiteBuild's specific components.
        loader = ConfigLoader(BUILD_SCHEMA, validator_class=LiteBuildValidator)

        # 2. Read the file, requesting normalization.
        #    The powerful error formatting is  handled automatically by the loader.
        normalized_config = loader.read(
            config_file=Path(config_filepath), normalize=True
        )
        print(f"CONFIG:\n{normalized_config}")
        return normalized_config

    except (FileNotFoundError, ValueError) as e:
        # Re-raise the already well-formatted exceptions from the generic loader
        # for the higher-level application to handle.
        raise e
