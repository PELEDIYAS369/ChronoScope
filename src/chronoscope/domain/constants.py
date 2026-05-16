# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — System Constants
All system-wide constants live here.
No magic numbers anywhere else in the codebase.
"""

# ---------------------------------------------------------------------------
# CCSDS Protocol Constants
# ---------------------------------------------------------------------------

CCSDS_PRIMARY_HEADER_SIZE = 6          # bytes
CCSDS_MAX_PACKET_SIZE = 65536          # bytes
CCSDS_APID_MAX = 2047
CCSDS_SEQUENCE_COUNT_MAX = 16383
CCSDS_VERSION_NUMBER = 0

# ---------------------------------------------------------------------------
# Replay Engine Constants
# ---------------------------------------------------------------------------

REPLAY_DEFAULT_SPEED = 1.0             # 1x realtime
REPLAY_MAX_SPEED = 100.0              # 100x realtime maximum
REPLAY_MIN_SPEED = 0.1                # 0.1x minimum
REPLAY_BUFFER_SIZE = 10_000           # packets in memory at once
REPLAY_CHUNK_SIZE = 1_000             # packets per load chunk

# ---------------------------------------------------------------------------
# Anomaly Detection Thresholds
# ---------------------------------------------------------------------------

ANOMALY_CONFIDENCE_THRESHOLD = 0.75   # minimum confidence to flag
ANOMALY_CRITICAL_THRESHOLD = 0.95     # confidence for critical flags
ANOMALY_MAX_FLAGS_PER_SESSION = 10_000

# ---------------------------------------------------------------------------
# Audit Log Constants
# ---------------------------------------------------------------------------

AUDIT_HASH_ALGORITHM = "sha256"
AUDIT_LOG_VERSION = "1.0"
AUDIT_MAX_CHAIN_LENGTH = 1_000_000    # entries before rotation

# ---------------------------------------------------------------------------
# Storage Constants
# ---------------------------------------------------------------------------

MAX_SESSION_PACKETS = 10_000_000      # 10M packets per session
MAX_PACKET_SIZE_BYTES = 65_536
DEFAULT_ENCODING = "utf-8"

# ---------------------------------------------------------------------------
# API Constants
# ---------------------------------------------------------------------------

API_VERSION = "v1"
API_MAX_PAGE_SIZE = 1_000
API_DEFAULT_PAGE_SIZE = 100
API_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# Spacecraft Identifiers (Public Data Sources)
# ---------------------------------------------------------------------------

SPACECRAFT_DSCOVR = "DSCOVR"
SPACECRAFT_ACE = "ACE"
SPACECRAFT_WIND = "WIND"
SPACECRAFT_SOHO = "SOHO"

# ---------------------------------------------------------------------------
# Data Source URLs (NASA Public Data)
# ---------------------------------------------------------------------------

NASA_SPDF_BASE_URL = "https://spdf.gsfc.nasa.gov"
NASA_CDA_WEB_URL = "https://cdaweb.gsfc.nasa.gov"
NOAA_DSCOVR_URL = "https://services.swpc.noaa.gov/products/solar-wind"