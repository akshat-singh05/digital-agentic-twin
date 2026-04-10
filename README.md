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

It continuously monitors usage patterns, protects user privacy with differential privacy, negotiates better deals with providers, validates plan switches against KPIs and SLA risk, and maintains an explainable audit trail of every decision — all executable through a **single API call**.

### 🎯 Key Highlights

- **Fully autonomous pipeline** — one endpoint triggers the entire optimization cycle
- **Modular architecture** — every module has a clean separation of pure logic vs DB persistence
- **Privacy-first** — raw data is never exposed; Laplace noise is applied before any external interaction
- **Explainable AI** — every decision generates a human-readable, deterministic audit message
- **Safe plan switching** — KPI validation + SLA risk assessment + atomic rollback on failure

---

## ✨ Features

### 1. 📊 Usage Analyzer
Evaluates subscription utilization across all active plans using rule-based efficiency scoring.

- Multi-subscription comparison with best-plan recommendation
- Efficiency categories: `underutilized` / `optimal` / `overutilized`
- Confidence scoring based on available billing history (ramps to 1.0 after 6 months)
- Savings estimation for underutilized plans

### 2. 🔒 Privacy-Preserving Mediator
Applies differential privacy (Laplace mechanism) to sanitize sensitive usage data before processing.

- Controlled Laplace noise on `data_used_gb` and `call_minutes_used`
- Configurable noise scale (β parameter)
- Zero-clamping to prevent negative values
- Raw data never leaves the privacy boundary

### 3. 🤝 Autonomous Negotiation Agent
Simulates multi-round price negotiation between the user's digital twin and the service provider.

- 3–5 round offer–counteroffer sessions
- Strategy adapts to utilization (aggressive for underutilized, gentle for optimal)
- Acceptance threshold: ≤ 3% gap between offers
- Full round history persisted for transparency

### 4. 🔄 Plan Switching with Rollback
Validates and applies negotiated plan changes using KPI thresholds and SLA risk assessment.

- **Cost validation**: downgrade must save ≥ 5%
- **SLA risk**: low / medium / high based on data + call limit proximity
- **Atomic transactions**: automatic rollback on failure
- **Snapshot-based recovery**: previous plan state stored as JSON

### 5. 📝 Explainable Audit Logger
Generates deterministic, human-readable explanations for every decision the system makes.

- Action-specific formatters for analysis, negotiation, and switching
- Immutable audit trail with JSON detail payloads
- Paginated retrieval by user
- Module attribution for traceability

### 6. ⚡ Unified System Pipeline
Single-click intelligent workflow that connects all 5 modules end-to-end.

- Sequential execution: Analyze → Sanitize → Negotiate → Switch → Audit
- Graceful failure handling — continues pipeline on partial failures
- Final status: `completed` / `partial` / `failed`
- Cross-module output passing

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
│   ├── models.py                # ORM models (5 tables)
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
│   │   └── system_service.py    # Unified pipeline orchestrator
│   │
│   └── utils/
│       └── seed_data.py         # Database seeding utility
│
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

// Error
{ "status": "error", "message": "..." }
```

### Core Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/users` | Register a new user |
| `GET` | `/api/users` | List all users (paginated) |
| `GET` | `/api/users/{user_id}` | Get a user by ID |
| `POST` | `/api/subscriptions` | Create a subscription |
| `GET` | `/api/subscriptions/user/{user_id}` | List user's subscriptions |
| `GET` | `/api/subscriptions/{sub_id}` | Get a subscription by ID |
| `POST` | `/api/usage` | Ingest a usage record |
| `GET` | `/api/usage/user/{user_id}` | List user's usage records |

### Intelligent Modules

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/analyze/{user_id}` | Run usage analysis |
| `POST` | `/api/sanitize/{user_id}` | Get sanitized (private) usage data |
| `POST` | `/api/negotiate/{user_id}` | Run autonomous price negotiation |
| `POST` | `/api/switch/{user_id}` | Attempt KPI-validated plan switch |
| `GET` | `/api/audit/{user_id}` | Get explainable audit logs |
| `GET` | `/api/audit/user/{user_id}` | Get audit logs (alternate) |
| `GET` | `/api/negotiation/{sub_id}/history` | Get negotiation round history |

### Unified Pipeline

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/run-cycle/{user_id}` | **Execute full pipeline** (analyze → sanitize → negotiate → switch → audit) |

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

This creates 5 users, ~12 subscriptions, and ~54 usage records with realistic data.

### 5. Start the server

```bash
uvicorn app.main:app --reload
```

The API is now live at **http://127.0.0.1:8000**

### 6. Explore the interactive docs

Open your browser and navigate to:

- **Swagger UI** → [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc** → [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

### 7. Run integration tests (optional)

```bash
# With the server running in another terminal:
python test_integration.py
```

---

## 📡 Example API Calls

### Run the Full Pipeline (Single Click)

```bash
curl -X POST http://127.0.0.1:8000/api/run-cycle/1
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "user_id": 1,
    "analysis": {
      "efficiency": 0.33,
      "recommendation": "downgrade",
      "savings_estimate": 401.33,
      "usage_category": "underutilized",
      "confidence_score": 1.0
    },
    "sanitization": {
      "sanitized_usage": [ ... ]
    },
    "negotiation": {
      "final_price": 486.43,
      "savings_pct": 8.29,
      "total_rounds": 5,
      "status": "accepted"
    },
    "switching": {
      "applied": true,
      "risk_flag": "low",
      "projected_cost": 486.43,
      "rollback": false
    },
    "audit_logged": ["analysis", "negotiation", "switching"],
    "errors": null,
    "final_status": "completed"
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

### Switch Plan

```bash
curl -X POST http://127.0.0.1:8000/api/switch/1
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

```
┌──────────┐       ┌────────────────┐       ┌───────────────────┐
│  users   │──1:N──│ subscriptions  │──1:N──│ negotiation_history│
│          │       │                │       │                   │
│ id       │       │ id             │       │ id                │
│ name     │       │ user_id (FK)   │       │ subscription_id   │
│ email    │       │ provider       │       │ round_number      │
│ phone    │       │ plan_name      │       │ agent_offer       │
│ created_at│      │ monthly_cost   │       │ provider_counter  │
└──────────┘       │ data_limit_gb  │       │ status            │
     │             │ call_minutes.. │       │ notes             │
     │             │ is_active      │       └───────────────────┘
     │             │ previous_plan..│
     │             └────────────────┘
     │
     ├──1:N──┌──────────────┐
     │       │  usage_data  │
     │       │              │
     │       │ id           │
     │       │ user_id (FK) │
     │       │ provider     │
     │       │ data_used_gb │
     │       │ call_minutes │
     │       │ billing_amt  │
     │       └──────────────┘
     │
     └──1:N──┌──────────────┐
             │  audit_logs  │
             │              │
             │ id           │
             │ user_id (FK) │
             │ action       │
             │ module       │
             │ description  │
             │ details (JSON)│
             └──────────────┘
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
