from __future__ import annotations

import ast
import unittest
from pathlib import Path


class TestB4CosUploadHeaders(unittest.TestCase):
    def test_put_object_header_values_are_strings(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/cos-b4-upload-once.yml"
        ).read_text(encoding="utf-8")
        step = workflow.split(
            "      - name: Reuse or upload exact installer, then verify COS and public HTTPS\n", 1
        )[1]
        source = step.split("          python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        tree = ast.parse("\n".join(line[10:] for line in source.splitlines()))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "put_object"
        ]
        self.assertEqual(len(calls), 1)

        class RecordingClient:
            def put_object(self, **kwargs):
                return kwargs

        size = 1623675349
        sha = "a" * 64
        args = eval(
            compile(ast.Expression(calls[0]), "<put_object>", "eval"),
            {"__builtins__": {"str": str}},
            {
                "client": RecordingClient(), "bucket": "test-bucket",
                "key": "test.exe", "stream": object(), "size": size, "sha": sha,
            },
        )
        self.assertEqual(args["ContentLength"], str(size))
        for name in ("ContentLength", "ContentType", "ContentDisposition", "ForbidOverwrite"):
            self.assertIsInstance(args[name], str, name)
        self.assertEqual(args["Metadata"], {"x-cos-meta-sha256": sha})
        self.assertEqual(args["ForbidOverwrite"], "true")


if __name__ == "__main__":
    unittest.main()
