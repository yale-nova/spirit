from time import sleep
import copy
import time
import math
import numpy as np
import numpy as np
from utils.logger import Logger
from .allocator_base import ResourceAllocator, AllocatorParams, base_search_granularity
from .ptas_algorithm import ptas_algorithm, get_static_allocation, get_search_dict, ResourceLimited

_search_granularity = base_search_granularity

class SpiritAllocatorParams(AllocatorParams):
    def __init__(
        self,
        allocation_interval_in_sec: float,
        init_phase_interval: float = 2,
        search_granularity: float = _search_granularity,    # 2app: 0.125 / 4.0, 4apps: 0.125 / 8.0
        search_range: float = _search_granularity * 5,      # 3 x search_granularity
        search_range_delta: float = 0.025,
        search_range_max: float = 0.4,
        measurements_per_alloc: float = 0.25,  # it's per second
    ) -> None:
        super().__init__(search_granularity, allocation_interval_in_sec)
        self.init_phase_interval = init_phase_interval
        self.search_range = search_range
        self.search_range_delta = search_range_delta
        self.search_range_max = search_range_max
        self.measurements_per_alloc = (
            int(allocation_interval_in_sec * measurements_per_alloc)
        )
        self.allocation_update_clip = (
            0.05 # if previous alloc was 0.5, then new allocation can be 0.25 ~ 0.75 for clip=0.25
                 # default is 0.05: 512 MB for 10 GB total memory and 512 Mbps for 10 Gbps total bandwidth
        )
        self.adaptive_granularity = False
        self.adaptive_granularity_ratio = 0.5
        self.adaptive_iter=False
        self.min_iter_bound=5
        self.max_iter_bound=25

class SpiritAllocator(ResourceAllocator):
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
        self.num_conflict_resolve_th = 3
        self.num_conflict = 0
        self.max_iteration=20
        self.was_converged = True

    def initialize(self, param: AllocatorParams = SpiritAllocatorParams(1.0)):
        super().initialize(param)
        self.parameters: SpiritAllocatorParams = param

    def allocate_and_parse(self, skip_monitoring=False, verbose_n_user=8):
        start_time = time.time_ns()

        # Get VM to app mapping
        vm_to_app_map = self.monitor.get_vm_to_app_mapping()
        self.logger.log_msg(f"VM to app mapping: {vm_to_app_map}")

        # allocation
        users = self.estimator.get_app_ids()
        allocation = {}
        extract_keys = ["cache", "mem_bw"]

        static_alloc = True
        if not skip_monitoring:
            # check if it has enough data points
            if len(users) >= 1:
                static_alloc = False

            # for user in users:
            for user in [1]:
                if (
                    user not in self.monitor.collected_data
                    or self.monitor.collected_data[user]["total_record"]
                    < self.parameters.init_phase_interval
                ):
                    static_alloc = True
        else:
            static_alloc = True if self.last_static_allocation is None else False

        is_complete_vm_map = vm_to_app_map and (set(users) - set(user for app_list in vm_to_app_map.values() for user in app_list) == set())
        # If we need to use static allocation or no VM mapping is available
        if static_alloc or not is_complete_vm_map:
            self.last_allocation = {}

            if not is_complete_vm_map:
                num_vms = self.get_num_vms()
                users_per_vm = max(1, len(users) // num_vms)
                # No VM mapping - use flat allocation
                self.logger.log_msg(f"VM to app mapping not available. Using flat allocation across all apps. Number of VMs: {num_vms}")
                for idx, user in enumerate(users):
                    vm_idx = idx // users_per_vm
                    users_in_this_vm = users_per_vm if vm_idx < num_vms else len(users) - vm_idx * users_per_vm
                    allocation[user] = {}
                    self.last_allocation[user] = {}
                    for key in extract_keys:
                        allocation[user][key] = float(
                            self.resource_scale[key] / float(users_in_this_vm)
                        )
                        # update latest allocation
                        if key == "mem_bw":
                            allocation[user][key] *= 1024  # gb to mb
                        # enforce int
                        allocation[user][key] = int(allocation[user][key])
                        self.last_allocation[user][key] = 1. / float(users_in_this_vm)
            else:
                # With VM mapping - allocate per VM
                self.logger.log_msg("Using per-VM static allocation with VM mapping.")

                # Process each VM
                for vm_id, app_ids in vm_to_app_map.items():
                    # Filter app IDs to those in our user list
                    vm_apps = [app_id for app_id in app_ids if app_id in users]
                    vm_apps.sort()

                    if not vm_apps:
                        continue

                    # Each VM gets full resources, divide equally among apps in this VM
                    for user in vm_apps:
                        allocation[user] = {}
                        self.last_allocation[user] = {}
                        for key in extract_keys:
                            allocation[user][key] = float(
                                self.resource_scale[key] / float(len(vm_apps))
                            )
                            # update latest allocation
                            if key == "mem_bw":
                                allocation[user][key] *= 1024  # gb to mb
                            # enforce int
                            allocation[user][key] = int(allocation[user][key])
                            self.last_allocation[user][key] = 1. / float(len(vm_apps))

            self.logger.log_msg("Static allocation in actual resource unit (MB, Mbps): {}".format(allocation))
            self.last_static_allocation = copy.deepcopy(self.last_allocation)
            return allocation

        # Now we have enough data point - dynamic allocation
        # Here, is_complete_vm_map is True
        # Per-VM allocation
        # Process each VM separately
        all_runtime_list = []
        all_num_iter_list = []

        for vm_id, app_ids in vm_to_app_map.items():
            # Filter app IDs to those in our user list
            vm_apps = [app_id for app_id in app_ids if app_id in users]
            vm_apps.sort()

            if not vm_apps:
                continue

            # Perform allocation for this VM's apps
            vm_cur_alloc, vm_runtime_list, vm_num_iter_list, vm_converged = self.allocate(
                vm_apps,
                dict.fromkeys(vm_apps, 1 / len(vm_apps)),
                search_granularity=self.parameters.search_granularity,
                search_range=self.parameters.search_range,
            )

            if not isinstance(vm_cur_alloc, dict):
                self.logger.log_msg(f"Warning: VM {vm_id} allocation returned non-dictionary result. Using static allocation.")
                # Use static allocation for this VM
                for user in vm_apps:
                    if user not in self.last_allocation:
                        self.last_allocation[user] = {}
                    allocation[user] = {}
                    for key in extract_keys:
                        self.last_allocation[user][key] = 1. / float(len(vm_apps))
                        allocation[user][key] = float(self.resource_scale[key] / float(len(vm_apps)))
                        if key == "mem_bw":
                            allocation[user][key] *= 1024  # gb to mb
                        allocation[user][key] = int(allocation[user][key])
                continue

            # Update runtime metrics
            all_runtime_list.extend(vm_runtime_list)
            all_num_iter_list.extend(vm_num_iter_list)

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
        self.logger.log_msg(f"All VMs - Num_iter: {all_num_iter_list}")
        self.logger.log_msg(f"All VMs - Runtime (ms): {runtime_list}")

        return allocation

    def allocate(
        self,
        users: list,
        weights: {},
        search: str = "binary",
        price_adjust_coef: float = 0.05,
        float_precision: float = 0.00001,
        gaussian_err_stddev=None,
        search_granularity=0.025,
        search_range=0.05,
        default_guide_factor=0.1,
        verbose=False,

        # @clipping_res_decrease_ratio
        # 0.5 as default; need more careful tuning, decrease it (scarse resource)
        clipping_res_decrease_ratio=0.25,
    ):
        search_methods = ["linear", "binary"]
        searched_price = set()
        maximum_iteration = self.max_iteration
        maximum_retry = 3
        is_converged = True

        # check search method
        if search not in search_methods:
            raise ValueError(f"Search method {search} is not supported.")

        # check gaussian_err_stddev
        if gaussian_err_stddev is None:
            gaussian_err_stddev = dict.fromkeys(users, 0)

        # Generate guide_factor
        guide_factors = {}
        for user_id in users:
            guide_factors[user_id] = default_guide_factor

        # ftn for binary search
        def compute_and_update_mid(price_vec):
            price_vec["cache"] = (
                price_vec["left"]["cache"] + price_vec["right"]["cache"]
            ) / 2.0
            price_vec["mem_bw"] = (
                price_vec["left"]["mem_bw"] + price_vec["right"]["mem_bw"]
            ) / 2.0

        if search == "linear":
            price_vector = {"cache": 0.5, "mem_bw": 0.5}
        elif search == "binary":
            price_vector = {
                "left": {"cache": 1.0, "mem_bw": 0.0},
                "right": {"cache": 0.0, "mem_bw": 1.0},
            }
        else:
            raise Exception("Search method {} is not supported.".format(search))
        # print("Initial price vector: {}".format(price_vector))
        runtime_list = []
        num_iter_list = []
        iteration = 0
        while True:
            iteration += 1
            resource_limited = ResourceLimited()
            # compute mid point for binary search
            if search == "binary":
                compute_and_update_mid(price_vector)
                # print("Current price vector: {}".format(price_vector))

            # compute current resource usage and performance estimation
            searched_price.add((price_vector["cache"], price_vector["mem_bw"]))
            cur_alloc, _runtime_list, _num_iter_list, resource_limited_new =\
                self.find_best_allocation_per_user(
                    price_vector,
                    users,
                    weights,
                    gaussian_err_stddev,
                    search_granularity,
                    search_range,
                    guide_factors,
                )
            resource_limited.update(resource_limited_new)
            runtime_list.extend(_runtime_list)  # per-user, per iteration
            num_iter_list.extend(_num_iter_list)
            sum_alloc = self.compute_resource_usage(cur_alloc)
            self.logger.log_msg(
                f"Iteration: {iteration}, price_vector: {price_vector}, sum_alloc: {sum_alloc}, res_limit: {resource_limited}\n"
            )
            self.logger.log_msg("--- Allocation: {}\n".format(cur_alloc))
            # update price vector
            if search == "linear":
                reduce_coeff = False
                # if cache is over allocated, increase price of cache
                if sum_alloc["cache"] > 1.0 + float_precision:
                    price_vector["cache"] += price_adjust_coef
                    price_vector["mem_bw"] -= price_adjust_coef
                # if mem_bw is over allocated, increase price of mem_bw
                elif sum_alloc["mem_bw"] > 1.0 + float_precision:
                    price_vector["cache"] -= price_adjust_coef
                    price_vector["mem_bw"] += price_adjust_coef
                else:
                    reduce_coeff = True
                # check if the price has been searched before
                if (
                    price_vector["cache"],
                    price_vector["mem_bw"],
                ) in searched_price or reduce_coeff:
                    if verbose:
                        print(
                            "Price vector {} has been searched before, decrease coefficient.".format(
                                price_vector
                            )
                        )
                    price_adjust_coef /= 2.0
                    searched_price.clear()
            elif search == "binary":
                # check if there was a resource limiting (clipping), if so, adjust price to resolve it
                if resource_limited.is_resource_limited():
                    if resource_limited.mem_bw_max_limit or resource_limited.mem_bw_min_limit:
                        if sum_alloc["cache"] > 1.0 + float_precision:
                            # prioritize overall allocation but move gradually
                             price_vector["right"] = {
                            "cache": price_vector["right"]["cache"] + (price_vector["cache"] - price_vector["right"]["cache"]) * clipping_res_decrease_ratio,
                            "mem_bw": price_vector["right"]["mem_bw"] + (price_vector["mem_bw"] - price_vector["right"]["mem_bw"]) * clipping_res_decrease_ratio,
                            }
                        else:
                            price_vector["left"] = {
                                "cache": price_vector["left"]["cache"] + (price_vector["cache"] - price_vector["left"]["cache"]) * clipping_res_decrease_ratio,
                                "mem_bw": price_vector["left"]["mem_bw"] + (price_vector["mem_bw"] - price_vector["left"]["mem_bw"]) * clipping_res_decrease_ratio,
                            }
                    elif resource_limited.cache_max_limit or resource_limited.cache_min_limit:
                        if sum_alloc["mem_bw"] > 1.0 + float_precision:
                            # prioritize overall allocation but move gradually
                            price_vector["left"] = {
                                "cache": price_vector["left"]["cache"] + (price_vector["cache"] - price_vector["left"]["cache"]) * clipping_res_decrease_ratio,
                                "mem_bw": price_vector["left"]["mem_bw"] + (price_vector["mem_bw"] - price_vector["left"]["mem_bw"]) * clipping_res_decrease_ratio,
                            }
                        else:
                            price_vector["right"] = {
                                "cache": price_vector["right"]["cache"] + (price_vector["cache"] - price_vector["right"]["cache"]) * clipping_res_decrease_ratio,
                                "mem_bw": price_vector["right"]["mem_bw"] + (price_vector["mem_bw"] - price_vector["right"]["mem_bw"]) *    clipping_res_decrease_ratio,
                            }
                elif sum_alloc["cache"] > 1.0 + float_precision:
                    # increase cache price
                    price_vector["right"] = {
                        "cache": price_vector["cache"],
                        "mem_bw": price_vector["mem_bw"],
                    }
                # if mem_bw is over allocated, increase price of mem_bw
                elif sum_alloc["mem_bw"] > 1.0 + float_precision:
                    price_vector["left"] = {
                        "cache": price_vector["cache"],
                        "mem_bw": price_vector["mem_bw"],
                    }
                # converged
                else:
                    self.num_conflict = 0
                    break

            # check minimal price and break if the price is too small
            if (
                price_vector["cache"] < float_precision
                and price_vector["mem_bw"] < float_precision
            ):
                if self.parameters.adaptive_iter:
                    self.max_iteration = max(
                        int(self.max_iteration / 1.5),
                        self.parameters.min_iter_bound
                    )
                is_converged = False
                break
            # check maximum iteration
            if iteration >= maximum_iteration:
                # update price vector
                if (
                    sum_alloc["cache"] > 1.0 + float_precision
                    or sum_alloc["mem_bw"] > 1.0 + float_precision
                ):
                    if iteration <= maximum_iteration + maximum_retry:
                        continue
                    print(
                        f"Warning: total resource usage is over 1.0: {sum_alloc}"
                    )
                    if self.parameters.adaptive_iter:
                        self.max_iteration = min(
                            int(self.max_iteration * 1.5),
                            self.parameters.max_iter_bound
                        )
                    self.num_conflict += 1
                    if self.num_conflict > self.num_conflict_resolve_th:
                        self.num_conflict = 0
                        # reset to the static
                        print(f"Warning: Conflict counter is over the threshold {self.num_conflict_resolve_th}.")
                        cur_alloc = self.last_allocation
                    else:
                        # use the last allocation
                        cur_alloc = self.last_allocation
                    # print current price vector
                    print("--- Current price vector: {}".format(price_vector))
                    # print current allocation
                    print("--- Current allocation")
                    for user_id, alloc in cur_alloc.items():
                        if user_id <= 10:
                            print(f"user: {user_id} || cache: {alloc['cache']}, mem_bw: {alloc['mem_bw']}")
                    is_converged = False
                # else, break
                break
        # add the current price to cur_alloc
        for user_id in users:
            cur_alloc[user_id]["price"] = price_vector
        return cur_alloc, runtime_list, num_iter_list, is_converged

    def compute_resource_usage(self, allocation: dict):
        # best_alloc = {'cache': cache_alloc, 'mem_bw': mem_bw_alloc}
        cache_alloc = sum([alloc["cache"] for alloc in allocation.values()])
        mem_bw_alloc = sum([alloc["mem_bw"] for alloc in allocation.values()])
        return {"cache": cache_alloc, "mem_bw": mem_bw_alloc}

    def _get_static_alloc(self, num_user):
        return get_static_allocation(num_user)

    def _get_search_dict(self, cur_alloc, search_range: float):
        return get_search_dict(cur_alloc, search_range)

    # @search_range: the range of search space in [0, 1]
    def find_best_allocation_per_user(
        self,
        price_vector: {},
        user_ids: list,
        weights: {},
        gaussian_err_stddev: {},
        search_granularity: float,
        search_range: float,
        guide_factor: dict,
        logger=None,
    ):
        # self.estimator.get_estimation(user, 128.0, 2.0)  # cache in mb, bandwidth in gbps
        allocations = {}
        runtime_list = []
        num_iter_list=[]

        # apply search range to the search space
        # search_range_dict = self._get_search_dict(len(user_ids), search_range)
        resource_limited = ResourceLimited()
        start_time = time.time_ns()
        for user_id in user_ids:
            search_range_dict = self._get_search_dict(self.last_allocation[user_id], search_range)

            allocations[user_id], num_iter, resource_limited_new =\
                ptas_algorithm(
                    self.estimator,
                    user_id,
                    len(user_ids),
                    epsilon=search_granularity,
                    budget=weights[user_id],
                    price_vector=price_vector,
                    last_allocation=self.last_allocation,
                    last_static_allocation=self.last_static_allocation,
                    resource_scale=self.resource_scale,
                    noise_std_dev=gaussian_err_stddev[user_id],
                    search_range=search_range_dict,
                    guide_factor=guide_factor[user_id],
                    logger=self.logger,
                    # Disable explore advantage for overhead computaton since there is no monitor anyway
                    explore_adv=0. if len(user_ids) >= 8 else 0.025,
                    allocation_update_clip=self.parameters.allocation_update_clip,
            )
            # update resource limited
            resource_limited.update(resource_limited_new)

            # check if allocation is None
            if (allocations[user_id] is None) or (
                allocations[user_id]["cache"] is None or
                allocations[user_id]["mem_bw"] is None
            ):
                # static allocation
                allocations[user_id] = self._get_static_alloc(len(user_ids))
                self.logger.log_msg(f"Warning: allocation is None for user {user_id}")
            num_iter_list.append(num_iter)

        # runtime
        runtime_list.append(
            (float(time.time_ns() - start_time) / float(1e6) / float(len(user_ids)))
        )  # in ms, per user
        return allocations, runtime_list, num_iter_list, resource_limited

    def get_static_allocation(self, users_ids: list):
        allocation_lists = {}
        for user_id in users_ids:
            allocation_lists[user_id] = get_static_allocation(len(users_ids))
        return allocation_lists

    def get_static_utility(self, users_ids: list):
        # TODO: maybe no longer needed
        return self.estimator.get_util_from_allocation(
            self.get_static_allocation(users_ids)
        )


# Example usage
if __name__ == "__main__":
    initial_allocations = np.array(
        [[0.3, 0.7], [0.6, 0.4], [0.5, 0.5]]
    )  # Initial allocations for 3 players and 2 items
    initial_prices = np.array([1, 1])  # Initial prices for 2 items
    gp_models = None  # Placeholder for Gaussian Process models
    virtual_income = 1  # Virtual income for each player under CEEI

    raise Exception("This file is not supposed to be executed directly.")
