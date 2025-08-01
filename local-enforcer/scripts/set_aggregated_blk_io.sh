#!/bin/bash
# example usage with a 20 Gbps limit: sudo ./set_aggregated_blk_io.sh 20000
if [ -z "$1" ]; then
  echo "Please provide block device bandwidth in Mbps and integer as the first argument."
  exit 1
fi

# Define the block device name (e.g., sda, nvme0n1)
MEMORY_DEV_NAME="/dev/mind_ram0"

# Define the aggregated I/O bandwidth limit in Mbps
AGGREGATED_BLOCK_LIMIT_Mbps="$1"

# Convert the aggregated limit to bytes per second (assuming the limit is in Mbps)
BLOCK_LIMIT_BYTES_PER_SEC=$((AGGREGATED_BLOCK_LIMIT_Mbps * 125000))

# Get the major and minor device number for the block device
MAJ_MIN=$(lsblk -dno MAJ:MIN "${MEMORY_DEV_NAME}")

# Path to the io.max file for the system.slice
CGROUP_SYSTEM_SLICE_PATH="/sys/fs/cgroup/system.slice/io.max"

# Construct the limit string
LIMIT_STR="${MAJ_MIN} rbps=${BLOCK_LIMIT_BYTES_PER_SEC} wbps=${BLOCK_LIMIT_BYTES_PER_SEC}"

# Apply the limit to the block device at the system.slice level
echo "${LIMIT_STR}" > "${CGROUP_SYSTEM_SLICE_PATH}" || {
    echo "Failed to set the aggregated block device bandwidth limit"
    exit 1
}

echo "Aggregated block device bandwidth limit set to ${AGGREGATED_BLOCK_LIMIT_Mbps} Mbps for all containers under system.slice"
