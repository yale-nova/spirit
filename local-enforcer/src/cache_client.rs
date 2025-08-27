use futures::Future;
use regex::Regex;
use std::collections::{HashMap, VecDeque};
use std::{fs, mem};
use std::path::Path;
use std::io::Write;
use std::ops::Drop;
use std::process::Command;
use std::sync::{Arc, Mutex, RwLock};
use std::time::{SystemTime, UNIX_EPOCH, Instant};
use rand::Rng;

use lib::{
    commands::send_usage, AppId, AppUsage, BandwidthMbps, MemoryMb, UpdateConfig, UsageMap, VmId, AppStats, Port
};

use crate::InitConfig;
use chrono;
use core_affinity;

const MAX_CONTAINER_NUM: u64 = 128;
const MBPS_TO_BYTES_PER_SEC: u64 = 125000;
const PERF_HISTORY_LENGTH: usize = 16;
const OS_PREFETCH_FACTOR: f64 = (1 << 3) as f64; // OS prefetch factor

const MAX_CACHE_IN_MB: u64 = 16*1024; // Maximum cache size in MB
const CACHE_GRANULARITY_MB: u64 = 256; // Cache granularity in MB
const CACHE_SAMPLE_RATE: u64 = 25; // Sampling rate for computing MRC (default: 100)
const CACHE_LINE_SIZE_BYTES: u64 = 64; // Cache line size in bytes
const PAGE_SIZE_BYTES: u64 = 4096; // Cache line size in bytes

const SAMPLE_DURATION_SECS: f32 = 1.; // Duration for sampling addresses in seconds
const MEASURE_DURATION_SECS: f32 = 5.; // Duration for measuring addresses in seconds
const LEN_PERF_HISTORY: usize = 100; // Length of the perf history

const SEPRATE_COMPULSORY: bool = false; // Separate compulsory and capacity misses
const EVICTION_PRESSURE_RATIO: f64 = 0.9; // Eviction pressure starts at this ratio of cache size
const EVICTION_PRESSURE_MB: u64 = 256; // Eviction pressure starts at this size in MB

struct BlkUseStat {
    prev_read_bytes: u64,
    prev_write_bytes: u64,
    prev_time: u128,
}

impl BlkUseStat {
    fn new() -> Self {
        BlkUseStat {
            prev_read_bytes: 0,
            prev_write_bytes: 0,
            prev_time: 0,
        }
    }
}

struct PerfStat {
    cache_misses: VecDeque<u64>,
    cache_references: VecDeque<u64>,
    major_faults: VecDeque<u64>,
    mem_ops: VecDeque<u64>,
    avg_miss: u64,
    avg_hit: u64,
    avg_faults: u64,
    avg_mem_ops: u64,
    mrc: Vec<(u64, f64)>,
}

impl PerfStat {
    fn new() -> Self {
        PerfStat {
            cache_misses: VecDeque::new(),
            cache_references: VecDeque::new(),
            major_faults: VecDeque::new(),
            mem_ops: VecDeque::new(),
            avg_miss: 0,
            avg_hit: 0,
            avg_faults: 0,
            avg_mem_ops: 0,
            mrc: Vec::new(),
        }
    }

    fn update(&mut self, miss: u64, hit: u64, faults: u64, mem_ops: u64) {
        self.cache_misses.push_back(miss);
        if self.cache_misses.len() > PERF_HISTORY_LENGTH {
            self.cache_misses.pop_front();
        }
        self.cache_references.push_back(hit);
        if self.cache_references.len() > PERF_HISTORY_LENGTH {
            self.cache_references.pop_front();
        }
        self.major_faults.push_back(faults);
        if self.major_faults.len() > PERF_HISTORY_LENGTH {
            self.major_faults.pop_front();
        }
        self.mem_ops.push_back(mem_ops);
        if self.mem_ops.len() > PERF_HISTORY_LENGTH {
            self.mem_ops.pop_front();
        }
        self.avg_miss = self.cache_misses.iter().sum::<u64>() / self.cache_misses.len() as u64;
        self.avg_hit =
            self.cache_references.iter().sum::<u64>() / self.cache_references.len() as u64;
        self.avg_faults = self.major_faults.iter().sum::<u64>() / self.major_faults.len() as u64;
        self.avg_mem_ops = self.mem_ops.iter().sum::<u64>() / self.mem_ops.len() as u64;
    }

    fn update_mrc(&mut self, mrc: Vec<(u64, f64)>) {
        self.mrc = mrc;
    }

    fn drop_records(&mut self) {
        self.cache_misses.clear();
        self.cache_references.clear();
        self.avg_miss = 0;
        self.avg_hit = 0;
    }
}

pub struct CacheClient {
    pub cur_config: Arc<tokio::sync::RwLock<UpdateConfig>>,
    pub id: VmId,
    pub id_container_map: HashMap<AppId, String>, // AppId -> Container ID or name
    pub container_cgroups_map: Arc<RwLock<HashMap<String, String>>>, // Container ID or name -> cgroup path
    conatiner_blk_use_stat: HashMap<AppId, Arc<Mutex<BlkUseStat>>>,
    container_perf_stat: Arc<Mutex<HashMap<AppId, PerfStat>>>,
    latest_usage: Arc<tokio::sync::Mutex<Vec<(UsageMap, AppStats)>>>,
    global_ip: String,
    memory_ip: String,
    memory_dev_name: String,
    memory_dev_maj_min: String,
    pub running_benchmarks: bool,
    child_processes: HashMap<AppId, std::process::Child>,
    enable_mrc: bool,
}

impl CacheClient {
    pub fn mk_client() -> Self {
        CacheClient {
            cur_config: Arc::new(tokio::sync::RwLock::new(UpdateConfig::new())),
            id: 0,
            id_container_map: HashMap::new(),
            container_cgroups_map: Arc::new(RwLock::new(HashMap::new())),
            conatiner_blk_use_stat: HashMap::new(),
            container_perf_stat: Arc::new(Mutex::new(HashMap::new())),
            latest_usage: Arc::new(tokio::sync::Mutex::new(Vec::new())),
            global_ip: String::new(),
            memory_ip: String::new(),
            memory_dev_name: String::new(),
            memory_dev_maj_min: String::new(),
            running_benchmarks: false,
            child_processes: HashMap::new(),
            enable_mrc: true,
        }
    }

    // cont_cgroup_map: Arc<RwLock<HashMap<String, String>>>
    pub fn get_cgroup_path(container_name: &str, cont_cgroup_map: Arc<RwLock<HashMap<String, String>>>) -> Option<String> {
        let cgroup_path_locked = cont_cgroup_map.read().unwrap();
        let cgroup_path = cgroup_path_locked.get(container_name);
        if cgroup_path.is_none() {
            // update the cgroup path
            drop(cgroup_path_locked);
            let group_path = Self::get_cgroup_path_docker(container_name);
            // update the cgroup map if group__path is not None
            if !group_path.is_none() {
                let mut cgroup_map = cont_cgroup_map.write().unwrap();
                let cgroup_path = group_path.unwrap();
                cgroup_map.insert(container_name.to_string(), cgroup_path.clone());
                println!("Updated cgroup path for container {}: {}", container_name, cgroup_path);
                return Some(cgroup_path);
            }
            return None;
        }
        Some(cgroup_path.unwrap().clone())
    }

    pub fn get_cgroup_path_docker(container_name: &str) -> Option<String> {
        // Get PID and cgroup name of the container
        let mut attempts = 0;
        let max_attempts = 5; // Define maximum retry attempts
        let mut pid = String::new();
        let mut cgroup = String::new();

        while attempts < max_attempts {
            let output = Command::new("docker")
                .arg("inspect")
                .arg("-f")
                .arg("{{.State.Pid}}")
                .arg(container_name)
                .output();

            match output {
                Ok(output) => {
                    pid = String::from_utf8_lossy(&output.stdout).trim().to_string();
                    let cgroup_result = fs::read_to_string(format!("/proc/{}/cgroup", pid));
                    match cgroup_result {
                        Ok(cgroup_str) => {
                            cgroup = cgroup_str;
                            break;
                        }
                        Err(_) => {
                            attempts += 1;
                            continue;
                        }
                    }
                }
                Err(_) => {
                    attempts += 1;
                    continue;
                }
            }
        }

        if attempts == max_attempts {
            // panic!(
            //     "Failed to get PID and cgroup after {} attempts",
            //     max_attempts
            // );
            eprintln!("Failed to get PID and cgroup after {} attempts", max_attempts);
            return None;
        }
        // Get cgroup path of the container
        let cgroup_path = cgroup
            .lines()
            .find(|line| line.contains("::/"))
            .unwrap()
            .split("::")
            .nth(1)
            .unwrap();
        Some(format!("/sys/fs/cgroup{}", cgroup_path.to_string()))
    }

    pub fn update_mem_dev(&mut self, dev_name: String) {
        self.memory_dev_name = dev_name;
        // Get MAJ:MIN of the device
        let output = Command::new("lsblk")
            .arg("-dno")
            .arg("MAJ:MIN")
            .arg(&self.memory_dev_name)
            .output()
            .expect("Failed to execute command");
        let maj_min = String::from_utf8_lossy(&output.stdout).trim().to_string();
        self.memory_dev_maj_min = maj_min.clone();
    }

    pub fn update_swap_bandwidth(&self, app_id: &AppId, mem_bw: &BandwidthMbps) {
        let container_name = self.id_container_map.get(app_id);
        let container_name = match container_name {
            None => {
                eprintln!(
                    "update_swap_bandwidth :: Container not found for app_id: {}",
                    app_id
                );
                return;
            }
            _ => container_name.unwrap(),
        };
        // Apply updated limits
        let cgroup_path = Self::get_cgroup_path(container_name, self.container_cgroups_map.clone());
        println!("Trying to set I/O limits to {} Mbps for container {}", mem_bw, container_name);
        if cgroup_path.is_none() {
            eprintln!("Failed to update I/O limits :: reason - cgroup path for container {}", container_name);
            return;
        }

        // Set I/O limits
        let mut file = fs::OpenOptions::new()
            .write(true)
            .open(format!("{}/io.max", cgroup_path.unwrap()))
            .expect("Failed to open io.max file");
        let bw_alloc_str = format!(
            "{} rbps={} wbps={}",
            self.memory_dev_maj_min,
            *mem_bw * MBPS_TO_BYTES_PER_SEC,
            *mem_bw * MBPS_TO_BYTES_PER_SEC
        );
        // println!("bw_alloc_str: {}", bw_alloc_str);
        write!(file, "{}", bw_alloc_str).expect("Failed to write to io.max file");
        println!("Set I/O limits to {} Mbps", *mem_bw);
    }

    // == Gradual decrease ==
    pub async fn set_memory_size_via_cgroup(&self, app_id: &AppId, mem_size: &MemoryMb) {
        let container_name = match self.id_container_map.get(app_id) {
            None => {
                eprintln!(
                    "set_memory_size_via_cgroup :: Container not found for app_id: {}",
                    app_id
                );
                return;
            }
            Some(c) => c,
        };

        // Get the cgroup path for the container
        let cgroup_path = match Self::get_cgroup_path(container_name, self.container_cgroups_map.clone()) {
            None => {
                eprintln!(
                    "Failed to set memory size :: reason - no cgroup path for container {}",
                    container_name
                );
                return;
            }
            Some(p) => p,
        };

        let mem_limit_path = format!("{}/memory.max", cgroup_path);
        let mem_high_path = format!("{}/memory.high", cgroup_path);
        let swap_limit_path = format!("{}/memory.swap.max", cgroup_path);

        // Read current memory limit
        let current_mem_str = fs::read_to_string(&mem_limit_path)
            .unwrap_or_else(|_| "0".to_string())
            .trim()
            .to_string();

        // Parse current memory limit (memory.max is in bytes)
        let current_mem_bytes: u64 = current_mem_str.parse().unwrap_or(0);
        let current_mem_mb = if current_mem_bytes > 0 {
            current_mem_bytes / (1024 * 1024)
        } else {
            0
        };

        let target_mem_mb = *mem_size as u64;
        let step_factor_decrease = EVICTION_PRESSURE_RATIO; // reduce 10% per step

        // Helper closure to write the settings for given mem_mb
        let write_settings = |mem_mb: u64| {
            let max_val_str = format!("{}M", mem_mb);
            let pressure_mb = if mem_mb > EVICTION_PRESSURE_MB {
                mem_mb - EVICTION_PRESSURE_MB
            } else {
                0
            };
            let high_val_str = format!("{}M", std::cmp::max(pressure_mb, (mem_mb as f64 * EVICTION_PRESSURE_RATIO) as u64));

            if let Err(err) = fs::write(&mem_limit_path, &max_val_str) {
                eprintln!("Failed to set memory limit: {}", err);
            }
            if let Err(err) = fs::write(&mem_high_path, &high_val_str) {
                eprintln!("Failed to set memory high: {}", err);
            }
        };

        // Adjust memory in steps if needed
        let mut current_mem = current_mem_mb as f64;
        let mut iter = 0;
        let iter_max = 5;
        let wait_time = 2;  // seconds

        if target_mem_mb < current_mem_mb {
            // Decreasing memory: gradually reduce
            while iter < iter_max {
                iter += 1;
                // Check the current usage and adjust the memory
                let current_use = Self::get_current_mem_usage(&cgroup_path);
                if current_use.is_none() {
                    eprintln!("Failed to get current memory usage; set the target directly");
                    break;
                }
                let current_use = current_use.unwrap();
                if current_use <= target_mem_mb as u64 {
                    break;
                } else if current_use <= current_mem as u64 {
                    current_mem *= step_factor_decrease;
                    if current_mem < target_mem_mb as f64 {
                        break;  // Now we are ready to set the target
                    }
                    write_settings(current_mem as u64);
                }
                tokio::time::sleep(tokio::time::Duration::from_secs(wait_time)).await;
            }
        }
        // After the last step, set the target directly
        write_settings(target_mem_mb);

        // Set swap limit (e.g., fixed 40 GB)
        let swap_size_mb: u64 = 40 * 1024; // 40 GB in MB
        // Read the current swap size and allocate it only if it is less than the target
        let current_swap_str = fs::read_to_string(&swap_limit_path)
            .unwrap_or_else(|_| "0".to_string())
            .trim()
            .to_string();
        let current_swap_bytes: u64 = current_swap_str.parse().unwrap_or(0);
        let current_swap_mb = if current_swap_bytes > 0 {
            current_swap_bytes / (1024 * 1024)
        } else {
            0
        };
        if current_swap_mb < swap_size_mb {
            if let Err(err) = fs::write(&swap_limit_path, format!("{}", swap_size_mb * 1024 * 1024)) {
                eprintln!("Failed to set swap limit: {}", err);
                return;
            }
        }

        println!(
            "{} | Set memory size to {} MB (40 GB swap) for container {} after {} iter",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
            target_mem_mb, container_name,
            iter
        );
    }

    pub async fn update_config(&mut self, new_config: UpdateConfig) {
        // Update the metadata
        let mut locked_curconfig = self.cur_config.write().await;
        println!("Updating config: {:?}", new_config);

        // Apply updated limits
        for (app_id, (mem, bw)) in &new_config.allocation_map {
            locked_curconfig.allocation_map.insert(*app_id, (*mem, *bw));   // insert or update
            // Update memory limit
            self.set_memory_size_via_cgroup(app_id, mem).await;
            // Update swap bandwidth
            self.update_swap_bandwidth(app_id, bw);
        }
    }

    fn run_container_exec(docker_name: &str, cmd: &str) {
        let exec_cmd = format!("docker exec {} {}", docker_name, cmd);
        println!("Running command: {}", exec_cmd);
        Command::new("sh")
            .arg(exec_cmd)
            .output()
            .expect("Failed to execute command");
    }

    async fn launch_container(&mut self, bench_cmd: &str, docker_name: &str, app_id: AppId, port: Option<Port>) {
        println!(
            "Running benchmark: {}, with docker name: {}",
            bench_cmd, docker_name
        );

        let mut child = Command::new("sh");
        child.arg(bench_cmd)
            .arg(docker_name);
        if port.is_some() {
            child.arg(port.unwrap().to_string());
        }
        let child_proc = child.spawn()
                                .expect("Failed to execute command");

        // Store the Child instance so we can kill it later
        // - Here, child is the script to start the docker
        self.child_processes.insert(app_id, child_proc);
        // Print the container name
        println!("Container created: {}", docker_name);
    }

    fn print_configs(&self) {
        println!("== Sampling Configs ==");
        println!("CACHE_SAMPLE_RATE: {}", CACHE_SAMPLE_RATE);
        println!("SAMPLE_DURATION_SECS: {}", SAMPLE_DURATION_SECS);
        println!("MEASURE_DURATION_SECS: {}", MEASURE_DURATION_SECS);
        println!("");
    }

    pub fn init<'a>(&'a mut self, config: InitConfig) -> impl Future<Output = ()> + 'a {
        let container_perf_stat = Arc::clone(&self.container_perf_stat);
        self.print_configs();
        async move {
            self.global_ip = config.global_ip;
            self.memory_ip = config.memory_ip;
            self.enable_mrc = config.enable_mrc.unwrap_or(true);
            self.update_mem_dev(config.memory_dev_name);
            // self.id_container_map = HashMap::new();
            for container_entry in &config.id_preload_map {
                // assert if all elements are present
                assert!(container_entry.id <= MAX_CONTAINER_NUM);
                assert!(!container_entry.docker_name.is_empty());
                // check if config has `launch` flag and it is set
                if container_entry.launch.is_some() && container_entry.launch.unwrap() {
                    assert!(!container_entry.script.is_empty());
                    self.launch_container(
                        &container_entry.script,
                        &container_entry.docker_name,
                        container_entry.id,
                        container_entry.port
                    )
                    .await;
                }
                // initialize per-container records
                self.id_container_map
                    .insert(container_entry.id, container_entry.docker_name.to_string());
                self.conatiner_blk_use_stat
                    .insert(container_entry.id, Arc::new(Mutex::new(BlkUseStat::new())));
                self.container_perf_stat
                    .lock()
                    .unwrap()
                    .insert(container_entry.id, PerfStat::new());
                // insert the container name to cgroup map
                if container_entry.cgroup_map.is_some() {
                    let cgroup_name = container_entry.cgroup_map.clone().unwrap();
                    self.container_cgroups_map
                        .write()
                        .unwrap()
                        .insert(container_entry.docker_name.to_string(), cgroup_name.clone());
                    println!("Predefined cgroup path for container {}: {}", container_entry.docker_name, cgroup_name);
                }
            }
            tokio::time::sleep(tokio::time::Duration::from_secs(10)).await;
            // if init script exists, run it
            if config.init_script.is_some() {
                let init_script_path = config.init_script.clone().unwrap();
                let output = Command::new("bash")
                    .arg(init_script_path)
                    .output()
                    .expect("Failed to execute command");
                println!("Init script output: {}", String::from_utf8_lossy(&output.stdout));
            }

            // spawn background perf monitoring
            for app_id in self.id_container_map.keys() {
                let app_id: u64 = *app_id; // Clone the app_id here
                let container_name = self.id_container_map.get(&app_id).unwrap().clone();
                let container_perf_stat = Arc::clone(&container_perf_stat);
                let cgroups_map: Arc<RwLock<HashMap<String, String>>> = self.container_cgroups_map.clone();

                std::thread::spawn(move || {
                    loop {
                        // let measurement = Self::monitor_multiple_pids_with_perf(&container_name);
                        let measurement = Self::monitor_perf_via_cgroups(&container_name, cgroups_map.clone());
                        if measurement.0 == 0 && measurement.1 == 0 {
                            // sleep for 1 seconds
                            std::thread::sleep(std::time::Duration::from_secs(1));
                            continue;
                        }
                        let mut locked_measurement = container_perf_stat.lock().unwrap();
                        let locked_measurement = locked_measurement.get_mut(&app_id).unwrap();
                        locked_measurement.update(measurement.0, measurement.1, measurement.2, measurement.3);
                    }
                });

                // PEBS based MRC computation
                let container_name = self.id_container_map.get(&app_id).unwrap().clone();
                let container_perf_stat = Arc::clone(&self.container_perf_stat);
                let cgroups_map: Arc<RwLock<HashMap<String, String>>> = self.container_cgroups_map.clone();
                let cur_config_ref = self.cur_config.clone();
                let latest_use = Arc::clone(&self.latest_usage);
                let client_id = self.id;
                // Spawn tokio blocking task
                let enable_mrc = self.enable_mrc.clone();
                let _report_usage_handle = tokio::task::spawn_blocking( move || {
                    // choose the last available core
                    let available_cores = core_affinity::get_core_ids().expect("Unable to get core IDs");
                    let core_id = available_cores.len() - 1;
                    let last_core = available_cores[core_id];
                    if core_affinity::set_for_current(last_core) {
                        println!("Thread pinned to core {}", core_id);
                    } else {
                        eprintln!("Failed to pin thread to core {}", core_id);
                    }

                    let runtime = tokio::runtime::Builder::new_current_thread()
                        .enable_io()   // Enable IO driver
                        .enable_time() // Enable time driver (for timers)
                        .build()
                        .unwrap();
                    runtime.block_on(async {
                        let mut est_exponent = Some(2.);
                        loop {
                            let mut est_coeff = Some((1.0, 1e-6, 1.0));
                            let measure_start_time = SystemTime::now();
                            if enable_mrc {
                                let mrc = Self::compute_mrc_via_aet(
                                    &container_name, cgroups_map.clone(),
                                    app_id, client_id,
                                    cur_config_ref.clone(),
                                    latest_use.clone(),
                                    &mut est_exponent, &mut est_coeff).await;
                                if mrc.is_some() {
                                    let mut locked_measurement = container_perf_stat.lock().unwrap();
                                    let locked_measurement = locked_measurement.get_mut(&app_id).unwrap();
                                    locked_measurement.update_mrc(mrc.unwrap());
                                    // drop(locked_measurement);
                                }
                            }
                            // check if the time taken is less than MEASURE_DURATION_SECS
                            let elapsed_time = measure_start_time.elapsed().unwrap().as_secs_f32();
                            if elapsed_time < MEASURE_DURATION_SECS {
                                // sleep for the remaining time
                                tokio::time::sleep(tokio::time::Duration::from_secs_f32(MEASURE_DURATION_SECS - elapsed_time)).await;
                            }
                        }
                    });
                });
            }
            // update flag
            if self.id_container_map.len() > 0 {
                self.running_benchmarks = true;
            }
        }
    }

    fn get_current_mem_usage (cgroup_path: &str) -> Option<MemoryMb> {
        let cgroup_mem_path = format!("{}/memory.current", cgroup_path);

        if !Path::new(&cgroup_mem_path).exists() {
            return None;
        }

        let mem_usage_str = fs::read_to_string(cgroup_mem_path)
            .expect("Failed to read memory usage");

        let mem_usage_bytes: u64 = mem_usage_str.trim().parse()
            .expect("Failed to parse memory usage as u64");

        // Convert memory usage from bytes to MiB
        Some((mem_usage_bytes as f64 / 1024.0 / 1024.0) as MemoryMb)
    }

    fn get_anon_mem_usage(cgroup_path: &str) -> Option<MemoryMb> {
        let cgroup_mem_stat = format!("{}/memory.stat", cgroup_path);
        let anon_mem_bytes = match fs::read_to_string(&cgroup_mem_stat) {
            Ok(content) => {
                // Find the line starting with "anon "
                if let Some(anon_line) = content.lines().find(|line| line.starts_with("anon ")) {
                    let parts: Vec<&str> = anon_line.split_whitespace().collect();
                    if parts.len() == 2 {
                        match parts[1].parse::<u64>() {
                            Ok(value) => value,
                            Err(_) => {
                                eprintln!("Failed to parse anon memory for {}", cgroup_path);
                                return None;
                            }
                        }
                    } else {
                        eprintln!("Invalid format for anon memory line in memory.stat for {}", cgroup_path);
                        return None;
                    }
                } else {
                    eprintln!("Anon memory not found in memory.stat for {}", cgroup_path);
                    return None;
                }
            },
            Err(_) => {
                eprintln!("Failed to read memory.stat for {}", cgroup_path);
                return None;
            }
        };

        // Swapped out pages
        let cgroup_swap_stat = format!("{}/memory.swap.current", cgroup_path);
        let swap_bytes = match fs::read_to_string(&cgroup_swap_stat) {
            Ok(content) => {
                match content.trim().parse::<u64>() {
                    Ok(value) => value,
                    Err(_) => {
                        eprintln!("Failed to parse swap memory for {}", cgroup_path);
                        return None;
                    }
                }
            },
            Err(_) => {
                eprintln!("Failed to read memory.swap.current for {}", cgroup_path);
                return None;
            }
        };
        Some(((swap_bytes + anon_mem_bytes) as f64 / 1024. / 1024.) as MemoryMb)
    }

    async fn get_mem_usages_from_cgroup(&self, cont_names: &HashMap<AppId, String>) -> HashMap<AppId, (MemoryMb, MemoryMb)> {
        let mut mem_usages = HashMap::new();

        for (app_id, cont_name) in cont_names.iter() {
            let cgroup_path = Self::get_cgroup_path(cont_name, self.container_cgroups_map.clone());
            if cgroup_path.is_none() {
                eprintln!("Failed to get memory usage: cgroup path for container {}", cont_name);
                continue;
            }
            let cgroup_path = cgroup_path.unwrap();
            let mem_usage_mib = Self::get_current_mem_usage(&cgroup_path);
            let mem_usage_mib = match mem_usage_mib {
                Some(value) => value,
                None => {
                    eprintln!("Failed to get memory usage for container {}", cont_name);
                    continue;
                }
            };

            // Read memory.stat and extract the anon memory usage
            let anon_mem_mib = Self::get_anon_mem_usage(&cgroup_path);
            let anon_mem_mib = match anon_mem_mib {
                Some(value) => value,
                None => {
                    // error printed inside get_anon_mem_usage
                    continue;
                }
            };

            // Insert the total and anon memory usage into the hashmap
            mem_usages.insert(*app_id, (mem_usage_mib as MemoryMb, anon_mem_mib as MemoryMb));
        }
        mem_usages
    }

    fn get_blk_bandwidth(&mut self, app_id: &AppId, cont_name: &str) -> BandwidthMbps {
        let cgroup_path = Self::get_cgroup_path(cont_name, self.container_cgroups_map.clone());
        if cgroup_path.is_none() {
            eprintln!("Failed to get blk bw :: reason - cgroup path for container {}", cont_name);
            return 0;
        }
        let cgroup_path = format!("{}/io.stat", cgroup_path.unwrap());
        let contents = fs::read_to_string(cgroup_path).expect("Failed to read blkio statistics");

        let (mut read_bytes, mut write_bytes) = (0u64, 0u64);
        for line in contents.lines() {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 3 && parts[0] == self.memory_dev_maj_min {
                for part in &parts[1..] {
                    let split: Vec<&str> = part.split('=').collect();
                    if split.len() == 2 {
                        let action = split[0];
                        let value = split[1].parse::<u64>().expect("Failed to parse bytes");
                        match action {
                            "rbytes" => read_bytes = value,
                            "wbytes" => write_bytes = value,
                            _ => (),
                        }
                    }
                }
            }
        }

        let current_time = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("Time went backwards")
            .as_millis(); // Use milliseconds for higher precision

        let mut locked_stat = self
                .conatiner_blk_use_stat
                .get(app_id)
                .unwrap()
                .lock()
                .unwrap();

        let prev_time = locked_stat.prev_time;
        if current_time <= prev_time {
            println!("Time went backwards or same timestamp, returning 0");
            return 0;
        }

        let elapsed_time = (current_time - prev_time) as f64 / 1000.0; // Convert to seconds

        let read_mbps = ((read_bytes - locked_stat.prev_read_bytes) * 8) as f64
            / (1024.0 * 1024.0 * elapsed_time);
        let write_mbps = ((write_bytes - locked_stat.prev_write_bytes) * 8) as f64
            / (1024.0 * 1024.0 * elapsed_time);
        // Update with the current value
        locked_stat.prev_read_bytes = read_bytes;
        locked_stat.prev_write_bytes = write_bytes;
        locked_stat.prev_time = current_time;
        read_mbps.max(write_mbps) as BandwidthMbps
    }

    pub fn monitor_perf_via_cgroups(cont_name: &str, cont_cgroup_map: Arc<RwLock<HashMap<String, String>>>) -> (u64, u64, u64, u64) {
        let cgroup_path = Self::get_cgroup_path(cont_name, cont_cgroup_map);
        if cgroup_path.is_none() {
            eprintln!(
                "Failed to monitor perf :: cgroup path for container {} not found. Please ensure the container is running and cgroup settings are configured correctly.",
                cont_name
            );
            return (0, 0, 0, 0);
        }

        let cgroup_path = cgroup_path.unwrap();
        let cgroup_name = cgroup_path.trim_start_matches("/sys/fs/cgroup");

        // Construct and run the perf command
        let mut command = Command::new("perf");
        command
            .arg("stat")
            .arg("-e")
            // Mix of events: some properly show :u suffix, others don't but still filter correctly
            .arg("MEM_UOPS_RETIRED.ALL_LOADS:u,MEM_LOAD_UOPS_RETIRED.L3_MISS:u,cache-references:u,major-faults:u")
            // Previous alternatives tested:
            // .arg("MEM_LOAD_RETIRED.L3_MISS:u,cache-references:u,major-faults:u,mem_inst_retired.all_loads:u")// Added mem operations (v2)
            .arg("-G")
            .arg(cgroup_name)
            .arg("-a")
            .arg("sleep")
            .arg("1");

        let perf_output = match command.output() {
            Ok(output) => output,
            Err(e) => {
                eprintln!("Failed to execute perf command: {}", e);
                return (0, 0, 0, 0);
            }
        };

        let output_str = String::from_utf8(perf_output.stderr).unwrap_or_default();
        if output_str.is_empty() {
            eprintln!("Perf command returned empty output for container {}", cont_name);
            return (0, 0, 0, 0);
        }

        // Parse the output using regex - handles both reliable :u events and LLC events
        let misses_regex = Regex::new(r"(\d[\d,]*)\s+MEM_LOAD_UOPS_RETIRED.L3_MISS:u").unwrap();
        let references_regex = Regex::new(r"(\d[\d,]*)\s+cache-references:u").unwrap();
        let faults_regex = Regex::new(r"(\d[\d,]*)\s+major-faults:u").unwrap();
        let mem_loads_regex = Regex::new(r"(\d[\d,]*)\s+MEM_UOPS_RETIRED.ALL_LOADS:u").unwrap();

        let cache_misses = misses_regex
            .captures(&output_str)
            .and_then(|cap| cap.get(1))
            .and_then(|match_| match_.as_str().replace(",", "").parse::<u64>().ok());

        let cache_references = references_regex
            .captures(&output_str)
            .and_then(|cap| cap.get(1))
            .and_then(|match_| match_.as_str().replace(",", "").parse::<u64>().ok());

        let major_faults = faults_regex
            .captures(&output_str)
            .and_then(|cap| cap.get(1))
            .and_then(|match_| match_.as_str().replace(",", "").parse::<u64>().ok());

        let mem_loads = mem_loads_regex
            .captures(&output_str)
            .and_then(|cap| cap.get(1))
            .and_then(|match_| match_.as_str().replace(",", "").parse::<u64>().ok());

        let mem_operations: u64 = match mem_loads {
            Some(loads) => loads,
            _ => 0,
        };

        if cache_misses.is_none() {
            eprintln!("Failed to parse cache-misses:u from perf output: {}", output_str);
        }
        if cache_references.is_none() {
            eprintln!("Failed to parse cache-references:u from perf output: {}", output_str);
        }
        if major_faults.is_none() {
            eprintln!("Failed to parse major-faults:u from perf output: {}", output_str);
        }

        match (cache_misses, cache_references, major_faults) {
            (Some(misses), Some(references), Some(major_faults)) => (misses, references, major_faults, mem_operations),
            _ => (0, 0, 0, mem_operations),
        }
    }

    async fn collect_perf_pebs(
        cont_name: &str,
        cont_cgroup_map: Arc<RwLock<HashMap<String, String>>>,
    ) -> Option<Vec<u64>> {
        // Retrieve the cgroup path for the container
        let cgroup_path = Self::get_cgroup_path(cont_name, cont_cgroup_map.clone());
        if cgroup_path.is_none() {
            eprintln!(
                "Failed to compute MRC: cgroup path for container '{}' not found. \
                Please ensure the container is running and cgroup settings are configured correctly.",
                cont_name
            );
            return None;
        }

        let cgroup_path = cgroup_path.unwrap();

        // Trim the cgroup path to get the relative cgroup name
        // Assuming cgroup v2 mounted at /sys/fs/cgroup
        let cgroup_name = cgroup_path.trim_start_matches("/sys/fs/cgroup/");
        // Sanitize cont_name to make it safe for filenames
        let sanitized_cont_name = cont_name.replace("/", "_").replace(" ", "_");

        // Generate filenames using cont_name (and timestamp if needed)
        let perf_data_file = format!("perf_{}.data", sanitized_cont_name);
        // Construct the perf record command with a unique output file
        let perf_record_cmd = format!(
            "sudo perf record -a -m 1024 -e MEM_LOAD_UOPS_RETIRED.L3_MISS:uP -d -c {} --cgroup {} -o {} -- sleep {:.2}",
            CACHE_SAMPLE_RATE, cgroup_name, perf_data_file, SAMPLE_DURATION_SECS
        );

        // Execute the perf record command
        let status = tokio::process::Command::new("sh")
            .arg("-c")
            .arg(&perf_record_cmd)
            .status()
            .await;

        match status {
            Ok(status) if status.success() => {
                // perf record succeeded
            }
            Ok(status) => {
                eprintln!("perf record command failed with exit code: {}", status);
                return None;
            }
            Err(e) => {
                eprintln!("Failed to execute perf record command: {}", e);
                return None;
            }
        }

        // Run perf script to process the recorded data
        let perf_script_cmd = format!("sudo perf script -i {}", perf_data_file);

        println!("perf_script_cmd for {}", sanitized_cont_name);
        let output = tokio::process::Command::new("sh")
            .arg("-c")
            .arg(perf_script_cmd)
            .output()
            .await;

        let output = match output {
            Ok(output) => output,
            Err(e) => {
                eprintln!("Failed to execute perf script command: {}", e);
                return None;
            }
        };

        if !output.status.success() {
            eprintln!(
                "perf script command failed with exit code: {}",
                output.status
            );
            eprintln!(
                "perf script stderr: {}",
                String::from_utf8_lossy(&output.stderr)
            );
            return None;
        }

        // Parse the output to extract memory addresses
        let output_str = String::from_utf8_lossy(&output.stdout);

        let mut memory_accesses = Vec::new();

        // Regular expression to match both load and store events
        // === Example outputs and regex that we tested ===
        // redis-server 2466569 [035] 432515.504177:      10000 cpu/mem-stores/pp:      58b72328b356 je_pa_shard_stats_merge+0x226 (/usr/local/bin/redis-server)
        // let re = Regex::new(r":\s+\d+\s+cpu/mem-(loads|stores)/pp:\s+([0-9a-fA-F]+)").unwrap();

        // memtouch  117264 19751.351774:        100 MEM_LOAD_UOPS_RETIRED.L3_MISS:uP:     75a4abe405c0      1e05080022 |OP LOAD|LVL N/A|SNP N/A|TLB N/A|LCK N/A|BLK  N/A     5bb98b3d329e main+0xb5 (/users/sslee_cs/memtouch)
        let re = Regex::new(r":\s*\d+\s*MEM_LOAD_UOPS_RETIRED.L3_MISS:uP:\s*([0-9a-fA-F]+)").unwrap();

        // JournalFlusher   27530 [035] 722230.631124:        100 cache-misses:u:  ffffffff9bf5045b task_mm_cid_work+0xbb ([kernel.kallsyms])
        // let re = Regex::new(r":\s*\d+\s*cache-misses:u:\s*([0-9a-fA-F]+)").unwrap();

        for line in output_str.lines() {
            if let Some(cap) = re.captures(line) {
                let addr_str = &cap[1];
                if let Ok(addr) = u64::from_str_radix(addr_str, 16) {
                    // Normalize the address to cache line address
                    let cache_line_addr = addr / PAGE_SIZE_BYTES;
                    memory_accesses.push(cache_line_addr);
                }
            }
        }
        println!("Memory access for container {}: {}", cont_name, memory_accesses.len());
        Some(memory_accesses)
    }

    fn compute_beta_coeff(coeff_value: &mut (f64, f64, f64), data_x: &Vec<(f64, f64)>, exponent: &mut Option<f64>) {
        let max_coeff = data_x[0].1 * 100.;
        if let Some(exponent) = exponent {
            if *exponent > 1. {
                let pages_in_gb = coeff_value.2;
                coeff_value.0 = data_x[0].1 * pages_in_gb * (1. - *exponent) / ((1. + 1. / pages_in_gb).powf(1. - *exponent) - 1.);
                if coeff_value.0 < 0. {
                    eprintln!("Invalid beta coefficient: {} | args - data: {}, exponent: {}, coeff: {:?}",
                        coeff_value.0, data_x[0].1, exponent, coeff_value);
                }
                if coeff_value.0 >= max_coeff {
                    coeff_value.0 = max_coeff;
                }
            }
        }
    }

    fn precompute_delta_coeff( coeff_value: &mut (f64, f64, f64), sample_rate: f64) {
        coeff_value.1 = 0.1 * sample_rate
    }

    // While it was originally for testing AET model from DCAPS paper (EuroSys '18),
    // due to the limitations of AET model in remote memory scale,
    // the current implementation is our own estimation scheme based on regression.
    pub async fn compute_mrc_via_aet(
        cont_name: &str,
        cont_cgroup_map: Arc<RwLock<HashMap<String, String>>>,
        app_id: AppId, client_id: u64,
        cur_config: Arc<tokio::sync::RwLock<UpdateConfig>>,
        latest_use: Arc<tokio::sync::Mutex<Vec<(UsageMap, AppStats)>>>,
        exponent: &mut Option<f64>,
        coeff: &mut Option<(f64, f64, f64)>,
    ) -> Option<Vec<(u64, f64)>> {
        // Collect memory accesses using PEBS
        let memory_accesses = Self::collect_perf_pebs(cont_name, cont_cgroup_map.clone()).await;
        if memory_accesses.is_none() {
            eprintln!(
                "Failed to collect memory accesses via PEBS for container {}",
                cont_name
            );
            return None;
        }
        let mut memory_accesses = memory_accesses.unwrap();
        // Filter out non-user accesses in place
        memory_accesses.retain(|&addr| addr < 0xFFFF800000000000 / PAGE_SIZE_BYTES);
        if memory_accesses.is_empty() {
            eprintln!("No memory accesses were recorded.");
            return None;
        }
        // let total_sampled_accesses = memory_accesses.len() as u64;
        // Get the current allocation map
        let cur_config = cur_config.read().await;
        let cache_size_mb = cur_config
            .allocation_map
            .get(&app_id);
        if cache_size_mb.is_none() {
            eprintln!("Failed to get cache size for app_id {} in {:?}", app_id, cur_config.allocation_map);
            return None;
        }
        let cache_size_mb = cache_size_mb.unwrap().0;
        let latest_use = latest_use.lock().await;
        if latest_use.len() < LEN_PERF_HISTORY {
            eprintln!("Not enough data to compute MRC: {}/{}", latest_use.len(), LEN_PERF_HISTORY);
            return None;
        }
        // Iterate over the vector and sum bio mbps and cache miss mbps, individually
        let mut sum_bio_mbps = 0;
        let mut sum_cache_miss_mbps = 0;
        let mut avg_cache_ref_mbps = 0;
        let mut avg_bio_mbps: f64 = 0.;
        let mut avg_faults_per_sec: f64 = 0.;
        let mut avg_faults_ratio: f64 = 0.;
        let mut len_use = 0;
        let mut anon_mem_pages = 0;
        for use_record in latest_use.iter() {
            let record = use_record.0.map.get(&client_id);
            if record.is_none() {
                eprintln!("Failed to get bio mbps for client_id {}", client_id);
                return None;
            }
            let record = record.unwrap().get(&app_id);
            if record.is_none() {
                eprintln!("Failed to get bio mbps for app_id {}", app_id);
                return None;
            }
            sum_bio_mbps += record.unwrap().bw_mbps;
            sum_cache_miss_mbps += record.unwrap().cache_mbps;
            avg_cache_ref_mbps += record.unwrap().access_rate_ops_sec;
            let num_cache_acc = record.unwrap().cache_mbps * MBPS_TO_BYTES_PER_SEC / CACHE_LINE_SIZE_BYTES;
            avg_faults_per_sec += record.unwrap().hit_rate_percent * num_cache_acc as f64;
            avg_faults_ratio += record.unwrap().hit_rate_percent;
            len_use += 1;
            let record = use_record.1.anon_memory_mb.get(&app_id);
            if record.is_none() {
                eprintln!("Failed to get anon memory for app_id {}", app_id);
                return None;
            }
            let anon_mem_mb = record.unwrap();
            anon_mem_pages += anon_mem_mb * 1024 * 1024 / PAGE_SIZE_BYTES;
        }
        let avg_cache_ref_mbps = avg_cache_ref_mbps as f64 / len_use as f64;    // l3 cache references
        avg_bio_mbps = sum_bio_mbps as f64 / len_use as f64;
        avg_faults_per_sec /= len_use as f64;
        avg_faults_ratio /= len_use as f64;
        anon_mem_pages /= len_use as u64;
        let miss_ratio = sum_bio_mbps as f64 / sum_cache_miss_mbps as f64;
        drop(latest_use);

        // Collect observed frequencies
        let base_sample_rate = CACHE_SAMPLE_RATE;
        // Dynamically generate sampling rates
        let sampling_rates: Vec<u64> = (0..1).map(|i| base_sample_rate * 2f64.powi(i) as u64).collect();
        // let mut user_accesses: usize = 0;

        // Sub sampling rates
        let mut frequency_buckets_by_sampling_rate: HashMap<u64, HashMap<u64, u64>> = HashMap::new();

        for &sampling_rate in &sampling_rates {
            assert!(sampling_rate > 0);
            let subsample: Vec<u64> = if sampling_rate != base_sample_rate {
                memory_accesses
                    .iter()
                    .filter(|_| rand::thread_rng().gen_bool(base_sample_rate as f64 / sampling_rate as f64))
                    .cloned()
                    .collect()
            } else {
                memory_accesses.clone()
            };

            // Construct frequency buckets for the subsample
            let mut address_frequencies: HashMap<u64, u64> = HashMap::new();
            for &addr in &subsample {
                *address_frequencies.entry(addr).or_insert(0) += 1;
            }

            // Group addresses into frequency buckets (frequency -> count of addresses)
            let mut frequency_buckets: HashMap<u64, u64> = HashMap::new();
            for &freq in address_frequencies.values() {
                *frequency_buckets.entry(freq).or_insert(0) += 1;
            }

            frequency_buckets_by_sampling_rate.insert(sampling_rate, frequency_buckets);
        }
        let frequency_buckets: HashMap<u64, u64> = frequency_buckets_by_sampling_rate[&base_sample_rate].clone();
        let avg_freq: f64 = frequency_buckets.iter().map(|(k, v)| *k as f64 * *v as f64).sum::<f64>() / frequency_buckets.len().max(1) as f64;

        // Miss ratio from sampled data
        let pages_detected = frequency_buckets.values().sum::<u64>() as f64;
        let pages_accessed = pages_detected as f64 * CACHE_SAMPLE_RATE as f64;
        let pages_per_sec = pages_accessed / SAMPLE_DURATION_SECS as f64;
        let pages_fetched = avg_bio_mbps as f64 * 1024. * 1024. / 8. / PAGE_SIZE_BYTES as f64;
        let miss_ratio_from_sampled = pages_fetched / pages_per_sec;
        println!("{} | Avg:: bio mbps: {}, cache mbps: {} | {} pages fetched, {} pages detected (anon: {}), missed page ratio: {} | Target MissRatio: {} / {} | Accessed: u: {}",
                 chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
                 sum_bio_mbps as f64 / len_use as f64, sum_cache_miss_mbps as f64 / len_use as f64,
                 pages_fetched, pages_detected, anon_mem_pages, pages_fetched / pages_detected as f64,
                 miss_ratio, miss_ratio_from_sampled,
                 memory_accesses.len());
        let cache_access_bytes = sum_cache_miss_mbps as f64 / len_use as f64 * MBPS_TO_BYTES_PER_SEC as f64; // into B/s
        let cache_access_per_evicted_page = cache_access_bytes / CACHE_LINE_SIZE_BYTES as f64 / pages_per_sec; // into accesses per page
        let cache_access_sampled = (memory_accesses.len().max(1) * CACHE_LINE_SIZE_BYTES as usize) as f64 / SAMPLE_DURATION_SECS as f64;
        let est_sample_ratio = cache_access_bytes as f64 / cache_access_sampled;
        let est_sample_ratio = est_sample_ratio.max(CACHE_SAMPLE_RATE as f64);  // atleast the base sample rate
        let cache_access_bytes = cache_access_sampled * est_sample_ratio;   // compute back the access bytes
        // amount of cache hit within the hardware cache (L3)
        let cache_hit_within_hw_cache = (avg_cache_ref_mbps - (sum_cache_miss_mbps as f64 / len_use as f64)) / avg_cache_ref_mbps;
        let cache_hit_within_hw_cache = cache_hit_within_hw_cache.max(1e-6).min(1.0 - 1e-6);

        println!("{} | Cache access per page:: bw-based: {}, pebs-based: {} | sample ratio (est): {} | faults: {}, miss ratio: {}, ratio-to-fet: {}",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
            cache_access_per_evicted_page, avg_freq,
            est_sample_ratio,
            avg_faults_per_sec, avg_faults_ratio,   // #faults / #l3miss
            pages_fetched/avg_faults_per_sec.max(1.0)
        );

        // memory_accesses is page-aligned but each sample still represents cache miss
        let cache_access = cache_access_bytes / CACHE_LINE_SIZE_BYTES as f64;
        let miss_ratio = avg_faults_ratio.min(pages_fetched / cache_access).min(1.0) / OS_PREFETCH_FACTOR;
        if miss_ratio >= 1. {
            // Sometimes, due to the delayed block IO, miss ratio can be greater than 1
            eprintln!("Miss ratio is still greater than 1: {}; consider increasing LEN_PERF_HISTORY, currently={}",
                miss_ratio, LEN_PERF_HISTORY);
            return None;
        }

        // Define cache sizes in MB and compute corresponding cache lines
        let cache_sizes_mb: Vec<u64> = (CACHE_GRANULARITY_MB..=MAX_CACHE_IN_MB)
            .step_by(CACHE_GRANULARITY_MB as usize)
            .collect();

        // Sort frequencies in descending order
        let mut sorted_frequencies: Vec<u64> = frequency_buckets.keys().cloned().collect();
        sorted_frequencies.sort_unstable_by(|a, b| b.cmp(a));
        println!("Freq buckets: {:?}", &frequency_buckets);

        let cache_size_in_pages = cache_size_mb * 1024 * 1024 / PAGE_SIZE_BYTES;
        let mut access_sampled = memory_accesses.len() as f64 * est_sample_ratio;

        // Anon page based estimation
        let hit_ratio = (1. - miss_ratio).max(1e-8);
        let mut exp_coeff = None;
        if cache_size_in_pages <= pages_detected as u64 {
            eprintln!("Cache size is already filled: {} <= {}", cache_size_in_pages, pages_detected);
        } else {
            // Create a list of data points from sorted_frequencies
            let mut data_x = Vec::new();
            let mut page_idx = 1;
            let mut cumulated_accesses = 0.0;
            if pages_detected > 0. && pages_detected < 10. {
                let access_at_zero_page = access_sampled / (1. - cache_hit_within_hw_cache) - access_sampled;
                cumulated_accesses += access_at_zero_page;
                access_sampled += access_at_zero_page;
                data_x.push((page_idx as f64, cumulated_accesses));
                page_idx += 1;
                println!("{} | AppId: {}, adding zero page access: {}/{}",
                    chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
                    app_id, access_at_zero_page, access_sampled);
            }
            for &k in &sorted_frequencies {
                // k: number of accesses (freq) w/o sample_rate
                let accesses = k as f64 * est_sample_ratio;
                for _ in 0..*frequency_buckets.get(&k).unwrap() {
                    cumulated_accesses += accesses;
                    data_x.push((page_idx as f64, cumulated_accesses));
                    page_idx += 1;
                }
            }
            // If data is not enough, add one with sampling rate (lowest freq)
            // we need at least 2 data points considering coefficients
            if data_x.len() < 10 {
                cumulated_accesses += est_sample_ratio;
                access_sampled += est_sample_ratio;
                data_x.push((page_idx as f64, cumulated_accesses));
            }
            // Set beta to start from data_x's first entry
            if let Some(coeff_value) = coeff.as_mut() {
                Self::compute_beta_coeff(coeff_value, &data_x, exponent);
                Self::precompute_delta_coeff(coeff_value, est_sample_ratio);
            }

            exp_coeff = Self::compute_exponent(
                &app_id,
                access_sampled, est_sample_ratio as f64, hit_ratio,
                pages_detected as f64,
                cache_size_in_pages as f64,
                anon_mem_pages as f64,
                exponent, coeff, &data_x

            );

            if exp_coeff.is_none() {
                eprintln!("Failed to compute exponent for cache size: {} MB", cache_size_mb);
                return None;
            } else {
                let (exponent, coeff) = exp_coeff.unwrap();
                println!("{} | Exponent for cache size: {} MB: {:.6} w/ coeff {:?} -> MR: {}",
                    chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
                    cache_size_mb, exponent, coeff, Self::compute_miss_ratio(
                        access_sampled, est_sample_ratio as f64, pages_detected,
                        cache_size_in_pages as f64, anon_mem_pages as f64,
                        cache_size_in_pages as f64, exponent, coeff
                    ));
            }
        }

        // Reconstruct the MRC with adjusted counts
        let mut mrc = Vec::new();
        for &cache_size_mb in &cache_sizes_mb {
            let cache_size_lines = cache_size_mb * 1024 * 1024 / PAGE_SIZE_BYTES;
            let cache_capacity_addresses = cache_size_lines as f64;

            let miss_ratio = if exp_coeff.is_some() && cache_capacity_addresses > pages_detected as f64 && cache_capacity_addresses <= anon_mem_pages as f64 {
                let (exponent, coeff) = exp_coeff.unwrap();
                Self::compute_miss_ratio(
                    access_sampled, est_sample_ratio as f64, pages_detected,
                    cache_capacity_addresses, anon_mem_pages as f64,
                    cache_capacity_addresses as f64, exponent, coeff
                )
            } else {
                // Likely due to the cache size is large enough to cover all the observed pages
                0.
            };

            // Add to the mrc
            mrc.push((cache_size_mb, miss_ratio.clamp(0.0, 1.0)));
        }

        // Ensure that at the largest cache size, miss ratio is zero
        if let Some((_, last_miss_ratio)) = mrc.last_mut() {
            *last_miss_ratio = 0.0;
        }

        // Print the MRC for debugging
        println!("{} | MRC: {:?}",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
            &mrc[..mrc.len().min(64)]);

        Some(mrc)
    }

    fn compute_compulsory_miss_point(
        exponent: f64,
        coeff: (f64, f64, f64),
        pages_detected: f64,
        pages_until_cache: f64,
        pages_until_anon: f64,
        _sample_rate: f64,
    ) -> f64 {
        let max_iter: usize = 2500;
        let tol: f64 = 1e-6;
        let epsilon: f64 = 1e-1;
        let (beta, gamma, n_pages_in_gb) = coeff;

        // Precompute constants
        let term = beta * n_pages_in_gb / (1.0 - exponent);
        let anon_u = 1.0 + pages_until_anon / n_pages_in_gb;
        let anon_accesses = term * anon_u.powf(1.0 - exponent) + gamma * pages_until_anon;

        // Initial guess for mid
        let mut mid = pages_until_cache - epsilon;

        for _ in 0..max_iter {
            let u_mid = 1.0 + mid / n_pages_in_gb;
            let mid_accesses = term * u_mid.powf(1.0 - exponent) + gamma * mid;

            // Derivatives of mid_accesses
            let d_mid_accesses = beta * u_mid.powf(-exponent) + gamma;
            let d2_mid_accesses = (-exponent) * u_mid.powf(-exponent - 1.0) * (1.0 / n_pages_in_gb);

            // Compute budget and its derivatives
            let budget = (pages_until_cache - mid) * mid_accesses;
            let d_budget: f64 = -mid_accesses + (pages_until_cache - mid) * d_mid_accesses;
            let d2_budget = -2.0 * d_mid_accesses + (pages_until_cache - mid) * d2_mid_accesses;

            // Compute f(mid) and its derivatives
            let f = anon_accesses - mid_accesses - budget;
            let f_prime = -d_mid_accesses - d_budget;
            let f_double_prime = -d2_mid_accesses - d2_budget;

            // Compute denominator
            let denominator = f_prime * f_prime + f * f_double_prime;

            if denominator.abs() < tol {
                break; // Avoid division by zero
            }

            // Newton-Raphson update for minimizing f(mid)^2
            let delta = (f * f_prime) / denominator;
            mid -= epsilon * delta;

            // Ensure mid stays within bounds
            if mid < pages_detected {
                mid = pages_detected;
            } else if mid > pages_until_cache {
                mid = pages_until_cache;
            }

            // Check for convergence
            if delta.abs() < tol {
                break;
            }
        }
        mid
    }

    // Function to compute accesses
    fn compute_accesses(
        pages: f64,
        exponent: f64,
        coeff: (f64, f64, f64),
        sample_rate: f64,
        pages_detected: f64,
        pages_until_cache: f64,
        pages_until_anon: f64,
        _access_sampled: f64,
        tolerance: f64,
        hit_only: bool) -> f64 {

        // Compute the current hit rate
        let (beta, gamma, pages_in_gb) = coeff;
        if (exponent - 1.0).abs() < tolerance {
            let term = (pages / pages_in_gb as f64).ln_1p();
            if hit_only {
                let compulsory_miss_point = Self::compute_compulsory_miss_point(
                    exponent, coeff, pages_detected, pages_until_cache, pages_until_anon, sample_rate);
                if pages <= compulsory_miss_point {
                    (beta * pages_in_gb as f64 * term).max(0.) + gamma * pages
                } else {
                    // now hit does not account any pages beyond compulsory_miss_point
                    let term: f64 = (compulsory_miss_point / pages_in_gb as f64).ln_1p();
                    (beta * pages_in_gb as f64 * term - compulsory_miss_point).max(0.) + gamma * compulsory_miss_point
                }
            } else {
                beta * pages_in_gb as f64 * term + gamma * pages
            }
        } else {
            let term = (1.0 + pages / pages_in_gb as f64).powf(1.0 - exponent) - 1.0;
            if hit_only {
                let compulsory_miss_point = Self::compute_compulsory_miss_point(
                    exponent, coeff, pages_detected, pages_until_cache, pages_until_anon, sample_rate);
                if pages <= compulsory_miss_point {
                    // access_sampled
                    (beta / (1.0 - exponent) * pages_in_gb as f64 * term).max(0.) + gamma * pages
                } else {
                    // now hit does not account any pages beyond compulsory_miss_point
                    let term = (1.0 + compulsory_miss_point / pages_in_gb as f64).powf(1.0 - exponent) - 1.0;
                    // access_sampled
                    (beta / (1.0 - exponent) * pages_in_gb as f64 * term).max(0.) + gamma * compulsory_miss_point                }
            } else {
                // access_sampled
                beta / (1.0 - exponent) * pages_in_gb as f64 * term + gamma * pages
            }
        }
    }

    fn compute_exponent(
        app_id: &AppId,
        access_sampled: f64,
        sample_rate: f64,
        hit_rate: f64,
        pages_detected: f64,
        pages_until_cache: f64,
        pages_until_anon: f64,
        prev_exponent: &mut Option<f64>,
        prev_coeff: &mut Option<(f64, f64, f64)>,
        observed_data: &Vec::<(f64, f64)>,
    ) -> Option<(f64, (f64, f64, f64))> {
        let tolerance = 1e-6;
        let target_hit_rate = hit_rate.max(tolerance).min(1. - tolerance);  // avoid division by zero
        let tolerance_mr = 0.03;    // 3%
        let tolerance_observed = 0.05;  // 5%
        let max_iterations = 10000; // 100000;
        let learning_rate = 0.5;
        let learning_rate_shape = 0.1;
        let leraning_rate_sensitive = 0.01;
        let mut dx = 1e-4; // Small value for numerical derivative
        let mut exponent = prev_exponent.unwrap_or(2.); // Initial guess for exponent
        let mut coeff = prev_coeff.unwrap_or((1.0, tolerance, 1.0));
        let start_time = Instant::now();
        let mut last_difference = f64::MAX;
        let mut last_hit_rate = 0.0;
        let mut last_derivative = 0.;
        let clip_hit_rate = 100.;

        println!("{} | App: {}, Compute_exponent args: access_sampled={}, sample_rate={}, hit_rate={}, pages_detected={}, pages_until_cache={}, pages_until_anon={}, exponent={}, coeff={:?}",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S"), app_id,
            access_sampled, sample_rate, target_hit_rate, pages_detected, pages_until_cache, pages_until_anon,
            exponent, coeff);
        let mut hit_rate_history = vec![];
        let mut diff_history = vec![];
        let history_len = 16;
        let mut diff_records = Vec::new();

        // Shape optimization
        for iter in 0..max_iterations {
            // Avoid exponent = 1 to prevent division by zero
            if (exponent - 1.0 as f64).abs() < tolerance {
                exponent += tolerance;
                continue;
            }

            // Compute the current hit rate
            let accesses_est = Self::compute_accesses(
                pages_until_cache, exponent, coeff, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, SEPRATE_COMPULSORY);
            let accesses_tot = Self::compute_accesses(
                pages_until_anon, exponent, coeff, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, false);

            let computed_hit_rate = (accesses_est / accesses_tot).max(0.0).min(1.0);
            let mut difference = (((1. - computed_hit_rate) - (1. - target_hit_rate))/(1. - target_hit_rate)).powi(2);
            let mut diff_exponent = difference;
            diff_records.clear();

            for &(page, observed_access_count) in observed_data.iter() {
                let estimated_access_count = Self::compute_accesses(
                    page, exponent, coeff, sample_rate,
                    pages_detected, pages_until_cache, pages_until_anon,
                    access_sampled, tolerance, false
                );  // assuming that the observed data is about sizes before the compulsory miss point

                let relative_error = if observed_access_count != 0.0 {
                    (estimated_access_count - observed_access_count) / observed_access_count
                } else {
                    0.0
                };
                diff_records.push(
                    ((estimated_access_count, observed_access_count, relative_error),
                    relative_error * relative_error));
                diff_exponent += (relative_error * relative_error) / observed_data.len() as f64;
            }

            // Check for convergence
            // tolerance -> difference from (1) the target hit rate + (2) observed data
            if (diff_exponent - difference).abs() < tolerance_observed {
                println!(
                    "Shape || Converged in {} us ({} iterations): exponent = {}, coeff = {:?}, difference = {}",
                    start_time.elapsed().as_micros(),
                    iter,
                    exponent,
                    coeff,
                    diff_exponent
                );
                break;
            }

            // for iter in 0..max_iterations {
            // Compute numerical derivative
            let exponent_plus_dx = exponent + dx;
            // Avoid division by zero when exponent_plus_dx is close to 1
            let exponent_plus_dx = if (exponent_plus_dx - 1.0).abs() < tolerance {
                dx += tolerance;    // for derivative computation
                exponent_plus_dx + tolerance
            } else {
                exponent_plus_dx
            };

            let mut derivative_exponent = 0.;
            // let mut difference_dx = 0.;
            let mut index = 0;
            for &(page, observed_access_count) in observed_data.iter() {
                let estimated_access_count_dx = Self::compute_accesses(
                    page, exponent_plus_dx, coeff, sample_rate,
                    pages_detected, pages_until_cache, pages_until_anon,
                    access_sampled, tolerance, false
                );

                let relative_error_dx = if observed_access_count != 0.0 {
                    (estimated_access_count_dx - observed_access_count) / observed_access_count
                } else {
                    0.0
                };
                // compute the derivative of the difference with respect to exponent
                let max_clip = 10.0; // Adjust the value as needed
                let error_sq = relative_error_dx.powi(2) - diff_records[index].1;
                derivative_exponent += (error_sq / dx).min(max_clip).max(-max_clip) / observed_data.len() as f64;
                index += 1;
            }

            // Compute derivative of the difference with respect to exponent
            last_derivative = derivative_exponent;

            // Update exponent using gradient descent
            if derivative_exponent.abs() < tolerance {
                if derivative_exponent > 0.0 {
                    exponent -= learning_rate_shape * tolerance;
                } else {
                    exponent += learning_rate_shape * tolerance;
                }
            } else {
                exponent -= learning_rate_shape * derivative_exponent;
            }
            if exponent < 0.0 {
                exponent = tolerance;
            } else if observed_data.len() > 1 && exponent > observed_data[0].1 * 2.{
                // at x==1, y should be observed_data[0].1, not beyond it too much
                exponent = observed_data[0].1 * 2.;
            }

            // Compute drviative for coeff (beta, gamma)
            Self::compute_beta_coeff(&mut coeff, observed_data, &mut Some(exponent));

            // Ensure exponent stays within reasonable bounds
            if exponent.is_nan() || exponent.is_infinite() || exponent < 0.0 {
                println!("Exponent out of bounds | {} / {:?} | diff_vec: {:?}", exponent, coeff, diff_records);
                println!(
                    "* History: Diff: {:?}", diff_history);
                return None;
            }
        }
        // Define cache sizes in MB and compute corresponding cache lines
        let cache_sizes_mb: Vec<u64> = (CACHE_GRANULARITY_MB..=MAX_CACHE_IN_MB)
        .step_by(CACHE_GRANULARITY_MB as usize)
        .collect();
        let mut mrc = Vec::new();
        for &cache_size_mb in &cache_sizes_mb {
            let cache_size_lines = cache_size_mb * 1024 * 1024 / PAGE_SIZE_BYTES;
            let cache_capacity_addresses = cache_size_lines as f64;

            let miss_ratio = Self::compute_miss_ratio(
                    access_sampled, sample_rate as f64, pages_detected,
                    cache_capacity_addresses, pages_until_anon as f64,
                    cache_capacity_addresses as f64, exponent, coeff
                );

            // Add to the mrc
            mrc.push((cache_size_mb, miss_ratio.clamp(0.0, 1.0)));
        }
        println!("AppId: {} | MRC: {:?}", app_id, &mrc[..mrc.len().min(64)]);

        // Optimize toward MR
        for iter in 0..max_iterations {
            // Avoid exponent = 1 to prevent division by zero
            if (exponent - 1.0 as f64).abs() < tolerance {
                exponent += tolerance;
                continue;
            }

            // Compute the current hit rate
            let accesses_est = Self::compute_accesses(
                pages_until_cache, exponent, coeff, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, SEPRATE_COMPULSORY);
            let accesses_tot = Self::compute_accesses(
                pages_until_anon, exponent, coeff, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, false);

            let computed_hit_rate = (accesses_est / accesses_tot).max(0.0).min(1.0);
            let mut difference = (((1. - computed_hit_rate) - (1. - target_hit_rate))/(1. - target_hit_rate)).powi(2);
            let mut diff_exponent = difference;
            // let mut difference: f64 = 0.0;
            diff_records.clear();

            for &(page, observed_access_count) in observed_data.iter() {
                let estimated_access_count = Self::compute_accesses(
                    page, exponent, coeff, sample_rate,
                    pages_detected, pages_until_cache, pages_until_anon,
                    access_sampled, tolerance, false
                );  // assuming that the observed data is about sizes before the compulsory miss point

                let relative_error = if observed_access_count != 0.0 {
                    (estimated_access_count - observed_access_count) / observed_access_count
                } else {
                    0.0
                };
                diff_records.push(
                    ((estimated_access_count, observed_access_count, relative_error),
                    relative_error * relative_error));
                // shape is not accounted in the phase 2
            }

            // Check for convergence
            // tolerance -> difference from (1) the target hit rate + (2) observed data
            if difference.abs() < tolerance_mr {
                let acc_beyond_cache = Self::compute_accesses(
                    pages_until_anon, exponent, coeff, sample_rate,
                    pages_detected, pages_until_cache, pages_until_anon,
                    access_sampled, tolerance, false);
                let acc_beyond_cache = acc_beyond_cache
                    - Self::compute_accesses(
                        pages_until_cache, exponent, coeff, sample_rate,
                        pages_detected, pages_until_cache, pages_until_anon,
                        access_sampled, tolerance, false);
                println!(
                    "MR || Converged in {} us ({} iterations): exponent = {}, difference = {}, computed_hit_rate = {}, compulsory_point = {}, access_beyond_cache = {}",
                    start_time.elapsed().as_micros(),
                    iter,
                    exponent,
                    diff_exponent,
                    computed_hit_rate,
                    Self::compute_compulsory_miss_point(exponent, coeff, pages_detected, pages_until_cache, pages_until_anon, sample_rate),
                    acc_beyond_cache,
                );
                *prev_exponent = Some(exponent);
                *prev_coeff = Some(coeff);
                return Some((exponent, coeff));
            }
            last_difference = diff_exponent;
            last_hit_rate = computed_hit_rate;
            diff_history.push(diff_exponent);
            hit_rate_history.push(computed_hit_rate);
            if hit_rate_history.len() > history_len {
                hit_rate_history.remove(0);
                diff_history.remove(0);
            }

            // Compute numerical derivative
            let exponent_plus_dx = exponent + dx;
            // Avoid division by zero when exponent_plus_dx is close to 1
            let exponent_plus_dx = if (exponent_plus_dx - 1.0).abs() < 1e-8 {
                dx += tolerance;    // for derivative computation
                exponent_plus_dx + tolerance
            } else {
                exponent_plus_dx
            };

            let accesses_est_dx = Self::compute_accesses(
                pages_until_cache, exponent_plus_dx, coeff, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, SEPRATE_COMPULSORY);

            let accesses_tot_dx = Self::compute_accesses(
                pages_until_anon, exponent_plus_dx, coeff, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, false);

            let computed_hit_rate_dx = (accesses_est_dx / accesses_tot_dx).max(tolerance).min(1.0 - tolerance);
            let mut difference_dx = (((1. - computed_hit_rate_dx) - (1. - target_hit_rate))/(1. - target_hit_rate)).powi(2);
            let mut derivative_exponent = (difference_dx - difference) / dx;

            // Update exponent using gradient descent
            if derivative_exponent.abs() < tolerance {
                if derivative_exponent > 0.0 {
                    exponent -= learning_rate * tolerance;
                } else {
                    exponent += learning_rate * tolerance;
                }
            } else {
                exponent -= leraning_rate_sensitive * derivative_exponent;
            }
            if exponent < 0.0 {
                exponent = tolerance;
            } else if observed_data.len() > 1 && exponent > observed_data[0].1 * 2.{
                // at x==1, y should be observed_data[0].1, not beyond it too much
                exponent = observed_data[0].1 * 2.;
            }

            // Compute drviative for coeff (beta, gamma)
            Self::compute_beta_coeff(&mut coeff, observed_data, &mut Some(exponent));

            // gamma
            // updated diff with updated coeff.1 (gamma)
            let accesses_est = Self::compute_accesses(
                pages_until_cache, exponent, coeff, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, SEPRATE_COMPULSORY);
            let accesses_tot = Self::compute_accesses(
                pages_until_anon, exponent, coeff, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, false);
            let computed_hit_rate = (accesses_est / accesses_tot).max(0.0).min(1.0);
            let difference = (((1. - computed_hit_rate) - (1. - target_hit_rate))/(1. - target_hit_rate)).powi(2);
            // with dx
            let coeff_plus_dx = (coeff.0, coeff.1 + dx, coeff.2);
            let accesses_est_dx = Self::compute_accesses(
                pages_until_cache, exponent, coeff_plus_dx, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, SEPRATE_COMPULSORY);
            let accesses_tot_dx = Self::compute_accesses(
                pages_until_anon, exponent, coeff_plus_dx, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, false);
            let computed_hit_rate_dx = (accesses_est_dx / accesses_tot_dx).max(tolerance).min(1.0 - tolerance);
            let difference_dx = (((1. - computed_hit_rate_dx) - (1. - target_hit_rate))/(1. - target_hit_rate)).powi(2);
            let derivative_coeff = (difference_dx - difference).min(clip_hit_rate).max(-clip_hit_rate) / dx;

            if derivative_coeff.abs() < tolerance {
                if derivative_coeff > 0.0 {
                    coeff.1 -= learning_rate * tolerance;
                } else {
                    coeff.1 += learning_rate * tolerance;
                }
            } else {
                coeff.1 -= leraning_rate_sensitive * derivative_coeff;
            }
            if coeff.1 < 0.0 {
                coeff.1 = tolerance;
            }

            // delta
            let accesses_est = Self::compute_accesses(
                pages_until_cache, exponent, coeff, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, SEPRATE_COMPULSORY);
            let accesses_tot = Self::compute_accesses(
                pages_until_anon, exponent, coeff, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, false);
            let computed_hit_rate = (accesses_est / accesses_tot).max(0.0).min(1.0);
            let difference = (((1. - computed_hit_rate) - (1. - target_hit_rate))/(1. - target_hit_rate)).powi(2);
            // with dx
            let coeff_plus_dx = (coeff.0, coeff.1, coeff.2 + dx);
            let accesses_est_dx = Self::compute_accesses(
                pages_until_cache, exponent, coeff_plus_dx, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, SEPRATE_COMPULSORY);
            let accesses_tot_dx = Self::compute_accesses(
                pages_until_anon, exponent, coeff_plus_dx, sample_rate,
                pages_detected, pages_until_cache, pages_until_anon,
                access_sampled, tolerance, false);
            let computed_hit_rate_dx = (accesses_est_dx / accesses_tot_dx).max(tolerance).min(1.0 - tolerance);

            let difference_dx = (((1. - computed_hit_rate_dx) - (1. - target_hit_rate))/(1. - target_hit_rate)).powi(2);
            let derivative_coeff = (difference_dx - difference).min(clip_hit_rate).max(-clip_hit_rate) / dx;

            if derivative_coeff.abs() < tolerance {
                if derivative_coeff > 0.0 {
                    coeff.2 -= learning_rate * tolerance;
                } else {
                    coeff.2 += learning_rate * tolerance;
                }
            } else {
                coeff.2 -= learning_rate * derivative_coeff;
            }
            if coeff.2 < tolerance {
                coeff.2 = tolerance;
            }

            if coeff.0.is_nan() || coeff.0.is_infinite() || coeff.0 < 0.0 {
                println!("Coefficient (beta) out of bounds | {} / {:?} | diff_vec: {:?}", exponent, coeff, diff_records);
                println!(
                    "* History: Diff: {:?}", diff_history);
                return None;
            }
            if coeff.1.is_nan() || coeff.1.is_infinite() || coeff.1 < 0.0 {
                println!("Coefficient out (gamma) of bounds | {} / {:?} | diff: {}, diff_dx: {} | diff_vec: {:?}",
                    exponent, coeff, difference, difference_dx, diff_records);
                println!(
                    "* History: Diff: {:?}", diff_history);
                return None;
            }
            last_derivative = derivative_coeff;
        }

        // Compute final hit rate
        let accesses_est = Self::compute_accesses(
            pages_until_cache, exponent, coeff, sample_rate,
            pages_detected, pages_until_cache, pages_until_anon,
            access_sampled, tolerance, SEPRATE_COMPULSORY);
        let accesses_tot = Self::compute_accesses(
            pages_until_anon, exponent, coeff, sample_rate,
            pages_detected, pages_until_cache, pages_until_anon,
            access_sampled, tolerance, false);
        let computed_hit_rate = (accesses_est / accesses_tot).max(0.0).min(1.0);

        // If maximum iterations are reached without convergence
        // Compute error rate based on the target miss rate (usually smaller so more sensitive)
        let err_percent: f64 = (target_hit_rate - computed_hit_rate).abs() / (1. - target_hit_rate) * 100.;
        let acc_beyond_cache = accesses_tot - Self::compute_accesses(
            pages_until_cache, exponent, coeff, sample_rate,
            pages_detected, pages_until_cache, pages_until_anon,
            access_sampled, tolerance, false);
        println!(
            "{} | Maximum iterations reached after {} us without convergence: exponent = {}, coeff = {:?} diff = {}, deriv = {}, hit rate = {}, to match: {}, err(%): {}, compulsory point: {}, access_beyond_cache: {}\ndiff_vec: {:?})",
            chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
            start_time.elapsed().as_micros(),
            exponent, coeff, last_difference, last_derivative, computed_hit_rate, target_hit_rate, err_percent,
            Self::compute_compulsory_miss_point(exponent, coeff, pages_detected, pages_until_cache, pages_until_anon, sample_rate),
            acc_beyond_cache,
            &diff_records.iter().take(12).collect::<Vec<_>>() // Only take the first 12 entries
        );
        let mut mrc = Vec::new();
        for &cache_size_mb in &cache_sizes_mb {
            let cache_size_lines = cache_size_mb * 1024 * 1024 / PAGE_SIZE_BYTES;
            let cache_capacity_addresses = cache_size_lines as f64;

            let miss_ratio = Self::compute_miss_ratio(
                    access_sampled, sample_rate as f64, pages_detected,
                    cache_capacity_addresses, pages_until_anon as f64,
                    cache_capacity_addresses as f64, exponent, coeff
                );

            // Add to the mrc
            mrc.push((cache_size_mb, miss_ratio.clamp(0.0, 1.0)));
        }
        println!("AppId: {} | MRC: {:?}", app_id, &mrc[..mrc.len().min(64)]);

        println!(
            "* History: Diff: {:?}", diff_history);
        if err_percent.abs() < 5. || (target_hit_rate - computed_hit_rate).abs() < tolerance_mr { // it might not much in HR (not MR)
            *prev_exponent = Some(exponent);
            *prev_coeff = Some(coeff);
            Some((exponent, coeff))
        } else {
            None
        }
    }

    fn compute_miss_ratio(
        access_sampled: f64, sample_rate: f64,
        pages_detected: f64,
        pages_until_cache: f64,
        pages_until_anon: f64,
        pages_until_target: f64, exponent: f64, coeff: (f64, f64, f64)) -> f64 {
        let tolerance = 1e-6;
        let accesses_est = Self::compute_accesses(
            pages_until_target, exponent, coeff, sample_rate,
            pages_detected, pages_until_cache, pages_until_anon,
            access_sampled, tolerance, SEPRATE_COMPULSORY);

        let accesses_tot = Self::compute_accesses(
            pages_until_anon, exponent, coeff, sample_rate,
            pages_detected, pages_until_cache, pages_until_anon,
            access_sampled, tolerance, false);

        return 1. - (accesses_est / accesses_tot).max(0.).min(1.);
    }

    async fn get_app_mem_usage(&mut self, app_ids: &Vec<AppId>) -> HashMap<u64, (MemoryMb, MemoryMb)> {
        // data structure: HashMap<AppId, ContainerName>
        let mut cont_names = HashMap::new();
        for app_id in app_ids.iter() {
            // Cache metadata
            cont_names.insert(*app_id, self.id_container_map.get(app_id).unwrap().clone());
        }
        // == batched version ==
        let mem_usage = self.get_mem_usages_from_cgroup(&cont_names).await;

        mem_usage
    }

    async fn get_app_usage(&mut self, app_id: &AppId, mem_mb: MemoryMb) -> AppUsage {
        // Cache metadata
        let cont_name = self.id_container_map.get(app_id).unwrap().clone();

        // get memory usage of the docker container from the given container name
        let bw_mbps = self.get_blk_bandwidth(app_id, &cont_name);

        // cache counter -> cache-misses, cache-references
        let mut locked_measurement = self.container_perf_stat.lock().unwrap();
        let locked_measurement = locked_measurement.get_mut(app_id).unwrap();
        let (cache_miss, cache_hit) = (locked_measurement.avg_miss, locked_measurement.avg_hit);

        // DRAM cache rate
        let cache_mbps: u64 = (cache_miss * CACHE_LINE_SIZE_BYTES / MBPS_TO_BYTES_PER_SEC) as u64;

        // let miss_rate_ops_sec: u64 = 0;
        let access_mem_ops_sec: u64 = locked_measurement.avg_mem_ops;

        // L3 access rate
        let access_rate_ops_sec: u64 = (cache_hit * CACHE_LINE_SIZE_BYTES / MBPS_TO_BYTES_PER_SEC) as u64;
        let hit_rate_percent: f64 = locked_measurement.avg_faults as f64 / locked_measurement.avg_miss.max(1) as f64;

        AppUsage {
            vm_id: self.id,
            app_id: *app_id,
            mem_mb,
            bw_mbps,
            cache_mbps,
            access_mem_ops_sec,
            access_rate_ops_sec,
            hit_rate_percent,
            local_lat: 0,
            remote_lat: 0,
            mrc: Some(locked_measurement.mrc.clone()),
        }
    }

    pub async fn report_usage(&mut self) {
        if !self.running_benchmarks {
            return;
        }

        let app_ids = self.id_container_map.keys().map(|x| *x).collect::<Vec<_>>();
        let mut usage_map = UsageMap::new();
        let mut local_usage = HashMap::new();
        let mut stat_map = AppStats { anon_memory_mb: HashMap::new() };

        // We parallelize commands called inside get_app_usage, especially docker stats
        let mem_mb_results = self.get_app_mem_usage(&app_ids).await;
        // Parsing them require lock-protected Self, due to state updates such as self.conatiner_blk_use_stat
        for app in app_ids.iter() {
            if !(mem_mb_results.contains_key(app)) {
                eprintln!("Failed to get memory usage for app_id {}", app);
                continue;
            }
            let usage = self
                .get_app_usage(app, mem_mb_results.get(app).unwrap().0)
                .await;
            println!("{} | App {} | mem_mb: {}, bw_mbps: {}, cache_mbps: {}, access_rate_mbps: {}, mem_rate: {}",
                chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
                app, usage.mem_mb, usage.bw_mbps, usage.cache_mbps, usage.access_rate_ops_sec, usage.access_mem_ops_sec);
            local_usage.insert(*app, usage);
            stat_map.anon_memory_mb.insert(*app, mem_mb_results.get(app).unwrap().1);
        }
        usage_map.map.insert(self.id, local_usage);
        // Insert new data; if it is larger than the limit, remove the oldest data
        let mut locked_usage = self.latest_usage.lock().await;
        locked_usage.push((usage_map.clone(), stat_map.clone()));
        if locked_usage.len() > LEN_PERF_HISTORY {
            locked_usage.remove(0);
        }
        send_usage(&usage_map, &self.global_ip).await;
    }
}

impl Drop for CacheClient {
    fn drop(&mut self) {
        for child in self.child_processes.values_mut() {
            match child.kill() {
                Err(e) => eprintln!("Error killing child process: {}", e),
                _ => (),
            }
        }
    }
}
