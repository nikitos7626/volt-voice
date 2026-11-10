#[cfg(windows)]
fn main() {
    let mut res = winres::WindowsResource::new();
    res.set_icon("icon.ico");
    res.set("FileDescription", "VOLT");
    res.set("ProductName", "VOLT");
    res.set("LegalCopyright", "VOLT 2026");
    if let Err(e) = res.compile() {
        eprintln!("Warning: failed to compile windows resource: {}", e);
    }
}

#[cfg(not(windows))]
fn main() {}
