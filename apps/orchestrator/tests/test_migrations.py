import sqlite3


def test_migrations_create_all_required_tables(client):
    assert client.get("/api/health").status_code == 200
    # Exercise repositories backed by every primary Milestone table through the API,
    # then inspect the fixture database location from settings.
    response = client.get("/api/settings")
    database_path = response.json()["paths"]["database"]
    connection = sqlite3.connect(database_path)
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    required = {
        "characters",
        "presets",
        "recipes",
        "recipe_items",
        "engine_components",
        "model_packs",
        "generation_jobs",
        "generations",
        "app_settings",
        "character_references",
        "lora_packs",
        "character_loras",
        "pose_assets",
        "schema_migrations",
    }
    assert required.issubset(tables)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(generation_jobs)")}
    assert {"progress", "prompt_id", "started_at", "completed_at"}.issubset(columns)
    character_columns = {row[1] for row in connection.execute("PRAGMA table_info(characters)")}
    assert {"hair", "eyes", "style_notes", "default_negative_prompt"}.issubset(character_columns)
    pose_columns = {row[1] for row in connection.execute("PRAGMA table_info(pose_assets)")}
    assert {"status", "progress", "error_message"}.issubset(pose_columns)
    connection.close()
