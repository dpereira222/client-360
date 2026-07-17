# FalconX Client 360

Cross-product client intelligence for the IC / sales desk. One screen for a
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
/home/ubuntu/market-color/start_apps.sh
```

## Files

| File | Role |
|------|------|
| `app.py` | Streamlit UI — presentation only, never touches Databricks |
| `data.py` | **The data contract.** One function per data need; mock today, Databricks later |
| `mock.py` | Deterministic fake data so the UI demos without a DB (engineering can ignore) |
| `theme.py` | Colors + plotly template (validated CVD-safe data-viz palette) |
| `WHITEPAPER.md` | Scope, security constraints, per-panel data requirements, phasing |
| `app.yaml` | Databricks App manifest |

## Going live

1. Engineering prepares/normalizes the Databricks tables named in `WHITEPAPER.md`.
2. Replace each function body in `data.py` with a query returning the documented
   schema. **Do not change `app.py`.**
3. Set `CLIENT360_MOCK=0`.
