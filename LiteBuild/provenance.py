"""Shared provenance analysis for LiteBuild.

This module contains the provenance mechanics shared by reporting and
provenance validation.  It deliberately does not decide whether missing
provenance is a warning or a build failure; that policy belongs to the caller.

Core rules
----------
* A source file is an input to the resolved workflow that is not produced by
  another step in that same resolved workflow.
* Provenance entries classify sources as either INPUT (data) or CONFIG.
  The classification is supplied by the project author; LiteBuild does not
  infer it.
* Exact provenance paths take precedence over wildcard patterns.
* If no exact match exists, exactly one wildcard match is accepted.
* Multiple wildcard matches are ambiguous.
* Multiple exact matches are ambiguous.
* Wildcards use Unix-style glob syntax supported by Python ``fnmatch``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from glob import has_magic
import os
from typing import Callable, Iterable, Mapping, Sequence


class ProvenanceError(ValueError):
    """Base class for provenance errors."""


class ProvenanceConfigError(ProvenanceError):
    """Raised when the PROVENANCE section itself is invalid."""


@dataclass(frozen=True)
class ProvenanceEntry:
    """One resolved PROVENANCE declaration."""

    name: str
    kind: str  # "input" | "config"
    pattern: str
    description: str
    attribution: str | None
    citation: str | None
    is_exact: bool


@dataclass(frozen=True)
class ProvenanceAmbiguity:
    """A source path matched more than one equally valid provenance entry."""

    path: str
    matches: tuple[ProvenanceEntry, ...]


@dataclass(frozen=True)
class SourceProvenance:
    """Resolved provenance for one workflow source file."""

    path: str
    entry: ProvenanceEntry | None


@dataclass
class ProvenanceAnalysis:
    """Complete provenance analysis for one resolved workflow."""

    sources: list[SourceProvenance] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    ambiguous: list[ProvenanceAmbiguity] = field(default_factory=list)

    @property
    def data_sources(self) -> list[SourceProvenance]:
        """Matched data sources."""
        return [
            source
            for source in self.sources
            if source.entry is not None and source.entry.kind == "input"
        ]

    @property
    def config_sources(self) -> list[SourceProvenance]:
        """Matched configuration sources."""
        return [
            source
            for source in self.sources
            if source.entry is not None and source.entry.kind == "config"
        ]

    @property
    def is_complete(self) -> bool:
        """True when every source has one unambiguous provenance match."""
        return not self.missing and not self.ambiguous


def normalize_path(path: str) -> str:
    """Normalize a path for source/output comparison and provenance matching.

    The result remains relative if the input is relative.  This intentionally
    does not call ``Path.resolve()`` because provenance patterns can describe
    files that do not yet exist and because resolution would make matching
    depend on the current working directory.
    """
    if not isinstance(path, str) or not path.strip():
        raise ProvenanceConfigError("Provenance paths must be non-empty strings.")

    normalized = os.path.normpath(path.strip())

    # Use one separator representation for deterministic comparison/reporting.
    if os.sep != "/":
        normalized = normalized.replace(os.sep, "/")
    if os.altsep:
        normalized = normalized.replace(os.altsep, "/")

    return normalized


def is_exact_pattern(pattern: str) -> bool:
    """Return True when *pattern* contains no Unix-style glob metacharacters."""
    return not has_magic(pattern)


def get_source_files(command_map: Mapping[str, Mapping]) -> list[str]:
    """Return workflow inputs that are not outputs of another workflow step.

    ``command_map`` is expected to contain planner-resolved entries with
    ``output`` and ``input_files`` fields.

    Results are normalized, de-duplicated, and sorted by pathname.
    """
    produced: set[str] = set()

    for command in command_map.values():
        output = command.get("output")
        if output:
            produced.add(normalize_path(str(output)))

    sources: set[str] = set()

    for command in command_map.values():
        for input_file in command.get("input_files") or ():
            normalized = normalize_path(str(input_file))
            if normalized not in produced:
                sources.add(normalized)

    return sorted(sources)


def _default_resolver(value: str, variables: Mapping[str, object]) -> str:
    """Resolve ``{NAME}`` expressions using a mapping.

    LiteBuild callers with an existing variable resolver can pass that resolver
    to :func:`resolve_provenance` or :func:`analyze_provenance` instead.
    """
    try:
        return value.format_map(variables)
    except KeyError as exc:
        missing = exc.args[0]
        raise ProvenanceConfigError(
            f"Unknown variable {{{missing}}} in provenance value: {value}"
        ) from exc
    except (ValueError, AttributeError) as exc:
        raise ProvenanceConfigError(
            f"Unable to resolve provenance value: {value!r}"
        ) from exc


def resolve_provenance(
    provenance_config: Mapping[str, Mapping] | None,
    variables: Mapping[str, object] | None = None,
    *,
    resolver: Callable[[str, Mapping[str, object]], str] | None = None,
) -> list[ProvenanceEntry]:
    """Validate and resolve the top-level PROVENANCE section.

    The input has already passed LiteBuild schema validation. This function
    resolves provenance paths and carries reporting metadata forward.

    ``resolver`` allows LiteBuild to reuse its normal variable-resolution
    implementation.  When omitted, Python ``str.format_map`` is used.
    """
    if not provenance_config:
        return []

    variables = variables or {}
    resolver = resolver or _default_resolver

    entries: list[ProvenanceEntry] = []

    for name, definition in provenance_config.items():
        selector = "INPUT" if "INPUT" in definition else "CONFIG"
        kind = selector.lower()

        raw_pattern = definition[selector]
        description = definition["DESCRIPTION"]
        attribution = definition.get("ATTRIBUTION")
        citation = definition.get("CITATION")

        resolved_pattern = resolver(raw_pattern, variables)
        pattern = normalize_path(resolved_pattern)

        entries.append(
            ProvenanceEntry(
                name=name,
                kind=kind,
                pattern=pattern,
                description=description.strip(),
                attribution=attribution.strip() if attribution else None,
                citation=citation.strip() if citation else None,
                is_exact=is_exact_pattern(pattern),
            )
        )

    return entries


def _matching_entries(
    source_file: str,
    entries: Sequence[ProvenanceEntry],
) -> tuple[list[ProvenanceEntry], list[ProvenanceEntry]]:
    """Return (exact_matches, wildcard_matches) for one normalized source path."""
    source = normalize_path(source_file)
    exact: list[ProvenanceEntry] = []
    wildcard: list[ProvenanceEntry] = []

    for entry in entries:
        if entry.is_exact:
            if source == entry.pattern:
                exact.append(entry)
        elif fnmatchcase(source, entry.pattern):
            wildcard.append(entry)

    return exact, wildcard


def match_provenance(
    source_file: str,
    entries: Sequence[ProvenanceEntry],
) -> ProvenanceEntry | None | ProvenanceAmbiguity:
    """Match one source file using LiteBuild provenance precedence rules.

    Returns:
        ProvenanceEntry:
            One unambiguous match.
        None:
            No provenance declaration matched.
        ProvenanceAmbiguity:
            Multiple exact matches, or multiple wildcard matches when no exact
            match exists.
    """
    source = normalize_path(source_file)
    exact, wildcard = _matching_entries(source, entries)

    if len(exact) == 1:
        return exact[0]

    if len(exact) > 1:
        return ProvenanceAmbiguity(source, tuple(exact))

    if len(wildcard) == 1:
        return wildcard[0]

    if len(wildcard) > 1:
        return ProvenanceAmbiguity(source, tuple(wildcard))

    return None


def analyze_provenance(
    command_map: Mapping[str, Mapping],
    provenance_config: Mapping[str, Mapping] | None,
    variables: Mapping[str, object] | None = None,
    *,
    resolver: Callable[[str, Mapping[str, object]], str] | None = None,
) -> ProvenanceAnalysis:
    """Analyze provenance for a resolved LiteBuild workflow.

    This is the primary shared entry point for both BuildReporter and
    provenance validation.
    """
    source_files = get_source_files(command_map)
    entries = resolve_provenance(
        provenance_config,
        variables,
        resolver=resolver,
    )

    analysis = ProvenanceAnalysis()

    for source_file in source_files:
        result = match_provenance(source_file, entries)

        if isinstance(result, ProvenanceAmbiguity):
            analysis.ambiguous.append(result)
            # An ambiguous source has no authoritative matched entry.
            analysis.sources.append(SourceProvenance(source_file, None))
            continue

        if result is None:
            analysis.missing.append(source_file)
            analysis.sources.append(SourceProvenance(source_file, None))
            continue

        analysis.sources.append(SourceProvenance(source_file, result))

    return analysis


def group_sources_by_entry(
    sources: Iterable[SourceProvenance],
) -> list[tuple[ProvenanceEntry, list[str]]]:
    """Group matched sources by provenance entry for report generation.

    Groups are ordered by provenance logical name.  Paths within each group are
    sorted by pathname.
    """
    grouped: dict[ProvenanceEntry, list[str]] = {}

    for source in sources:
        if source.entry is None:
            continue
        grouped.setdefault(source.entry, []).append(source.path)

    return [
        (entry, sorted(paths))
        for entry, paths in sorted(
            grouped.items(),
            key=lambda item: item[0].name,
        )
    ]


__all__ = [
    "ProvenanceAnalysis",
    "ProvenanceAmbiguity",
    "ProvenanceConfigError",
    "ProvenanceEntry",
    "ProvenanceError",
    "SourceProvenance",
    "analyze_provenance",
    "get_source_files",
    "group_sources_by_entry",
    "is_exact_pattern",
    "match_provenance",
    "normalize_path",
    "resolve_provenance",
]
