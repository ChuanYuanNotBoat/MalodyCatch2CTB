import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mc2ctb_core import (CIRCLE_SIZE, McChart, circle_size_for_judge,
                        validate_circle_size)
from mcz2osz_batch import convert_mcz, main, prompt_circle_size


def chart_data(version="Test"):
    return {
        "meta": {"mode": 3, "song": {"title": "Test"}, "version": version},
        "time": [{"beat": [0, 0, 1], "bpm": 120}],
        "note": [{"type": 1, "beat": [0, 0, 1], "sound": "audio.ogg"},
                 {"beat": [1, 0, 1], "x": 100},
                 {"beat": [2, 0, 1], "x": 200}],
    }


class ConversionTests(unittest.TestCase):
    def test_judge_matches_actual_catching_width(self):
        # Independent engine reference values, on a 512-wide field.
        widths = {"A": 120, "B": 105.6, "C": 96, "D": 88.32, "E": 80.64}
        for judge, width in widths.items():
            for pro in (False, True):
                with self.subTest(judge=judge, pro=pro):
                    cs = circle_size_for_judge(judge, pro)
                    osu_width = 106.75 * 0.8 * (1 - 0.14 * (cs - 5))
                    self.assertAlmostEqual(osu_width, width * (0.95 if pro else 1))
        self.assertAlmostEqual(CIRCLE_SIZE, 5.398126463700235)
        self.assertEqual(circle_size_for_judge(" e "), CIRCLE_SIZE)
        with self.assertRaises(ValueError):
            circle_size_for_judge("F")

    def test_generated_osu_default_and_custom_cs(self):
        chart = McChart(chart_data())
        default = chart.to_osu_text("audio.ogg")
        line = next(line for line in default.splitlines() if line.startswith("CircleSize:"))
        self.assertAlmostEqual(float(line.split(":")[1]), CIRCLE_SIZE, places=10)
        custom = chart.to_osu_text("audio.ogg", 7.25)
        self.assertIn("CircleSize:7.25\r\n", custom)
        self.assertEqual(default.split("[HitObjects]")[1], custom.split("[HitObjects]")[1])

    def test_invalid_cs_is_rejected(self):
        for value in ("bad", None, -0.1, 10.1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_circle_size(value)
        self.assertEqual(validate_circle_size(0), 0)
        self.assertEqual(validate_circle_size("10"), 10)
        with self.assertRaises(ValueError):
            McChart(chart_data()).to_osu_text("audio.ogg", float("nan"))

    def test_prompt_default_judge_custom_and_retry(self):
        for replies, expected in (([""], CIRCLE_SIZE), (["c"], circle_size_for_judge("C")),
                                  (["6.25"], 6.25), (["oops", "nan", "-1", "11", "0"], 0)):
            with self.subTest(replies=replies), patch("builtins.input", side_effect=replies), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(prompt_circle_size(), expected)
        with patch("builtins.input", side_effect=EOFError), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prompt_circle_size(), CIRCLE_SIZE)
        with patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prompt_circle_size(True), circle_size_for_judge("E", True))

    def test_batch_and_cli_apply_selection_to_every_chart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "test.mcz"
            output = root / "out"
            output.mkdir()
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("song/first.mc", json.dumps(chart_data("First")))
                archive.writestr("song/second.mc", json.dumps(chart_data("Second")))
                archive.writestr("song/audio.ogg", b"test audio resource")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(convert_mcz(source, output, circle_size=6.25), (2, 0, []))
            with zipfile.ZipFile(output / "Test.osz") as archive:
                charts = [name for name in archive.namelist() if name.endswith(".osu")]
                self.assertEqual(len(charts), 2)
                for name in charts:
                    self.assertIn("CircleSize:6.25", archive.read(name).decode("utf-8"))

            script = Path(__file__).with_name("mcz2osz_batch.py")
            for flags, expected in (([], CIRCLE_SIZE), (["--judge", "B", "--pro"], circle_size_for_judge("B", True)),
                                    (["--cs", "0"], 0), (["--cs", "7.25"], 7.25)):
                with self.subTest(flags=flags):
                    result = subprocess.run([sys.executable, str(script), str(source), "-o", str(output)] + flags,
                                            input="", text=True, capture_output=True,
                                            env=dict(os.environ, PYTHONIOENCODING="utf-8"), encoding="utf-8")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    with zipfile.ZipFile(output / "Test.osz") as archive:
                        for name in charts:
                            text = archive.read(name).decode("utf-8")
                            line = next(line for line in text.splitlines() if line.startswith("CircleSize:"))
                            self.assertAlmostEqual(float(line.split(":")[1]), expected, places=10)
                    self.assertTrue(source.exists())
            with patch("sys.argv", [str(script), str(source), "-o", str(output)]), \
                    patch("sys.stdin.isatty", return_value=True), \
                    patch("builtins.input", return_value="D") as ask, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
                ask.assert_called_once()
            with zipfile.ZipFile(output / "Test.osz") as archive:
                for name in charts:
                    text = archive.read(name).decode("utf-8")
                    line = next(line for line in text.splitlines() if line.startswith("CircleSize:"))
                    self.assertAlmostEqual(float(line.split(":")[1]), circle_size_for_judge("D"), places=10)
            for flags in (["--cs", "nan"], ["--cs", "11"], ["--judge", "E", "--cs", "5"], ["--pro", "--cs", "5"]):
                result = subprocess.run([sys.executable, str(script)] + flags, capture_output=True)
                self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
