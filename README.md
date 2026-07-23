# 🧵 Strands Team Platform

An internal coordination harness for an AI-enabled strike team — humans and AI
agents tracking milestones, asking questions, recording decisions, posting
standups, and planning work through a shared Chief-of-Staff agent.

Built on the [Strands Agents SDK](https://github.com/strands-agents/harness-sdk)
(backend agents) and [assistant-ui](https://github.com/assistant-ui/assistant-ui)
(chat frontend).

## Architecture

```
frontend/  Next.js 16 + @assistant-ui/react
  ├─ /            chat with the Chief of Staff (streaming, markdown, per-browser thread)
  └─ /dashboard   milestones · tasks · questions · decisions · standups · calendar · notes · activity

backend/   FastAPI + Strands Agents + SQLite
  ├─ POST /api/chat          SSE stream from the orchestrator agent
  ├─ GET  /api/<entity>      read-only REST for the dashboard
  ├─ app/agents/team_agent   Chief-of-Staff orchestrator (+ planner sub-agent via agents-as-tools)
  ├─ app/tools/              18 tools over SQLite (work, collab, schedule)
  └─ data/                   platform.db + per-thread session files (gitignored)
```

The agent is the write path: you *tell* it things ("mark task 12 done", "plan
our Q3 launch", "record that we chose Postgres") and it persists them with
tools. The dashboard is the read path. Every tool call lands in the activity
log, and conversations persist per thread via Strands `FileSessionManager`.

## Setup

### Backend

```bash
cd backend
uv venv .venv && uv pip install -e . --python .venv/bin/python
cp .env.example .env        # set your API key(s)
.venv/bin/uvicorn app.main:app --port 8000 --reload
```

Model provider is configurable in `.env`:

| Variable | Values | Default |
|---|---|---|
| `STRANDS_MODEL_PROVIDER` | `anthropic` \| `openai` | `anthropic` |
| `STRANDS_MODEL_ID` | any model ID | `claude-opus-4-8` / `gpt-5` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | credential for the chosen provider | — |

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # points at http://localhost:8000
npm run dev                        # http://localhost:3000
```

Or run both at once from the repo root: `./dev.sh`

## Try it

- *"Plan a project to migrate our billing service — call the project `billing`."*
  → the planner sub-agent creates milestones + tasks; watch them appear on the dashboard.
- *"Post a standup for Mario: shipped the API, next is auth, blocked on the vendor contract."*
- *"Log a question from me to the infra agent: do we have staging capacity for load tests?"*
- *"Record the decision that we're using SQLite until we hit 10 concurrent agents."*
- *"Schedule a retro Friday at 3pm with the whole team."*
- *"What's blocked right now, and what's on the calendar this week?"*

## What's next

A 4-agent panel (Product Manager, Workflow Architect, AI Engineer, UX
Researcher) ideated the roadmap for strike-team use — see
[docs/ROADMAP.md](docs/ROADMAP.md) for the full synthesis. Headlines:

1. Semantic team memory (RAG over the whole workspace)
2. Human-in-the-loop approval gates (Strands interrupts → approval cards)
3. Engagement intake & triage queue
4. Project-class playbooks/templates
5. Autonomous daily digest + blocker detector ("My Day" view)
6. Agent work queue with diff-style human review
7. Blocker & escalation register
8. Handoff contracts + rotation handoff generator
9. MCP integrations (GitHub/Slack/calendar) + Slack surface
10. Provenance badges, observability (OpenTelemetry), and cost tracking
