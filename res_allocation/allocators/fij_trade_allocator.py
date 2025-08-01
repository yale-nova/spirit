import copy
import time
import statistics
import numpy as np
from .allocator_base import ResourceAllocator, AllocatorParams, base_search_granularity


_search_granularity = base_search_granularity

class FijTradeAllocatorParams(AllocatorParams):
    def __init__(
        self,
        allocation_interval_in_sec: float,
        init_phase_interval: float = 6, # 2 was for 30 sec interval, so we will use 6 for 10 sec interval
        search_granularity: float = _search_granularity,    # 2app: 0.125 / 4.0, 4apps: 0.125 / 8.0
        measurements_per_alloc: float = 0.25,  # it's per second
    ) -> None:
        super().__init__(search_granularity, allocation_interval_in_sec)
        self.init_phase_interval = init_phase_interval
        self.measurements_per_alloc = (
            int(allocation_interval_in_sec * measurements_per_alloc)
        )

class FijTradeAllocator(ResourceAllocator):
    ### ===================== internal functions ====================== ###
    def __init__(
        self,
        config_path: str,
        resource_scale: {} = {"cache": 1, "mem_bw": 1},
        estimator=None,
        monitor=None,
        deployer=None,
    ):
        super().__init__(config_path, resource_scale, estimator, monitor, deployer)
        self.last_allocation = None
        self.last_static_allocation = None
        self.last_static_performance = None

        # Track users whose resources were adjusted in the previous iteration for each VM
        self.last_adjusted_users = {}

    def initialize(self, param: AllocatorParams = FijTradeAllocatorParams(1.0)):
        super().initialize(param)
        self.parameters: FijTradeAllocatorParams = param

    def is_static_allocation(self, skip_monitoring):
        static_alloc = True

        if self.last_static_performance is None:
            return True

        if not skip_monitoring:
            # Get list of users from estimator
            users = self.estimator.get_app_ids()

            # check if it has enough data points
            if len(users) >= 1:
                static_alloc = False

            # Check if we have enough monitoring data for each user
            for user in users:
                if (
                    user not in self.monitor.collected_data
                    or self.monitor.collected_data[user]["total_record"]
                    < self.parameters.init_phase_interval
                ):
                    static_alloc = True
                    break
        else:
            static_alloc = True if self.last_static_allocation is None else False
        return static_alloc

    def is_complete_vm_map(self, vm_to_app_map, users):
        """
        Check if the VM to app mapping contains all users.

        Args:
            vm_to_app_map: Dictionary mapping VM IDs to lists of app IDs
            users: List of all user IDs

        Returns:
            bool: True if all users are mapped to VMs, False otherwise
        """
        return vm_to_app_map and (set(users) - set(user for app_list in vm_to_app_map.values() for user in app_list) == set())

    def _allocate_resources_for_user(self, num_users, extract_keys):
        """Allocate resources for a single user.

        Args:
            num_users: Number of users to divide resources among
            extract_keys: List of resource keys to allocate (e.g. ["cache", "mem_bw"])

        Returns:
            tuple: (allocation dict, last_allocation dict) for the user
        """
        allocation = {}
        last_allocation = {}

        for key in extract_keys:
            allocation[key] = float(
                self.resource_scale[key] / float(num_users)
            )
            if key == "mem_bw":
                allocation[key] *= 1024  # gb to mb
            allocation[key] = int(allocation[key])
            last_allocation[key] = 1. / float(num_users)

        return allocation, last_allocation

    def _get_static_allocation_for_vm(self, vm_apps, extract_keys):
        """Compute static allocation for a single VM's apps.

        Args:
            vm_apps: List of app IDs in the VM
            extract_keys: List of resource keys to allocate (e.g. ["cache", "mem_bw"])

        Returns:
            tuple: (allocation dict, last_allocation dict) for the VM's apps
        """
        allocation = {}
        last_allocation = {}

        if not vm_apps:
            return allocation, last_allocation

        # Each VM gets full resources, divide equally among apps in this VM
        for user in vm_apps:
            user_allocation, user_last_allocation = self._allocate_resources_for_user(
                len(vm_apps), extract_keys
            )
            allocation[user] = user_allocation
            last_allocation[user] = user_last_allocation

        return allocation, last_allocation

    def _get_static_allocation_with_vm_mapping(self, users, vm_to_app_map, extract_keys, is_complete_vm_map):
        """Get static allocation based on VM mapping.

        Args:
            users: List of user IDs
            vm_to_app_map: Dictionary mapping VM IDs to lists of app IDs
            extract_keys: List of resource keys to allocate (e.g. ["cache", "mem_bw"])

        Returns:
            tuple: (allocation dict, last_allocation dict)
        """
        allocation = {}
        last_allocation = {}

        if not is_complete_vm_map:
            # No VM mapping - use flat allocation
            num_vms = self.get_num_vms()
            users_per_vm = max(1, len(users) // num_vms)
            self.logger.log_msg(f"VM to app mapping not available. Using flat allocation across all apps. Number of VMs: {num_vms}")

            # Create virtual VMs for flat allocation
            for vm_idx in range(num_vms):
                start_idx = vm_idx * users_per_vm
                end_idx = start_idx + users_per_vm if vm_idx < num_vms - 1 else len(users)
                vm_apps = users[start_idx:end_idx]

                # Use the same allocation logic as VM-based allocation
                vm_allocation, vm_last_allocation = self._get_static_allocation_for_vm(
                    vm_apps, extract_keys
                )

                allocation.update(vm_allocation)
                last_allocation.update(vm_last_allocation)
        else:
            # With VM mapping - allocate per VM
            self.logger.log_msg("Using per-VM static allocation with VM mapping.")

            for vm_id, app_ids in vm_to_app_map.items():
                # Filter app IDs to those in our user list
                vm_apps = [app_id for app_id in app_ids if app_id in users]
                vm_apps.sort()

                # Get allocation for this VM's apps
                vm_allocation, vm_last_allocation = self._get_static_allocation_for_vm(
                    vm_apps, extract_keys
                )

                # Merge the VM's allocation into the overall allocation
                allocation.update(vm_allocation)
                last_allocation.update(vm_last_allocation)

        return allocation, last_allocation

    def _update_last_static_performance(self, users, initial_resource_type="mem_bw"):
        # Check if previous static allocation exists
        if self.last_static_allocation is None:
            return

        # Check if the last allocation matches the static allocation
        allocation_matches = True
        for user in users:
            if user not in self.last_allocation or user not in self.last_static_allocation:
                allocation_matches = False
                break

            for key in ["cache", "mem_bw"]:
                if abs(self.last_allocation[user][key] - self.last_static_allocation[user][key]) > 0.001:
                    allocation_matches = False
                    break

            if not allocation_matches:
                break

        # If allocations don't match, nothing to record
        if not allocation_matches:
            return

        # Allocations match - record performances
        self.last_static_performance = {}

        for user in users:
            # Skip users with no data
            if user not in self.monitor.collected_data:
                continue

            recent_data = self.monitor.collect_recent_measurement(user)
            if not recent_data:
                continue

            # Get static allocation values
            static_cache = float(self.last_static_allocation[user]["cache"])
            static_mem_bw = float(self.last_static_allocation[user]["mem_bw"])
            # Convert to [0, 1]
            static_cache = static_cache * self.resource_scale["cache"]
            static_mem_bw = static_mem_bw * self.resource_scale["mem_bw"]
            # print(f"User {user} - Static cache: {static_cache} // Static mem_bw: {static_mem_bw}")
            # print(f"User {user} - Recent data: {recent_data}")
            # print(f"User {user} - Monitor's recent data: {self.monitor.recent_measurement[user]}")

            # Skip if no cache sizes available
            cache_sizes = list(recent_data.keys())
            if not cache_sizes:
                continue

            # Find closest matching cache and mem_bw
            closest_cache = min(cache_sizes, key=lambda x: abs(x - static_cache))
            if closest_cache not in recent_data:
                continue

            mem_bws = list(recent_data[closest_cache].keys())
            if not mem_bws:
                continue

            closest_mem_bw = min(mem_bws, key=lambda x: abs(x - static_mem_bw))
            if closest_mem_bw not in recent_data[closest_cache]:
                continue

            # Get performance data
            perf_list = recent_data[closest_cache][closest_mem_bw]
            if not perf_list:
                continue

            # Calculate and store average performance
            self.last_static_performance[user] = statistics.mean(perf_list)
            print(f"User {user} - Last static performance: {self.last_static_performance[user]}")

    def allocate_and_parse(self, skip_monitoring=False, verbose_n_user=8):
        start_time = time.time_ns()

        # Get VM to app mapping
        vm_to_app_map = self.monitor.get_vm_to_app_mapping()
        self.logger.log_msg(f"VM to app mapping: {vm_to_app_map}")

        # allocation
        users = self.estimator.get_app_ids()
        allocation = {}
        extract_keys = ["cache", "mem_bw"]

        is_complete_vm_map = self.is_complete_vm_map(vm_to_app_map, users)
        # If the vm map is complete, update the last static performance
        if is_complete_vm_map:
            self._update_last_static_performance(users)

        # Check if we need to use static allocation (no enough data or no last static performance record)
        need_static_alloc = self.is_static_allocation(skip_monitoring)
        # If we need to use static allocation or no VM mapping is available
        if need_static_alloc or not is_complete_vm_map:
            self.last_allocation = {}
            allocation, self.last_allocation = self._get_static_allocation_with_vm_mapping(
                users, vm_to_app_map, extract_keys, is_complete_vm_map
            )
            self.logger.log_msg("Static allocation in actual resource unit (MB, Mbps): {}".format(allocation))
            self.last_static_allocation = copy.deepcopy(self.last_allocation)
            return allocation

        # Here, is_complete_vm_map is True
        # Per-VM allocation
        # Process each VM separately
        all_runtime_list = []

        for vm_id, app_ids in vm_to_app_map.items():
            # Filter app IDs to those in our user list
            vm_apps = [app_id for app_id in app_ids if app_id in users]
            vm_apps.sort()

            if not vm_apps:
                continue

            # Perform allocation for this VM's apps
            vm_cur_alloc, vm_runtime_list = self.allocate(
                vm_id,
                vm_apps,
                # dict.fromkeys(vm_apps, 1 / len(vm_apps)),
                search_granularity=self.parameters.search_granularity,
            )

            if not isinstance(vm_cur_alloc, dict):
                self.logger.log_msg(f"Warning: VM {vm_id} allocation returned non-dictionary result. Using static allocation.")
                # Use static allocation for this VM
                vm_allocation, vm_last_allocation = self._get_static_allocation_for_vm(
                    vm_apps, extract_keys
                )
                allocation.update(vm_allocation)
                self.last_allocation.update(vm_last_allocation)
                continue

            # Update runtime metrics
            all_runtime_list.extend(vm_runtime_list)

            # Update last_allocation with this VM's allocation
            for user in vm_cur_alloc.keys():
                if not isinstance(vm_cur_alloc[user], dict):
                    continue
                if user not in self.last_allocation:
                    self.last_allocation[user] = {}
                self.last_allocation[user].update(vm_cur_alloc[user])

                # Create resource allocation for this user
                allocation[user] = {}
                for key in extract_keys:
                    if key in vm_cur_alloc[user]:
                        allocation[user][key] = (
                            vm_cur_alloc[user][key] * self.resource_scale[key]
                        )
                        if key == "mem_bw":
                            allocation[user][key] *= 1024.0  # gb to mb
                        allocation[user][key] = int(allocation[user][key])
                if user <= verbose_n_user:
                    self.logger.log_msg(f"VM {vm_id} - Current allocation for user {user}: {vm_cur_alloc[user]} // {allocation[user]}")

        # Log metrics for all VMs
        runtime_list = {
            "per-user": np.sum(all_runtime_list),  # aggregate over iterations
            "per-alloc": (float(time.time_ns() - start_time) / float(1e6)),
        }
        self.logger.log_msg(f"All VMs - Runtime_list: {all_runtime_list}")
        self.logger.log_msg(f"All VMs - Runtime (ms): {runtime_list}")
        return allocation

    def allocate(
        self,
        vm_id: str,
        users: list,
        search_granularity: float = _search_granularity,
    ):
        """
        Allocate resources for users in a specific VM.

        Args:
            vm_id: Identifier of the VM
            users: List of user IDs in this VM
            search_granularity: Granularity for resource adjustments

        Returns:
            tuple: (current allocation dict, runtime metrics list)
        """
        # Initialize data structures for tracking allocation and runtime
        cur_alloc = {}
        runtime_list = []
        start_time = time.time_ns()

        # Clear the set of adjusted users from previous iteration for this VM
        if vm_id not in self.last_adjusted_users:
            self.last_adjusted_users[vm_id] = set()

        # Define resource types and constants
        resource_types = ["cache", "mem_bw"]
        resource_units = {
            "cache": search_granularity,
            "mem_bw": search_granularity
        }

        # Initialize current allocation
        cur_alloc = self._initialize_allocation(users, resource_types)

        # Find cache and bandwidth sensitivity for each user
        sensitivity_scores = self._calculate_sensitivity_scores(vm_id, users, cur_alloc, resource_units)

        # Find best cache-sensitive and bw-sensitive users
        cache_sensitive_user = self._find_most_cache_sensitive_user(sensitivity_scores)
        bw_sensitive_user = self._find_most_bw_sensitive_user(sensitivity_scores, exclude_user=cache_sensitive_user)

        # Perform trade if we found suitable users
        if cache_sensitive_user and bw_sensitive_user:
            self._trade_resources(vm_id, cur_alloc, cache_sensitive_user, bw_sensitive_user, resource_units)

            # Add these users to the last adjusted users set
            if vm_id not in self.last_adjusted_users:
                self.last_adjusted_users[vm_id] = set()
            self.last_adjusted_users[vm_id].add(cache_sensitive_user)
            self.last_adjusted_users[vm_id].add(bw_sensitive_user)

            self.logger.log_msg(f"Traded resources between cache-sensitive user {cache_sensitive_user} and bw-sensitive user {bw_sensitive_user}")

        # Normalize allocations to ensure they sum to 1.0
        self._normalize_allocations(cur_alloc, resource_types, users)

        # Record runtime
        runtime_list.append((float(time.time_ns() - start_time) / float(1e6)))  # in ms

        return cur_alloc, runtime_list

    def _calculate_sensitivity_scores(self, vm_id, users, cur_alloc, resource_units):
        """
        Calculate sensitivity scores for each user to determine if they are cache-sensitive or bw-sensitive.

        Args:
            vm_id: VM identifier
            users: List of user IDs
            cur_alloc: Current allocation dict
            resource_units: Resource units dict

        Returns:
            dict: Sensitivity scores for each user
        """
        sensitivity_scores = {}

        for user in users:
            # Skip if user not in current allocation
            if user not in cur_alloc:
                continue

            # Skip users that were adjusted in the previous iteration
            if user in self.last_adjusted_users.get(vm_id, set()):
                continue

            # Get current allocation for user
            cache_alloc = cur_alloc[user]["cache"]
            mem_bw_alloc = cur_alloc[user]["mem_bw"]

            # Calculate absolute values for resource allocation
            abs_cache = cache_alloc * self.resource_scale["cache"]
            abs_mem_bw = mem_bw_alloc * self.resource_scale["mem_bw"]

            # Get current performance
            current_perf = self.estimator.get_estimation(user, abs_cache, abs_mem_bw)

            # Calculate performance with more cache but less mem_bw
            more_cache_less_bw_perf = None
            # Get normalized min/max values
            min_cache_norm = self.resource_scale.get("min_cache", 0.0) / self.resource_scale["cache"]
            max_cache_norm = self.resource_scale.get("max_cache", self.resource_scale["cache"]) / self.resource_scale["cache"]
            min_mem_bw_norm = self.resource_scale.get("min_mem_bw", 0.0) / self.resource_scale["mem_bw"]
            max_mem_bw_norm = self.resource_scale.get("max_mem_bw", self.resource_scale["mem_bw"]) / self.resource_scale["mem_bw"]

            if (cache_alloc + resource_units["cache"] <= max_cache_norm and
                mem_bw_alloc - resource_units["mem_bw"] >= min_mem_bw_norm):
                more_cache_abs = (cache_alloc + resource_units["cache"]) * self.resource_scale["cache"]
                less_bw_abs = (mem_bw_alloc - resource_units["mem_bw"]) * self.resource_scale["mem_bw"]
                more_cache_less_bw_perf = self.estimator.get_estimation(user, more_cache_abs, less_bw_abs)

            # Calculate performance with less cache but more mem_bw
            less_cache_more_bw_perf = None
            if (cache_alloc - resource_units["cache"] >= min_cache_norm and
                mem_bw_alloc + resource_units["mem_bw"] <= max_mem_bw_norm):
                less_cache_abs = (cache_alloc - resource_units["cache"]) * self.resource_scale["cache"]
                more_bw_abs = (mem_bw_alloc + resource_units["mem_bw"]) * self.resource_scale["mem_bw"]
                less_cache_more_bw_perf = self.estimator.get_estimation(user, less_cache_abs, more_bw_abs)

            # Calculate sensitivity scores
            cache_sensitivity = 0
            bw_sensitivity = 0

            if current_perf is not None:
                if more_cache_less_bw_perf is not None:
                    cache_sensitivity = (more_cache_less_bw_perf - current_perf) / current_perf

                if less_cache_more_bw_perf is not None:
                    bw_sensitivity = (less_cache_more_bw_perf - current_perf) / current_perf

            # Store sensitivity scores
            sensitivity_scores[user] = {
                "cache_sensitivity": cache_sensitivity,
                "bw_sensitivity": bw_sensitivity,
                "current_perf": current_perf
            }

            # Log sensitivity scores
            self.logger.log_msg(f"User {user} - Cache sensitivity: {cache_sensitivity}, BW sensitivity: {bw_sensitivity}")

        # Reset the last adjusted users for this VM
        self.last_adjusted_users[vm_id] = set()

        return sensitivity_scores

    def _find_most_cache_sensitive_user(self, sensitivity_scores):
        """
        Find the user who would benefit most from trading BW for cache.
        A negative cache sensitivity means the user prefers cache over BW.

        Args:
            sensitivity_scores: Dict of sensitivity scores

        Returns:
            str: User ID of the most cache-sensitive user, or None if none found
        """
        most_cache_sensitive_user = None
        best_score = 0

        for user, scores in sensitivity_scores.items():
            # For cache-sensitive user, we want someone who benefits from more cache
            # and is harmed by less cache (negative bw_sensitivity)
            cache_score = scores["cache_sensitivity"]
            bw_score = scores["bw_sensitivity"]

            # User is cache-sensitive if they benefit more from additional cache
            # than from additional bandwidth
            if cache_score > 0 and cache_score > bw_score:
                if most_cache_sensitive_user is None or cache_score > best_score:
                    most_cache_sensitive_user = user
                    best_score = cache_score

        return most_cache_sensitive_user

    def _find_most_bw_sensitive_user(self, sensitivity_scores, exclude_user=None):
        """
        Find the user who would benefit most from trading cache for BW.
        A negative BW sensitivity means the user prefers BW over cache.

        Args:
            sensitivity_scores: Dict of sensitivity scores
            exclude_user: User ID to exclude (typically the cache-sensitive user)

        Returns:
            str: User ID of the most BW-sensitive user, or None if none found
        """
        most_bw_sensitive_user = None
        best_score = 0

        for user, scores in sensitivity_scores.items():
            # Skip the excluded user
            if user == exclude_user:
                continue

            # For BW-sensitive user, we want someone who benefits from more BW
            # and is harmed by less BW
            cache_score = scores["cache_sensitivity"]
            bw_score = scores["bw_sensitivity"]

            # User is BW-sensitive if they benefit more from additional bandwidth
            # than from additional cache
            if bw_score > 0 and bw_score > cache_score:
                if most_bw_sensitive_user is None or bw_score > best_score:
                    most_bw_sensitive_user = user
                    best_score = bw_score

        return most_bw_sensitive_user

    def _trade_resources(self, vm_id, cur_alloc, cache_sensitive_user, bw_sensitive_user, resource_units):
        """
        Trade resources between a cache-sensitive user and a BW-sensitive user.

        Args:
            vm_id: VM identifier
            cur_alloc: Current allocation dict
            cache_sensitive_user: User ID of cache-sensitive user
            bw_sensitive_user: User ID of BW-sensitive user
            resource_units: Resource units dict
        """
        if not cache_sensitive_user or not bw_sensitive_user:
            return

        # Record old performance
        old_cache_perf = self._get_current_performance(cache_sensitive_user)
        old_bw_perf = self._get_current_performance(bw_sensitive_user)

        # Trade resources
        # Cache-sensitive user gets more cache, less BW
        cur_alloc[cache_sensitive_user]["cache"] += resource_units["cache"]
        cur_alloc[cache_sensitive_user]["mem_bw"] -= resource_units["mem_bw"]

        # BW-sensitive user gets more BW, less cache
        cur_alloc[bw_sensitive_user]["mem_bw"] += resource_units["mem_bw"]
        cur_alloc[bw_sensitive_user]["cache"] -= resource_units["cache"]

        # Add these users to the last adjusted users set
        if vm_id not in self.last_adjusted_users:
            self.last_adjusted_users[vm_id] = set()
        self.last_adjusted_users[vm_id].add(cache_sensitive_user)
        self.last_adjusted_users[vm_id].add(bw_sensitive_user)

        self.logger.log_msg(
            f"Traded {resource_units['cache']} cache and {resource_units['mem_bw']} mem_bw between users "
            f"{cache_sensitive_user} and {bw_sensitive_user}"
        )

    def _initialize_allocation(self, users, resource_types):
        """
        Initialize allocation for users.

        Args:
            users: List of user IDs
            resource_types: List of resource types

        Returns:
            dict: Initial allocation for each user
        """
        cur_alloc = {}

        # Initialize with equal distribution
        equal_share = 1.0 / len(users)
        for user in users:
            cur_alloc[user] = {
                "cache": equal_share,
                "mem_bw": equal_share
            }

        # Use previous allocation if available
        for user in users:
            if user in self.last_allocation:
                for res_type in resource_types:
                    if res_type in self.last_allocation[user]:
                        cur_alloc[user][res_type] = self.last_allocation[user][res_type]

        return cur_alloc

    def _normalize_allocations(self, cur_alloc, resource_types, users):
        """
        Normalize allocations to ensure they sum to 1.0 for each resource type.

        Args:
            cur_alloc: Current allocation dict
            resource_types: List of resource types
            users: List of user IDs
        """
        for res_type in resource_types:
            total = sum(cur_alloc[user][res_type] for user in users)
            if total > 1:
                for user in users:
                    cur_alloc[user][res_type] /= total

    def _get_current_performance(self, user):
        """
        Get the current performance for a user from recent measurements.

        Args:
            user: User ID

        Returns:
            float: Current performance metric, or None if no data available
        """
        if user not in self.monitor.collected_data:
            return None

        recent_data = self.monitor.collect_recent_measurement(user)
        if not recent_data:
            return None

        # Find the cache size closest to current allocation
        if user not in self.last_allocation:
            return None

        alloc_cache = self.last_allocation[user].get("cache", 0)
        alloc_mem_bw = self.last_allocation[user].get("mem_bw", 0)

        # Convert to absolute values for finding closest match
        abs_cache = int(alloc_cache * self.resource_scale["cache"])
        abs_mem_bw = float(alloc_mem_bw * self.resource_scale["mem_bw"])  # In GB/s

        # Find closest matching cache and mem_bw in data
        cache_sizes = list(recent_data.keys())
        if not cache_sizes:
            return None

        closest_cache = min(cache_sizes, key=lambda x: abs(x - abs_cache))

        if closest_cache not in recent_data:
            return None

        mem_bws = list(recent_data[closest_cache].keys())
        if not mem_bws:
            return None

        closest_mem_bw = min(mem_bws, key=lambda x: abs(x - abs_mem_bw))

        if closest_mem_bw not in recent_data[closest_cache]:
            return None

        # Calculate and return average performance
        perf_list = recent_data[closest_cache][closest_mem_bw]
        if not perf_list:
            return None

        return statistics.mean(perf_list)

# Example usage
if __name__ == "__main__":
    raise Exception("This file is not supposed to be executed directly.")
