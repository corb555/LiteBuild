from pathlib import Path

from YMLEditor.yaml_reader import ConfigLoader

from schema import BUILD_SCHEMA, LiteBuildValidator


def load_litebuild_config(config_filepath: str | Path) -> dict:
    """Load, validate, and normalize a LiteBuild configuration file.

    This is a project-specific wrapper around the generic ``ConfigLoader``. It
    supplies LiteBuild's schema and validator and returns normalized
    configuration with schema defaults applied.

    The loader performs no direct logging. File and validation failures are
    allowed to propagate to the calling layer, which owns presentation and
    diagnostic logging.

    Args:
        config_filepath: Path to the LiteBuild YAML configuration file.

    Returns:
        The validated and normalized configuration dictionary.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        ValueError: If configuration validation fails.
    """
    loader = ConfigLoader(
        BUILD_SCHEMA,
        validator_class=LiteBuildValidator,
    )

    return loader.read(
        config_file=Path(config_filepath),
        normalize=True,
    )
