import copy
import time
import statistics
import numpy as np
from .allocator_base import ResourceAllocator, AllocatorParams, base_search_granularity


_search_granularity = base_search_granularity

class IncrementalTradeAllocatorParams(AllocatorParams):
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

class IncrementalTradeAllocator(ResourceAllocator):
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
        self.num_conflict_resolve_th = 3
        self.num_conflict = 0
        self.max_iteration=10
        self.was_converged = True

        # Record for the ongoing allocation performance tracking
        # key: user, value: {
        # 'res_type': 'cache' or 'mem_bw',
        # 'direction': 'up' or 'down',
        # 'performance': float
        # }
        self.user_performances = {}

        # Per-VM tracking for last allocation decisions
        # Structure: vm_id -> user_id -> {'res_type', 'direction', 'performance', 'last_updated'}
        self.vm_allocation_decisions = {}

        # Per-VM remaining resources
        # each should be in range of [0, 1]
        self.remaining_resources = {'cache': 0, 'mem_bw': 0}

        # Track users whose resources were adjusted in the previous iteration for each VM
        self.last_adjusted_users = {}

    def initialize(self, param: AllocatorParams = IncrementalTradeAllocatorParams(1.0)):
        super().initialize(param)
        self.parameters: IncrementalTradeAllocatorParams = param

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
            # Update the user_performances
            self.user_performances[user] = {
                'res_type': initial_resource_type,
                'direction': 'up',
                'performance': self.last_static_performance[user]
            }

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

        # Initialize VM tracking if this is the first time seeing this VM
        if vm_id not in self.vm_allocation_decisions:
            self.vm_allocation_decisions[vm_id] = {}

        # Clear the set of adjusted users from previous iteration for this VM
        if vm_id not in self.last_adjusted_users:
            self.last_adjusted_users[vm_id] = set()

        # Define resource types and constants
        resource_types = ["cache", "mem_bw"]
        resource_units = self._get_resource_units(search_granularity)

        # Initialize current allocation
        cur_alloc = self._initialize_allocation(users, resource_types)

        # Check performance changes for previously adjusted users in this VM
        _ = self._check_performance_changes(vm_id, users, resource_units)

        # Find worst performer
        worst_user, performance_ratios = self._find_worst_performer_for_allocation(vm_id, users, resource_types, resource_units, cur_alloc)

        # Skip further steps if no worst user found
        if worst_user is None:
            self.logger.log_msg("No worst user found, returning current allocation")
            return cur_alloc, runtime_list

        # Get resource type to adjust for worst user
        res_type_to_adjust = self._get_resource_type_for_user(vm_id, worst_user)

        # Try allocating resources first, then fall back to harvesting if needed
        adjustment_made = self._allocate_resources(
            vm_id, worst_user, res_type_to_adjust, resource_units, cur_alloc
        )

        # If no adjustment was made and we need to harvest resources
        if not adjustment_made:
            best_user = self._harvest_resources(
                vm_id, res_type_to_adjust,
                resource_units, performance_ratios, cur_alloc
            )
            # Reset last_adjusted_users for this VM
            self.last_adjusted_users[vm_id] = set()
            # Add this user to the set of users whose resources were adjusted in this iteration
            if best_user is not None:
                self.last_adjusted_users[vm_id].add(best_user)
        else:
            # Reset last_adjusted_users for this VM
            self.last_adjusted_users[vm_id] = set()
            # Add this user to the set of users whose resources were adjusted in this iteration
            self.last_adjusted_users[vm_id].add(worst_user)

        # Normalize allocations to ensure they sum to 1.0
        self._normalize_allocations(cur_alloc, resource_types, users)

        # Record runtime
        runtime_list.append((float(time.time_ns() - start_time) / float(1e6)))  # in ms

        return cur_alloc, runtime_list

    def _get_resource_units(self, search_granularity):
        """
        Define the resource units for allocation adjustments.

        Args:
            search_granularity: Granularity for resource adjustments

        Returns:
            dict: Resource types and their unit sizes
        """
        return {
            "cache": search_granularity,
            "mem_bw": search_granularity
        }

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

    def _check_performance_changes(self, vm_id, users, resource_units,
            perf_margin=0.01, revoke_margin=0.1):
        """
        Check performance changes for the most recently adjusted user in this VM.

        Args:
            vm_id: VM identifier
            users: List of user IDs in this VM
            resource_units: Resource units dict
            perf_margin: Performance margin for comparison
            revoke_margin: Margin for revoking previous allocation
        Returns:
            str or None: ID of the adjusted user, or None if no adjustment was found
        """
        opposite_resource = {"cache": "mem_bw", "mem_bw": "cache"}
        adjusted_user = None

        if not self.vm_allocation_decisions.get(vm_id):
            return None

        # Find the most recently updated user for this VM
        last_updated_time = 0
        last_adjusted_user = None

        for user, decision in self.vm_allocation_decisions[vm_id].items():
            if user in users and decision.get('last_updated', 0) > last_updated_time:
                last_updated_time = decision['last_updated']
                last_adjusted_user = user

        # Check performance for this user
        if last_adjusted_user:
            adjusted_user = last_adjusted_user
            decision = self.vm_allocation_decisions[vm_id][adjusted_user]
            current_perf = self._get_current_performance(adjusted_user)

            # Skip if no current performance data
            if current_perf is not None:
                # Check performance change based on direction of last adjustment
                if decision["direction"] == "down":
                    # If we reduced resources and performance degraded, switch resource type
                    if current_perf < decision["performance"] - perf_margin:
                        if current_perf < decision["performance"] - revoke_margin:
                            # Revoke the previous allocation by updating the last_allocation
                            self.last_allocation[adjusted_user][decision["res_type"]] += resource_units[decision["res_type"]]
                            # Reduce the pool of remaining resources
                            self.remaining_resources[decision["res_type"]] -= resource_units[decision["res_type"]]
                            self.remaining_resources[decision["res_type"]] = max(0, self.remaining_resources[decision["res_type"]])
                        # Switch resource type
                        decision["res_type"] = opposite_resource[decision["res_type"]]
                        self.logger.log_msg(f"Performance degraded after reduction for user {adjusted_user}. Switching resource type to {decision['res_type']}")

                elif decision["direction"] == "up":
                    # If we increased resources but performance didn't improve, switch resource type
                    if current_perf <= decision["performance"] - perf_margin:
                        decision["res_type"] = opposite_resource[decision["res_type"]]
                        self.logger.log_msg(f"Performance not improved after increase for user {adjusted_user}. Switching resource type to {decision['res_type']}")

                # Update the performance record
                decision["performance"] = current_perf
                self.vm_allocation_decisions[vm_id][adjusted_user] = decision

                # Also update in user_performances for global tracking
                self.user_performances[adjusted_user] = {
                    "res_type": decision["res_type"],
                    "direction": decision["direction"],
                    "performance": current_perf
                }

        return adjusted_user

    def _find_worst_performer_for_allocation(self, vm_id, users, resource_types, resource_units, cur_alloc):
        """
        Find the user with the worst performance compared to baseline.

        Args:
            vm_id: VM identifier
            users: List of user IDs
            resource_types: List of resource types
            resource_units: Resource units dict
            cur_alloc: Current allocation dict

        Returns:
            tuple: (worst user ID or None, performance ratios dict)
        """
        worst_user = None
        worst_ratio = float('inf')
        performance_ratios = {}

        # Get the set of users whose resources were adjusted in the previous iteration for this VM
        previously_adjusted_users = self.last_adjusted_users.get(vm_id, set())

        for user in users:
            # Skip users that had their resources adjusted in the previous iteration
            if user in previously_adjusted_users:
                self.logger.log_msg(f"Skipping user {user} as its resources were adjusted in the previous iteration")
                continue

            current_perf = self._get_current_performance(user)

            # Skip if no current performance data
            if current_perf is None:
                continue

            # Compare with baseline if available
            if self.last_static_performance and user in self.last_static_performance:
                baseline_perf = self.last_static_performance[user]
                ratio = current_perf / baseline_perf if baseline_perf > 0 else float('inf')
                performance_ratios[user] = ratio
                self.logger.log_msg(f"User {user} - Current performance: {current_perf} // Baseline performance: {baseline_perf} // Ratio: {ratio}")

                # Find user with worst performance (lowest ratio)
                if ratio < worst_ratio:
                    # check if the adjust resource will not violate the resource limit in self.resource_scale
                    res_type_to_adjust = self._get_resource_type_for_user(vm_id, user)
                    if res_type_to_adjust == "cache":
                        if cur_alloc[user]["cache"] + resource_units["cache"]\
                            > self.resource_scale["max_cache"] / self.resource_scale["cache"]:
                            continue
                    elif res_type_to_adjust == "mem_bw":
                        if cur_alloc[user]["mem_bw"] + resource_units["mem_bw"]\
                            > self.resource_scale["max_mem_bw"] / self.resource_scale["mem_bw"]:
                            continue
                    worst_ratio = ratio
                    worst_user = user

        return worst_user, performance_ratios

    def _get_resource_type_for_user(self, vm_id, user):
        """
        Get the resource type to adjust for a user.

        Args:
            vm_id: VM identifier
            user: User ID

        Returns:
            str: Resource type ('cache' or 'mem_bw')
        """
        # Default resource type
        res_type = "cache"

        # Check if we have a previous decision for this user in this VM
        if user in self.vm_allocation_decisions.get(vm_id, {}):
            res_type = self.vm_allocation_decisions[vm_id][user]["res_type"]
        # Fall back to global user performance if available
        elif user in self.user_performances:
            res_type = self.user_performances[user]["res_type"]

        return res_type

    def _allocate_resources(self, vm_id, worst_user, res_type_to_adjust, resource_units, cur_alloc):
        """
        Allocate resources to the worst performer if resources are available.

        Args:
            vm_id: VM identifier
            worst_user: User ID of worst performer
            res_type_to_adjust: Resource type to adjust
            resource_units: Resource units dict
            cur_alloc: Current allocation dict

        Returns:
            bool: True if allocation was made, False otherwise
        """

        # Check if we have remaining resources
        if self.remaining_resources[res_type_to_adjust] > 0:
            # Reduce remaining resources
            self.remaining_resources[res_type_to_adjust] -= resource_units[res_type_to_adjust]

            # Record old performance
            old_performance = self._get_current_performance(worst_user)

            # Increase allocation
            cur_alloc[worst_user][res_type_to_adjust] += resource_units[res_type_to_adjust]

            # Record in VM allocation decisions for next iteration
            self.vm_allocation_decisions[vm_id][worst_user] = {
                "res_type": res_type_to_adjust,
                "direction": "up",
                "performance": old_performance,
                "last_updated": time.time()
            }

            # Also update global user performances
            self.user_performances[worst_user] = {
                "res_type": res_type_to_adjust,
                "direction": "up",
                "performance": old_performance
            }

            self.logger.log_msg(f"Allocated {resource_units[res_type_to_adjust]} of {res_type_to_adjust} to user {worst_user}")
            return True

        return False

    def _harvest_resources(self, vm_id, res_type_to_adjust,
                          resource_units, performance_ratios, cur_alloc):
        """
        Harvest resources from best performer and recycle them to the resource pool.

        Args:
            vm_id: VM identifier
            res_type_to_adjust: Resource type to adjust
            resource_units: Resource units dict
            performance_ratios: Performance ratios dict
            cur_alloc: Current allocation dict

        Returns:
            str or None: User ID of best performer, or None if none found
        """
        opposite_resource = {"cache": "mem_bw", "mem_bw": "cache"}

        # Find user with best performance to harvest from
        best_user = self._find_best_performer_for_harvest(vm_id, performance_ratios, res_type_to_adjust, resource_units, cur_alloc)

        if best_user is None:
            self.logger.log_msg("No suitable user to harvest resources from")
            return

        # Record old performance
        old_performance = self._get_current_performance(best_user)

        # Decrease allocation for best user
        cur_alloc[best_user][res_type_to_adjust] -= resource_units[res_type_to_adjust]
        if cur_alloc[best_user][res_type_to_adjust] < 0:
            cur_alloc[best_user][res_type_to_adjust] = 0

        # Increase resource pool of the SAME resource type we harvested
        self.remaining_resources[res_type_to_adjust] += resource_units[res_type_to_adjust]

        # Record in VM allocation decisions for next iteration
        self.vm_allocation_decisions[vm_id][best_user] = {
            "res_type": opposite_resource[res_type_to_adjust],
            "direction": "down",
            "performance": old_performance,
            "last_updated": time.time()
        }

        # Also update global user performances
        self.user_performances[best_user] = {
            "res_type": opposite_resource[res_type_to_adjust],
            "direction": "down",
            "performance": old_performance
        }

        self.logger.log_msg(f"Harvested {resource_units[res_type_to_adjust]} of {res_type_to_adjust} from user {best_user}")
        return best_user

    def _find_best_performer_for_harvest(self, vm_id, performance_ratios, res_type_to_adjust, resource_units, cur_alloc):
        """
        Find the user with the best performance.

        Args:
            vm_id: VM identifier
            performance_ratios: Performance ratios dict
            res_type_to_adjust: Resource type to adjust
            resource_units: Resource units dict
            cur_alloc: Current allocation dict

        Returns:
            str or None: User ID of best performer, or None if none found
        """
        best_user = None
        best_ratio = -float('inf')

        # Get the set of users whose resources were adjusted in the previous iteration for this VM
        previously_adjusted_users = self.last_adjusted_users.get(vm_id, set())

        # Find user with best performance (highest ratio)
        for user, ratio in performance_ratios.items():
            # Skip users that had their resources adjusted in the previous iteration
            if user in previously_adjusted_users:
                self.logger.log_msg(f"Skipping user {user} as its resources were adjusted in the previous iteration")
                continue
            # check if the adjust resource will not violate the resource limit in self.resource_scale
            if ratio > best_ratio:
                if res_type_to_adjust == "cache":
                    if cur_alloc[user]["cache"] - resource_units["cache"]\
                        < self.resource_scale["min_cache"] / self.resource_scale["cache"]:
                        continue
                elif res_type_to_adjust == "mem_bw":
                    if cur_alloc[user]["mem_bw"] - resource_units["mem_bw"]\
                        < self.resource_scale["min_mem_bw"] / self.resource_scale["mem_bw"]:
                        continue
                best_ratio = ratio
                best_user = user

        return best_user

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
    initial_allocations = np.array(
        [[0.3, 0.7], [0.6, 0.4], [0.5, 0.5]]
    )  # Initial allocations for 3 players and 2 items
    initial_prices = np.array([1, 1])  # Initial prices for 2 items
    gp_models = None  # Placeholder for Gaussian Process models
    virtual_income = 1  # Virtual income for each player under CEEI

    raise Exception("This file is not supposed to be executed directly.")
