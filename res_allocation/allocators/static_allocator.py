from time import sleep
import numpy as np
import time
import numpy as np
from utils.logger import Logger
from .allocator_base import ResourceAllocator, AllocatorParams

class StaticAllocatorParams(AllocatorParams):
    def __init__(self,
                 allocation_interval_in_sec: float, init_phase_interval: float=2,
                 search_granularity: float=0.125/2., search_range: float=0.1, search_range_delta: float=0.005,
                 search_range_max: float=0.4,
                 measurements_per_alloc: float = 0.25,  # it's per second
                 ) -> None:
        super().__init__(search_granularity, allocation_interval_in_sec, requires_retrain=False)
        self.init_phase_interval = init_phase_interval
        self.search_range = search_range
        self.search_range_delta = search_range_delta
        self.search_range_max = search_range_max
        self.measurements_per_alloc = int(allocation_interval_in_sec * measurements_per_alloc)

class StaticAllocator(ResourceAllocator):
### ===================== internal functions ====================== ###
    def __init__(self, config_path: str, resource_scale: {} = { "cache": 1,"mem_bw": 1 }, estimator=None, monitor=None, deployer=None):
        super().__init__(config_path, resource_scale, estimator, monitor, deployer)

    def initialize(self, param: AllocatorParams=StaticAllocatorParams(1.0)):
        super().initialize(param)
        self.parameters: StaticAllocatorParams = param

    def allocate_and_parse(self, skip_monitoring=False):
        start_time = time.time_ns()

        # Get VM to app mapping from monitor
        vm_to_app_map = self.monitor.get_vm_to_app_mapping()
        
        # Get all user IDs
        users = self.estimator.get_app_ids()
        allocation = {}
        extract_keys = ["cache", "mem_bw"]
        
        # Get the number of VMs from the config
        num_vms = self.get_num_vms()
        
        # If VM mapping is empty or not available, use the original approach
        if not vm_to_app_map:
            self.logger.log_msg(f"VM to app mapping not available. Using flat allocation across all apps. Number of VMs: {num_vms}")
            users_per_vm = max(1, len(users) // num_vms)
            for idx, user in enumerate(users):
                allocation[user] = {}
                vm_idx = idx // users_per_vm
                users_in_this_vm = users_per_vm if vm_idx < num_vms else len(users) - vm_idx * users_per_vm
                for key in extract_keys:
                    allocation[user][key] = self.resource_scale[key] / float(users_in_this_vm)
                    if key == "mem_bw":
                        allocation[user][key] *= 1024   # gb to mb
                    allocation[user][key] = int(allocation[user][key])
        else:
            self.logger.log_msg(f"Using per-VM resource allocation with VM mapping: {vm_to_app_map}")
            
            # Allocate resources per VM first, then distribute within each VM
            for vm_id, app_ids in vm_to_app_map.items():
                # Filter out app_ids that aren't in our user list
                vm_apps = [app_id for app_id in app_ids if app_id in users]
                
                if not vm_apps:
                    continue
                
                # Each VM gets the full resource allocation (10GB cache, 7.5Gbps mem_bw)
                # Then divide those resources equally among the apps in this VM
                for app_id in vm_apps:
                    allocation[app_id] = {}
                    for key in extract_keys:
                        allocation[app_id][key] = self.resource_scale[key] / float(len(vm_apps))
                        if key == "mem_bw":
                            allocation[app_id][key] *= 1024  # gb to mb
                        allocation[app_id][key] = int(allocation[app_id][key])
            
            # Handle any users not assigned to VMs
            unassigned_users = set(users) - set(user for app_list in vm_to_app_map.values() for user in app_list)
            if unassigned_users:
                self.logger.log_msg(f"Warning: Some users are not assigned to any VM: {unassigned_users}")
                for user in unassigned_users:
                    allocation[user] = {}
                    for key in extract_keys:
                        allocation[user][key] = self.resource_scale[key] / float(len(unassigned_users))
                        if key == "mem_bw":
                            allocation[user][key] *= 1024   # gb to mb
                        allocation[user][key] = int(allocation[user][key])
                
        self.logger.log_msg(f"Per-VM static allocation: {allocation}")
        return allocation
