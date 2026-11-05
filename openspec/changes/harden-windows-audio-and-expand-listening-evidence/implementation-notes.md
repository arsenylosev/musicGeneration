# Implementation verification notes

## Windows CI group

- Equivalent clean native run ID: `bb4fb51962c64969bb20afaa5ac6a03e`.
- Source revision before this checkpoint: `fb7c0a252648761560d54f9f1b044db91aa78ad4`.
- Wheel SHA-256: `2686f1f0188290a96c8cee257e83431dce631268c5ef0182ed3623bbd2561d45`.
- Result-record SHA-256: `ceedafbdb34a54cee02357ba7997ea29d88ba4ca2cbce98de696dae5f09732e6`.
- Result: 56 installed-wheel tests run under a non-administrator, non-elevated medium-integrity token; 55 passed and one symlink case was narrowly unavailable with `WinError 1314`. Mandatory substitute invariants passed, the asset environment was clean, and no unexpected skip occurred.
- The local run used a new virtual environment in an external path containing spaces and non-ASCII characters. A hosted `windows-latest` run remains required before relying on GitHub-specific account/ACL behavior.

## Linux CI-equivalent group

- Run ID: `wsl-ubuntu-20261105-dd690b020da0`.
- Full unittest log SHA-256: `dd690b020da06d11c7b451d72fdd13a4ec31b9084efe4a85f066f14ca9ecc783`.
- `flake8 aimusic tests ui.py`: passed.
- `mypy`: 159 source files passed.
- Compile/import checks: 68 executable modules imported.
- Unit discovery: 825 passed, 43 skipped in 442.716 seconds.
- ML job targets: 2 passed, 7 dependency/capability skips.

These are local equivalent runs, not hosted GitHub run identifiers. No organization PR or workflow dispatch was performed.
