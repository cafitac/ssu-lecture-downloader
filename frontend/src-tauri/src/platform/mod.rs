//! The platform seam: everything OS-specific the shared shell code needs, behind one
//! cfg-selected API (design/02-domain-model.puml, `PlatformShell`).
//!
//! - `macos.rs` (unix targets): the existing macOS bodies, moved verbatim.
//! - `windows.rs`: compile-only stubs ("not supported yet"). Real Windows adapters come in S2.
//!
//! Shared code calls `crate::platform::<fn>` and never uses `libc`, `launchctl`, `osascript` or
//! `open` directly. Both modules expose the same names and signatures.

#[cfg(unix)]
mod macos;
#[cfg(unix)]
pub(crate) use macos::*;

#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub(crate) use windows::*;
