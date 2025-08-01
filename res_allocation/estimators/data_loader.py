import os
import json
import numpy as np

def parse_log_file(file_path, cache_size, bandwidth, user_id='1'):
    '''Parse the log file and return a list of data points, given cache size and bandwidth.'''
    data_points = []
    with open(file_path, 'r') as file:
        for line in file:
            try:
                data = json.loads(line)
                access_rate = data["map"]["0"][user_id]["access_rate_ops_sec"]
                if access_rate != 0:  # Ignore data with zero; likely erroneous case
                    data_points.append({'cache_in_mb': int(cache_size), 'bw_in_gbps': float(bandwidth) / 1024., 'performance': access_rate})
            except (KeyError, ValueError) as e:
                print(f"Error processing line in {file_path}: {e}")
                continue
    
    return data_points

def collect_data(directory, user_id='1'):
    '''Collect data from the log files in the given directory and return a list of data points.'''
    all_data_points = []
    for file_name in os.listdir(directory):
        print(f"File: {file_name}")
        if not file_name.startswith("c_") or not file_name.endswith(".log"):
            continue  # Skip non-log files
        
        # Extract cache size and bandwidth from the file name
        parts = file_name.split('_')
        cache_size = parts[1]  # Assuming cache size is correctly extracted here
        bandwidth = parts[3].split('.')[0]  # Assuming bandwidth is correctly extracted here
        
        # Parse the file to get data points
        file_path = os.path.join(directory, file_name)
        data_points = parse_log_file(file_path, cache_size, bandwidth, user_id)
        
        # Extend the list with data points from this file
        all_data_points.extend(data_points)
    
    return all_data_points