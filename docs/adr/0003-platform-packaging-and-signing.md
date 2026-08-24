# ADR-0003 (O-06): Supported OS/architecture and packaging/signing/update approach

**Status:** Proposed — direction only, final minimum-version numbers deferred
to Contributor decision.
**Decision needed by:** Milestone 0 exit (PRD gate table).
**Related PRD items:** D-02, §12.1, §12.3, O-06

## Context

The base project already ships signed/notarized builds for macOS and a
signed installer for Windows, built via PyInstaller + Inno Setup, with
ffmpeg statically bundled. This ADR's job is to extend that existing,
working pipeline to the new binary/model dependencies, not replace it.

## Minimum OS/hardware (proposed, pending Contributor confirmation)

- **macOS:** inherit whatever minimum the base project already targets —
  README badge states "macOS 13+." No evidence found in G0 that this
  feature requires a higher floor; CPU (Apple Silicon `arm64` and Intel
  `x86_64`, matching the base project's existing dual-arch bundling via
  `static-ffmpeg`) should get the same treatment for the whisper.cpp binary.
- **Windows:** inherit the base project's stated "Windows 10+" floor.
  `x86_64` only unless evidence emerges that ARM64 Windows support is
  already claimed elsewhere (none found in G0).
- **RAM:** the actual floor depends on G3's `base.en`/`small.en` memory
  benchmarks (ADR-0001, still open) — this ADR cannot set a number until
  that measurement exists. Flagging the dependency rather than guessing.

## Installer/package design

Extend the existing artifacts directly:
- `m4bmaker.spec` (macOS PyInstaller spec) — add the whisper.cpp binary as
  an additional bundled data file, same mechanism `static-ffmpeg`'s
  PyInstaller hook already uses (confirmed present in `requirements-dev.txt`
  and referenced in `utils.py`'s `_which()` MEIPASS-scanning logic).
- `m4bmaker-windows.spec` — same treatment for the Windows `.exe` binary.
- `installer.iss` (Inno Setup) — extend to include the new bundled binary;
  no new installer framework needed.
- Model files themselves are **not** bundled in the installer (D-11 — they
  are downloaded post-install), keeping installer size close to today's
  baseline; only the engine binary adds to base install size.

## Signing/notarization

- Windows: same code-signing requirement the project already documents
  (exact certificate/process is a Contributor-owned secret/credential
  concern, out of scope for this ADR to specify further).
- macOS: same notarization flow already documented in `RELEASING.md`
  (named keychain profile) — the new bundled binary must be signed with
  the same signing identity and included in the same notarization
  submission as the rest of the `.app` bundle, or Gatekeeper will reject
  the whole bundle at launch on end-user machines. This is a real risk to
  flag explicitly: a forgotten unsigned helper binary inside an otherwise
  notarized bundle is a common, easy-to-miss notarization failure mode.

## Update integrity

- The existing app has a GitHub-Releases-API update *checker* only
  (`gui/updater.py`, per the README's documented single outbound request)
  — it does not auto-download or auto-apply updates today. No evidence in
  G0 of an auto-update mechanism to extend. Recommend keeping that scope
  boundary for this feature too: update checking stays as-is, no new
  auto-update capability is introduced by this ADR.
- Model updates (a *new* concept this feature introduces) must use HTTPS +
  checksum verification + atomic staging, per PRD §10.1 — this is
  functionally a new, narrower "update" surface than an app auto-updater
  and should not be conflated with one in the UI or the code.

## Offline installation behavior

- App installation itself has no network dependency today (ffmpeg is
  bundled) and this ADR proposes the same for the whisper.cpp engine
  binary — only the *model* download requires network, exactly as D-11
  already specifies. This keeps "offline after model install" a clean,
  single well-defined boundary rather than several partial ones.

## Open items for Contributor decision

1. Exact minimum macOS/Windows versions and whether they should be raised
   from the base project's current floor (no evidence found that they
   need to be — recommend keeping them unchanged unless G3/G4 benchmarking
   finds a hard dependency on a newer OS API).
2. ~~Whether GPU-accelerated whisper.cpp builds are worth the
   packaging/testing-matrix cost for v1~~ — **resolved by the ADR-0001 G3
   spike**: `--no-gpu` is a plain runtime flag on the standard whisper.cpp
   build, not a separate binary. There is no separate CPU-only build to
   source or maintain — every platform ships the one standard build and
   the app always passes `--no-gpu` at invocation time per the product
   owner's CPU-only-for-v1 decision. This removes an entire axis from the
   packaging/testing matrix that this ADR originally worried about.
