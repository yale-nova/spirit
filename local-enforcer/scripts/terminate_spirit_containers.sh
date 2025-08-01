#!/bin/bash
# Terminate all spirit containers; since we does not check the status of the containers,
# `docker kill` can fail if the docker has been stopped before
docker ps -a --format '{{ .Names }}' | grep '^spirit_' | xargs -r -I {} sh -c 'docker kill {} || true'
docker ps -a --format '{{ .Names }}' | grep '^spirit_' | xargs -r -I {} sh -c 'docker stop {} || true'
docker ps -a --format '{{ .Names }}' | grep '^spirit_' | xargs -r -I {} sh -c 'docker rm {}'

# Addional application specific cleanup
## Social Network
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

# # Goes to the socialnet directory and shutdown the service
# cd "$socialnet_script_path" && make shutdown_service_flush || true
cd "$socialnet_script_path" && make restart_jaeger || true

# Clear all the caches
sudo sync; echo 3 | sudo tee /proc/sys/vm/drop_caches
