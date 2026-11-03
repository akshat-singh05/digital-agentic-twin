# Design decisions

The ideas behind the Agentic Digital Twin, and why the code is shaped the way it is.
Each section names the files to read if you want to see the idea in practice.

## 1. An agent that can only act with permission

**Idea:** an autonomous assistant that handles money should start with *no* access and be
given it piece by piece.

Every agent calls `require_consent(db, user_id, scope, agent)` before it touches data. There
are four scopes, and the default for each is **denied**:

| Scope | Lets the twin... |
|---|---|
| `usage_analysis` | analyze your usage history |
| `share_usage_with_provider` | share privacy-protected (noised) usage during negotiation |
| `negotiate_on_behalf` | negotiate prices for you |
| `execute_plan_changes` | apply a change you have approved |

A denial is not an error to hide: it is written to the audit log as `access_denied`. Inside a
decision cycle a missing consent *skips* that agent and the steps that depend on it; a direct
call to the same agent's endpoint answers 403.
*See `app/modules/privacy_mediator/`.*

## 2. Proposals, not actions

**Idea:** the system may recommend a plan change, but a person decides.

Plan switching never changes a subscription. It creates a `Recommendation` with status
`proposed`, an explanation (what, why, benefits, drawbacks) and the evidence behind it. Only
the owner's **approve** applies it, and approving needs the `execute_plan_changes` consent.
Approval checks that the plan and the catalog price are unchanged since the proposal; if they
moved, the proposal expires instead of applying stale advice. Every applied change is stored
in `plan_history`, so it can be rolled back. A rejected proposal is held back for 30 days
unless the terms change, so the system does not nag.
*See `app/modules/plan_switching/service.py`.*

## 3. Savings measured honestly

**Idea:** a "saving" that ignores what you actually pay is not a saving.

- The analyzer reports **unused spend** as an indicator, *not* as a saving.
- Savings are measured against the **effective** current cost: price plus the overage you were
  really billed. That is why a bigger plan can be the cheaper one.
- A candidate plan must cover the larger of your peak and projected usage **plus 10% headroom**,
  and respect your preferences (budget, minimums, required features, provider rules).
- A change must save at least **5%**. Every plan that was ruled out is shown with its reasons.
- A downgrade is held back when usage is trending up, even if today's utilisation is low.

*See `app/modules/usage_analyzer/analyzer.py` and `app/modules/plan_switching/alternatives.py`.*

## 4. Negotiation with a walk-away price

**Idea:** know your best alternative before you negotiate.

The agent will not agree to pay more than **95% of today's price**, or more than the best
catalog alternative costs, whichever is lower. That is its walk-away price (the user's
BATNA, best alternative to a negotiated agreement). It negotiates up to five rounds and
accepts when the gap to the provider's counter is within 3%.

**The provider is a simulator.** It is seeded per subscription and negotiation session, so any
outcome can be replayed, and every result is labelled `simulated: true`. There are no
fabricated integrations: the interface is real, the counterparty is clearly a stand-in.
*See `app/modules/negotiation_agent/engine.py`.*

## 5. Privacy before sharing

Usage that leaves the privacy boundary is first passed through the Laplace mechanism (noise
on data and call minutes, clamped at zero). It is a basic form of differential privacy:
the noise scales are fixed and no privacy budget is tracked, which the README lists as a
limitation.
*See `app/modules/privacy_mediator/sanitizer.py`.*

## 6. One orchestrated cycle, fully recorded

`POST /api/run-cycle/{user_id}` runs Analyze, Sanitize, Negotiate, Propose in order. The
orchestrator records an `agent_run` and one `agent_run_step` per agent (status, timing,
summary, error). A failing agent is rolled back and recorded without stopping steps that do
not depend on it. Audit entries and recommendations carry the run id, so any decision can be
traced back to the cycle that produced it.
*See `app/orchestrator/pipeline.py`.*

## 7. Pure logic apart from I/O

Each module has two layers:

- **Pure logic** (`analyzer.py`, `engine.py`, `executor.py`, `sanitizer.py`, `formatter.py`,
  `alternatives.py`): no database, no web framework, deterministic, easy to test.
- **`service.py`**: fetches data, calls the pure logic, persists the result in a transaction.

Routes stay thin: authenticate, authorize, call a service, wrap the result.

## 8. Users reach only their own data

Records that belong to someone else answer **404**, not 403, so another account's data cannot
be probed. Admins can read but never act for a user: consents, preferences, usage, runs and
approvals are the owner's alone.
*See `app/core/security.py`.*

## 9. Your data in, all-or-nothing

Real data enters as CSV (usage for yourself, plan prices for an administrator). A file is
validated as a whole and **one bad row rejects all of it**, with the line and column of every
problem, so a file is never half-imported. A dry run reports what would happen first, and
importing the same file twice adds nothing. Making someone an administrator is a
command-line step with no API, so nobody can promote themselves.
*See `app/modules/data_import/`.*

## 10. Say what is real

The README separates what is real (analysis, consent, audit, approvals, rollback), what is
basic (differential privacy), what is demo data (the plan catalog) and what is simulated
(the provider). A project that is honest about its limits is easier to trust and to extend.
