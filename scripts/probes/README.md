# R1 probes (AUDIT_PLAN.md, Reproducibility watch)

Re-test for the machine-level fault recorded as R1. Run from the project root with
`PYTHONPATH=.`; all three enable `faulthandler`.

- `probe_pure.py SECONDS` -- stdlib only. Six of these concurrently (`Start-Process` x6, 240 s)
  is Arm D: it must pass 6/6 on both interpreters before R1 can be closed. Any access violation,
  `SystemError`, or "sort order broken" assertion is the fault.
- `probe_mixed.py ROUNDS` -- the native combination present at the original crash: the real
  `_solve_optimal_assignment` (scipy `linear_sum_assignment`) plus the exhaustive brute force,
  interleaved with real matplotlib/seaborn rendering and pandas. 300 rounds ~ 3 min.
- `stress_lsa.py` -- 300k direct assignment calls, single process (was clean on both stacks).

Findings that led here: single processes never failed; concurrent CPU-heavy processes failed
with a different corrupted object each time, on Python 3.8 and 3.10, with and without BLAS
threads, with and without bytecode caching, and with no native extension loaded at all.
