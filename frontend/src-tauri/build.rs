fn main() {
    // An app manifest makes Tauri check app commands against capabilities. That is what lets
    // the remote LMS pages of the playback window reach exactly one command (`watch_signal`,
    // capabilities/watch-remote.json) while the main window keeps the commands listed in
    // permissions/main-window-commands.toml. Permissions come from `permissions/*.toml`.
    println!("cargo:rerun-if-changed=permissions");
    println!("cargo:rerun-if-changed=capabilities");
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new()),
    )
    .expect("failed to run tauri-build");
}
