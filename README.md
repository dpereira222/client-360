# FalconX Client 360

Cross-product client intelligence and sales CRM for the IC / sales desk. One screen for a
client relationship: **all legal entities, spot, derivatives, options, credit,
and FX** — trades, positions, revenue, and risk in one place.

This is the **prototype + spec** that goes to engineering. It runs today on a
mock data layer with zero Databricks access; engineering wires it to live data
by implementing the seam in `data.py`. See **`WHITEPAPER.md`** for the full
requirements and the data contract.

## Run locally (demo / mock data)

```bash
cd /home/ubuntu/market-color/client-360
CLIENT360_MOCK=1 streamlit run app.py --server.port 8503
# → http://localhost:8503
```

Or use the repo launcher (starts it on 8503 alongside the other apps):

```bash
/home/ubuntu/market-color/start## Layout

Top navigation, two workspaces (the selected client follows you between pages):

- **Client 360**: Overview (Summary · Trades) · Risk (Positions · Options · Credit) ·
  Peers · Relationship (Brief · Notes · Pitches & Chat) · Analyst
- **Desk pages** (whole book): Expiries · Idea targeting · Daily color

## Files

| File | Role |
|------|------|
| `app.py` | Router (`st.navigation`, top nav) — no logic |
| `views/client.py` | Client 360 page (all client tabs) |
| `views/expiries.py`, `views/targeting.py`, `views/daily_color.py` | Desk pages |
| `ui.py` | Shared UI helpers: sidebar client picker, masthead, KPI strip, formatting, cached views |
| `theme.py` / `.streamlit/config.toml` | Institutional theme (navy sidebar, Inter), CVD-safe plotly palette |
| `data.py` | **The data contract.** One function per data need; mock today, Databricks later |
| `options.py` | Multi-leg options structure detection |
| `peers.py` | Anonymous peer cohorts (k-anonymity + dominance rules) |
| `pitches.py` / `chat_intel.py` | Pitch tracker / LLM chat → trade-idea extractor |
| `notes.py` / `tags.py` | CRM notes + deterministic auto-hashtags |
| `expiries.py` | Live option structures, pin risk, Monday digest |
| `targeting.py` | "Who will bite?" idea ranking + outcome learning |
| `daily_color.py` | 4:30pm ET desk color (`python3 daily_color.py` for a scheduled job) |
| `llm.py` | Opus 5.5 analyst (⚠️ prod must use in-Databricks serving — WHITEPAPER §10) |
| `mock*.py` | Deterministic fake data (engineering can ignore) |
| `tests/` | `python3 -m pytest -q tests` (includes AppTest page renders) |
| `WHITEPAPER.md` | Scope, security constraints, data contract, privacy rules, phasing |
| `app.yaml` | Databricks App manifest |

p manifest |

## Going live

1. Engineering prepares/normalizes the Databricks tables named in `WHITEPAPER.md`.
2. Replace each function body in `data.py` with a query returning the documented
   schema. **Do not change `app.py` / `views/`.**
3. Set `CLIENT360_MOCK=0`.
