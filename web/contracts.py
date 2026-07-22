import math
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ErrorPayload:
    code: str
    message: str

    def to_dict(self):
        return {"error": {"code": self.code, "message": self.message}}


def iso_date(value):
    return None if value is None else pd.Timestamp(value).date().isoformat()


def json_safe(value):
    if value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return iso_date(value)
    if isinstance(value, np.datetime64):
        return iso_date(value)
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
