"""Regression test for AISBench's synthetic token-ID benchmark."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
NORMALIZE = ROOT / ".github" / "_scripts" / "normalize_aisbench_token_ids.py"
SYNTHETIC_SOURCE = """
def build_prompt(tokenizer_model, prefix_ids, input_ids):
    prefix_str = tokenizer_model.decode(prefix_ids)
    return prefix_str + tokenizer_model.decode(input_ids)
"""


class Tensor:
    def __init__(self, ids):
        self.ids = ids

    def tolist(self):
        return self.ids


class SequenceTokenizer:
    def decode(self, ids):
        if not isinstance(ids, list):
            raise TypeError("decode requires a list of token IDs")
        return ",".join(str(token_id) for token_id in ids)


class AISBenchTokenIdsTests(unittest.TestCase):
    def test_synthetic_dataset_can_decode_tensor_ids_with_a_sequence_tokenizer(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "synthetic.py"
            source.write_text(SYNTHETIC_SOURCE, encoding="utf-8")

            command = [sys.executable, str(NORMALIZE), str(source)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

            patched = source.read_text(encoding="utf-8")
            namespace = {"torch": type("Torch", (), {"Tensor": Tensor})}
            exec(compile(patched, str(source), "exec"), namespace)
            build_prompt = namespace["build_prompt"]
            tokenizer = SequenceTokenizer()
            self.assertEqual(build_prompt(tokenizer, Tensor([17]), Tensor([2, 9])), "172,9")
            self.assertEqual(build_prompt(tokenizer, Tensor([]), Tensor([2, 9])), "2,9")
            self.assertEqual(build_prompt(tokenizer, [], [2, 9]), "2,9")

            subprocess.run(command, check=True)
            self.assertEqual(source.read_text(encoding="utf-8"), patched)


if __name__ == "__main__":
    unittest.main()
