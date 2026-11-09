import json
import io
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from paper_core import Segment


class SavedJobTests(unittest.TestCase):
    def setUp(self):
        self.job_id = "a" * 32
        self.payload = {
            "id": self.job_id,
            "document_id": "document",
            "title": "Paper",
            "status": "completed",
            "message": "翻訳が完了しました",
            "progress": 100,
            "completed": 1,
            "total": 1,
            "source_lang": "en",
            "target_lang": "ja",
            "form": "plain",
            "context": "",
            "glossary": [],
            "segments": [Segment(0, 1, "Source", "訳文").__dict__],
            "cancelled": False,
        }

    def tearDown(self):
        app.jobs.pop(self.job_id, None)

    def test_loads_completed_job_from_disk(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "JOB_DIR", Path(directory)):
            (Path(directory) / f"{self.job_id}.json").write_text(
                json.dumps(self.payload, ensure_ascii=False), encoding="utf-8"
            )
            loaded = app._load_saved_job(self.job_id)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "completed")
        self.assertIsInstance(loaded["segments"][0], Segment)
        self.assertEqual(loaded["segments"][0].translation, "訳文")

    def test_marks_orphaned_running_job_as_failed(self):
        self.payload["status"] = "running"
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "JOB_DIR", Path(directory)):
            path = Path(directory) / f"{self.job_id}.json"
            path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
            loaded = app._load_saved_job(self.job_id)
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(loaded["status"], "failed")
        self.assertEqual(saved["status"], "failed")
        self.assertIn("再起動", loaded["message"])

    def test_bookmark_round_trip_on_disk(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "JOB_DIR", Path(directory)):
            job = dict(self.payload)
            job["segments"] = [Segment(0, 1, "1 Introduction", kind="heading")]
            app._set_segment_bookmark(job, 0, True)
            app._save_job(job)
            app.jobs.pop(self.job_id, None)
            loaded = app._load_saved_job(self.job_id)

        self.assertTrue(loaded["segments"][0].bookmarked)
        self.assertEqual(loaded["segments"][0].heading_level, 1)

    def test_bookmark_update_validates_value_and_index(self):
        job = {"segments": [Segment(0, 1, "Source")]}
        self.assertTrue(app._set_segment_bookmark(job, 0, True).bookmarked)
        with self.assertRaises(ValueError):
            app._set_segment_bookmark(job, 0, "true")
        with self.assertRaises(IndexError):
            app._set_segment_bookmark(job, 2, False)

    def test_clip_round_trip_and_limit(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "JOB_DIR", Path(directory)):
            job = dict(self.payload)
            job["segments"] = [Segment(0, 1, "Source")]
            for part in ("source", "translation", "note"):
                app._set_segment_clip(job, 0, part, True)
            with self.assertRaises(ValueError):
                job["segments"].append(Segment(1, 1, "Other"))
                app._set_segment_clip(job, 1, "source", True)
            app._save_job(job)
            app.jobs.pop(self.job_id, None)
            loaded = app._load_saved_job(self.job_id)

        self.assertEqual(loaded["segments"][0].clips, ["source", "translation", "note"])
        self.assertNotIn("source", app._set_segment_clip(loaded, 0, "source", False).clips)

    def test_clip_update_validates_value_and_index(self):
        job = {"segments": [Segment(0, 1, "Source")]}
        with self.assertRaises(ValueError):
            app._set_segment_clip(job, 0, "source", "true")
        with self.assertRaises(ValueError):
            app._set_segment_clip(job, 0, "invalid", True)
        with self.assertRaises(IndexError):
            app._set_segment_clip(job, 2, "source", False)


class NativeMessagingTests(unittest.TestCase):
    def test_frozen_launcher_config_selects_matching_app_home(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "dist" / "PLaMoPaperTranslator.exe"
            executable.parent.mkdir()
            executable.touch()
            app_home = root / "project"
            app_home.mkdir()
            config_root = root / "PLaMoPaperTranslator"
            config_root.mkdir()
            (config_root / "launcher-config.json").write_text(
                json.dumps({"executable": str(executable), "app_home": str(app_home)}),
                encoding="utf-8",
            )
            with patch.dict(app.os.environ, {"LOCALAPPDATA": str(root)}):
                self.assertEqual(app._configured_frozen_user_dir(executable), app_home.resolve())
                self.assertIsNone(app._configured_frozen_user_dir(root / "other.exe"))

    def test_native_message_round_trip(self):
        output = io.BytesIO()
        app._write_native_message(output, {"action": "launch", "日本語": "起動"})
        encoded = output.getvalue()
        self.assertEqual(struct.unpack("=I", encoded[:4])[0], len(encoded) - 4)
        self.assertEqual(app._read_native_message(io.BytesIO(encoded)), {"action": "launch", "日本語": "起動"})

    def test_native_invocation_accepts_only_explicit_mode_or_fixed_origin(self):
        self.assertTrue(app._is_native_messaging_invocation(["--native-messaging"]))
        self.assertTrue(app._is_native_messaging_invocation([app.CHROME_EXTENSION_ORIGIN]))
        self.assertFalse(app._is_native_messaging_invocation(["chrome-extension://wrong/"]))

    def test_native_launch_does_not_spawn_duplicate_server(self):
        request = io.BytesIO()
        app._write_native_message(request, {"action": "launch"})
        request.seek(0)
        response = io.BytesIO()

        class FakeInput:
            buffer = request

            @staticmethod
            def fileno():
                return 0

        class FakeOutput:
            buffer = response

            @staticmethod
            def fileno():
                return 1

        with (
            patch.object(app.os, "name", "test"),
            patch.object(app.sys, "stdin", FakeInput()),
            patch.object(app.sys, "stdout", FakeOutput()),
            patch.object(app, "_app_is_available", return_value=True),
            patch.object(app, "_launch_detached_server") as launcher,
        ):
            app._native_messaging_main()

        payload = app._read_native_message(io.BytesIO(response.getvalue()))
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["started"])
        launcher.assert_not_called()

    def test_frozen_detached_server_starts_as_independent_pyinstaller_instance(self):
        with (
            patch.object(app.sys, "frozen", True, create=True),
            patch.object(app.sys, "executable", r"C:\App\PLaMoPaperTranslator.exe"),
            patch.object(app.subprocess, "Popen") as popen,
        ):
            app._launch_detached_server()

        command, = popen.call_args.args
        options = popen.call_args.kwargs
        self.assertEqual(command, [r"C:\App\PLaMoPaperTranslator.exe", "--no-browser"])
        self.assertEqual(options["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")


if __name__ == "__main__":
    unittest.main()
