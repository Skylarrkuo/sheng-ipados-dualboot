import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import draft_release as draft


class DraftTests(unittest.TestCase):
    def test_require_image_job(self):
        repo = "example/sheng"
        run = {"head_repository": {"full_name": repo}, "path": ".github/workflows/images.yml",
               "status": "completed", "conclusion": "failure", "event": "workflow_dispatch", "head_sha": "test"}
        jobs = {"jobs": [{"name": name, "conclusion": status} for name, status in
                         [("storage-tests", "success"), ("root", "success"), ("boot", "failure")]]}
        for name in ("root", "boot"):
            with patch.dict(os.environ, {"GH_REPO": repo}), patch.object(
                draft, "gh", side_effect=[json.dumps(run), json.dumps(jobs)]
            ):
                if name == "root":
                    self.assertEqual(draft.source_job("1", name), "test")
                else:
                    with self.assertRaises(ValueError):
                        draft.source_job("1", name)

    def test_reject_corrupted_or_extra_assets(self):
        with TemporaryDirectory() as dirname:
            folder = Path(dirname)
            image = folder / "boot.img"
            image.write_bytes(b"image")
            checksum = draft.digest(image)
            (folder / "SHA256SUMS").write_text(f"{checksum}  boot.img\n")
            self.assertEqual(draft.verify_files(folder, {"boot.img"}), {"boot.img": checksum})
            extra = folder / "extra"
            extra.touch()
            with self.assertRaises(ValueError):
                draft.verify_files(folder, {"boot.img"})
            extra.unlink()
            image.write_bytes(b"broken")
            with self.assertRaises(ValueError):
                draft.verify_files(folder, {"boot.img"})
