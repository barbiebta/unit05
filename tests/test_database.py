from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unit05.database import Journal


class JournalTests(unittest.TestCase):
    def test_round_trip_and_bundle_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = Journal(Path(temporary) / "journal.sqlite")
            journal.insert(
                job_id="job-1",
                bundle_hash="a" * 64,
                bundle_name="job.zip",
                bundle_path="/working/job.zip",
                state="validated",
                timestamp="2026-08-25T00:00:00+00:00",
                metadata={"hello": "world"},
            )
            journal.update("job-1", "2026-08-25T00:00:01+00:00", state="rendering", prompt_id="prompt-1")
            record = journal.get("job-1")
            self.assertEqual(record["state"], "rendering")
            self.assertEqual(record["metadata"], {"hello": "world"})
            self.assertEqual(journal.get_by_hash("a" * 64)["job_id"], "job-1")
            journal.close()


if __name__ == "__main__":
    unittest.main()
