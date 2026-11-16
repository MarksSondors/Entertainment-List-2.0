"""The champion model file must be published without copying ~400 MB twice.

``save_bundle`` and the model upload endpoint used to ``shutil.copy2`` the
bundle into ``svd_model_latest.pkl`` and ``svd_model.pkl``, so one champion
cost three copies on disk (~1.2 GB) and a reader could catch a half-written
pickle while the copy was running.
"""
import os
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from movies.services.recommender.model_io import _publish_bundle


class PublishBundleTests(SimpleTestCase):
    def test_publishing_shares_the_inode_instead_of_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'svd_model_20260101_000000.pkl'
            src.write_bytes(b'model bytes')
            dst = Path(tmp) / 'svd_model_latest.pkl'

            _publish_bundle(src, dst)

            self.assertEqual(dst.read_bytes(), b'model bytes')
            self.assertEqual(os.stat(src).st_ino, os.stat(dst).st_ino)

    def test_publishing_replaces_the_previous_champion_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / 'svd_model_20250101_000000.pkl'
            old.write_bytes(b'old model')
            dst = Path(tmp) / 'svd_model_latest.pkl'
            _publish_bundle(old, dst)

            new = Path(tmp) / 'svd_model_20260101_000000.pkl'
            new.write_bytes(b'new model')
            _publish_bundle(new, dst)

            self.assertEqual(dst.read_bytes(), b'new model')
            # The old snapshot is still readable on its own.
            self.assertEqual(old.read_bytes(), b'old model')
            # No temporary file is left behind.
            leftovers = [p.name for p in Path(tmp).iterdir() if 'publish-tmp' in p.name]
            self.assertEqual(leftovers, [])

    def test_publishing_leaves_no_temporary_file_when_it_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / 'svd_model_latest.pkl'
            with self.assertRaises(OSError):
                _publish_bundle(Path(tmp) / 'does-not-exist.pkl', dst)
            leftovers = [p.name for p in Path(tmp).iterdir() if 'publish-tmp' in p.name]
            self.assertEqual(leftovers, [])
