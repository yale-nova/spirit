use futures::Future;
use lib::commands::{preload_benchmarks, start_backend_instance};
use lib::{AppId, UpdateConfig, UsageMap, VmId};
use rocket::serde::json::serde_json;
use rocket::serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fs;
use tokio::process::Child;

const BACKEND_PORT: u64 = 6379;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(crate = "rocket::serde")]
pub struct GlobalAllocation {
    #[serde(skip)]
    pub cur_config: UpdateConfig,
    #[serde(skip)]
    pub usage_map: UsageMap,
    pub vm_ip_map: HashMap<VmId, String>,
    pub placement_map: HashMap<AppId, Vec<VmId>>,
    pub id_preload_map: HashMap<VmId, String>,
    run_backend_cmd_map: Option<HashMap<AppId, String>>,
}

impl GlobalAllocation {
    pub fn new(config_file: &str) -> Self {
        let data = fs::read_to_string(config_file).expect("Unable to read config file");
        serde_json::from_str::<GlobalAllocation>(&data).expect("Failed to parse global config")
    }

    pub fn init<'a>(&'a mut self) -> impl Future<Output = Vec<Child>> + Send + 'a {
        async move {
            let mut backend_handles = vec![];
            if self.run_backend_cmd_map.is_none() {
                return backend_handles;
            }

            for (app_id, cmd) in self.run_backend_cmd_map.as_ref().unwrap() {
                if cmd.is_empty() {
                    continue;
                }
                println!("Starting backend for app_id: {}", app_id);
                let handle = start_backend_instance(cmd);
                backend_handles.push(handle);
            }

            // Preload benchmarks (if exists)
            let unique_benchmarks = self.id_preload_map.values().collect::<HashSet<_>>();
            preload_benchmarks(unique_benchmarks).await;
            backend_handles
        }
    }

    pub fn split_global_allocation(&self, cfg: &UpdateConfig) -> HashMap<VmId, UpdateConfig> {
        let mut local_allocs = HashMap::new();

        for app_id in cfg.allocation_map.keys() {
            let (mut mem_alloc, mut bw_alloc) = cfg.allocation_map.get(app_id).unwrap();
            let app_id_vms = self.placement_map.get(app_id).unwrap();

            mem_alloc /= app_id_vms.len() as u64;
            bw_alloc /= app_id_vms.len() as u64;

            for vm in app_id_vms {
                // println!("Allocating app {} to vm {}", app_id, vm);
                if local_allocs.contains_key(vm) {
                    let new_config: &mut UpdateConfig = local_allocs.get_mut(vm).unwrap();
                    new_config
                        .allocation_map
                        .insert(*app_id, (mem_alloc, bw_alloc));
                } else {
                    let mut new_config = UpdateConfig {
                        allocation_map: HashMap::new(),
                        ..cfg.clone()
                    };
                    new_config
                        .allocation_map
                        .insert(*app_id, (mem_alloc, bw_alloc));
                    local_allocs.insert(*vm, new_config);
                }
            }
        }

        local_allocs
    }
}
