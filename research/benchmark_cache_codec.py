"""Safe, deterministic serialization for benchmark DataFrame bundles.

The codec intentionally supports a narrow set of pandas values and never
deserializes executable Python objects.  The compressed payload is suitable
for content-addressed research caches, not for general DataFrame persistence.
"""

from __future__ import annotations

import hashlib
import json
import math
import zlib
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
from pandas.api import types as pandas_types


CACHE_CODEC = "typed-json-v1+zlib"
_SCHEMA_VERSION = "benchmark-frame-bundle-v1"
_CHECKSUM_LENGTH = 64
_DECOMPRESSION_CHUNK_BYTES = 64 * 1024


def _missing(value: Any) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    return isinstance(value, (float, np.floating)) and math.isnan(float(value))


def _encode_object_value(value: Any) -> dict[str, Any]:
    if _missing(value):
        return {"type": "missing"}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, (bool, np.bool_)):
        return {"type": "boolean", "value": bool(value)}
    if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)):
        return {"type": "integer", "value": int(value)}
    if isinstance(value, (float, np.floating)):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("numeric values must be finite")
        return {"type": "number", "value": numeric}
    if isinstance(value, tuple):
        return {
            "type": "tuple",
            "value": [_encode_object_value(item) for item in value],
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "value": [_encode_object_value(item) for item in value],
        }
    raise TypeError(
        "unsupported object value; only scalar strings, numbers, booleans, "
        "missing values, tuples, and lists are allowed"
    )


def _column_kind(series: pd.Series) -> str:
    dtype = series.dtype
    if isinstance(dtype, pd.DatetimeTZDtype):
        raise ValueError("timezone-aware datetimes are not supported")
    if pandas_types.is_datetime64_dtype(dtype):
        return "datetime"
    if pandas_types.is_bool_dtype(dtype):
        return "boolean"
    if pandas_types.is_integer_dtype(dtype):
        return "integer"
    if pandas_types.is_float_dtype(dtype):
        return "number"
    if isinstance(dtype, pd.StringDtype):
        return "string"
    if pandas_types.is_object_dtype(dtype):
        return "object"
    raise TypeError("unsupported pandas dtype: {}".format(dtype))


def _encode_typed_value(value: Any, kind: str) -> Any:
    if _missing(value):
        return None
    if kind == "datetime":
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            raise ValueError("timezone-aware datetimes are not supported")
        return timestamp.isoformat()
    if kind == "boolean":
        if not isinstance(value, (bool, np.bool_)):
            raise TypeError("boolean column contains a non-boolean value")
        return bool(value)
    if kind == "integer":
        if not isinstance(value, (int, np.integer)) or isinstance(
            value, (bool, np.bool_)
        ):
            raise TypeError("integer column contains a non-integer value")
        return int(value)
    if kind == "number":
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("numeric values must be finite")
        return numeric
    if kind == "string":
        if not isinstance(value, str):
            raise TypeError("string column contains a non-string value")
        return value
    if kind == "object":
        return _encode_object_value(value)
    raise ValueError("unsupported column kind")


def encode_frame_bundle(
    frames: Mapping[str, pd.DataFrame],
) -> tuple[bytes, str, int]:
    """Encode frames as canonical typed JSON compressed with zlib."""

    if not isinstance(frames, Mapping):
        raise TypeError("frames must be a mapping")
    encoded_frames = []
    seen_names = set()
    total_rows = 0
    for name, frame in frames.items():
        if not isinstance(name, str) or not name:
            raise TypeError("frame name must be a non-empty string")
        if name in seen_names:
            raise ValueError("duplicate frame name")
        seen_names.add(name)
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("frame values must be pandas DataFrames")
        if not frame.columns.is_unique:
            raise ValueError("duplicate DataFrame columns are not supported")
        if not all(isinstance(column, str) for column in frame.columns):
            raise TypeError("DataFrame column names must be strings")

        columns = []
        kinds = []
        for column in frame.columns:
            series = frame[column]
            kind = _column_kind(series)
            columns.append(
                {"name": column, "dtype": str(series.dtype), "kind": kind}
            )
            kinds.append(kind)
        records = [
            [
                _encode_typed_value(value, kind)
                for value, kind in zip(row, kinds)
            ]
            for row in frame.itertuples(index=False, name=None)
        ]
        total_rows += len(frame)
        encoded_frames.append(
            {
                "name": name,
                "columns": columns,
                "records": records,
            }
        )

    document = {
        "schema_version": _SCHEMA_VERSION,
        "frames": sorted(encoded_frames, key=lambda item: item["name"]),
    }
    raw = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload = zlib.compress(raw)
    return payload, hashlib.sha256(payload).hexdigest(), total_rows


def _bounded_decompress(payload: bytes, maximum: int) -> bytes:
    decompressor = zlib.decompressobj()
    output = bytearray()
    try:
        for offset in range(0, len(payload), _DECOMPRESSION_CHUNK_BYTES):
            pending = payload[offset : offset + _DECOMPRESSION_CHUNK_BYTES]
            while pending:
                remaining = maximum - len(output)
                part = decompressor.decompress(pending, remaining + 1)
                output.extend(part)
                if len(output) > maximum:
                    raise ValueError("payload exceeds maximum uncompressed bytes")
                pending = decompressor.unconsumed_tail
                if pending and not part and remaining == 0:
                    raise ValueError("payload exceeds maximum uncompressed bytes")
        remaining = maximum - len(output)
        output.extend(decompressor.flush(remaining + 1))
    except zlib.error as error:
        raise ValueError("invalid compressed payload") from error
    if len(output) > maximum:
        raise ValueError("payload exceeds maximum uncompressed bytes")
    if not decompressor.eof or decompressor.unused_data:
        raise ValueError("invalid compressed payload")
    return bytes(output)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _decode_object_value(value: Any) -> Any:
    if not isinstance(value, dict) or "type" not in value:
        raise ValueError("invalid object value")
    value_type = value["type"]
    expected_keys = {"type"} if value_type == "missing" else {"type", "value"}
    if set(value) != expected_keys:
        raise ValueError("invalid object value schema")
    if value_type == "missing":
        return None
    decoded = value["value"]
    if value_type == "string" and isinstance(decoded, str):
        return decoded
    if value_type == "boolean" and isinstance(decoded, bool):
        return decoded
    if (
        value_type == "integer"
        and isinstance(decoded, int)
        and not isinstance(decoded, bool)
    ):
        return decoded
    if (
        value_type == "number"
        and isinstance(decoded, (int, float))
        and not isinstance(decoded, bool)
        and math.isfinite(float(decoded))
    ):
        return float(decoded)
    if value_type in {"tuple", "list"} and isinstance(decoded, list):
        items = [_decode_object_value(item) for item in decoded]
        return tuple(items) if value_type == "tuple" else items
    raise ValueError("invalid or unsupported object value")


def _validate_column(column: Any) -> tuple[str, str, str]:
    if not isinstance(column, dict) or set(column) != {"name", "dtype", "kind"}:
        raise ValueError("invalid column schema")
    name = column["name"]
    dtype = column["dtype"]
    kind = column["kind"]
    if not isinstance(name, str) or not name:
        raise ValueError("invalid column name")
    if not isinstance(dtype, str) or not dtype:
        raise ValueError("invalid column dtype")
    if kind not in {"datetime", "boolean", "integer", "number", "string", "object"}:
        raise ValueError("unsupported column kind")
    try:
        parsed_dtype = pd.api.types.pandas_dtype(dtype)
    except (TypeError, ValueError) as error:
        raise ValueError("unsupported column dtype") from error
    expected_kind = _column_kind(pd.Series([], dtype=parsed_dtype))
    if expected_kind != kind:
        raise ValueError("column dtype and kind do not match")
    return name, dtype, kind


def _decode_typed_value(value: Any, kind: str) -> Any:
    if value is None:
        return None
    if kind == "datetime":
        if not isinstance(value, str):
            raise ValueError("invalid datetime value")
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            raise ValueError("timezone-aware datetimes are not supported")
        return timestamp
    if kind == "boolean" and isinstance(value, bool):
        return value
    if (
        kind == "integer"
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        return value
    if (
        kind == "number"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ):
        return float(value)
    if kind == "string" and isinstance(value, str):
        return value
    if kind == "object":
        return _decode_object_value(value)
    raise ValueError("invalid typed value")


def _decode_object_column(values) -> list[Any]:
    decoded = []
    for value in values:
        if value is None:
            decoded.append(None)
        elif (
            isinstance(value, dict)
            and set(value) == {"type", "value"}
            and value.get("type") == "string"
            and isinstance(value.get("value"), str)
        ):
            decoded.append(value["value"])
        elif isinstance(value, dict) and value == {"type": "missing"}:
            decoded.append(None)
        else:
            decoded.append(_decode_object_value(value))
    return decoded


def _decode_primitive_column(values: pd.Series, kind: str) -> pd.Series:
    nonmissing = values.loc[values.notna()]
    inferred = pd.api.types.infer_dtype(nonmissing, skipna=False)
    expected = {
        "datetime": {"string", "empty"},
        "boolean": {"boolean", "empty"},
        "integer": {"integer", "empty"},
        "number": {"floating", "integer", "mixed-integer-float", "empty"},
        "string": {"string", "empty"},
    }[kind]
    if inferred not in expected:
        raise ValueError("invalid {} column values".format(kind))
    if kind == "datetime":
        converted = pd.to_datetime(values, errors="raise")
        if getattr(converted.dt, "tz", None) is not None:
            raise ValueError("timezone-aware datetimes are not supported")
        return converted
    if kind == "number":
        converted = pd.to_numeric(values, errors="raise")
        numeric = converted.loc[converted.notna()].to_numpy(dtype=float)
        if not np.isfinite(numeric).all():
            raise ValueError("numeric values must be finite")
        return converted
    return values


def decode_frame_bundle(
    payload: bytes,
    expected_checksum: str,
    *,
    maximum_uncompressed_bytes: int = 1_000_000_000,
) -> dict[str, pd.DataFrame]:
    """Verify and decode a frame bundle without loading executable objects."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if (
        not isinstance(expected_checksum, str)
        or len(expected_checksum) != _CHECKSUM_LENGTH
        or any(character not in "0123456789abcdef" for character in expected_checksum)
    ):
        raise ValueError("expected checksum must be a lowercase SHA-256 digest")
    if (
        not isinstance(maximum_uncompressed_bytes, int)
        or isinstance(maximum_uncompressed_bytes, bool)
        or maximum_uncompressed_bytes <= 0
    ):
        raise ValueError("maximum uncompressed bytes must be positive")
    if hashlib.sha256(payload).hexdigest() != expected_checksum:
        raise ValueError("payload checksum mismatch")

    raw = _bounded_decompress(payload, maximum_uncompressed_bytes)
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("invalid JSON numeric constant")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid typed JSON payload") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "frames"}
        or document["schema_version"] != _SCHEMA_VERSION
        or not isinstance(document["frames"], list)
    ):
        raise ValueError("unsupported frame bundle schema")

    restored = {}
    previous_name = None
    for encoded_frame in document["frames"]:
        if (
            not isinstance(encoded_frame, dict)
            or set(encoded_frame) != {"name", "columns", "records"}
        ):
            raise ValueError("invalid frame schema")
        name = encoded_frame["name"]
        if not isinstance(name, str) or not name:
            raise ValueError("invalid frame name")
        if name in restored:
            raise ValueError("duplicate frame name")
        if previous_name is not None and name <= previous_name:
            raise ValueError("frame names are not in canonical order")
        previous_name = name
        if not isinstance(encoded_frame["columns"], list) or not isinstance(
            encoded_frame["records"], list
        ):
            raise ValueError("invalid frame columns or records")
        columns = [_validate_column(item) for item in encoded_frame["columns"]]
        names = [item[0] for item in columns]
        if len(names) != len(set(names)):
            raise ValueError("duplicate DataFrame columns")

        records = encoded_frame["records"]
        for record in records:
            if not isinstance(record, list) or len(record) != len(columns):
                raise ValueError("invalid frame record")
        raw_frame = pd.DataFrame.from_records(records, columns=names)
        decoded_columns = {}
        for column_name, dtype, kind in columns:
            values = raw_frame[column_name]
            if kind == "object":
                decoded = _decode_object_column(values)
            else:
                decoded = _decode_primitive_column(values, kind)
            decoded_columns[column_name] = pd.Series(decoded, dtype=dtype)
        frame = pd.DataFrame(
            decoded_columns,
            columns=names,
        )
        restored[name] = frame
    return restored
