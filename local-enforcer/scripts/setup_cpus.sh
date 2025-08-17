#!/bin/bash
# set -euo pipefail

# Starting CPU index for first NUMA node
FIRST_NUMA_CPU=4
NUMA_END_CPU=36
# if docker name include spirit_stream, it should be assigned to the second numa node
# 1) figure out the start cpu index for the second numa node
SPIRIT_START_CPU=$(lscpu | grep "NUMA node1 CPU(s):" | awk '{print $4}' | cut -d'-' -f1)
if ! [[ "$SPIRIT_START_CPU" =~ ^[0-9]+$ ]]; then
    echo "Failed to get the starting CPU index for the second NUMA node"
    # Set to the hardcoded default value
    SPIRIT_START_CPU=24
fi

# Initialize pointer for second NUMA node
SECOND_NUMA_CPU=$SPIRIT_START_CPU

# Number of CPUs to assign per container
DEFAULT_CPUS_PER_CONTAINER=4

# Helper function to update container CPU assignment
assign_container() {
    local container_id="$1"
    local container_name="$2"
    local cpu_range="$3"
    local msg="$4"
    docker update --cpuset-cpus="$cpu_range" "$container_id"
    echo "$msg container $container_name ($container_id) to CPUs $cpu_range"
}

# Get the list of running spirit container IDs
CONTAINER_IDS=$(docker ps --format '{{.Names}}' | grep '^spirit_' | sort | xargs -I {} docker ps -q -f name={})

# Check if any spirit containers are running
if [ -z "$CONTAINER_IDS" ]; then
    echo "No spirit containers found running"
    exit 0
fi
echo "Found spirit containers: $CONTAINER_IDS"

for CONTAINER_ID in $CONTAINER_IDS; do
    CONTAINER_NAME=$(docker ps --format '{{.Names}}' -f "id=$CONTAINER_ID")

    if [[ "$CONTAINER_NAME" == *spirit_social_net* || "$CONTAINER_NAME" == *spirit_dlrm_inf_* ]]; then
        # Allocate spirit_socialnet and spirit_stream_ containers from second NUMA node
        CPUS_PER_CONTAINER=$DEFAULT_CPUS_PER_CONTAINER
        END_CPU=$((SECOND_NUMA_CPU + CPUS_PER_CONTAINER - 1))
        if [ "$END_CPU" -gt "$NUMA_END_CPU" ]; then
            END_CPU=$((FIRST_NUMA_CPU + CPUS_PER_CONTAINER - 1))
            CPU_RANGE="${FIRST_NUMA_CPU}-${END_CPU}"
            assign_container "$CONTAINER_ID" "$CONTAINER_NAME" "$CPU_RANGE" "Allocated container fallback to first NUMA for"
            FIRST_NUMA_CPU=$((END_CPU + 1))
        else
            CPU_RANGE="${SECOND_NUMA_CPU}-${END_CPU}"
            assign_container "$CONTAINER_ID" "$CONTAINER_NAME" "$CPU_RANGE" "Assigned container from second NUMA for"
            SECOND_NUMA_CPU=$((END_CPU + 1))
        fi
    else
        # Set default CPU count first
        CPUS_PER_CONTAINER=$DEFAULT_CPUS_PER_CONTAINER

        # Allocate all other containers from first NUMA node
        END_CPU=$((FIRST_NUMA_CPU + CPUS_PER_CONTAINER - 1))
        CPU_RANGE="${FIRST_NUMA_CPU}-${END_CPU}"
        assign_container "$CONTAINER_ID" "$CONTAINER_NAME" "$CPU_RANGE" "Assigned container"
        FIRST_NUMA_CPU=$((END_CPU + 1))
    fi
done
