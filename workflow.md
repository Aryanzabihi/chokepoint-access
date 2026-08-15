The key change: **ACT / WAIT is not a separate navigation page. It is the central decision output.**

---

# TAR — Application Structure

```
┌──────────────────────────────────────────────────────────────────────┐
```

│ TAR                                            🔔   Aryan ▾          │

├───────────────┬──────────────────────────────────────────────────────┤

│               │                                                      │

│  🏠 Home      │                                                      │

│               │                                                      │

│  📊 Portfolio │                                                      │

│               │                 MAIN WORKSPACE                       │

│  ＋ New        │                                                      │

│    Decision   │                                                      │

│               │                                                      │

│  ◷ Decisions  │                                                      │

│               │                                                      │

│  ⚙ Settings   │                                                      │

│               │                                                      │

└───────────────┴──────────────────────────────────────────────────────┘

---

# 1. HOME

The home page should **not be complicated**.

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ Good morning                                                        │

│                                                                    │

│ Procurement decisions affected by current geopolitical exposure.  │

│                                                                    │

│             ┌──────────────────────────────┐                       │

│             │       + NEW DECISION         │                       │

│             └──────────────────────────────┘                       │

│                                                                    │

│ ACTIVE DECISIONS                                                   │

│                                                                    │

│ ┌────────────────────────────────────────────────────────────────┐ │

│ │ PO #1042 · Strait of Hormuz                    🔴 ACT           │ │

│ │ 5,000 units · ETA 18 Sept                                      │ │

│ │ Partial reroute recommended                       [Review]     │ │

│ └────────────────────────────────────────────────────────────────┘ │

│                                                                    │

│ ┌────────────────────────────────────────────────────────────────┐ │

│ │ Component B · Red Sea                         🟡 WAIT           │ │

│ │ Procurement requirement · 30 days                              │ │

│ │ Monitor escalation                                [Review]     │ │

│ └────────────────────────────────────────────────────────────────┘ │

│                                                                    │

│ ┌────────────────────────────────────────────────────────────────┐ │

│ │ Steel · Black Sea                            🟢 NO ACTION       │ │

│ │ No economically material exposure                               │ │

│ └────────────────────────────────────────────────────────────────┘ │

│                                                                    │

└─────────────────────────────────────────────────────────────────────┘

The user immediately understands:

**What needs my attention?**

---

# 2. NEW DECISION

Click:

> **+ New Decision**

First screen:

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ New Decision                                                       │

│                                                                     │

│ What are you deciding?                                             │

│                                                                     │

│ ┌──────────────────────┐                                           │

│ │ 📦                   │                                           │

│ │ I NEED TO PURCHASE  │                                           │

│ │                     │                                           │

│ │ I have a demand     │                                           │

│ │ requirement but no  │                                           │

│ │ purchase order yet. │                                           │

│ └──────────────────────┘                                           │

│                                                                     │

│ ┌──────────────────────┐                                           │

│ │ 📋                   │                                           │

│ │ EXISTING ORDER      │                                           │

│ │                     │                                           │

│ │ A PO already exists │                                           │

│ │ but I need to decide│                                           │

│ │ what to do with it. │                                           │

│ └──────────────────────┘                                           │

│                                                                     │

│ ┌──────────────────────┐                                           │

│ │ 🚢                   │                                           │

│ │ SHIPMENT IN TRANSIT │                                           │

│ │                     │                                           │

│ │ Cargo is already    │                                           │

│ │ moving.              │                                           │

│ └──────────────────────┘                                           │

│                                                                     │

└─────────────────────────────────────────────────────────────────────┘

This is important because **these three situations require different strategies**.

---

# 3. DEMAND & SUPPLY

Suppose the user chooses:

> **I need to purchase**

Now TAR asks for the company's existing planning information.

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ New Decision · 1 of 4                                             │

│                                                                     │

│ DEMAND & SUPPLY                                                     │

│                                                                     │

│ What are you planning to supply?                                   │

│                                                                     │

│ Product / SKU       [ Industrial Component A              ]        │

│                                                                     │

│ Forecast quantity   [ 10,000 ]  Unit [ units ▾ ]                   │

│                                                                     │

│ Forecast horizon    [ 60 ] days                                    │

│                                                                     │

│ Required-by date    [ 15 / 10 / 2026 ]                             │

│                                                                     │

│ ──────────────────────────────────────────────────────────────────  │

│                                                                     │

│ CURRENT SUPPLY                                                       │

│                                                                     │

│ Available inventory [ 3,000 ] units                                │

│                                                                     │

│ Confirmed inbound   [ 4,000 ] units                                │

│ Arrival date        [ 25 / 09 / 2026 ]                             │

│                                                                     │

│ Safety stock        [ 1,000 ] units                                │

│                                                                     │

│                         [ Continue → ]                              │

└─────────────────────────────────────────────────────────────────────┘

TAR calculates:

```
Forecast demand       10,000
```

Available inventory    3,000

Confirmed inbound      4,000

Safety stock           1,000

────────────────────────────

Additional requirement 4,000

The user does **not** have to calculate that.

---

# 4. PROCUREMENT / ORDER

Next:

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ New Decision · 2 of 4                                             │

│                                                                     │

│ PROCUREMENT REQUIREMENT                                            │

│                                                                     │

│ Quantity to secure       4,000 units                               │

│                                                                     │

│ Supplier                 [ Supplier A                    ]          │

│                                                                     │

│ Origin                   [ India                         ]          │

│                                                                     │

│ Destination              [ Italy                         ]          │

│                                                                     │

│ Supplier lead time       [ 25 ] days                               │

│                                                                     │

│ Unit price               [ €48 ]                                   │

│                                                                     │

│ Incoterm                 [ FOB ▾ ]                                 │

│                                                                     │

│ Preferred route          [ Strait of Hormuz ▾ ]                    │

│                                                                     │

│ Alternative supplier     [ Optional ]                              │

│                                                                     │

│                         [ ← Back ]    [ Continue → ]                │

└─────────────────────────────────────────────────────────────────────┘

If it's an existing PO, this screen changes to PO/shipment information.

---

# 5. TAR EXPOSURE

Now the user gives TAR the situation-specific information.

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ New Decision · 3 of 4                                             │

│                                                                     │

│ TAR EXPOSURE                                                       │

│                                                                     │

│ Chokepoint              Strait of Hormuz                           │

│                                                                     │

│ Current TAR signal      🔴 ESCALATING                              │

│                                                                     │

│ Your probability        [ 35 ] %                                   │

│                         Leave blank → historical base rate         │

│                                                                     │

│ Expected delay          [ 25 ] days                                │

│                                                                     │

│ Delay range             [ 15 ] — [ 45 ] days                       │

│                                                                     │

│ Capacity disruption     [ Optional ]                               │

│                                                                     │

│ ──────────────────────────────────────────────────────────────────  │

│                                                                     │

│ LIVE MARKET DATA                                                    │

│                                                                     │

│ Reroute quote           [ Optional ]                               │

│ War-risk insurance     [ Optional ]                               │

│ Emergency replacement  [ Optional ]                               │

│                                                                     │

│                         [ ← Back ]    [ Analyze with TAR → ]        │

└─────────────────────────────────────────────────────────────────────┘

Notice how we **don't dump WACC, gross margin, penalties, etc. here**.

Those belong in advanced economic assumptions or company settings.

---

# 6. TAR ANALYSIS

Now the magic happens.

The user shouldn't see a huge mathematical calculation.

Instead:

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ TAR ANALYSIS                                                        │

│                                                                     │

│ Demand & Supply → Exposure → Economics → Strategy                  │

│                                                                     │

│ ┌───────────────────────────────────────────────────────────────┐  │

│ │                     TAR DECISION                              │  │

│ │                                                               │  │

│ │                         ACT                                   │  │

│ │                                                               │  │

│ │              PARTIAL REROUTE RECOMMENDED                      │  │

│ │                                                               │  │

│ │              Secure / reroute 2,000 units                     │  │

│ └───────────────────────────────────────────────────────────────┘  │

│                                                                     │

│ WHY?                                                                │

│                                                                     │

│ Demand at risk                         3,800 units                 │

│ Potential supply shortfall             12 days                    │

│ Expected cost of waiting               €1.42M                     │

│ Recommended strategy                   €980k                      │

│ Avoidable exposure                     €440k                      │

│                                                                     │

└─────────────────────────────────────────────────────────────────────┘

---

# 7. Strategy comparison

Immediately below:

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ STRATEGY COMPARISON                                                 │

│                                                                     │

│ Strategy                Incremental     Expected     Supply        │

│                         cost            total cost   protection    │

│                                                                     │

│ Continue                 €0             €1.42M       Low           │

│                                                                     │

│ ★ Partial reroute       €700k          €980k        Medium        │

│                                                                     │

│ Full reroute             €1.20M         €1.05M       High          │

│                                                                     │

│ Emergency replacement    €1.50M         €1.61M       High          │

│                                                                     │

└─────────────────────────────────────────────────────────────────────┘

The recommendation is not simply:

> "Risk is high."

It is:

> **"This specific procurement action has the lowest expected economic exposure."**

---

# 8. Why TAR recommends it

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ WHY TAR RECOMMENDS THIS                                            │

│                                                                     │

│ ① DEMAND                                                             │

│   Your forecast requires 10,000 units over 60 days.                 │

│                                                                     │

│ ② SUPPLY                                                             │

│   Existing inventory + inbound supply leave limited coverage.       │

│                                                                     │

│ ③ GEOPOLITICAL EXPOSURE                                             │

│   Hormuz signal is escalating.                                      │

│   Estimated disruption: 25 days.                                    │

│                                                                     │

│ ④ ECONOMIC IMPACT                                                    │

│   Waiting creates greater expected shortage exposure than          │

│   the cost of partial rerouting.                                    │

│                                                                     │

└─────────────────────────────────────────────────────────────────────┘

This is where TAR's **explainability** becomes very strong.

---

# 9. ACT / WAIT / MODIFY

This should be the biggest decision control.

```
┌─────────────────────────────────────────────────────────────────────┐
```

│                                                                     │

│                         TAR DECISION                                │

│                                                                     │

│                              ACT                                    │

│                                                                     │

│                   Partial reroute                                  │

│                   2,000 units                                       │

│                                                                     │

│        ┌──────────────┐   ┌──────────────┐   ┌──────────────┐       │

│        │   APPROVE    │   │    MODIFY    │   │    REJECT    │       │

│        └──────────────┘   └──────────────┘   └──────────────┘       │

│                                                                     │

└─────────────────────────────────────────────────────────────────────┘

If TAR determines there isn't enough economic justification:

```
                         TAR DECISION
```




                            WAIT




              Maintain current procurement plan




              Expected intervention cost > 

              expected avoidable exposure




                    [ APPROVE ] [ MODIFY ]

Or:

```
                         TAR DECISION
```




                           MODIFY




              Split procurement:

              50% current supplier

              50% alternative supplier




                    [ APPROVE ] [ MODIFY ]

---

# 10. Decision sensitivity

This is where TAR becomes more advanced without making the main interface complicated.

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ WHEN WOULD THE DECISION CHANGE?                                    │

│                                                                     │

│ Current decision: ACT                                              │

│                                                                     │

│ Disruption probability      35%                                    │

│ → WAIT if below             18%                                    │

│                                                                     │

│ Expected delay              25 days                                │

│ → WAIT if below             11 days                                │

│                                                                     │

│ Reroute quote               €700k                                  │

│ → WAIT if above             €1.05M                                 │

│                                                                     │

│ Demand forecast             10,000                                │

│ → WAIT if below             9,800                                 │

│                                                                     │

└─────────────────────────────────────────────────────────────────────┘

This tells procurement:

> **What should I watch?**

---

# 11. Then comes procurement

After the user approves:

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ PROCUREMENT ACTION                                                 │

│                                                                     │

│ TAR recommendation approved.                                       │

│                                                                     │

│ ACTION                                                              │

│ Secure / reroute 2,000 units.                                      │

│                                                                     │

│ TAR expected cost             €980k                                │

│                                                                     │

│                                                                     │

│        [ GO TO MARKET ]                                            │

│                                                                     │

│ Request quotes from suppliers, carriers or brokers.                │

│                                                                     │

└─────────────────────────────────────────────────────────────────────┘

TAR doesn't have to become the marketplace.

The procurement team goes to market.

---

# 12. Market quote comes back

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ MARKET INFORMATION                                                 │

│                                                                     │

│ Reroute quote              €720k                                   │

│ War-risk insurance         €85k                                    │

│ Alternative supplier       €52 / unit                             │

│                                                                     │

│                       [ REASSESS WITH TAR ]                        │

└─────────────────────────────────────────────────────────────────────┘

TAR recalculates.

Maybe:

> **ACT — still optimal**

or:

> **MODIFY — emergency replacement now cheaper**

That creates your closed loop.

---

# 13. Decision record

Every decision gets permanently recorded.

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ DECISION RECORD                                                    │

│                                                                     │

│ PO / Requirement       PO #1042                                   │

│ Chokepoint             Strait of Hormuz                            │

│ Original decision      ACT                                         │

│ Strategy               Partial reroute                            │

│ Quantity               2,000 units                                │

│ Approved cost          €720k                                      │

│ Decision maker         Procurement Manager                         │

│ Decision date          14 Aug 2026                                │

│                                                                     │

│ TAR signal at decision  Escalating                                 │

│ Probability            35%                                         │

│ Delay assumption       25 days                                     │

│                                                                     │

│ Status                 🟢 Executed                                 │

└─────────────────────────────────────────────────────────────────────┘

---

# 14. Monitoring

Finally:

```
┌─────────────────────────────────────────────────────────────────────┐
```

│ MONITORING                                                         │

│                                                                     │

│ Decision: ACT — Partial reroute                                   │

│                                                                     │

│ 🟢 TAR signal             Stable                                   │

│ 🟢 Inventory coverage    31 days                                  │

│ 🟡 Demand forecast       +8%                                      │

│ 🟢 Reroute quote          €720k                                   │

│                                                                     │

│ REASSESSMENT TRIGGERS                                              │

│                                                                     │

│ ⚠ Recalculate if probability > 45%                                │

│ ⚠ Recalculate if delay > 35 days                                  │

│ ⚠ Recalculate if quote > €1.05M                                   │

│ ⚠ Recalculate if coverage < 20 days                               │

│                                                                     │

└─────────────────────────────────────────────────────────────────────┘

---

# Final sitemap

So visually, I would make the actual application:

```
TAR
```

│

├── 🏠 HOME

│

├── 📊 PORTFOLIO

│    ├── Demand

│    ├── Supply

│    ├── Orders

│    └── Shipments

│

├── ＋ NEW DECISION

│    │

│    ├── Need to purchase

│    ├── Existing order

│    └── Shipment in transit

│           │

│           ▼

│      Demand & Supply

│           ↓

│      Procurement / Order

│           ↓

│      TAR Exposure

│           ↓

│      TAR Analysis

│           ↓

│    ┌───────────────┐

│    │ ACT           │

│    │ WAIT          │

│    │ MODIFY        │

│    └───────────────┘

│           ↓

│      User decision

│           ↓

│      Go to market

│           ↓

│      Reassess

│           ↓

│      Monitor

│

├── ◷ DECISIONS

│    ├── Active

│    ├── Monitoring

│    └── History

│

└── ⚙ SETTINGS

     ├── Company

     ├── Financial assumptions

     ├── Users

     └── Integrations / API

### The most important UX principle

**Don't expose the complexity of TAR's model to the user.**