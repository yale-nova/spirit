#!/bin/bash

# Define a signal handler for the external signal
wait_for_signal() {
    echo "Waiting for an external signal to proceed..."
    while :; do
        if [ -f /tmp/start_signal ]; then
            echo "Signal received! Toggling application..."
            rm -f /tmp/start_signal
            break
        fi
        sleep 1
    done
}

# Preserve the original arguments properly
# Handle all possible cases for memcached arguments
if [ "${1#-}" != "$1" ]; then
    # First arg starts with dash, prepend memcached
    set -- memcached "$@"
elif [ "$1" = "memcached" ]; then
    # First arg is already memcached, keep as is
    :
else
    # First arg is something else, prepend memcached
    set -- memcached "$@"
fi

# Store the memcached command and arguments for later use
MEMCACHED_CMD="$1"
shift
MEMCACHED_ARGS=("$@")

# Log the memcached command for debugging
echo "Memcached command will be: $MEMCACHED_CMD ${MEMCACHED_ARGS[*]}"

# Variables to track state and PIDs
CURRENT_APP="none"
APP_PID=""

# Main loop to alternate between applications
while true; do
    if [ "$CURRENT_APP" = "none" ] || [ "$CURRENT_APP" = "memcached" ]; then
        # Switch to stream
        echo "Starting stream benchmark..."
        
        # Kill memcached if it's running
        if [ "$CURRENT_APP" = "memcached" ] && [ -n "$APP_PID" ]; then
            echo "Stopping memcached (PID: $APP_PID)..."
            kill -9 $APP_PID || true
            wait $APP_PID 2>/dev/null || true
        fi
        
        # Start stream
        cd "/usr/local/bin"
        echo "Current directory: $(pwd)"
        echo "Available files: $(ls -al)"
        
        /usr/local/bin/stream 12 &
        APP_PID=$!
        CURRENT_APP="stream"
        echo "STREAM started with PID: $APP_PID"
        
    else
        # Switch to memcached
        echo "Starting memcached server..."
        
        # Kill stream if it's running
        if [ "$CURRENT_APP" = "stream" ] && [ -n "$APP_PID" ]; then
            echo "Stopping stream benchmark (PID: $APP_PID)..."
            pkill -P $APP_PID || true
            kill -9 $APP_PID || true
            wait $APP_PID 2>/dev/null || true
            ps aux | grep stream
            echo "Stream benchmark stopped"
        fi
        
        # Start memcached with proper argument handling
        cd "/usr/bin"
        "$MEMCACHED_CMD" "${MEMCACHED_ARGS[@]}" &
        APP_PID=$!
        CURRENT_APP="memcached"
        echo "Memcached started with PID: $APP_PID"
    fi
    
    # Wait for signal to toggle applications
    wait_for_signal
done