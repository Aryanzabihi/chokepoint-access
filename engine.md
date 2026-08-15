# TAR Economic & Decision Engines — Design Specification

## 1. Purpose

The TAR Economic & Decision Engine converts a maritime/geopolitical disruption scenario into:

1. Economic exposure
2. Expected disruption loss
3. Welfare / avoidable economic gap
4. Mitigation alternatives
5. Preferred decision
6. Economic action threshold

### Core chain

**Threat → Probability → Disruption → Physical consequence → Market prices → Economic loss → Welfare gap → Mitigation alternatives → Decision → Threshold**

---

# 2. System Architecture

```text
                    TAR RISK ENGINE
                          │
                          ▼
                 DISRUPTION SCENARIO
          ┌─────────────────────────────┐
          │ Probability                 │
          │ Duration                    │
          │ Severity                    │
          │ Capacity loss               │
          │ Route disruption            │
          │ Recovery time               │
          └──────────────┬──────────────┘
                         │
                         ▼
                ┌───────────────────┐
                │ ECONOMIC ENGINE   │
                └───────────────────┘
                         │
        ┌────────────────┼─────────────────┐
        ▼                ▼                 ▼
    Transport         Insurance        Commodity
      costs             costs            effects
        │                │                 │
        └────────────────┼─────────────────┘
                         ▼
                  TOTAL EXPOSURE
                         │
                         ▼
                   WELFARE GAP
                         │
                         ▼
                  DECISION ENGINE
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
     Continue          Reroute         Mitigate
        │                │                │
        └────────────────┼────────────────┘
                         ▼
                EXPECTED NET BENEFIT
                         │
                         ▼
                   ACTION THRESHOLD
```

---

# 3. Engine Modules

## 3.1 Risk Engine

The Economic Engine receives its disruption scenario from the existing TAR risk layer.

Required outputs:

- Scenario ID
- Corridor
- Disruption probability
- Severity
- Expected duration
- Capacity reduction
- Delay
- Closure probability
- Recovery time
- Confidence / uncertainty

Example:

```yaml
scenario:
  corridor: Strait of Hormuz
  probability: 0.22
  severity: severe
  expected_duration_days: 11
  capacity_reduction: 0.55
  recovery_days: 18
  confidence: medium
```

The Economic Engine should not independently invent geopolitical probabilities unless explicitly configured to do so.

---

# 4. Input Layer

Inputs are divided into six groups.

## 4.1 Disruption Inputs

- Corridor
- Probability
- Duration
- Severity
- Capacity reduction
- Delay
- Closure probability
- Recovery time

## 4.2 Cargo / Commodity Inputs

- Commodity
- Quantity
- Cargo value
- Origin
- Destination
- Shipment frequency
- Inventory level
- Criticality
- Baseline commodity price

## 4.3 Transport Inputs

- Baseline freight
- Disrupted freight
- Alternative-route freight
- Fuel / bunker cost
- Port charges
- Handling costs
- Transit time
- Congestion cost

## 4.4 Insurance Inputs

- Baseline insurance premium
- War-risk premium
- Additional surcharge
- Deductible
- Coverage limit

## 4.5 Economic Inputs

- Commodity price
- Price volatility
- Shortage cost
- Inventory holding cost
- Delay cost
- Lost margin
- Substitution cost
- Discount rate where relevant

## 4.6 Mitigation Inputs

Possible strategies:

- Continue current route
- Full rerouting
- Partial rerouting
- Additional inventory
- Alternative supplier
- Alternative port
- Additional insurance
- Early booking
- Split shipment
- Other customer-defined strategies

---

# 5. Data Provenance

Every market input should contain:

```yaml
value:
unit:
currency:
source:
source_type:
retrieved_at:
valid_from:
valid_to:
confidence:
```

## Recommended source hierarchy

1. Customer-provided quotation
2. Licensed commercial data
3. Public market data
4. Model estimate
5. Proxy estimate

The UI must disclose which category was used.

---

# 6. Baseline Engine

The engine must first construct the counterfactual:

> What would the economic outcome have been without the disruption?

Baseline variables include:

- Normal freight
- Normal transit time
- Normal insurance
- Normal fuel cost
- Normal commodity price
- Normal inventory cost
- Normal operational margin

This becomes the reference state.

```text
BASELINE COST =
normal transport
+ normal insurance
+ normal delay
+ normal inventory
+ other relevant baseline costs
```

Baseline and disruption costs must remain separate.

---

# 7. Disruption Cost Engine

## 7.1 Transport Cost

```text
C_transport =
freight
+ fuel
+ port costs
+ handling
+ rerouting premium
```

## 7.2 Delay Cost

Conceptually:

```text
C_delay =
cargo_value
× daily_exposure_rate
× additional_delay_days
```

The exposure rate must be configurable and never silently assumed.

Possible approaches:

- Customer-provided rate
- Historical company margin
- Commodity-specific proxy
- Scenario estimate

## 7.3 Insurance Cost

```text
C_insurance =
baseline premium
+ war-risk premium
+ additional surcharge
```

Avoid double-counting the baseline premium when calculating incremental disruption cost.

## 7.4 Inventory Cost

```text
C_inventory =
additional_inventory_quantity
× holding_cost_rate
× additional_days
```

## 7.5 Commodity Effect

Conceptually:

```text
C_commodity =
affected_quantity
× attributable_price_change
```

The engine must distinguish:

- Market-wide price movement
- Disruption-attributable price movement

This is important to prevent double-counting.

---

# 8. Total Economic Cost

For each scenario:

```text
Total Disrupted Cost =
Transport
+ Insurance
+ Delay
+ Inventory
+ Commodity
+ Other relevant incremental costs
```

Every component must remain separately observable.

**Never return only one opaque number.**

The customer should be able to see exactly where the economic exposure comes from.

---

# 9. Expected Economic Loss

For scenario `s`:

```text
Expected Loss_s =
P(disruption_s) × Conditional Loss_s
```

For multiple mutually exclusive scenarios:

```text
Expected Loss =
Σ P(s) × Loss(s)
```

Example:

| Scenario | Probability | Conditional Loss |
|---|---:|---:|
| No disruption | 60% | €0 |
| Partial disruption | 25% | €1.2M |
| Severe disruption | 12% | €4.5M |
| Closure | 3% | €15M |

The engine must validate scenario probabilities and ensure the scenario structure is logically consistent.

---

# 10. Welfare Gap Engine

The welfare layer compares the disrupted economic allocation with the counterfactual allocation.

## Core concept

```text
Welfare Gap =
Economic outcome under disruption
-
Economic outcome under counterfactual
```

The implementation should support three views.

### 10.1 Private Welfare Gap

Economic loss to the customer/company.

Recommended commercial label:

> **Avoidable Economic Loss**

### 10.2 Supply-Chain Welfare Gap

Economic loss distributed across the affected supply chain.

### 10.3 Social Welfare Gap

Broader economic impact where sufficiently reliable data exists.

The system must clearly distinguish measured private losses from broader social welfare estimates.

---

# 11. Decision Engine

The Decision Engine compares alternative strategies.

For every strategy:

```text
Expected Total Cost =
Direct Strategy Cost
+ Expected Residual Disruption Loss
+ Implementation Cost
- Relevant Benefits
```

Example:

| Strategy | Direct Cost | Residual Expected Loss | Expected Total Cost |
|---|---:|---:|---:|
| Continue | €0 | €2.8M | €2.8M |
| Reroute | €1.2M | €350k | €1.55M |
| Extra inventory | €800k | €1.1M | €1.9M |
| Insurance | €400k | €2.1M | €2.5M |
| Partial reroute | €700k | €750k | **€1.45M** |

Recommendation:

> **Partial rerouting minimizes expected total economic cost.**

The engine must support customer-specific constraints:

- Maximum acceptable delay
- Maximum budget
- Minimum service level
- Maximum disruption probability
- Contractual restrictions
- Capacity constraints

---

# 12. Decision Threshold Engine

Determine when a mitigation strategy becomes economically justified.

## General condition

```text
Expected Cost(continue)
>
Expected Cost(mitigate)
```

Simplified threshold:

```text
P* × Loss_if_disrupted
>
Cost_of_mitigation
```

Therefore:

```text
P* =
Cost_of_mitigation
/
Loss_if_disrupted
```

The production implementation must include:

- Residual risk
- Strategy-specific costs
- Strategy benefits
- Uncertainty
- Customer constraints

Example:

```text
Current disruption probability: 22%
Mitigation threshold: 16%

STATUS:
MITIGATION ECONOMICALLY JUSTIFIED
```

Thresholds must be reported with uncertainty rather than presented as perfectly precise values.

---

# 13. Market Agent

Create a separate service:

> **TAR Market Agent**

Purpose:

Monitor and update market inputs without coupling the Economic Engine to a specific provider.

## Potential data

- Freight rates
- Bunker / fuel prices
- Commodity prices
- Port congestion
- Transit times
- Alternative-route costs
- Insurance indicators
- Publicly reported war-risk changes

## Default update frequency

Daily.

Where reliable intraday data exists, higher-frequency monitoring can be added later.

Every update stores:

- Current value
- Previous value
- Change
- Source
- Timestamp
- Confidence
- Data-quality status

---

# 14. Proprietary / Paywalled Data Strategy

**Do not build TAR around scraping paywalled quotations.**

Use a layered model:

```text
Public data
     +
Licensed APIs
     +
Customer-provided quotations
     +
Manual overrides
     +
Model estimates
```

Every value must identify its source.

This allows TAR to launch without requiring every commercial data license.

---

# 15. Customer Quotation Upload

Allow users to upload:

- Freight quotations
- Insurance quotations
- Carrier offers
- Alternative-route quotes

TAR should extract:

- Route
- Vessel/service
- Freight price
- Surcharges
- Insurance premium
- Transit time
- Currency
- Validity period
- Other relevant terms

Extracted values must be reviewable before being used in the calculation.

Preferred UI message:

> **Calculation based on your actual quotation.**

This can be more commercially valuable than relying entirely on estimated market prices.

---

# 16. Uncertainty Engine

The Economic Engine must not imply false precision.

Instead of:

```text
Expected Loss = €1,827,431
```

show:

```text
Expected Economic Exposure
€1.83M

Estimated range
€1.2M – €2.7M

Confidence
Medium
```

Potential methods:

- Scenario analysis
- Monte Carlo simulation
- Parameter distributions
- Sensitivity analysis
- Historical error distributions

The engine should identify the main drivers of uncertainty.

Example:

```text
Main uncertainty drivers:
1. Disruption duration      41%
2. Freight increase         27%
3. Commodity effect         19%
4. Insurance                 8%
5. Other                     5%
```

---

# 17. Sensitivity Analysis

Stress-test:

- Probability
- Duration
- Freight rate
- Insurance premium
- Commodity price
- Delay cost
- Mitigation cost
- Capacity reduction

Example:

```text
5 days  → mitigation not justified
10 days → borderline
15 days → mitigation justified
20 days → strongly justified
```

The UI should allow users to change assumptions and immediately observe how the recommendation changes.

---

# 18. Scenario Engine

Minimum scenarios:

### Base Case

No major disruption.

### Moderate

Partial operational disruption.

### Severe

Major capacity loss / rerouting.

### Extreme

Temporary closure or near-total disruption.

Each scenario has:

- Probability
- Duration
- Severity
- Economic consequences
- Mitigation options

Users should also be able to create custom scenarios.

---

# 19. User Interface

The main dashboard should answer four questions immediately.

### What is happening?

**Hormuz — Severe Disruption Risk**

### What will it cost?

**Expected exposure: €3.7M**

### What can I avoid?

**Avoidable economic loss: €2.1M**

### What should I do?

**Recommended action: Partial rerouting**

Example:

```text
TAR DECISION
────────────────────────────────

Corridor
Strait of Hormuz

Scenario
Severe disruption

Probability
22%

Expected Economic Exposure
€3.7M

Avoidable Economic Loss
€2.1M

Best Mitigation
Partial rerouting

Mitigation Cost
€700k

Expected Saving
€1.0M

Action Threshold
16%

Current Probability
22%

STATUS
MITIGATION ECONOMICALLY JUSTIFIED
```

---

# 20. Explainability

Every recommendation must be explainable.

The user should be able to click:

> **Why is TAR recommending this?**

and see:

```text
Recommendation drivers:

• Disruption probability: 22%
• Expected delay: 11 days
• Freight increase: +63%
• War-risk premium: +41%
• Alternative-route cost: €700k
• Expected disruption loss: €2.1M
• Expected mitigation cost: €700k
```

The system must never produce a recommendation without exposing the main assumptions.

---

# 21. API Output

A standardized result object should resemble:

```json
{
  "scenario_id": "HORMUZ-2026-001",
  "currency": "EUR",
  "probability": 0.22,
  "baseline_cost": 850000,
  "disrupted_cost": 4550000,
  "expected_exposure": 3700000,
  "avoidable_loss": 2100000,
  "recommended_strategy": "partial_reroute",
  "mitigation_cost": 700000,
  "expected_saving": 1000000,
  "action_threshold": 0.16,
  "current_status": "MITIGATION_JUSTIFIED",
  "confidence": "medium"
}
```

The production schema should additionally include:

- Data provenance
- Uncertainty intervals
- Assumptions
- Calculation version
- Model version
- Timestamp
- Input dataset version

---

# 22. Validation

Validate the engine against historical disruptions.

For each historical event:

1. Establish counterfactual baseline.
2. Reconstruct market conditions.
3. Reconstruct disruption severity.
4. Calculate predicted economic exposure.
5. Compare with observed costs.
6. Measure error.
7. Identify systematic bias.
8. Test decision recommendations retrospectively.

## Important metrics

- MAE
- RMSE
- MAPE where appropriate
- Calibration
- Prediction-interval coverage
- Threshold accuracy
- False-action rate
- Missed-action rate

Do not claim predictive accuracy without out-of-sample validation.

---

# 23. Development Roadmap

## Phase 1 — MVP

Build:

- Baseline Engine
- Transport Cost Engine
- Insurance Cost Engine
- Delay Cost Engine
- Total Cost Engine
- Manual inputs
- Customer quotation upload
- Basic Welfare Gap
- Basic Decision Engine
- Basic Threshold Engine

**No automated market agent is required initially.**

The objective is to prove that the economic decision logic works.

---

## Phase 2 — Market Integration

Add:

- Public freight data
- Commodity data
- Fuel data
- Port/congestion data
- Automated daily updates
- Historical market database

---

## Phase 3 — Advanced Decision Intelligence

Add:

- Monte Carlo simulation
- Scenario distributions
- Sensitivity analysis
- Optimization
- Multi-strategy comparison
- Dynamic thresholds
- Customer-specific constraints

---

## Phase 4 — Commercial Data

Add:

- Licensed freight data
- Licensed maritime data
- Commercial insurance indicators
- Customer/API integrations

---

## Phase 5 — Autonomous TAR Agent

The agent should:

1. Monitor market data
2. Detect material changes
3. Update economic exposure
4. Recalculate thresholds
5. Detect when mitigation status changes
6. Notify the customer
7. Explain why the recommendation changed

Example alert:

> **TAR Alert — 08:00**
>
> Hormuz mitigation threshold crossed.
>
> Estimated disruption probability increased from 13% to 18%.
>
> Current economic exposure increased from €1.1M to €2.4M.
>
> Partial rerouting is now economically justified.

---

# 24. Product Architecture

```text
TAR
│
├── Threat Intelligence
│
├── Threat-to-Acts Engine
│
├── Disruption Engine
│
├── Economic Exposure Engine
│
├── Welfare Gap Engine
│
├── Decision Engine
│
├── Threshold Engine
│
├── Market Agent
│
└── Reporting / Alerts
```

---

# 25. Core Design Principle

The Economic Engine must never simply output:

> **Risk = High**

It should answer:

> **What happens?**
>
> **What does it cost?**
>
> **What portion of that loss is avoidable?**
>
> **Which mitigation minimizes expected total cost?**
>
> **At what probability or market condition does the decision change?**
>
> **How confident are we?**

This economic/decision layer is intended to become one of the core differentiating capabilities of TAR.

---

# 26. Implementation Rules

1. Keep the Risk, Economic, Welfare, Decision, and Market layers modular.
2. Never hard-code a market price into the economic model.
3. Store every input with source and timestamp.
4. Preserve baseline and disruption calculations separately.
5. Prevent double-counting across freight, commodity, insurance, delay, and inventory effects.
6. Make every recommendation reproducible from stored inputs and model version.
7. Version all formulas and model parameters.
8. Expose assumptions to users.
9. Support customer overrides.
10. Never hide uncertainty behind a single precise number.
11. Keep commercial data-provider integrations replaceable.
12. Validate against historical events before making predictive claims.
13. Make the Decision Engine capable of comparing multiple mitigation strategies.
14. Make thresholds dynamic rather than fixed risk scores.
15. Treat customer quotations as a first-class data source.
16. Record calculation provenance for every material output.
17. Separate observed market data from model-derived estimates.
18. Never silently substitute missing data with arbitrary assumptions.
19. Flag low-confidence calculations.
20. Preserve model/version history so previous customer reports can be reproduced.

---

# 27. MVP Acceptance Criteria

The first commercial version is ready for testing when a user can:

1. Select a maritime corridor.
2. Define or import a disruption scenario.
3. Enter or upload actual freight and insurance costs.
4. Define cargo exposure.
5. Establish a baseline.
6. Calculate disruption costs.
7. Calculate expected economic exposure.
8. Calculate avoidable economic loss.
9. Compare at least three mitigation strategies.
10. Calculate the mitigation threshold.
11. See the assumptions behind the result.
12. See uncertainty/ranges.
13. Export a decision report.
14. Reproduce the same result from the same data and model version.

The first goal is **not autonomous prediction**.

The first goal is a **defensible economic decision engine that a real customer can use, understand, and audit**.