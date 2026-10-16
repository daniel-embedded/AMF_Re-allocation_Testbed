# Presentation Script — AMF Re-allocation Testbed

A ~10-minute talk track for explaining the setup and experiments, plus an
optional live-demo sequence. Adjust timings to your audience.

---

## 1. The hook (~30 s)

> "In 5G, every UE attaches to a network slice through an AMF — the core
> network function that manages mobility and sessions. Now imagine the
> operator needs to *move* UEs from one AMF or slice to another: for load
> balancing, for maintenance, or to reconfigure slices at runtime. How much
> does that move cost — and can it be done without dropping anyone's session?
> That's the question this work answers."

## 2. The testbed (~2 min)

> "We built the testbed entirely in Docker on a single PC: Open5GS v2.7.6 as
> the 5G core, UERANSIM v3.2.7 simulating the radio side — 2 gNBs and up to
> 48 UEs. The core is deployed as **two slices, each with its own AMF**.
>
> The key component is a **dual SCTP proxy** sitting between the gNBs and the
> AMFs on the N2 interface. All UE signaling passes through it, and it can
> re-route UEs from AMF1 to AMF2 at runtime — either the harsh way, by tearing
> down the SCTP associations, or the gentle way, by redirecting them.
>
> Two components needed patches: the Open5GS AMF, so it tolerates the
> non-cleartext IEs that arrive during a context transfer, and the UERANSIM
> gNB. A Python orchestrator drives the experiments and timestamps everything
> — the proxy switch, container restarts, UE log events — so latencies are
> measured, not estimated."

*(If showing a diagram, point at: UEs → gNB1/gNB2 → proxy → AMF1/AMF2, and the
N14 interface between the two AMFs.)*

## 3. The two mechanism families (~2 min)

> "We compare two families of re-allocation mechanisms.
>
> **Restart-based**: drop the SCTP association. The UE detects a radio link
> failure, reconnects, and performs a *full initial registration* at the new
> AMF. Simple and always works — but the PDU session is torn down, the UE gets
> a **new IP address**, and any state at the old AMF is lost.
>
> **N14 mobility**: the proxy redirects the UE *without* tearing anything
> down, while AMF1 transfers the UE's full context to AMF2 over the standard
> **N14 interface** — the UEContextTransfer service. The new AMF already knows
> the UE: the registration becomes a short mobility update, the session
> survives, and the **IP address is preserved**. This is the mechanism this
> work proposes and validates."

## 4. The eight experiments (~3 min)

> "Eight experiments, one question each:
>
> 1. **Sequential** — move UEs one at a time, restart-based. The conservative
>    baseline; cost grows linearly with UE count.
> 2. **Concurrent** — move everyone at once, restart-based. The realistic
>    burst scenario: how bad does the signaling storm get?
> 3. **Back-and-forth** — switch AMF1→AMF2→AMF1 repeatedly. Is the cost
>    symmetric, or does state residue accumulate?
> 4. **Partial** — move only half the UEs. What's the *blast radius* on the
>    UEs that were never targeted?
> 5. **Isolate gNB** — cut a gNB off from the core. How fast do UEs recover
>    at the radio level via RRC re-establishment?
> 6. **Split, simultaneous** — redistribute the population across both AMFs in
>    one burst.
> 7. **Split, sequential** — the same redistribution, one UE at a time, to
>    separate redistribution cost from burst cost.
> 8. **N14 mobility** — the context-transfer mechanism, tested under the same
>    correlated-burst conditions as experiment 2.
>
> Every experiment was run at 6, 12, 24, and 48 UEs, and experiment 8 was
> replicated up to 6 runs per tier for statistical confidence — we report run
> means with 95% confidence intervals."

## 5. Key results (~2 min)

> "Three findings stand out.
>
> **First, N14 mobility does not degrade with scale.** End-to-end re-allocation
> latency stays flat at roughly 2.9–3.0 seconds from 6 to 48 UEs, with tight
> confidence intervals — while restart-based concurrent degrades from 5.3 to
> 14.2 seconds, and sequential reaches about 3 minutes at 48 UEs. That's close
> to two orders of magnitude between the best and worst mechanism.
>
> **Second, the N14 latency is dominated by radio failure detection, not
> signaling.** The context transfers themselves serialize at only ~40 ms per
> UE — even at 48 UEs that's under a second of queueing, and the rank–latency
> correlation of 0.97 confirms the queue is orderly. The bottleneck is the
> ~2.5 s the UE takes to detect the radio link failure, which is independent
> of burst size.
>
> **Third, continuity is absolute.** In every N14 run, at every scale: 100%
> of UEs recovered, 100% kept their IP address, and every context transfer was
> confirmed in the AMF logs. The one caveat we measured honestly: in partial
> re-allocation, the restart-based mechanism collaterally disturbs *all*
> non-targeted UEs through the shared gNB — they keep their IPs, but they do
> re-register. N14 has no such blast radius."

## 6. The takeaway (~30 s)

> "The message is simple: context-transfer mobility decouples the cost of AMF
> re-allocation from the size of the UE burst. If your slice orchestration
> ever needs to move UEs at scale, restart-based mechanisms charge you
> linearly and break sessions; N14 charges you a constant and preserves them."

---

## Anticipated questions

- **"Why is N14 flat while concurrent grows?"** — End-to-end latency =
  RLF detection (constant, ~2.5 s) + signaling. Restart-based signaling grows
  with contention; N14 signaling only queues politely at ~40 ms/transfer.
- **"Is 2.9 s acceptable?"** — It's bounded by 3GPP RLF timers, not by our
  mechanism; tuning T310/N310 would shrink it for all mechanisms equally.
- **"What breaks first beyond 48 UEs?"** — The proxy CPU, estimated around
  90–100 UEs; the SMF's /24 IP pool caps near 250. Host RAM/CPU were never
  close to limiting.
- **"Is this standard-compliant?"** — N14 UEContextTransfer is 3GPP TS 29.518;
  the AMF patch only relaxes IE-strictness on the receiving side.

---

## Optional live demo (~3 min)

```bash
# 1. Start the network with 12 UEs (phased startup, waits for registration)
./start_network.sh 12

# 2. Run one experiment live — N14 mobility is the most impressive
python3 Python_scripts/amf_orchestrator_v7.py --mode n14-mobility --ues 12

# 3. Show the structured output
cat results/<latest>/08_n14-mobility/amf_realloc.json | python3 -m json.tool | head -50

# 4. Generate and show the figures
./generate_all_plots_v3.sh results/<latest>
# open results/<latest>/plots/08_n14-mobility/ip_rotation.png
#   → every UE in the green "Changed? = No" column: sessions survived
```

Point out in the JSON: `ip_changed: false`, `session_continuity: true`,
`n14_context_transfer: true` — the three flags that tell the whole story.
