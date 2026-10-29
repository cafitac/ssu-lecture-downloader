//! Runs the app's real capabilities (compiled in by `tauri_build` from `capabilities/` and
//! `permissions/`) against a mock-runtime app: which window and page origin may call which
//! command. These are the guarantees behind the watch script's IPC signalling.

use crate::watch_runtime::WATCH_LABEL_PREFIX;
use std::collections::HashMap;
use tauri::ipc::{CallbackFn, InvokeBody};
use tauri::test::{get_ipc_response, mock_builder, MockRuntime, INVOKE_KEY};
use tauri::webview::InvokeRequest;
use tauri::{App, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

// Stand-ins with the real commands' names (the real ones need the Wry runtime). Which command
// may run is decided by the ACL before any handler is reached, and the names are all it uses.
#[tauri::command]
fn watch_signal(_kind: String, _payload: HashMap<String, String>) -> &'static str {
    "signalled"
}
#[tauri::command]
fn real_download_dir() -> &'static str {
    "dir"
}
#[tauri::command]
fn open_url_in_browser(_url: String) -> &'static str {
    "opened"
}

#[tauri::command]
fn update_install_guard() -> &'static str {
    "guard"
}
#[tauri::command]
fn prepare_for_update() -> &'static str {
    "prepared"
}

fn app() -> App<MockRuntime> {
    mock_builder()
        .invoke_handler(tauri::generate_handler![
            watch_signal,
            real_download_dir,
            open_url_in_browser,
            update_install_guard,
            prepare_for_update
        ])
        .build(crate::app_context())
        .expect("app builds with the real capabilities")
}

fn window(app: &App<MockRuntime>, label: &str, url: WebviewUrl) -> WebviewWindow<MockRuntime> {
    WebviewWindowBuilder::new(app, label, url)
        .build()
        .expect("window")
}

fn remote(url: &str) -> WebviewUrl {
    WebviewUrl::External(url.parse().expect("url"))
}

fn call(
    window: &WebviewWindow<MockRuntime>,
    origin: &str,
    cmd: &str,
    body: serde_json::Value,
) -> Result<String, String> {
    let url = if origin == "local" {
        "tauri://localhost".to_string()
    } else {
        origin.to_string()
    };
    get_ipc_response(
        window,
        InvokeRequest {
            cmd: cmd.into(),
            callback: CallbackFn(0),
            error: CallbackFn(1),
            url: url.parse().expect("request url"),
            body: InvokeBody::Json(body),
            headers: Default::default(),
            invoke_key: INVOKE_KEY.to_string(),
        },
    )
    .map(|body| body.deserialize::<String>().expect("string result"))
    .map_err(|error| error.to_string())
}

fn signal_body() -> serde_json::Value {
    serde_json::json!({ "kind": "progress", "payload": { "time": "3", "duration": "450" } })
}

#[test]
fn only_the_watch_window_s_ssu_pages_may_send_watch_signals() {
    let app = app();
    let watch = window(
        &app,
        &format!("{WATCH_LABEL_PREFIX}-1"),
        remote("https://canvas.ssu.ac.kr/courses/1/modules/items/2"),
    );
    for origin in [
        "https://canvas.ssu.ac.kr/courses/47625/modules/items/3410470",
        "https://commons.ssu.ac.kr/em/abc",
        "https://ssu.ac.kr/",
    ] {
        assert_eq!(
            call(&watch, origin, "watch_signal", signal_body()),
            Ok("signalled".to_string()),
            "{origin}"
        );
    }
    // Any other site in that window is refused, as are look-alike hosts.
    for origin in [
        "https://evil.example/",
        "https://ssu.ac.kr.evil.example/",
        "https://evilssu.ac.kr/",
        "http://canvas.ssu.ac.kr/",
    ] {
        assert!(
            call(&watch, origin, "watch_signal", signal_body()).is_err(),
            "{origin} must not reach watch_signal"
        );
    }
}

#[test]
fn the_watch_pages_reach_nothing_but_the_signal_command() {
    let app = app();
    let watch = window(
        &app,
        &format!("{WATCH_LABEL_PREFIX}-7"),
        remote("https://canvas.ssu.ac.kr/"),
    );
    let origin = "https://canvas.ssu.ac.kr/courses/1";
    for (cmd, body) in [
        ("real_download_dir", serde_json::json!({})),
        (
            "open_url_in_browser",
            serde_json::json!({ "url": "https://x.example" }),
        ),
        ("plugin:dialog|open", serde_json::json!({ "options": {} })),
        (
            "plugin:event|listen",
            serde_json::json!({ "event": "x", "handler": 1 }),
        ),
        (
            "plugin:webview|create_webview_window",
            serde_json::json!({ "options": {} }),
        ),
    ] {
        assert!(
            call(&watch, origin, cmd, body).is_err(),
            "{cmd} must not be reachable from the LMS page"
        );
    }
}

#[test]
fn other_windows_cannot_send_watch_signals_even_from_ssu_pages() {
    let app = app();
    let lms_page = window(&app, "lms-page-1", remote("https://canvas.ssu.ac.kr/"));
    let viewer = window(
        &app,
        "official-viewer-1",
        remote("https://canvas.ssu.ac.kr/"),
    );
    for page in [&lms_page, &viewer] {
        assert!(
            call(
                page,
                "https://canvas.ssu.ac.kr/courses/1",
                "watch_signal",
                signal_body()
            )
            .is_err(),
            "{} must not reach watch_signal",
            page.label()
        );
        assert!(call(
            page,
            "https://canvas.ssu.ac.kr/courses/1",
            "real_download_dir",
            serde_json::json!({})
        )
        .is_err());
    }
}

#[test]
fn the_main_window_keeps_its_commands_but_has_no_watch_signal() {
    let app = app();
    let main = window(&app, "main", WebviewUrl::default());
    assert_eq!(
        call(&main, "local", "real_download_dir", serde_json::json!({})),
        Ok("dir".to_string())
    );
    assert_eq!(
        call(
            &main,
            "local",
            "open_url_in_browser",
            serde_json::json!({ "url": "https://x.example" })
        ),
        Ok("opened".to_string())
    );
    assert!(
        call(&main, "local", "watch_signal", signal_body()).is_err(),
        "the signal command belongs to the playback window's pages only"
    );
}

/// Command names registered in `generate_handler![...]` of `lib.rs` (last path segment).
fn registered_commands() -> Vec<String> {
    let source = include_str!("lib.rs");
    let production = source
        .split("#[cfg(test)]\nmod tests")
        .next()
        .expect("production");
    let start = production
        .find("generate_handler![")
        .expect("invoke handler")
        + "generate_handler![".len();
    let list = &production[start..start + production[start..].find(']').expect("end of list")];
    list.split(',')
        .map(|entry| entry.trim().rsplit("::").next().unwrap_or("").to_string())
        .filter(|name| !name.is_empty())
        .collect()
}

#[test]
fn every_registered_command_has_a_permission_and_only_the_signal_is_remote() {
    let commands = registered_commands();
    assert!(commands.len() > 15, "{commands:?}");
    assert!(commands.contains(&"watch_signal".to_string()));
    let main_permissions = include_str!("../permissions/main-window-commands.toml");
    let signal_permission = include_str!("../permissions/watch-signal.toml");
    for command in &commands {
        let identifier = format!("allow-{}", command.replace('_', "-"));
        if command == "watch_signal" {
            assert!(
                signal_permission.contains(&format!("identifier = \"{identifier}\""))
                    && signal_permission.contains("commands.allow = [\"watch_signal\"]"),
                "watch_signal permission"
            );
            assert!(
                !main_permissions.contains("watch_signal")
                    && !main_permissions.contains("allow-watch-signal"),
                "the main window must not get the watch signal"
            );
            continue;
        }
        // Defined as a permission ...
        assert!(
            main_permissions.contains(&format!("identifier = \"{identifier}\""))
                && main_permissions.contains(&format!("commands.allow = [\"{command}\"]")),
            "{command} needs a permission in permissions/main-window-commands.toml"
        );
        // ... and part of the set the default capability grants to the main window.
        assert!(
            main_permissions.contains(&format!("  \"{identifier}\",")),
            "{command} must be in the main-window-commands set"
        );
    }
}

#[test]
fn the_remote_capability_is_scoped_to_the_watch_window_and_ssu_pages() {
    let capability: serde_json::Value =
        serde_json::from_str(include_str!("../capabilities/watch-remote.json")).expect("json");
    assert_eq!(
        capability["windows"],
        serde_json::json!([format!("{WATCH_LABEL_PREFIX}-*")]),
        "the window pattern follows the playback window's label prefix"
    );
    assert_eq!(capability["local"], serde_json::json!(false));
    assert_eq!(
        capability["remote"]["urls"],
        serde_json::json!(["https://*.ssu.ac.kr/*", "https://ssu.ac.kr/*"])
    );
    assert_eq!(
        capability["permissions"],
        serde_json::json!(["allow-watch-signal"]),
        "no core API and no other command for remote pages"
    );

    let default: serde_json::Value =
        serde_json::from_str(include_str!("../capabilities/default.json")).expect("json");
    assert_eq!(default["windows"], serde_json::json!(["main"]));
    assert!(
        default.get("remote").is_none(),
        "the main window is local only"
    );
}

#[test]
fn updating_is_for_the_main_window_only() {
    let default: serde_json::Value =
        serde_json::from_str(include_str!("../capabilities/default.json")).expect("json");
    let permissions = default["permissions"].as_array().expect("permissions");
    for needed in ["updater:default", "process:allow-restart"] {
        assert!(permissions.contains(&serde_json::json!(needed)), "{needed}");
    }
    assert!(
        !permissions.contains(&serde_json::json!("process:default"))
            && !permissions.contains(&serde_json::json!("process:allow-exit")),
        "the main window may restart the app, not exit it"
    );
    let remote_json = include_str!("../capabilities/watch-remote.json");
    assert!(!remote_json.contains("updater") && !remote_json.contains("process"));

    // The guard and the preparation are ordinary main-window commands (and so not remote).
    let app = app();
    let watch = window(
        &app,
        &format!("{WATCH_LABEL_PREFIX}-2"),
        remote("https://canvas.ssu.ac.kr/"),
    );
    let main = window(&app, "main", WebviewUrl::default());
    for (cmd, expected) in [
        ("update_install_guard", "guard"),
        ("prepare_for_update", "prepared"),
    ] {
        assert_eq!(
            call(&main, "local", cmd, serde_json::json!({})),
            Ok(expected.to_string())
        );
        assert!(
            call(
                &watch,
                "https://canvas.ssu.ac.kr/",
                cmd,
                serde_json::json!({})
            )
            .is_err(),
            "{cmd} must not be reachable from the LMS page"
        );
    }
}
