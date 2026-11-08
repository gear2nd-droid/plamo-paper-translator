import json
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


if __name__ == "__main__":
    unittest.main()
