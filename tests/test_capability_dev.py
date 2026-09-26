"""Capability-owned LangGraph development configuration."""

import json
from pathlib import Path

import pytest

from bruno.capability_dev import (
    CapabilityConfigError,
    available_capabilities,
    capability_config_path,
    langgraph_dev_arguments,
)


def test_financial_capability_owns_its_langgraph_config() -> None:
    config = capability_config_path("financial-agent")
    payload = json.loads(config.read_text())

    assert config.parent.name == "financial-agent"
    assert available_capabilities() == ("financial-agent",)
    assert set(payload["graphs"]) == {
        "finance_agent",
        "daily_budget_monitor",
        "expense_monitor",
    }
    assert payload["env"] == "../../.env"


def test_dev_arguments_select_config_and_forward_server_options(tmp_path: Path) -> None:
    root = tmp_path
    config = root / "Capabilities" / "financial-agent" / "langgraph.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}")

    arguments = langgraph_dev_arguments(
        "financial-agent",
        ("--port", "2025", "--no-browser"),
        root=root,
    )

    assert arguments == (
        "dev",
        "--config",
        str(config),
        "--port",
        "2025",
        "--no-browser",
    )


@pytest.mark.parametrize(
    "value",
    ("../finance", "Finance Agent", "/tmp/finance"),
)
def test_capability_name_rejects_path_traversal(
    value: str, tmp_path: Path
) -> None:
    with pytest.raises(CapabilityConfigError, match="Invalid capability name"):
        capability_config_path(value, root=tmp_path)
