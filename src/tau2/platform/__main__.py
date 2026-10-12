"""python -m tau2.platform — поднять сервис сессий."""

import argparse
from pathlib import Path

import uvicorn

from tau2.config import PLATFORM_DATA_DIR, PLATFORM_PORT
from tau2.platform.service import PlatformSettings, build_app


def parse_args(argv: list[str]) -> tuple[PlatformSettings, str, int]:
    parser = argparse.ArgumentParser(prog="tau2.platform")
    parser.add_argument("--domain", default="banking_ru")
    parser.add_argument("--split", default="base")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=PLATFORM_PORT)
    parser.add_argument("--max-calls", type=int, default=60)
    parser.add_argument("--max-errors", type=int, default=5)
    parser.add_argument("--mcp-layout", choices=["single", "systems"])
    parser.add_argument("--reveal-checks", action="store_true")
    parser.add_argument("--results-dir", type=Path, default=PLATFORM_DATA_DIR)
    args = parser.parse_args(argv)
    settings = PlatformSettings(
        domain=args.domain,
        split=args.split,
        max_calls=args.max_calls,
        max_errors=args.max_errors,
        mcp_layout=args.mcp_layout,
        reveal_checks=args.reveal_checks,
        results_dir=args.results_dir,
    )
    return settings, args.host, args.port


def main(argv: list[str] | None = None) -> None:
    import sys

    settings, host, port = parse_args(sys.argv[1:] if argv is None else argv)
    uvicorn.run(build_app(settings), host=host, port=port)


if __name__ == "__main__":
    main()
