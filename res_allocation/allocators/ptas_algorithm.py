import copy
import time
import math
import numpy as np

class ResourceLimited:
    """
    Data class to indicate if there was resource limiting.
    """
    def __init__(self, cache_min_limit=False, cache_max_limit=False,
                 mem_bw_min_limit=False, mem_bw_max_limit=False):
        self.cache_min_limit = cache_min_limit
        self.cache_max_limit = cache_max_limit
        self.mem_bw_min_limit = mem_bw_min_limit
        self.mem_bw_max_limit = mem_bw_max_limit

    def update(self, other):
        """
        Update the resource limits based on another ResourceLimited instance.
        """
        self.cache_min_limit = self.cache_min_limit or other.cache_min_limit
        self.cache_max_limit = self.cache_max_limit or other.cache_max_limit
        self.mem_bw_min_limit = self.mem_bw_min_limit or other.mem_bw_min_limit
        self.mem_bw_max_limit = self.mem_bw_max_limit or other.mem_bw_max_limit

    def is_resource_limited(self):
        """
        Check if any resource is limited.
        """
        return (
            self.cache_min_limit or self.cache_max_limit or
            self.mem_bw_min_limit or self.mem_bw_max_limit
        )

    # print format
    def __str__(self):
        return (
            f"ResourceLimited(cache_limit=({self.cache_min_limit}, {self.cache_max_limit}), "
            f"mem_bw_limit=({self.mem_bw_min_limit}, {self.mem_bw_max_limit}))"
        )

def check_resource_limits(cache_alloc, mem_bw_alloc, resource_scale, max_util=False, resource_limited=None, margin={"cache": 0.0, "mem_bw": 0.0}):
    """
    Check if the allocation satisfies the minimum and maximum resource requirements.

    Parameters:
    @cache_alloc (float): The cache allocation (normalized)
    @mem_bw_alloc (float): The memory bandwidth allocation (normalized)
    @resource_scale (dict): Dictionary containing resource scale information
    @max_util (float, optional): The current maximum utility
    @resource_limited (ResourceLimited, optional): Object to track resource limiting
    @margin (dict, optional): Margin by which resource limits can be exceeded
                              It should be given as the same scale as cache and mem_bw

    Returns:
    - bool: True if allocation is within limits (or within margin), False otherwise
    """
    # Calculate actual resource values
    cache_actual = cache_alloc * resource_scale["cache"]
    mem_bw_actual = mem_bw_alloc * resource_scale["mem_bw"]
    cache_margin = margin["cache"] * resource_scale["cache"]
    mem_bw_margin = margin["mem_bw"] * resource_scale["mem_bw"]

    # Calculate margin-adjusted limits
    min_cache_with_margin = resource_scale["min_cache"] - cache_margin
    max_cache_with_margin = resource_scale["max_cache"] + cache_margin
    min_mem_bw_with_margin = resource_scale["min_mem_bw"] - mem_bw_margin
    max_mem_bw_with_margin = resource_scale["max_mem_bw"] + mem_bw_margin

    # Check cache limits with margin
    if (cache_actual < min_cache_with_margin or
        cache_actual > max_cache_with_margin):
        # Update resource_limited if provided and the utility is significant
        if resource_limited is not None and max_util:
            new_limit = ResourceLimited(cache_min_limit=cache_actual < min_cache_with_margin,
                                        cache_max_limit=cache_actual > max_cache_with_margin)
            resource_limited.update(new_limit)
        return False

    # Check memory bandwidth limits with margin
    if (mem_bw_actual < min_mem_bw_with_margin or
        mem_bw_actual > max_mem_bw_with_margin):
        # Update resource_limited if provided and the utility is significant
        if resource_limited is not None and max_util:
            new_limit = ResourceLimited(mem_bw_min_limit=mem_bw_actual < min_mem_bw_with_margin,
                                        mem_bw_max_limit=mem_bw_actual > max_mem_bw_with_margin)
            resource_limited.update(new_limit)
        return False

    return True

def clip_allocation(cache_alloc, mem_bw_alloc, resource_scale):
    """
    Clip the allocation to be within the resource scale limits.
    Parameters:
    - cache_alloc (float): Cache allocation
    - mem_bw_alloc (float): Memory bandwidth allocation
    - resource_scale (dict): Dictionary containing resource scale information
    Returns:
    - cache_alloc (float): Clipped cache allocation
    """
    cache_actual = cache_alloc * resource_scale["cache"]
    mem_bw_actual = mem_bw_alloc * resource_scale["mem_bw"]
    cache_alloc = min(resource_scale["max_cache"], cache_actual)
    mem_bw_alloc = min(resource_scale["max_mem_bw"], mem_bw_actual)
    return cache_alloc / resource_scale["cache"], mem_bw_alloc / resource_scale["mem_bw"]

def check_last_allocation(
    user_id,
    last_allocation,
    budget,
    epsilon,
    price_vector,
    resource_scale,
    estimator,
    max_util=float(0.),
    logger=None
):
    """
    Check if the last allocation can be reused or needs to be adjusted based on the current budget.

    Parameters:
    @user_id (str): The ID of the user
    @last_allocation (dict): Dictionary containing the last allocation
    @budget (float): Available budget
    @epsilon (float): The epsilon parameter to control the granularity of the partition
    @price_vector (dict): Dictionary containing the prices for cache and memory bandwidth
    @resource_scale (dict): Dictionary containing resource scale information
    @estimator: Utility estimator
    @max_util (float): Current maximum utility
    @logger: Logger object

    Returns:
    - best_alloc (dict): Updated allocation based on last allocation if better than max_util
    - updated_max_util (float): Updated max utility if the allocation is better
    """
    if last_allocation is None or user_id not in last_allocation:
        return None, max_util

    last_alloc = copy.deepcopy(last_allocation[user_id])
    cache_alloc = last_alloc["cache"]
    mem_bw_alloc = None

    # Determine how to calculate mem_bw based on budget
    if budget - float(cache_alloc) * price_vector["cache"] > 0:
        # Enough budget for the previous cache allocation, calculate new mem_bw
        mem_bw_alloc = int((budget - float(cache_alloc) * price_vector["cache"]) / max(1e-6, price_vector["mem_bw"]) / epsilon)
        mem_bw_alloc = max(1., min(int(1.0 / epsilon), mem_bw_alloc)) * epsilon
        # Clip it into the resource scale's maximum bw
        mem_bw_alloc = min(resource_scale["max_mem_bw"], mem_bw_alloc * resource_scale["mem_bw"])
        mem_bw_alloc = mem_bw_alloc / resource_scale["mem_bw"]
    else:
        # Not enough budget, maintain the same ratio between cache and mem_bw
        cache_bw_ratio = last_alloc["cache"] / last_alloc["mem_bw"]

        # First check if we can satisfy minimum resource requirements
        min_cache_norm = resource_scale["min_cache"] / resource_scale["cache"]
        min_mem_bw_norm = resource_scale["min_mem_bw"] / resource_scale["mem_bw"]

        # Calculate two allocations: one based on min cache, one based on min memory bandwidth
        # Then choose the one that requires more budget as our baseline
        min_cache_alloc = max(epsilon, min_cache_norm)
        corresponding_mem_bw = min_cache_alloc / cache_bw_ratio

        min_mem_bw_alloc = max(epsilon, min_mem_bw_norm)
        corresponding_cache = min_mem_bw_alloc * cache_bw_ratio

        # Calculate costs for both options
        option1_cost = min_cache_alloc * price_vector["cache"] + corresponding_mem_bw * price_vector["mem_bw"]
        option2_cost = corresponding_cache * price_vector["cache"] + min_mem_bw_alloc * price_vector["mem_bw"]

        # If we can't afford either minimum allocation, choose the one we can get closest to
        if option1_cost > budget and option2_cost > budget:
            # Use whichever gets us closer to meeting requirements
            if option1_cost <= option2_cost:
                # Prioritize min cache and calculate mem_bw with remaining budget
                cache_alloc = min_cache_alloc
                mem_bw_alloc = (budget - cache_alloc * price_vector["cache"]) / price_vector["mem_bw"]
            else:
                # Prioritize min mem_bw and calculate cache with remaining budget
                mem_bw_alloc = min_mem_bw_alloc
                cache_alloc = (budget - mem_bw_alloc * price_vector["mem_bw"]) / price_vector["cache"]
        else:
            # We can afford at least one of the minimum allocations
            # Use the standard ratio-based allocation, but make sure it meets minimum requirements
            cache_alloc = budget / (price_vector["cache"] + price_vector["mem_bw"] / cache_bw_ratio)
            mem_bw_alloc = cache_alloc / cache_bw_ratio

            # Check if the allocation meets minimum requirements
            if cache_alloc < min_cache_norm:
                cache_alloc = min_cache_norm
                mem_bw_alloc = (budget - cache_alloc * price_vector["cache"]) / price_vector["mem_bw"]
            elif mem_bw_alloc < min_mem_bw_norm:
                mem_bw_alloc = min_mem_bw_norm
                cache_alloc = (budget - mem_bw_alloc * price_vector["mem_bw"]) / price_vector["cache"]

        # Round to epsilon grid
        cache_alloc = int(cache_alloc / epsilon) * epsilon
        mem_bw_alloc = int(mem_bw_alloc / epsilon) * epsilon

        # Ensure minimum values are not less than epsilon
        cache_alloc = max(epsilon, cache_alloc)
        mem_bw_alloc = max(epsilon, mem_bw_alloc)

    # Check if allocation is within resource limits
    if check_resource_limits(
        cache_alloc, mem_bw_alloc, resource_scale
    ):
        # Calculate utility for this allocation
        last_util = estimator.get_estimation(
            user_id,
            cache_alloc * resource_scale["cache"],
            mem_bw_alloc * resource_scale["mem_bw"],
        )

        need_update = last_util > max_util

        # Log the allocation attempt
        if logger:
            cost = price_vector["cache"] * cache_alloc + price_vector["mem_bw"] * mem_bw_alloc

            # Log differently based on whether we're using the original or ratio-based allocation
            if budget - float(last_alloc["cache"]) * price_vector["cache"] > 0:
                logger.log_msg(
                    f"User: {user_id} | "
                    f"{('Using' if need_update else 'Skipping')} "
                    f'last allocation: cache[{cache_alloc}], mem_bw[{mem_bw_alloc}] | '
                    f"Utility: {last_util} <-> {max_util}| "
                    f"Cost: {cost}"
                )
            else:
                cache_bw_ratio = last_alloc["cache"] / last_alloc["mem_bw"]
                logger.log_msg(
                    f"User: {user_id} | "
                    f"{('Using' if need_update else 'Skipping')} "
                    f"scaled allocation with ratio {cache_bw_ratio:.3f}: "
                    f"cache[{cache_alloc:.4f}], mem_bw[{mem_bw_alloc:.4f}] | "
                    f"Utility: {last_util} | "
                    f"Cost: {cost:.4f}"
                )

        # Update best allocation if the utility is better
        if need_update:
            return {"cache": cache_alloc, "mem_bw": mem_bw_alloc}, last_util
    elif logger:
        # Log when resource limits are violated
        if budget - float(last_alloc["cache"]) * price_vector["cache"] > 0:
            logger.log_msg(
                f"User: {user_id} | Skipping resource-limited last allocation: "
                f"cache[{cache_alloc}], mem_bw[{mem_bw_alloc}]"
            )
        else:
            cache_bw_ratio = last_alloc["cache"] / last_alloc["mem_bw"]
            logger.log_msg(
                f"User: {user_id} | Skipping resource-limited scaled allocation with ratio {cache_bw_ratio:.3f}: "
                f"cache[{cache_alloc:.4f}], mem_bw[{mem_bw_alloc:.4f}]"
            )

    return None, max_util

def ptas_algorithm(
    estimator,
    user_id,
    num_user,
    epsilon: float,
    budget,
    price_vector,
    last_allocation=None,
    last_static_allocation=None,
    resource_scale={},
    verbose=False,
    noise_mean=0,
    noise_std_dev=0.1,
    search_range={"cache": [0.45, 0.55], "mem_bw": [0.45, 0.55]},
    guide_factor=0.1,
    logger=None,
    max_ubound=True,
    explore_adv=0.025,
    explore_adv_ratio=0.25,    # 0.25; Or 0 to disable
    explore_adv_intense_ratio=0.5,
    verbose_n_user=8,
    margin_in_budget=0.97,   # for many contending apps, typically 0.96-1.0
                             # for fairness, everyone must have the same margin
                             # 0.99 for 2 apps, 0.97 for 5 apps, 0.96 for 6 apps
    reallocation_threshold = 1.005,   # reallocate if the new allocation is better by this threshold
    prefer_last_allocation=True,
    allocation_update_clip=0.05,
    clip_to_min_max=True,
):
    """
    Polynomial Time Approximation Scheme (PTAS) algorithm to allocate resources
    in a way that maximizes utility while fully spending the budget.

    Parameters:
    @epsilon (float): The epsilon parameter to control the granularity of the partition.
    @price_vector (dict): A dictionary containing the prices for cache size and memory bandwidth.
    @explore_adv_ratio (float): The ratio of the exploration happens basd on the coverage
      - Set this value to < 0. to disable coverage-based exploration
    @explore_adv_intense_ratio (float): The ratio of the exploration used when the coverage is low

    Returns:
    - best_alloc (dict): The allocation that maximizes utility.
    - checked_point (int): Number of data points checked
    """
    # Initialize variables
    max_util = float(0.)
    best_alloc = None
    resource_limited = ResourceLimited()

    # Ensure resource_scale has all required fields
    if "min_cache" not in resource_scale:
        resource_scale["min_cache"] = 0
    if "max_cache" not in resource_scale:
        resource_scale["max_cache"] = float('inf')
    if "min_mem_bw" not in resource_scale:
        resource_scale["min_mem_bw"] = 0
    if "max_mem_bw" not in resource_scale:
        resource_scale["max_mem_bw"] = float('inf')

    # Log arguments - logger is now expected to be present
    if logger:  # Keep this check for safety but expect logger to be provided
        logger.log_msg(
            f"User: {user_id} | Budget: {budget} | Price: {price_vector} | "
            f"Epsilon: {epsilon} | Search range: {search_range} | Res-scale: {resource_scale} | "
            f"Last static-alloc: {last_static_allocation[user_id] if last_static_allocation and user_id in last_static_allocation else None} | "
            f"Last alloc: {last_allocation[user_id] if last_allocation and user_id in last_allocation else None}"
        )

    # Check the static allocation
    if last_static_allocation is not None and user_id in last_static_allocation:
        best_alloc = copy.deepcopy(last_static_allocation[user_id])
        # {"cache": cache_alloc, "mem_bw": mem_bw_alloc}

        # if the current allocation is too far from the static allocation,
        # the best alloc should be on that direction
        if best_alloc["cache"] < search_range["cache"][0]:
            best_alloc["cache"] = search_range["cache"][0]
        elif best_alloc["cache"] > search_range["cache"][1]:
            best_alloc["cache"] = search_range["cache"][1]
        best_alloc["mem_bw"] = int((budget - float(best_alloc["cache"]) * price_vector["cache"]) / max(1e-6, price_vector["mem_bw"]) / epsilon)
        best_alloc["mem_bw"] = max(1., min(int(1.0 / epsilon), best_alloc["mem_bw"])) * epsilon

        # If mem_bw violates minimum requirement, we need to adjust the cache size
        if (
            resource_scale["max_mem_bw"] <
            best_alloc["mem_bw"] * resource_scale["mem_bw"]
            or
            resource_scale["min_mem_bw"] >
            best_alloc["mem_bw"] * resource_scale["mem_bw"]
        ):
            best_alloc["mem_bw"] = int(min(
                resource_scale["max_mem_bw"],
                max(resource_scale["min_mem_bw"], best_alloc["mem_bw"] * resource_scale["mem_bw"])
            ) / resource_scale["mem_bw"])
            # adjust cache based on the new mem_bw
            best_alloc["cache"] = int((budget - float(best_alloc["mem_bw"]) * price_vector["mem_bw"]) / price_vector["cache"])
            # if cache violates minimum or maximum requirement, use static allocation
            if (
                resource_scale["max_cache"] <
                best_alloc["cache"] * resource_scale["cache"]
                or
                resource_scale["min_cache"] >
                best_alloc["cache"] * resource_scale["cache"]
            ):
                best_alloc = copy.deepcopy(last_static_allocation[user_id])
        max_util = estimator.get_estimation(
            user_id,
            best_alloc["cache"] * resource_scale["cache"],
            best_alloc["mem_bw"] * resource_scale["mem_bw"],
        )
        # if the static allocation is more than 1% better than the current allocation
        max_util = max_util / reallocation_threshold
        # print the current allocation
        if logger:
            logger.log_msg(
                f"User: {user_id} | Static allocation: cache[{best_alloc['cache']}], mem_bw[{best_alloc['mem_bw']}] | "
                f"Utility: {max_util} | Cost: {price_vector['cache'] * best_alloc['cache'] + price_vector['mem_bw'] * best_alloc['mem_bw']}"
            )

    # Apply margin to the budget
    budget *= margin_in_budget

    # Compare it with the last allocation
    last_alloc, max_util = check_last_allocation(
        user_id,
        last_allocation,
        budget,
        epsilon,
        price_vector,
        resource_scale,
        estimator,
        max_util,
        logger
    )
    if last_alloc:
        best_alloc = last_alloc

    fair_share = float(budget)

    # Determine chunk sizes based on epsilon
    cache_chunk_size = epsilon
    mem_bw_chunk_size = epsilon

    # Loop through each possible allocation of cache and memory bandwidth
    # Note) the allocation cannot be zero (i.e., at least 1 chunk)
    checked_point = 0
    closest_to_last = None
    closest_to_last_util = float("-inf")
    gap_to_last = float("inf")

    for cache in range(1, int(1.0 / cache_chunk_size) + 1):
        if (
            cache_chunk_size * cache < search_range["cache"][0]
            or cache_chunk_size * cache > search_range["cache"][1]
        ):
            continue
        checked_point += 1
        if float(cache) * cache_chunk_size * price_vector["cache"] > budget:
            break

        # Compute maximum mem_bw within the budget
        mem_bw = int((budget - float(cache) * cache_chunk_size * price_vector["cache"]) / max(1e-6, price_vector["mem_bw"]) / mem_bw_chunk_size)
        mem_bw = min(int(1.0 / mem_bw_chunk_size), mem_bw)

        # Do not use budget for the fair share
        if abs(fair_share - float(cache) * cache_chunk_size) < 1e-6:
            mem_bw = int(fair_share / mem_bw_chunk_size)

        # Calculate the normalized allocation (0.0 to 1.0)
        cache_alloc = float(cache) * cache_chunk_size
        mem_bw_alloc = float(mem_bw) * mem_bw_chunk_size

        # Check if current allocation satisfies minimum resource
        # - for memory the margin should be based on the bandwidth needs under the next cache iteration
        # - memory bandwidth is rounded, so we need to set enough margin
        mem_bw_margin = 2 * cache_chunk_size * price_vector["cache"] / max(1e-6, price_vector["mem_bw"])
        if not check_resource_limits(
            cache_alloc, mem_bw_alloc, resource_scale,
            margin={"cache": cache_chunk_size + 1e-6, "mem_bw": mem_bw_margin + 1e-6}):
            if logger:
                cur_alloct = {"cache": cache_alloc, "mem_bw": mem_bw_alloc}
                logger.log_msg(
                    f"Early skipping resource limited allocation: {cur_alloct} | Cost: {price_vector['cache'] * cache_alloc + price_vector['mem_bw'] * mem_bw_alloc}"
                )
            continue

        # Clip the cache and mem_bw based on the max and min limits
        if clip_to_min_max:
            cache_alloc, mem_bw_alloc = clip_allocation(
                cache_alloc, mem_bw_alloc, resource_scale
            )

        # Calculate cost of the current allocation
        total_cost = (
            cache_alloc * price_vector["cache"]
            + mem_bw_alloc * price_vector["mem_bw"]
        )

        # Check if the allocation is within the budget
        if total_cost * margin_in_budget <= budget:
            # Get utility estimation
            est_util = estimator.get_estimation(
                user_id,
                cache_alloc * resource_scale["cache"],
                mem_bw_alloc * resource_scale["mem_bw"],
            )

            # If cache size decreases, it may cause temporary slowdown
            if last_allocation and reallocation_threshold > 1.0 and cache_alloc < last_allocation[user_id]["cache"]:
                est_util /= reallocation_threshold

            # Check if current allocation satisfies minimum resource requirements
            if not check_resource_limits(cache_alloc, mem_bw_alloc, resource_scale, est_util > max_util, resource_limited):
                if logger:
                    cur_alloct = {"cache": cache_alloc, "mem_bw": mem_bw_alloc}
                    logger.log_msg(
                        f"Skipping resource limited allocation: {cur_alloct} | Utility: {est_util} > {max_util} | Cost: {total_cost} | Limits: {resource_limited}"
                    )
                continue

            # Track allocation closest to last allocation
            if last_allocation is not None and user_id in last_allocation:
                distance = (cache_alloc - last_allocation[user_id]["cache"]) ** 2
                distance += (mem_bw_alloc - last_allocation[user_id]["mem_bw"]) ** 2
                if distance < gap_to_last:
                    closest_to_last = {"cache": cache_alloc, "mem_bw": mem_bw_alloc}
                    gap_to_last = distance
                    closest_to_last_util = est_util

            if verbose:
                print(
                    "Estimate utility: {} for cache: {} and mem_bw: {} || cost: {}".format(
                        est_util, cache_alloc, mem_bw_alloc, total_cost
                    )
                )

            if logger and estimator.get_app_num() <= verbose_n_user:
                logger.log_msg(
                    "App: {} Estimate utility: {} for cache: {} / {} mb and mem_bw: {} / {} gbps || cost: {}".format(
                        user_id, est_util,
                        cache_alloc, cache_alloc * resource_scale["cache"],
                        mem_bw_alloc, mem_bw_alloc * resource_scale["mem_bw"],
                        total_cost
                    )
                )

            # Update max_util and best_alloc if the current utility is greater
            if est_util > max_util:
                max_util = est_util
                best_alloc = {"cache": cache_alloc, "mem_bw": mem_bw_alloc}
                if logger:
                    logger.log_msg(
                        f"Updated best allocation: {best_alloc} | Utility: {max_util} | Cost: {total_cost}"
                    )
                # If resource limited is touched and has better utility,
                # ignore noises in the mid points (early exit optimization)
                if resource_limited.is_resource_limited():
                    break
            else:
                if logger:
                    cur_alloct = {"cache": cache_alloc, "mem_bw": mem_bw_alloc}
                    logger.log_msg(
                        f"Not updated best allocation: {cur_alloct} | Utility: {est_util} < {max_util} | Cost: {total_cost}"
                    )
        else:
            # unlikely, error case
            if logger:
                logger.log_msg(
                    "OUT OF BUDGET :: App: {} | cache: {} | mem_bw: {} | cost: {} | budget: {}".format(
                        user_id,
                        cache_alloc,
                        mem_bw_alloc,
                        total_cost,
                        budget,
                    )
                )

    # If the best allocation is not significantly better than the last allocation,
    # use the one closest to the last allocation
    if prefer_last_allocation:
        if closest_to_last_util is not None and closest_to_last is not None:
            if max_util / reallocation_threshold < closest_to_last_util:
                best_alloc = closest_to_last
                max_util = closest_to_last_util

    # Calculate cost of the best allocation
    if best_alloc:
        total_cost = (
            best_alloc["cache"] * price_vector["cache"]
            + best_alloc["mem_bw"] * price_vector["mem_bw"]
        )

        if verbose:
            print(
                "Allocation for user {} | util: {}, alloc: {}, cost: {}".format(
                    user_id,
                    max_util,
                    best_alloc,
                    total_cost,
                )
            )
            print(
                "Checked data points: {} || chunk size: {} / {}".format(
                    checked_point, cache_chunk_size, mem_bw_chunk_size
                )
            )

        if logger:
            logger.log_msg(
                "Allocation for user {} | util: {}, alloc: {}, cost: {}".format(
                    user_id,
                    max_util,
                    best_alloc,
                    total_cost,
                )
            )

    return best_alloc, checked_point, resource_limited

def get_static_allocation(num_users):
    """
    Return a static allocation that divides resources equally among users
    """
    return {
        "cache": 1.0 / float(num_users),
        "mem_bw": 1.0 / float(num_users),
    }

def get_search_dict(cur_alloc, search_range: float):
    """
    Create a search range dictionary based on the current allocation
    """
    return {
        "cache": [max(0.0, cur_alloc["cache"] - search_range), min(1.0, cur_alloc["cache"] + search_range)],
        "mem_bw": [max(0.0, cur_alloc["mem_bw"] - search_range), min(1.0, cur_alloc["mem_bw"] + search_range)],
    }