import os
import logging
from utils.plotting import *
import json

class RuntimeEstimator:
    def __init__(self, estimation_cache=False, init_search_range=1.0, resource_scale={"cache": 1.0, "mem_bw": 1.0}):
        self.profiles_config = None
        self.redis_config = None
        self.profiles = {}
        self.online_learned_records = {}
        self.default_training_data = {}
        self.retraining_counter = 0
        self.profile_data_over_time = {}
        self.l3miss_clip = {}
        self.iteration_clip = {}
        self.estimation_cache = estimation_cache
        self.search_range = init_search_range
        self.resource_scale = resource_scale
        self.monitor = None
        self.allocator = None
        self.search_granularity = 0.01
        self.raw_config = None

    def initialize(self, config_path: str, search_granularity: float):
        # load configuration
        self.search_granularity = search_granularity
        self._read_config(config_path)

    def get_config(self):
        return self.profiles_config

    def get_raw_config(self):
        return self.raw_config

    def cleanup(self):
        pass

    # read config file
    def _read_config(self, config_file="config.json", verbose=True):
        with open(config_file) as f:
            config_data = json.load(f)
            self.raw_config = config_data
            if config_data is not None and "profiles" in config_data:
                if verbose:
                    print(f"Profiles: {config_data['profiles']}")
                self.profiles_config = config_data["profiles"]
                for entry in self.profiles_config:
                    if 'user_id' not in entry or 'file' not in entry:
                        raise KeyError("Invalid profile configuration.")
                    if 'clip_l3miss' in entry:
                        self.l3miss_clip[entry['user_id']] = entry['clip_l3miss']
                    if 'clip_iteration' in entry:
                        self.iteration_clip[entry['user_id']] = entry['clip_iteration']

    def get_app_ids(self):
        return [entry["user_id"] for entry in self.profiles_config]

    def get_app_num(self):
        return len(self.profiles_config)

    def set_allocator(self, allocator):
        self.allocator = allocator

    def set_monitor(self, monitor):
        self.monitor = monitor

    def _estimate_slow_down(self, current_miss_rate, target_miss_rate,
                       current_bw_mbps, target_bw_mbps,
                       current_alloc_bw_mbps,
                       loc_to_ret_slowdown = 100,
                       margin = 0.8):
        target_bw_mbps = max(1, target_bw_mbps)
        current_bw_mbps = max(1, current_bw_mbps)

        if current_bw_mbps <= current_alloc_bw_mbps * margin or current_bw_mbps > target_bw_mbps:
            bw_est = current_bw_mbps * (target_miss_rate / current_miss_rate)
        else:
            if current_bw_mbps >= current_alloc_bw_mbps * margin:  # adjustment considering the margin
                current_bw_mbps = current_alloc_bw_mbps
            bw_est = target_bw_mbps * min(1., current_bw_mbps/current_alloc_bw_mbps) * (target_miss_rate / current_miss_rate)

        cur_slowdown = 1. + current_miss_rate * loc_to_ret_slowdown * max(1, (bw_est / current_alloc_bw_mbps))
        slowdown = 1. + target_miss_rate * loc_to_ret_slowdown * max(1, (bw_est / target_bw_mbps))

        return slowdown / cur_slowdown, bw_est

    def estimate_miss_rate(self, mrc_data, cache_size):
        """
        Estimate the miss rate for a given cache size using linear interpolation.

        Parameters:
        - mrc_data: List of [cache_size, miss_rate] pairs, sorted by cache_size.
        - cache_size: The cache size for which to estimate the miss rate.

        Returns:
        - Estimated miss rate for the given cache size.
        """
        cache_size = float(cache_size)

        # Separate the data into two lists for easier processing
        cache_sizes = [point[0] for point in mrc_data]
        miss_rates = [point[1] for point in mrc_data]

        # Handle cache sizes outside the provided data range with linear extrapolation
        if cache_size <= cache_sizes[0]:
            x0, y0 = cache_sizes[0], miss_rates[0]
            x1, y1 = cache_sizes[1], miss_rates[1]
        elif cache_size >= cache_sizes[-1]:
            x0, y0 = cache_sizes[-2], miss_rates[-2]
            x1, y1 = cache_sizes[-1], miss_rates[-1]
        else:
            # Find the interval [x0, x1] where x0 <= cache_size <= x1
            for i in range(1, len(cache_sizes)):
                if cache_size <= cache_sizes[i]:
                    x0, y0 = cache_sizes[i - 1], miss_rates[i - 1]
                    x1, y1 = cache_sizes[i], miss_rates[i]
                    break

        # Perform linear interpolation (or extrapolation if outside the range)
        estimated_miss_rate = y0 + (y1 - y0) * (cache_size - x0) / max(x1 - x0, 1e-6)
        if estimated_miss_rate < 0 or estimated_miss_rate > 1:
            print(f"MR error: {x0}, {y0} : {x1}, {y1} -> {cache_size} =>  {estimated_miss_rate}")
        return estimated_miss_rate

    def get_estimation(self, user_id, cache_in_mb, bw_in_gbps):
        # Check allocator and the current allocation
        if self.allocator is None:
            print("Allocator is not set.")
            return -1.
        current_alloc = self.allocator.get_last_allocation()
        if user_id not in current_alloc:
            print(f"User {user_id} not found in the current allocation.")
            return -1.
        current_alloc = current_alloc[user_id]
        # print(f"Current allocation: {current_alloc['cache']} MB, {current_alloc['mem_bw']} Mbps -> target: {cache_in_mb} MB, {bw_in_gbps} Gbps")

        # Check the monitor and MRC
        if self.monitor is None:
            print("Monitor is not set.")
            return -1.
        last_mrc = self.monitor.get_last_mrc(user_id)
        if not last_mrc:
            print(f"App: {user_id}, Last MRC is not available.")
            return -1.
        last_usage = self.monitor.get_last_usage(user_id)
        if not last_usage or 'cache' not in last_usage or 'mem_bw' not in last_usage:
            print(f"App: {user_id}, Last usage is not available.")
            return -1.
        cur_mr = self.estimate_miss_rate(last_mrc, current_alloc['cache'])
        tar_mr = self.estimate_miss_rate(last_mrc, cache_in_mb)
        if cur_mr < 0 or tar_mr < 0:
            print(f"Miss rate estimation failed: {cur_mr}, {tar_mr}")
            return -1.
        if cur_mr <= 1e-12:
            cur_mr = 1e-12
        if tar_mr <= 1e-12:
            tar_mr = 1e-12
        # Estimation based on the collected data
        slowdown, bw_est = self._estimate_slow_down(cur_mr, tar_mr, last_usage['mem_bw'], bw_in_gbps * 1024., current_alloc['mem_bw'])
        relative_perf = 1. / max(1e-4, slowdown)
        print(f"App: {user_id} | Last usage: {last_usage}, Alloc: {current_alloc}, Tar: {cache_in_mb}, {bw_in_gbps} | Est MR: {cur_mr} -> {tar_mr}, Est bw: {bw_est}, Perf: {relative_perf}")  # in MB, Mbps

        return relative_perf

    def get_util_from_allocation(self, allocation: {}):
        utility = {}
        for user_id in allocation.keys():
            util = self.get_estimation(
                    user_id, allocation[user_id]["cache"] * self.resource_scale["cache"],
                    allocation[user_id]["mem_bw"] * self.resource_scale["mem_bw"])
            utility[user_id] = util
        return utility

    def update_profile(self, monitor, num_samples_for_init_phase, retraining_interval=5, search_granularity=0.01, retraining_data_size=7):
        print(f"Not implemented yet :: {self.update_profile.__name__}")

    def get_sensitivity(self):
        sensitivity = {}
        for entry in self.profiles_config:
            if 'user_id' not in entry or 'sensitivity' not in entry:
                continue
            if entry['sensitivity'] not in sensitivity:
                sensitivity[entry['sensitivity']] = []
            sensitivity[entry['sensitivity']].append(entry['user_id'])
        if len(sensitivity) == 0:
            raise ValueError("No sensitivity is specified. Check configuration json (default: config.json)")
        return sensitivity

    @staticmethod
    def initialize_logger(logger_id):
        # check directory for logging and create one if it does not exist
        log_dir = './logs'
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        else:
            # remove any existing log files
            for file in os.listdir(log_dir):
                if file.startswith('worker_'):
                    os.remove(os.path.join(log_dir, file))
        # initiate logger instance
        logger = logging.getLogger('worker_{}'.format(logger_id))
        handler = logging.FileHandler('{}/worker_{}.log'.format(log_dir, logger_id))
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        return logger
