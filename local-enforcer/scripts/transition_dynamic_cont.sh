#!/bin/bash

# Check if an ID was provided
if [ -z "$1" ]; then
  echo "Please provide a docker name as the first argument."
  exit 1
fi

# Use the provided ID to create a unique container name
container_name="$1"
echo "Container name: $container_name"

# Check if the target container exists
if [ "$(docker ps --all -q -f name=$container_name)" ]; then
  echo "Container $container_name already exists. Sending a signal..."
  # temporarily assign 16 cores to the container
  docker update --cpus="16" "$container_name"
  docker exec "$container_name" touch /tmp/start_signal
  sleep 3 # give time to terminate stream
  docker update --cpus="4" "$container_name"
  # signal location is defined at: ${workspace}/scripts/disagg/apps/dynamic_docker/run_commands.sh
  # docker kill --signal=SIGUSR1 "$container_name"
fi
