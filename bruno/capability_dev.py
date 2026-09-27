"""Launch one capability's LangGraph development server."""

import os
import re
from collections.abc import Sequence
from pathlib import Path

_CAPABILITY_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


class CapabilityConfigError(ValueError):
    """A capability name or LangGraph configuration is invalid."""


def repository_root() -> Path:
    """Return the Bruno repository root independently of the current directory."""

    return Path(__file__).resolve().parents[1]


def available_capabilities(*, root: Path | None = None) -> tuple[str, ...]:
    """List capability folders that own a LangGraph configuration."""

    capabilities_root = (root or repository_root()) / "Capabilities"
    if not capabilities_root.is_dir():
        return ()
    return tuple(
        path.name
        for path in sorted(capabilities_root.iterdir(), key=lambda item: item.name)
        if path.is_dir() and (path / "langgraph.json").is_file()
    )


def capability_config_path(
    capability: str,
    *,
    root: Path | None = None,
) -> Path:
    """Resolve one allow-listed capability config without accepting path traversal."""

    if _CAPABILITY_NAME.fullmatch(capability) is None:
        raise CapabilityConfigError(f"Invalid capability name: {capability!r}")
    resolved_root = root or repository_root()
    config = resolved_root / "Capabilities" / capability / "langgraph.json"
    if not config.is_file():
        available = ", ".join(available_capabilities(root=resolved_root)) or "none"
        raise CapabilityConfigError(
            f"Capability {capability!r} has no langgraph.json; available: {available}"
        )
    return config


def langgraph_dev_arguments(
    capability: str,
    extra_args: Sequence[str] = (),
    *,
    root: Path | None = None,
) -> tuple[str, ...]:
    """Build CLI arguments while keeping config ownership with the capability."""

    if any(arg == "--config" or arg.startswith("--config=") for arg in extra_args):
        raise CapabilityConfigError("--config is selected by the capability command")
    config = capability_config_path(capability, root=root)
    return ("dev", "--config", str(config), *extra_args)


def run_capability_dev(capability: str, extra_args: Sequence[str] = ()) -> None:
    """Start LangGraph's in-memory development server for one capability."""

    config = capability_config_path(capability)
    previous_directory = Path.cwd()
    try:
        from langgraph_cli.cli import cli
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Install Bruno with the dev extra before starting Studio"
        ) from exc
    try:
        os.chdir(config.parent)
        cli.main(
            args=list(langgraph_dev_arguments(capability, extra_args)),
            prog_name="langgraph",
        )
    finally:
        os.chdir(previous_directory)
