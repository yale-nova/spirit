"""
Log parser implementations for different applications.
This module provides base and specific parsers for parsing logs from different applications.
"""

import re
import numpy as np
import json
from abc import ABC, abstractmethod


class LogParser(ABC):
    """Base class for log parsers"""

    def __init__(self, app_id="1", filter_index=(0, -1)):
        """
        Initialize the log parser.

        Args:
            app_id (str): Application ID to filter logs
            filter_index (tuple): Range of indices to filter (start, end)
        """
        self.app_id = app_id
        self.filter_index = filter_index

    @abstractmethod
    def get_log_filename(self):
        """Return the expected log filename for this application"""
        pass

    @abstractmethod
    def parse_log_file(self, file_path):
        """
        Parse the log file and extract throughput/bandwidth measurements.

        Args:
            file_path (str): Path to the log file

        Returns:
            tuple: (median, p10, p90) of the bandwidth measurements
        """
        pass

    def _filter_measurements(self, measurements):
        """
        Filter measurements based on filter_index and compute statistics

        Args:
            measurements (list): List of bandwidth measurements

        Returns:
            tuple: (median, p10, p90) of the filtered measurements
        """
        if not measurements:
            return 0, 0, 0

        print(f"  Found {len(measurements)} measurements")
        # Apply filtering if needed
        if self.filter_index and len(measurements) > self.filter_index[1]:
            filtered_measurements = measurements[self.filter_index[0]:self.filter_index[1]]
        else:
            # Use the second half of measurements to avoid warmup period
            half_point = len(measurements) // 2
            filtered_measurements = measurements[half_point:]

        if filtered_measurements:
            # Calculate statistics
            median = np.median(filtered_measurements)
            p10 = np.percentile(filtered_measurements, 10)
            p90 = np.percentile(filtered_measurements, 90)

            print(f"  Extracted {len(filtered_measurements)} bandwidth measurements")
            print(f"  Bandwidth stats: median={median:.2f}, p10={p10:.2f}, p90={p90:.2f} Mbps")

            return median, p10, p90

        return 0, 0, 0


class MemcachedLogParser(LogParser):
    """Parser for Memcached logs"""
    def __init__(self, app_id="1", filter_index=(0, -1), latency=False):
        super().__init__(app_id, filter_index)
        self.latency = latency

    def get_log_filename(self):
        return f"spirit_mc_client_{self.app_id}.log"

    def parse_log_file(self, file_path):
        """
        Parse the Memcached log file and extract throughput/bandwidth or latency measurements.

        Args:
            file_path (str): Path to the log file
            return_latency (bool): If True, return 90th percentile latency instead of bandwidth

        Returns:
            tuple: (value, 0, 0) where value is the last bandwidth or latency measurement
        """
        return_latency = self.latency
        try:
            with open(file_path, 'r') as file:
                content = file.readlines()

                # Look for bandwidth values in the log file
                # Format 1: "X requests sent | Y bytes | Z Mbps | L us | active: A"
                # Format 2: "X requests sent | Y bytes | Z Mbps | avg: L us | 75th: P1 us, 90th: P2 us, 99th: P3 us | active: A"

                # Define patterns for both formats
                bandwidth_pattern1 = r'(\d+) requests sent \| \d+ bytes \| ([\d.]+) Mbps \| ([\d.]+) us \| active: \d+'
                bandwidth_pattern2 = r'(\d+) requests sent \| \d+ bytes \| ([\d.]+) Mbps \| avg: ([\d.]+) us \|.*?99th: ([\d.]+) us.*?\| active: \d+'

                # Only keep the last match
                last_requests = 0
                last_bandwidth = 0
                last_latency = 0

                for line in content:
                    # Try the first pattern
                    match = re.search(bandwidth_pattern1, line)
                    if match:
                        requests = int(match.group(1))
                        if requests >= last_requests:
                            last_requests = requests
                            last_bandwidth = float(match.group(2))
                            last_latency = float(match.group(3))  # This is avg latency in format 1
                    else:
                        # If first pattern doesn't match, try the alternative pattern
                        match = re.search(bandwidth_pattern2, line)
                        if match:
                            requests = int(match.group(1))
                            if requests >= last_requests:
                                last_requests = requests
                                last_bandwidth = float(match.group(2))
                                # 90th percentile latency
                                last_latency = float(match.group(4))
                                # avg latency
                                # last_latency = float(match.group(3))  # This is avg latency in format 2

                if last_requests > 0:
                    if return_latency:
                        return last_latency, 0, 0  # Return only the last latency value
                    else:
                        return last_bandwidth, 0, 0  # Return only the last bandwidth value

                if return_latency:
                    print(f"  No latency measurements found in {file_path}")
                else:
                    print(f"  No bandwidth measurements found in {file_path}")
                return 0, 0, 0

        except Exception as e:
            print(f"Error processing file {file_path}: {e}")
            return 0, 0, 0


class SocialNetworkLogParser(LogParser):
    """Parser for Social Network logs"""
    def __init__(self, app_id="1", filter_index=(0, -1), latency=False, percentile=0.99):
        """
        Initialize the Social Network log parser.

        Args:
            app_id (str): Application ID to filter logs
            filter_index (tuple): Range of indices to filter (start, end)
            latency (bool): If True, parse latency measurements instead of bandwidth
        """
        super().__init__(app_id, filter_index)
        self.latency = latency
        self.percentile = percentile

    def get_log_filename(self):
        return f"spirit_social_net_{self.app_id}_client.log"

    def parse_log_file(self, file_path):
        """Parse the Social Network log file and extract throughput or latency measurements."""
        try:
            if self.latency:
                # Use the JSON parsing function for latency
                parsed_json = self.parse_log_file_to_json(file_path)
                if parsed_json and 'cdf' in parsed_json:
                    cdf_data = parsed_json['cdf']
                    for latency_value, percentile in cdf_data:
                        if percentile >= self.percentile:
                            return latency_value, 0, 0  # Return 90th percentile latency

                print(f"  No latency CDF measurements found in {file_path}")
                return 0, 0, 0

            with open(file_path, 'r') as file:
                content = file.readlines()

                # Look for throughput values in the log file
                throughput_pattern = r'Performed (\d+) timeline reads, Throughput: ([\d.]+) req/s, Avg Latency: [\d.]+ ms'
                throughputs = []
                for line in content:
                    match = re.search(throughput_pattern, line)
                    if match:
                        reads = int(match.group(1))
                        throughput = float(match.group(2))
                        throughputs.append((reads, throughput))
                if throughputs:
                    return throughputs[-1][1], 0, 0  # Return only the last throughput value

                print(f"  No throughput measurements found in {file_path}")
                return 0, 0, 0

        except Exception as e:
            print(f"Error processing file {file_path}: {e}")
            return 0, 0, 0

    def parse_log_file_to_json(self, file_path):
        """Parse the log file and convert it into JSON format."""
        try:
            with open(file_path, 'r') as file:
                content = file.read()

                # Attempt to fix unmatched brackets
                if content.count('[') != content.count(']'):
                    print("Warning: Unmatched brackets detected. Attempting to fix...")
                    content = content.rstrip(']').rstrip(',') + ']'  # Fix unmatched brackets

                # Parse the content as JSON
                parsed_json = json.loads(content)
                return parsed_json

        except json.JSONDecodeError as e:
            print(f"JSON decoding error in file {file_path}: {e}")
        except Exception as e:
            print(f"Error processing file {file_path}: {e}")

        return None


class StreamLogParser(LogParser):
    """Parser for STREAM benchmark logs"""

    def get_log_filename(self):
        return f"spirit_stream_{self.app_id}.log"

    def parse_log_file(self, file_path):
        """Parse the STREAM log file and extract the highest iteration number."""
        try:
            with open(file_path, 'r') as file:
                content = file.readlines()

                # Look for iteration numbers in the log file
                highest_iteration = 0
                num_detecteed_iterations = 0

                for line in content:
                    # Match patterns like "Iteration X - Copy phase completed" or any other phase
                    match = re.search(r'Iteration (\d+) -', line)
                    if match:
                        iteration = int(match.group(1))
                        if iteration > highest_iteration:
                            highest_iteration = iteration
                        num_detecteed_iterations += 1

                if highest_iteration > 0:
                    print(f"  Highest iteration detected: {highest_iteration}, Total iterations: {num_detecteed_iterations}")
                    # Return highest iteration as median, and zeros for p10 and p90
                    return num_detecteed_iterations, 0, 0

                print(f"  No iterations found in {file_path}")
                return 0, 0, 0

        except Exception as e:
            print(f"Error processing file {file_path}: {e}")
            return 0, 0, 0

class DlrmLogParser(LogParser):
    """Parser for DLRM logs"""

    def get_log_filename(self):
        return f"spirit_dlrm_inf_{self.app_id}.log"

    def parse_log_file(self, file_path):
        """Parse the DLRM log file and extract throughput measurements."""
        try:
            with open(file_path, 'r') as file:
                content = file.readlines()

                # Look for throughput values in the log file
                # Format: "Batch 1710 inference time: 1078562.26 us"
                throughput_pattern = r'Batch (\d+) inference time: ([\d.]+) us'

                throughputs = []
                for line in content:
                    match = re.search(throughput_pattern, line)
                    if match:
                        # throughput = float(match.group(1))
                        # throughputs.append(throughput)
                        #
                        throughput = 1. / float(match.group(2))
                        throughputs.append(throughput)

                if throughputs:
                    # throughput based
                    # return throughputs[-1], 0, 0
                    # latency based
                    # return self._filter_measurements(throughputs)
                    # average and 0, 0
                    return np.mean(throughputs[-100:]), 0, 0


                print(f"  No throughput measurements found in {file_path}")
                return 0, 0, 0

        except Exception as e:
            print(f"Error processing file {file_path}: {e}")
            return 0, 0, 0

# Add more parsers as needed
# class RocksDBLogParser(LogParser):
#     """Parser for RocksDB logs"""
#
#     def get_log_filename(self):
#         return f"spirit_rocks_client_{self.app_id}.log"
#
#     def parse_log_file(self, file_path):
#         # Implementation for RocksDB log parsing
#         pass