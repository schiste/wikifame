web: uvicorn wikifame.app:app --host 0.0.0.0 --port ${PORT:-8000}
worker: python -m wikifame.worker
prewarm: python -m wikifame.prewarm --days 7
backfill: python -m wikifame.backfill --batches 1
cleanup: python -m wikifame.cleanup
