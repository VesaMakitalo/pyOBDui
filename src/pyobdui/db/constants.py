"""SQL statements and other database constants."""

TELEMETRY_INSERT = """
INSERT INTO telemetry_samples (pid, recorded_at, value_json)
VALUES (?, ?, ?)
"""

TELEMETRY_LATEST = """
SELECT ts.pid, ts.recorded_at, ts.value_json
FROM telemetry_samples AS ts
JOIN (
    SELECT pid, MAX(recorded_at) AS recorded_at
    FROM telemetry_samples
    GROUP BY pid
) AS latest
    ON latest.pid = ts.pid AND latest.recorded_at = ts.recorded_at
ORDER BY ts.pid
"""

DTC_INSERT = """
INSERT INTO dtc_events (code, description, detected_at, cleared)
VALUES (?, ?, ?, ?)
"""

DTC_HISTORY = """
SELECT code, description, detected_at, cleared
FROM dtc_events
ORDER BY detected_at DESC
LIMIT ?
"""
