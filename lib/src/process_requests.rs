use core::fmt;
use std::cmp::min;
use crate::VALUE_SIMULATION_THRESHOLD;

use rayon::iter::{IntoParallelIterator, ParallelIterator};
use serde::{Deserialize, Serialize};
use tokio::{
    fs::File,
    io::{AsyncBufReadExt, BufReader},
};

use crate::AppId;

pub const MAX_YCSB_REQUEST_NUM: usize = 50_000_000; // 50 m

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Default)]
pub enum DatabaseOperation {
    #[default]
    Unknown, // maybe comments
    Put,
    Update,
    Get,
    Delete,
    Scan,
    GetAndPut,
    // Ack,
}

impl fmt::Display for DatabaseOperation {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DatabaseOperation::Put => write!(f, "Put"),
            DatabaseOperation::Update => write!(f, "Update"),
            DatabaseOperation::Get => write!(f, "Get"),
            DatabaseOperation::Delete => write!(f, "Delete"),
            DatabaseOperation::Scan => write!(f, "Scan"),
            DatabaseOperation::Unknown => write!(f, "Unknown"),
            DatabaseOperation::GetAndPut => write!(f, "GetAndPut"),
        }
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct DatabaseRequest {
    pub operation: DatabaseOperation,
    pub key: String,
    pub keys: Vec<String>,
    pub key_len: u32,
    pub value: Option<String>,
    pub value_len: usize,
    pub timestamp: u64,
    pub thread_id: u16, // thread that is assigned to handle this request
    pub n_req: u64,     // number of requests in a batch
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct AppDatabaseRequest {
    pub app_id: AppId,
    pub db_req: DatabaseRequest,
}

fn parse_line(line: String, ignore_modification: bool) -> DatabaseRequest {
    const MAX_SCAN_RANGE_SIZE: usize = 100;

    let mut words = line.split_whitespace();
    let mut operation = match words.next().unwrap() {
        "READ" => DatabaseOperation::Get,
        "SCAN" => DatabaseOperation::Scan,
        "UPDATE" => DatabaseOperation::Update,
        "PUT" | "INSERT" => DatabaseOperation::Put,
        "DELETE" => DatabaseOperation::Delete,
        _ => DatabaseOperation::Unknown,    // properties and comments
    };

    if operation == DatabaseOperation::Unknown {
        return DatabaseRequest {
            operation,
            key: "DUMMY".to_string(),
            keys: vec![],
            key_len: 1,
            value: Some("DUMMY".to_string()),
            value_len: 1,
            timestamp: 0,   // dummy timestamp for non-real-trace based requests
            thread_id: 0,   // dummy id from here
            n_req: 1,       // single request
        };
    }
    let _table = words.next().unwrap();
    let key = words.next().unwrap();
    if &key[..4] != "user" {
        panic!("Invalid key: {}", key);
    }
    // convert user3951203277381243007 -> 3951203277381243007 % NUM_MAX_DATA_SIZE
    let key = key[4..].parse::<u64>().unwrap();
    let mut key_len: u32 = 1;
    let mut value_size = 0;
    let value: Option<String> = match operation {
        DatabaseOperation::Put | DatabaseOperation::Update => {
            if ignore_modification {
                // override operation; eventually remove/skip
                operation = DatabaseOperation::Unknown;
                Some(String::new())
            } else {
                // Normal YCSB workload
                let mut value = String::new();
                while let Some(word) = words.next() {
                    // generate new value since the value can have special characters that will cause issues
                    value_size += word.len();
                }
                // sharing the same output variable
                if 0 < value_size && value_size <= VALUE_SIMULATION_THRESHOLD as usize {
                    let ascii_a = 0x61u8;
                    let value_str = (0..value_size)
                        .map(|_| (ascii_a + (rand::random::<f32>() * 26.0) as u8) as char)
                        .collect::<String>();
                    value.push_str(value_str.as_str());
                }
                if value.len() > 0 {
                    Some(value) // return with value
                } else {
                    None    // return without value; value will be generated at runtime
                }
            }
        }
        DatabaseOperation::Get => None,
        DatabaseOperation::Scan => {
            key_len = 1 + rand::random::<u32>() % MAX_SCAN_RANGE_SIZE as u32;
            None
        }
        _ => None,
    };

    DatabaseRequest {
        operation,
        key: key.to_string(),
        keys: vec![],
        key_len,
        value,
        value_len: value_size,
        timestamp: 0,   // dummy timestamp for non-real-trace based requests
        thread_id: 0,   // dummy id from here
        n_req: 1,       // single request
    }
}

pub async fn parse_database_requests(filename: &str) -> Vec<DatabaseRequest> {
    let reader = BufReader::new(File::open(filename).await.unwrap());
    let mut lines = reader.lines();
    let mut db_reqs: Vec<DatabaseRequest> = vec![];
    let mut total_value_size = 0;

    println!("Reading file {}", filename);
    while let Some(line) = lines.next_line().await.unwrap() {
        let req = parse_line(line, false);
        if req.operation != DatabaseOperation::Unknown {
            total_value_size += req.value_len;
            db_reqs.push(req);
        }
        if db_reqs.len() >= MAX_YCSB_REQUEST_NUM {
            break;
        }
    }
    println!("Total requests: {}", db_reqs.len());
    println!("Total value size: {} MB", total_value_size / 1024 / 1024);

    db_reqs
}

pub async fn get_keys_from_file(filename: &str) -> Vec<String> {
    let reader = BufReader::new(File::open(filename).await.unwrap());
    let mut lines = reader.lines();
    let mut line_vec: Vec<String> = vec![];

    println!("Reading file {}", filename);
    while let Some(line) = lines.next_line().await.unwrap() {
        line_vec.push(line);
        if line_vec.len() >= MAX_YCSB_REQUEST_NUM {
            break;
        }
    }

    println!("Parsing file {}", filename);
    let mut db_reqs: Vec<DatabaseRequest> = line_vec
        .into_par_iter()
        .map(|line| parse_line(line, false))
        .collect();

    println!("Remove unknown operations");
    db_reqs.retain(|req| req.operation != DatabaseOperation::Unknown);
    db_reqs.iter().map(|req| req.key.clone()).collect()
}

pub fn update_scan_operations(db_reqs: &mut Vec<DatabaseRequest>, keys: &Vec<String>) {
    for (index, req) in db_reqs.iter_mut().enumerate() {
        if req.operation == DatabaseOperation::Scan {
            // find the range of keys to scan

            println!("keys.len: {}, index: {}", keys.len(), index);
            let range_end = min(keys.len(), index + req.key_len as usize);
            println!("Range: {} - {}", index, range_end);
            let scan_keys = keys[index..range_end].to_vec();
            req.keys = scan_keys;
        }
    }
}
