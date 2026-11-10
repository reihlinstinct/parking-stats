# Process-cleanup test polling

The descendant timeout test polls `/proc/PID/stat` after cleanup. A missing
file means the process was reaped and cleanup succeeded. A zombie also counts
as stopped; sleeping or running processes do not. Read the file directly and
handle `FileNotFoundError`, rather than checking existence first: the process
can be reaped between the existence check and the read. Other read errors
still fail the test. Deterministic tests cover missing, zombie and live states.

This is a test-only change. Production process-group cleanup is unchanged.
