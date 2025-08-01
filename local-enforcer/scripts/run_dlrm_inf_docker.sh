#!/bin/bash

# Check if an ID was provided
if [ -z "$1" ]; then
  echo "Please provide a docker name as the first argument."
  exit 1
fi

# Use the provided ID to create a unique container name
container_name="$1"
echo "Container name: $container_name"

default_cgroup="spirit.slice"

# Check and remove the container if it already exists
if [ "$(docker ps --all -q -f name=$container_name)" ]; then
  echo "Container $container_name already exists. Removing it..."
  docker kill $container_name
  docker rm -f $container_name
fi

port_num="8001"
# check $2 and override port_num if provided (use -n)
if [ -n "$2" ]; then
  port_num="$2"
fi
echo "Port number: $port_num"


# Run the container and execute the command in one step
docker run --cpus=4 --memory=3g --memory-swap=48g --cgroup-parent="$default_cgroup" -v /mnt/spirit_data/dlrm_bench:/root/dlrm_bench -p "$port_num":8001 --name $container_name --restart on-failure -d dlrm_cpu ./run_inf.sh
