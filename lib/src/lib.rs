pub mod commands;
pub mod libcache_loader;
pub mod process_requests;

use rand::distributions::{Distribution, Uniform};
use rand::{thread_rng, Rng};
use rocket::serde::{json::serde_json, Deserialize, Serialize};
use std::cmp::max;
use std::collections::hash_map::HashMap;
use std::time::Duration;
use net2::TcpBuilder;
use std::process::Command;


pub type AppId = u64;
pub type VmId = u64;
pub type MemoryMb = u64;
pub type BandwidthMbps = u64;
pub type Port = u64;

pub const REPORT_INTERVAL_MS: u64 = 1000;
pub const UPDATE_INTERVAL_MS: u64 = 200;
pub const BACKEND_RETRY_MS: u64 = 1;
pub const RATE_LIMIT: u64 = 1_000_000;
pub const RATE_LIMIT_INTERVAL_MS: u64 = 200;
pub const IS_RATE_LIMITED: bool = false;
pub const VALUE_SIMULATION_THRESHOLD: u64 = 32; // 1 KB
pub const PRECOMPUTED_SIZE: usize = 1_048_576; // Precompute 1MB of random data

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BackendAuth {
    pub admin_url: String,
    pub admin_id: String,
    pub admin_pass: String,
    pub access_key: String,
    pub secret_key: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(crate = "rocket::serde")]
pub struct InitConfig {
    // pub id_port_map: HashMap<AppId, Port>,
    pub id_preload_map: HashMap<AppId, String>,
    pub id_benchmark_map: HashMap<AppId, String>,
    pub backend_ip_map: HashMap<AppId, Vec<String>>,
    pub backend_auth: Option<HashMap<AppId, Vec<BackendAuth>>>,
    pub config_path: Option<HashMap<AppId, String>>,
    pub global_ip: String,
    pub val_size_bytes: u64,
    pub network_limit_mbps: Option<BandwidthMbps>,
    pub netdev: Option<String>,     // Network device name in the cache client ()== benchmark runner)
    pub cache_ip: Option<String>,   // IP address of the cache node
    pub bind_port: Option<Port>,    // Port for the benchmark runner to bind and listen
    pub metric_port: Option<Port>,  // Port for the benchmark client to expose metrics
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(crate = "rocket::serde")]
pub struct UpdateConfig {
    pub allocation_map: HashMap<AppId, (MemoryMb, BandwidthMbps)>,
}

impl UpdateConfig {
    pub fn new() -> Self {
        UpdateConfig {
            allocation_map: HashMap::new(),
        }
    }

    pub fn to_string(&self) -> String {
        serde_json::to_string(&self).unwrap()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(crate = "rocket::serde")]
pub struct AppUsage {
    pub vm_id: VmId,
    pub app_id: AppId,
    pub mem_mb: MemoryMb,
    pub bw_mbps: BandwidthMbps,
    pub cache_mbps: BandwidthMbps,
    pub access_mem_ops_sec: u64,
    pub access_rate_ops_sec: u64,
    pub hit_rate_percent: f64,
    pub local_lat: u32,
    pub remote_lat: u32,
    pub mrc: Option<Vec<(u64, f64)>>
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(crate = "rocket::serde")]
pub struct UsageMap {
    pub map: HashMap<VmId, HashMap<AppId, AppUsage>>,
}

impl UsageMap {
    pub fn new() -> Self {
        UsageMap {
            map: HashMap::new(),
        }
    }

    pub fn to_string(&self) -> String {
        serde_json::to_string(&self).unwrap()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(crate = "rocket::serde")]
pub struct AppStats {
    pub anon_memory_mb: HashMap<AppId, MemoryMb>,
}

pub fn bw_to_req(bw_mbps: BandwidthMbps, object_size_bytes: u64) -> u64 {
    let total_memory_bytes = (bw_mbps * 1000000) / 8;
    let reqs_per_sec = max(1, total_memory_bytes / object_size_bytes);
    let intervals_per_sec = 1000 / UPDATE_INTERVAL_MS;
    reqs_per_sec / intervals_per_sec
}

pub fn req_to_bw(reqs: u64, object_size_bytes: u64) -> BandwidthMbps {
    let period = REPORT_INTERVAL_MS as f64 / 1000.0;
    let total_memory_bytes = reqs * object_size_bytes;
    let total_memory_mb = total_memory_bytes / (1024 * 1024);
    ((total_memory_mb * 8) as f64 / period) as u64
}

// convert bytes collected in UPDATE_INTERVAL_MS to Mbps
pub fn bytes_to_report_mbps(bytes: u64, time_elapsed: Duration) -> BandwidthMbps {
    // let period = REPORT_INTERVAL_MS as f64 / 1000.0;
    let period = time_elapsed.as_secs_f64();
    ((bytes * 8) as f64 / period) as u64 / (1024 * 1024)
}

// convert Mbps to bytes collected in UPDATE_INTERVAL_MS
pub fn mbps_to_bytes_interval(mbps: BandwidthMbps) -> u64 {
    let intervals_per_sec = 1000 / UPDATE_INTERVAL_MS;
    mbps * 1024 * 1024 / (intervals_per_sec * 8)
}

// Utils for random string generation
// Function to precompute a random string of given size
pub fn precompute_random_string(size: usize) -> String {
    let mut rng = thread_rng();
    let ascii_range = Uniform::from(97..123); // Lowercase letters a-z
    (0..size)
        .map(|_| ascii_range.sample(&mut rng) as u8 as char)
        .collect()
}

// Function to get a random string of desired length using the precomputed string
pub fn get_random_string(source: &String, len: usize) -> String {
    if len <= source.len() {
        // If requested length is within the single precomputed size, slice directly
        let start = if len == source.len() {
            0
        } else {
            thread_rng().gen_range(0..source.len() - len)
        };
        source[start..start + len].to_string()
    } else {
        // If requested length is greater, repeat the precomputed string
        let repeats = len / source.len() + 1; // Calculate necessary repeats
        source.repeat(repeats)[..len].to_string()
    }
}

// Check the port and terminate the process using the port if it is already in use
pub async fn precheck_port(port: u16, wait_time_ms: u64) {
    loop {
        let port_bind = TcpBuilder::new_v4()
            .unwrap()
            .bind(("0.0.0.0", port))
            .is_ok();
        if port_bind {
            break;
        }
        // find and terminate the process that is using the port
        println!(
            "Port {} is already in use. Trying to terminate the process using the port...",
            port
        );
        let output = Command::new("lsof")
            .arg("-i")
            .arg(format!(":{}", port))
            .output()
            .expect("failed to execute process");
        let output_str = String::from_utf8_lossy(&output.stdout);
        let mut pid = 0;
        for line in output_str.lines() {
            if line.contains("LISTEN") {
                let iter = line.split_whitespace();
                let mut i = 0;
                for s in iter {
                    if i == 1 {
                        pid = s.parse::<i32>().unwrap();
                        break;
                    }
                    i += 1;
                }
            }
        }
        if pid != 0 {
            println!("Terminating process with pid {}", pid);
            Command::new("kill")
                .arg("-9")
                .arg(format!("{}", pid))
                .output()
                .expect("failed to execute process");
        }
        tokio::time::sleep(std::time::Duration::from_millis(
            wait_time_ms
        ))
        .await;
    }
}
