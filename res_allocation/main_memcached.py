from resource_monitor import MemcachedMindMonitor
from deployer import MemcachedDeployer
from utils.config import Config
import argparse
from estimators.runtime_estimator import RuntimeEstimator
from metrics_reset_server import MetricsResetServer

def parse_args():
    parser = argparse.ArgumentParser(description="Parse log files for experiment setup and result.")
    parser.add_argument("--config", help="Path to the configuration file.",
                            type=str, default="config.json")
    parser.add_argument("--allocator", help="Type of allocator to use in [spirit, static, partial, oracle, inc-trade, fij-trade]", type=str, default="none")
    parser.add_argument("--alloc_interval", help="Allocation interval in seconds", type=int, default=15)
    parser.add_argument("--max_iter", help="Max iterations", type=int, default=150)
    return parser.parse_args()

def run_evaluation(args, allocation_interval_in_sec: int=10, move_logs=True):
    # max_iteration = 100 for 300 iteraions; 50 for 150 iterations

    # = Prepare system components =
    # Prepare estimator and allocator
    print("Starting estimator...", flush=True)
    config = Config().load_config(config_path=args.config)
    if config.allocation_parameters is not None and "allocation_interval_in_sec" in config.allocation_parameters:
        allocation_interval_in_sec = config.allocation_parameters["allocation_interval_in_sec"]
        print(f"Config::Allocation interval is set to {allocation_interval_in_sec} sec.")

    max_iteration = args.max_iter
    # configuration params based on parsed configs
    base_alloc_int = int(10)
    # 100 iterations at 10 sec interval
    # = 200 iterations at 5 sec interval
    # = 16 iterations at 60 sec interval
    max_iteration = max_iteration * base_alloc_int // allocation_interval_in_sec

    # Scan range increase is based on wall clock time -> longer interval, larger delta
    search_range_delta = 0. # No dynamic search range
    print(f"Config: base_alloc_int: {base_alloc_int}, alloc_int: {allocation_interval_in_sec},\
          max_iteration: {max_iteration}, search_range_delta: {search_range_delta}")
    print(f"Config::Num applications: {len(config.benchmark_map)}")
    # NOTE) reseource scale should be matched to the scale in the profile data (*.joblib)
    resource_scale = {"cache": float(config.cache_in_mb), "min_cache": float(config.min_cache_in_mb),
                      "max_cache": float(config.max_cache_in_mb),
                      # so mem bw in "gbps" in this case
                      "mem_bw": float(config.mem_bw_in_mbps) / 1024., "min_mem_bw": float(config.min_mem_bw_in_mbps) / 1024.,
                      "max_mem_bw": float(config.max_mem_bw_in_mbps) / 1024.}
    # Monitor (collecting resource usage from the controller)
    monitor = MemcachedMindMonitor(config=config)
    # Deployer (sending allocation to the controller)
    deployer = MemcachedDeployer(config=config)
    # Estimator
    estimator = RuntimeEstimator(resource_scale=resource_scale)

    # Allocator
    if args.allocator == "spirit":
        from allocators.spirit_allocator import SpiritAllocator
        allocator = SpiritAllocator(args.config, estimator=estimator, monitor=monitor, deployer=deployer, resource_scale=resource_scale)
    elif args.allocator == "static":
        from allocators.static_allocator import StaticAllocator
        allocator = StaticAllocator(args.config, estimator=estimator, monitor=monitor, deployer=deployer, resource_scale=resource_scale)
    elif args.allocator == "oracle":
        from allocators.oracle_allocator import OracleAllocator
        allocator = OracleAllocator(args.config, estimator=estimator, monitor=monitor, deployer=deployer, resource_scale=resource_scale)
    elif args.allocator == "inc-trade":
        from allocators.inc_trade_allocator import IncrementalTradeAllocator
        allocator = IncrementalTradeAllocator(args.config, estimator=estimator, monitor=monitor, deployer=deployer, resource_scale=resource_scale)
    elif args.allocator == "fij-trade":
        from allocators.fij_trade_allocator import FijTradeAllocator
        allocator = FijTradeAllocator(args.config, estimator=estimator, monitor=monitor, deployer=deployer, resource_scale=resource_scale)
    else:
        raise Exception(f"Unknown allocator type: {args.allocator}")

    # = Initialization/Parameters =
    init_phase_interval = 3     # default = 3
    estimator.set_allocator(allocator)
    if hasattr(estimator, "set_monitor"):
        estimator.set_monitor(monitor)
    if args.allocator == "spirit":
        from allocators.spirit_allocator import SpiritAllocatorParams
        allocator.initialize(SpiritAllocatorParams(allocation_interval_in_sec=allocation_interval_in_sec, search_range_delta=search_range_delta, init_phase_interval=init_phase_interval))
    elif args.allocator == "static":
        from allocators.static_allocator import StaticAllocatorParams
        allocator.initialize(StaticAllocatorParams(allocation_interval_in_sec=allocation_interval_in_sec, init_phase_interval=init_phase_interval))
    elif args.allocator == "oracle":
        from allocators.oracle_allocator import OracleAllocatorParams
        allocator.initialize(OracleAllocatorParams(allocation_interval_in_sec=allocation_interval_in_sec, init_phase_interval=init_phase_interval))
    elif args.allocator == "inc-trade":
        from allocators.inc_trade_allocator import IncrementalTradeAllocatorParams
        # 3 was for 30 sec interval, so we will use 9 for 10 sec interval
        allocator.initialize(IncrementalTradeAllocatorParams(allocation_interval_in_sec=allocation_interval_in_sec, init_phase_interval=init_phase_interval * 3))
    elif args.allocator == "fij-trade":
        from allocators.fij_trade_allocator import FijTradeAllocatorParams
        allocator.initialize(FijTradeAllocatorParams(allocation_interval_in_sec=allocation_interval_in_sec, init_phase_interval=init_phase_interval))

    # = Start =
    # Start the metrics reset API server
    metrics_server = MetricsResetServer(monitor)
    metrics_server.start()
    allocator.start(max_iteration=max_iteration)

    if not move_logs:
        metrics_server.stop()
        return

    # deallocate monitor, deployer, estimator, allocator
    allocator.cleanup()
    estimator.cleanup()
    deployer.cleanup()
    monitor.cleanup()
    metrics_server.stop()


if __name__ == "__main__":
    # parse arguments
    args = parse_args()
    run_evaluation(args)
