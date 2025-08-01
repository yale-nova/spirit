#!/bin/bash
# Define a function to run when an interrupt signal is received
trap 'echo "Script interrupted"; continue=false; exit 1' INT

# Check hosts file to ensure we have only one server
HOSTS_FILE="$(dirname "$0")/ansible/hosts"
SERVER_COUNT=$(grep -c "^[0-9]" "$HOSTS_FILE")
if [ "$SERVER_COUNT" -gt 1 ]; then
    echo "Error: More than one server found in hosts file. This script is designed to run with one server only."
    echo "Please update the hosts file or use a different script for multi-server setup."
    exit 1
fi
echo "Only one server found in hosts file. Proceeding with the script."

# The amount of cache and bandwidth to use

# == entire set ==
# cache_gb_values=("2.5" "5" "7.5" "10")
# bw_gbps_values=("1.9" "3.8" "5.6" "7.5")

# == Specific value ==
cache_gb_values=("2.5")
bw_gbps_values=("5.6")

num_vm="1"  # ansible's `hosts` must be updated to 1 VM

# Use a single VM setup and per-application mapping
# - Each application will have its own id
config_file="../../sample_configs/global_6_apps_${num_vm}vm.json"

# == Per app local config (all no mrc setup) ==
# local_config_prefix="sample_configs/local_social_net_vm"
# local_config_prefix="sample_configs/local_mc_kvs_vm"
local_config_prefix="sample_configs/local_stream_vm"
# local_config_prefix="sample_configs/local_dlrm_inf_vm"
#

# Resource allocation config located in resource allocation submodule
res_config_prefix="configs/1app/config"

echo "Global enforcer config: ${config_file}"
echo "Local enforcer config: ${app_config_file}"

# Loop over the bw_gbps values
for bw_gbps in "${bw_gbps_values[@]}"; do
    # Loop over the cache_gb values
    for cache_gb in "${cache_gb_values[@]}"; do
        # - default res config (except oracle)
        res_config_file="${res_config_prefix}_${cache_gb}g_${bw_gbps}gbps_30sec_bal.json"
        echo "Resource config: ${res_config_file}"
        # - static allocation
        if ! just run_ansible_docker_static "${config_file}" "${res_config_file}" "${local_config_prefix}"; then
            kill -INT $$
        fi
        sleep 30
    done
done

exit