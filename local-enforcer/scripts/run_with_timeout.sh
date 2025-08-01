#!/bin/bash
timeout_duration=$1
shift
command_to_run=$@

# Get the current date and time
datetime=$(date '+%Y%m%d%H%M%S')

# Get the first 10 characters of the command
command_prefix=${command_to_run:0:10}

# Replace spaces with underscores in the command prefix
command_prefix=${command_prefix// /_}

# Create the log file name
logfile="${command_prefix}_${datetime}.log"

# Run the command in the background and redirect output to the log file
$command_to_run > $logfile 2>&1 &
pid=$!

# Wait for the specified timeout duration
sleep $timeout_duration

# Kill the process if it is still running
kill $pid 2>/dev/null || true
