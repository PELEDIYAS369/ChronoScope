# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Historical Corpus Storage

Persists TelemetryPacket streams as partitioned Parquet files and provides
DuckDB-backed SQL query over them. See DEC-004 for the storage strategy
decision and DEC-005 for the ingester column-mapping verification.
"""
