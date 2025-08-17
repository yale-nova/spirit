#!/bin/bash

# Check if the correct number of arguments are provided
if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <metric_server_port> <cache_limit_mb> <bw_limit_mbps>"
    exit 1
fi

# Assign arguments to variables for better readability
metric_server_port="$1"
cache_limit_mb="$2"
bw_limit_mbps="$3"

# Create logs directory if it doesn't exist
mkdir -p /spirit-controller/logs

# Check if the directory was created successfully
if [ ! -d "/spirit-controller/logs" ]; then
    echo "Failed to create logs directory"
    exit 1
fi

# Check if the curl command exists
if ! command -v curl &> /dev/null; then
    echo "curl command not found"
    exit 1
fi

# Get the current date and time in the format YYYYMMDD_HHMMSS
timestamp=$(date "+%Y%m%d_%H%M%S")

# Define the log file name with the timestamp at the end
log_file="/spirit-controller/logs/cdf_c_${cache_limit_mb}_bw_${bw_limit_mbps}_${timestamp}.log"

# Continuously get the status and write it to the log file
curl -X GET "http:///10.10.10.201:${metric_server_port}/metric" >> "${log_file}" || true
