import json

class Config:
    def __init__(self) -> None:
        self.url = "http://localhost"
        self.collect_route = "/collect"
        self.deploy_route = "/deploy"
        self.users_to_user_ids = {}
        self.users_to_profile_file = {}
        self.raw_json = None
        self.allocation_parameters = None
        self.profiles = None

    def load_config(self, config_path: str):
        with open(config_path, 'r') as f:
            data = json.load(f)
            self.raw_json = data
            # cluster related
            if "cluster" in data:
                self.cluster_name = data["cluster"]["name"]
                self.cache_in_mb = data["cluster"]["total_cache_in_mb"]
                self.mem_bw_in_mbps = data["cluster"]["total_mem_bw_in_mbps"]
                self.min_cache_in_mb = data["cluster"]["min_cache_in_mb"]
                self.max_cache_in_mb = data["cluster"]["max_cache_in_mb"] if "max_cache_in_mb" in data["cluster"].keys() else data["cluster"]["total_cache_in_mb"]
                self.min_mem_bw_in_mbps = data["cluster"]["min_mem_bw_in_mbps"]
                self.max_mem_bw_in_mbps = data["cluster"]["max_mem_bw_in_mbps"] if "max_mem_bw_in_mbps" in data["cluster"].keys() else data["cluster"]["total_mem_bw_in_mbps"]
            # resource controller related
            if "resource_controller" in data:
                self.url = data["resource_controller"]["base_url"]
                self.collect_route = data["resource_controller"]["collect_route"]
                self.deploy_route = data["resource_controller"]["deploy_route"]
            if "benchmark_map" in data:
                self.benchmark_map = data["benchmark_map"]
            if "allocation_parameters" in data:
                self.allocation_parameters = data["allocation_parameters"]
            if "profiles" in data:
                self.profiles = data["profiles"]
        return self

    def __str__(self):
        return f'Config(url={self.url}, collect_route={self.collect_route}, deploy_route={self.deploy_route}, users_to_user_ids={self.users_to_user_ids}, raw_json={self.raw_json})'
