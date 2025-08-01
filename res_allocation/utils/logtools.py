import os
import re
import ast
import numpy as np
from datetime import datetime

def get_latest_subdirectory(directory: str, target_cache_bw: list = None) -> str:
    subdirs = [os.path.join(directory, d) for d in os.listdir(directory) if os.path.isdir(os.path.join(directory, d))]
    if target_cache_bw is not None:
        target_subdirs = []
        for subdir in subdirs:
            cache, bw = extract_properties(subdir)
            if cache is not None and bw is not None and int(cache) == target_cache_bw[0] and int(bw) == target_cache_bw[1]:
                target_subdirs.append(subdir)
        subdirs = target_subdirs
    # Extract date
    date_subdir = []
    for subdir in subdirs:
        try:
            date_subdir.append({'path': subdir, 'date': datetime.strptime(subdir.split('_')[-3].split('/')[-1], "%Y%m%d-%H%M%S")})
        except ValueError:
            pass    # skip
    # Assuming subdirectory names are in the format 'YYYYMMDD-HHMMSS'
    # latest_subdir = max(subdirs, key=lambda d: datetime.strptime(d.split('_')[-3].split('/')[-1], "%Y%m%d-%H%M%S"))
    latest_subdir = max(date_subdir, key=lambda d: d['date'])['path']
    return latest_subdir

def extract_properties(dir_name):
    pattern = r"(\d+)mb_(\d+)mbps"
    match = re.search(pattern, dir_name)
    if match:
        return int(match.group(1)), int(match.group(2))
    else:
        return None, None

def parse_log_file(file_path, file_id, n_collect_lines=600, n_skip_lines=100, user_inbalance_threshold=0.3, include_use=False, perf_metric='perf'):
    measurement_tag = "- INFO - New raw measurement:"
    data = []
    per_user_counter = {}
    target_fields = ['user_id', perf_metric]
    if include_use:
        target_fields += ['cache', 'mem_bw']

    with open(file_path, 'r') as file:
        idx = 0
        measurement_info = ""
        measurement_dict = {}
        for line in file:
            if measurement_tag in line:
                try:
                    measurement_info = line.split(measurement_tag)[1]
                    measurement_info = measurement_info.strip()[1:-1]  # Removing braces
                    parts = measurement_info.split(', ')
                    measurement_dict = {p.split(': ')[0]: int(p.split(': ')[1].split('/')[0]) for p in parts if p.split(': ')[0] in target_fields}
                    if include_use:
                        data.append([idx, measurement_dict.get('user_id', None),
                                     measurement_dict.get(perf_metric, None),
                                     measurement_dict.get('cache', None),
                                     measurement_dict.get('mem_bw', None), file_id])
                    else:
                        data.append([idx, measurement_dict.get('user_id', None), measurement_dict.get(perf_metric, None), file_id])
                    if measurement_dict.get('user_id', None) is not None:
                        if measurement_dict['user_id'] in per_user_counter:
                            per_user_counter[measurement_dict['user_id']] += 1
                        else:
                            per_user_counter[measurement_dict['user_id']] = 1
                    idx += 1
                except Exception as e:
                    print(f"Error parsing line: {line}. mInfo: {measurement_info} mDict: {measurement_dict} Error: {e}")
        if len(per_user_counter) > 0:
            print(f"Total {len(per_user_counter)} users with measurements: {per_user_counter}")
            # Check if there is any user with inbalanced number of measurements,
            # comparing across users (e.g., average number of measurements)
            avg_measurements = sum(per_user_counter.values()) / len(per_user_counter)
            for user_id, count in per_user_counter.items():
                if count < user_inbalance_threshold * avg_measurements or count > (1 + user_inbalance_threshold) * avg_measurements:
                    print(f"User {user_id} has {count} measurements, which is inbalanced compared to the average {avg_measurements}.")
    # print(data)
    data = data if len(data) <= n_collect_lines else data[:n_collect_lines]
    data = data if len(data) <= n_skip_lines else data[n_skip_lines:]
    # print the cache and bandwidth use per user_id
    if include_use:
        user_metrics = {}
        for d in data:
            user_id = d[1]
            cache = d[3]
            bw = d[4]

            if user_id not in user_metrics:
                user_metrics[user_id] = {"cache": [], "bw": []}

            user_metrics[user_id]["cache"].append(cache)
            user_metrics[user_id]["bw"].append(bw)

        for user_id, metrics in user_metrics.items():
            avg_cache = np.mean(metrics["cache"])
            avg_bw = np.mean(metrics["bw"])
            print(f"User {user_id} - Average cache use: {avg_cache:.2f}, Average bandwidth use: {avg_bw:.2f}")

        # Also print overall averages for reference
        cache_use = np.mean([d[3] for d in data])
        bw_use = np.mean([d[4] for d in data])
        print(f"Overall - Average cache use: {cache_use:.2f}, Average bandwidth use: {bw_use:.2f}")
    return data

def parse_cont_logs(file_path, file_id, app_id, latency=False):
    '''
    Parse container log files and return the parsed data as a list of tuples.
    Uses the specialized LogParser classes from log_parsers module.
    '''
    from utils.log_parsers import (
        MemcachedLogParser, SocialNetworkLogParser, StreamLogParser, DlrmLogParser
    )

    data = []
    idx = 0
    parser = None

    # Select the appropriate parser based on file path
    if 'spirit_mc_client' in file_path:
        print(f"Parsing memcached client log file at {file_path}")
        parser = MemcachedLogParser(app_id=app_id, latency=latency)

    elif 'spirit_socialnet_client' in file_path or 'spirit_social_net' in file_path:
        print(f"Parsing social network client log file at {file_path}")
        # spirit_social_net_1_client.log
        if '_client' in file_path:
            parser = SocialNetworkLogParser(app_id=app_id)
        elif '_metrics' in file_path:
            parser = SocialNetworkLogParser(app_id=app_id, latency=True)

    elif 'spirit_stream' in file_path:
        print(f"Parsing stream client log file at {file_path}")
        parser = StreamLogParser(app_id=app_id)

    elif 'spirit_dlrm_inf' in file_path:
        print(f"Parsing DLRM inference log file at {file_path}")
        parser = DlrmLogParser(app_id=app_id)

    # Use the parser if one was selected
    if parser:
        median, _, _ = parser.parse_log_file(file_path)
        # Maintain the same output format as before: [idx, app_id, perf, file_id]
        if median > 0:
            print(f"Performance metric: {median:.2f}")
            data.append([idx, app_id, median, file_id])
    else:
        # Fallback to original implementation for unhandled log types
        print(f"No specialized parser found for {file_path}, using basic parsing")
        # Here we could add the original parsing logic from the function
        # or implement a generic parser

    return data

def parse_log_file_target_reqs(file_path, file_id, target_reqs=1000000, metrics=['perf']):
    measurement_tag = "- INFO - New raw measurement:"
    data = []
    total_reqs = 0
    with open(file_path, 'r') as file:
        idx = 0
        measurement_info = ""
        measurement_dict = {}
        for line in file:
            if measurement_tag in line:
                try:
                    measurement_info = line.split(measurement_tag)[1]
                    measurement_info = measurement_info.strip()[1:-1]  # Removing braces
                    parts = measurement_info.split(', ')
                    _metrics = ['user_id'] + metrics
                    measurement_dict = {p.split(': ')[0]: int(p.split(': ')[1].split('/')[0]) for p in parts if p.split(': ')[0] in _metrics}
                    measurements = [measurement_dict.get(met, None) for met in metrics]
                    data.append([idx, measurement_dict.get('user_id', None)] + measurements + [file_id])
                    idx += 1
                    total_reqs += measurement_dict.get('perf', 0)
                    if target_reqs > 0 and total_reqs >= target_reqs:
                        break
                except Exception as e:
                    print(f"Error parsing line: {line}. mInfo: {measurement_info} mDict: {measurement_dict} Error: {e}")
    return data

def parse_log_file_mainlog(file_path, file_id, target_reqs=1000000, metrics=['hit_rate_percent', 'local_lat', 'remote_lat']):
    data = []
    total_reqs = 0
    idx = 0
    with open(file_path, 'r') as file:
        for line in file:
            try:
                log_data = ast.literal_eval(line.strip())
                for vm_id, app_data in log_data.get("map", {}).items():
                    for app_id, metrics_data in app_data.items():
                        user_id = metrics_data['app_id']
                        measurements = [metrics_data.get(met, None) for met in metrics]
                        data.append([idx, user_id] + measurements + [file_id])
                        total_reqs += metrics_data.get('access_rate_ops_sec', 0)
                        if target_reqs > 0 and total_reqs >= target_reqs:
                            break
                    idx += 1
                    if target_reqs > 0 and total_reqs >= target_reqs:
                        break
                if target_reqs > 0 and total_reqs >= target_reqs:
                    break
            except Exception as e:
                # ignore non related lines
                pass
    return data