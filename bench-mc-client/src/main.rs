use futures::future::join_all;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::collections::HashMap;
use std::time::Instant;
use tokio::sync::{Mutex, RwLock};
use core_affinity;
use clap::{Command, Arg};
use lib::{AppId, InitConfig, precompute_random_string, PRECOMPUTED_SIZE};

// Module declarations
mod metrics;
mod benchmark;

// Use items from modules
use metrics::{PerfMetrics, initialize_buckets};
use benchmark::{mk_preload_requests, populate_requests, run_preload, run_benchmark};

// Constants
const NUM_CORE: usize = 4;

// Global atomic counter to assign cores in on_thread_start
static THREAD_COUNTER: AtomicUsize = AtomicUsize::new(0);

#[derive(Debug, Clone)]
pub struct BenchmarkClientConfig {
    pub concurrency: usize,
    pub initial_config: InitConfig,
}

fn parse_arguments() -> BenchmarkClientConfig {
    let matches = Command::new("Bench Client")
        .version("0.1")
        .author("Spirit authors")
        .about("Sends concurrent requests to a Rocket HTTP server and measures latency/throughput")
        .arg(
            Arg::new("num_requests")
                .short('r')
                .long("requests")
                .help("Number of requests to send")
                .num_args(1)
                .default_value("10000"),
        )
        .arg(
            Arg::new("config")
                .short('c')
                .long("config")
                .help("path to the config file")
                .num_args(1)
                .default_value("/configs/local_meta.json"),
        )
        .arg(
            Arg::new("max_concurrent_requests")
                .short('l')
                .long("limit")
                .help("number of concurrent requests allowed")
                .num_args(1)
                .default_value("12"),
        )
        .get_matches();

    // check if the config file exists
    let config_data = std::fs::read_to_string(matches.get_one::<String>("config")
        .expect("Unable to open config file"))
        .expect("Unable to read config file");
    let initial_config: InitConfig =
        serde_json::from_str(&config_data).expect("Unable to parse config file");
    // cache IP, port, and own metric port
    if initial_config.cache_ip.is_none() {
        panic!("Cache IP not found in the config file");
    }
    if initial_config.bind_port.is_none() {
        panic!("Cache port not found in the config file");
    }
    if initial_config.metric_port.is_none() {
        panic!("Metric port not found in the config file");
    }

    let concurrency: usize = matches
        .get_one::<String>("max_concurrent_requests")
        .unwrap()
        .parse()
        .expect("Invalid number of concurrent requests");

    // Printings
    println!("/metric exposed to the port: 0.0.0.0:{}", initial_config.metric_port.expect("Metric port not found"));

    BenchmarkClientConfig {
        initial_config,
        concurrency
    }
}

async fn run_main() {
    let bench_config = parse_arguments();
    let bench_config_clone = bench_config.clone();

    // Random string generator
    let random_string = precompute_random_string(PRECOMPUTED_SIZE);
    let random_string_src = Arc::new(RwLock::new(random_string));

    // Preloading: create dummy metrics and active counter for preloader
    let preload_metrics = Arc::new(PerfMetrics {
        latencies: initialize_buckets(),
        num_requests: AtomicUsize::new(0),
        tot_size_bytes: AtomicUsize::new(0),
        start_time: Mutex::new(Instant::now()),
        init_time: Mutex::new(Instant::now()),
    });
    let preload_active = Arc::new(AtomicUsize::new(0));
    let preload_map = mk_preload_requests(&bench_config.initial_config).await;
    let mut preload_handles = Vec::new();
    for (preload_path, preload_requests) in preload_map.iter() {
        // Find all app_ids that use this preload path
        let matching_app_ids: Vec<AppId> = bench_config.initial_config.id_preload_map
            .iter()
            .filter_map(|(app_id, path)| if path == preload_path { Some(*app_id) } else { None })
            .collect();
        println!(
            "Preloading for app: {:?}, path: {}, reqs: {}",
            matching_app_ids,
            preload_path,
            preload_requests.read().await.len()
        );
        for &app_id in &matching_app_ids {
            let req = preload_requests.clone();
            let cache_ip = bench_config.initial_config.cache_ip.clone().unwrap();
            let cache_port = bench_config.initial_config.bind_port.unwrap() as u16;
            let preload_thread_num = bench_config.concurrency as u16;
            // Spawn preload tasks per app_id
            for thread_idx in 0..preload_thread_num {
                let req_clone = req.clone();
                let cache_ip_clone = cache_ip.clone();
                let preload_signal = Arc::new(AtomicBool::new(false));
                let handle = tokio::spawn(run_preload(
                    app_id,
                    thread_idx,
                    preload_thread_num,
                    req_clone,
                    cache_ip_clone,
                    cache_port,
                    preload_signal,
                    preload_metrics.clone(),
                    preload_active.clone(),
                    random_string_src.clone()
                ));
                preload_handles.push(handle);
            }
        }
    }
    // Await all preload tasks
    let _ = join_all(preload_handles).await;

    // Proceed with benchmark initialization
    let requests = populate_requests(&bench_config).await;
    let mut perf_metrics: HashMap<AppId, Arc<PerfMetrics>> = HashMap::new();
    let stop_signal = Arc::new(AtomicBool::new(false));
    let mut bench_handles = Vec::new();

    for app_id in bench_config_clone.initial_config.id_benchmark_map.keys() {
        let app_id = *app_id;
        let app_latency = initialize_buckets();
        perf_metrics.insert(
            app_id,
            Arc::new(PerfMetrics {
                latencies: app_latency,
                num_requests: AtomicUsize::new(0),
                tot_size_bytes: AtomicUsize::new(0),
                start_time: Mutex::new(Instant::now()),
                init_time: Mutex::new(Instant::now()),
            }),
        );
        let app_requests = requests.get(&app_id).unwrap().clone();
        let perf_metrics_clone = perf_metrics.clone();
        let benchmark_config_clone = bench_config.clone();
        let random_string_src = random_string_src.clone();
        let stop_signal = stop_signal.clone();
        let handle = tokio::spawn(async move {
            let perf_metrics = perf_metrics_clone.get(&app_id).expect("App id does not exist");

            run_benchmark(
                app_id,
                &benchmark_config_clone,
                app_requests,
                perf_metrics.clone(),
                random_string_src,
                stop_signal,
            )
            .await
            .unwrap();

            // Calculate average latency
            let total_req_num = perf_metrics.num_requests.load(Ordering::SeqCst);
            let average_latency: f64 = perf_metrics
                .latencies
                .iter()
                .enumerate()
                .map(|(i, x)| {
                    let num_requests = x.load(Ordering::SeqCst) as f64;
                    let percentile = (i as f64 + 0.5) / metrics::NUM_BUCKETS as f64;
                    let latency = (percentile * metrics::MAX_LATENCY as f64) as f64;
                    num_requests * latency
                })
                .sum::<f64>()
                / total_req_num as f64;

            // Calculate throughput
            let total_time = perf_metrics.start_time.lock().await.elapsed().as_secs_f64();
            let throughput = perf_metrics.tot_size_bytes.load(Ordering::SeqCst) as f64 / total_time;
            let throughput_requests = total_req_num as f64 / total_time;

            println!("App{} | Total requests: {}", app_id, total_req_num);
            println!("App{} | Total time taken: {:.2} seconds", app_id, total_time);
            println!(
                "App{} | Throughput: {:.2} requests/second, {:.2} Mbps",
                app_id,
                throughput_requests,
                throughput * 8.0 / 1_000_000.0
            );
            println!("App{} | Average latency: {:.2} us", app_id, average_latency);
        });
        bench_handles.push(handle);
    }

    // Flattened bench handle: directly await all benchmark tasks
    let _ = join_all(bench_handles).await;
}

fn main() {
    // Wait for 10 seconds to give the OS time to allocate cores to benchmarks
    println!("Waiting for 10 seconds to allocate cores to benchmarks");
    std::thread::sleep(std::time::Duration::from_secs(10));

    // Retrieve available cores and select exactly 4 cores.
    let available_cores = core_affinity::get_core_ids().expect("Unable to get core IDs");
    assert!(available_cores.len() >= NUM_CORE, "Need at least {} cores", NUM_CORE);
    let selected_cores = available_cores.into_iter().take(NUM_CORE).collect::<Vec<_>>();
    let selected_cores = Arc::new(selected_cores);

    // Build the Tokio runtime with 4 worker threads and pin each thread to a core.
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(NUM_CORE)
        .enable_all()
        .on_thread_start({
            let selected_cores = selected_cores.clone();
            move || {
                // Assign a core to this thread using an atomic counter.
                let thread_index = THREAD_COUNTER.fetch_add(1, Ordering::SeqCst);
                if let Some(core_id) = selected_cores.get(thread_index % selected_cores.len()) {
                    core_affinity::set_for_current(*core_id);
                }
            }
        })
        .build()
        .expect("Failed to build Tokio runtime");
    runtime.block_on(run_main());
}
