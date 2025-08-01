mod global_allocation;

use std::env;
use std::sync::Arc;

use rocket::fairing::AdHoc;
use rocket::response::status;
use rocket::serde::json::Json;
use rocket::State;

use global_allocation::GlobalAllocation;
use lib::commands::{send_config, shut_down};
use lib::{UpdateConfig, UsageMap};

use tokio::sync::Mutex;
use std::collections::BTreeMap;
use chrono;

#[macro_use]
extern crate rocket;

#[get("/status")]
async fn cluster_status(global_alloc: &State<Arc<Mutex<GlobalAllocation>>>) -> String {
    let mut usage_map = global_alloc.lock().await.clone().usage_map;
    sort_usage_map(&mut usage_map);
    serde_json::to_string(&usage_map).unwrap_or("".to_string())
}

fn sort_usage_map(usage_map: &mut UsageMap) {
    if let Some(map) = usage_map.map.get_mut(&0) {
        let mut sorted = BTreeMap::new();
        for (k, v) in map.iter() {
            sorted.insert(*k, v.clone());
        }
        *map = sorted.into_iter().collect();
    }
}

#[post("/config", format = "json", data = "<new_config>")]
async fn config(
    global_alloc: &State<Arc<Mutex<GlobalAllocation>>>,
    new_config: Json<UpdateConfig>,
) -> status::Accepted<String> {
    println!("Got new config: {:?}", new_config.clone().into_inner());

    let mut global_alloc_lock = global_alloc.lock().await;

    // allocation hashmap: key: vm id, value: list of app's allocation
    let local_allocs = global_alloc_lock.split_global_allocation(&new_config);
    global_alloc_lock.cur_config = new_config.into_inner();

    for (local_name, local_config) in local_allocs {
        let dest_ip = global_alloc_lock.vm_ip_map.get(&local_name).unwrap();
        send_config(&local_config, dest_ip).await;
    }

    status::Accepted(String::new())
}

#[post("/usage", format = "json", data = "<client_usage_map>")]
async fn usage(
    global_alloc: &State<Arc<Mutex<GlobalAllocation>>>,
    client_usage_map: Json<UsageMap>,
) {
    global_alloc
        .lock()
        .await
        .usage_map
        .map
        .extend(client_usage_map.map.clone());

    println!(
        "{} | Got new usage map: {:?}",
        chrono::Local::now().format("%Y-%m-%d %H:%M:%S"),
        client_usage_map.clone().into_inner()
    );
    //TODO: check if usage_map contains allocations for all client_ids
    //  adjust allocations if needed
}

#[rocket::main]
async fn main() {
    let args: Vec<String> = env::args().collect();
    let config_path = &args[1];
    let mut global_allocation = GlobalAllocation::new(config_path);
    let backend_db_proc = global_allocation.init().await;
    let global_allocation_arc = Arc::new(Mutex::new(global_allocation));

    // let mut backend_db_proc_vec = vec![];
    // if backend_db_proc.is_some() {
    //     backend_db_proc_vec.push(backend_db_proc.unwrap());
    // }

    rocket::build()
        .manage(global_allocation_arc)
        .mount("/", routes![config, usage, cluster_status])
        .attach(AdHoc::on_shutdown("Shutdown -- compose down", |_| {
            println!("Shutting down...");
            Box::pin(async move { shut_down(backend_db_proc).await })
        }))
        .launch()
        .await
        .unwrap();
}
