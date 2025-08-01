#!/bin/bash
log_path="logs/spirit_python.log"

# Check if the correct number of arguments are provided (1 or 2)
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: $0 <path to a resource allocation config file> [max_iter]"
    exit 1
fi

# Assign arguments to variables for better readability
res_alloc_config_path="$1"

# If a second argument is provided, check that it's a valid integer and set the max_iter parameter
max_iter_arg=""
if [ -n "$2" ]; then
    if ! [[ "$2" =~ ^[0-9]+$ ]]; then
        echo "Error: max_iter is not an integer: $2"
        exit 1
    fi
    max_iter_arg="--max_iter $2"
fi

# Check if the python environment exists
if [ ! -d "/opt/venv/bin" ]; then
    echo "Error: Python environment not found at /opt/venv/bin"
    exit 1
fi

# Activate the preconfigured python env
source /opt/venv/bin/activate

# Check if the program directory exists
if [ ! -d "/spirit-controller/res_allocation" ]; then
    echo "Error: Program directory not found at /spirit-controller/res_allocation"
    exit 1
fi

# Run the python program
cd /spirit-controller/res_allocation || exit 1

# Check if the config file exists and is readable
if [ ! -f "$res_alloc_config_path" ] || [ ! -r "$res_alloc_config_path" ]; then
    echo "Error: Config file does not exist or is not readable: $res_alloc_config_path"
    exit 1
fi

python3 main_memcached.py --config "${res_alloc_config_path}" --allocator spirit ${max_iter_arg} > "${log_path}" 2>&1
