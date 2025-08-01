#!/bin/bash

if [ -z "$1" ]; then
  echo "Usage: $0 <bandwidth_in_Mbps>"
  exit 1
fi

BANDWIDTH_MBPS="$1"

# Device name
DEVICE_NAME="mind_ram0"

# Find the device path
DEVICE_PATH=$(lsblk -lno NAME,PATH | awk -v dev="$DEVICE_NAME" '$1 == dev {print $2}')

# Check if the device exists
if [ -z "$DEVICE_PATH" ]; then
  echo "Device $DEVICE_NAME not found."
  exit 1
fi

# Get MAJ:MIN numbers
MAJMIN=$(lsblk -no MAJ:MIN "$DEVICE_PATH")

# Check if MAJ:MIN was retrieved
if [ -z "$MAJMIN" ]; then
  echo "Failed to get MAJ:MIN for $DEVICE_PATH"
  exit 1
fi

# Calculate bandwidth in bytes per second for 10 Gbps
# BANDWIDTH_BPS=$(($BANDWIDTH_MBPS * 1024 * 1024 * 1024 / 8))
BANDWIDTH_BPS=$(echo "scale=0; $BANDWIDTH_MBPS * 1024 * 1024 / 8" | bc)

# Check if bc command succeeded
if [ -z "$BANDWIDTH_BPS" ]; then
  echo "Error calculating BANDWIDTH_BPS."
  exit 1
fi

# Apply the I/O bandwidth limits
echo "$MAJMIN rbps=$BANDWIDTH_BPS wbps=$BANDWIDTH_BPS" | sudo tee /sys/fs/cgroup/user.slice/io.max
echo "$MAJMIN rbps=$BANDWIDTH_BPS wbps=$BANDWIDTH_BPS" | sudo tee /sys/fs/cgroup/system.slice/io.max
echo "$MAJMIN rbps=$BANDWIDTH_BPS wbps=$BANDWIDTH_BPS" | sudo tee /sys/fs/cgroup/spirit.slice/io.max || true

