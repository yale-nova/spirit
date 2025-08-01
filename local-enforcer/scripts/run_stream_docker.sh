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

# Run the container and execute the command in one step
docker run --cpus=4 --memory=2g --memory-swap=40g --cgroup-parent="$default_cgroup" --name $container_name --restart on-failure -d stream-docker ./stream 12
