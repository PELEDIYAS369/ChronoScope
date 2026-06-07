# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""Event/label ingestion for the ChronoScope corpus.

Labels (geomagnetic indices, ICME intervals) are fetched from external
catalogs and stored in a sibling `labels/` tree next to the telemetry corpus,
joinable by timestamp. See docs/DECISIONS.md (DEC-008) for the design.
"""
