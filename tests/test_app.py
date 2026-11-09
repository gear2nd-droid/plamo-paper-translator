import json
import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

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
            "tags": [],
            "paper_summary": "",
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

    def test_paper_summary_round_trip_on_disk(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "JOB_DIR", Path(directory)):
            job = dict(self.payload)
            job["segments"] = [Segment(0, 1, "Source")]
            tags, summary = app._set_job_summary(job, [" LLM ", "翻訳", "LLM"], "要点を記録する。")
            app._save_job(job)
            app.jobs.pop(self.job_id, None)
            loaded = app._load_saved_job(self.job_id)

        self.assertEqual(tags, ["LLM", "翻訳"])
        self.assertEqual(summary, "要点を記録する。")
        self.assertEqual(loaded["tags"], ["LLM", "翻訳"])
        self.assertEqual(loaded["paper_summary"], "要点を記録する。")

    def test_paper_summary_validates_limits(self):
        job = {}
        with self.assertRaises(ValueError):
            app._set_job_summary(job, "tag", "summary")
        with self.assertRaises(ValueError):
            app._set_job_summary(job, ["x" * (app.MAX_PAPER_TAG_LENGTH + 1)], "summary")
        with self.assertRaises(ValueError):
            app._set_job_summary(job, [str(index) for index in range(app.MAX_PAPER_TAGS + 1)], "summary")
        with self.assertRaises(ValueError):
            app._set_job_summary(job, [], "x" * (app.MAX_PAPER_SUMMARY_LENGTH + 1))

    def test_library_metadata_validates_and_tracks_reading_position(self):
        job = {"segments": [Segment(0, 1, "A"), Segment(1, 1, "B"), Segment(2, 1, "C")]}
        metadata = app._set_library_metadata(
            job,
            source_url="https://arxiv.org/abs/2302.14165",
            reading_status="reading",
            reading_index=2,
            mark_opened=True,
        )
        self.assertEqual(metadata["reading_index"], 2)
        self.assertEqual(metadata["reading_status"], "reading")
        self.assertTrue(metadata["last_opened_at"])
        with self.assertRaises(ValueError):
            app._set_library_metadata(job, source_url="file:///private/paper.pdf")
        with self.assertRaises(ValueError):
            app._set_library_metadata(job, reading_status="unknown")

    def test_library_search_includes_translation_and_notes(self):
        job = dict(self.payload)
        job["segments"] = [Segment(0, 1, "Original", "固有の訳語", note="確認メモ")]
        job["reading_index"] = 0
        translated = app._library_item(job, "訳語")
        noted = app._library_item(job, "確認メモ")
        self.assertIn("固有の訳語", translated["match"])
        self.assertIn("確認メモ", noted["match"])

    def test_library_lists_existing_jobs_without_migration(self):
        second_id = "b" * 32
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "JOB_DIR", Path(directory)):
            first = dict(self.payload)
            first["segments"] = [Segment(0, 1, "Neural translation", "ニューラル翻訳").__dict__]
            second = {**first, "id": second_id, "title": "Other", "segments": [Segment(0, 1, "Other").__dict__]}
            (Path(directory) / f"{self.job_id}.json").write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
            (Path(directory) / f"{second_id}.json").write_text(json.dumps(second, ensure_ascii=False), encoding="utf-8")
            app.jobs.pop(self.job_id, None)
            app.jobs.pop(second_id, None)
            items = app._list_library("ニューラル")

        app.jobs.pop(second_id, None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], self.job_id)
        self.assertEqual(items[0]["reading_status"], "unread")

    def test_library_backup_restore_adds_new_files_without_overwrite(self):
        backup = io.BytesIO()
        job_payload = {**self.payload, "segments": [Segment(0, 1, "Backup source", "復元").__dict__]}
        with zipfile.ZipFile(backup, "w") as bundle:
            bundle.writestr(
                "backup-manifest.json",
                json.dumps({"format": "plamo-paper-translator-library", "version": 1}),
            )
            bundle.writestr(f"data/jobs/{self.job_id}.json", json.dumps(job_payload, ensure_ascii=False))
            bundle.writestr("data/uploads/document.md", "# Backup source")
        backup.seek(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for child in ("jobs", "uploads", "assets"):
                (root / child).mkdir()
            with (
                patch.object(app, "DATA_DIR", root),
                patch.object(app, "JOB_DIR", root / "jobs"),
                patch.object(app, "UPLOAD_DIR", root / "uploads"),
                patch.object(app, "ASSET_DIR", root / "assets"),
            ):
                result = app._restore_library_backup(backup)
                first_text = (root / "jobs" / f"{self.job_id}.json").read_text(encoding="utf-8")
                backup.seek(0)
                repeated = app._restore_library_backup(backup)
                second_text = (root / "jobs" / f"{self.job_id}.json").read_text(encoding="utf-8")

        self.assertEqual(result["restored_jobs"], 1)
        self.assertEqual(result["restored_files"], 2)
        self.assertEqual(repeated["restored_files"], 0)
        self.assertEqual(repeated["skipped_files"], 2)
        self.assertEqual(first_text, second_text)

    def test_library_backup_restore_rejects_path_traversal(self):
        backup = io.BytesIO()
        with zipfile.ZipFile(backup, "w") as bundle:
            bundle.writestr(
                "backup-manifest.json",
                json.dumps({"format": "plamo-paper-translator-library", "version": 1}),
            )
            bundle.writestr("data/jobs/../../escape.json", "{}")
        backup.seek(0)
        with self.assertRaises(ValueError):
            app._validated_backup_members(zipfile.ZipFile(backup))

    def test_manual_translation_keeps_revision_history(self):
        job = dict(self.payload)
        job["updated_at"] = "2026-11-09T00:00:00+00:00"
        job["segments"] = [Segment(0, 1, "Source", "最初の訳", translation_origin="model")]
        segment = app._replace_segment_translation(job, 0, "修正した訳", "manual")

        self.assertEqual(segment.translation, "修正した訳")
        self.assertEqual(segment.translation_origin, "manual")
        self.assertEqual(segment.translation_revisions[0]["translation"], "最初の訳")
        self.assertEqual(segment.translation_revisions[0]["origin"], "model")
        with self.assertRaises(ValueError):
            app._replace_segment_translation(job, 0, " ", "manual")

    def test_single_segment_retranslation_reuses_job_options(self):
        job = dict(self.payload)
        job.update({"status": "completed", "context": "研究分野", "glossary": [{"source": "term", "target": "用語"}]})
        job["segments"] = [Segment(0, 1, "Translate this term.", "旧訳", paragraph=1)]
        fake_translator = Mock()
        fake_translator.health.return_value = True
        fake_translator.translate.return_value = "新しい訳"

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(app, "JOB_DIR", Path(directory)),
            patch.object(app, "translator", fake_translator),
        ):
            segment = app._retranslate_segment(self.job_id, job, 0)

        self.assertEqual(segment.translation, "新しい訳")
        self.assertEqual(segment.translation_revisions[0]["translation"], "旧訳")
        fake_translator.translate.assert_called_once()
        options = fake_translator.translate.call_args.kwargs
        self.assertEqual(options["target_lang"], "ja")
        self.assertEqual(options["glossary"], [{"source": "term", "target": "用語"}])
        self.assertIn("研究分野", options["context"])

    def test_translation_quality_check_finds_common_issues(self):
        job = dict(self.payload)
        job["source_lang"] = "en"
        job["target_lang"] = "ja"
        job["glossary"] = [{"source": "term", "target": "用語"}]
        job["segments"] = [
            Segment(0, 1, "This sentence has no translation."),
            Segment(1, 1, "Accuracy was 42.0% for this term.", "モデル精度を示した。"),
            Segment(2, 1, "This sentence remains exactly the same after translation.", "This sentence remains exactly the same after translation."),
            Segment(3, 1, "x = 42", "x = 42", kind="equation", assets=["/api/assets/a/equation.png"]),
        ]

        issues = app._translation_quality_issues(job)
        issue_types = {(issue["index"], issue["type"]) for issue in issues}

        self.assertIn((0, "missing"), issue_types)
        self.assertIn((1, "number"), issue_types)
        self.assertIn((1, "glossary"), issue_types)
        self.assertIn((2, "unchanged"), issue_types)
        self.assertFalse(any(issue["index"] == 3 for issue in issues))

    def test_source_url_is_suggested_from_arxiv_or_doi(self):
        self.assertEqual(
            app._suggest_source_url("2302.14165v2.pdf", [Segment(0, 1, "Paper")]),
            "https://arxiv.org/abs/2302.14165",
        )
        self.assertEqual(
            app._suggest_source_url("paper.pdf", [Segment(0, 1, "DOI: 10.1145/3544548.3580816.")]),
            "https://doi.org/10.1145/3544548.3580816",
        )


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


class LlamaProcessTests(unittest.TestCase):
    def test_start_waits_for_existing_loading_process(self):
        process = app.LlamaProcess()
        process.process = Mock()
        process.process.poll.return_value = None
        fake_translator = Mock()
        fake_translator.health.side_effect = [False, True]
        with (
            patch.object(app, "translator", fake_translator),
            patch.object(app.subprocess, "Popen") as popen,
        ):
            process.start()

        popen.assert_not_called()
        self.assertEqual(process.error, "")


if __name__ == "__main__":
    unittest.main()
