"""
ChronoScope AI — Custom Exceptions
All domain-specific exceptions.
Every error in ChronoScope has a specific type.
No bare Exception raises anywhere in production code.
"""


class ChronoScopeError(Exception):
    """Base exception for all ChronoScope errors."""
    pass


# ---------------------------------------------------------------------------
# Telemetry Exceptions
# ---------------------------------------------------------------------------

class TelemetryError(ChronoScopeError):
    """Base class for telemetry-related errors."""
    pass


class PacketParseError(TelemetryError):
    """Raised when a CCSDS packet cannot be parsed."""
    def __init__(self, reason: str, raw_bytes: bytes | None = None):
        self.reason = reason
        self.raw_bytes = raw_bytes
        super().__init__(f"Packet parse failed: {reason}")


class PacketValidationError(TelemetryError):
    """Raised when a packet fails validation checks."""
    def __init__(self, field: str, value: object, reason: str):
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"Validation failed for {field}={value}: {reason}")


class SequenceGapError(TelemetryError):
    """Raised when a gap is detected in packet sequence counts."""
    def __init__(self, expected: int, received: int, spacecraft_id: str):
        self.expected = expected
        self.received = received
        self.spacecraft_id = spacecraft_id
        super().__init__(
            f"Sequence gap on {spacecraft_id}: "
            f"expected {expected}, received {received}"
        )


# ---------------------------------------------------------------------------
# Replay Exceptions
# ---------------------------------------------------------------------------

class ReplayError(ChronoScopeError):
    """Base class for replay engine errors."""
    pass


class SessionNotFoundError(ReplayError):
    """Raised when a requested session does not exist."""
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class ReplayStateError(ReplayError):
    """Raised when a replay operation is invalid for the current state."""
    def __init__(self, operation: str, current_state: str):
        self.operation = operation
        self.current_state = current_state
        super().__init__(
            f"Cannot perform '{operation}' while in state '{current_state}'"
        )


class DeterminismViolationError(ReplayError):
    """
    Raised when replay produces different output for identical input.
    This should never happen. If it does, it is a critical system error.
    """
    def __init__(self, session_id: str, details: str):
        self.session_id = session_id
        super().__init__(
            f"DETERMINISM VIOLATION in session {session_id}: {details}"
        )


# ---------------------------------------------------------------------------
# Audit Exceptions
# ---------------------------------------------------------------------------

class AuditError(ChronoScopeError):
    """Base class for audit log errors."""
    pass


class AuditChainBrokenError(AuditError):
    """Raised when the audit log hash chain is broken — tampering detected."""
    def __init__(self, entry_id: str, expected_hash: str, actual_hash: str):
        self.entry_id = entry_id
        super().__init__(
            f"AUDIT CHAIN BROKEN at entry {entry_id}. "
            f"Expected {expected_hash[:16]}... "
            f"Got {actual_hash[:16]}..."
        )


# ---------------------------------------------------------------------------
# Anomaly Detection Exceptions
# ---------------------------------------------------------------------------

class AnomalyDetectionError(ChronoScopeError):
    """Raised when anomaly detection fails to produce a result."""
    pass


class ExplainabilityError(AnomalyDetectionError):
    """Raised when an anomaly flag cannot provide a human-readable reason."""
    pass


# ---------------------------------------------------------------------------
# Ingestion Exceptions
# ---------------------------------------------------------------------------

class IngestionError(ChronoScopeError):
    """Base class for data ingestion errors."""
    pass


class DataSourceUnavailableError(IngestionError):
    """Raised when a public data source cannot be reached."""
    def __init__(self, source_url: str, reason: str):
        self.source_url = source_url
        super().__init__(f"Data source unavailable: {source_url} — {reason}")


class UnsupportedFormatError(IngestionError):
    """Raised when an unsupported telemetry format is encountered."""
    def __init__(self, format_name: str):
        self.format_name = format_name
        super().__init__(f"Unsupported telemetry format: {format_name}")