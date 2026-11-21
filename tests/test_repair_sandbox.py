from __future__ import annotations
import unittest
from verox_brain.repair_sandbox import PatchRejected, _validate_patch

class RepairSandboxTests(unittest.TestCase):
    def test_accepts_allowlisted_python_diff(self):
        patch = "diff --git a/verox_brain/example.py b/verox_brain/example.py\n--- a/verox_brain/example.py\n+++ b/verox_brain/example.py\n@@ -1 +1 @@\n-x = 1\n+y = 2\n"
        self.assertEqual(_validate_patch(patch), ["verox_brain/example.py"])

    def test_rejects_path_traversal(self):
        patch = "diff --git a/verox_brain/../../.env b/verox_brain/../../.env\n"
        with self.assertRaises(PatchRejected):
            _validate_patch(patch)

    def test_rejects_workflow_and_dependency_changes(self):
        for path in (".github/workflows/test.yml", "requirements.txt"):
            with self.subTest(path=path):
                patch = f"diff --git a/{path} b/{path}\n"
                with self.assertRaises(PatchRejected):
                    _validate_patch(patch)

    def test_rejects_additions_and_deletions(self):
        with self.assertRaises(PatchRejected):
            _validate_patch("diff --git a/verox_brain/new.py b/verox_brain/new.py\n--- /dev/null\n+++ b/verox_brain/new.py\n")

if __name__ == "__main__":
    unittest.main()
