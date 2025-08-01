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
        container_list = config.get('log_collect')

    # assert that client_list is not none and its length is greater than 0
    # assert client_list is not None and len(client_list) > 0
    if container_list is None or len(container_list) == 0:
        print("Container list is empty; skipping...")
        exit(0)

    # create a directory to store the logs at /tmp/logs/
    # if there existing logs, remove them
    log_dir = "/tmp/spirit_logs/"
    # merge the working directory with the log directory
    # log_dir = os.path.join(working_dir, "../../", log_dir)
    if os.path.exists(log_dir):
        print("Removing existing logs...")
        os.system("rm -rf " + log_dir)
    os.system("mkdir -p " + log_dir)

    # container list is actually a list of dictionary
    # [
    # {
    #   "id": 1,
    #   "script": "local-enforcer/scripts/run_stream_docker.sh",
    #   "docker_name": "spirit_stream_1",
    #   "launch": true
    # },
    # ...
    for container_name in container_list:
        log_cmd = None
        # check the container name include compress
        if "compress" in container_name:
            log_file = os.path.join(log_dir, container_name + ".log")
            log_cmd = f"docker cp {container_name}:/data/progress.log " + log_file
        else:
            # Use docker log command to get the logs
            log_file = os.path.join(log_dir, container_name + ".log")
            print("Writing log to:", log_file)
            # Docker log command
            log_cmd = "docker logs " + container_name + " > " + log_file
            print("Log command:", log_cmd)
            # Run the command in the shell
        if log_cmd is not None:
            # Run the command in the shell
            print("Running command:", log_cmd)
            os.system(log_cmd)
