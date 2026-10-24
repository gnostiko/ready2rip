# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for Melton argv builder (no HandBrake required)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ready2rip.transcode.command import OptionError, build_argv, options_from_dict


class TranscodeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media = Path(self.tmp.name) / "sample.mkv"
        self.media.write_bytes(b"not-really-mkv")
        self.out = Path(self.tmp.name) / "out"
        self.out.mkdir()

    def test_default_transcode_omits_default_flags(self) -> None:
        opts = options_from_dict(
            {
                "tool": "transcode",
                "inputs": [str(self.media)],
                "output_dir": str(self.out),
            }
        )
        argv, cwd = build_argv(opts)
        self.assertEqual(cwd, self.out.resolve())
        self.assertIn("transcode-video.rb", argv[1])
        self.assertNotIn("--mode", argv)
        self.assertNotIn("--audio-mode", argv)
        self.assertNotIn("--add-audio", argv)
        self.assertEqual(argv[-1], str(self.media.resolve()))

    def test_audio_language_and_mp4_extra(self) -> None:
        opts = options_from_dict(
            {
                "tool": "transcode",
                "inputs": [str(self.media)],
                "mode": "nvenc-hevc",
                "audio_mode": "aac",
                "add_audio": ["eng"],
                "container": "av_mp4",
                "burn_subtitle": "none",
                "dry_run": True,
            }
        )
        argv, _ = build_argv(opts)
        joined = " ".join(argv)
        self.assertIn("--mode nvenc-hevc", joined)
        self.assertIn("--audio-mode aac", joined)
        self.assertIn("--add-audio eng", joined)
        self.assertIn("--burn-subtitle none", joined)
        self.assertIn("--extra format=av_mp4", joined)
        self.assertIn("--dry-run", argv)

    def test_rejects_unknown_mode(self) -> None:
        opts = options_from_dict(
            {"tool": "transcode", "inputs": [str(self.media)], "mode": "magic"}
        )
        with self.assertRaises(OptionError):
            build_argv(opts)

    def test_detect_crop(self) -> None:
        opts = options_from_dict(
            {
                "tool": "detect_crop",
                "inputs": [str(self.media)],
                "crop_mode": "auto",
            }
        )
        argv, cwd = build_argv(opts)
        self.assertIsNone(cwd)
        self.assertIn("detect-crop.rb", argv[1])
        self.assertIn("--mode", argv)
        self.assertIn("auto", argv)


if __name__ == "__main__":
    unittest.main()
