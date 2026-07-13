"""Run the pinned local WD SwinV2 ONNX captioner without network access."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def _prepare_image(path: Path, size: int):
    import numpy as np
    from PIL import Image

    with Image.open(path) as source:
        rgba = source.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "WHITE")
        white.alpha_composite(rgba)
        image = white.convert("RGB")
    edge = max(image.size)
    square = Image.new("RGB", (edge, edge), "WHITE")
    square.paste(image, ((edge - image.width) // 2, (edge - image.height) // 2))
    square = square.resize((size, size), Image.Resampling.BICUBIC)
    rgb = np.asarray(square, dtype=np.float32)
    return np.expand_dims(rgb[:, :, ::-1], axis=0)


def _multiple_subject_count(tags: list[str]) -> int | None:
    normalized = {tag.replace(" ", "_") for tag in tags}
    if normalized.intersection({"multiple_girls", "multiple_boys", "group", "crowd"}):
        return 2
    count = 0
    for subject in ("girl", "boy", "woman", "man"):
        for number in range(1, 7):
            if (
                f"{number}{subject}" in normalized
                or f"{number}_{subject}" in normalized
            ):
                count += number
                break
    return count or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--tags", type=Path, required=True)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--trigger-token", default="")
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.package_root))

    import onnxruntime as ort

    session = ort.InferenceSession(str(args.model), providers=["CPUExecutionProvider"])
    if args.self_test:
        print(
            json.dumps(
                {"onnxruntime": ort.__version__, "input": session.get_inputs()[0].shape}
            )
        )
        return 0
    if not args.image:
        raise ValueError("--image is required")

    with args.tags.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    input_meta = session.get_inputs()[0]
    dimension = input_meta.shape[1] if isinstance(input_meta.shape[1], int) else 448
    probabilities = session.run(
        None, {input_meta.name: _prepare_image(args.image, dimension)}
    )[0][0]
    ranked: list[tuple[float, str]] = []
    for row, score in zip(rows, probabilities, strict=False):
        if int(row.get("category", -1)) != 0 or float(score) < args.threshold:
            continue
        tag = row["name"].replace("_", " ").strip()
        if tag:
            ranked.append((float(score), tag))
    selected = [tag for _, tag in sorted(ranked, reverse=True)[:40]]
    caption = ", ".join([part for part in [args.trigger_token, *selected] if part])
    print(
        json.dumps(
            {
                "caption": caption,
                "tags": selected,
                "face_count": _multiple_subject_count(selected),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
