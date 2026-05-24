"""Golden-path smoke (Plan 0016).

`golden_path.py` is a runnable driver (no `test_` prefix → never collected by
`pytest tests/`) that drives one end-to-end golden path against the live stack
started by `pnpm dev:all`. `test_golden_path_helpers.py` is the offline,
network-free unit coverage of the driver's pure helpers and IS collected.
"""
