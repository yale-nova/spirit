from resource_monitor import MemcachedMindMonitor
from deployer import MemcachedDeployer
from multiprocessing import Process, Queue
from communication import ControlOperationTypes
from time import sleep
from utils.logger import Logger
from utils.config import Config
import argparse
import os
import time
from estimators.runtime_estimator import RuntimeEstimator

def wait_for_msg(queue: Queue, msg, verbose=False):
    if verbose:
        print("Start waiting for message: {}".format(msg))
    while True:
        msg = queue.get()
        if msg == ControlOperationTypes.START:
            break
        if verbose:
            print("{} | Received message: {}".format(queue, msg), flush=True)

def parse_args():
    parser = argparse.ArgumentParser(description="Parse log files for experiment setup and result.")
    parser.add_argument("--config", help="Path to the configuration file.",
                            type=str, default="config.json")
    parser.add_argument("--allocator", help="Type of allocator to use in [spirit, static, partial]", type=str, default="none")
    parser.add_argument("--alloc_interval", help="Allocation interval in seconds", type=int, default=15)
    return parser.parse_args()

def move_logs(args, allocation_interval_in_sec: int=10):
    config = Config().load_config(config_path=args.config)
    if config.allocation_parameters is not None and "allocation_interval_in_sec" in config.allocation_parameters:
        allocation_interval_in_sec = config.allocation_parameters["allocation_interval_in_sec"]
        print(f"Config::Allocation interval is set to {allocation_interval_in_sec} sec.")

    # = Logging and Clean up =
    # move files: *.html, logs/*.log to logs/<allocation_interval_in_sec>/<timestamp>, ...
    # Create a new directory with timestamp
    allocation_interval_in_sec = float(allocation_interval_in_sec)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    new_dir = f"logs/{config.cluster_name}/{args.allocator}/App{len(config.benchmark_map)}_int_{allocation_interval_in_sec:.1f}sec/{timestamp}_{config.cache_in_mb}mb_{config.mem_bw_in_mbps}mbps"
    os.system(f"mkdir -p {new_dir}")
    # move *.html files
    os.system(f"mv *.html {new_dir}/.")
    # move *.log files
    os.system(f"mv logs/*.log {new_dir}/.")

if __name__ == "__main__":
    # parse arguments
    args = parse_args()
    move_logs(args)
