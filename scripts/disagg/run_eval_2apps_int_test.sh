#!/bin/bash
# Define a function to run when an interrupt signal is received
trap 'echo "Script interrupted"; continue=false; exit 1' INT

# == specific ==
cache_gb_values=("10")
bw_gbps_values=("5")

# == entire set ==
alloc_interval=("10" "20" "30" "40" "50" "60" "90" "120")

# Define the config file
config_file="../../sample_configs/global_2_apps.json"
local_config_prefix="sample_configs/local_2_apps_vm"
res_config_prefix="configs/2apps/config"

echo "Global enforcer config: ${config_file}"

# Loop over the bw_gbps values
for bw_gbps in "${bw_gbps_values[@]}"; do
    # Loop over the cache_gb values
    for cache_gb in "${cache_gb_values[@]}"; do
        for alloc_int in "${alloc_interval[@]}"; do
            res_config_file="${res_config_prefix}_${cache_gb}g_${bw_gbps}gbps_${alloc_int}sec.json"
            if ! just run_ansible_docker_global "${config_file}" "${res_config_file}" "${local_config_prefix}"; then
                kill -INT $$
            fi
            sleep 30
        done
    done
done

exit