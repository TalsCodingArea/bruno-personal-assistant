"""Command-line entrypoint for Bruno development workflows."""

import argparse
from collections.abc import Sequence

from bruno.capability_dev import CapabilityConfigError, run_capability_dev


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bruno")
    commands = parser.add_subparsers(dest="command", required=True)
    capability_dev = commands.add_parser(
        "capability-dev",
        help="start the LangGraph development server for one capability",
    )
    capability_dev.add_argument(
        "capability",
        help="folder name under Capabilities, for example financial-agent",
    )
    capability_dev.add_argument(
        "langgraph_args",
        nargs=argparse.REMAINDER,
        help="additional langgraph dev arguments, such as --port 2025",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "capability-dev":
        try:
            run_capability_dev(
                arguments.capability,
                arguments.langgraph_args,
            )
        except (CapabilityConfigError, RuntimeError) as exc:
            parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
