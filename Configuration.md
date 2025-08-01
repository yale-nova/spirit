# Running a KVS workload

Install all dependencies as described in the [README](README.md).

## Configuration files
Each experiment has 3 configuration files associated with it:
- global.json
- local.json
- lims.json _(optional)_

We provide sample configurations in the [sample_configs](sample_configs) folder. Let's look
at the `basic` configuration in detail:

### global.json
```json
{
  "vm_ip_map": {
    "1": "127.0.0.1" //<VM_ID: IP_ADDR> 
  },
  "placement_map": {
    "1": [1] // <VM_ID: APP_ID>
  },
  "id_preload_map": {
    "1": "" // <APP_ID: Command>
  },
  "run_backend_cmd_map": {
    "1": "../redis/src/redis-server --port 6379 --save '' --bind 127.0.0.1" // <APP_ID: Command>
  }
}
```
- `vm_ip_map` - specifies the IP addresses of all nodes/VMs running this experiment (here we are just running locally)
- `placement_map` - distributes local instaces across the nodes defined in `vm_ip_map` (here App1 will be on node 1)
- `id_preload_map` - shell command to prepare APP_ID's backend instance (can be blank)
- `run_backend_cmd_map` - shell command to start APP_ID's backend instance

### local.json
```json
{
  "id_preload_map": {
    "1": "../ycsb-0.17.0/preload_c.log" // <APP_ID: FILE>
  },
  "id_benchmark_map": {
    "1": "../ycsb-0.17.0/workload_c.log" // <APP_ID: FILE>
  },
  "backend_ip_map": {
   "1": "127.0.0.1:6379" // <APP_ID: IP:PORT>
  },
  "global_ip": "127.0.0.1:8000", // IP:PORT
  "val_size_bytes": 10000 // INT
}
```

- `id_preload_map` - mappings of APP_IDs to preload files
- `id_benchmark_map` - mappings of APP_IDs to workload files
- `backend_ip_map` - mapping of APP_IDs to their Redis backend instances
- `global_ip` - address of the global node's rocket instance
- `val_size_bytes` - size of objects stored in bytes

### lims.json
Optionally specifies the initial limits for the experiment.
```json
{
    "allocation_map": {"1": [512, 500000]} // APP_ID: [MEM_LIMIT_MB, BW_LIMIT_MBPS]
}
```
- `allocation_map` - cache and bandwidth limits for each APP_ID (units in MBs and Mbps)

## Running the workload
Once the configuration files are set up, run the following commands to run the experiment:
1. Run the global node \
`just global-run <PORT> <PATH_TO_GLOBAL.JSON>`
2. Run the local node
`just local-run <PORT> <PATH_TO_LOCAL.JSON> (<PATH_TO_LIMS.JSON>)`

### Example:
Assuming `redis,spirit` and `ycsb` are in the same directory, with preload and workload
files generated in the `ycsb` directory: \
1. `just global-run 8000 sample_configs/basic/global.json`
2. `just local-run 8888 sample_configs/basic/local.json sample_configs/basic/lims.json`

## Troubleshooting
**Experiments not launching** \
Make sure that the number of APPs, VMs and instances is compatible between the `local.json` and 
`global.json` files and the commands and paths in both files are valid.

**Apparently inacurate metrics** \
Verify the object size declared in `local.json`, it is used to calculate the final throughput and
memory usage. 