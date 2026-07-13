# Project Vanta — Product Specification

## Product

A premium local desktop studio for original AI character creation. It hides generation and training engines behind a coherent, approachable creative workflow.

## Modes

### Simple

Character, wardrobe, expression, pose, scene, lighting, camera, quality and Generate.

### Studio

Full prompt composition, negative prompt, model, LoRA weights, identity strength, ControlNet strength, seed, sampler, scheduler, steps, guidance, dimensions and workflow overrides.

## Custom prompt system

Every prompt component is a `Preset`.

- categories: identity_modifier, wardrobe, expression, pose, location, lighting, camera, quality, negative, motion
- origin: builtin or user
- scope: global, character or project
- fields: name, prompt, negative_prompt, tags, favorite, thumbnail, created_at, updated_at
- built-ins are immutable; editing creates a user-owned copy
- a `Recipe` references presets plus freeform additions and explicit ordering
- users can save a complete composition as a recipe

## Local architecture

The desktop shell owns lifecycle. It launches a local orchestrator, which launches and supervises ComfyUI and later a training engine. The frontend talks only to the orchestrator. The orchestrator compiles stable domain requests into engine-specific workflows.

## Local cost policy

No inference APIs are required. Runtime costs are electricity, storage and optional purchases the user independently chooses. Model licenses and redistribution restrictions must be respected.

## Initial character direction

Store the supplied Y2K bedroom concept as a recipe, but normalize the identity wording to clearly describe an adult original character. Do not depend on proprietary “GPT Image 2 in Eromify”; choose the best installed local model/profile through the engine adapter.

## Later training flow

Dataset import → quality checks → duplicate detection → auto-caption locally → caption review → GPU-safe training recommendation → train → sample checkpoints → choose winner → install into character.
