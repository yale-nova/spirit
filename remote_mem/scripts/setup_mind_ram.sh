#!/bin/bash
# Setup script for mind_ram RDMA driver
#
# Usage:
#   Default: ./setup_mind_ram.sh
#   Custom RDMA device: RDMA_DEVICE=mlx5_3 ./setup_mind_ram.sh
#
# dependency for rdma connection manager
sudo modprobe rdma_cm

# Automatically determine the server IP
candidate=$(ifconfig | grep -Eo 'inet (10\.10\.10\.[0-9]+)' | awk '{print $2}' | grep -E '10\.10\.10\.(20[1-9]|21[0-6])' | head -n 1)
if [ -z "$candidate" ]; then
    echo "No suitable IP address found with last octet between 201 and 216."
    exit 1
fi

last_digit=$(echo $candidate | cut -d. -f4)
server_digit=$((last_digit + 20))
server_ip="10.10.10.${server_digit}"
echo "Using server IP: ${server_ip}"
mem_size=49152 # 48 GB in MB

# RDMA device selection - can be overridden via environment variable
rdma_device="${RDMA_DEVICE:-mlx5_0}"  # Default to mlx5_0
echo "Using RDMA device: ${rdma_device}"

# check if the module is compiled
if [ ! -f ../drivers/mind_ram/mind_ram_rdma.ko ]; then
    echo "The module is not compiled yet. Please compile the module first."
    exit 1
fi

# the memory size here set to 12 GB (where defeault size at the remote size is 16 GB)
sudo insmod ../drivers/mind_ram/mind_ram_rdma.ko capacity_mb="${mem_size}" server_ip="${server_ip}" rdma_device_name="${rdma_device}" &&\

# add time for the daemon to map the queue
echo "Wait for the daemon to map the queue" &&\
sleep 5 &&\
sudo mkswap /dev/mind_ram0 &&\
sudo swapon --priority 100 /dev/mind_ram0

# Swap setup
echo 3 | sudo tee /proc/sys/vm/page-cluster
echo 50 | sudo tee /proc/sys/vm/dirty_ratio
echo 60 | sudo tee /proc/sys/vm/swappiness

# Allow overcommit
echo 1 | sudo tee /proc/sys/vm/overcommit_memory

# Disable perf watchod
echo 99 | sudo tee /proc/sys/kernel/perf_cpu_time_max_percent

echo "none" | sudo tee /sys/block/mind_ram0/queue/scheduler
