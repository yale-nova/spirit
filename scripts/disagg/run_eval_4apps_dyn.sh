#!/bin/bash
# Define a function to run when an interrupt signal is received
trap 'echo "Script interrupted"; continue=false; exit 1' INT

# Define the arrays
# == specific ==
cache_gb_values=("10")
bw_gbps_values=("7.5")

num_vm="1"

# Multiple VMs
config_file="../../sample_configs/global_4_apps.json"
local_config_prefix="sample_configs/local_4_apps_dynamic_vm"
res_config_prefix="configs/4apps_dyn/config"

echo "Global enforcer config: ${config_file}"
echo "Local enforcer config: ${app_config_file}"

# Loop over the bw_gbps values
for bw_gbps in "${bw_gbps_values[@]}"; do
    # Loop over the cache_gb values
    for cache_gb in "${cache_gb_values[@]}"; do
        # - default res config (except oracle)
        res_config_file="${res_config_prefix}_${cache_gb}g_${bw_gbps}gbps_30sec_bal.json"
        echo "Resource config: ${res_config_file}"
        echo "Resource picked config: ${res_config_file_picked}"
        # -----------------------
        # - spirit allocation
        if ! just run_ansible_docker_dynamic "${config_file}" "${res_config_file}" "${local_config_prefix}"; then
            kill -INT $$
        fi
        sleep 30
    done
done

exit