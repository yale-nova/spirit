use libc::{c_void, c_int, c_char};
use std::ffi::{CStr, CString};

#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReqOpE {
    Nop = 0,
    Get = 1,
    Gets = 2,
    Set = 3,
    Add = 4,
    Cas = 5,
    Replace = 6,
    Append = 7,
    Prepend = 8,
    Delete = 9,
    Incr = 10,
    Decr = 11,
    Read,
    Write,
    Update,
    Invalid,
}

#[repr(C)]
pub struct ObjIdT {
    // Assuming representation details based on your description (simplified here)
    pub id: u64,
}

#[repr(C)]
pub struct Request {
    pub clock_time: u64, // use u64 because vscsi uses microsecond timestamp
    pub hv: u64, // hash value, used when offloading hash to reader
    pub obj_id: u64,
    pub obj_size: i64,
    pub ttl: i32,
    pub op: ReqOpE,

    pub n_req: u64,
    pub next_access_vtime: i64,

    pub eviction_algo_data: *mut c_void,

    pub kv_data: KVdata,

    pub ns: i32,
    pub content_type: i32,
    pub tenant_id: i32,

    pub bucket_id: i32,
    pub age: i32,
    pub hostname: i32,
    pub extension: i16,
    pub colo: i16,
    pub n_level: i16,
    pub n_param: i16,
    pub method: i8,

    pub vtime_since_last_access: i64,
    pub rtime_since_last_access: i64,
    pub prev_size: i64, // previous size
    pub create_rtime: i32,
    pub compulsory_miss: bool,
    pub overwrite: bool,
    pub first_seen_in_window: bool,

    pub valid: u8, // indicate whether request is a valid request
}

#[repr(C)]
pub struct KVdata {
    pub key_size: u32, // TODO: 16 bits size
    pub value_size: u32, // TODO: 48 bits size
}

impl Request {
    // You might want to add methods here to help with managing or interpreting the data
    pub fn new() -> Self {
        Self {
            clock_time: 0,
            hv: 0,
            obj_id: 0,
            obj_size: 0,
            ttl: 0,
            op: ReqOpE::Nop,
            n_req: 0,
            next_access_vtime: 0,
            eviction_algo_data: std::ptr::null_mut(),
            kv_data: KVdata {
                key_size: 0,
                value_size: 0,
            },
            ns: 0,
            content_type: 0,
            tenant_id: 0,
            bucket_id: 0,
            age: 0,
            hostname: 0,
            extension: 0,
            colo: 0,
            n_level: 0,
            n_param: 0,
            method: 0,
            vtime_since_last_access: 0,
            rtime_since_last_access: 0,
            prev_size: 0,
            create_rtime: 0,
            compulsory_miss: false,
            overwrite: false,
            first_seen_in_window: false,
            valid: 0,
        }
    }
}

// Library functions
extern "C" {
    fn open_trace_oracle(path: *const libc::c_char) -> i32;
    fn get_next_request(reader_idx: i32) -> Request;
    fn close_trace_wrapper(reader_idx: i32) -> bool;
}

pub fn open_trace_oracle_rs(path: &str) -> i32 {
    let c_path = CString::new(path).expect("CString::new failed");
    unsafe {
        open_trace_oracle(c_path.as_ptr())
    }
}

pub fn get_next_request_rs(reader_idx: i32) -> Option<Request> {
    unsafe {
        let req: Request = get_next_request(reader_idx);
        if req.valid == 0 {
            None
        } else {
            Some(req)
        }
    }
}

pub fn close_trace_rs(reader_idx: i32) -> bool {
    unsafe {
        close_trace_wrapper(reader_idx)
    }
}
