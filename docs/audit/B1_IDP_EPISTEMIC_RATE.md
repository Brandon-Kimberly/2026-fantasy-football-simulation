# B1 — The IDP epistemic rate, measured

**2026-09-23.** Backlog item B1 asks whether `EPISTEMIC_ERROR_RATES['DL'|'LB'|'DB'] = 0.15`
is right, notes that it is a carried number rather than a measured one, and sets a
conditional: *"If the fitted rate is materially above 0.15, change `config.py` … MAJOR tag.
If the fitted rate is near 0.15, close as measured-and-cleared and leave it."*

**Result: measured-and-cleared. The constant does not move — and the evidence points the
opposite way from the item's own hypothesis.** B1 argued the rate is too LOW, so the model
"learns about quarterbacks and does not learn about linebackers." On held-out prediction
0.15 is already **looser than optimal** for all three IDP positions. Raising it would make
predictions worse.

---

## What was measured, and on what

2025 weekly stat lines for every NFL player, from Sleeper's public stats feed, **scored
under this league's own current settings**. 18 unfiltered calls, ~2,360 rows each.

**Scoring basis, and a correction to B1's method.** B1's trap says *"Fit on 2025 data
(pre-change scoring) and note the boundary."* That was written while F49's sack cut was
pending. **It landed 2026-09-23** (`docs/EVALUATION_BOUNDARIES.md`, boundary 1), so the
correct basis for a constant governing 2026 predictions is the *post*-cut scoring the
engine now reads — `idp_sack` 2.0, `idp_qb_hit` 0.5. That is simpler, not harder: there is
no boundary to straddle, only 2025 stat lines re-scored under today's rules.

**Population.** Top 24 per position by season total, matching the engine's own replacement
level (the 24th-best at a position). The 8-team league starts one DL, one LB and one DB and
rosters 34 IDP in total. Sensitivity to this choice is reported below rather than assumed
away — it matters.

**Positions are normalised through `normalize_position`**, so DE/DT fold into DL, OLB/ILB
into LB, CB/S into DB. Phase 3 finding 3 is exactly the bug of not doing this.

---

## Measurement 1 — variance components (Phase 7's instrument)

The offensive rates came from "survey measurement 2" in `AUDIT_PHASE_7_FINDINGS.md`:
between-player variance of season means, minus the within-player sampling term, over the
population mean. Reusing that instrument is what makes an IDP number comparable to the
rates already shipped.

| pos | n | gms | mean | sd_obs | sd_true | **fitted** | config | ratio config/fitted |
|---|---|---|---|---|---|---|---|---|
| QB | 24 | 12.2 | 17.73 | 2.90 | 1.86 | 0.105 | 0.30 | 2.9× |
| RB | 24 | 12.8 | 14.37 | 3.86 | 3.16 | 0.220 | 0.63 | 2.9× |
| WR | 24 | 12.3 | 13.00 | 2.63 | 1.58 | 0.121 | 0.55 | 4.5× |
| TE | 24 | 11.7 | 9.15 | 2.29 | 1.41 | 0.154 | 0.50 | 3.2× |
| K | 24 | 12.5 | 10.67 | 1.64 | 0.75 | 0.071 | 0.40 | 5.7× |
| **DL** | 24 | 12.7 | 8.79 | 2.18 | 1.35 | **0.153** | 0.15 | **1.0×** |
| **LB** | 24 | 12.6 | 12.51 | 1.37 | 0.00 | **degenerate** | 0.15 | — |
| **DB** | 24 | 12.9 | 10.15 | 0.90 | 0.00 | **degenerate** | 0.15 | — |

"Degenerate" means the sampling term exceeded the observed spread: at the startable
population, **the data cannot distinguish the top LBs and DBs from one another at all.**
That is reported rather than clamped into a small positive number that would read like a
measurement.

**The raw fit says DL 0.153 against a shipped 0.15 — B1's "near 0.15" branch.**

**The one genuine asymmetry found.** Every offensive rate sits at 2.9–5.7× its own fitted
value; DL sits at 1.0×. So the IDP rate was set on a different convention. That looked like
grounds to raise it — until measurement 2, and it is *not* grounds on its own: Phase 7
records the offensive rates as **tuned with `backtest_player`**, with the ≈2× being an
*observed property of the tuned values*, not a derivation. There is no principled multiplier
to transfer.

---

## Measurement 2 — held-out prediction (B1's stated acceptance)

B1 asks for *"the epistemic rate that maximises held-out predictive likelihood"*. Fit on
weeks 1–7, predict weeks 8–14, mean squared error (the Gaussian predictive likelihood up to
a constant at fixed observation variance). The prior is the **leave-one-out positional
mean** of the training half — look-ahead-safe, and the same peer-prior device Phase 7 used,
because no stored 2025 preseason projection exists (Sleeper 404s on both URL forms).

| pos | n | 0.05 | 0.10 | 0.15 | 0.20 | 0.30 | 0.45 | 0.63 | 1.00 | best | config |
|---|---|---|---|---|---|---|---|---|---|---|---|
| QB | 23 | 70.52 | 69.69 | 68.89 | 68.36 | **68.05** | 68.15 | 68.21 | 68.34 | 0.30 | **0.30** |
| RB | 24 | 76.46 | 76.29 | 76.14 | 75.99 | 75.53 | 74.73 | 74.13 | **73.97** | 1.00 | 0.63 |
| WR | 22 | 52.71 | 52.52 | 52.31 | **52.17** | 52.20 | 52.74 | 53.44 | 54.30 | 0.20 | 0.55 |
| TE | 20 | 29.23 | 29.07 | 28.88 | 28.71 | **28.55** | 28.60 | 28.74 | 28.79 | 0.30 | 0.50 |
| K | 24 | 26.11 | 25.96 | 25.82 | 25.71 | **25.58** | 25.60 | 25.65 | 25.72 | 0.30 | 0.40 |
| **DL** | 24 | **44.01** | 44.07 | 44.19 | 44.38 | 44.83 | 45.42 | 45.73 | 45.74 | **0.05** | 0.15 |
| **LB** | 24 | **28.72** | 28.91 | 29.19 | 29.50 | 30.00 | 30.28 | 30.36 | 30.44 | **0.05** | 0.15 |
| **DB** | 24 | **21.92** | 22.14 | 22.47 | 22.84 | 23.55 | 24.16 | 24.42 | 24.47 | **0.05** | 0.15 |

**QB's held-out optimum is 0.30, exactly its shipped rate.** That is the check that the
method reproduces how the offensive rates were tuned, and it passes on the position whose
rate is best established.

**All three IDP positions are monotone increasing in the rate** — every step looser
predicts worse. Extending the scan downward (0.005, 0.02) keeps falling to the floor, so
the optimum is at or below 0.05.

---

## Robustness

Best held-out rate by population size:

| pos | K=16 | K=24 | K=32 | K=48 |
|---|---|---|---|---|
| DL | 0.05 | 0.05 | 0.05 | 0.30 |
| LB | 0.05 | 0.05 | 0.05 | 0.30 |
| DB | 0.05 | 0.05 | 0.05 | 0.05 |
| QB | 0.20 | 0.30 | 0.30 | 0.30 |
| RB | 1.00 | 1.00 | 1.00 | 1.00 |

Stable at every startable population (K = 16–32). At K = 48 DL and LB jump to 0.30 —
because a pool twice the size of the league's startable set contains genuinely worse
players, so there is real spread to learn. **K = 48 is not the population this constant
governs**, and the jump is recorded precisely so nobody reads the 0.05 as universal.

---

## Why the answer is what it is

Because startable defenders really do cluster. Top-24 season means, 2025:

| pos | range | ratio |
|---|---|---|
| LB | 10.46 → 15.37 | 1.47× |
| DB | 8.77 → 11.92 | 1.36× |
| DL | 6.65 → 15.19 | **2.28×** |

Tackle volume is role-determined: every full-time linebacker gets broadly similar
opportunity, so their true weekly means sit close together and a stiff prior is *correct*.
DL is the exception — edge rushers separate from rotational linemen — which is why DL is the
only IDP position with a measurable true spread.

**So B1's Devin Lloyd example inverts.** A two-game sample of 16.5 and 41.5 drawn from a
population whose true spread is ~1.4 points is overwhelmingly noise. The posterior barely
moving is the right response, not a failure to learn.

**Lumping DL with LB and DB at one constant is the residual question**, not the value of
that constant. DL behaves like an offensive position (`sd_obs / sqrt(sampling)` = 1.27,
against QB 1.30 and WR 1.25); LB is 0.86 and DB 0.69, both *below* the noise floor. Splitting
them is not justified by this data either — DL's fitted 0.153 is already its shipped 0.15.

---

## What was NOT done, and why

**No constant changed, so no goldens regenerated and no MAJOR tag.** B1 makes both
conditional on a material move, and there is none.

**No adoption attempted on the strength of a fit alone.** Phase 7 built, gated and
**reverted** exactly that: the joint change to its own fitted values was worse on the points
backtest in both configurations tested. A fitted number is evidence for a decision, not the
decision.

**The offensive rates were not touched**, though this measurement says RB wants 1.00 against
a shipped 0.63 and WR wants 0.20 against 0.55. That is outside B1's scope, it is the pair
Phase 7 already tried and reverted, and changing offensive rates is a much larger MAJOR than
the one B1 authorised. **Recorded here as a live open question, not acted on.**

**One caveat on n.** Every row is n ≈ 20–24 players over 13 weeks. The IDP conclusion is
consistent across the whole population range that matters and across both instruments, but
these are not large samples, and a second season would be the right time to re-run it.

---

## Status

**CLOSED — measured and cleared.** `EPISTEMIC_ERROR_RATES['DL'|'LB'|'DB']` stays at **0.15**,
and its `config.py` comment now cites this entry. The rate is no longer a carried number: it
is a measured one that happens to land where it already was, on the loose side of optimal.
