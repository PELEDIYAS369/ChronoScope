# Installation & Setup Guide

## System Requirements

- **Python 3.13+** (3.12 may work but is not officially supported)
- **Operating System:** Windows 10+, macOS 11+, Linux (Ubuntu 20.04+)
- **RAM:** 4GB minimum (8GB recommended)
- **Disk:** 500MB for application + data
- **Network:** Internet access for NASA DSCOVR data (optional for local testing)

## Quick Start (5 minutes)

### 1. Clone Repository

```bash
git clone https://github.com/PELEDIYAS369/ChronoScope.git
cd ChronoScope
```

### 2. Create Virtual Environment

**On macOS/Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**On Windows:**
```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Verify Installation

```bash
pytest tests/ -v
```

You should see `246 tests passing` if everything is working correctly.

## Running the Demo

### Live NASA Demo (Requires Internet)

```bash
python scripts/sale_demo.py
```

This will:
- Connect to NOAA DSCOVR spacecraft
- Ingest real solar wind telemetry
- Replay the session
- Run AI anomaly detection
- Generate professional reports

Expected output:
```
✓ Connected to real NASA spacecraft (DSCOVR)
✓ Ingested 223 real telemetry packets
✓ Loaded session into deterministic replay engine
✓ Seeked to any point in the timeline instantly
✓ Verified mathematical determinism (fingerprint: abc123...)
✓ Ran AI anomaly detection with explainable output
✓ Logged 10 audit entries with SHA-256 chain
✓ Generated dashboard health snapshot
✓ Produced professional JSON and Markdown reports
```

### Local Demo (No Internet Required)

```bash
python scripts/demo_local.py
```

Uses pre-recorded sample telemetry data.

## CLI Usage

### Status Check

```bash
python -m src.chronoscope.cli status
```

### Ingest Data

```bash
# Ingest 2 hours of DSCOVR data
python -m src.chronoscope.cli ingest --spacecraft DSCOVR --hours 2

# Ingest from local file
python -m src.chronoscope.cli ingest --file telemetry.csv
```

### Audit Trail

```bash
python -m src.chronoscope.cli audit
```

### Generate Report

```bash
python -m src.chronoscope.cli report --format json
python -m src.chronoscope.cli report --format markdown
```

## API Server

Start the FastAPI server:

```bash
python -m uvicorn src.chronoscope.api:app --host 0.0.0.0 --port 8000
```

API will be available at: `http://localhost:8000`

API Documentation: `http://localhost:8000/docs`

### Example Requests

```bash
# Get status
curl http://localhost:8000/status

# Create new session
curl -X POST http://localhost:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"name": "Demo Session", "spacecraft": "DSCOVR"}'

# List anomalies
curl http://localhost:8000/anomalies
```

## Testing

### Run All Tests

```bash
pytest tests/ -v
```

### Run Specific Test Category

```bash
# Unit tests only
pytest tests/unit/ -v

# Integration tests only
pytest tests/integration/ -v

# Performance benchmarks
pytest tests/benchmarks/ -v --benchmark-only
```

### Generate Coverage Report

```bash
pytest tests/ --cov=src --cov-report=html
# Open htmlcov/index.html in your browser
```

## Troubleshooting

### Issue: `ModuleNotFoundError: No module named 'src'`

**Solution:** Ensure you're in the repository root and virtual environment is activated.

```bash
pwd  # Should show .../ChronoScope
which python  # Should show .../ChronoScope/.venv/...
```

### Issue: Tests fail with `ImportError`

**Solution:** Reinstall dependencies in development mode.

```bash
pip install -e .
pip install -r requirements.txt
```

### Issue: Cannot connect to NASA DSCOVR data

**Solution:** Check network connection or use local demo.

```bash
# Test connection
curl https://api.noaa.gov/dscovr/latest

# Fall back to local demo
python scripts/demo_local.py
```

### Issue: Permission denied on `.venv/bin/activate`

**Solution (Linux/macOS):** Make it executable.

```bash
chmod +x .venv/bin/activate
source .venv/bin/activate
```

## Environment Variables (Optional)

Create a `.env` file in the repository root:

```bash
# API settings
API_HOST=0.0.0.0
API_PORT=8000
API_DEBUG=false

# Data sources
DSCOVR_API_URL=https://api.noaa.gov/dscovr/
DSCOVR_TIMEOUT=30

# Logging
LOG_LEVEL=INFO
LOG_FILE=chronoscope.log
```

## Production Deployment

For enterprise/production deployment:

1. **Use Python 3.13+ certified environment**
2. **Enable cryptographic verification** of audit chains
3. **Backup audit logs** to immutable storage
4. **Monitor API and CLI** access
5. **Run on air-gapped network** for maximum security

For deployment support, contact: **deployment@chronoscope.ai**

## Next Steps

- Read the [README](README.md) for architecture overview
- Review [SECURITY.md](SECURITY.md) for security practices
- Check out test examples in `tests/` for usage patterns
- Run the live demo: `python scripts/sale_demo.py`

## Support

- **Issues:** GitHub Issues (for evaluation feedback)
- **Bugs:** security@chronoscope.ai (for security issues)
- **Business:** business@chronoscope.ai (partnerships, licensing)
- **Technical:** [Create an issue](https://github.com/PELEDIYAS369/ChronoScope/issues)

---

*ChronoScope AI Inc. — Built in Toronto, Canada*
