# Dogfooding Plan - Authority Runtime Research Agent

**Goal**: Use Authority Runtime daily with a real research agent to generate concrete metrics for Ribbit pitch.

**Timeline**: Week 2 (Dec 26 - Jan 2)

---

## 🎯 The Use Case: AI Research Assistant

### What It Does
Research agent that helps with Authority Runtime development:
- Market research (competitors, pricing, features)
- Technical research (papers, frameworks, best practices)
- Developer research (GitHub repos, code examples)
- News monitoring (AI agent trends, VC activity)

### Why This Validates Authority Runtime
1. **Real daily usage** - Not a demo, actual work
2. **Measurable metrics** - Every query tracked
3. **Authentic pitch** - "I use this to build this"
4. **Diverse queries** - Different tools/contexts each time
5. **Concrete savings** - Real $ saved, not estimates

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CLI: authority-research                   │
│              "What are top AI agent frameworks?"             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                   Authority Runtime Wrapper                  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 1. Create parent envelope (full scopes)             │   │
│  │ 2. LLM selects minimal skill (web_search)           │   │
│  │ 3. Create child envelope (narrowed)                 │   │
│  │ 4. Execute tool with minimal context                │   │
│  │ 5. Save metrics to SQLite                           │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│               CrewAI/LangChain Research Agent                │
│  Tools: [web_search, scrape_page, summarize, github_search] │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      SQLite Database                         │
│  Tables: envelopes, queries, metrics, token_savings         │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                  Dashboard (localhost:8000)                  │
│  Charts: Token reduction, cost savings, query history       │
└─────────────────────────────────────────────────────────────┘
```

---

## 📦 Components to Build

### 1. Research Agent (`research-agent/`)

**File**: `authority-runtime-python/research_agent/agent.py`

```python
from crewai import Agent, Task, Crew
from authority_runtime import AuthorityWrapper

# Define research agent
researcher = Agent(
    role="AI Research Analyst",
    goal="Research AI agent frameworks and tools",
    tools=[web_search, scrape_page, summarize],
)

# Wrap with Authority Runtime
authority_researcher = AuthorityWrapper(
    agent=researcher,
    initial_scopes=["read:web", "write:summary"],
    llm_compiler="gpt-4o-mini",
)
```

**Tools**:
- `web_search`: Tavily or DuckDuckGo
- `scrape_page`: BeautifulSoup
- `summarize`: LLM-based summarization
- `github_search`: GitHub API

### 2. Metrics Database (`metrics/`)

**File**: `authority-runtime-python/metrics/schema.sql`

```sql
-- Envelope chain storage
CREATE TABLE envelopes (
    id INTEGER PRIMARY KEY,
    envelope_id TEXT UNIQUE,
    parent_envelope_id TEXT,
    step_number INTEGER,
    skill_name TEXT,
    scopes TEXT,  -- JSON array
    context_fields TEXT,  -- JSON array
    created_at TIMESTAMP,
    signature TEXT
);

-- Query tracking
CREATE TABLE queries (
    id INTEGER PRIMARY KEY,
    query_text TEXT,
    root_envelope_id TEXT,
    total_steps INTEGER,
    avg_token_reduction REAL,
    compiler_cost_usd REAL,
    net_savings_usd REAL,
    created_at TIMESTAMP
);

-- Aggregate metrics
CREATE TABLE daily_metrics (
    date DATE PRIMARY KEY,
    total_queries INTEGER,
    avg_token_reduction REAL,
    total_savings_usd REAL,
    total_compiler_cost_usd REAL
);
```

**File**: `authority-runtime-python/metrics/db.py`

```python
import sqlite3
from datetime import datetime

class MetricsDB:
    def __init__(self, db_path="metrics.db"):
        self.conn = sqlite3.connect(db_path)
        self.init_schema()

    def save_envelope(self, envelope):
        # Store envelope in DB
        pass

    def save_query(self, query_text, metrics):
        # Store query metrics
        pass

    def get_daily_metrics(self):
        # Return aggregated metrics
        pass
```

### 3. Dashboard (`dashboard/`)

**File**: `authority-runtime-python/dashboard/app.py`

```python
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from metrics.db import MetricsDB

app = FastAPI()
db = MetricsDB()

@app.get("/")
async def dashboard():
    # Render single-page dashboard
    return HTMLResponse(open("dashboard/index.html").read())

@app.get("/api/metrics")
async def get_metrics():
    return db.get_daily_metrics()

@app.get("/api/queries")
async def get_queries():
    return db.get_recent_queries(limit=20)
```

**File**: `authority-runtime-python/dashboard/index.html`

```html
<!DOCTYPE html>
<html>
<head>
    <title>Authority Runtime - Metrics Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
    <h1>Authority Runtime - Live Metrics</h1>

    <!-- Token Reduction Chart -->
    <canvas id="tokenReductionChart"></canvas>

    <!-- Cost Savings -->
    <div id="costSavings">
        <h2>Total Savings: $<span id="savings">0.00</span></h2>
    </div>

    <!-- Recent Queries -->
    <table id="recentQueries">
        <tr>
            <th>Query</th>
            <th>Token Reduction</th>
            <th>Cost Saved</th>
        </tr>
    </table>

    <script>
        // Fetch metrics and render charts
        async function loadMetrics() {
            const response = await fetch('/api/metrics');
            const data = await response.json();
            renderCharts(data);
        }

        loadMetrics();
        setInterval(loadMetrics, 5000);  // Refresh every 5s
    </script>
</body>
</html>
```

### 4. CLI Tool (`cli/`)

**File**: `authority-runtime-python/cli/research.py`

```python
#!/usr/bin/env python3
import click
from research_agent.agent import authority_researcher
from metrics.db import MetricsDB

@click.group()
def cli():
    """Authority Runtime Research Assistant"""
    pass

@cli.command()
@click.argument('query')
def research(query):
    """Run a research query"""
    click.echo(f"🔍 Researching: {query}")

    result = authority_researcher.invoke({"input": query})

    click.echo(f"\n✅ Result:\n{result['output']}")

    metrics = authority_researcher.get_metrics_summary()
    click.echo(f"\n📊 Metrics:")
    click.echo(f"   Token Reduction: {metrics['token_reduction_percent']:.1f}%")
    click.echo(f"   Cost Saved: ${metrics['net_savings_usd']:.6f}")

@cli.command()
def metrics():
    """View metrics summary"""
    db = MetricsDB()
    metrics = db.get_daily_metrics()

    click.echo("📈 Authority Runtime Metrics")
    click.echo(f"   Total Queries: {metrics['total_queries']}")
    click.echo(f"   Avg Token Reduction: {metrics['avg_token_reduction']:.1f}%")
    click.echo(f"   Total Savings: ${metrics['total_savings_usd']:.2f}")

@cli.command()
def dashboard():
    """Start the metrics dashboard"""
    click.echo("🚀 Starting dashboard on http://localhost:8000")
    import uvicorn
    uvicorn.run("dashboard.app:app", host="0.0.0.0", port=8000)

if __name__ == '__main__':
    cli()
```

---

## 🗓️ Week 2 Timeline

### Day 1 (Today - Dec 26)
- [x] LangChain integration complete
- [ ] Fix signature verification bug
- [ ] Set up CrewAI research agent
- [ ] Test with 3 manual queries

**Deliverable**: Working research agent that runs with Authority Runtime

### Day 2 (Dec 27)
- [ ] Create SQLite schema
- [ ] Implement MetricsDB class
- [ ] Update AuthorityWrapper to persist envelopes
- [ ] Test database persistence

**Deliverable**: Metrics being saved to database

### Day 3 (Dec 28)
- [ ] Build FastAPI dashboard server
- [ ] Create single-page HTML dashboard
- [ ] Add Chart.js visualizations
- [ ] Test dashboard with saved metrics

**Deliverable**: Working dashboard showing live metrics

### Day 4-5 (Dec 29-30)
- [ ] Use research agent for real work (10+ queries)
- [ ] Screenshot interesting results
- [ ] Document specific use cases
- [ ] Identify any bugs or issues

**Deliverable**: Real-world usage data

### Weekend (Dec 31 - Jan 1)
- [ ] Polish dashboard UI
- [ ] Add more chart types
- [ ] Record demo video
- [ ] Update docs with real metrics

**Deliverable**: Demo-ready package

### Week 3 Start (Jan 2)
- [ ] Write VALIDATION.md with concrete numbers
- [ ] Create pitch deck slides
- [ ] Prepare for Ribbit meeting

---

## 📊 Metrics to Track

### Per Query
- Query text
- Tools used
- Token reduction %
- Cost saved ($)
- Compiler cost ($)
- Net savings ($)
- Envelope chain (JSON)

### Daily Aggregate
- Total queries
- Average token reduction
- Total savings
- Total compiler cost
- Most-used tools

### For Ribbit Pitch
- "I used this for 2 weeks, made 150 research queries"
- "Average token reduction: 78%"
- "Total cost saved: $12.50"
- "Compiler cost: $0.015 (0.12% of savings)"
- "ROI: 833x"

---

## 🎯 Example Research Queries

Real questions you might ask while building Authority Runtime:

1. **Competitive research**:
   - "What IAM solutions exist for AI agents?"
   - "How does LangSmith handle agent permissions?"
   - "What do enterprises need for AI agent governance?"

2. **Technical research**:
   - "Best practices for Ed25519 signing in Python"
   - "How to optimize LLM compiler latency?"
   - "Resource-level scoping patterns in IAM systems"

3. **Market research**:
   - "Latest VC trends in AI infrastructure"
   - "Ribbit Capital's recent AI investments"
   - "Pricing models for developer infrastructure tools"

4. **Developer research**:
   - "Popular LangChain agent examples on GitHub"
   - "How do people handle agent context management?"
   - "Open source alternatives to paid agent platforms"

---

## ✅ Success Criteria

By end of Week 2:
- [ ] Research agent running daily
- [ ] 20+ real queries processed
- [ ] Dashboard showing live metrics
- [ ] Average token reduction >70%
- [ ] Net cost savings (positive ROI)
- [ ] Screenshot/video proof
- [ ] Concrete numbers for pitch

---

## 🚀 Next Steps

**Right now**:
1. Fix signature verification bug (blocker)
2. Set up CrewAI research agent
3. Run first test query

**Today**:
- Get research agent working
- Use it for 3-5 real queries
- Validate Authority Runtime wrapper works

**This week**:
- Build metrics database
- Create dashboard
- Use daily for real work

---

**Status**: Ready to build
**Blocker**: Signature verification (fixing now)
**ETA to first query**: <2 hours
**ETA to dashboard**: 2-3 days
**ETA to pitch-ready metrics**: 1 week
