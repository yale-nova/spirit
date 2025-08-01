use std::collections::HashSet;

use crate::{BandwidthMbps, UpdateConfig, UsageMap};
use tokio::process::{Child, Command};
use std::process::Stdio;
use std::io::Write;

pub fn start_cache_instance(port: u64, backend_ip: &str) -> Child {
    Command::new("sh")
        .arg("-c")
        .arg(format!("../redis-cache/src/redis-server --port {} --save '' --rate-limit-key rate_limit_key_{} --remote-backend-ip {}", port, port, backend_ip))
        .spawn()
        .unwrap_or_else(|_| panic!("Failed to launch cache instance on port {}", port))
}

pub fn start_backend_instance(cmd: &str) -> Child {
    Command::new("sh")
        .arg("-c")
        .arg(cmd)
        .spawn()
        .unwrap_or_else(|_| panic!("Failed to launch backend instance, backend cmd: {cmd}"))
}

pub async fn preload_benchmark(preload_cmd: &str) {
    println!("Pre-loading benchmark: {preload_cmd}");
    Command::new("sh")
        .arg("-c")
        .arg(preload_cmd)
        .output()
        .await
        .expect("local: Failure preloading benchmark");
}

pub fn run_benchmark(run_cmd: &str) -> Child {
    println!("Starting benchmark: {run_cmd}");
    Command::new("sh")
        .arg("-c")
        .arg(run_cmd)
        .spawn()
        .unwrap_or_else(|_| panic!("Failed to launch benchmark instance {run_cmd}"))
}

pub async fn preload_benchmarks(preload_cmds: HashSet<&String>) {
    for preload_cmd in preload_cmds {
        println!("Pre-loading benchmark: {preload_cmd}");
        Command::new("sh")
            .arg("-c")
            .arg(preload_cmd)
            .output()
            .await
            .expect("global: Failure preloading benchmarks");
    }
}

pub async fn send_config(cfg: &UpdateConfig, dest: &str) {
    let command = format!(
        "curl -H 'Content-Type: application/json' -d'{}' http://{dest}/config",
        cfg.to_string()
    );
    println!("Sending config: {command}");
    Command::new("sh")
        .arg("-c")
        .arg(command)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("Failed to spawn command");
}

pub async fn send_usage(usage_map: &UsageMap, dest: &str) {
    let command = format!(
        "curl -H 'Content-Type: application/json' -d'{}' http://{dest}/usage",
        usage_map.to_string()
    );
    // println!("Sending usage: {command}");
    Command::new("sh")
        .arg("-c")
        .arg(command)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("Failed to spawn command");
}

pub async fn get_usage(dest: &str) -> UsageMap {
    let command = format!("curl http://{dest}/usage");
    // println!("Getting usage: {command}");
    let output = Command::new("sh")
        .arg("-c")
        .arg(command)
        .output()
        .await
        .expect("Failed to get usage");
    let usage_map = serde_json::from_slice(&output.stdout);
    if usage_map.is_err() {
        println!("Usage response: {}", String::from_utf8_lossy(&output.stdout));
        // flush
        std::io::stdout().flush().unwrap();
        eprint!("Failed to parse usage map from response (maybe the container is initializing)");
        UsageMap::new()
    } else {
        usage_map.unwrap()
    }
}

pub async fn shut_down(mut processes: Vec<Child>) {
    for process in processes.iter_mut() {
        process
            .kill()
            .await
            .unwrap_or_else(|_| panic!("Failure shutting down process"));
    }
}

pub async fn reset_network_limit(dev_name: &String) {
    let command = format!(
        "sudo tc qdisc del dev {dev_name} root",
        dev_name = dev_name
    );
    Command::new("sh")
        .arg("-c")
        .arg(command)
        .output()
        .await
        .expect("Failed to reset tc net dev");
}

pub async fn set_network_limit(dev_name: &String, limit_in_mbps: BandwidthMbps) {
    let command = format!(
        "sudo tc qdisc add dev {dev_name} root handle 1: tbf rate {limit_in_mbps}Mbit burst 10mb latency 10ms",
        dev_name = dev_name,
        limit_in_mbps = limit_in_mbps
    );
    Command::new("sh")
        .arg("-c")
        .arg(command)
        .spawn()
        .expect("Failed to spawn tc bandwidth limit command");
}
