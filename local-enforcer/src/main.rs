mod cache_client;

use lib::{AppId, VmId, Port, UpdateConfig, precheck_port};

use cache_client::CacheClient;
use rocket::fairing::AdHoc;
use rocket::response::status;
use rocket::serde::{
    json::{serde_json, Json},
    Deserialize, Serialize,
};
use rocket::State;

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::{env, fs};

use tokio::sync::Mutex;
use chrono;

#[macro_use]
extern crate rocket;

const RESOURCE_REPORT_INTERVAL_IN_MS: u64 = 1000; // in ms

#[derive(serde::Deserialize)]
struct ConfigRequest {
    server_id: i64,
    client_id: String,
    allocation_map: HashMap<String, Allocation>,
    benchmark_map: HashMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(crate = "rocket::serde")]
pub struct InitConfig {
    pub id_preload_map: Vec<PreloadMap>,
    pub id_benchmark_map: HashMap<AppId, String>,
    pub memory_dev_name: String,
    pub memory_ip: String,
    pub global_ip: String,
    pub config_path: Option<HashMap<AppId, String>>,
    pub init_script: Option<String>,
    pub enable_mrc: Option<bool>,
    pub vm_id: Option<VmId>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(crate = "rocket::serde")]
pub struct PreloadMap {
    pub id: AppId,
    pub script: String,
    pub docker_name: String,
    pub launch: Option<bool>,
    pub cgroup_map: Option<String>,
    pub port: Option<Port>,
}

#[derive(serde::Deserialize)]
struct Allocation {
    cache_in_mb: i64,
    bw_in_mbps: i64,
}

#[post("/config", format = "json", data = "<new_config>")]
async fn config(
    client: &State<Arc<Mutex<CacheClient>>>,
    new_config: Json<UpdateConfig>,
) -> status::Accepted<String> {
    println!("Got new config: {:?}", new_config.clone().into_inner());

    let mut cache_client = client.lock().await;
    let _ = cache_client.update_config(new_config.into_inner()).await;
    return status::Accepted(String::new());
}

fn precheck_config(config: &InitConfig) -> bool {
    // TODO: prechecking logic here
    return true;
}

#[rocket::main]
async fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 2 {
        println!("Usage: {} <config_path>", args[0]);
        return;
    }
    let config_path = &args[1];
    let data = fs::read_to_string(config_path)
        .expect(format!("Unable to read config file: {config_path}").as_str());
    let initial_config: InitConfig =
        serde_json::from_str(&data).expect("Unable to parse config file");
    let mut cache_client = CacheClient::mk_client();

    // Set the VM ID from the configuration if provided
    if let Some(vm_id) = initial_config.vm_id {
        println!("Setting VM ID to: {}", vm_id);
        cache_client.id = vm_id;
    } else {
        println!("Warning: No VM ID specified in configuration file. Using default ID: 0");
    }

    let cache_client = Arc::new(Mutex::new(cache_client));
    let stop = Arc::new(AtomicBool::new(false));

    // check config
    if !precheck_config(&initial_config) {
        println!("Invalid config file");
        return;
    }

    // Initialization from the provided configuration
    let _ = cache_client.lock().await.init(initial_config).await;

    // Periodic usage report
    let stop_usage: Arc<AtomicBool> = stop.clone();
    let cc_clone_usage = cache_client.clone();

    let report_usage_handle = tokio::task::spawn_blocking( move || {

        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_io()   // Enable IO driver
            .enable_time() // Enable time driver (for timers)
            .build()
            .unwrap();
        runtime.block_on(async {
            loop {
                if stop_usage.load(Ordering::SeqCst) {
                    break;
                }

                // Measure the time before calling report_usage
                let start_time = std::time::Instant::now();
                println!("{} | Start reporting usage",
                    chrono::Local::now().format("%Y-%m-%d %H:%M:%S"));

                // Acquire lock and call report_usage
                let mut client_lock = cc_clone_usage.lock().await;
                client_lock.report_usage().await;
                drop(client_lock);

                // Measure the time after report_usage
                let _elapsed = start_time.elapsed().as_millis() as u64;
                tokio::time::sleep(std::time::Duration::from_millis(RESOURCE_REPORT_INTERVAL_IN_MS)).await;
            }
        })
    });
    let report_usage_handle = Arc::new(Mutex::new(report_usage_handle));

    let figment = rocket::Config::figment().merge(("workers", 16));
    let port: u16 = figment.extract_inner::<u16>("port").unwrap_or(8000);

    precheck_port(port, RESOURCE_REPORT_INTERVAL_IN_MS,).await;

    loop {
        let stop_usage: Arc<AtomicBool> = stop.clone();
        let report_usage_handle_clone = report_usage_handle.clone();
        let server = rocket::custom(figment.clone())
            .manage(cache_client.clone())
            .mount("/", routes![config])
            .attach(AdHoc::on_shutdown("Shutdown -- compose down", move |_| {
                Box::pin(async move {
                    println!("Shutting down...");
                    stop_usage.store(true, Ordering::SeqCst);
                    let locked_handle = report_usage_handle_clone.lock().await;
                    locked_handle.abort();
                    println!("All Clear.");
                })
            }));

        let result = server.launch().await;

        if let Err(e) = result {
            let error_str = e.to_string();
            if error_str.contains("Address already in use") {
                println!("Port is already in use. Attempting to kill the process and retry...");
                precheck_port(port, RESOURCE_REPORT_INTERVAL_IN_MS,).await;
                continue; // Retry launching the server
            } else {
                println!("Failed to launch server: {:?}", e);
                break; // Exit loop on other errors
            }
        } else {
            break; // Server launched successfully
        }
    }
}
