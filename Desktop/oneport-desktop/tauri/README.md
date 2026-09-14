# OnePort Desktop (Tauri)

The native desktop build of the OnePort Security Cockpit. It wraps the **exact
same UI** the web version serves (`../ui/index.html`) and a Rust backend that is
a 1:1 port of the verified Python engine (`oneport_mcp/gates.py`) — same gate
registry, same exit-code contract, same honest classification (a missing tool or
login is a visible SKIP; a tool that errors is an ERROR, never a fake pass).

Nothing here talks to the network. Gates run locally against a repo on disk.

---

## ⚠️ Verification status (read this first)

The Rust in `src-tauri/src/main.rs` was **written but NOT compiled** in the
environment it was authored in — that machine had no Rust toolchain. It mirrors
the Python engine line-for-line, but *until you run `cargo build` below and see
the app open, treat it as unverified.* If the first build throws a compile
error, that's expected scaffolding friction, not a design problem — send the
error and it gets fixed to green.

The **web version** (`../server.py` + `../ui/index.html`) *is* verified and runs
today; use it while the native build is being brought up.

---

## Prerequisites (one-time)

The OnePort CLIs it drives (`oneport-secrets`, `oneport-depcheck`,
`oneport-migrate`, `oneport-apidiff`, `oneport-testgap`, `oneport-review`) must
be installed and on `PATH`. Then, to *build* the installer you need:

### Windows
1. **Rust** — https://rustup.rs (run `rustup-init.exe`, accept defaults).
2. **Microsoft C++ Build Tools** — the MSVC linker Tauri links against.
   Install "Desktop development with C++" from the Visual Studio Build Tools:
   ```
   winget install Microsoft.VisualStudio.2022.BuildTools --override "--quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
   ```
3. **WebView2** — already present on Windows 11 (the app runtime).
4. **Node.js** — for the Tauri CLI (`@tauri-apps/cli`).

### macOS
1. **Rust** — https://rustup.rs
2. **Xcode Command Line Tools** — `xcode-select --install`
3. **Node.js**

### Linux
1. **Rust**, **Node.js**, and the webkit2gtk / librsvg dev packages
   (see https://tauri.app/start/prerequisites/).

## Build

```bash
cd tauri
npm install
npm run build      # = tauri build  → produces the installer
```

Output:
- **Windows**: `src-tauri/target/release/bundle/nsis/OnePort_0.1.0_x64-setup.exe`
  (and an `.msi` under `bundle/msi/`)
- **macOS**: `src-tauri/target/release/bundle/dmg/OnePort_0.1.0_*.dmg`
- **Linux**: `.AppImage` / `.deb` under `src-tauri/target/release/bundle/`

## Develop (hot window, no installer)

```bash
cd tauri
npm install
npm run dev        # = tauri dev
```

## Layout

```
tauri/
  package.json            # Tauri CLI + scripts
  src-tauri/
    Cargo.toml            # Rust deps (tauri, serde, which, wait-timeout)
    build.rs
    tauri.conf.json       # window + bundle config; frontendDist -> ../../ui
    capabilities/
      default.json        # grants the window core:default
    icons/                # generated (shield + check)
    src/main.rs           # the two commands: list_gates_status, run_gate
  ../ui/index.html        # the cockpit (shared with the web server)
```

The frontend auto-detects its backend: inside this app it calls
`window.__TAURI__.core.invoke(...)`; served by `../server.py` it calls the HTTP
API. One UI, two backends — so the cockpit you see in the browser is exactly the
cockpit that ships in the installer.

## Code signing (before public distribution)

Unsigned installers trip SmartScreen (Windows) and Gatekeeper (macOS). For a
security product that matters — sign before you hand out download links:
- Windows: an Authenticode cert (`bundle.windows.certificateThumbprint` in
  `tauri.conf.json`, or the `signtool` env vars).
- macOS: an Apple Developer ID + notarization.
Ship unsigned only for your own testing.
