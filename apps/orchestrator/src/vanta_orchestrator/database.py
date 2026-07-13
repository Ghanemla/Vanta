from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(
        self,
        path: Path,
        migrations_dir: Path,
        starter_presets: Path,
        *,
        seed_demo_data: bool = False,
    ):
        self.path = path
        self.migrations_dir = migrations_dir
        self.starter_presets = starter_presets
        self.seed_demo_data = seed_demo_data

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for migration in sorted(self.migrations_dir.glob("*.sql")):
                if migration.stem in applied:
                    continue
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (migration.stem, utc_now()),
                )
        self.seed_presets()
        if self.seed_demo_data:
            self.seed_fixtures()

    def seed_presets(self) -> None:
        source = json.loads(self.starter_presets.read_text(encoding="utf-8"))
        now = utc_now()
        with self.connect() as connection:
            for category, items in source.items():
                for item in items:
                    connection.execute(
                        """INSERT OR IGNORE INTO presets
                        (id, category, name, prompt, tags, origin, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'builtin', ?, ?)""",
                        (
                            item["id"],
                            category,
                            item["name"],
                            item["prompt"],
                            json.dumps(item["tags"]),
                            now,
                            now,
                        ),
                    )

    def seed_fixtures(self) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO characters
                (id, name, identity_description, default_model_profile, reference_assets, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    "character-sophia",
                    "Sophia",
                    "Original adult character, age 24, dark shoulder-length hair, expressive brown eyes, editorial presence.",
                    "photoreal_balanced",
                    json.dumps(["front portrait", "three-quarter portrait", "neutral expression"]),
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO app_settings(key, value, updated_at) VALUES ('default_mode', 'simple', ?)",
                (now,),
            )
            connection.execute(
                "INSERT OR IGNORE INTO app_settings(key, value, updated_at) VALUES ('engine_autostart', 'true', ?)",
                (now,),
            )
            fixture_meta = json.dumps(
                {"recipe": "Y2K Bedroom Study", "steps": 28, "guidance": 5.5, "disclosure": True}
            )
            for index, seed in enumerate((483201, 483202, 483203), start=1):
                connection.execute(
                    """INSERT OR IGNORE INTO generations
                    (id, character_id, image_path, prompt, seed, model_alias, width, height, metadata, created_at)
                    VALUES (?, 'character-sophia', ?, ?, ?, 'photoreal_balanced', 832, 1216, ?, ?)""",
                    (
                        f"fixture-generation-{index}",
                        f"fixture://y2k-bedroom-{index}",
                        "Original adult character in a moody Y2K bedroom, editorial portrait, blue ambient light",
                        seed,
                        fixture_meta,
                        now,
                    ),
                )

    def query_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def query_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        with self.connect() as connection:
            connection.execute(sql, parameters)
