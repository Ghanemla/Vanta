from __future__ import annotations

import argparse
import logging
import socket
import sys

import uvicorn

from .app import create_app
from .config import Settings
from .database import Database


def configure_logging(settings: Settings) -> None:
    settings.ensure_runtime_paths()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(
                (settings.logs_dir or settings.data_dir / "logs") / "orchestrator.log",
                encoding="utf-8",
            )
        ],
        force=True,
    )


def self_test(settings: Settings) -> int:
    """Validate that the packaged runtime is self-contained and usable."""
    try:
        configure_logging(settings)
        for resource in (
            settings.migrations_dir,
            settings.starter_presets_path,
            settings.engine_manifest_dir,
        ):
            if not resource.exists():
                raise RuntimeError(f"Required bundled resource is missing: {resource.name}")
        database = Database(
            settings.database_path, settings.migrations_dir, settings.starter_presets_path
        )
        database.migrate()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
        create_app(settings)
        logging.getLogger("vanta.orchestrator").info("self-test completed")
        print("Vanta orchestrator self-test passed")
        return 0
    except Exception as error:
        print(f"Vanta orchestrator self-test failed: {error}", file=sys.stderr)
        return 1


def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings)
    logging.getLogger("vanta.orchestrator").info("starting loopback orchestrator")
    uvicorn.run(
        create_app(settings), host=settings.host, port=settings.port, reload=False, log_config=None
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Vanta local orchestrator")
    parser.add_argument("--self-test", action="store_true", help="verify bundled runtime resources")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.self_test:
        return self_test(settings)
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
