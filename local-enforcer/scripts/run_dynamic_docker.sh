#!/bin/bash

# Check if an ID was provided
if [ -z "$1" ]; then
  echo "Please provide a docker name as the first argument."
  exit 1
fi

# Use the provided ID to create a unique container name
container_name="$1"
echo "Container name: $container_name"
# PORT: for KVS, we use 61002; for other workloads, choose another port like 61001, 61003, etc.
port_num="61002"
# check $2 and override port_num if provided (use -n)
if [ -n "$2" ]; then
  port_num="$2"
fi
echo "Port number: $port_num"

default_cgroup="spirit.slice"

# Check and remove the container if it already exists
if [ "$(docker ps --all -q -f name=$container_name)" ]; then
  echo "Container $container_name already exists. Removing it..."
  docker kill $container_name
  docker rm -f $container_name
fi

# Run the container and execute the command in one step
docker run --cpus="4" --memory=4g --memory-swap=32g --cgroup-parent="$default_cgroup" --name "$container_name" --network host --restart on-failure -d dynamic-docker memcached -m 25600 -p "$port_num" -u root
