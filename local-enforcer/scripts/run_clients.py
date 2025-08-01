import sys
import os
import json
import subprocess
import time

# PER_APP_CONFIG_FILE = "local_meta.json"

if __name__ == '__main__':
    # print current working directory
    working_dir = os.getcwd()
    print("Current working directory:", working_dir)

    # print the first argument passed to the script
    if len(sys.argv) > 1:
        print("Application argument:", sys.argv[1])
    else:
        print("No arguments passed.")
        exit(-1)

    # try to read the configuration file
    # note) here ../../ is to the root of the repository
    config_path = os.path.join(working_dir, "../../", sys.argv[1])
    config_file = os.path.abspath(config_path)
    if not os.path.exists(config_file):
        print("Configuration file not found.")
        exit(-1)

    # look up 'config_path': app_id as str -> config_subpath as str
    with open(config_file, 'r') as f:
        config = json.load(f)
        client_list = config.get('config_path')

    # assert that client_list is not none and its length is greater than 0
    # assert client_list is not None and len(client_list) > 0
    if client_list is None or len(client_list) == 0:
        print("Client list is empty; skipping...")
        exit(0)

    # print the configuration for each app_id
    for app_id, config_subpath in client_list.items():
        print("Set working directory to:", working_dir)
        os.chdir(working_dir)
        print("App ID:", app_id)
        print("Config subpath:", config_subpath)

        # First, try to stop and remove any existing container with the same name
        container_name = f"spirit_mc_client_{app_id}"
        cleanup_cmd = f"docker rm -f {container_name} 2>/dev/null || true"
        cleanup_result = subprocess.run(cleanup_cmd, shell=True, check=False)
        if cleanup_result.returncode != 0 and cleanup_result.returncode != 1:  # 1 is ok as it means container didn't exist
            print(f"Warning: Container cleanup failed with return code {cleanup_result.returncode}")

        # Assembling a docker command
        docker_cmd = "docker run -d --rm -v $(pwd)/target/release/bench-mc-client:/bench-mc-client:ro -v /workload:/workload:ro -v $(pwd)/benchmarks/ycsb:/ycsb:ro -v $(pwd)/" + config_subpath + ":/configs:ro --network host --name " + container_name + " bench-mc-client-docker"
        print("Docker command:", docker_cmd)

        # Change directory to the root (../../)
        os.chdir(os.path.join(working_dir, "../../"))
        pwd = os.getcwd()
        print("Moved to the root:", pwd)

        # Run the Docker command and check its completion
        docker_result = subprocess.run(docker_cmd, shell=True, check=False)
        if docker_result.returncode != 0:
            print(f"Error: Docker command failed with return code {docker_result.returncode}")
            sys.exit(1)  # Exit if Docker command fails

        # sleep
        time.sleep(3)
