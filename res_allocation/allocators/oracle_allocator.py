from time import sleep
import numpy as np
import time
import numpy as np
from utils.logger import Logger
from utils.helpers import create_app_to_vm_map
from .allocator_base import ResourceAllocator, AllocatorParams


class OracleAllocatorParams(AllocatorParams):
    def __init__(
        self,
        allocation_interval_in_sec: float,
        init_phase_interval: float = 2,
        search_granularity: float = 0.125 / 2.0,
        search_range: float = 0.1,
        search_range_delta: float = 0.005,
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
            0.2  # DUMMY for this allocator
        )


class OracleAllocator(ResourceAllocator):
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

    def initialize(self, param: AllocatorParams = OracleAllocatorParams(1.0)):
        super().initialize(param)
        self.parameters: OracleAllocatorParams = param

    def get_oracle_allocation(self, alloc_remaining=False):
        allocation = {}
        # "profiles":
        # [{"user_id": 1, "file": "dummy.joblib", "sensitivity": "mem_bw", "oracle_allocation": {"cache": 2048, "mem_bw": 3948}}, ...]
        config_profile = self.estimator.get_config()
        try:
            if len(config_profile) > 0:
                # Extract basic allocation from configuration
                for profile in config_profile:
                    allocation[profile.get("user_id")] = profile.get("oracle_allocation")
                    
                # Check if we need to normalize the allocation
                if alloc_remaining:
                    # Get VM to app mapping
                    vm_to_app_map = self.monitor.get_vm_to_app_mapping()
                    app_to_vm_map = create_app_to_vm_map(vm_to_app_map)
                    
                    # Group users by VM
                    vm_users = {}
                    for user in allocation.keys():
                        vm = app_to_vm_map.get(user)
                        if vm:
                            if vm not in vm_users:
                                vm_users[vm] = []
                            vm_users[vm].append(user)
                        
                    # Normalize allocations per VM
                    for vm, users in vm_users.items():
                        # Calculate total resources requested for this VM
                        total_cache = sum([allocation[user].get("cache", 0) for user in users])
                        total_mem_bw = sum([allocation[user].get("mem_bw", 0) for user in users])
                        
                        # Normalize if the VM's resources are exceeded
                        if total_cache > self.resource_scale["cache"] or total_mem_bw > self.resource_scale["mem_bw"] * 1024:
                            self.logger.log_msg(f"Normalizing resources for VM {vm}: cache {total_cache}/{self.resource_scale['cache']}, mem_bw {total_mem_bw}/{self.resource_scale['mem_bw']}")
                            
                            for user in users:
                                user_alloc = allocation[user]
                                if total_cache > 0:
                                    user_alloc["cache"] = int(user_alloc.get("cache", 0) * self.resource_scale["cache"] / total_cache)
                                if total_mem_bw > 0:
                                    user_alloc["mem_bw"] = int(user_alloc.get("mem_bw", 0) * 1024 * self.resource_scale["mem_bw"] / total_mem_bw)
                
                self.logger.log_msg(f"Oracle allocation: {allocation}")
                return allocation
        except Exception as e:
            self.logger.log_msg(f"Error in get_oracle_allocation: {str(e)}")
            return None

    def allocate_and_parse(self, skip_monitoring=False):
        start_time = time.time_ns()

        # allocation
        users = self.estimator.get_app_ids()
        allocation = {}

        # check if explicit allocaiton is available
        alloc = self.get_oracle_allocation()
        if alloc is not None:
            return alloc

        # genuine sensitivity info so that we can do oracle allocation
        sensitivity = self.estimator.get_sensitivity()
        total_user = float(len(users))
        mem_sensitive_users = 0
        min_cache_for_mem_sensitive = 0
        if 'mem_bw' in sensitivity:
            min_cache_for_mem_sensitive = len(sensitivity['mem_bw']) * self.resource_scale['min_cache']
            mem_sensitive_users = len(sensitivity['mem_bw'])
        cache_sensitive_users = 0
        min_mem_bw_for_cache_sensitive = 0
        if 'cache' in sensitivity:
            min_mem_bw_for_cache_sensitive = len(sensitivity['cache']) * self.resource_scale['min_mem_bw']  # stored in Gbps
            cache_sensitive_users = len(sensitivity['cache'])

        # allocation per sensitivity type
        alloc_sensitive = {}
        alloc_sensitive['cache'] = {'cache': (self.resource_scale['cache'] - min_cache_for_mem_sensitive) / cache_sensitive_users, 'mem_bw': self.resource_scale['min_mem_bw']}
        alloc_sensitive['mem_bw'] = {'cache': self.resource_scale['min_cache'], 'mem_bw': (self.resource_scale['mem_bw'] - min_mem_bw_for_cache_sensitive) / mem_sensitive_users}

        # for user in users:
        sensitivity_types = ['cache', 'mem_bw']
        for user in users:
            allocation[user] = {}
            for sensitivity_type in sensitivity_types:
                if sensitivity_type in sensitivity and user in sensitivity[sensitivity_type]:
                    allocation[user]['cache'] = int(alloc_sensitive[sensitivity_type]['cache'])
                    allocation[user]['mem_bw'] = int(alloc_sensitive[sensitivity_type]['mem_bw'] * 1024)
                    break
        # print out allocation
        self.logger.log_msg("Oracle allocation in actual resource unit (MB, Mbps): {}".format(allocation))
        return allocation
