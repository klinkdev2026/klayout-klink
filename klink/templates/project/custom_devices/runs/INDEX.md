# runs — the project ledger

A "run" is one task: its driver code, its artifacts, and its record
live together in one folder `runs/<YYYY-MM-DD>_<slug>/` — never split
across the project. Start one with `klink run new <slug>`; it
registers itself below. When a run finishes, replace its
"(in progress)" with a one-line summary of what the folder does,
plus PASS/FAIL of its verification. One line per run, newest on top.

<!-- newest first -->
