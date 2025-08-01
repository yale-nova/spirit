use crate::process_requests::{DatabaseOperation, DatabaseRequest};
use crate::{VALUE_SIMULATION_THRESHOLD, precompute_random_string, get_random_string, PRECOMPUTED_SIZE};

use trace_loader::{Request, open_trace_oracle_rs, get_next_request_rs, close_trace_rs};
use std::io::{self, Write};
use std::cmp::min;
use std::collections::HashMap;
use serde::Serialize;

const REPORT_REQUEST_NUM: usize = 10_000_000;
const MAX_REQUEST_NUM: usize = REPORT_REQUEST_NUM * 10; // 100 million in total (ref. Memshare microbench setup)
const MAX_IDLE_TIME: u64 = 10;   // 10 second
const MAX_REPEATED_REQ: u64 = 1;   // 3 times
pub const TRACE_REPLAY_DUPLICATION_RATIO: u64 = 1;  // 1 for YCSB; 100 for Meta, original traffic ratio was 1/100 so make it back to 1
pub const TRACE_REPLAY_TIMING_RATIO: u64 = 100;  // original traffic ratio was 1/100 so make it back to 1

// 500 MB; Redis limit is 512 MiB for all string values
// Ref: https://stackoverflow.com/a/5624569
// Due to the Redis crate's memory management, we limit the value size to 64 MiB
pub const MAX_VALUE_SIZE: usize = 512 * 1024 * 1024;
pub const MAX_TOTAL_VALUE_SIZE: usize = 200 * 1024 * 1024 * 1024;   // we can support 200 GB x 2 or 100 GB x 4 large value working sets
pub const VALUE_SIZE_SCALE: usize = 1;  // 1x or higher scale to enlarge the value size

// Note) parse_cachesim_requests() shrinks the inter value time
// -- Example --
// => 2 becomes 2 - 1 = 1
//
// 5 - 1 = 4 > 1 + 1
// off = 5 - 1 - 1 = 3
// ---
// => 5 becomes 5 - 3 = 2
//
// 11 - 3 = 8 > 2 + 1
// off = 11 - 2 - 1 = 8
// ---
// => 11 becomes 11 - 8 = 3
//
// 20 - 8 = 12 > 3 + 1
// off = 20 - 3 - 1 -> 16
// ---
// => 20 becomes 20 - 16 = 4

// Structures for JSON output.
#[derive(Serialize)]
struct HistogramBucket {
    bucket: u32,
    num_keys: u32,
}

#[derive(Serialize)]
struct GraphBucket {
    bucket: u32,
    total_bytes: usize,
}

pub async fn parse_cachesim_requests(filename: &str) -> Vec<DatabaseRequest> {
    let mut db_reqs: Vec<DatabaseRequest> = vec![];

    // Initialize per-key tracking maps for access count and total accessed bytes.
    println!("Reading file {}", filename);
    let reader_idx = open_trace_oracle_rs(filename);
    if reader_idx < 0 {
        println!("Failed to open trace: {}", filename);
        return db_reqs;
    }

    // Generate the large precomputed random string.
    let large_random_string = precompute_random_string(PRECOMPUTED_SIZE);

    // Start parsing requests.
    let mut total_requests = 0;
    let mut last_req_time: u64 = 0;
    let mut time_offset: u64 = 0;
    let mut total_value_size: usize = 0;
    let mut kv_tracker = HashMap::new();

    loop {
        let req = get_next_request_rs(reader_idx);
        match req {
            Some(req) if req.valid != 0 => {
                let mut new_req = generate_dbrequest(
                    &req,
                    &large_random_string,
                    &mut total_value_size,
                    &mut kv_tracker,
                );

                let new_req_time = new_req.timestamp;
                if new_req_time - time_offset > last_req_time + MAX_IDLE_TIME {
                    time_offset = new_req_time - last_req_time - MAX_IDLE_TIME;
                }
                new_req.timestamp -= time_offset;
                last_req_time = new_req.timestamp;
                db_reqs.push(new_req);
            }
            Some(_) => {
                println!("Invalid request");
                break;
            }
            None => break,
        }
        if total_requests == 0 && !db_reqs.is_empty() {
            println!("First request in {}: {:?}", filename, db_reqs[0]);
        }
        total_requests += 1;
        if total_requests >= MAX_REQUEST_NUM {
            break;
        }
        if total_value_size >= MAX_TOTAL_VALUE_SIZE {
            break;
        }
        if total_requests % REPORT_REQUEST_NUM == 0 {
            println!("Read {} reqs", total_requests);
            io::stdout().flush().unwrap();
        }
    }
    println!(
        "Read {} reqs ({} MB) in total... Closing opened trace file.",
        total_requests,
        total_value_size / 1024 / 1024
    );
    io::stdout().flush().unwrap();
    close_trace_rs(reader_idx);

    db_reqs
}

fn generate_dbrequest(req: &Request, precompute_str: &String, total_value: &mut usize, kv_tracker: &mut HashMap<String, usize>) -> DatabaseRequest {
    let operation = DatabaseOperation::GetAndPut;
    let key = req.obj_id.to_string();
    let key_len: u32 = 1;   // single request for cachesim
    // Genune values
    let value_len = min(MAX_VALUE_SIZE, req.obj_size as usize * VALUE_SIZE_SCALE);
    // let ascii_a = 0x61u8;
    let mut value = None;
    if 0 < value_len && value_len <= VALUE_SIMULATION_THRESHOLD as usize {
        let value_str = get_random_string(precompute_str, value_len);
        value = Some(value_str);
    }
    // check key is in the tracker
    if kv_tracker.contains_key(&key) {
        let kv_data = kv_tracker.get(&key).unwrap();
        if *kv_data != value_len {
            if kv_data < &value_len {
                *total_value += value_len - kv_data;
            } else {
                *total_value -= kv_data - value_len;
            }
            kv_tracker.insert(key.clone(), value_len);
        }
    } else {
        kv_tracker.insert(key.clone(), value_len);
        *total_value += value_len;
    }

    DatabaseRequest {
        operation,
        key,
        keys: vec![],
        key_len,
        value,
        value_len,
        timestamp: req.clock_time / TRACE_REPLAY_TIMING_RATIO,
        thread_id: 0,
        n_req: std::cmp::min(MAX_REPEATED_REQ,
            std::cmp::max(1, req.n_req)),
    }
}
