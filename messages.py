"""MQTT JSON message format shared by the laptop and the ESP32 (schema version 1)."""

import json
import math
import time

MAX_MESSAGE_BYTES = 8192
REQUIRED = {
    "schema_version", "msg_type", "project_id", "machine_id",
    "device_id", "run_id", "seq", "ts_wall_ms", "sim_time_s", "payload",
}
MONITORING_TYPES = {"temperature_sample", "temperature_feature", "vibration_feature", "ai_result"}
TRUTH_FIELDS = {"scenario_id", "fault_type", "fault_severity", "fault_label", "injection_params"}


def make_message(msg_type: str, device_id: str, run_id: str, seq: int,
                 sim_time_s: float, payload: dict,
                 project_id: str = "group07", machine_id: str = "motor01") -> dict:
    """Create a message. The Plant will later supply the shared run_id/time."""
    message = {
        "schema_version": 1,
        "msg_type": msg_type,
        "project_id": project_id,
        "machine_id": machine_id,
        "device_id": device_id,
        "run_id": run_id,
        "seq": seq,
        "ts_wall_ms": int(time.time() * 1000),
        "sim_time_s": sim_time_s,
        "payload": payload,
    }
    validate_message(message)
    return message


def _check_finite(value):
    if isinstance(value, dict):
        for item in value.values():
            _check_finite(item)
    elif isinstance(value, list):
        for item in value:
            _check_finite(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN/Infinity not allowed")


def _reject_ground_truth_nested(value):
    """Never allow simulator labels in any monitoring data, including nested objects."""
    if isinstance(value, dict):
        for field, child in value.items():
            if field in TRUTH_FIELDS:
                raise ValueError(f"Ground-truth field not allowed: {field}")
            _reject_ground_truth_nested(child)
    elif isinstance(value, list):
        for child in value:
            _reject_ground_truth_nested(child)


def _require_number(value, field: str) -> None:
    """Reject strings, bools, NaN/inf; only finite JSON numbers are accepted."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid {field}: expected finite numeric value")


def _validate_temperature_sample(payload: dict, message_sim_time: float) -> None:
    """Contract for raw virtual-temperature measurements from the Plant."""
    expected = {"temp_c", "sample_sim_time_s"}
    if set(payload) != expected:
        raise ValueError("temperature_sample payload requires exactly: temp_c, sample_sim_time_s")
    _require_number(payload["temp_c"], "temp_c")
    _require_number(payload["sample_sim_time_s"], "sample_sim_time_s")
    if payload["sample_sim_time_s"] < 0:
        raise ValueError("sample_sim_time_s must be non-negative")
    if not math.isclose(payload["sample_sim_time_s"], message_sim_time, abs_tol=1e-6, rel_tol=0):
        raise ValueError("sample_sim_time_s must match envelope sim_time_s")


def validate_message(message: dict) -> None:
    """Validate shared envelope and the temperature_sample payload contract."""
    if not isinstance(message, dict):
        raise ValueError("Message must be a JSON object")
    if REQUIRED - message.keys():
        raise ValueError(f"Missing fields: {sorted(REQUIRED - message.keys())}")
    if type(message["schema_version"]) is not int or message["schema_version"] != 1:
        raise ValueError("Unsupported schema version")
    for field in ("msg_type", "project_id", "machine_id", "device_id", "run_id"):
        if not isinstance(message[field], str) or not message[field]:
            raise ValueError(f"Invalid {field}")
    for field in ("seq", "ts_wall_ms"):
        if type(message[field]) is not int or message[field] < 0:
            raise ValueError(f"Invalid {field}")
    sim_time = message["sim_time_s"]
    if isinstance(sim_time, bool) or not isinstance(sim_time, (int, float)) or not math.isfinite(sim_time) or sim_time < 0:
        raise ValueError("Invalid sim_time_s")
    if not isinstance(message["payload"], dict):
        raise ValueError("Payload must be a JSON object")
    if message["msg_type"] in MONITORING_TYPES:
        _reject_ground_truth_nested(message)
    if message["msg_type"] == "temperature_sample":
        _validate_temperature_sample(message["payload"], sim_time)
    _check_finite(message)


def encode_message(message: dict) -> bytes:
    """Check and serialize before publishing to MQTT."""
    validate_message(message)
    data = json.dumps(message, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("MQTT message too large")
    return data


def decode_message(data: bytes) -> dict:
    """Check received bytes before other modules use them."""
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("MQTT message too large")
    try:
        message = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise ValueError("Invalid JSON") from exc
    validate_message(message)
    return message
