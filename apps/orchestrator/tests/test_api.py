def test_health_is_loopback_only(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["host"] == "127.0.0.1"
    assert response.json()["privacy"] == "local-only"


def test_character_crud_archives_without_deleting(client):
    payload = {"name": "Mara", "identity_description": "Original adult character, age 29"}
    created = client.post("/api/characters", json=payload)
    assert created.status_code == 201
    character_id = created.json()["id"]
    updated = client.put(f"/api/characters/{character_id}", json={**payload, "name": "Mara Vale"})
    assert updated.json()["name"] == "Mara Vale"
    assert client.delete(f"/api/characters/{character_id}").status_code == 204
    assert all(item["id"] != character_id for item in client.get("/api/characters").json())
    assert any(
        item["id"] == character_id
        for item in client.get("/api/characters?include_archived=true").json()
    )


def test_preset_crud_and_builtin_copy_rule(client):
    preset = {
        "category": "camera",
        "name": "Low Angle",
        "prompt": "low camera angle",
        "tags": ["editorial"],
    }
    created = client.post("/api/presets", json=preset)
    assert created.status_code == 201
    preset_id = created.json()["id"]
    updated = client.put(f"/api/presets/{preset_id}", json={**preset, "favorite": True})
    assert updated.json()["favorite"] is True
    duplicated = client.post(f"/api/presets/{preset_id}/duplicate")
    assert duplicated.status_code == 201
    assert duplicated.json()["source_preset_id"] == preset_id
    builtin = next(
        item for item in client.get("/api/presets").json() if item["origin"] == "builtin"
    )
    copied = client.put(
        f"/api/presets/{builtin['id']}",
        json={
            "category": builtin["category"],
            "name": builtin["name"],
            "prompt": builtin["prompt"],
            "tags": builtin["tags"],
        },
    )
    assert copied.json()["origin"] == "user"
    assert copied.json()["source_preset_id"] == builtin["id"]
    assert client.delete(f"/api/presets/{preset_id}").status_code == 204


def test_recipe_and_real_generation_queue_are_not_seeded_with_fixtures(client):
    recipe = client.post(
        "/api/recipes", json={"name": "Bedroom Study", "freeform_prompt": "editorial portrait"}
    )
    assert recipe.status_code == 201
    assert client.get("/api/gallery").json() == []
    job = client.post(
        "/api/generations",
        json={
            "direction": "an original editorial portrait",
            "model_alias": "photoreal_balanced",
            "seed": 7,
            "width": 832,
            "height": 1216,
            "steps": 1,
            "guidance": 1,
        },
    )
    assert job.status_code == 202
    assert job.json()["status"] in {"queued", "checking_engine", "failed"}


def test_engine_manifest_and_model_pack_services(client):
    components = client.get("/api/engine/components").json()
    identity = next(item for item in components if item["id"] == "identity-lock")
    assert identity["state"] == "unsupported"
    assert client.post("/api/engine/components/identity-lock/repair").status_code == 409
    packs = client.get("/api/engine/model-packs").json()
    balanced = next(item for item in packs["packs"] if item["alias"] == "photoreal_balanced")
    assert balanced["installed"] is False
    assert balanced["verified"] is False
    assert balanced["is_default"] is False
