from utils.plotting import *



FLOAT_TO_INT = 1024. / 1000. / 10000.

'''
    This class is the base class for all estimators.
'''
class Estimator:
    def __init__(self, resource_scale: None):
        self.allocator = None
        self.resource_scale = resource_scale

    def initialize(self, config_path: str, search_granularity: float):
        raise NotImplementedError("initialize is not implemented.")

    def cleanup(self):
        pass

    def set_allocator(self, allocator):
        self.allocator = allocator

    def msg_controller(self, msg):
        pass

    # read config file
    def read_config(self, config_file="config.json", verbose=False):
        raise NotImplementedError("read_config is not implemented.")

    # add a new datapoint
    def add_data(self, app_id: int, cache_in_mb: float, bw_in_gbps: float, performance: float):
        raise NotImplementedError("add_data is not implemented.")

    # Update model based on the added data (in self.measurements, for example)
    def update_model(self):
        raise NotImplementedError("update_model is not implemented.")

    # Update model based on a given dataset; this involves resetting the existing dataset
    def update_model_after_reset(self, app_id: int, dataset: list):
        raise NotImplementedError("update_model_after_reset is not implemented.")

    def predict(self, app_id: int, cache_in_mb: float, bw_in_gbps: float):
        raise NotImplementedError("predict is not implemented.")

    def store_model(self, app_id: int, model_path: str):
        pass

    def load_model(self, app_id: int, model_path: str):
        raise NotImplementedError("load_model is not implemented.")
