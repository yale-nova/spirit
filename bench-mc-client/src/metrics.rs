use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Instant, SystemTime};
use tokio::sync::Mutex;
use rocket::{get, State};
use serde_json::json;
use chrono::{DateTime, Utc};

use lib::AppId;

// Constants for metrics collection
pub const NUM_BUCKETS: usize = 50_000; // 50k -> 500 us per bucket
pub const MAX_LATENCY: u128 = 2_500_000; // Define the maximum latency value (in us)
pub const PRINT_INTERVAL: usize = 10000;

#[derive(Debug)]
pub struct PerfMetrics {
    pub latencies: Vec<AtomicUsize>,
    pub num_requests: AtomicUsize,
    pub tot_size_bytes: AtomicUsize,
    pub start_time: Mutex<Instant>,
    pub init_time: Mutex<Instant>,
}

pub fn initialize_buckets() -> Vec<AtomicUsize> {
    (0..NUM_BUCKETS).map(|_| AtomicUsize::new(0)).collect()
}

// Function to convert Instant to human-readable time format
pub fn instant_to_string(instant: std::time::Instant) -> String {
    let duration_since_epoch = instant.elapsed();
    let system_time = SystemTime::now() - duration_since_epoch;
    let datetime: DateTime<Utc> = system_time.into();
    datetime.to_rfc3339()
}

pub fn update_latency_metrics(perf_metrics: &Arc<PerfMetrics>, start_time: Instant) {
    let latency = start_time.elapsed().as_micros();
    let bucket_index = ((latency as f64 / MAX_LATENCY as f64) * NUM_BUCKETS as f64)
        .min(NUM_BUCKETS as f64 - 1.0) as usize;
    perf_metrics.latencies[bucket_index].fetch_add(1, Ordering::SeqCst);
}

pub fn update_request_metrics(
    perf_metrics: &Arc<PerfMetrics>,
    cnt: &mut usize,
    sent_values: &mut usize,
    size_served: usize,
    active_req: &Arc<AtomicUsize>,
) {
    *cnt += 1;
    perf_metrics.num_requests.fetch_add(1, Ordering::SeqCst);
    *sent_values += size_served;
    perf_metrics.tot_size_bytes.fetch_add(size_served, Ordering::SeqCst);

    if *cnt % PRINT_INTERVAL == 0 {
        let start_time = perf_metrics.start_time.try_lock().map(|time| time.clone());
        if start_time.is_err() {
            return; // no print
        }
        let start_time = start_time.unwrap();
        // Check if at least 10 seconds have passed since start_time
        if start_time.elapsed().as_secs() < 10 {
            return; // no print, minimum 10 second of data required
        }
        let elapsed_time = start_time.elapsed().as_secs_f64();
        let total_bytes = perf_metrics.tot_size_bytes.load(Ordering::SeqCst);
        let throughput_mbps = (total_bytes as f64 * 8.0) / (elapsed_time * 1_000_000.0); // Convert to Mbps

        let total_requests = perf_metrics.num_requests.load(Ordering::SeqCst);
        let total_requests_f64 = total_requests as f64;
        let mut sum_latency = 0.0;
        let mut cumulative = 0.0;
        let mut p75 = 0.0;
        let mut p90 = 0.0;
        let mut p99 = 0.0;
        for (i, bucket) in perf_metrics.latencies.iter().enumerate() {
            let count = bucket.load(Ordering::SeqCst) as f64;
            let bucket_latency = (i as f64 + 0.5) / NUM_BUCKETS as f64 * MAX_LATENCY as f64;
            sum_latency += count * bucket_latency;
            cumulative += count;
            let fraction = cumulative / total_requests_f64;
            if p75 == 0.0 && fraction >= 0.75 {
                p75 = bucket_latency;
            }
            if p90 == 0.0 && fraction >= 0.90 {
                p90 = bucket_latency;
            }
            if p99 == 0.0 && fraction >= 0.99 {
                p99 = bucket_latency;
            }
        }
        let avg_latency = sum_latency / total_requests_f64;

        println!(
            "{} requests sent | {} bytes | {:.2} Mbps | avg: {:.3} us | 75th: {:.3} us, 90th: {:.3} us, 99th: {:.3} us | active: {}",
            *cnt,
            *sent_values,
            throughput_mbps,
            avg_latency,
            p75,
            p90,
            p99,
            active_req.load(Ordering::SeqCst)
        );
    }
}

pub async fn reset_perf_metrics(perf_metrics: &Arc<PerfMetrics>) {
    perf_metrics.num_requests.store(0, Ordering::SeqCst);
    perf_metrics.tot_size_bytes.store(0, Ordering::SeqCst);
    *perf_metrics.start_time.lock().await = Instant::now();
    for bucket in perf_metrics.latencies.iter() {
        bucket.store(0, Ordering::SeqCst);
    }
}

#[get("/metric")]
pub async fn metric(perf_metrics: &State<HashMap<AppId, Arc<PerfMetrics>>>) -> String {
    let mut cdf_data = HashMap::new();
    let mut thput_data = HashMap::new();
    let mut init_time_data: HashMap<u64, String> = HashMap::new();
    let mut start_time_data: HashMap<u64, String> = HashMap::new();

    // Calculate CDF for each app
    for app_id in perf_metrics.keys() {
        let app_id = *app_id;
        let perf_metric = perf_metrics.get(&app_id).unwrap();
        let latency_snapshot = perf_metric.latencies.iter().map(|bucket| bucket.load(Ordering::SeqCst)).collect::<Vec<usize>>();

        let total_count: usize = latency_snapshot.iter().sum();
        if total_count == 0 {
            return json!({"error": "No data available"}).to_string();
        }

        // Calculate CDF
        let mut cumulative_count = 0;
        let mut app_cdf_data = Vec::new();
        // CDF: x-axis: latency, y-axis: percentile
        for (i, bucket) in latency_snapshot.iter().enumerate() {
            cumulative_count += bucket;
            let percentile = cumulative_count as f64 / total_count as f64;
            let latency_value = (i as f64 / NUM_BUCKETS as f64) * MAX_LATENCY as f64;
            app_cdf_data.push((latency_value, percentile));
        }
        cdf_data.insert(app_id, app_cdf_data);

        // Compute throughput
        let total_time = perf_metric.start_time.lock().await.elapsed().as_secs_f64();
        let throughput = perf_metric.tot_size_bytes.load(Ordering::SeqCst) as f64 / total_time;
        thput_data.insert(app_id, throughput * 8.0 / 1_000_000.0);  // in Mbps

        // Time stamps
        let init_time = perf_metric.init_time.lock().await;
        let init_time_str = instant_to_string(*init_time);
        init_time_data.insert(app_id, init_time_str);
        let start_time = perf_metric.start_time.lock().await;
        let start_time_str = instant_to_string(*start_time);
        start_time_data.insert(app_id, start_time_str);
    }

    // Convert to JSON string
    json!({
        "cdf": cdf_data,
        "thput_mbps": thput_data,
        "init_time": init_time_data,
        "start_time": start_time_data,
    }).to_string()
}