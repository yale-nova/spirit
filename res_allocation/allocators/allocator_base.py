from time import sleep
import datetime
import time
import numpy as np
from utils.logger import Logger
from tqdm import tqdm

base_search_granularity = float(1. / 200.)    # 2app: 0.125 / 4.0, 4apps: 0.125 / 8.0

class AllocatorParams:
    def __init__(self, search_granularity=0.125/2., allocation_interval_in_sec=1.0, requires_retrain=False) -> None:
        self.search_granularity = search_granularity
        self.allocation_interval_in_sec = allocation_interval_in_sec
        self.retrain = requires_retrain

class ResourceAllocator:
    def __init__(self, config_path: str, resource_scale: {} = {"cache": 1.0, "mem_bw": 1.0}, estimator=None, monitor=None, deployer=None):
        self.config_path = config_path
        self.resource_scale = resource_scale
        if estimator is None:
            raise Exception("Estimator is not provided.")
        self.estimator = estimator
        print(f"configuration: {config_path}")
        self.monitor = monitor
        self.deployer = deployer
        # Logging
        self.logger = Logger()
        self.logger.prepare_logger('spirit_ceei_allocator')
        self.parameters: AllocatorParams = None
        self.e2e_last_allocation = None

    def cleanup(self):
        self.logger.close()

    def initialize(self, param: AllocatorParams=AllocatorParams()):
        if self.monitor is None:
            raise Exception("Resource monitor is not provided.")
        if self.deployer is None:
            raise Exception("Deployer is not provided.")
        if self.estimator is None:
            raise Exception("Estimator is not provided.")

        self.logger.log_msg("Start resource allocator.")
        self.parameters = param
        # initialize the estimator (loading models, etc)
        self.estimator.initialize(self.config_path, self.parameters.search_granularity)

    def get_last_allocation(self):
        return self.e2e_last_allocation

    def start(self, max_iteration: int=1e6, init_timer: int=180, skip_monitoring=False, verification_th=0.025):
        if self.parameters is None:
            raise ValueError("Resource allocator is not initialized.")

        # pre-running
        # run algorithm to get new allocation
        allocation = self.allocate_and_parse(skip_monitoring=skip_monitoring)
        # send allocation to the controller
        time.sleep(10)  # to enforce the initial allocation
        self.deployer.deploy(allocation)
        self.e2e_last_allocation = allocation
        # set last allocation to estimator
        self.monitor.set_last_allocation(allocation)
        sleep_interval: float = self.parameters.allocation_interval_in_sec / float(self.parameters.measurements_per_alloc)
        print(f"Max iteration: {max_iteration}, Allocation interval: {self.parameters.allocation_interval_in_sec} sec, #measurement per alloc: {self.parameters.measurements_per_alloc}, sleep interval: {sleep_interval} sec", flush=True)
        print(f"Initial wait for {init_timer} seconds (cache-warm up).")
        for _ in tqdm(range(init_timer, 0, -1)):
            time.sleep(1)

        for iteration in range(int(max_iteration)):
            # run algorithm to get new allocation
            allocation = self.allocate_and_parse(skip_monitoring=skip_monitoring)
            # send allocation to the controller
            self.deployer.deploy(allocation)
            self.e2e_last_allocation = allocation
            # set last allocation to estimator
            self.monitor.set_last_allocation(allocation)
            # collect data for a while
            for _ in range(self.parameters.measurements_per_alloc):
                # sleep for a while
                sleep(sleep_interval)
                # collect data from monitor
                self.monitor.collect(verification_th)

            # consume buffered/collected data
            if not skip_monitoring:
                self.monitor.comsume_collected_data()
                if self.parameters.retrain:
                    # call estimator for profile update
                    self.estimator.update_profile(
                        self.monitor, self.parameters.init_phase_interval,
                        search_granularity=self.parameters.search_granularity,
                        retraining_interval=self.parameters.init_phase_interval, retraining_data_size=3)

            print(f"{datetime.datetime.now()} Iter: {iteration} | Search granularity: {self.parameters.search_granularity}")

    def allocate_and_parse(self, skip_monitoring=False):
        raise Exception("Not implemented.")

    def get_num_vms(self):
        # Get the number of VMs from the config
        num_vms = 1  # Default to 1 VM
        if hasattr(self.estimator, "get_raw_config") and self.estimator.get_raw_config():
            raw_config = self.estimator.get_raw_config()
            if "cluster" in raw_config and "num_vms" in raw_config["cluster"]:
                num_vms = int(raw_config["cluster"]["num_vms"])
        return num_vms