# Run from res_allocation: python -m unittest discover -s tests -v
import os
import tempfile
import unittest

from allocators.spirit_allocator import SpiritAllocator, SpiritAllocatorParams
from allocators.static_allocator import StaticAllocator, StaticAllocatorParams


class FakeEstimator:
    """Exact utilities f(cache, mem_bw) in place of the runtime estimator."""

    def __init__(self, utilities, num_vms=1):
        self.utilities = utilities
        self.num_vms = num_vms

    def initialize(self, config_path, search_granularity):
        pass

    def get_app_ids(self):
        return list(self.utilities)

    def get_app_num(self):
        return len(self.utilities)

    def get_raw_config(self):
        return {"cluster": {"num_vms": self.num_vms}}

    def get_estimation(self, user_id, cache, mem_bw):
        return self.utilities[user_id](cache, mem_bw)


class FakeMonitor:
    def __init__(self, vm_to_app_map):
        self.vm_to_app_map = vm_to_app_map
        self.collected_data = {}

    def get_vm_to_app_mapping(self):
        return self.vm_to_app_map

    def set_records(self, records):
        self.collected_data = {
            app_id: {"total_record": n} for app_id, n in records.items()
        }


def make_spirit(app_ids, vm_to_app_map, num_vms=1):
    # Even-indexed apps use only cache, odd-indexed apps only bandwidth, so
    # any dynamic allocation moves away from the static split.
    utilities = {
        app_id: (lambda c, b: c) if i % 2 == 0 else (lambda c, b: b)
        for i, app_id in enumerate(app_ids)
    }
    monitor = FakeMonitor(vm_to_app_map)
    allocator = SpiritAllocator(
        "unused.json",
        resource_scale={"cache": 1000.0, "mem_bw": 10.0},
        estimator=FakeEstimator(utilities, num_vms),
        monitor=monitor,
        deployer=object(),
    )
    allocator.initialize(SpiritAllocatorParams(30.0))
    return allocator, monitor


def make_static(app_ids, num_vms):
    allocator = StaticAllocator(
        "unused.json",
        resource_scale={"cache": 1000.0, "mem_bw": 10.0},
        estimator=FakeEstimator(dict.fromkeys(app_ids), num_vms),
        monitor=FakeMonitor({}),
        deployer=object(),
    )
    allocator.initialize(StaticAllocatorParams(30.0))
    return allocator


class AllocatorTestCase(unittest.TestCase):
    def setUp(self):
        # The allocators log to ./logs and clear old logs there.
        cwd = os.getcwd()
        tmp = tempfile.TemporaryDirectory()
        os.chdir(tmp.name)
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir, cwd)


class TestStartupSplit(AllocatorTestCase):
    # Until the monitor knows which VM hosts which app, the apps are split into
    # contiguous blocks, one per VM. No block may get more than one VM holds.
    ALLOCATORS = {
        "spirit": lambda app_ids, num_vms: make_spirit(app_ids, {}, num_vms)[0],
        "static": make_static,
    }
    CASES = ((5, 2), (7, 3), (25, 4), (24, 4), (3, 4))

    def test_allocation_fits_the_cluster(self):
        for kind, make_allocator in self.ALLOCATORS.items():
            for num_apps, num_vms in self.CASES:
                with self.subTest(allocator=kind, num_apps=num_apps, num_vms=num_vms):
                    app_ids = list(range(1, num_apps + 1))
                    allocation = make_allocator(app_ids, num_vms).allocate_and_parse()
                    self.assertEqual(len(allocation), num_apps)
                    self.assertLessEqual(
                        sum(alloc["cache"] for alloc in allocation.values()),
                        num_vms * 1000,
                    )
                    self.assertLessEqual(
                        sum(alloc["mem_bw"] for alloc in allocation.values()),
                        num_vms * 10 * 1024,
                    )


class TestSpiritWarmup(AllocatorTestCase):
    # Spirit leaves the static split only once every app has monitor data.
    CASES = (
        ([1, 2], {1: 50, 2: 50}, True),
        ([2, 3], {2: 50, 3: 50}, True),
        ([1, 2], {1: 50, 2: 0}, False),
        ([2, 3], {2: 50, 3: 0}, False),
    )

    def test_goes_dynamic_once_every_app_has_data(self):
        for app_ids, records, goes_dynamic in self.CASES:
            with self.subTest(app_ids=app_ids, records=records):
                allocator, monitor = make_spirit(app_ids, {0: list(app_ids)})
                static = allocator.allocate_and_parse()  # no data yet
                monitor.set_records(records)
                epochs = [allocator.allocate_and_parse() for _ in range(5)]
                self.assertEqual(any(a != static for a in epochs), goes_dynamic)


if __name__ == "__main__":
    unittest.main()
