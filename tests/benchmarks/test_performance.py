"""
ChronoScope AI — Performance Benchmarks
Proves the system handles real-world data volumes.
These numbers go in your SBIR application and buyer demos.
Run with: pytest tests/benchmarks/test_performance.py -v -s
"""

import pytest
import time
from datetime import datetime, timezone, timedelta
from src.chronoscope.domain.models import (
    TelemetryPacket,
    MissionSession,
    MissionPhase,
    PacketType,
)
from src.chronoscope.replay.engine import ReplayEngine
from src.chronoscope.audit.log import AuditLog, AuditEventType
from src.chronoscope.ai.detector import AnomalyDetector


def make_packet(seq: int, offset_sec: int) -> TelemetryPacket:
    return TelemetryPacket.create(
        spacecraft_id="DSCOVR",
        packet_type=PacketType.TELEMETRY,
        apid=100,
        sequence_count=seq % 16384,
        raw_bytes=f"pkt{seq:06d}".encode(),
        parameters={
            "bulk_speed_km_s": 450.0 + (seq % 200),
            "proton_density_n_cc": 5.0 + (seq % 10),
            "ion_temperature_k": 80000.0 + (seq % 50000),
            "bz_gsm_nt": -5.0 + (seq % 8),
        },
        source="benchmark",
        timestamp=datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        + timedelta(seconds=offset_sec),
    )


def make_session(packet_count: int) -> MissionSession:
    session = MissionSession.create(
        spacecraft_id="DSCOVR",
        mission_phase=MissionPhase.NOMINAL,
        start_time=datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc),
    )
    for i in range(packet_count):
        session.add_packet(make_packet(i, i))
    return session


class TestIngestionPerformance:

    def test_1000_packet_session_creation(self):
        """1,000 packets — typical 15-minute window."""
        start = time.perf_counter()
        session = make_session(1_000)
        elapsed = time.perf_counter() - start

        assert session.packet_count == 1_000
        assert elapsed < 1.0, f"1K packets took {elapsed:.3f}s — too slow"
        rate = 1_000 / elapsed
        print(f"\n  1,000 packets: {elapsed * 1000:.1f}ms "
              f"({rate:,.0f} packets/sec)")

    def test_10000_packet_session_creation(self):
        """10,000 packets — typical 2.5-hour window."""
        start = time.perf_counter()
        session = make_session(10_000)
        elapsed = time.perf_counter() - start

        assert session.packet_count == 10_000
        assert elapsed < 5.0, f"10K packets took {elapsed:.3f}s — too slow"
        rate = 10_000 / elapsed
        print(f"\n  10,000 packets: {elapsed * 1000:.1f}ms "
              f"({rate:,.0f} packets/sec)")

    def test_50000_packet_session_creation(self):
        """50,000 packets — full day of telemetry."""
        start = time.perf_counter()
        session = make_session(50_000)
        elapsed = time.perf_counter() - start

        assert session.packet_count == 50_000
        assert elapsed < 20.0, f"50K packets took {elapsed:.3f}s"
        rate = 50_000 / elapsed
        print(f"\n  50,000 packets: {elapsed:.2f}s "
              f"({rate:,.0f} packets/sec)")


class TestReplayPerformance:

    def test_replay_load_10000_packets(self):
        """Load 10K packets into replay engine."""
        session = make_session(10_000)
        engine = ReplayEngine()

        start = time.perf_counter()
        cursor = engine.load_session(session)
        elapsed = time.perf_counter() - start

        assert cursor.total_packets == 10_000
        assert elapsed < 2.0, f"Load took {elapsed:.3f}s"
        print(f"\n  Replay load 10K: {elapsed * 1000:.1f}ms")

    def test_seek_performance_10000_packets(self):
        """Seek operations on 10K packet session — binary search."""
        session = make_session(10_000)
        engine = ReplayEngine()
        engine.load_session(session)

        targets = [
            datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
            + timedelta(seconds=i * 2000)
            for i in range(5)
        ]

        start = time.perf_counter()
        for target in targets:
            engine.seek(session.session_id, target)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / len(targets)) * 1000
        assert avg_ms < 10.0, f"Seek avg {avg_ms:.2f}ms — too slow"
        print(f"\n  Seek avg (10K packets): {avg_ms:.2f}ms per seek")

    def test_stream_10000_packets(self):
        """Stream all 10K packets through the engine."""
        session = make_session(10_000)
        engine = ReplayEngine()
        engine.load_session(session)

        start = time.perf_counter()
        count = sum(
            1 for _ in engine.stream_packets(session.session_id)
        )
        elapsed = time.perf_counter() - start

        assert count == 10_000
        rate = count / elapsed
        print(f"\n  Stream 10K: {elapsed:.3f}s ({rate:,.0f} pkts/sec)")

    def test_determinism_verify_10000_packets(self):
        """Verify determinism on 10K packet session."""
        session = make_session(10_000)
        engine = ReplayEngine()
        engine.load_session(session)

        start = time.perf_counter()
        result = engine.verify_determinism(session.session_id)
        elapsed = time.perf_counter() - start

        assert result is True
        print(f"\n  Determinism verify 10K: {elapsed * 1000:.1f}ms")


class TestAuditPerformance:

    def test_1000_audit_entries(self):
        """Write 1,000 audit entries and verify chain."""
        log = AuditLog()

        start = time.perf_counter()
        for i in range(1_000):
            log.record(
                AuditEventType.REPLAY_SEEKED,
                actor=f"controller_{i % 5}",
                details={"step": i, "index": i * 10},
            )
        elapsed_write = time.perf_counter() - start

        start = time.perf_counter()
        result = log.verify_chain()
        elapsed_verify = time.perf_counter() - start

        assert result is True
        assert log.entry_count == 1_000
        write_rate = 1_000 / elapsed_write
        print(f"\n  1K audit entries write: {elapsed_write * 1000:.1f}ms "
              f"({write_rate:,.0f}/sec)")
        print(f"  1K chain verify: {elapsed_verify * 1000:.1f}ms")

    def test_audit_export_1000_entries(self):
        """Export 1,000 entry audit log as JSON."""
        log = AuditLog()
        for i in range(1_000):
            log.record(AuditEventType.ANOMALY_DETECTED, "ai", {"i": i})

        start = time.perf_counter()
        exported = log.export_json()
        elapsed = time.perf_counter() - start

        assert len(exported) > 1000
        print(f"\n  1K entry export: {elapsed * 1000:.1f}ms "
              f"({len(exported) / 1024:.1f}KB)")


class TestAIDetectionPerformance:

    def test_analyze_1000_packets(self):
        """Run AI detection on 1,000 packets."""
        detector = AnomalyDetector()
        session = make_session(1_000)

        start = time.perf_counter()
        reports = detector.analyze_session(session)
        elapsed = time.perf_counter() - start

        rate = 1_000 / elapsed
        print(f"\n  AI analyze 1K packets: {elapsed * 1000:.1f}ms "
              f"({rate:,.0f} pkts/sec)")
        print(f"  Anomalies found: {len(reports)}")

    def test_analyze_10000_packets(self):
        """Run AI detection on 10,000 packets."""
        detector = AnomalyDetector()
        session = make_session(10_000)

        start = time.perf_counter()
        reports = detector.analyze_session(session)
        elapsed = time.perf_counter() - start

        assert elapsed < 30.0, f"10K AI analysis took {elapsed:.1f}s"
        rate = 10_000 / elapsed
        print(f"\n  AI analyze 10K packets: {elapsed:.2f}s "
              f"({rate:,.0f} pkts/sec)")
        print(f"  Anomalies found: {len(reports)}")


class TestEndToEndPerformance:

    def test_complete_pipeline_1000_packets(self):
        """
        Full pipeline benchmark — what you quote to buyers.
        1,000 packets through every system.
        """
        from src.chronoscope.controller import ChronoScopeController
        from src.chronoscope.ingestion.base import IngestionResult
        from unittest.mock import patch, MagicMock

        # Build session directly (no network call)
        session = make_session(1_000)

        engine = ReplayEngine()
        detector = AnomalyDetector()
        audit = AuditLog()

        total_start = time.perf_counter()

        # Replay load
        t = time.perf_counter()
        cursor = engine.load_session(session)
        replay_load_ms = (time.perf_counter() - t) * 1000

        # Seek to midpoint
        t = time.perf_counter()
        for i in range(5):
           mid = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=i * 2000)
           engine.seek(session.session_id, mid)
        seek_ms = (time.perf_counter() - t) * 1000

        # AI analysis
        t = time.perf_counter()
        reports = detector.analyze_session(session)
        ai_ms = (time.perf_counter() - t) * 1000

        # Audit chain verify
        for i in range(50):
            audit.record(AuditEventType.REPLAY_SEEKED, "controller",
                         {"step": i})
        t = time.perf_counter()
        audit.verify_chain()
        audit_ms = (time.perf_counter() - t) * 1000

        # Determinism verify
        t = time.perf_counter()
        engine.verify_determinism(session.session_id)
        det_ms = (time.perf_counter() - t) * 1000

        total_ms = (time.perf_counter() - total_start) * 1000

        print(f"\n  ┌─ ChronoScope Pipeline Benchmark (1,000 packets) ─┐")
        print(f"  │ Replay engine load:    {replay_load_ms:8.1f}ms           │")
        print(f"  │ Seek to timestamp:     {seek_ms:8.2f}ms           │")
        print(f"  │ AI anomaly scan:       {ai_ms:8.1f}ms           │")
        print(f"  │ Audit chain verify:    {audit_ms:8.2f}ms           │")
        print(f"  │ Determinism verify:    {det_ms:8.2f}ms           │")
        print(f"  │ TOTAL pipeline:        {total_ms:8.1f}ms           │")
        print(f"  │ Anomalies found:       {len(reports):8d}             │")
        print(f"  └──────────────────────────────────────────────────┘")

        assert total_ms < 10_000, f"Pipeline too slow: {total_ms:.1f}ms"