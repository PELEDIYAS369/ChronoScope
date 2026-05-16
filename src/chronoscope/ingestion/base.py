# Copyright (c) 2026 Utsav Sojitra. All rights reserved.
# ChronoScope AI — Proprietary and Confidential
# Unauthorized use, copying, or distribution is strictly prohibited.

"""
ChronoScope AI — Base Ingestion Interface
All data sources implement this contract.
New data sources plug in without changing anything else.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator
import structlog

from src.chronoscope.domain.models import TelemetryPacket, MissionSession

logger = structlog.get_logger(__name__)


@dataclass
class IngestionResult:
    """
    Result returned after an ingestion operation completes.
    Always returned — never raise silently.
    """
    success: bool
    source: str
    packets_ingested: int
    packets_failed: int
    start_time: datetime
    end_time: datetime
    errors: list[str]
    session_id: str | None = None

    @property
    def duration_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()

    @property
    def success_rate(self) -> float:
        total = self.packets_ingested + self.packets_failed
        if total == 0:
            return 0.0
        return self.packets_ingested / total


class BaseIngester(ABC):
    """
    Abstract base class for all data ingesters.
    Every data source — NASA, ESA, NOAA, local file —
    implements this interface identically.
    """

    def __init__(self, source_name: str):
        self.source_name = source_name
        self.logger = structlog.get_logger(__name__).bind(
            source=source_name
        )

    @abstractmethod
    def fetch_packets(
        self,
        spacecraft_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Iterator[TelemetryPacket]:
        """
        Yield telemetry packets for the given spacecraft and time range.
        Must yield packets in chronological order.
        Must be deterministic — same inputs always yield same packets.
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this data source is currently reachable."""
        ...

    @abstractmethod
    def get_available_spacecraft(self) -> list[str]:
        """Return list of spacecraft IDs available from this source."""
        ...

    def ingest_into_session(
        self,
        session: MissionSession,
        start_time: datetime,
        end_time: datetime,
    ) -> IngestionResult:
        """
        Fetch packets and load them into a mission session.
        Handles errors gracefully — never crashes the session.
        """
        from datetime import timezone
        ingestion_start = datetime.now(timezone.utc)
        packets_ingested = 0
        packets_failed = 0
        errors: list[str] = []

        self.logger.info(
            "ingestion_started",
            spacecraft_id=session.spacecraft_id,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
        )

        try:
            for packet in self.fetch_packets(
                session.spacecraft_id, start_time, end_time
            ):
                try:
                    session.add_packet(packet)
                    packets_ingested += 1
                except Exception as e:
                    packets_failed += 1
                    errors.append(f"Packet add failed: {e}")
                    self.logger.warning("packet_add_failed", error=str(e))

        except Exception as e:
            errors.append(f"Fetch failed: {e}")
            self.logger.error("ingestion_fetch_failed", error=str(e))

        ingestion_end = datetime.now(timezone.utc)

        result = IngestionResult(
            success=packets_ingested > 0,
            source=self.source_name,
            packets_ingested=packets_ingested,
            packets_failed=packets_failed,
            start_time=ingestion_start,
            end_time=ingestion_end,
            errors=errors,
            session_id=session.session_id,
        )

        self.logger.info(
            "ingestion_complete",
            packets_ingested=packets_ingested,
            packets_failed=packets_failed,
            duration_seconds=result.duration_seconds,
            success=result.success,
        )

        return result