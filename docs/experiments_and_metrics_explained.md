# Experiments and Metrics — Explained

Reference document for the AMF re-allocation testbed thesis: what each of the
eight experiments does, and what each recorded metric means.

---

## The eight experiments

### Exp 1 — Sequential re-allocation (restart-based)
UEs are moved from AMF1 to AMF2 one at a time by tearing down and
re-establishing each UE's SCTP association through the proxy. This is the
conservative baseline: it avoids signaling storms but serializes all recovery,
so total cost grows linearly with the number of UEs. It establishes the upper
bound on re-allocation time.

### Exp 2 — Concurrent re-allocation (restart-based)
All UEs are switched simultaneously, triggering a correlated burst of
re-registrations at AMF2. This is the realistic "slice reconfiguration"
scenario and exposes how the restart-based approach degrades under burst load
(signaling congestion, radio contention) as the UE count grows.

### Exp 3 — Back-and-forth re-allocation
The concurrent switch is executed AMF1→AMF2, then AMF2→AMF1, then repeated.
It verifies that re-allocation cost is symmetric between the two AMFs — i.e.,
that no directional bias, state residue, or hysteresis accumulates across
repeated switches.

### Exp 4 — Partial re-allocation
Only a subset of UEs (the first half of the pool) is re-allocated; the rest
are supposed to be undisturbed. This measures the *blast radius* of a
re-allocation: because the restart-based mechanism resets the shared gNB,
non-moving UEs can suffer collateral radio link failures and be forced into
re-registration even though they were never targeted.

### Exp 5 — gNB isolation
A gNB is isolated from the core, and UEs must recover at the access level via
RRC re-establishment rather than full re-registration. This isolates the
radio-recovery component of mobility and quantifies how fast UEs return when
the core context itself is not the problem.

### Exp 6 — Split, simultaneous
The UE population is divided across AMF1 and AMF2 in one simultaneous action.
This tests load redistribution between slices/AMFs under burst conditions —
the multi-AMF analogue of Exp 2.

### Exp 7 — Split, sequential
The same population split as Exp 6, but performed one UE at a time. Comparing
it against Exp 6 separates the cost of the redistribution itself from the cost
of doing it under a correlated burst.

### Exp 8 — N14 mobility (context transfer)
The proxy redirects UEs to AMF2 *without* tearing down their sessions, while
AMF1 transfers each UE's context to AMF2 over the N14 interface
(UEContextTransfer / transfer-update). Because the AMF receives the full
context, UEs keep their PDU session and IP address — this is the
seamless-mobility mechanism the thesis proposes and evaluates against the
restart-based baselines.

---

## The metrics

### Per-UE metrics (recorded for every UE in every run)

- **Recovered (recovery success)** — whether the UE returned to a fully
  registered, connected state after the experiment trigger. The fundamental
  correctness criterion.
- **Re-allocation latency (ms)** — end-to-end time from the experiment trigger
  until the UE's recovery completes. The primary cost metric; reported as run
  means with 95% confidence intervals across runs.
- **Recovery type** — *how* the UE came back:
  - `re-registration` — full NAS registration, new IP;
  - `rrc_reestablishment` — access-level recovery, context intact;
  - `collateral_reregistration` / `collateral_rrc_activity` — disturbed but
    not targeted (partial mode only).
- **IP changed / IP preservation** — whether the UE kept its PDU-session IP
  address across the re-allocation. Direct indicator of user-plane session
  continuity.
- **Mobility registration flag** — whether the post-switch registration was a
  mobility (context-preserving) registration rather than a fresh initial
  registration.
- **Session continuity flag** — composite indicator that the UE's session
  survived the switch (context transferred, IP preserved, no re-establishment
  of the PDU session).
- **N14 context transfer flag** — whether UEContextTransfer evidence was
  observed in the AMF logs (trigger-anchored, timestamp-parsed scan of the AMF
  stderr log): `transferStatus: TRANSFERRED` followed by
  `regStatusTransferComplete: true`.
- **Registration-exchange latency (ms)** — duration of the UE's own
  registration signaling exchange, as opposed to the end-to-end latency.
  Separating the two exposes the N14 queueing effect: per-transfer
  serialization (~40 ms/transfer) inflates this metric with burst size while
  end-to-end latency stays flat, because RLF detection time dominates the
  latter.

### Aggregate (per-run) metrics

- **Recovered count / success rate** — fraction of UEs recovered; 100% at all
  tiers in the final campaigns.
- **Collateral disturbed count** — number of non-targeted UEs disturbed in
  partial mode; quantifies the blast radius (all non-movers were disturbed at
  every tier, though all kept their IPs).
- **Run-mean latency and 95% CI** — the mean of per-UE latencies within a run,
  then averaged across runs with a Student-t 95% confidence interval
  (n = number of runs). Per-UE pooled values are used only for
  distribution-shape analysis (e.g., the queueing ramp).
