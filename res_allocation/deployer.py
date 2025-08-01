from utils.logger import Logger
import requests
from utils.config import Config

class Deployer:
    def __init__(self, config):
        self.config = config

    def deploy(self, resource_alloc):
        pass

class MemcachedDeployer(Deployer):
    def __init__(self, config):
        super().__init__(config)
        self.logger = Logger()
        self.logger.prepare_logger('memcached_deployer')
        self.physical_server_id = 1
        self.allocation_map = {
            "1": [420, 540],
            "2": [730, 910],
            "3": [100, 270],
            "4": [500, 620],
            "5": [910, 100]
        }
        self.timestamp = 0
        self.load_config()

    def cleanup(self):
        self.logger.close()

    def load_config(self):
        self.logger.log_msg(f"Deployer configuration: deploy URL: {self.config.url}{self.config.deploy_route}")

    def update_allocation_map(self, resource_alloc):
        # check the format of resource_alloc
        if not isinstance(resource_alloc, dict) or not resource_alloc:
            return
        # initial empty allocation map
        self.allocation_map = {}
        for user, resources in resource_alloc.items():
            if not isinstance(resources, dict):
                continue
            cache = resources.get("cache", 0)
            mem_bw = resources.get("mem_bw", 0)
            if not isinstance(cache, int) or not isinstance(mem_bw, int):
                continue
            self.allocation_map[user] = [cache, mem_bw]

    def assemble_command(self, resource_alloc, dummy_vm_id, _append_benchmark):
        self.update_allocation_map(resource_alloc)
        # Deprecated fields have been commented out
        json_alloc_data = {
            "allocation_map": self.allocation_map,
        }

        return json_alloc_data

    def deploy(self, resource_alloc, dummy_vm_id=False, append_benchmark=False):
        if not isinstance(resource_alloc, dict) or not resource_alloc:
            print(f"Invalid resource allocation: {resource_alloc}... SKIP")
            return

        url = f"{self.config.url}{self.config.deploy_route}"
        headers = {'Content-Type': 'application/json'}
        json_alloc_data = self.assemble_command(resource_alloc, dummy_vm_id, append_benchmark)
        self.logger.log_msg(f"Sending the configuration to the controller: {json_alloc_data} | {url}")
        response = requests.post(url, headers=headers, json=json_alloc_data)

        if response.status_code in [200, 202]:
            self.logger.log_msg(f"Succeeded to send allocation: {json_alloc_data}")
        else:
            self.logger.log_err(f"Failed to send the configuration. Status code: {response.status_code} | {response.text}")

class DummyDeployer(MemcachedDeployer):
    '''Dummy deployer for algorithm complexity evaluation.'''
    def deploy(self, resource_alloc, dummy_vm_id=False, append_benchmark=False):
        pass
