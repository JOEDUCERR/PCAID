"""
Tests for the text summary in pcaid.profiler. The summary is what the model
reads as the PC's facts, so its wording matters. (The probes themselves are
checked by running `python -m pcaid.profiler` on real machines.)

Run from the project root:
    python -m unittest discover -s tests -v
"""
import unittest

from pcaid.profiler import summarize


class SummaryTests(unittest.TestCase):
    def test_linux_version_is_not_repeated(self):
        profile = {"os": {"name": "Ubuntu 24.04.5 LTS", "version": "24.04", "family": "Linux",
                          "arch": "x86_64", "kernel": "6.8.0"}}
        first_line = summarize(profile).splitlines()[0]
        self.assertEqual(first_line, "OS: Ubuntu 24.04.5 LTS (Linux x86_64, kernel 6.8.0)")

    def test_windows_keeps_its_version_and_build(self):
        profile = {"os": {"name": "Microsoft Windows 11 Home", "version": "10.0.26200", "family": "Windows",
                          "arch": "AMD64", "build": "26200"}}
        first_line = summarize(profile).splitlines()[0]
        self.assertEqual(first_line, "OS: Microsoft Windows 11 Home 10.0.26200 (Windows AMD64, build 26200)")

    def test_missing_data_shows_unknown_instead_of_crashing(self):
        text = summarize({})
        self.assertIn("unknown", text)
        self.assertIn("GPU: none detected", text)

    def test_failed_probes_are_listed(self):
        text = summarize({"probe_errors": {"gpus": "RuntimeError: boom"}})
        self.assertIn("Could not determine: gpus", text)


if __name__ == "__main__":
    unittest.main()
