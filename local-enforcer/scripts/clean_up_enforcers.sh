#!/bin/bash

terminate_processes() {
    # Check if we got any process IDs
    if [ -z "$1" ]; then
        echo "No processes with 'run_with_timeout.sh' found."
    else
        # Terminate the processes using 'kill -9'
        for pid in $1; do
            echo "Terminating process with PID $pid."
            kill -9 $pid
        done
    fi
}

# Find processes with 'run_with_timeout.sh' in their command line
process_ids=$(pgrep -f run_with_timeout.sh)
terminate_processes "$process_ids"

process_ids=$(pgrep -f target/release/local-enforcer)
terminate_processes "$process_ids"
