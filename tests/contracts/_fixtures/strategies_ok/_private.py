"""Underscore-prefixed module: discover() must skip without importing META."""

# Intentionally has no META at the module level — if discover() were to
# accidentally import this module, the test would still pass (no META → skip),
# so we add the side-effect below to catch it: a marker attribute that a test
# can assert is *not* in the result.
SHOULD_NOT_BE_DISCOVERED = True
