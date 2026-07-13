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
    jobs = client.get("/api/jobs")
    assert jobs.status_code == 200
    assert jobs.json()[0]["id"] == job.json()["id"]
    assert "queue_position" in jobs.json()[0]


def test_engine_manifest_and_model_pack_services(client):
    components = client.get("/api/engine/components").json()
    identity = next(item for item in components if item["id"] == "identity-lock")
    assert identity["state"] == "not_installed"
    assert client.post("/api/engine/components/identity-lock/health_check").status_code == 409
    pose_component = next(item for item in components if item["id"] == "pose-control")
    assert pose_component["state"] == "not_installed"
    packs = client.get("/api/engine/model-packs").json()
    balanced = next(item for item in packs["packs"] if item["alias"] == "photoreal_balanced")
    assert balanced["installed"] is False
    assert balanced["verified"] is False
    assert balanced["is_default"] is False
    upscale = next(item for item in packs["packs"] if item["alias"] == "realesrgan_x2plus")
    assert upscale["installed"] is False
    assert "Tiled Execution" in upscale["capabilities"]
    identity_pack = next(
        item for item in packs["packs"] if item["alias"] == "identity_plus_face_sdxl"
    )
    assert identity_pack["installed"] is False
    assert "Identity Lock" in identity_pack["capabilities"]
    assert identity_pack["download"]["bytes"] == 847517512
    assert identity_pack["download"]["clip_vision"]["bytes"] == 2528373448
    pose_pack = next(item for item in packs["packs"] if item["alias"] == "pose_xinsir_sdxl")
    assert pose_pack["installed"] is False
    assert pose_pack["sha256"] == "b8524e557a7df60d081f5d4a0eb109967d107df217943bf88c2d99b9ebcc06c5"


def test_character_reference_and_sdxl_lora_import_flow(client, tmp_path):
    import json

    from PIL import Image

    character = client.post(
        "/api/characters", json={"name": "Mara", "identity_description": "Original adult, age 29"}
    ).json()
    source_image = tmp_path / "owned-reference.png"
    Image.new("RGB", (640, 800), "white").save(source_image)
    reference = client.post(
        f"/api/characters/{character['id']}/references",
        json={"source_path": str(source_image), "notes": "owned reference"},
    )
    assert reference.status_code == 201
    assert reference.json()["is_primary"] is True
    assert client.get(f"/api/references/{reference.json()['id']}/crop").status_code == 200

    source_lora = tmp_path / "owned-style.safetensors"
    header = json.dumps({"__metadata__": {}, "lora_te1.sample": {}}).encode()
    source_lora.write_bytes(len(header).to_bytes(8, "little") + header + b"payload")
    lora = client.post(
        "/api/loras/import",
        json={
            "source_path": str(source_lora),
            "name": "Owned SDXL style",
            "trigger_token": "ownedstyle",
        },
    )
    assert lora.status_code == 201
    assert lora.json()["model_family"] == "SDXL"
    assignment = client.put(
        f"/api/characters/{character['id']}/loras",
        json={"lora_id": lora.json()["id"], "strength": 0.7, "clip_strength": 0.8},
    )
    assert assignment.status_code == 200
    restored = client.get(f"/api/characters/{character['id']}").json()
    assert restored["references"][0]["is_primary"] is True
    assert restored["loras"][0]["name"] == "Owned SDXL style"

    flux_lora = tmp_path / "owned-flux-style.safetensors"
    flux_header = json.dumps(
        {"__metadata__": {}, "lora_unet_double_blocks_0_img_attn_proj.lora_up.weight": {}}
    ).encode()
    flux_lora.write_bytes(len(flux_header).to_bytes(8, "little") + flux_header + b"payload")
    imported_flux = client.post(
        "/api/loras/import",
        json={"source_path": str(flux_lora), "name": "Owned FLUX style"},
    )
    assert imported_flux.status_code == 201
    assert imported_flux.json()["model_family"] == "FLUX"
    assert (
        client.put(
            f"/api/characters/{character['id']}/loras",
            json={"lora_id": imported_flux.json()["id"], "position": 1},
        ).status_code
        == 200
    )


def test_pose_library_persists_source_progress_edit_and_delete(client, tmp_path):
    import time

    from PIL import Image

    source = tmp_path / "owned-pose.png"
    Image.new("RGB", (640, 800), "navy").save(source)
    created = client.post(
        "/api/poses/import",
        json={
            "name": "Owned standing reference",
            "source_path": str(source),
            "tags": ["standing", "editorial"],
            "favorite": True,
            "notes": "Broad movement only",
            "strength": 0.7,
        },
    )
    assert created.status_code == 201
    pose_id = created.json()["id"]
    assert client.get(f"/api/poses/{pose_id}/source").status_code == 200
    for _ in range(50):
        item = client.get(f"/api/poses/{pose_id}").json()
        if item["status"] == "failed":
            break
        time.sleep(0.01)
    assert item["status"] == "failed"
    assert "Local Generation Engine" in item["error_message"]
    updated = client.put(
        f"/api/poses/{pose_id}",
        json={
            "name": "Renamed pose",
            "tags": ["standing"],
            "favorite": False,
            "notes": "Updated locally",
            "strength": 0.55,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed pose"
    assert updated.json()["strength"] == 0.55
    assert client.delete(f"/api/poses/{pose_id}").status_code == 204
    assert client.get(f"/api/poses/{pose_id}").status_code == 404


def test_inpaint_request_persists_a_validated_mask_outside_job_json(client, tmp_path):
    import base64
    import io
    import json
    import sqlite3
    from datetime import UTC, datetime

    from PIL import Image, ImageDraw

    database_path = client.get("/api/settings").json()["paths"]["database"]
    source = tmp_path / "owned-source.png"
    Image.new("RGB", (512, 512), "navy").save(source)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO generations
            (id, image_path, prompt, seed, model_alias, width, height, metadata, created_at)
            VALUES ('generation-inpaint-source', ?, 'original owned image', 1,
            'photoreal_balanced', 512, 512, '{}', ?)""",
            (str(source), now),
        )
    mask = Image.new("L", (512, 512), 0)
    ImageDraw.Draw(mask).ellipse((160, 120, 360, 380), fill=255)
    encoded = io.BytesIO()
    mask.save(encoded, "PNG")
    response = client.post(
        "/api/generations",
        json={
            "operation": "inpaint",
            "source_generation_id": "generation-inpaint-source",
            "region_prompt": "tailored rose jacket",
            "region_negative_prompt": "text",
            "inpaint_mask_data_url": "data:image/png;base64,"
            + base64.b64encode(encoded.getvalue()).decode(),
            "seed": 12,
            "width": 512,
            "height": 512,
            "steps": 2,
            "guidance": 4,
        },
    )
    assert response.status_code == 202
    with sqlite3.connect(database_path) as connection:
        request = json.loads(
            connection.execute(
                "SELECT request_json FROM generation_jobs WHERE id=?", (response.json()["id"],)
            ).fetchone()[0]
        )
    assert "inpaint_mask_data_url" not in request
    assert request["inpaint_mask_path"].endswith(".png")
    assert Image.open(request["inpaint_mask_path"]).getbbox() is not None


def test_motion_import_requires_explicit_rights_confirmation(client):
    response = client.post(
        "/api/motion-assets",
        json={
            "name": "Unconfirmed motion",
            "source_path": "missing.mp4",
            "start_seconds": 0,
            "end_seconds": 2,
            "fit_mode": "crop",
            "smoothing": 0.5,
            "strength": 0.65,
            "rights_confirmed": False,
        },
    )
    assert response.status_code == 422
    assert "rights" in response.json()["detail"].lower()
    assert client.get("/api/motion-assets").json() == []


def test_video_request_is_persisted_as_a_local_generation_job(client, tmp_path):
    import json
    import sqlite3
    from datetime import UTC, datetime

    from PIL import Image

    database_path = client.get("/api/settings").json()["paths"]["database"]
    source = tmp_path / "video-source.png"
    Image.new("RGB", (512, 768), "navy").save(source)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO generations
            (id, image_path, prompt, seed, model_alias, width, height, metadata, created_at)
            VALUES ('generation-video-source', ?, 'owned fictional character', 1,
            'photoreal_balanced', 512, 768, '{}', ?)""",
            (str(source), datetime.now(UTC).isoformat()),
        )
    response = client.post(
        "/api/videos",
        json={
            "source_generation_id": "generation-video-source",
            "motion_prompt": "subtle breathing and a gentle posture shift",
            "profile": "safe",
            "duration_seconds": 2,
            "seed": 99,
            "motion_strength": 0.65,
        },
    )
    assert response.status_code == 202
    with sqlite3.connect(database_path) as connection:
        request = json.loads(
            connection.execute(
                "SELECT request_json FROM generation_jobs WHERE id=?", (response.json()["id"],)
            ).fetchone()[0]
        )
    assert request["operation"] == "video"
    assert request["model_alias"] == "video_ltx_2b"
    assert request["duration_seconds"] == 2
