#!/bin/bash
# Define a function to run when an interrupt signal is received
trap 'echo "Script interrupted"; continue=false; exit 1' INT

# == specific ==
cache_gb_values=("5")
bw_gbps_values=("5")

# EXAMPLE TARGET CMD::
# just run_ansible_docker_global ../../sample_configs/global_2_apps.json sample_configs/local_2_apps.json configs/config_mindv2_1g.json

# Define the config file
config_file="../../sample_configs/global_2_apps.json"
local_config_prefix="sample_configs/local_2_apps_vm"
local_config_nomrc_prefix="sample_configs/local_2_apps_no_mrc_vm"
res_config_prefix="configs/2apps/config"

echo "Global enforcer config: ${config_file}"

# Loop over the bw_gbps values
for bw_gbps in "${bw_gbps_values[@]}"; do
    # Loop over the cache_gb values
    for cache_gb in "${cache_gb_values[@]}"; do
        # - default res config (except oracle)
        res_config_file="${res_config_prefix}_${cache_gb}g_${bw_gbps}gbps_30sec.json"
        # - Debugging with 20 Gbps, bw only
        echo "Resource config: ${res_config_file}"
        res_config_file_picked="${res_config_prefix}_${cache_gb}g_${bw_gbps}gbps_30sec_oracle.json"
        # res_config_file_picked="${res_config_prefix}_${cache_gb}g_${bw_gbps}gbps_30sec_unbal_oracle.json"
        echo "Resource picked config: ${res_config_file_picked}"
        # -----------------------
        # - static allocation
        if ! just run_ansible_docker_static "${config_file}" "${res_config_file}" "${local_config_nomrc_prefix}"; then
            kill -INT $$
        fi
        sleep 30
        # -----------------------
    done
done

exit