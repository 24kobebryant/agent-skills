#!/usr/bin/env python3
"""Local deterministic regressions; no UI, network, or production writes."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys

import effectctl as ctl
import inspect_douyin_runtime as runtime


class EffectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="effectctl-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "My Cat"
        self.project.mkdir()
        (self.project / "Assets").mkdir()
        (self.project / "effect.dyehpj").write_text(json.dumps({"name": "test", "projectID": "test"}))
        self.source = self.project / "Assets/main.scene"
        self.source.write_text("native fixture")
        self.package = self.root / "effect.zip"
        self.package.write_bytes(b"fixture package, not native export")
        self.artifact = self.root / "observation.txt"
        self.artifact.write_text("fixture observation, not real device evidence")
        self.ledger = self.root / "evidence.json"

    def records(self):
        return [{"layer": layer, "status": "passed", "observed_at": "2026-09-05T00:00:00Z",
                 "environment": "test fixture", "source_sha256": ctl.revision(self.project),
                 "package_sha256": ctl.digest(self.package),
                 "artifacts": [{"path": str(self.artifact), "sha256": ctl.digest(self.artifact)}]}
                for layer in ctl.LAYERS]

    def save(self, records):
        self.ledger.write_text(json.dumps({"schema_version": 1, "records": records}))

    def test_revision_changes_source_not_cache(self):
        before = ctl.revision(self.project)
        cache = self.project / "Library"
        cache.mkdir()
        (cache / "old").write_text("cached")
        self.assertEqual(before, ctl.revision(self.project))
        self.source.write_text("changed")
        self.assertNotEqual(before, ctl.revision(self.project))

    def test_evidence_stale_and_missing(self):
        self.save(self.records())
        result = ctl.evidence(self.project, self.ledger, self.package)
        self.assertEqual(result["layers"]["phone_preview"]["status"], "passed")
        self.source.write_text("changed source")
        self.assertIn("source_revision_mismatch", ctl.evidence(self.project, self.ledger, self.package)["layers"]["phone_preview"]["reasons"])
        self.artifact.unlink()
        self.assertIn("artifact_missing_or_not_absolute", ctl.evidence(self.project, self.ledger, self.package)["layers"]["source"]["reasons"])

    def test_wrong_package_and_changed_artifact(self):
        self.save(self.records())
        self.package.write_bytes(b"new package")
        self.artifact.write_text("changed evidence")
        reasons = ctl.evidence(self.project, self.ledger, self.package)["layers"]["phone_preview"]["reasons"]
        self.assertIn("package_revision_mismatch", reasons)
        self.assertIn("artifact_changed", reasons)

    def test_legacy_pass_not_migrated(self):
        self.assertEqual(ctl.evidence(self.project, self.ledger)["layers"]["source"]["status"], "not_run")

    def test_size_debug_directory_not_excluded(self):
        debug = self.project / "verification"
        debug.mkdir()
        (debug / "huge.mov").write_bytes(b"x" * 4096)
        result = ctl.inventory(self.project, 1024, self.package)
        self.assertTrue(result["over_limit"])
        self.assertEqual(result["largest_files"][0][0], "verification/huge.mov")
        self.assertEqual(result["effect_package"]["bytes"], self.package.stat().st_size)

    def test_source_frames_do_not_claim_runtime(self):
        folder = self.project / "Assets/cat.otextureseq.folder"
        folder.mkdir()
        for i in range(3):
            (folder / f"{i}.png").write_bytes(b"fixture")
        result = ctl.sequences(self.project)[0]
        self.assertEqual(result["source_frame_count"], 3)
        self.assertIsNone(result["runtime_frame_count"])
        self.assertEqual(result["runtime_status"], "not_run")

    def test_submission_and_rejection_remain_separate(self):
        record = self.records()[0]
        record.update(layer="submission", effect_id="123")
        rejection = dict(record, layer="review", status="failed")
        self.save([record, rejection])
        result = ctl.evidence(self.project, self.ledger, self.package)
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["layers"]["submission"]["status"], "passed")
        self.assertEqual(result["layers"]["review"]["status"], "failed")

    def test_cli_preflight_missing_evidence_nonzero_readonly(self):
        before = ctl.revision(self.project)
        result = subprocess.run([sys.executable, str(ctl.HERE / "effectctl.py"), "preflight", str(self.project), "--json"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["gate"], "incomplete")
        self.assertEqual(before, ctl.revision(self.project))
        self.assertFalse((self.project / ".douyin-effect").exists())

    def test_process_spaces_patched_path_and_duplicate(self):
        path = str(self.project)
        command = f"/tmp/Douyin AR Patched.app/Contents/MacOS/Douyin AR --projectPath={path} --index=1"
        self.assertEqual(runtime.project_from_command(command), path)
        ps = "\n".join(f" {pid} 1 Fri Sep  5 12:00:00 2026 {command}" for pid in (123, 124))
        output = io.StringIO()
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "argv", ["runtime", "--project", path]), patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, ps, "")), patch.object(runtime, "latest_editor_log", return_value=None), contextlib.redirect_stdout(output):
            code = runtime.main()
        self.assertEqual(code, 1)
        self.assertIn("duplicate editor writers", output.getvalue())

    def test_timeout_log_does_not_leak_payload(self):
        log = self.root / "editor.log"
        log.write_text('ETIMEDOUT cookie=VERY_SECRET\nOpen project successfully {"icon":"BASE64_SECRET"}\n')
        output = io.StringIO()
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "argv", ["runtime"]), patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), patch.object(runtime, "latest_editor_log", return_value=log), contextlib.redirect_stdout(output):
            runtime.main()
        self.assertIn("ETIMEDOUT", output.getvalue())
        self.assertNotIn("VERY_SECRET", output.getvalue())
        self.assertNotIn("BASE64_SECRET", output.getvalue())

    def test_symlink_not_silently_certified(self):
        (self.project / "Assets/external").symlink_to(self.artifact)
        result = subprocess.run([sys.executable, str(ctl.HERE / "effectctl.py"), "snapshot", str(self.project), "--json"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["unsupported_symlinks"], ["Assets/external"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
