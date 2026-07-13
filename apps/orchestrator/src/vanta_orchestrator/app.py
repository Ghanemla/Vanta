from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from .config import Settings
from .database import Database, utc_now
from .engine import EngineService, GenerationService
from .pose import PoseService
from .repositories import (
    CharacterRepository,
    LoraRepository,
    PresetRepository,
    RecipeRepository,
    ReferenceRepository,
)
from .schemas import (
    CharacterInput,
    CharacterLoraInput,
    GenerationInput,
    IdentityAdapterImportInput,
    LoraImportInput,
    ModelImportInput,
    PoseImportInput,
    PoseUpdateInput,
    PresetInput,
    RecipeInput,
    ReferenceImportInput,
    ReferenceUpdateInput,
    SettingInput,
    UpscalerImportInput,
)

AUTH_HEADER = "X-Vanta-Token"
ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
ALLOWED_HEADERS = ["Content-Type", "Accept", AUTH_HEADER]
logger = logging.getLogger("vanta.orchestrator.requests")


class LaunchTokenMiddleware(BaseHTTPMiddleware):
    """Require the per-launch token for API requests, never for CORS preflight."""

    def __init__(self, app: ASGIApp, launch_token: str | None) -> None:
        super().__init__(app)
        self.launch_token = launch_token

    async def dispatch(self, request: Request, call_next):
        if (
            request.url.path.startswith("/api")
            and request.method != "OPTIONS"
            and self.launch_token
        ):
            provided = request.headers.get(AUTH_HEADER, "")
            if not hmac.compare_digest(provided, self.launch_token):
                return JSONResponse(
                    status_code=401, content={"detail": "Local service authentication failed"}
                )
        return await call_next(request)


class RequestDiagnosticsMiddleware(BaseHTTPMiddleware):
    """Log local CORS/auth routing metadata without ever logging the launch token."""

    def __init__(self, app: ASGIApp, enabled: bool) -> None:
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if self.enabled and request.url.path.startswith("/api"):
            preflight = request.method == "OPTIONS" and bool(
                request.headers.get("access-control-request-method")
            )
            logger.info(
                "request method=%s path=%s origin=%s requested_method=%s requested_headers=%s "
                "preflight=%s token_auth_attempted=%s status=%s",
                request.method,
                request.url.path,
                request.headers.get("origin", ""),
                request.headers.get("access-control-request-method", ""),
                request.headers.get("access-control-request-headers", ""),
                preflight,
                request.url.path.startswith("/api") and request.method != "OPTIONS",
                response.status_code,
            )
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(
        settings.database_path,
        settings.migrations_dir,
        settings.starter_presets_path,
    )
    db.migrate()
    characters, presets, recipes = (
        CharacterRepository(db),
        PresetRepository(db),
        RecipeRepository(db),
    )
    references = ReferenceRepository(db, settings.reference_root)
    loras = LoraRepository(db, settings.lora_root)
    engine = EngineService(db, settings)
    generation_jobs = GenerationService(db, engine)
    poses = PoseService(db, settings, engine)
    generation_jobs.recover()

    app = FastAPI(title="Vanta Local Orchestrator", version="0.1.0", docs_url="/api/docs")
    # Starlette applies the most recently added middleware first. CORS must be
    # outermost so it can answer browser preflight before token authentication.
    app.add_middleware(LaunchTokenMiddleware, launch_token=settings.launch_token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
        allow_credentials=False,
    )
    app.add_middleware(RequestDiagnosticsMiddleware, enabled=settings.diagnostics_enabled)

    @app.on_event("shutdown")
    def stop_managed_engine() -> None:
        engine.close()

    def missing(error: KeyError) -> HTTPException:
        return HTTPException(status_code=404, detail=f"Item {error.args[0]} was not found")

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ready",
            "host": "127.0.0.1",
            "privacy": "local-only",
            "version": app.version,
        }

    @app.get("/api/characters")
    def list_characters(include_archived: bool = False) -> list[dict]:
        return characters.list(include_archived)

    @app.post("/api/characters", status_code=201)
    def create_character(payload: CharacterInput) -> dict:
        return characters.create(payload)

    @app.get("/api/characters/{item_id}")
    def get_character(item_id: str) -> dict:
        try:
            return characters.get(item_id)
        except KeyError as error:
            raise missing(error) from error

    @app.put("/api/characters/{item_id}")
    def update_character(item_id: str, payload: CharacterInput) -> dict:
        try:
            return characters.update(item_id, payload)
        except KeyError as error:
            raise missing(error) from error

    @app.delete("/api/characters/{item_id}", status_code=204)
    def archive_character(item_id: str) -> None:
        try:
            characters.archive(item_id)
        except KeyError as error:
            raise missing(error) from error

    @app.post("/api/characters/{item_id}/restore")
    def restore_character(item_id: str) -> dict:
        try:
            return characters.restore(item_id)
        except KeyError as error:
            raise missing(error) from error

    @app.post("/api/characters/{item_id}/duplicate", status_code=201)
    def duplicate_character(item_id: str) -> dict:
        try:
            return characters.duplicate(item_id)
        except KeyError as error:
            raise missing(error) from error

    @app.delete("/api/characters/{item_id}/permanently", status_code=204)
    def delete_character_permanently(item_id: str) -> None:
        try:
            characters.delete_permanently(item_id)
        except KeyError as error:
            raise missing(error) from error

    @app.post("/api/characters/{item_id}/references", status_code=201)
    def import_reference(item_id: str, payload: ReferenceImportInput) -> dict:
        try:
            return references.import_image(item_id, payload.source_path, payload.notes)
        except KeyError as error:
            raise missing(error) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/api/references/{reference_id}")
    def update_reference(reference_id: str, payload: ReferenceUpdateInput) -> dict:
        try:
            return references.update(
                reference_id, payload.notes, payload.position, payload.is_primary
            )
        except KeyError as error:
            raise missing(error) from error

    @app.delete("/api/references/{reference_id}", status_code=204)
    def delete_reference(reference_id: str) -> None:
        try:
            references.delete(reference_id)
        except KeyError as error:
            raise missing(error) from error

    @app.get("/api/references/{reference_id}/{variant}")
    def reference_image(reference_id: str, variant: str) -> FileResponse:
        if variant not in {"image", "thumbnail", "crop"}:
            raise HTTPException(status_code=404, detail="Reference image variant was not found")
        try:
            reference = references.get(reference_id)
        except KeyError as error:
            raise missing(error) from error
        path = Path(reference[f"{variant}_path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Reference image file was not found")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/loras")
    def list_loras() -> list[dict]:
        return loras.list()

    @app.get("/api/poses")
    def list_poses(query: str = "") -> list[dict]:
        return poses.list(query)

    @app.post("/api/poses/import", status_code=201)
    def import_pose(payload: PoseImportInput) -> dict:
        try:
            return poses.import_pose(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/poses/{pose_id}")
    def get_pose(pose_id: str) -> dict:
        try:
            return poses.get(pose_id)
        except KeyError as error:
            raise missing(error) from error

    @app.put("/api/poses/{pose_id}")
    def update_pose(pose_id: str, payload: PoseUpdateInput) -> dict:
        try:
            return poses.update(pose_id, payload)
        except KeyError as error:
            raise missing(error) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/poses/{pose_id}/duplicate", status_code=201)
    def duplicate_pose(pose_id: str) -> dict:
        try:
            return poses.duplicate(pose_id)
        except KeyError as error:
            raise missing(error) from error

    @app.delete("/api/poses/{pose_id}", status_code=204)
    def delete_pose(pose_id: str) -> None:
        try:
            poses.delete(pose_id)
        except KeyError as error:
            raise missing(error) from error

    @app.get("/api/poses/{pose_id}/{variant}")
    def pose_media(pose_id: str, variant: str) -> FileResponse:
        field = {
            "source": "source_path",
            "source-thumbnail": "source_thumbnail_path",
            "control": "control_path",
            "control-thumbnail": "control_thumbnail_path",
        }.get(variant)
        if not field:
            raise HTTPException(status_code=404, detail="Pose media variant was not found")
        try:
            item = poses.get(pose_id)
        except KeyError as error:
            raise missing(error) from error
        path = Path(item[field])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Pose media is not ready")
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "image/png")

    @app.post("/api/loras/import", status_code=201)
    def import_lora(payload: LoraImportInput) -> dict:
        try:
            return loras.import_lora(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.delete("/api/loras/{item_id}", status_code=204)
    def remove_lora(item_id: str) -> None:
        try:
            loras.remove(item_id)
        except KeyError as error:
            raise missing(error) from error

    @app.put("/api/characters/{item_id}/loras")
    def assign_lora(item_id: str, payload: CharacterLoraInput) -> dict:
        try:
            return loras.assign(item_id, payload)
        except KeyError as error:
            raise missing(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/presets")
    def list_presets() -> list[dict]:
        return presets.list()

    @app.post("/api/presets", status_code=201)
    def create_preset(payload: PresetInput) -> dict:
        return presets.create(payload)

    @app.put("/api/presets/{item_id}")
    def update_preset(item_id: str, payload: PresetInput) -> dict:
        try:
            return presets.update(item_id, payload)
        except KeyError as error:
            raise missing(error) from error

    @app.post("/api/presets/{item_id}/duplicate", status_code=201)
    def duplicate_preset(item_id: str) -> dict:
        try:
            return presets.duplicate(item_id)
        except KeyError as error:
            raise missing(error) from error

    @app.delete("/api/presets/{item_id}", status_code=204)
    def delete_preset(item_id: str) -> None:
        try:
            presets.delete(item_id)
        except KeyError as error:
            raise missing(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/presets/restore-builtins")
    def restore_builtins() -> dict:
        presets.restore_builtins()
        return {"message": "Built-in presets restored"}

    @app.get("/api/presets-export")
    def export_presets() -> dict:
        return {
            "schema_version": 1,
            "presets": [item for item in presets.list() if item["origin"] == "user"],
        }

    @app.post("/api/presets-import")
    def import_presets(payload: dict) -> dict:
        if payload.get("schema_version") != 1 or not isinstance(payload.get("presets"), list):
            raise HTTPException(status_code=422, detail="Unsupported preset export format")
        imported = []
        for item in payload["presets"]:
            imported.append(presets.create(PresetInput.model_validate(item)))
        return {"imported": len(imported)}

    @app.get("/api/recipes")
    def list_recipes() -> list[dict]:
        return recipes.list()

    @app.post("/api/recipes", status_code=201)
    def create_recipe(payload: RecipeInput) -> dict:
        return recipes.create(payload)

    @app.get("/api/gallery")
    def gallery(model: str | None = Query(default=None)) -> list[dict]:
        sql, params = "SELECT * FROM generations", ()
        if model:
            sql, params = f"{sql} WHERE model_alias=?", (model,)
        rows = db.query_all(f"{sql} ORDER BY created_at DESC", params)
        for row in rows:
            row["metadata"] = json.loads(row["metadata"])
        return rows

    @app.post("/api/generations", status_code=202)
    def create_generation(payload: GenerationInput) -> dict:
        try:
            return generation_jobs.queue(payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/generations/{job_id}")
    def get_generation_job(job_id: str) -> dict:
        try:
            return generation_jobs.get(job_id)
        except KeyError as error:
            raise missing(error) from error

    @app.get("/api/jobs")
    def list_jobs(limit: int = Query(default=40, ge=1, le=100)) -> list[dict]:
        return generation_jobs.list(limit)

    @app.post("/api/generations/{job_id}/retry", status_code=202)
    def retry_generation(job_id: str) -> dict:
        try:
            return generation_jobs.retry(job_id)
        except KeyError as error:
            raise missing(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/generations/{job_id}/cancel")
    def cancel_generation(job_id: str) -> dict:
        try:
            return generation_jobs.cancel(job_id)
        except KeyError as error:
            raise missing(error) from error

    @app.get("/api/generations/{generation_id}/similar")
    def generation_similar(generation_id: str) -> dict:
        row = db.query_one("SELECT metadata FROM generations WHERE id=?", (generation_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="Generation was not found")
        return json.loads(row["metadata"]).get("request", {})

    def generation_media(generation_id: str, variant: str) -> FileResponse:
        if variant == "mask":
            row = db.query_one("SELECT metadata FROM generations WHERE id=?", (generation_id,))
            metadata = json.loads(row["metadata"]) if row else {}
            path = Path((metadata.get("inpaint") or {}).get("mask_path") or "")
            if not path.is_file() or path.parent.resolve() != settings.inpaint_root.resolve():
                raise HTTPException(status_code=404, detail="Generated mask file was not found")
            return FileResponse(path, media_type="image/png")
        if variant not in {"image", "thumbnail"}:
            raise HTTPException(status_code=404, detail="Generated media variant was not found")
        column = "image_path" if variant == "image" else "thumbnail_path"
        row = db.query_one(f"SELECT {column} FROM generations WHERE id=?", (generation_id,))
        path = Path(row.get(column) or "") if row else Path()
        if not row or not path.is_file():
            raise HTTPException(status_code=404, detail=f"Generated {variant} file was not found")
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "image/png")

    @app.get("/api/generations/{generation_id}/image")
    def generation_image(generation_id: str) -> FileResponse:
        return generation_media(generation_id, "image")

    @app.get("/api/generations/{generation_id}/thumbnail")
    def generation_thumbnail(generation_id: str) -> FileResponse:
        return generation_media(generation_id, "thumbnail")

    @app.get("/api/generations/{generation_id}/mask")
    def generation_mask(generation_id: str) -> FileResponse:
        return generation_media(generation_id, "mask")

    @app.post("/api/generations/{generation_id}/repair-media")
    def repair_generation_media(generation_id: str) -> dict:
        row = db.query_one(
            "SELECT image_path, thumbnail_path FROM generations WHERE id=?", (generation_id,)
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Generation was not found")
        image = Path(row["image_path"])
        if not image.is_file():
            candidate = settings.media_root / f"{generation_id}.png"
            if not candidate.is_file():
                raise HTTPException(
                    status_code=409, detail="The original generated image is missing"
                )
            image = candidate
        thumbnail = Path(row.get("thumbnail_path") or "")
        regenerated = False
        if not thumbnail.is_file():
            from PIL import Image

            thumbnail = settings.media_root / f"{generation_id}.thumb.jpg"
            with Image.open(image) as original:
                original.thumbnail((480, 480))
                original.convert("RGB").save(thumbnail, "JPEG", quality=88, optimize=True)
            regenerated = True
        db.execute(
            "UPDATE generations SET image_path=?, thumbnail_path=? WHERE id=?",
            (str(image), str(thumbnail), generation_id),
        )
        return {"generation_id": generation_id, "thumbnail_regenerated": regenerated}

    @app.delete("/api/generations/{generation_id}", status_code=204)
    def delete_generation(generation_id: str) -> None:
        row = db.query_one(
            "SELECT image_path, thumbnail_path, metadata FROM generations WHERE id=?",
            (generation_id,),
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Generation was not found")
        for path in {row["image_path"], row.get("thumbnail_path")}:
            if path:
                Path(path).unlink(missing_ok=True)
        mask_path = (json.loads(row["metadata"]).get("inpaint") or {}).get("mask_path") or ""
        if mask_path:
            candidate = Path(mask_path)
            if candidate.parent.resolve() == settings.inpaint_root.resolve():
                candidate.unlink(missing_ok=True)
        db.execute("DELETE FROM generations WHERE id=?", (generation_id,))

    @app.get("/api/engine/components")
    def list_components() -> list[dict]:
        return engine.list_components()

    @app.post("/api/engine/components/{item_id}/{action}")
    def component_action(item_id: str, action: str) -> dict:
        try:
            return engine.component_action(item_id, action)
        except KeyError as error:
            raise missing(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/engine/model-packs")
    def list_model_packs() -> dict:
        return {"hardware": engine.hardware, "packs": engine.list_packs()}

    @app.post("/api/engine/model-packs/{item_id}/{action}")
    def pack_action(item_id: str, action: str) -> dict:
        try:
            return engine.pack_action(item_id, action)
        except KeyError as error:
            raise missing(error) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/engine/models/import")
    def import_local_model(payload: ModelImportInput) -> dict:
        try:
            return engine.import_model(payload.source_path, payload.alias, payload.license_notes)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/engine/upscalers/import")
    def import_upscaler(payload: UpscalerImportInput) -> dict:
        try:
            return engine.import_upscaler(payload.source_path, payload.alias, payload.license_notes)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/engine/identity-adapter/import")
    def import_identity_adapter(payload: IdentityAdapterImportInput) -> dict:
        try:
            return engine.import_identity_adapter(
                payload.adapter_source_path, payload.clip_vision_source_path, payload.license_notes
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/engine/diagnostics")
    def diagnostics() -> dict:
        return engine.diagnostics()

    @app.get("/api/diagnostics/export")
    def export_diagnostics() -> FileResponse:
        export_dir = settings.data_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        archive = export_dir / "vanta-diagnostics.zip"
        metadata = {
            "desktop_data_dir": str(settings.data_dir),
            "database_path": str(settings.database_path),
            "logs_path": str(settings.logs_dir or settings.data_dir / "logs"),
            "orchestrator_version": app.version,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("system-metadata.json", json.dumps(metadata, indent=2))
            for log in (settings.logs_dir or settings.data_dir / "logs").glob("*.log"):
                sanitized = log.read_text(encoding="utf-8", errors="replace").replace(
                    settings.launch_token or "", "[redacted]"
                )
                bundle.writestr(f"logs/{log.name}", sanitized)
        return FileResponse(archive, media_type="application/zip", filename=archive.name)

    @app.get("/api/settings")
    def get_settings() -> dict:
        values = {row["key"]: row["value"] for row in db.query_all("SELECT * FROM app_settings")}
        return {
            "values": values,
            "paths": {
                "data": str(settings.data_dir.resolve()),
                "database": str(settings.database_path.resolve()),
                "models": str(settings.model_root.resolve()),
            },
        }

    @app.put("/api/settings/{key}")
    def set_setting(key: str, payload: SettingInput) -> dict:
        db.execute(
            "INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, payload.value, utc_now()),
        )
        return {"key": key, "value": payload.value}

    return app
