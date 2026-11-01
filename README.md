<p align="center">
  <h1 align="center">🤖 Agentic Digital Twin</h1>
  <p align="center">
    An autonomous AI agent that manages telecom &amp; digital subscriptions —<br/>
    analyzing usage, negotiating prices, switching plans, and explaining every decision.
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.100%2B-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/SQLAlchemy-2.0-red?logo=sqlalchemy" alt="SQLAlchemy">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

---

## 📋 Overview

**Agentic Digital Twin** is an intelligent backend system that acts as a user's autonomous representative for managing subscriptions (Jio, Airtel, Netflix, AWS, etc.).

It analyzes usage patterns, protects user privacy (consent gates + differential privacy), negotiates prices against a **built-in provider simulator**, validates proposed changes against KPIs and SLA risk, and keeps an explainable audit trail of every decision. Consequential changes are **proposed, not applied**: the user approves or rejects each one.

> **Simulation notice:** there are no real provider integrations. Negotiation outcomes come from a simulator and are labelled `simulated: true`; the plan catalog is demo data.

### 🎯 Key Highlights

- **Orchestrated decision cycle** — one endpoint runs every agent; each run and step is recorded
- **Human in the loop** — plan changes wait for the user's approval and can be rolled back
- **Modular architecture** — every module has a clean separation of pure logic vs DB persistence
- **Privacy-first** — every agent passes a consent gate; Laplace noise is applied before any data is shared
- **Explainable AI** — every decision generates a human-readable, deterministic audit message
- **Safe plan switching** — KPI validation + SLA risk assessment + atomic apply and rollback

---

## ✨ Features

### 1. 📊 Usage Analyzer
Measures every active subscription from its actual usage records (rule-based, deterministic).

- Utilisation of **data and call minutes**; the higher one is the binding constraint
- Average and **peak** usage, month-over-month **trend** and next-month **projection**
- **Overage** actually billed above the plan price (since the last price change)
- Categories: `underutilized` / `optimal` / `overutilized`; a downgrade is held back when usage is rising toward the threshold; plans with no cap are `no_metric`
- `unused_spend`: what you pay for capacity you don't use — an indicator, *not* a saving
- Confidence from history length (full at 6 periods) and usage stability
- Picks the subscription with the most ₹/month at stake (unused spend, or overage)

### 2. 🔒 Privacy-Preserving Mediator
Applies differential privacy (Laplace mechanism) to sanitize sensitive usage data before processing.

- Controlled Laplace noise on `data_used_gb` and `call_minutes_used`
- Configurable noise scale (β parameter)
- Zero-clamping to prevent negative values
- Raw data never leaves the privacy boundary

### 3. 🤝 Negotiation Agent (simulated provider)
Multi-round price negotiation against a **built-in provider simulator** — there is no real provider.

- **Walk-away price**: never agrees to pay more than 95% of today's price, or more than the best catalog alternative costs (the user's BATNA)
- Strategy adapts to utilisation; deal when within 3% and at or below the walk-away price
- **Reproducible**: seeded per subscription and session, so every outcome can be replayed
- Runs only for underutilized plans; full round history persisted

### 4. 🔄 Plan Switching with Approval and Rollback
Compares the negotiated price with the best **catalog alternative** and **proposes** the bigger saving.

- **Alternatives**: catalog plans must cover peak/projected usage + 10% headroom and respect the user's preferences (budget, minimum data/minutes, required features, provider rules); every excluded plan comes with its reasons (`GET /api/subscriptions/{id}/alternatives`)
- **Savings** are measured against the effective current cost (price + average overage), so a bigger plan can be the cheaper option
- **Explanation** on every proposal: what, why, benefits, drawbacks, and the usage data used
- **Cost validation**: downgrade must save ≥ 5%; paying the same or more needs more capacity
- **SLA risk**: low / medium / high based on data + call limit proximity
- **Approval**: a recommendation is applied only when the user approves it (needs `execute_plan_changes` consent); it expires if the plan changed in the meantime
- **History & rollback**: every applied change is recorded in `plan_history` and can be rolled back

### 5. 📝 Explainable Audit Logger
Generates deterministic, human-readable explanations for every decision the system makes.

- Action-specific formatters for analysis, negotiation, and switching
- Immutable audit trail with JSON detail payloads
- Paginated retrieval by user
- Module attribution for traceability

### 6. ⚡ Orchestrator
`app/orchestrator/pipeline.py` runs one decision cycle per call.

- Order: Analyze → Sanitize → Negotiate → Propose; every agent audits its own decision
- Privacy Mediator gate: a missing consent **skips** that agent (and the steps that need it) and is audited as `access_denied`
- No repeat negotiation while a proposal for that subscription awaits a decision
- Every run and step (status, timing, summary, error) is stored and served at `/api/runs/{run_id}`
- A failing agent is rolled back and recorded; independent steps still run
- Final status: `awaiting_approval` / `completed` / `partial` / `failed`

---

## 🛠 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Language** | Python 3.10+ |
| **Web Framework** | FastAPI |
| **ORM** | SQLAlchemy 2.0 |
| **Database** | SQLite (swappable to PostgreSQL) |
| **Validation** | Pydantic v2 |
| **Server** | Uvicorn (ASGI) |
| **Testing** | stdlib `urllib` (zero-dependency integration tests) |

---

## 📁 Project Structure

```
project/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app entry point
│   ├── config.py                # Centralized configuration
│   ├── database.py              # SQLAlchemy engine, session, Base
│   ├── models.py                # ORM models (12 tables)
│   ├── db_migrations.py         # Runs Alembic migrations on startup
│   ├── schemas.py               # Pydantic request/response schemas
│   │
│   ├── api/
│   │   └── routes.py            # All REST endpoints (thin layer)
│   │
│   ├── modules/
│   │   ├── usage_analyzer/
│   │   │   ├── analyzer.py      # Pure analysis logic (stateless)
│   │   │   └── service.py       # DB orchestration
│   │   │
│   │   ├── privacy_mediator/
│   │   │   ├── sanitizer.py     # Pure Laplace noise logic (stateless)
│   │   │   └── service.py       # DB orchestration
│   │   │
│   │   ├── negotiation_agent/
│   │   │   ├── engine.py        # Pure negotiation logic (stateless)
│   │   │   └── service.py       # DB orchestration
│   │   │
│   │   ├── plan_switching/
│   │   │   ├── executor.py      # Pure KPI validation (stateless)
│   │   │   └── service.py       # DB orchestration + rollback
│   │   │
│   │   └── audit_logger/
│   │       ├── formatter.py     # Pure message generation (stateless)
│   │       └── service.py       # DB persistence
│   │
│   ├── services/
│   │   ├── base_service.py      # Reusable DB helpers (save, transactional)
│   │   ├── user_service.py      # User CRUD
│   │   ├── subscription_service.py  # Subscription + usage CRUD
│   │   ├── catalog_service.py   # Plan catalog reads
│   │   ├── recommendation_service.py / run_service.py  # Proposal + run history reads
│   │
│   ├── orchestrator/
│   │   └── pipeline.py          # Decision cycle, run + step recording
│   │
│   └── utils/
│       └── seed_data.py         # Database seeding utility
│
├── migrations/versions/         # Alembic schema migrations
├── tests/                       # pytest suite
├── seed_data.py                 # Convenience CLI for seeding
├── test_integration.py          # End-to-end integration tests
├── requirements.txt
└── README.md
```

### Architecture Principle

Every module follows a strict **two-layer separation**:

| File | Role | Rules |
|------|------|-------|
| `analyzer.py` / `engine.py` / `executor.py` / `sanitizer.py` / `formatter.py` | **Pure logic** | No DB, no framework imports, stateless, easily testable |
| `service.py` | **DB orchestration** | Fetches data, calls pure logic, persists results |

---

## 🔌 API Endpoints

All endpoints are prefixed with `/api`. Responses follow a uniform envelope:

```json
// Success
{ "status": "success", "data": { ... } }

// Error (403 consent missing, 404 not found, 409 state conflict, 422 validation — adds "errors")
{ "status": "error", "message": "..." }
```

The pipeline acts on a single **optimization target**: the most underutilized active
subscription. Negotiation and switching both run against it. When no subscription is
underutilized they are skipped (`"skipped"` in the response). A switch is only ever
*proposed* for a price the provider **accepted**, never for a higher price at the same capacity.

### Core Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/users` | Register a new user |
| `GET` | `/api/users` | List all users (paginated) |
| `GET` | `/api/users/{user_id}` | Get a user by ID |
| `POST` | `/api/subscriptions` | Create a subscription |
| `GET` | `/api/subscriptions/user/{user_id}` | List user's subscriptions |
| `GET` | `/api/subscriptions/{sub_id}` | Get a subscription by ID |
| `POST` | `/api/usage` | Ingest a usage record (linked to the user's subscription with that provider, or an explicit `subscription_id`) |
| `GET` | `/api/usage/user/{user_id}` | List user's usage records |
| `GET` | `/api/subscriptions/{sub_id}/history` | Every applied change to a subscription |
| `GET` | `/api/subscriptions/{sub_id}/alternatives` | Catalog plans that fit, best saving first, and why the rest don't (consent: `usage_analysis`) |
| `GET` / `PUT` | `/api/users/{user_id}/preferences` | Budget, minimum data/minutes, required features, provider rules |
| `GET` | `/api/users/{user_id}/consents` | Every consent scope and whether it is granted (default: denied) |
| `PUT` | `/api/users/{user_id}/consents/{scope}` | Grant or revoke one scope (audited) |
| `GET` | `/api/plans` | Plan catalog, cheapest first (`?provider=`, `?category=`) |

> The seeded catalog is **demo data** (`source: "demo_seed"`), not live provider pricing.
> Consents are enforced by every agent (default: denied). Preferences constrain which catalog plans are recommended.

### Intelligent Modules

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/analyze/{user_id}` | Run usage analysis (consent: `usage_analysis`) |
| `POST` | `/api/sanitize/{user_id}` | Get sanitized (private) usage data (consent: `share_usage_with_provider`) |
| `POST` | `/api/negotiate/{user_id}` | Simulated price negotiation (consents: all three above) |
| `POST` | `/api/switch/{user_id}` | Evaluate the latest deal and **propose** a change (nothing is applied) |
| `GET` | `/api/recommendations/user/{user_id}` | List proposals (`?status=proposed`) |
| `GET` | `/api/recommendations/{id}` | One proposal with rationale and evidence |
| `POST` | `/api/recommendations/{id}/approve` | Apply it (consent: `execute_plan_changes`) |
| `POST` | `/api/recommendations/{id}/reject` | Reject it; nothing changes |
| `POST` | `/api/subscriptions/{sub_id}/rollback` | Undo the last applied change (consent: `execute_plan_changes`) |
| `GET` | `/api/audit/{user_id}` | Get explainable audit logs |
| `GET` | `/api/audit/user/{user_id}` | Get audit logs (alternate) |
| `GET` | `/api/negotiation/{sub_id}/history` | Get negotiation round history |

### Unified Pipeline

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/run-cycle/{user_id}` | **Run one decision cycle** (analyze → sanitize → negotiate → propose) |
| `GET` | `/api/runs/user/{user_id}` | A user's runs with their steps |
| `GET` | `/api/runs/{run_id}` | One run: each agent's status, timing, summary, error |

---

## 🚀 How to Run

### Prerequisites

- Python 3.10 or higher
- pip

### 1. Clone the repository

```bash
git clone https://github.com/your-username/agentic-digital-twin.git
cd agentic-digital-twin
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Seed the database with demo data

```bash
python seed_data.py --reset
```

This creates the demo plan catalog, 5 users with subscriptions and usage history, and full
consent for the demo users. The data is generated from a fixed seed (`--random-seed`, default
42), so every reset builds the same dataset.

### 5. Start the server

```bash
uvicorn app.main:app --reload
```

The API is now live at **http://127.0.0.1:8000**

### 6. Explore the interactive docs

Open your browser and navigate to:

- **Swagger UI** → [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc** → [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

### 7. Run the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite uses a throwaway SQLite database and needs no running server.
`test_integration.py` is an older smoke script that runs against a live server
(`python test_integration.py` with uvicorn running).

---

## 📡 Example API Calls

### Run the Full Pipeline (Single Click)

```bash
curl -X POST http://127.0.0.1:8000/api/run-cycle/2   # seeded user 2
```

**Response** (abridged):
```json
{
  "status": "success",
  "data": {
    "run_id": 1,
    "user_id": 2,
    "analysis": { "recommendation": "downgrade", "efficiency": 0.195, "binding_metric": "calls", "optimization_target": { "subscription_id": 4 } },
    "sanitization": { "sanitized_usage": [] },
    "negotiation": { "status": "final", "reservation_price": 449.0, "best_alternative": { "plan_name": "Hero 449" }, "simulated": true },
    "switching": { "proposed": true, "action": "switch_plan", "recommendation_id": 1, "proposed_plan": { "plan_name": "Hero 449" }, "risk_flag": "low" },
    "recommendation": { "recommendation_id": 1, "action": "switch_plan", "current_cost": 599.0, "projected_cost": 449.0, "estimated_monthly_savings": 150.0, "explanation": { "what": "Switch from Vi Max 599 to Vi Hero 449 at ₹449.0/month.", "benefits": ["..."], "drawbacks": ["..."] } },
    "audit_logged": ["analysis", "negotiation", "switching"],
    "skipped": null,
    "errors": null,
    "final_status": "awaiting_approval"
  }
}
```

### Analyze Usage

```bash
curl -X POST http://127.0.0.1:8000/api/analyze/1
```

### Negotiate a Better Price

```bash
curl -X POST http://127.0.0.1:8000/api/negotiate/1
```

### Propose a Switch, then Decide

```bash
curl -X POST http://127.0.0.1:8000/api/switch/1
curl -X POST http://127.0.0.1:8000/api/recommendations/2/approve   # or /reject
```

### Get Privacy-Sanitized Data

```bash
curl -X POST http://127.0.0.1:8000/api/sanitize/1
```

### View Audit Trail

```bash
curl http://127.0.0.1:8000/api/audit/1
```

### Create a User

```bash
curl -X POST http://127.0.0.1:8000/api/users \
  -H "Content-Type: application/json" \
  -d '{"name": "John Doe", "email": "john@example.com", "phone": "+91-99999-00000"}'
```

### Create a Subscription

```bash
curl -X POST http://127.0.0.1:8000/api/subscriptions \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 1,
    "provider": "Jio",
    "plan_name": "Gold 599",
    "monthly_cost": 599.0,
    "data_limit_gb": 100.0,
    "call_minutes_limit": 500
  }'
```

### Ingest Usage Data

```bash
curl -X POST http://127.0.0.1:8000/api/usage \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 1,
    "provider": "Jio",
    "period_start": "2026-01-01T00:00:00",
    "period_end": "2026-01-31T23:59:59",
    "data_used_gb": 35.5,
    "call_minutes_used": 120,
    "billing_amount": 599.0
  }'
```

---

## 🗄 Database Schema

| Table | Purpose | Key relationships |
|-------|---------|-------------------|
| `users` | Registered users | — |
| `subscriptions` | A user's current plan and terms | `user_id` → users, `catalog_plan_id` → plan_catalog |
| `usage_data` | Per-period consumption | `user_id` → users, `subscription_id` → subscriptions |
| `negotiation_history` | One row per negotiation round | `subscription_id` → subscriptions |
| `audit_logs` | Plain-English record of every decision | `user_id` → users, `run_id` → agent_runs |
| `plan_catalog` | Plans providers offer (the alternatives) | unique (`provider`, `plan_name`) |
| `user_preferences` | Budget, minimums, required features (1:1) | `user_id` → users |
| `consents` | Granted scopes; no row = denied | unique (`user_id`, `scope`) |
| `plan_history` | Every applied change, with before/after terms | `subscription_id`, `recommendation_id` |
| `agent_runs`, `agent_run_steps` | Orchestrator runs and per-agent steps | `user_id`, `run_id` |
| `recommendations` | Proposed actions and their approval status | `user_id`, `subscription_id`, `run_id`, `proposed_plan_id` |

SQLite foreign keys are enforced, so deleting a user cascades to their data.

### Migrations

The schema is managed by Alembic (`migrations/versions/`). The app upgrades the
database to the latest revision on startup. A database created by an older
version of the app (no `alembic_version` table) is recognised, stamped at the
baseline, and upgraded in place, keeping its data.

```bash
alembic upgrade head                                   # apply migrations manually
alembic revision --autogenerate -m "describe change"   # after editing app/models.py
```

---

## 🔮 Future Enhancements

| Area | Enhancement |
|------|-------------|
| **ML-Powered Analysis** | Replace rule-based analyzer with a trained model for smarter recommendations |
| **Real Provider APIs** | Integrate with Jio / Airtel / AWS billing APIs for live data ingestion |
| **Reinforcement Learning** | Train the negotiation agent using RL for optimal offer strategies |
| **Stronger Privacy** | Implement ε-differential privacy with formal privacy budgets |
| **Multi-User Scheduling** | Cron-based batch processing to run pipelines for all users periodically |
| **PostgreSQL Migration** | Swap SQLite for PostgreSQL for production-scale deployments |
| **Authentication** | Add JWT-based auth and role-based access control |
| **WebSocket Notifications** | Real-time pipeline progress updates via WebSocket |
| **Dashboard UI** | React/Next.js frontend with usage charts and audit timeline |
| **Docker** | Containerize with Docker Compose for one-command deployment |

---

## 📄 License

This project is open-sourced under the [MIT License](LICENSE).

---

<p align="center">
  Built with ❤️ as an Agentic AI demonstration project
</p>
