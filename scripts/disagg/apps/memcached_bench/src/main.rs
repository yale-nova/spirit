use memcache::Client;
use csv::Reader;
use rayon::ThreadPoolBuilder;
use std::fs::File;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use serde::Deserialize;
use glob::glob;
use rand::Rng;
use rand::distributions::Alphanumeric;
use clap::{Arg, App};
use std::collections::HashSet;
use std::time::{Duration, Instant};
use indicatif::{ProgressBar, ProgressStyle, MultiProgress};
use std::io::BufReader;
use std::thread;

fn collect_keys_and_sizes(memcached_server: &str, trace_directory: &str) {
    // let keys_and_sizes = Arc::new(Mutex::new(HashSet::new()));

    let files: Vec<_> = glob(format!("{}/*.csv", trace_directory).as_str()).expect("Failed to read glob pattern")
        .filter_map(Result::ok)
        .collect();

    let m = MultiProgress::new();
    let style = ProgressStyle::default_spinner()
            .template("{spinner:.green} {wide_msg} {pos}/{len} ({eta})").expect("pb style error");
    let handles: Vec<_> = files.into_iter().map(|path| {
        let pb = m.add(ProgressBar::new_spinner());
        pb.set_style(style.clone());
        pb.set_message(format!("Processing {:?}", path));

        // let keys_and_sizes = Arc::clone(&keys_and_sizes);
        let memcached_server = memcached_server.to_string();
        let handle = thread::spawn(move || {
            let client = Client::connect(memcached_server).expect("Failed to connect to Memcached server");
            let file = File::open(&path).expect(&format!("Cannot open file {:?}", path));
            let mut rdr = Reader::from_reader(file);
            for result in rdr.deserialize::<Record>() {
                let record = result.expect("Error deserializing record");
                if record.op != "SET" {
                    continue;
                }
                let key = format!("{:0>width$}", record.key, width = record.key_size);
                let value: String = rand::thread_rng()
                    .sample_iter(&Alphanumeric)
                    .take(record.size)
                    .map(char::from)
                    .collect();
                client.set(&key, &value, 0).unwrap();
                pb.tick();
            }
            pb.finish_with_message(format!("Finished {:?}", path));
        });

        handle
    }).collect();

    for handle in handles {
        handle.join().unwrap();
    }

    // Arc::try_unwrap(keys_and_sizes).unwrap().into_inner().unwrap()
}

fn print_throughput(message_count: Arc<AtomicUsize>, message_bytes: Arc<AtomicUsize>, interval: Duration) {
    let start = Instant::now();
    let mut last_time = start;
    let mut last_count = 0;
    let mut last_bytes = 0;

    loop {
        std::thread::sleep(interval);
        let current_count = message_count.load(Ordering::SeqCst);
        let now = Instant::now();
        let elapsed = now.duration_since(last_time);
        let throughput = (current_count - last_count) as f64 / elapsed.as_secs_f64();
        let total_bytes = message_bytes.load(Ordering::SeqCst);
        let bytes_throughput = (total_bytes - last_bytes) as f64 / elapsed.as_secs_f64();

        println!("Time elapsed: {:.2?}, Throughput: {:.2} ops/sec, {:.2} bytes/sec",
            now.duration_since(start), throughput, bytes_throughput);

        last_time = now;
        last_count = current_count;
        last_bytes = total_bytes;
    }
}

fn process_record(
    record: &Record, client: Client, 
    verbose: bool, message_count: &AtomicUsize, message_bytes: &AtomicUsize,
    scaling: usize) {
    match record.op.as_str() {
        "SET" => {
            let value: String = rand::thread_rng()
                .sample_iter(&Alphanumeric)
                .take(record.size * scaling)
                .map(char::from)
                .collect();
            let key = format!("{:0>width$}", record.key, width = record.key_size);
            client.set(&key, &value, 0).unwrap();
            message_count.fetch_add(1, Ordering::SeqCst);
            message_bytes.fetch_add(record.size * scaling, Ordering::SeqCst);
            // println!("SET key: {} value: {}", key, value);
        },
        "GET" => {
            let key: String = format!("{:0>width$}", record.key, width = record.key_size);
            let value = client.get::<String>(&key).unwrap();
            message_count.fetch_add(1, Ordering::SeqCst);
            message_bytes.fetch_add(record.size * scaling, Ordering::SeqCst);
            if verbose {
                if let Some(val) = value {
                    println!("GET key: {} value: {}", key, val);
                } else {
                    println!("GET key: {} value: None", key);
                }
            }
        },
        "DELETE" => {
            let key: String = format!("{:0>width$}", record.key, width = record.key_size);
            client.delete(&key).unwrap();
            message_count.fetch_add(1, Ordering::SeqCst);
            message_bytes.fetch_add(record.size * scaling, Ordering::SeqCst);
            if verbose {
                println!("DELETE key: {}", key);
            }
        },
        _ => {
            println!("Unknown operation: {}", record.op);
            unimplemented!()
        },
    }
}

fn main() {
    let matches = App::new("Memcached Benchmark")
        .version("1.0")
        .author("Your Name <your.email@example.com>")
        .about("Processes CSV files to benchmark Memcached operations")
        .arg(Arg::with_name("verbose")
            .short("v")
            .long("verbose")
            .help("Increases logging verbosity"))
        .arg(Arg::with_name("ip")
            .long("ip")
            .takes_value(true)
            .help("IP address of the Memcached server"))
        .arg(Arg::with_name("trace-dir")
            .long("trace-dir")
            .takes_value(true)
            .help("Directory path for log files"))
        .arg(Arg::with_name("preload")
            .long("preload")
            .help("Preload keys and values into Memcached"))
        .get_matches();

    let verbose = matches.is_present("verbose");
    let memcached_server = matches.value_of("ip").unwrap_or("memcache://10.10.11.73:8001");
    let trace_directory = matches.value_of("trace-dir").unwrap_or("./data");
    let preload = matches.is_present("preload");

    println!("Verbose: {}", verbose);
    println!("Using Memcached server at: {}", memcached_server);
    println!("Logging to directory: {}", trace_directory);

    // Collect keys and their sizes from CSV files
    if preload {
        collect_keys_and_sizes(memcached_server, trace_directory);
    }

    // Start the periodic throughput printing in a separate thread
    let message_count = Arc::new(AtomicUsize::new(0));
    let message_count_clone = Arc::clone(&message_count);
    let message_count_summary = Arc::clone(&message_count);
    let message_bytes = Arc::new(AtomicUsize::new(0));
    let message_bytes_clone = Arc::clone(&message_bytes);
    std::thread::spawn(move || {
        print_throughput(message_count_clone, message_bytes_clone, Duration::from_secs(1));
    });

    let files = glob(format!("{}/*.csv", trace_directory).as_str()).expect("Failed to read glob pattern")
        .filter_map(Result::ok)
        .collect::<Vec<_>>();
    let num_connections = 4;
    let pool = ThreadPoolBuilder::new().num_threads(num_connections).build().unwrap();

    let clients: Arc<Mutex<Vec<_>>> = Arc::new(Mutex::new((0..num_connections)
    .map(|_| Client::connect(memcached_server).unwrap())
    .collect()));
    let client = Client::connect(memcached_server).unwrap();

    for path in files {
        let file = File::open(&path).expect("Cannot open file");
        let reader = BufReader::new(file);
        let mut rdr = Reader::from_reader(reader);
        for result in rdr.deserialize::<Record>() {
            let record = result.expect("Error deserializing record");
            // let clients = Arc::clone(&clients);
            let message_count = message_count.clone();
            let message_bytes = message_bytes.clone();
            let client = client.clone();
            pool.install(move || {
                // let mut clients = clients.lock().unwrap();
                // let client = clients.pop().unwrap();
                let client = client.clone();
                for _ in 0..record.op_count {
                    process_record(&record, client.clone(), verbose, &message_count, &message_bytes, 10);
                }
                // clients.push(client);
            });
        }
    }
    println!("Total messages sent: {}", message_count_summary.load(Ordering::SeqCst));
}

#[derive(Debug, Deserialize)]
struct Record {
    key: String,
    op: String,
    size: usize,
    op_count: usize,
    key_size: usize,
}
