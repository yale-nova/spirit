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
if [ "$(docker ps --all -q -f name='$container_name')" ]; then
  echo "Container $container_name already exists. Removing it..."
  docker kill "$container_name"
  docker rm -f "$container_name"
fi

memcached_img="memcached@sha256:768b8b14b264b87cdde0d4bc0e800c332b8563ce19fd15ce98945c4441b98146"

# Run the container and execute the command in one step
docker run --memory=4g --memory-swap=40g --cgroup-parent="$default_cgroup" --name "$container_name" --network host --restart on-failure -d --ulimit nofile=65536:65536 "$memcached_img" memcached -t 4 -c 2048 -n 64 -m 32768 -p "$port_num" -o slab_reassign,slab_automove=2,hashpower=22,hash_algorithm=murmur3
