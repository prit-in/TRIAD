# A failed attempt, kept as a record

This run did not finish. Its fourth condition died with `OSError: [Errno 12] Cannot allocate
memory` while listing a bind-mounted folder (`traceback.txt`), and the harness's own write of
`result.json` failed the same way and left the file empty, so it is not kept. The harness now
writes results atomically.

The re-run on 10-10-2026 (`20261009T233715Z`) died the same way in its fifth condition with no
other container running, which located the cause in the experiment's process growing across
conditions; each condition now runs in a process of its own (DEC-42). No number from either
attempt is used.
