# This run's "partial" is the harness misreading its retrain

The first run on the data that arrived (DEC-48). Drift was flagged (`monetary_90d`, PSI 6.54), the
retrain run-2026-10-11-022 started on exactly the three new months, and the champion's promotion
waited for its two approvals. The retrain then escalated on AP-6 with `NO_LIFT_OVER_BASELINE`: it
was compared with the champion, showed no significant lift, and so was neither eligible for an
override nor promoted - the platform working as specified.

The harness read the comparison only from the prepare state, which a candidate that escalates
without being eligible for an override does not write, so it recorded the challenger as never
having met the champion (`challenger_compared_with_champion = False`, `challenger_selection =
None`) and the chain as untested. The acceptance decision, which every path through selection
records, also lacked the difference AP-8 judged.

The fixes (DEC-49): that decision carries `champion_delta`, and EXP-7 reads the comparison from
it. EXP-7 was then re-run; this run is kept as the record of what happened. Its detector arms are
unaffected.
