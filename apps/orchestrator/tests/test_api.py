def test_health_is_loopback_only(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["host"] == "127.0.0.1"
    assert response.json()["privacy"] == "local-only"


def test_engine_diagnostics_and_support_bundle_are_sanitized_and_traceable(client):
    import io
    import json
    import zipfile
    from pathlib import Path

    components = client.get("/api/engine/components").json()
    assert components
    assert all(
        {"version", "revision", "source", "sha256", "license"} <= item.keys() for item in components
    )
    diagnostics = client.get("/api/engine/diagnostics")
    assert diagnostics.status_code == 200
    payload = diagnostics.json()
    assert payload["system"]["orchestrator_host"] == "127.0.0.1"
    assert "active_jobs" in payload["runtime"]
    assert payload["components"]

    exported = client.get("/api/diagnostics/export")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert {"system-metadata.json", "engine-diagnostics.json"} <= set(archive.namelist())
        bundled = archive.read("engine-diagnostics.json").decode()
        assert str(Path.home()) not in bundled
        assert json.loads(bundled)["system"]["orchestrator_host"] == "127.0.0.1"


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


def test_complete_scoped_recipe_round_trip_and_json_portability(client):
    character = client.post(
        "/api/characters",
        json={"name": "Recipe subject", "identity_description": "Original adult character"},
    ).json()
    categories = {item["category"] for item in client.get("/api/presets").json()}
    assert categories == {
        "identity_modifier",
        "wardrobe",
        "expression",
        "pose",
        "location",
        "lighting",
        "camera",
        "quality",
        "negative",
        "motion",
    }
    scoped_preset = client.post(
        "/api/presets",
        json={
            "category": "location",
            "name": "Character loft",
            "prompt": "restrained private loft",
            "scope": "character",
            "scope_id": character["id"],
        },
    ).json()
    payload = {
        "name": "Complete editorial direction",
        "character_id": character["id"],
        "freeform_prompt": "authored positive direction",
        "negative_prompt": "watermark, text",
        "model_profile": "photoreal_balanced",
        "preset_ids": [scoped_preset["id"]],
        "scope": "project",
        "scope_id": "Autumn campaign",
        "favorite": True,
        "tags": ["campaign", "editorial"],
        "model_family": "SDXL",
        "model_file": "verified-local-model.safetensors",
        "lora_stack": [{"id": "lora-local", "strength": 0.75, "clip_strength": 0.9}],
        "identity_settings": {"reference_id": "reference-local", "strength": 0.65},
        "pose_settings": {"pose_id": "pose-local", "strength": 0.8},
        "variation_settings": {"mode": "lighting", "strength": 0.35},
        "video_settings": {"profile": "safe", "duration_seconds": 3},
        "generation_settings": {
            "width": 768,
            "height": 1024,
            "steps": 28,
            "guidance": 5.5,
            "sampler": "dpmpp_2m",
            "scheduler": "karras",
            "mode": "studio",
        },
    }
    created = client.post("/api/recipes", json=payload)
    assert created.status_code == 201
    recipe = created.json()
    assert recipe["scope_id"] == "Autumn campaign"
    assert recipe["lora_stack"][0]["strength"] == 0.75
    assert recipe["generation_settings"]["scheduler"] == "karras"
    assert recipe["items"][0]["preset_id"] == scoped_preset["id"]

    updated_payload = {**payload, "name": "Updated direction", "favorite": False}
    updated = client.put(f"/api/recipes/{recipe['id']}", json=updated_payload)
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated direction"
    duplicate = client.post(f"/api/recipes/{recipe['id']}/duplicate")
    assert duplicate.status_code == 201
    assert duplicate.json()["generation_settings"] == payload["generation_settings"]
    exported = client.get("/api/recipes-export").json()
    assert exported["schema_version"] == 1
    assert len(exported["recipes"]) == 2
    assert client.post("/api/recipes-import", json=exported).status_code == 200
    rejected = client.put(
        f"/api/recipes/{recipe['id']}",
        json={**updated_payload, "name": "Must not partially save", "preset_ids": ["missing"]},
    )
    assert rejected.status_code == 422
    assert client.get(f"/api/recipes/{recipe['id']}").json()["name"] == "Updated direction"
    assert client.delete(f"/api/recipes/{recipe['id']}").status_code == 204


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
    assert jobs.json()[0]["operation"] == "generate"
    assert jobs.json()[0]["model_alias"] == "photoreal_balanced"
    assert jobs.json()[0]["model_family"] == "SDXL"
    assert jobs.json()[0]["output_width"] == 832
    assert jobs.json()[0]["output_height"] == 1216
    assert isinstance(jobs.json()[0]["elapsed_seconds"], int)
    assert isinstance(jobs.json()[0]["progress_determinate"], bool)


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
    from pathlib import Path

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
    managed_lora = Path(lora.json()["installed_path"])
    managed_lora.unlink()
    assert client.get("/api/loras").json()[0]["verification_state"] == "repair_needed"
    repaired = client.post(f"/api/loras/{lora.json()['id']}/repair")
    assert repaired.status_code == 200
    assert repaired.json()["verification_state"] == "ready"
    assert managed_lora.is_file()
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
    from pathlib import Path

    from PIL import Image

    settings_paths = client.get("/api/settings").json()["paths"]
    database_path = settings_paths["database"]
    capabilities = client.get("/api/videos/capabilities?quality_profile=safe").json()
    assert capabilities["profiles"][0]["duration_seconds"] == 2
    assert capabilities["profiles"][1]["duration_seconds"] == 4
    assert capabilities["extended_verified"] is False
    assert capabilities["max_custom_seconds"] == 4
    source = Path(settings_paths["data"]) / "media" / "generations" / "video-source.png"
    source.parent.mkdir(parents=True, exist_ok=True)
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
            "duration_profile": "safe",
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
    assert request["duration_profile"] == "safe"

    sequence = client.post(
        "/api/video-sequences",
        json={"name": "Editorial continuation", "source_generation_id": "generation-video-source"},
    )
    assert sequence.status_code == 201
    sequence_id = sequence.json()["id"]
    segment = client.post(
        f"/api/video-sequences/{sequence_id}/segments",
        json={
            "motion_prompt": "a restrained turn toward the window",
            "profile": "safe",
            "duration_profile": "safe",
            "duration_seconds": 2,
            "seed": 100,
        },
    )
    assert segment.status_code == 202
    assert segment.json()["segments"][0]["source_generation_id"] == "generation-video-source"
    assert segment.json()["segments"][0]["motion_prompt"].startswith("a restrained")
    assert (
        client.post(
            "/api/videos",
            json={
                "source_generation_id": "generation-video-source",
                "motion_prompt": "too long for unverified hardware",
                "profile": "safe",
                "duration_profile": "extended",
                "duration_seconds": 6,
                "seed": 101,
            },
        ).status_code
        == 422
    )


def test_video_sequence_reorders_deletes_and_joins_completed_segments(client, tmp_path):
    import json
    import sqlite3
    from datetime import UTC, datetime
    from pathlib import Path

    from PIL import Image

    from vanta_orchestrator.video import encode_mp4

    settings_paths = client.get("/api/settings").json()["paths"]
    database_path = settings_paths["database"]
    generation_root = Path(settings_paths["data"]) / "media" / "generations"
    generation_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    still = generation_root / "sequence-source.png"
    Image.new("RGB", (64, 64), "plum").save(still)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO generations
            (id,image_path,prompt,seed,model_alias,width,height,metadata,created_at)
            VALUES('sequence-source',?,'owned original',1,'photoreal_balanced',64,64,'{}',?)""",
            (str(still), now),
        )
    sequence = client.post(
        "/api/video-sequences",
        json={"name": "Joined study", "source_generation_id": "sequence-source"},
    ).json()
    generation_ids: list[str] = []
    segment_ids: list[str] = []
    with sqlite3.connect(database_path) as connection:
        for index, color in enumerate(("#9f3f68", "navy")):
            frames = []
            for frame_index in range(2):
                frame = tmp_path / f"segment-{index}-frame-{frame_index}.png"
                Image.new("RGB", (64, 64), color).save(frame)
                frames.append(frame)
            video = generation_root / f"segment-{index}.mp4"
            poster = generation_root / f"segment-{index}.jpg"
            continuation = generation_root / f"segment-{index}-last.png"
            encode_mp4(frames, video, 2)
            Image.open(frames[0]).save(poster)
            Image.open(frames[-1]).save(continuation)
            generation_id = f"sequence-video-{index}"
            segment_id = f"sequence-segment-{index}"
            generation_ids.append(generation_id)
            segment_ids.append(segment_id)
            connection.execute(
                """INSERT INTO generations
                (id,image_path,thumbnail_path,prompt,seed,model_alias,width,height,metadata,created_at,media_type)
                VALUES(?,?,?,?,?,'video_ltx_2b',64,64,?,?,'video')""",
                (
                    generation_id,
                    str(video),
                    str(poster),
                    f"motion {index}",
                    index + 2,
                    json.dumps(
                        {
                            "duration_seconds": 1,
                            "frame_count": 2,
                            "fps": 2,
                            "continuation_frame_path": str(continuation),
                        }
                    ),
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO video_sequence_segments
                (id,sequence_id,position,source_generation_id,generation_id,motion_prompt,
                quality_profile,duration_profile,duration_seconds,seed,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'safe','safe',2,?,'completed',?,?)""",
                (
                    segment_id,
                    sequence["id"],
                    index,
                    "sequence-source" if index == 0 else generation_ids[index - 1],
                    generation_id,
                    f"motion {index}",
                    index + 20,
                    now,
                    now,
                ),
            )
    reordered = client.put(
        f"/api/video-sequences/{sequence['id']}/order",
        json={"segment_ids": list(reversed(segment_ids))},
    )
    assert [item["id"] for item in reordered.json()["segments"]] == list(reversed(segment_ids))
    duplicate = client.post(
        f"/api/video-sequences/{sequence['id']}/join",
        json={"segment_ids": [segment_ids[0], segment_ids[0]]},
    )
    assert duplicate.status_code == 409
    joined = client.post(
        f"/api/video-sequences/{sequence['id']}/join",
        json={"segment_ids": list(reversed(segment_ids))},
    )
    assert joined.status_code == 200
    assert joined.json()["status"] == "joined"
    final_id = joined.json()["final_generation_id"]
    with sqlite3.connect(database_path) as connection:
        final_path, metadata = connection.execute(
            "SELECT image_path,metadata FROM generations WHERE id=?", (final_id,)
        ).fetchone()
    assert Path(final_path).is_file()
    assert json.loads(metadata)["segment_ids"] == list(reversed(segment_ids))
    continuation = client.post(
        f"/api/videos/{generation_ids[0]}/continuation-frame",
        json={"timestamp_seconds": 0.25},
    )
    assert continuation.status_code == 201
    assert continuation.json()["media_type"] == "image"
    assert Path(continuation.json()["image_path"]).is_file()
    removed = client.delete(f"/api/video-sequences/{sequence['id']}/segments/{segment_ids[0]}")
    assert removed.status_code == 200
    assert len(removed.json()["segments"]) == 1


def test_training_dataset_quality_checks_captions_and_truthful_readiness(client, tmp_path):
    from PIL import Image, ImageDraw

    character = client.post(
        "/api/characters",
        json={"name": "Owned fictional trainer subject", "identity_description": "Original"},
    ).json()
    created = client.post(
        "/api/training/datasets",
        json={
            "name": "Editorial identity set",
            "character_id": character["id"],
            "trigger_token": "vantaSubject",
            "model_alias": "photoreal_balanced",
        },
    )
    assert created.status_code == 201
    dataset_id = created.json()["id"]

    image_path = tmp_path / "owned.png"
    image = Image.new("RGB", (640, 768), "white")
    draw = ImageDraw.Draw(image)
    for offset in range(0, 640, 32):
        draw.rectangle((offset, 0, offset + 16, 768), fill=(offset % 255, 40, 120))
    image.save(image_path)
    imported = client.post(
        f"/api/training/datasets/{dataset_id}/images",
        json={"source_paths": [str(image_path)], "rights_confirmed": True},
    )
    assert imported.status_code == 201
    training_image = imported.json()["dataset"]["images"][0]
    assert training_image["caption"] == "vantaSubject"
    assert training_image["width"] == 640
    assert training_image["height"] == 768
    assert client.get(f"/api/training/images/{training_image['id']}/thumbnail").status_code == 200

    duplicate = client.post(
        f"/api/training/datasets/{dataset_id}/images",
        json={"source_paths": [str(image_path)], "rights_confirmed": True},
    )
    assert duplicate.status_code == 422
    assert "duplicate" in duplicate.json()["detail"].lower()
    updated = client.put(
        f"/api/training/images/{training_image['id']}/caption",
        json={"caption": "vantaSubject, original fictional person, editorial portrait"},
    )
    assert updated.status_code == 200
    assert "editorial portrait" in updated.json()["caption"]

    profiles = client.get("/api/training/profiles").json()
    assert profiles["safe_12gb"]["resolution"] == 512
    assert profiles["balanced_12gb"]["rank"] == 8
    components = {item["id"]: item for item in client.get("/api/engine/components").json()}
    assert components["lora-training"]["state"] == "not_installed"
    assert components["captioning"]["state"] == "not_installed"
    blocked = client.post(
        "/api/training/runs",
        json={"dataset_id": dataset_id, "profile": "safe_12gb", "epochs": 1},
    )
    assert blocked.status_code == 409
    assert "install" in blocked.json()["detail"].lower()


def test_training_dataset_rejects_corrupt_and_low_rights_inputs(client, tmp_path):
    dataset = client.post(
        "/api/training/datasets",
        json={"name": "Local set", "trigger_token": "localSubject"},
    ).json()
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    response = client.post(
        f"/api/training/datasets/{dataset['id']}/images",
        json={"source_paths": [str(corrupt)], "rights_confirmed": True},
    )
    assert response.status_code == 422
    not_confirmed = client.post(
        f"/api/training/datasets/{dataset['id']}/images",
        json={"source_paths": [str(corrupt)], "rights_confirmed": False},
    )
    assert not_confirmed.status_code == 422
    assert "permission" in not_confirmed.json()["detail"].lower()


def test_typed_media_routes_enforce_owned_roots_repair_derivatives_and_support_ranges(
    client, tmp_path
):
    import json
    import sqlite3
    from datetime import UTC, datetime
    from pathlib import Path

    from PIL import Image

    from vanta_orchestrator.video import encode_mp4

    paths = client.get("/api/settings").json()["paths"]
    database_path = paths["database"]
    data_root = Path(paths["data"])
    generation_root = data_root / "media" / "generations"
    generation_root.mkdir(parents=True, exist_ok=True)

    image_id = "generation-media-repair"
    image_path = generation_root / f"{image_id}.png"
    thumbnail_path = generation_root / f"{image_id}.thumb.jpg"
    Image.new("RGB", (320, 480), "plum").save(image_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO generations
            (id,image_path,thumbnail_path,prompt,seed,model_alias,width,height,metadata,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                image_id,
                str(image_path),
                str(thumbnail_path),
                "owned fictional portrait",
                1,
                "photoreal_balanced",
                1,
                1,
                "{}",
                datetime.now(UTC).isoformat(),
            ),
        )
    missing = client.get(f"/api/media/generation/{image_id}/thumbnail")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "media_missing"
    repaired = client.post("/api/media/repair")
    assert repaired.status_code == 200
    assert repaired.json()["regenerated_derivatives"] >= 1
    thumbnail = client.get(f"/api/media/generation/{image_id}/thumbnail")
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/jpeg"
    assert thumbnail.headers["x-content-type-options"] == "nosniff"
    with sqlite3.connect(database_path) as connection:
        dimensions = connection.execute(
            "SELECT width,height FROM generations WHERE id=?", (image_id,)
        ).fetchone()
        indexed = connection.execute(
            "SELECT mime_type,state FROM media_assets WHERE entity_type='generation' AND entity_id=? AND variant='thumbnail'",
            (image_id,),
        ).fetchone()
    assert dimensions == (320, 480)
    assert indexed == ("image/jpeg", "ready")

    frames = []
    for index in range(4):
        frame = tmp_path / f"video-frame-{index}.png"
        Image.new("RGB", (64, 64), (80 + index * 20, 10, 70)).save(frame)
        frames.append(frame)
    video_id = "generation-range-video"
    video_path = generation_root / f"{video_id}.mp4"
    poster_path = generation_root / f"{video_id}.thumb.jpg"
    encode_mp4(frames, video_path, 8)
    Image.open(frames[0]).save(poster_path, "JPEG")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO generations
            (id,image_path,thumbnail_path,prompt,seed,model_alias,width,height,metadata,created_at,media_type)
            VALUES(?,?,?,?,?,?,?,?,?,?, 'video')""",
            (
                video_id,
                str(video_path),
                str(poster_path),
                "local motion",
                2,
                "video_ltx_2b",
                64,
                64,
                json.dumps({"duration_seconds": 0.5}),
                datetime.now(UTC).isoformat(),
            ),
        )
    ranged = client.get(f"/api/media/generation/{video_id}/video", headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206
    assert ranged.headers["content-type"] == "video/mp4"
    assert ranged.headers["accept-ranges"] == "bytes"
    assert ranged.headers["content-range"].startswith("bytes 0-99/")

    outside = tmp_path / "outside.png"
    Image.new("RGB", (32, 32), "black").save(outside)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE generations SET image_path=? WHERE id=?", (str(outside), image_id)
        )
    blocked = client.get(f"/api/media/generation/{image_id}/original")
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "media_outside_vanta_storage"
    unsafe_sequence = client.post(
        "/api/video-sequences",
        json={"name": "Unsafe source", "source_generation_id": image_id},
    )
    assert unsafe_sequence.status_code == 409
