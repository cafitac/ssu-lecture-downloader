//! The per-user LaunchAgent that runs `<app> --check-reminders` every 30 minutes, so reminders
//! also arrive while the app is closed. Only this one plist is ever written or removed.

use crate::reminders::{write_atomic, CHECK_ARG};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

pub const LABEL: &str = "com.local.ssu-lecture-downloader.reminders";
pub const START_INTERVAL_SECS: u32 = 1800;

pub fn plist_path(home: &Path) -> PathBuf {
    home.join("Library")
        .join("LaunchAgents")
        .join(format!("{LABEL}.plist"))
}

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

pub fn render_plist(exe: &Path) -> String {
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>{arg}</string>
    </array>
    <key>StartInterval</key>
    <integer>{interval}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
"#,
        label = LABEL,
        exe = xml_escape(&exe.to_string_lossy()),
        arg = CHECK_ARG,
        interval = START_INTERVAL_SECS,
    )
}

/// Whether `exe` is the binary of an installed, packaged `.app` that is safe to point a
/// LaunchAgent at: not a debug build, not a mounted disk image and not a translocated copy.
pub fn is_packaged_exe(exe: &Path) -> bool {
    let text = exe.to_string_lossy();
    text.contains(".app/Contents/MacOS/")
        && !text.contains("/target/debug/")
        && !text.starts_with("/Volumes/")
        && !text.contains("/AppTranslocation/")
}

/// True when this process may install the LaunchAgent (release build running from a `.app`).
pub fn supported() -> bool {
    cfg!(target_os = "macos")
        && !cfg!(debug_assertions)
        && std::env::current_exe()
            .map(|exe| is_packaged_exe(&exe))
            .unwrap_or(false)
}

/// `launchctl` abstracted so tests never touch the real one.
pub trait Launchctl {
    /// Runs `launchctl <args>`; true when it exited successfully.
    fn run(&self, args: &[&str]) -> bool;
}

pub struct SystemLaunchctl;

impl Launchctl for SystemLaunchctl {
    fn run(&self, args: &[&str]) -> bool {
        Command::new("/bin/launchctl")
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }
}

fn domain(uid: u32) -> String {
    format!("gui/{uid}")
}

fn target(uid: u32) -> String {
    format!("gui/{uid}/{LABEL}")
}

pub fn is_installed(home: &Path) -> bool {
    plist_path(home).is_file()
}

/// Writes the plist (only when its content differs) and loads it. If the plist had to change
/// while it was loaded, it is booted out first so launchd picks up the new executable path.
pub fn install(home: &Path, exe: &Path, uid: u32, ctl: &dyn Launchctl) -> Result<(), String> {
    let path = plist_path(home);
    let desired = render_plist(exe);
    let changed = std::fs::read_to_string(&path).ok().as_deref() != Some(desired.as_str());
    if changed {
        write_atomic(&path, desired.as_bytes())
            .map_err(|error| format!("알림 설정 파일을 만들 수 없습니다: {error}"))?;
    }
    let mut loaded = ctl.run(&["print", &target(uid)]);
    if loaded && changed {
        ctl.run(&["bootout", &target(uid)]);
        loaded = false;
    }
    if loaded {
        return Ok(());
    }
    let plist = path.to_string_lossy().to_string();
    if ctl.run(&["bootstrap", &domain(uid), &plist]) || ctl.run(&["load", "-w", &plist]) {
        Ok(())
    } else {
        Err("백그라운드 알림을 켤 수 없습니다.".to_string())
    }
}

/// Boots the agent out and deletes its plist. Nothing else in `~/Library/LaunchAgents` is touched.
pub fn uninstall(home: &Path, uid: u32, ctl: &dyn Launchctl) -> Result<(), String> {
    let path = plist_path(home);
    let plist = path.to_string_lossy().to_string();
    if !ctl.run(&["bootout", &target(uid)]) && path.is_file() {
        ctl.run(&["unload", &plist]);
    }
    match std::fs::remove_file(&path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("알림 설정 파일을 지울 수 없습니다: {error}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::fs;

    struct FakeCtl {
        calls: RefCell<Vec<String>>,
        loaded: RefCell<bool>,
        bootstrap_ok: bool,
    }

    impl FakeCtl {
        fn new(loaded: bool, bootstrap_ok: bool) -> FakeCtl {
            FakeCtl {
                calls: RefCell::new(Vec::new()),
                loaded: RefCell::new(loaded),
                bootstrap_ok,
            }
        }
        fn calls(&self) -> Vec<String> {
            self.calls.borrow().clone()
        }
    }

    impl Launchctl for FakeCtl {
        fn run(&self, args: &[&str]) -> bool {
            self.calls.borrow_mut().push(args.join(" "));
            match args[0] {
                "print" => *self.loaded.borrow(),
                "bootout" => std::mem::replace(&mut *self.loaded.borrow_mut(), false),
                "bootstrap" => {
                    if self.bootstrap_ok {
                        *self.loaded.borrow_mut() = true;
                    }
                    self.bootstrap_ok
                }
                "load" => {
                    *self.loaded.borrow_mut() = true;
                    true
                }
                _ => true,
            }
        }
    }

    fn temp_home(name: &str) -> PathBuf {
        let home = std::env::temp_dir().join(format!(
            "ssu-agent-{name}-{}-{}",
            std::process::id(),
            crate::reminders::unix_now()
        ));
        let _ = fs::remove_dir_all(&home);
        fs::create_dir_all(&home).unwrap();
        home
    }

    const EXE: &str =
        "/Applications/SSU Lecture Downloader.app/Contents/MacOS/ssu-lecture-downloader";

    #[test]
    fn plist_runs_the_given_binary_with_check_arg_every_30_minutes() {
        let plist = render_plist(Path::new(EXE));
        assert!(plist.contains(&format!("<string>{LABEL}</string>")));
        assert!(plist.contains(&format!("<string>{EXE}</string>")));
        assert!(plist.contains("<string>--check-reminders</string>"));
        assert!(plist.contains("<key>StartInterval</key>\n    <integer>1800</integer>"));
        assert!(plist.contains("<key>RunAtLoad</key>\n    <true/>"));
        assert!(
            plist.find(EXE).unwrap() < plist.find("--check-reminders").unwrap(),
            "the executable is the first program argument"
        );
    }

    #[test]
    fn plist_escapes_xml_in_the_path() {
        let plist = render_plist(Path::new("/Apps/A&B <x>.app/Contents/MacOS/bin"));
        assert!(plist.contains("/Apps/A&amp;B &lt;x&gt;.app/Contents/MacOS/bin"));
    }

    #[test]
    fn plist_lives_in_the_user_launch_agents_directory() {
        assert_eq!(
            plist_path(Path::new("/Users/me")),
            Path::new(
                "/Users/me/Library/LaunchAgents/com.local.ssu-lecture-downloader.reminders.plist"
            )
        );
    }

    #[test]
    fn only_installed_app_bundles_are_packaged() {
        assert!(is_packaged_exe(Path::new(EXE)));
        assert!(!is_packaged_exe(Path::new(
            "/Users/me/p/frontend/src-tauri/target/debug/ssu-lecture-downloader"
        )));
        assert!(!is_packaged_exe(Path::new(
            "/Users/me/p/target/debug/bundle/macos/X.app/Contents/MacOS/x"
        )));
        assert!(!is_packaged_exe(Path::new(
            "/Volumes/Disk/X.app/Contents/MacOS/x"
        )));
        assert!(!is_packaged_exe(Path::new(
            "/private/var/folders/AppTranslocation/ABC/d/X.app/Contents/MacOS/x"
        )));
    }

    #[test]
    fn install_writes_the_plist_and_bootstraps_it() {
        let home = temp_home("install");
        let ctl = FakeCtl::new(false, true);
        install(&home, Path::new(EXE), 501, &ctl).unwrap();
        let written = fs::read_to_string(plist_path(&home)).unwrap();
        assert_eq!(written, render_plist(Path::new(EXE)));
        let plist = plist_path(&home).to_string_lossy().to_string();
        assert_eq!(
            ctl.calls(),
            vec![
                format!("print gui/501/{LABEL}"),
                format!("bootstrap gui/501 {plist}"),
            ]
        );
        assert!(is_installed(&home));
        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn install_falls_back_to_load_when_bootstrap_fails() {
        let home = temp_home("fallback");
        let ctl = FakeCtl::new(false, false);
        install(&home, Path::new(EXE), 501, &ctl).unwrap();
        assert!(ctl.calls().last().unwrap().starts_with("load -w "));
        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn install_is_a_no_op_when_unchanged_and_loaded() {
        let home = temp_home("noop");
        install(&home, Path::new(EXE), 501, &FakeCtl::new(false, true)).unwrap();
        let ctl = FakeCtl::new(true, true);
        install(&home, Path::new(EXE), 501, &ctl).unwrap();
        assert_eq!(ctl.calls(), vec![format!("print gui/501/{LABEL}")]);
        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn install_rewrites_and_reloads_when_the_path_changed() {
        let home = temp_home("moved");
        install(
            &home,
            Path::new("/Old/X.app/Contents/MacOS/x"),
            501,
            &FakeCtl::new(false, true),
        )
        .unwrap();
        let ctl = FakeCtl::new(true, true);
        install(&home, Path::new(EXE), 501, &ctl).unwrap();
        assert_eq!(
            fs::read_to_string(plist_path(&home)).unwrap(),
            render_plist(Path::new(EXE))
        );
        let calls = ctl.calls();
        assert_eq!(calls[1], format!("bootout gui/501/{LABEL}"));
        assert!(calls[2].starts_with("bootstrap gui/501 "));
        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn uninstall_boots_out_and_removes_only_our_plist() {
        let home = temp_home("uninstall");
        install(&home, Path::new(EXE), 501, &FakeCtl::new(false, true)).unwrap();
        let other = home.join("Library/LaunchAgents/com.other.agent.plist");
        fs::write(&other, "other").unwrap();

        let ctl = FakeCtl::new(true, true);
        uninstall(&home, 501, &ctl).unwrap();
        assert_eq!(ctl.calls(), vec![format!("bootout gui/501/{LABEL}")]);
        assert!(!plist_path(&home).exists());
        assert!(other.exists(), "other LaunchAgents are never touched");
        assert!(!is_installed(&home));
        // Uninstalling again is fine.
        uninstall(&home, 501, &FakeCtl::new(false, true)).unwrap();
        fs::remove_dir_all(home).ok();
    }
}
