"""
Tests for pcaid.selector. They use made-up profiles, so no hardware is probed
and no model is needed.

Run from the project root (the folder that contains PROJECT.md):
    python -m unittest discover -s tests -v
"""
import unittest

from pcaid.profiler import collect_profile
from pcaid.selector import Selection, load_models, select_model

MODELS, HEADROOM = load_models()


def make_profile(free_ram=8.0, total_ram=16.0, threads=8, free_disk=50.0,
                 gpus=None, internet=True):
    """Build a profile with the same shape profiler.collect_profile() returns.

    Pass free_ram=None / free_disk=None to simulate a probe that failed, and
    internet=None to simulate a skipped network check.
    """
    profile = {
        "memory": {"total_gb": total_ram, "available_gb": free_ram},
        "cpu": {"logical_cores": threads},
        "gpus": gpus or [],
        "disk": {"temp_dir": {"free_gb": free_disk}},
    }
    if internet is not None:
        profile["network"] = {"model_hosts_reachable": {"huggingface.co": internet}}
    return profile


def gpu(vendor="NVIDIA", vram_gb=None, unified=False):
    return {"name": f"{vendor} test GPU", "vendor": vendor,
            "vram_gb": vram_gb, "unified_memory": unified}


def choose(profile, preference="auto"):
    return select_model(profile, MODELS, preference, HEADROOM)


class ConfigTests(unittest.TestCase):
    def test_models_json_loads_in_size_order(self):
        self.assertEqual([m.id for m in MODELS], ["tiny", "small", "medium", "large"])
        self.assertGreater(HEADROOM, 0)


class AutoSelectionTests(unittest.TestCase):
    def test_strong_nvidia_pc_gets_largest_model(self):
        result = choose(make_profile(free_ram=24, total_ram=32, threads=16,
                                     gpus=[gpu("NVIDIA", 12.0)]))
        self.assertEqual(result.model.id, "large")

    def test_amd_laptop_from_real_windows_run(self):
        # Numbers taken from the first real Windows run of the profiler:
        # AMD GPU with unknown VRAM, 7.4 GB RAM free, 16 threads, 53 GB disk.
        result = choose(make_profile(
            free_ram=7.4, total_ram=15.3, threads=16, free_disk=53.0,
            gpus=[gpu("AMD", 0.5), gpu("AMD", None)]))
        self.assertEqual(result.model.id, "medium")
        self.assertTrue(any("Qwen3.5 9B" in r and "9.0 GB" in r for r in result.reasons))

    def test_gpu_lets_a_bigger_model_run_than_ram_alone(self):
        # Only 3 GB RAM free, but 6 GB of NVIDIA VRAM: medium fits on the GPU.
        result = choose(make_profile(free_ram=3.0, total_ram=8.0, threads=4,
                                     gpus=[gpu("NVIDIA", 6.0)]))
        self.assertEqual(result.model.id, "medium")

    def test_apple_silicon_uses_unified_memory(self):
        result = choose(make_profile(free_ram=10.0, total_ram=16.0, threads=8,
                                     gpus=[gpu("Apple", None, unified=True)]))
        self.assertEqual(result.model.id, "large")

    def test_amd_and_intel_gpus_do_not_count_as_acceleration(self):
        result = choose(make_profile(free_ram=3.0, threads=4,
                                     gpus=[gpu("AMD", 8.0), gpu("Intel", 2.0)]))
        self.assertEqual(result.model.id, "tiny")

    def test_few_cpu_threads_hold_the_choice_back(self):
        # Plenty of RAM, but only 2 threads: only the tiny model is comfortable.
        result = choose(make_profile(free_ram=12.0, threads=2))
        self.assertEqual(result.model.id, "tiny")

    def test_nothing_comfortable_falls_back_to_smallest_with_warning(self):
        result = choose(make_profile(free_ram=5.0, threads=1))
        self.assertEqual(result.model.id, "tiny")
        self.assertTrue(any("threads" in w for w in result.warnings))


class PreferenceTests(unittest.TestCase):
    def test_light_picks_smallest_even_on_a_strong_pc(self):
        result = choose(make_profile(free_ram=24, total_ram=32, threads=16,
                                     gpus=[gpu("NVIDIA", 12.0)]), "light")
        self.assertEqual(result.model.id, "tiny")

    def test_heavy_never_exceeds_what_fits(self):
        result = choose(make_profile(free_ram=7.4, threads=16), "heavy")
        self.assertEqual(result.model.id, "medium")  # large needs 9 GB

    def test_heavy_can_pick_a_slow_model_but_warns(self):
        result = choose(make_profile(free_ram=12.0, threads=2), "heavy")
        self.assertEqual(result.model.id, "large")
        self.assertTrue(any("threads" in w for w in result.warnings))

    def test_unknown_preference_is_rejected(self):
        with self.assertRaises(ValueError):
            choose(make_profile(), "turbo")


class LimitTests(unittest.TestCase):
    def test_too_little_ram_means_no_model(self):
        result = choose(make_profile(free_ram=1.5, total_ram=4.0))
        self.assertIsNone(result.model)
        self.assertTrue(any("smallest" in r and "2.0 GB" in r for r in result.reasons))

    def test_no_model_even_when_light_is_requested(self):
        self.assertIsNone(choose(make_profile(free_ram=1.5), "light").model)

    def test_unreadable_ram_means_no_model(self):
        result = choose(make_profile(free_ram=None, total_ram=None))
        self.assertIsNone(result.model)

    def test_low_disk_limits_the_choice(self):
        # 4.0 GB free: tiny needs 3.0, small needs 4.7.
        result = choose(make_profile(free_ram=32, total_ram=32, threads=16, free_disk=4.0))
        self.assertEqual(result.model.id, "tiny")
        self.assertTrue(any("disk" in r for r in result.reasons))

    def test_too_little_disk_for_any_model(self):
        self.assertIsNone(choose(make_profile(free_disk=1.0)).model)


class WarningTests(unittest.TestCase):
    def test_unreachable_model_hosts_warn_but_do_not_block(self):
        result = choose(make_profile(internet=False))
        self.assertIsNotNone(result.model)
        self.assertTrue(any("could not be reached" in w for w in result.warnings))

    def test_skipped_network_check_adds_no_warning(self):
        self.assertEqual(choose(make_profile(internet=None)).warnings, [])

    def test_unknown_disk_warns_but_does_not_block(self):
        result = choose(make_profile(free_disk=None))
        self.assertIsNotNone(result.model)
        self.assertTrue(any("disk" in w for w in result.warnings))


class SmokeTest(unittest.TestCase):
    def test_works_with_a_real_profile_of_this_machine(self):
        # Result depends on the machine; we only check it runs and is well formed.
        result = choose(collect_profile(check_network=False))
        self.assertIsInstance(result, Selection)
        self.assertTrue(result.reasons)


if __name__ == "__main__":
    unittest.main()
