#!/bin/bash
# Define a function to run when an interrupt signal is received
trap 'echo "Script interrupted"; continue=false; exit 1' INT

# Define the arrays
# == entire set ==
# cache_gb_values=("10" "20")
# bw_gbps_values=("7.5")

# == specific ==
cache_gb_values=("10")
bw_gbps_values=("7.5")

num_vm="4"

# Multiple VMs
config_file="../../sample_configs/global_6_apps_${num_vm}vm.json"
local_config_prefix="sample_configs/local_6_apps_bal_vm"
local_config_nomrc_prefix="sample_configs/local_6_apps_bal_no_mrc_vm"
res_config_prefix="configs/6apps_${num_vm}vm/config"

echo "Global enforcer config: ${config_file}"

# Loop over the bw_gbps values
for bw_gbps in "${bw_gbps_values[@]}"; do
    # Loop over the cache_gb values
    for cache_gb in "${cache_gb_values[@]}"; do
        # - default res config (except oracle)
        res_config_file="${res_config_prefix}_${cache_gb}g_${bw_gbps}gbps_30sec_bal.json"
        echo "Resource config: ${res_config_file}"
        res_config_file_picked="${res_config_prefix}_${cache_gb}g_${bw_gbps}gbps_30sec_bal_oracle.json"
        echo "Resource picked config: ${res_config_file_picked}"
        # -----------------------
        # - static as a warm-up / check
        if ! just run_ansible_docker_static "${config_file}" "${res_config_file}" "${local_config_nomrc_prefix}"; then
            kill -INT $$
        fi
        sleep 30
        # -----------------------
        # - spirit allocation
        if ! just run_ansible_docker_global "${config_file}" "${res_config_file}" "${local_config_prefix}"; then
            kill -INT $$
        fi
        sleep 30
        # -----------------------
        # - static allocation
        if ! just run_ansible_docker_static "${config_file}" "${res_config_file}" "${local_config_nomrc_prefix}"; then
            kill -INT $$
        fi
        sleep 30
        # -----------------------
        # - oracle allocation
        echo "Resource config: ${res_config_file_picked}"
        #
        if ! just run_ansible_docker_oracle "${config_file}" "${res_config_file_picked}" "${local_config_nomrc_prefix}"; then
            kill -INT $$
        fi
        sleep 30
        # -----------------------
        # - fij-trade allocation
        echo "Resource config: ${res_config_file}"
        if ! just run_ansible_docker_fij_trade "${config_file}" "${res_config_file}" "${local_config_prefix}"; then
            kill -INT $$
        fi
        sleep 30
        # -----------------------
        # - inc-trade allocation
        res_config_inc_file="${res_config_prefix}_${cache_gb}g_${bw_gbps}gbps_30sec_bal_inc.json"
        echo "Resource config: ${res_config_inc_file}"
        if ! just run_ansible_docker_inc_trade "${config_file}" "${res_config_inc_file}" "${local_config_nomrc_prefix}"; then
            kill -INT $$
        fi
        sleep 30
        # -----------------------
    done
done

exit