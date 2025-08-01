import subprocess
import re

def parse_memory_usage(mem_usage_str):
    """Convert memory usage string like '2.676MiB' or '2.894GiB' to MiB"""
    mem_value, mem_unit = re.match(r"([0-9\.]+)([A-Za-z]+)", mem_usage_str).groups()
    mem_value = float(mem_value)

    # Convert memory to MiB
    if mem_unit == "GiB":
        return mem_value * 1024  # 1 GiB = 1024 MiB
    elif mem_unit == "MiB":
        return mem_value
    elif mem_unit == "KiB":
        return mem_value / 1024
    else:
        raise ValueError(f"Unexpected memory unit: {mem_unit}")

def get_total_memory_usage():
    # Run the docker stats command with --no-stream option to get a one-time output
    result = subprocess.run(['docker', 'stats', '--no-stream'], stdout=subprocess.PIPE)
    output = result.stdout.decode('utf-8').splitlines()

    total_memory = 0

    # Skip the header line (first line)
    for line in output[1:]:
        # Split columns and extract the memory usage column (4th column)
        columns = line.split()
        if len(columns) >= 6:
            mem_usage_str = columns[3]  # MEM USAGE / LIMIT column
            mem_usage = mem_usage_str.split('/')[0].strip()  # Take only the memory usage part
            try:
                mem_usage_mib = parse_memory_usage(mem_usage)
                total_memory += mem_usage_mib
            except ValueError as e:
                print(f"Error parsing memory usage: {e}")

    return total_memory

if __name__ == "__main__":
    total_memory = get_total_memory_usage()
    print(f"Total memory usage of all containers: {total_memory:.2f} MiB")
