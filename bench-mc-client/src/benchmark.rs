use tokio::sync::RwLock;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::collections::{HashMap, HashSet};
use std::time::Instant;
use futures::future::join_all;
use async_memcached::Client as MemcachedClient;

use lib::{AppId, InitConfig, get_random_string};
use lib::process_requests::{DatabaseRequest, parse_database_requests};
use lib::libcache_loader::{parse_cachesim_requests, TRACE_REPLAY_DUPLICATION_RATIO};

use crate::metrics::{PerfMetrics, update_latency_metrics, update_request_metrics, reset_perf_metrics};

// Constants
const RESET_COUNTER_THRESHOLD: f64 = 0.3;  // half of the workload to fill 3 GB
const FAILURE_THRESHOLD: u32 = 5;  // number of consecutive failures before entering observing mode

pub async fn mk_req_map(config: &InitConfig) -> HashMap<String, Arc<RwLock<Vec<DatabaseRequest>>>> {
    let unique_benchmarks = config.id_benchmark_map.values().collect::<HashSet<_>>();

    let mut new_map = HashMap::default();
    for benchmark_path in unique_benchmarks {
        if benchmark_path.is_empty() {
            continue;
        }

        let db_requests;
        // check if filename ends with ".bin.zst"
        if benchmark_path.ends_with(".zst") {
            db_requests = parse_cachesim_requests(benchmark_path).await;
        } else {
            db_requests = parse_database_requests(benchmark_path).await;
            // let preload = config.id_preload_map.get(&target_app_id).unwrap();
            // TODO: no scan operation supported now
        }
        new_map.insert(benchmark_path.clone(), Arc::new(RwLock::new(db_requests)));
    }
    new_map
}

pub async fn mk_preload_requests(config: &InitConfig) -> HashMap<String, Arc<RwLock<Vec<DatabaseRequest>>>> {
    let unqiue_preloads = config.id_preload_map.values().collect::<HashSet<_>>();
    // Loading files
    let mut new_map = HashMap::default();
    for preload_path in unqiue_preloads {
        if preload_path.is_empty() {
            continue;
        }
        let db_requests = parse_database_requests(preload_path).await;
        new_map.insert(preload_path.clone(), Arc::new(RwLock::new(db_requests)));
    }
    new_map
}

pub async fn populate_requests(bench_config: &crate::BenchmarkClientConfig) -> HashMap<AppId, Arc<RwLock<Vec<DatabaseRequest>>>> {
    // Loading files
    let new_map = Arc::new(mk_req_map(&bench_config.initial_config).await);

    // For each app
    let mut requests = HashMap::default();
    for app_id in bench_config.initial_config.id_benchmark_map.keys() {
        let app_id = *app_id;
        // check new_map include this app_id
        let app_bench_path = bench_config.initial_config.id_benchmark_map.get(&app_id).unwrap();
        if !new_map.contains_key(app_bench_path) {
            panic!("App id {} not found in the benchmark map", app_id);
        }
        requests.insert(app_id, new_map.get(app_bench_path).unwrap().clone());
    }
    requests
}

pub async fn init_memcached_client(cache_ip: &str, cache_port: u16) -> Option<MemcachedClient> {
    match MemcachedClient::new(format!("tcp://{}:{}", cache_ip, cache_port)).await {
        Ok(client) => Some(client),
        Err(e) => {
            eprintln!("Failed to connect to memcached: {:?}", e);
            None
        }
    }
}

pub async fn process_db_request(
    client: &mut MemcachedClient,
    mut db_request: DatabaseRequest,
    random_string: &String,
    perf_metrics: &Arc<PerfMetrics>,
    active_req: &Arc<AtomicUsize>,
    cnt: &mut usize,
    sent_values: &mut usize,
    thread_idx: u16,
) -> bool {
    db_request.thread_id = thread_idx;

    generate_value_if_needed(&mut db_request, random_string);

    let key = &db_request.key;
    let value_len = db_request.value_len;

    active_req.fetch_add(1, Ordering::SeqCst);
    let request_start_time = Instant::now();
    let get_result = client.get(key).await;

    update_latency_metrics(perf_metrics, request_start_time);

    let mut success = true;

    match get_result {
        Ok(Some(value)) => {
            handle_existing_key(
                client,
                key,
                value,
                &db_request,
                perf_metrics,
                cnt,
                sent_values,
                active_req,
            )
            .await;
        }
        Ok(None) => {
            handle_missing_key(
                client,
                key,
                &db_request,
                perf_metrics,
                cnt,
                sent_values,
                active_req,
            )
            .await;
        }
        Err(err) => {
            eprintln!("Error getting key from memcached: {:?}", err);
            success = false;
        }
    }

    active_req.fetch_sub(1, Ordering::SeqCst);
    success
}

fn generate_value_if_needed(db_request: &mut DatabaseRequest, random_string: &String) {
    if db_request.value_len > 0 && db_request.value.is_none() {
        let value_str = get_random_string(random_string, db_request.value_len);
        db_request.value = Some(value_str);
    }
}

async fn handle_existing_key(
    client: &mut MemcachedClient,
    key: &str,
    value: async_memcached::Value,
    db_request: &DatabaseRequest,
    perf_metrics: &Arc<PerfMetrics>,
    cnt: &mut usize,
    sent_values: &mut usize,
    active_req: &Arc<AtomicUsize>,
) {
    let value_string = String::from_utf8_lossy(&value.data).to_string();
    let actual_value_len = value_string.len();

    let needs_set = should_perform_set(actual_value_len, db_request.value_len);

    if needs_set {
        if let Some(ref value_str) = db_request.value {
            perform_set_operation(
                client,
                key,
                value_str,
                perf_metrics,
                cnt,
                sent_values,
                active_req,
            )
            .await;
        }
    } else {
        update_request_metrics(
            perf_metrics,
            cnt,
            sent_values,
            value_string.len(),
            active_req,
        );
    }
}

async fn handle_missing_key(
    client: &mut MemcachedClient,
    key: &str,
    db_request: &DatabaseRequest,
    perf_metrics: &Arc<PerfMetrics>,
    cnt: &mut usize,
    sent_values: &mut usize,
    active_req: &Arc<AtomicUsize>,
) {
    if let Some(ref value_str) = db_request.value {
        perform_set_operation(
            client,
            key,
            value_str,
            perf_metrics,
            cnt,
            sent_values,
            active_req,
        )
        .await;
    }
}

fn should_perform_set(actual_value_len: usize, expected_value_len: usize) -> bool {
    if expected_value_len == 0 {
        false
    } else {
        actual_value_len != expected_value_len
    }
}

async fn perform_set_operation(
    client: &mut MemcachedClient,
    key: &str,
    value: &str,
    perf_metrics: &Arc<PerfMetrics>,
    cnt: &mut usize,
    sent_values: &mut usize,
    active_req: &Arc<AtomicUsize>,
) {
    let set_start_time = Instant::now();
    let set_result = client.set(key, value, None, None).await;

    update_latency_metrics(perf_metrics, set_start_time);

    match set_result {
        Ok(()) => {
            update_request_metrics(
                perf_metrics,
                cnt,
                sent_values,
                value.len(),
                active_req,
            );
        }
        Err(err) => {
            eprintln!("Error setting key in memcached: {:?}", err);
        }
    }
}

pub async fn wait_for_next_request() {
    tokio::time::sleep(std::time::Duration::from_millis(10)).await;
}

pub fn update_trace_time(prev_trace_time: &mut Instant, trace_time: &mut u64) {
    let cur_time = Instant::now();
    if cur_time - *prev_trace_time > std::time::Duration::from_secs(1) {
        *prev_trace_time = cur_time;
        *trace_time += 1;
    }
}

pub fn get_db_request(
    db_requests: &Vec<DatabaseRequest>,
    index: u64,
) -> Option<DatabaseRequest> {
    match db_requests.get(index as usize) {
        Some(req) => Some(req.clone()),
        None => {
            eprintln!("Index {} out of bounds", index);
            None
        }
    }
}

pub fn update_index(
    index: &mut u64,
    thread_num: u16,
    thread_idx: u16,
    db_requests_len: u64,
) {
    *index += thread_num as u64;
    if *index >= db_requests_len {
        *index = thread_idx as u64;
    }
}

pub fn should_reset_perf_metrics(
    thread_idx: u16,
    warmup: bool,
    index: u64,
    db_requests_len: u64,
) -> bool {
    thread_idx == 0 && warmup && index * TRACE_REPLAY_DUPLICATION_RATIO > (db_requests_len as f64 * RESET_COUNTER_THRESHOLD) as u64
}

pub fn bench_runner_body<'a>(
    app_id: AppId,
    thread_idx: u16,
    thread_num: u16,
    db_requests: Arc<RwLock<Vec<DatabaseRequest>>>,
    cache_ip: String,
    cache_port: u16,
    stop_signal: Arc<AtomicBool>,
    perf_metrics: Arc<PerfMetrics>,
    active_req: Arc<AtomicUsize>,
    random_string_src: Arc<RwLock<String>>,
) -> impl futures::Future<Output = ()> + 'a {
    async move {
        // Initialize the Memcached client
        let mut client = match init_memcached_client(&cache_ip, cache_port).await {
            Some(client) => client,
            None => return,
        };

        // Initialize variables
        let mut index = thread_idx as u64;
        let db_requests = db_requests.read().await;
        let random_string = random_string_src.read().await;
        let mut prev_trace_time = Instant::now();
        let mut trace_time: u64 = 0;
        println!("Thread {} started", thread_idx);
        let mut cnt: usize = 0;
        let mut sent_values: usize = 0;
        let mut warmup: bool = true;

        // Variables for tracking server failures
        let mut consecutive_failures = 0;

        loop {
            if stop_signal.load(Ordering::Relaxed) {
                break;
            }

            let db_request = match get_db_request(&db_requests, index) {
                Some(req) => req,
                None => break,
            };

            for _ in 0..db_request.n_req {
                for key_dup_idx in 0..TRACE_REPLAY_DUPLICATION_RATIO {
                    update_trace_time(&mut prev_trace_time, &mut trace_time);
                    let mut db_request = db_request.clone();
                    // append "_" + key_dup_idx to the key
                    if TRACE_REPLAY_DUPLICATION_RATIO > 1 {
                        db_request.key = format!("{}_{}", db_request.key, key_dup_idx);
                    }

                    loop {
                        if db_request.timestamp <= trace_time {
                            // If we're in observing mode, wait 1 second before making the request
                            if consecutive_failures >= FAILURE_THRESHOLD {
                                if consecutive_failures == FAILURE_THRESHOLD {
                                    println!("Thread {} | Entering observing mode due to {} consecutive failures",
                                            thread_idx, FAILURE_THRESHOLD);
                                }
                                tokio::time::sleep(std::time::Duration::from_secs(1)).await;
                            }

                            let success = process_db_request(
                                &mut client,
                                db_request.clone(),
                                &random_string,
                                &perf_metrics,
                                &active_req,
                                &mut cnt,
                                &mut sent_values,
                                thread_idx,
                            ).await;

                            if success {
                                // Reset consecutive failures counter and log if exiting observing mode
                                if consecutive_failures >= FAILURE_THRESHOLD {
                                    println!("Thread {} | Server is available again, exiting observing mode", thread_idx);
                                }
                                consecutive_failures = 0;
                                break; // Break inner loop to move to next key_dup_idx
                            } else {
                                // Increment consecutive failures counter
                                consecutive_failures += 1;
                                if consecutive_failures >= FAILURE_THRESHOLD {
                                    consecutive_failures = FAILURE_THRESHOLD; // Cap at threshold
                                    continue; // Retry same request after waiting
                                }
                                break; // If not in observing mode yet, move to next key_dup_idx
                            }
                        } else {
                            wait_for_next_request().await;
                            continue;
                        }
                    }
                }
            }

            update_index(&mut index, thread_num, thread_idx, db_requests.len() as u64);

            if should_reset_perf_metrics(thread_idx, warmup, index, db_requests.len() as u64) {
                warmup = false;
                reset_perf_metrics(&perf_metrics).await;
                println!("Thread {} | Resetting metrics", thread_idx);
            }
        }
    }
}

pub async fn run_preload(
    _app_id: AppId,
    thread_idx: u16, thread_num: u16,
    db_requests: Arc<RwLock<Vec<DatabaseRequest>>>,
    cache_ip: String, cache_port: u16,
    _stop_signal: Arc<AtomicBool>,
    perf_metrics: Arc<PerfMetrics>,
    active_req: Arc<AtomicUsize>,
    random_string_src: Arc<RwLock<String>>,
) {
    let mut client = match init_memcached_client(&cache_ip, cache_port).await {
        Some(c) => c,
        None => {
            eprintln!("Failed to initialize memcached client in preload");
            return;
        }
    };

    let db_requests_lock = db_requests.read().await;
    let random_string = random_string_src.read().await;
    println!("Preload Thread {} started", thread_idx);
    let mut cnt = 0;
    let mut sent_values = 0;
    let mut index = thread_idx as u64;
    while index < db_requests_lock.len() as u64 {
        let mut db_request = db_requests_lock.get(index as usize).unwrap().clone();
        db_request.thread_id = thread_idx;
        process_db_request(
            &mut client,
            db_request,
            &random_string,
            &perf_metrics,
            &active_req,
            &mut cnt,
            &mut sent_values,
            thread_idx,
        )
        .await;
        index += thread_num as u64;
    }
}

pub async fn run_benchmark(
    app_id: AppId,
    bench_config: &crate::BenchmarkClientConfig,
    requests: Arc<RwLock<Vec<DatabaseRequest>>>,
    perf_metrics: Arc<PerfMetrics>,
    random_string_src: Arc<RwLock<String>>,
    stop_signal: Arc<AtomicBool>,
) -> Result<(), Box<dyn std::error::Error>> {
    // Define the number of concurrent requests allowed
    let cache_ip = bench_config.initial_config.cache_ip.clone().unwrap();
    let cache_port: u16 = bench_config.initial_config.bind_port.unwrap() as u16;
    println!("Sending requests to cache: {}:{}", cache_ip, cache_port);

    // Figure out the number of threads to spawn
    let num_threads = bench_config.concurrency;
    let mut thread_handle = Vec::new();
    let active_req = Arc::new(AtomicUsize::new(0));
    for i in 0..num_threads {
        let requests = requests.clone();
        let cache_ip = cache_ip.clone();
        let stop_signal = stop_signal.clone();
        let perf_metrics = perf_metrics.clone();
        let handle = tokio::spawn(bench_runner_body(
            app_id, i as u16, num_threads as u16, requests,
            cache_ip, cache_port,
            stop_signal, perf_metrics, active_req.clone(),
            random_string_src.clone()));
        thread_handle.push(handle);
    }
    join_all(thread_handle).await;

    Ok(())
}