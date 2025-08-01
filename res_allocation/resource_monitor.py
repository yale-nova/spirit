import statistics
import copy
import requests
from utils.logger import Logger
import re
import json
import numpy as np

class ResourceMonitor:
    def __init__(self, config):
        self.config = config

    def initialize(self, config: str):
        raise NotImplementedError

    def collect(self, verification_th):
        raise NotImplementedError

class MemcachedMindMonitor(ResourceMonitor):
    def __init__(self, config):
        super().__init__(config)
        self.logger = Logger()
        self.logger.prepare_logger('memcached_mind_monitor')
        self.load_config()
        self.collected_data = {}
        self.buffered_data = {}
        self.buffered_mrc = {}

        # the structure will be like this:
        # {
        #   "1" as application or user id: {
        #       "total_record": value,
        #       "total_datapoint": number of different cache and mem_bw values,
        #       "datapoints": { cache: { mem_bw: [l3miss as array] } }

        self.collection_iteration_count = 0
        self.last_allocation = {}
        self.last_usage = {}
        self.num_buffered_data = 0
        # allocation in sec
        self.allocation_interval_in_sec = -1
        if self.config.allocation_parameters is not None and "allocation_interval_in_sec" in self.config.allocation_parameters:
            self.allocation_interval_in_sec = int(self.config.allocation_parameters["allocation_interval_in_sec"])
        self.recent_measurement = {}
        self.recent_window = 24 # 2 min for 5 sec alloc interval
        self.recent_data_count = 2
        self.vm_to_app_map = {}  # Mapping from VM ID to list of App IDs

    def cleanup(self):
        self.logger.close()

    def load_config(self):
        self.logger.log_msg(f"Monitor configuration: deploy URL: {self.config.url}{self.config.collect_route}")
        self.logger.log_msg(f"Full config: {self.config}")

    def set_last_allocation(self, allocation: dict):
        self.last_allocation = allocation

    def get_last_mrc(self, user_id):
        try:
            return self.collected_data[user_id]["last_mrc"]
        except KeyError:
            if user_id not in self.collected_data:
                # self.logger.log_msg(f"User ID {user_id} is not found in the collected data.")
                pass
            elif "last_mrc" not in self.collected_data[user_id]:
                self.logger.log_msg(f"Last MRC is not found for user ID {user_id}.")
            return []

    def get_last_usage(self, user_id):
        try:
            return self.last_usage[user_id]
        except KeyError:
            if user_id not in self.last_usage:
                self.logger.log_msg(f"User ID {user_id} is not found in the last usage data.")
            return None

    def initialize(self, config: str):
        pass

    def has_enough_data(self, user_id, cache_in_mb, mem_bw_in_mbps, raw_rounded=8):
        cache_rounded = cache_in_mb // raw_rounded * raw_rounded
        mem_bw_rounded = mem_bw_in_mbps // raw_rounded * raw_rounded

        if user_id not in self.recent_measurement:
            return False
        data_cnt = 0
        for data in self.recent_measurement[user_id]:
            for entry in data:
                if int(entry["cache_size"]) == int(cache_rounded) and int(entry["mem_bw_in_gbps"]) == int(mem_bw_rounded):
                    data_cnt += 1
        return data_cnt >= self.recent_data_count

    def get_num_datapoints(self, user_id):
        if user_id not in self.collected_data:
            return 0
        return self.collected_data[user_id]["total_datapoint"]

    def get_app_ids(self):
        return list(self.collected_data.keys())

    def get_vm_to_app_mapping(self):
        """
        Returns the current mapping of VM IDs to App IDs

        :return: Dictionary mapping VM ID to list of App IDs
        """
        return self.vm_to_app_map

    def get_apps_for_vm(self, vm_id):
        """
        Returns the list of App IDs running on a specific VM

        :param vm_id: The VM ID to lookup
        :return: List of App IDs running on the VM or empty list if VM not found
        """
        return self.vm_to_app_map.get(int(vm_id), [])

    def get_datapoints_base(self, user_id):
        assert user_id in self.collected_data, f"User ID {user_id} is not found in the collected data."
        res_data = copy.deepcopy(self.collected_data[user_id])
        # Convert the "data points" into old form (list per cache, bw)
        res_data["datapoints"] = {}
        for cache_size, cache_data in self.collected_data[user_id]["datapoints"].items():
            for mem_bw_in_mbps, perf_list in cache_data.items():
                for perf in perf_list:
                    res_data["datapoints"].setdefault(cache_size, {})
                    if mem_bw_in_mbps not in res_data["datapoints"][cache_size]:
                        res_data["datapoints"][cache_size][mem_bw_in_mbps] = []
                    # perf format: (value, timestamp)
                    res_data["datapoints"][cache_size][mem_bw_in_mbps].append(perf[0])

        return res_data

    def get_datapoints_with_memory_limit(self, user_id, memory_limit):
        # Convert the "data points" into old form (list per cache, bw)
        res_data = {}
        for cache_size, cache_data in self.collected_data[user_id]["datapoints"].items():
            for mem_bw_in_mbps, perf_list in cache_data.items():
                for perf in perf_list:
                    res_data.setdefault(cache_size, {})
                    if perf[1] < self.collected_data[user_id]["last_update_iteration"] - memory_limit:
                        continue
                    if mem_bw_in_mbps not in res_data[cache_size]:
                        res_data[cache_size][mem_bw_in_mbps] = []
                    # perf format: (value, timestamp)
                    res_data[cache_size][mem_bw_in_mbps].append(perf[0])
        return res_data

    def get_num_recent_data(self, user_id):
        if user_id not in self.recent_measurement:
            return 0
        data_points_set = set()
        for entry in self.recent_measurement[user_id]:
            for record in entry:
                data_points_set.add((record['cache_size'], record['mem_bw_in_gbps']))
        return len(data_points_set)

    def collect(self, verification_th=0.025):
        url = f"{self.config.url}{self.config.collect_route}"
        headers = {'Content-Type': 'application/json'}
        self.logger.log_msg(f"Sending a data collect request to {url} w/ {headers}")
        response = requests.get(url, headers=headers)
        if response.status_code in [200, 202]:
            # decode response's content (it's b'...')
            data = response.content.decode('utf-8')
            if data:
                if data.strip() == "" or data.strip() == '\"\"':
                    self.logger.log_msg("Received empty string.")
                    return
                # parse the data
                print(data)     # to the main log
                data = self.parse_log_entries(data)
                self.buffer_collected_data(data, verification_th=verification_th)
            else:
                self.logger.log_msg(f"No allocation found: {data}")

    def _weighted_update_list(self, old_list, new_list, alpha=0.95):
        """
        Updates the second value of each pair using an exponentially weighted moving average.
        The first value of each pair (e.g., 256, 512, ...) is preserved.

        Args:
            old_list (list of lists): Previous list of [key, value] pairs.
            new_list (list of lists): Current list of [key, value] pairs.
            alpha (float): Smoothing factor (0 < alpha <= 1).
            logger (optional): Logger object for logging messages.

        Returns:
            list of lists: Updated list with the first value unchanged and the second value smoothed.
        """
        if not new_list:
            self.logger.log_msg("New list is empty.")
            return old_list

        # Ensure old_list is not empty
        if not old_list:
            old_list = [[key, 0.0] for key, _ in new_list]

        # Convert old_list and new_list to NumPy arrays for efficiency
        old_array = np.array(old_list, dtype=float)
        new_array = np.array(new_list, dtype=float)

        # Ensure keys match
        if not np.array_equal(old_array[:, 0], new_array[:, 0]):
            self.logger.log_msg("Keys in old_list and new_list must match.")
            return old_list

        # Perform the weighted update on the second column (values)
        updated_values = alpha * new_array[:, 1] + (1 - alpha) * old_array[:, 1]

        # Recombine keys with updated values
        updated_array = np.column_stack((old_array[:, 0], updated_values))

        # Convert back to list of lists
        updated_list = updated_array.tolist()
        return updated_list

    def _weighted_update_value(self, old_value, new_value, alpha=0.95):
        """
        Updates the second value of each pair using an exponentially weighted moving average.
        The first value of each pair (e.g., 256, 512, ...) is preserved.

        Args:
            old_value (float): Previous value.
            new_value (float): Current value.
            alpha (float): Smoothing factor (0 < alpha <= 1).
            logger (optional): Logger object for logging messages.

        Returns:
            float: Updated value.
        """
        import inspect
        if new_value is None:
            self.logger.log_msg(f"{inspect.currentframe().f_code.co_name} | New value is None.")
            return old_value

        # Ensure old_value is not None
        if old_value is None:
            return new_value

        # Perform the weighted update
        updated_value = alpha * new_value + (1 - alpha) * old_value

        return updated_value

    def buffer_collected_data(
        self, data: list,
        use_raw=False, raw_rounded=8, # raw means it is the actual use, not allocated amount
        skip_noise_after_alloc=1,
        verification_th=0.025   # for KVS (Meta trace), the cache is rough, so the value should be greater
        ):
        if not isinstance(data, list) or len(data) == 0:
            return
        self.num_buffered_data += 1 # based on iteration
        last_skip_data = {}
        # skip the first few records after allocation to avoid noise
        if self.num_buffered_data <= skip_noise_after_alloc\
            and (self.allocation_interval_in_sec < 0 or self.allocation_interval_in_sec > skip_noise_after_alloc * 5 * 2):
            # if alloc interval is lower than the skip_noise_after_alloc (x 5 seconds per record) x 2 recods, we do not skip
            for entry in data:
                user_id = entry.get("app_id")
                if user_id is None or entry.get("cache_size") is None or entry.get("bandwidth") is None or entry.get("perf") is None:
                    continue
                user_id = int(user_id)
                last_skip_data[user_id] = (entry.get("cache_size"), entry.get("bandwidth"), entry.get("perf"))
            return

        for entry in data:
            # self.logger.log_msg(f"Processing entry: {entry}")
            # get the user id
            user_id = entry.get("app_id")
            if user_id is None:
                self.logger.log_msg("Missing app_id in data entry.")
                continue
            user_id = int(user_id)
            # Process the rest of the data entry
            try:
                # get the cache size
                cache_size_alloc = entry.get("cache_size")
                if cache_size_alloc is None:
                    self.logger.log_msg(f"User: {user_id} | Missing cache_size in data entry.")
                    continue
                cache_size_alloc = int(cache_size_alloc)
                cache_size_raw = entry.get("cache_raw")
                if use_raw:
                    assert(cache_size_raw is not None)
                # if cache size raw is higher than the allocated, we skip the record
                if cache_size_raw > cache_size_alloc * (1. + verification_th):
                    self.logger.log_msg(f"User: {user_id} | overallocated cache: {cache_size_raw} > {cache_size_alloc}")
                    continue
                cache_size_raw = min(cache_size_alloc, cache_size_raw)
                # get the mem_bw
                mem_bw_in_mbps_alloc = entry.get("bandwidth")
                if mem_bw_in_mbps_alloc is None:
                    self.logger.log_msg(f"User: {user_id} | Missing bandwidth in data entry.")
                    continue
                mem_bw_in_mbps_alloc = int(mem_bw_in_mbps_alloc)
                mem_bw_in_mbps_raw = entry.get("mem_bw_raw")
                if use_raw:
                    assert(mem_bw_in_mbps_raw is not None)
                # if mem bw raw is higher than the allocated, we skip the record
                if mem_bw_in_mbps_raw > mem_bw_in_mbps_alloc * (1. + verification_th):
                    self.logger.log_msg(f"User: {user_id} | overallocated mem_bw: {mem_bw_in_mbps_raw} > {mem_bw_in_mbps_alloc}")
                    continue
                mem_bw_in_mbps_raw = min(mem_bw_in_mbps_alloc, mem_bw_in_mbps_raw)
                # get the performance proxy metric
                perf = entry.get("perf")
                if perf is None:
                    self.logger.log_msg(f"User: {user_id} | Missing perf in data entry.")
                    continue
                perf = int(perf)
                # optional memory access
                mem_access = entry.get("access_mem_ops_sec")
                if mem_access is None:
                    mem_access = 0

                self.logger.log_msg(f"New raw measurement: {{user_id: {user_id}, perf: {perf}, m_acc: {mem_access}, cache: {cache_size_raw}/{cache_size_alloc}, mem_bw: {mem_bw_in_mbps_raw}/{mem_bw_in_mbps_alloc}}}")

                if use_raw:
                    cache_size = cache_size_raw
                    mem_bw_in_mbps = mem_bw_in_mbps_raw
                    cache_size = cache_size // raw_rounded * raw_rounded
                    mem_bw_in_mbps = mem_bw_in_mbps // raw_rounded * raw_rounded
                else:
                    cache_size = cache_size_alloc
                    mem_bw_in_mbps = mem_bw_in_mbps_alloc
                # check and add user id
                self.buffered_data.setdefault(user_id, {})
                self.buffered_mrc.setdefault(user_id, {})
                # check and add cache size
                self.buffered_data[user_id].setdefault(cache_size, {})
                self.buffered_mrc[user_id].setdefault(cache_size, {})
                # check and add mem_bw
                self.buffered_data[user_id][cache_size].setdefault(mem_bw_in_mbps, [])
                self.buffered_mrc[user_id][cache_size].setdefault(mem_bw_in_mbps, [])
                # add perf
                if len(self.buffered_data[user_id][cache_size][mem_bw_in_mbps]) > 0\
                    and self.buffered_data[user_id][cache_size][mem_bw_in_mbps][-1] == perf:
                        self.logger.log_msg(f"User: {user_id} | Same perf as the last record.")
                        continue;
                    # we ingores the exactly the same value as the previous record (most likely redundant record)
                self.buffered_data[user_id][cache_size][mem_bw_in_mbps].append(perf)

                # Compute and print MR: faults = 1024 (to Kbps) / 8 (to kB/s) / 4 (to pages), access = 1024 * 1024 (to Bps) / 8 (to B/s) / 64 (to cache lines)
                # faults / access = 1024 / 8 / 4 / (1024 * 1024 / 8 / 64) = 1 / (1024 * 16)
                self.logger.log_msg(f"user_id: {user_id}, MR: {mem_bw_in_mbps_raw / max(1., perf * 1024. / 16.)}")
                # Get the mrc
                mrc = entry.get("mrc")
                if mrc is None or not mrc:
                    self.logger.log_msg(f"User: {user_id} | Missing mrc in data entry.")
                    continue

                # add the data
                self.buffered_mrc[user_id][cache_size][mem_bw_in_mbps] = self._weighted_update_list(self.buffered_mrc[user_id][cache_size][mem_bw_in_mbps], mrc)

                # log the current status
                self.logger.log_msg(f"::-> New measurement: {{user_id: {user_id}, perf: {perf}, cache: {cache_size}/{cache_size_alloc}, mem_bw: {mem_bw_in_mbps}/{mem_bw_in_mbps_alloc}}}, mrc size: {np.array(mrc).shape}")
                # last usage
                recent_usage = self.get_last_usage(user_id)
                if not recent_usage:
                    self.last_usage[user_id] = {
                        "cache": cache_size_raw,
                        "mem_bw": mem_bw_in_mbps_raw}
                else:
                    self.last_usage[user_id] = {
                        "cache": self._weighted_update_value(recent_usage["cache"], cache_size_raw),
                        "mem_bw": self._weighted_update_value(recent_usage["mem_bw"], mem_bw_in_mbps_raw)}
            except Exception as e:
                self.logger.log_msg(f"Error processing entry: {e}")
                self.logger.log_msg(f"Current usage: {self.last_usage}")

    def _update_recent_measurement(self):
        for user_id, user_data in self.buffered_data.items():
            if user_id not in self.recent_measurement:
                self.recent_measurement[user_id] = []
            if len(self.recent_measurement[user_id]) > self.recent_window:
                self.recent_measurement[user_id].pop(0)
            new_data = []
            for cache_size, cache_data in user_data.items():
                for mem_bw_in_mbps, perf_list in cache_data.items():
                    # calculate median of l3miss
                    median_perf = statistics.median(perf_list) if perf_list else 0
                    new_data.append({"cache_size": cache_size, "mem_bw_in_gbps": float(mem_bw_in_mbps) / 1024., "perf": median_perf})
            self.recent_measurement[user_id].append(new_data)

    def collect_recent_measurement(self, user_id):
        '''
        merge data into a single dict: cache: {bw: [perfs]}
        '''
        merged_data = {}
        if user_id not in self.recent_measurement:
            return merged_data
        for data in self.recent_measurement[user_id]:
            for entry in data:
                cache_size = entry["cache_size"]
                mem_bw_in_gbps = entry["mem_bw_in_gbps"]
                perf = entry["perf"]
                if cache_size not in merged_data:
                    merged_data[cache_size] = {}
                if mem_bw_in_gbps not in merged_data[cache_size]:
                    merged_data[cache_size][mem_bw_in_gbps] = []
                merged_data[cache_size][mem_bw_in_gbps].append(perf)
        return merged_data

    def comsume_collected_data(self):
        # increase the global counter
        self.collection_iteration_count += 1
        if len(self.buffered_data) == 0:
            print("*** No data to consume. Check 'skip_noise_after_alloc' value in buffer_collected_data(). ***")
        self._update_recent_measurement()
        # collect data across user, cache size, and memory bandwidth
        for user_id, user_data in self.buffered_data.items():
            # before consumption, update recent measurement
            for cache_size, cache_data in user_data.items():
                for mem_bw_in_mbps, perf_list in cache_data.items():
                    # calculate median of l3miss
                    median_perf = statistics.median(perf_list) if perf_list else 0
                    # check and add user id
                    if user_id not in self.collected_data:
                        self.collected_data[user_id] = {
                            "total_record": 0,
                            "total_datapoint": 0,
                            "datapoints": {},
                            "last_updated": {},
                            "last_update_iteration": 0,
                            "last_mrc": []
                        }
                    # to check if any new datapoint is added
                    is_new_datapoint = False
                    # check and add cache size
                    if cache_size not in self.collected_data[user_id]["datapoints"]:
                        self.collected_data[user_id]["datapoints"][cache_size] = {}
                        self.collected_data[user_id]["last_updated"][cache_size] = {}
                        is_new_datapoint = True
                    # check and add mem_bw
                    if mem_bw_in_mbps not in self.collected_data[user_id]["datapoints"][cache_size]:
                        self.collected_data[user_id]["datapoints"][cache_size][mem_bw_in_mbps] = []
                        self.collected_data[user_id]["last_updated"][cache_size][mem_bw_in_mbps] = 0
                        is_new_datapoint = True
                    # add avg_l3miss
                    self.collected_data[user_id]["total_record"] += 1
                    if is_new_datapoint:
                        self.collected_data[user_id]["total_datapoint"] += 1
                    # per-record timestamping
                    self.collected_data[user_id]["datapoints"][cache_size][mem_bw_in_mbps]\
                        .append((median_perf, self.collection_iteration_count))
                    # per-c,bw pair timestamp
                    self.collected_data[user_id]["last_updated"][cache_size][mem_bw_in_mbps]\
                        = self.collection_iteration_count
                    # mrc
                    self.collected_data[user_id]["last_mrc"] = self.buffered_mrc[user_id][cache_size][mem_bw_in_mbps]
            # current timestamp
            if user_id in self.collected_data:
                self.collected_data[user_id]["last_update_iteration"] = self.collection_iteration_count

        # clear buffered_data after consumption
        self.buffered_data = {}
        self.num_buffered_data = 0
        # log the final status
        self.logger.log_msg(f"Collected data: {self.collected_data}")

    def parse_log_entries(self, log_entries, separator="", use_bw_as_perf=True):
        """
        Parse a list of log entries with the format "l3miss.{l3_miss}:{server_id}:{app_id}:cache.{cache_size}:bw.{bandwidth}:{timestamp}"

        :param log_entries: A list of strings containing the log entries
        :return: A list of dictionaries with the parsed data
        """
        if separator:
            # split log entries by separator
            log_entries = log_entries.split(separator)
        else:
            # we assume full log entries in complete json
            # e.g., {"map":{
                # "0":{
                    # "2":{"vm_id":0,"app_id":2,"mem_mb":2013,"bw_mbps":0,"cache_mbps":4217,"miss_rate_ops_sec":0,"access_rate_ops_sec":8584,"hit_rate_percent":0.0},
                    # "1":{"vm_id":0,"app_id":1,"mem_mb":2013,"bw_mbps":0,"cache_mbps":4227,"miss_rate_ops_sec":0,"access_rate_ops_sec":8587,"hit_rate_percent":0.0}
                    # }
                # }
            # }
            # parse the string using json
            log_json_entries = json.loads(log_entries)

            # Update VM to App mapping from response data
            vm_to_app_map = {}
            for vm_id, apps in log_json_entries["map"].items():
                vm_id = int(vm_id)
                app_ids = [int(app_id) for app_id in apps.keys()]
                vm_to_app_map[vm_id] = app_ids

            # Update the instance mapping
            self.vm_to_app_map = vm_to_app_map
            self.logger.log_msg(f"Updated VM to App mapping: {self.vm_to_app_map}")

            # Process each VM's data
            log_entries = []
            for vm_id, apps in log_json_entries["map"].items():
                for app_id, entry in apps.items():
                    # Include VM ID in the processed data
                    entry["vm_id"] = int(vm_id)
                    entry["mem_mb_raw"] = entry["mem_mb"]
                    entry["bw_mbps_raw"] = entry["bw_mbps"]
                    # we will also use explicit allocation than collected value (which can be usage not allocaiton)
                    if self.last_allocation:
                        if int(app_id) in self.last_allocation:
                            app_id = int(app_id)
                        if app_id in self.last_allocation:
                            entry["mem_mb"] = self.last_allocation[app_id]["cache"]
                            entry["bw_mbps"] = self.last_allocation[app_id]["mem_bw"]
                    # print(entry)
                    # prepare entries required in buffer_collected_data()
                    # - cache_mbps: l3 misses in Mbps
                    # - access_rate_ops_sec: l3 reference in ops/sec
                    if use_bw_as_perf:
                        log_entry = {
                                "vm_id": int(vm_id),
                                "app_id": app_id, "cache_size": entry["mem_mb"], "bandwidth": entry["bw_mbps"],
                                "perf": int(entry["cache_mbps"]),
                                # + int(entry["bw_mbps"]),
                                "mem_bw_raw": entry["bw_mbps_raw"], "cache_raw": entry["mem_mb_raw"],
                                "access_mem_ops_sec": entry["access_rate_ops_sec"],
                                "mrc": entry["mrc"] if "mrc" in entry else None
                            }
                    else:
                        log_entry = {
                                "vm_id": int(vm_id),
                                "app_id": app_id, "cache_size": entry["mem_mb"], "bandwidth": entry["bw_mbps"], "perf": entry["access_rate_ops_sec"],
                                "mem_bw_raw": entry["bw_mbps_raw"], "cache_raw": entry["mem_mb_raw"],
                                "access_mem_ops_sec": entry["access_rate_ops_sec"],
                                "mrc": entry["mrc"] if "mrc" in entry else None
                            }
                    log_entries.append(log_entry)
            print(self.last_allocation)
        return log_entries

    @staticmethod
    def parse_log_entry(log_entry):
        """
        Parse a log entry with the format "l3miss.{l3_miss}:{server_id}:{app_id}:cache.{cache_size}:bw.{bandwidth}:{timestamp}"

        :param log_entry: A string containing the log entry
        :return: A dictionary with the parsed data
        """
        # Regular expression to match the log entry pattern
        pattern = re.compile(
            r"l3miss\.(?P<l3_miss>\d+):"
            r"(?P<server_id>\d+):"
            r"(?P<app_id>\d+):"
            r"cache\.(?P<cache_size>\d+):"
            r"bw\.(?P<bandwidth>\d+):"
            r"(?P<timestamp>\d+)"
        )

        # Match the pattern to the log entry
        match = pattern.match(log_entry)

        # If the pattern matches, return the corresponding groups as a dictionary
        if match:
            return match.groupdict()
        else:
            raise ValueError(f"Log entry does not match expected format: {log_entry}")

    def reset_metrics_for_app(self, app_id):
        """
        Reset metrics for a specific application

        Args:
            app_id: ID of the application to reset metrics for

        Returns:
            bool: True if metrics were reset, False if app_id was not found
        """
        app_id = int(app_id)  # Ensure app_id is an integer

        reset_occurred = False

        # Reset collected data for this app
        if app_id in self.collected_data:
            self.logger.log_msg(f"Resetting metrics for application {app_id}")

            # Reset the collected data structure but maintain the structure
            self.collected_data[app_id] = {
                "total_record": 0,
                "total_datapoint": 0,
                "datapoints": {},
                "last_updated": {},
                "last_update_iteration": self.collection_iteration_count,
                "last_mrc": []
            }
            reset_occurred = True

        # Reset buffered data
        if app_id in self.buffered_data:
            self.buffered_data[app_id] = {}
            reset_occurred = True

        # Reset buffered MRC
        if app_id in self.buffered_mrc:
            self.buffered_mrc[app_id] = {}
            reset_occurred = True

        # Reset last usage data
        if app_id in self.last_usage:
            self.last_usage[app_id] = {"cache": 0, "mem_bw": 0}
            reset_occurred = True

        # Reset recent measurements
        if app_id in self.recent_measurement:
            self.recent_measurement[app_id] = []
            reset_occurred = True

        self.logger.log_msg(f"Reset metrics for application {app_id}: {'Success' if reset_occurred else 'Not found'}")
        return reset_occurred

class DummyMonitor(MemcachedMindMonitor):
    '''Dummy monitor for algorithm overhead evaluation.'''
    def collect(self, verification_th=0.025):
        # dummy collect
        pass