# ML System

> For the full agent architecture (orchestrator, sub-agents, tools, weighting), see [architecture/agents.md](architecture/agents.md).

## ML Pipeline

`ml/training_pipeline.py` — RandomForest trained on price/volume features, exported to ONNX for fast inference.

- Weekly retrain Sunday 2AM via `MLScheduler`
- ONNX fast-path with sklearn fallback (`ml/onnx_inference.py`)
- Features: price, volume, market cap, percent changes

## Portfolio Manager

`ml/portfolio_manager.py` — Batch analysis, opportunity-weighted allocation, diversification scoring (Herfindahl index).
