"""Brave CLI — command-line entrypoint for pipeline operations.

Phase 1: stub. The run-fixture command will exercise the full pipeline
in Phase 2 when the Nascente service and score engine are built.
"""

import sys


def main() -> None:
    """CLI entrypoint.

    Usage:
        brave run-fixture   # runs a synthetic fixture through the pipeline
    """
    args = sys.argv[1:]
    if not args:
        print("Usage: brave <command>")
        print("Commands:")
        print("  run-fixture   Run a synthetic fixture through the pipeline")
        sys.exit(1)

    command = args[0]
    if command == "run-fixture":
        print("run-fixture: Phase 1 stub — pipeline scaffold verified.")
        print("Full fixture run (Nascente → Rio → score → route → Mar push) ships in Phase 2.")
    else:
        print(f"Unknown command: {command!r}")
        sys.exit(1)


if __name__ == "__main__":
    main()
