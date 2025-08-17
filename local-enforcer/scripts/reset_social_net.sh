#!/bin/bash

# Check if an ID was provided
if [ -z "$1" ]; then
  echo "Please provide a docker name as the first argument."
  exit 1
fi

# Use the provided ID to create a unique container name
container_name="$1"
# Add _client to the container name
container_name="${container_name}_client"
echo "Container name: $container_name"

# For social network workload, the system should be already running with docker-compose

# Check SPIRIT_PATH exist in env
if [ -z "$SPIRIT_PATH" ]; then
  echo "SPIRIT_PATH is not set. We will use the default path."
  SPIRIT_PATH="/opt/spirit/spirit-controller/"
fi

# Check if there is a directly containing Makefile
socialnet_script_path=$SPIRIT_PATH"/scripts/disagg/apps/socialnet"
if [ ! -f "$socialnet_script_path/Makefile" ]; then
  echo "Makefile not found in $socialnet_script_path. Please check the SPIRIT_PATH."
  exit 1
fi

# # Goes to the socialnet directory and start service
cd "$socialnet_script_path" && make restart_jaeger
sleep 10

# Start the client
cd "$socialnet_script_path" && make run CONTAINER_NAME="$container_name"