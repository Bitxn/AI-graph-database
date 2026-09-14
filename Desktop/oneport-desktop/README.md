<!--
  ┌─────────────────────────────────────────────────────────────────────────┐
  │  ADDING SCREENSHOTS (recommended — makes the repo look 10× better):       │
  │  1. Create a folder named  assets  in the repo root.                       │
  │  2. Drop in these PNGs (any width ~1400px looks great):                    │
  │       assets/studio.png   → OnePort Studio with a project open            │
  │                             (the Gates & History tab, verdict visible)     │
  │       assets/change.png   → the Live Change Intelligence panel            │
  │       assets/demo.gif     → a short clip from your launch video (optional) │
  │  3. Delete the <!-- --> around the matching block below to show it.        │
  │  The logo below loads from your live site, so it always renders.           │
  └─────────────────────────────────────────────────────────────────────────┘
-->

<div align="center">

<img src="https://www.oneport.co.in/icon-512.png" alt="OnePort" width="88" height="88" />

<h1>OnePort</h1>

<p><b>The gate between writing code and shipping it.</b></p>

<p>Your AI agents write the code. OnePort audits it — <b>locally</b> — before it reaches production.<br/>
Secrets · CVEs · migrations · breaking APIs · reviews · test gaps&nbsp; → &nbsp;<b>one verdict.</b></p>

<p>
  <a href="https://github.com/Bitxn/Oneport-Desktop/releases/latest/download/OnePort-Setup.exe">
    <img src="https://img.shields.io/badge/Download%20for%20Windows-018C37?style=for-the-badge&logo=windows&logoColor=white" alt="Download for Windows" />
  </a>
  &nbsp;
  <a href="https://github.com/Bitxn/Oneport-Desktop">
    <img src="https://img.shields.io/badge/Star%20this%20repo-24292F?style=for-the-badge&logo=github&logoColor=white" alt="Star this repo" />
  </a>
</p>

<p>
  <a href="https://github.com/Bitxn/Oneport-Desktop/releases"><img src="https://img.shields.io/github/v/release/Bitxn/Oneport-Desktop?color=018C37&label=release" alt="release" /></a>
  <a href="https://github.com/Bitxn/Oneport-Desktop/releases"><img src="https://img.shields.io/github/downloads/Bitxn/Oneport-Desktop/total?color=018C37" alt="downloads" /></a>
  <a href="https://github.com/Bitxn/Oneport-Desktop/stargazers"><img src="https://img.shields.io/github/stars/Bitxn/Oneport-Desktop?color=018C37" alt="stars" /></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-018C37" alt="platform" />
  <img src="https://img.shields.io/badge/runs-100%25%20local-018C37" alt="runs locally" />
</p>

<p>
  <a href="https://oneport.co.in"><b>Website</b></a> &nbsp;·&nbsp;
  <a href="https://oneport.co.in/docs"><b>Docs</b></a> &nbsp;·&nbsp;
  <a href="https://oneport.co.in/botspace"><b>Tools</b></a> &nbsp;·&nbsp;
  <a href="https://github.com/Bitxn/Oneport-Desktop/releases"><b>Releases</b></a>
</p>

</div>

<!-- ⬇️ HERO SCREENSHOT — add assets/studio.png then delete these comment markers:
<div align="center">
  <img src="assets/studio.png" alt="OnePort Studio" width="860" />
</div>
-->

---

## The problem

AI writes code **fast**. It also — silently — commits API keys, pulls vulnerable dependencies, breaks the function three other teams import, ships a migration that can't be rolled back, and skips the test that would've caught all of it.

You find out in production. At 2 a.m. On a Friday.

**OnePort is the checkpoint between "the agent wrote it" and "it's live."** Point it at a repo and it runs every pre-ship gate on your machine, then gives you one answer you can trust:

<div align="center">

### `READY` &nbsp;·&nbsp; `INCONCLUSIVE` &nbsp;·&nbsp; `BLOCKED`

</div>

---

## See it run

```console
$ op ship .

  scanning working tree + 1,204 commits…

  ✓ Secret & .env scan          clean
  ✗ Dependency CVEs (OSV)        1 high — requests 2.19.1 (CVE-2023-32681)
  ✓ Migration safety             safe, reversible
  ✓ Code review                  no blocking issues
  ✗ API breaking changes         charge(amount) → charge()  [removed param]
  ! Test coverage gaps           pay() untested

  ─────────────────────────────────────────────
  VERDICT: BLOCKED — 1 CVE, 1 breaking change   (exit 1)
```

> The exit code is non-zero on a block, so the same command guards your terminal **and** your CI.

<!-- ⬇️ LIVE CHANGE INTELLIGENCE SCREENSHOT — add assets/change.png then uncomment:
<div align="center">
  <img src="assets/change.png" alt="Live Change Intelligence" width="860" />
</div>
-->

---

## Install

### 🖥️ Desktop — OnePort Studio (recommended)

**[⬇ Download OnePort-Setup.exe](https://github.com/Bitxn/Oneport-Desktop/releases/latest/download/OnePort-Setup.exe)**
Windows 10/11 · self-contained (all tools bundled) · requires [Git for Windows](https://git-scm.com/download/win).

> Windows may show a SmartScreen notice because OnePort is a new publisher → click **More info → Run anyway**. Verify the download against the SHA-256 on each [release](https://github.com/Bitxn/Oneport-Desktop/releases).

### ⌨️ CLI

```bash
pip install oneport      # Python 3.9+
op ship                  # run every pre-ship gate, from any repo root
```

---

## What makes it different

|  | OnePort |
|---|---|
| 🔒 **Runs on your machine** | Every gate is local. Your code and findings never leave your computer. |
| 🎯 **One verdict, not a dashboard** | Six+ gates collapse to a single `READY` / `BLOCKED` with an exit code CI enforces. |
| 🤖 **Built for AI-written code** | Catches exactly what agents get wrong — leaked keys, unsafe deps, broken contracts. |
| 🧠 **Calibrated, not noisy** | Blocks only on *real* risk. AWS's own `AKIA…EXAMPLE` placeholder is a review, not a block — so a BLOCK is worth trusting. |
| 🛡️ **Enforcement, not advice** | OnePort Guard makes a repo *physically unable* to commit a secret. |

---

## ⚡ Live Change Intelligence

Turn on **Watch** and every save is analysed the instant it lands. For each change, in plain language:

- **What changed** — files, ± lines, and the risky areas it touches (auth, config, DB, deps, CI, payments).
- **Repercussions** — what this kind of change can break, and what to re-test.
- **Did it add errors?** — computed from the *delta* between the scan before and after your change, plus a deterministic check for broken/stray code. It names real regressions and can't cry wolf.
- **How to fix it** — concrete steps, a **ready-to-paste prompt** for your AI editor, and the exact **files to attach**.

---

## 🛡️ OnePort Guard

Guard turns OnePort from a check you *remember* to run into enforcement that just happens:

```bash
op guard install
```

- **Git hook** — blocks any commit that stages a secret.
- **Agent hook** — gates edits made by your AI editor, in its write path.
- **CI gate** — a workflow + status badge that fails the build on a leak.

The result: a repo that **can't ship a secret**, by construction.

---

## The toolbox — every tool, one CLI

<table>
<tr>
  <th align="left">🔎 Understand</th>
  <th align="left">✅ Check</th>
  <th align="left">⚙️ Operate</th>
</tr>
<tr valign="top">
<td>

`context` — live repo map<br/>
`docgen` — docs, kept in sync

</td>
<td>

`secrets` — leaks (tree + history)<br/>
`depcheck` — CVEs via OSV<br/>
`migrate` — unsafe migrations<br/>
`apidiff` — breaking API changes<br/>
`apiwatch` — endpoint health<br/>
`review` — AI code review<br/>
`testgap` — untested paths<br/>
`conformance` — intent drift

</td>
<td>

`impact` — blast radius<br/>
`debtpilot` — technical debt<br/>
`costwatch` — cost impact<br/>
`upgrade` — safe upgrade plans<br/>
`standup` — commits → standup<br/>
`postmortem` — incident drafts<br/>
`evidence` — SOC2 / ISO packs<br/>
`debug` — diagnose an error

</td>
</tr>
</table>

> 👉 Browse what each tool checks, with live demos, at **[oneport.co.in/botspace](https://oneport.co.in/botspace)**.

---

## Inside the desktop app

**OnePort Studio** attaches to any repo and gives you a cockpit:

- **Gates & History** — run Quick scan or the full production check; every result kept.
- **Live Change Intelligence** — the panel above, updating on every save.
- **Mind Map** — an AI map of the repo, kept current as code changes.
- **Chat** — ask *"is it safe?"*, *"what changed?"*, or *"how do I fix this?"* — it takes real actions.
- **Guard · Tech Debt · Reports · Terminal** — every tool, one click away.

---

## CI integration

The CLI exits non-zero on a blocking verdict, so it drops into any pipeline:

```yaml
- name: OnePort pre-ship gate
  run: |
    pip install oneport
    op ship --fail-on block
```

A risky change never merges — the job fails first.

---

## 🔒 Security & privacy

- **Local execution.** Gates run entirely on your machine. Source code and findings are **never uploaded**.
- **Minimal telemetry.** The app sends only an event name, your account email, and the app version — to measure usage. Never code, repos, or results.
- **Managed AI, no keys.** AI features run through OnePort's managed engine; you hold no API keys, so there's nothing to leak from the client.
- **History-aware.** The secret scanner checks the full git history — a secret committed once and "deleted" is still caught.

---

## Roadmap

- [x] Windows desktop (OnePort Studio)
- [x] OnePort Guard (git + agent + CI enforcement)
- [x] Live Change Intelligence with fix prompts
- [ ] macOS & Linux builds
- [ ] Code-signed installer (no SmartScreen prompt)
- [ ] Team policy sync (shared gates)
- [ ] VS Code / Cursor extension

> Have a request? [Open an issue](https://github.com/Bitxn/Oneport-Desktop/issues) — and tell us what almost shipped that OnePort should have caught.

---

<div align="center">

## ⭐ If OnePort catches even one bad ship for you, star the repo.

It's the single biggest thing you can do to help — it's how other developers find it.

<a href="https://github.com/Bitxn/Oneport-Desktop">
  <img src="https://img.shields.io/github/stars/Bitxn/Oneport-Desktop?style=social" alt="Star OnePort" />
</a>

<br/><br/>

<a href="https://oneport.co.in"><b>Website</b></a> &nbsp;·&nbsp;
<a href="https://oneport.co.in/docs"><b>Docs</b></a> &nbsp;·&nbsp;
<a href="https://github.com/Bitxn/Oneport-Desktop/releases/latest"><b>Download</b></a> &nbsp;·&nbsp;
<a href="https://github.com/Bitxn/Oneport-Desktop/issues"><b>Report an issue</b></a>

<sub>Built by an indie developer who got tired of finding out in production. · © 2026 OnePort</sub>

</div>
