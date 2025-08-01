// build.rs
use std::env;

fn main() {
    // 1. Where your .a files live
    let manifest_dir = env::var("CARGO_MANIFEST_DIR")
        .expect("CARGO_MANIFEST_DIR not set");

    // 2. Re-run build script if these change
    println!("cargo:rerun-if-changed={}/libTraceWrapper.a", manifest_dir);
    println!("cargo:rerun-if-changed={}/liblibCacheSim.a", manifest_dir);

    // 3. Tell rustc where to look for native libraries
    println!("cargo:rustc-link-search=native={}", manifest_dir);
    println!("cargo:rustc-link-search=native=/usr/local/lib");

    // 4. Link the static archives
    //    -l static=TraceWrapper   →  libTraceWrapper.a
    //    -l static=libCacheSim    →  liblibCacheSim.a
    println!("cargo:rustc-link-lib=static=TraceWrapper");
    println!("cargo:rustc-link-lib=static=libCacheSim");

    // 5. Link the dynamic libraries
    println!("cargo:rustc-link-lib=dylib=glib-2.0");
    println!("cargo:rustc-link-lib=dylib=zstd");
    println!("cargo:rustc-link-lib=dylib=stdc++");
}