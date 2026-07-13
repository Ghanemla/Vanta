"""Launch pinned sd-scripts inside Vanta's managed CUDA runtime.

The ComfyUI embedded Python uses an isolated path configuration, so the trainer's
packages and source are inserted explicitly. This keeps trainer dependencies away
from the generation runtime while reusing its verified CUDA PyTorch installation.
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--tokenizer-root", type=Path, required=True)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("training_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    sys.path[:0] = [str(args.package_root), str(args.source_root)]

    import accelerate
    import diffusers
    import torch
    import transformers

    if args.self_test:
        from networks import lora
        from transformers import CLIPTokenizer

        del lora
        tokenizer_names = (
            "openai_clip-vit-large-patch14",
            "laion_CLIP-ViT-bigG-14-laion2B-39B-b160k",
        )
        for name in tokenizer_names:
            CLIPTokenizer.from_pretrained(
                args.tokenizer_root / name, local_files_only=True
            )
        print(
            json.dumps(
                {
                    "torch": torch.__version__,
                    "cuda": torch.cuda.is_available(),
                    "gpu": torch.cuda.get_device_name(0)
                    if torch.cuda.is_available()
                    else None,
                    "accelerate": accelerate.__version__,
                    "diffusers": diffusers.__version__,
                    "transformers": transformers.__version__,
                    "tokenizers": list(tokenizer_names),
                }
            )
        )
        return 0 if torch.cuda.is_available() else 2

    training_args = args.training_args
    if training_args and training_args[0] == "--":
        training_args = training_args[1:]
    entry = args.source_root / "sdxl_train_network.py"
    sys.argv = [str(entry), *training_args]
    runpy.run_path(str(entry), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
