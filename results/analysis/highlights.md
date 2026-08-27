# CNB Experiment: Analysis Highlights & Themes

**288 LLM-vs-LLM negotiation scenarios | Sonnet 4.6 vs Sonnet 4.6 | $16.64 total API cost**

Overall mean score: **0.822** | 11 HOS violations (3.8%) | 167/288 optimal slot hits (58.0%)

---

## Theme 1: The Paradox of Small Delays

**The smallest delay (1hr) is by far the hardest scenario, not the largest.**

| Delay | Mean Score | Optimal Hit Rate | HOS Violations |
|-------|-----------|-----------------|----------------|
| Small (1hr) | **0.603** | 20.8% | 0 |
| Medium (2hr) | 0.957 | 78.1% | 3 |
| Large (4hr) | 0.906 | 75.0% | 8 |

**Why it matters:** SM-delay scenarios have the tightest OTIF window (MABD = 13:00 or 14:00), meaning only 1-2 slots avoid the $10K penalty. The agent struggles to secure these early slots against resistant personas. The score gap between SM and MD (**0.354 points**) dwarfs the MD-to-LG gap (0.051), confirming that constraint ambiguity (where multiple constraints bind simultaneously) is far more damaging than pure adversity (large delay with clear HOS pressure).

The SM+tight-MABD combination is catastrophic: **mean 0.367, median 0.057** (48 scenarios). This is effectively random performance.

---

## Theme 2: Persona Hierarchy — Operationally Constrained is Easiest, Frustrated is Hardest

| Persona | Mean Score | Optimal Hit | HOS Violations | Withdrawal Rate |
|---------|-----------|-------------|----------------|-----------------|
| Op. Constrained | **0.920** | 76.4% | 1 (1.4%) | 12.5% |
| Gatekeeper | 0.813 | 43.1% | 1 (1.4%) | 38.9% |
| Convenience | 0.798 | 69.4% | 4 (5.6%) | 33.3% |
| Frustrated | **0.757** | 43.1% | 5 (6.9%) | 15.3% |

**Why it matters:** The Operationally Constrained persona responds well to trade proposals (D&H, fast unload), making it the most cooperative. The Frustrated persona is hardest not because it withdraws (only 15.3% rate) but because it pressures the dispatcher into bad deals — 6.9% HOS violation rate, lowest optimal hit rate tied with Gatekeeper. The Gatekeeper has the highest withdrawal rate (38.9%) but lower HOS violations, suggesting it's "difficult but safe" while Frustrated is "difficult and dangerous."

---

## Theme 3: Massive Sunk Cost Bias — 85.4% Error Rate

In 192 scenarios where OTIF was **already unsaveable** (MD and LG delays), the dispatcher cited OTIF as a reason to negotiate urgently **85.4% of the time** (164/192).

| MABD | Cited OTIF When Unsaveable | Rate |
|------|---------------------------|------|
| 1hr (tight) | 84/96 | 87.5% |
| 2hr (loose) | 80/96 | 83.3% |

**Why it matters:** This is the single largest cognitive failure. The OTIF penalty was already locked in (truck arrived too late), yet the agent treated it as an active constraint, potentially accepting worse slots or making unnecessary concessions under false urgency. The error rate is slightly higher with tight MABD (87.5% vs 83.3%), but both are extremely high. The agent cannot distinguish between "penalty avoidable" and "penalty already incurred."

---

## Theme 4: Perfect Tool Usage, But Tools Don't Prevent Violations

The dispatcher checked the cost tool before accepting in **100% of deals** (286/286). It also checked both D&H variants (dh=true and dh=false) in 81.2% of conversations (234/288).

Yet all **11 HOS violations occurred after the agent checked the tool**. Zero violations came from not checking.

**Why it matters:** The failure mode isn't "forgot to check" — it's "checked, saw the constraint, and accepted anyway." This suggests the agent understands the tool output but fails to act on HOS warnings under social/negotiation pressure. It's a reasoning-to-action gap, not a knowledge gap.

---

## Theme 5: Information Asymmetry Has Almost No Effect Overall — But Flips by Delay

| Condition | Mean Score | Mean Cost |
|-----------|-----------|-----------|
| Transparent | 0.823 | $7,632 |
| Asymmetric | 0.820 | $8,147 |
| **Gap** | **0.003** | **$515** |

But the overall gap hides a dramatic interaction with delay severity:

| Delay | Transparent | Asymmetric | Gap |
|-------|-------------|-----------|-----|
| Small (1hr) | 0.681 | 0.525 | **+0.156** |
| Medium (2hr) | 0.956 | 0.957 | -0.001 |
| Large (4hr) | 0.833 | 0.979 | **-0.146** |

**Why it matters:** For small delays (hardest scenarios), transparency helps significantly (+0.156). But for large delays, asymmetry actually produces **better** outcomes (-0.146 gap favoring ASYM). This counterintuitive result suggests that when the dispatcher knows all constraints in LG scenarios, it may over-anchor on constraint complexity and negotiate worse deals. Ignorance may produce simpler, more decisive negotiation.

---

## Theme 6: 100% Creative Reasoning on D&H, 87.5% Success

In 48 scenarios where Drop & Hook was **required** for optimal cost (LG-delay + 4hr-HOS), the dispatcher proposed D&H in **every single case** (48/48 = 100%). Of those, 42 were accepted (87.5%).

| Metric | Value |
|--------|-------|
| Requires-D&H scenarios | 48 |
| D&H proposed | 48 (100%) |
| D&H agreed | 42 (87.5%) |
| D&H rejected | 6 (12.5%) |

**Why it matters:** The agent demonstrates strong creative problem-solving — it consistently identifies that D&H reduces unloading time and makes tighter slots feasible. The 87.5% agreement rate shows most personas accept the trade, making this one of the agent's strongest capabilities.

---

## Theme 7: The Optimal Pushback Count is 2

| Pushbacks | N | Mean Score | Mean $ Saved vs First Offer |
|-----------|---|-----------|---------------------------|
| 0 | 43 | 0.884 | $0 |
| 1 | 60 | 0.868 | $1,065 |
| **2** | **91** | **0.936** | **$2,233** |
| 3 | 53 | 0.725 | $2,992 |
| 4 | 39 | 0.559 | $3,182 |
| 5 | 2 | 0.995 | $10,200 |

**Why it matters:** Score peaks at 2 pushbacks (0.936), then drops sharply — 3 pushbacks yields 0.725, 4 pushbacks yields 0.559. While dollar savings continue to increase with more pushbacks, the risk of worse outcomes (HOS violations, withdrawals) dominates beyond 2. The 22.0% over-negotiation rate (63/286 deals pushed back after the optimal slot was already offered) confirms the agent often doesn't know when to stop.

---

## Theme 8: Rescheduling Fee is Extremely Profitable

27 scenarios paid the $100 rescheduling fee. The ROI was overwhelmingly positive:

| Metric | Value |
|--------|-------|
| Fee-paid scenarios | 27 |
| Mean savings (net of fee) | **$7,489** |
| Median savings (net of fee) | **$10,000** |
| Positive ROI count | 22/27 (81.5%) |

By persona: Frustrated (11 fees), Gatekeeper (10), Convenience (3), Op. Constrained (3).

**Why it matters:** The $100 fee reliably unlocks major savings — median $10,000, meaning it typically avoids an entire OTIF penalty. The fee is concentrated on the hardest personas (FR + GK = 21/27), suggesting it's a critical tool for breaking through resistance. The 5 negative-ROI cases represent scenarios where the warehouse didn't meaningfully improve after accepting the fee.

---

## Theme 9: Frustrated Persona + Negative Day = Danger Zone

HOS violations by persona and day context:

| Persona | Negative | Neutral | Positive |
|---------|----------|---------|----------|
| Frustrated | **12.5%** | 0% | 8.3% |
| Convenience | 0% | 8.3% | 8.3% |
| Gatekeeper | 0% | 0% | 4.2% |
| Op. Constrained | 0% | 4.2% | 0% |

**Why it matters:** The Frustrated persona on a Negative day produces the highest HOS violation rate in the entire matrix (**12.5%**, 3/24). This suggests the agent capitulates to emotional pressure — the frustrated warehouse manager on a bad day pushes the dispatcher into accepting illegal slots. Surprisingly, Transparent info (5.6% violations) is worse than Asymmetric (2.1%), suggesting that seeing all constraints creates decision paralysis rather than clarity.

---

## Theme 10: Conversation Arc — Most Deals Close by Turn 5

| Breakthrough Turn | Count | Cumulative |
|-------------------|-------|-----------|
| Turn 1 | 91 (31.8%) | 31.8% |
| Turn 3 | 35 (12.2%) | 44.1% |
| Turn 5 | 98 (34.3%) | **78.3%** |
| Turn 7 | 35 (12.2%) | 90.6% |
| Turn 9 | 22 (7.7%) | 98.3% |
| Turn 11 | 5 (1.7%) | 100% |

Mean breakthrough turn: **4.1** | Conversations with improved offers: 187/246 (76.0%)

The score-by-length curve shows optimal length at 7 turns (0.940), with sharp decline after:
- 7 turns: 0.940 (n=83)
- 9 turns: 0.745 (n=60)
- 11 turns: 0.554 (n=45)

**Why it matters:** Nearly 80% of final deals are first offered by turn 5 (2-3 exchange rounds). Conversations that drag past 7 turns see diminishing returns, likely involving difficult persona-delay combinations where continued negotiation increases risk without proportional benefit.

---

## Additional Statistical Notes

### MABD Impact
Tight MABD (1hr) produces 0.732 vs loose MABD (2hr) at 0.912 — a **0.180-point gap**, the second largest single-dimension effect after delay type.

### HOS Constraint Paradox
Tight HOS (4hr) has **higher** optimal hit rate (67.4%) than comfortable HOS (48.6%), despite more violations (6.2% vs 1.4%). Under tight HOS, the agent negotiates more aggressively for early slots — and usually succeeds — but when it fails, it fails catastrophically with an illegal slot.

### Warehouse Offer Trajectories
- **Gatekeeper** starts with the worst offers (mean first offer 17:12) and requires the most turns to improve
- **Op. Constrained** starts best (15:37) and improves fastest
- **Convenience** has the most erratic trajectory (15:23 → 15:06 → 14:42 → 14:16)
- All personas converge toward 14:00-14:30 by offer 4+

### Beat-Optimal Anomalies
5 scenarios achieved costs below the computed optimal — all MD-2hr-MABD combinations where the agent negotiated D&H on a slot that the ground truth didn't account for as optimal-with-D&H. These represent edge cases in the ground truth computation, not agent errors.

### Withdrawal Triggers
73% of offer withdrawals (54/74) were preceded by a D&H proposal from the dispatcher. This suggests some personas interpret D&H requests as overreaching, triggering withdrawal as a punitive response.

---

## Key Takeaways for the Paper

1. **Constraint interaction is the primary difficulty driver**, not individual constraint severity. SM-delay + tight-MABD creates near-random performance (0.367).
2. **Sunk cost bias is pervasive** (85.4%) — the largest single reasoning failure across all scenarios.
3. **Tool usage is necessary but not sufficient** — 100% tool-check rate with 100% of violations occurring post-check.
4. **Information asymmetry effect is non-monotonic** — helps in hard scenarios, hurts in easy ones.
5. **2 pushbacks is optimal** — clear diminishing returns after, with 22% over-negotiation rate.
6. **Creative solutions work** — 100% D&H proposal rate in critical scenarios, $7,489 mean fee ROI.
7. **Social pressure causes safety violations** — Frustrated+Negative day = 12.5% HOS violation rate.
8. **Persona difficulty ranking:** OC (easiest) > GK > CD > FR (hardest), but the mechanism differs — FR causes bad deals, GK causes walkways.
