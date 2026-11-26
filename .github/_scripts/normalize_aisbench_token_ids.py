"""Normalize sampled token IDs in the installed AISBench synthetic dataset."""

from pathlib import Path
import sys


def normalize_token_ids(dataset_path: Path) -> None:
    source = dataset_path.read_text(encoding="utf-8")
    updated = source
    # AISBench launches inference in a subprocess, so update the code it imports.
    # Tokenizers backed by tiktoken require a sequence of Python integers.
    for ids in ("prefix_ids", "input_ids"):
        updated = updated.replace(
            f"tokenizer_model.decode({ids})",
            f"tokenizer_model.decode({ids}.tolist() if isinstance({ids}, torch.Tensor) else {ids})",
        )

    if updated != source:
        dataset_path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    normalize_token_ids(Path(sys.argv[1]))
