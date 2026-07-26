"""Versioned SEC SIC to broad market-sector classification."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re


RULE_VERSION = "sec_sic_v1"


@dataclass(frozen=True)
class SectorClassification:
    sector_key: str
    theme_keys: tuple[str, ...]
    sic: str | None
    industry_description: str
    source: str
    rule_version: str
    confidence: float

    def to_dict(self):
        payload = asdict(self)
        payload["theme_keys"] = list(self.theme_keys)
        return payload


_EXACT_RULES = {
    "3711": ("consumer_discretionary", ()),
    "3674": ("technology", ("semiconductor",)),
    "6798": ("real_estate", ()),
    "4953": ("industrials", ()),
    **{
        str(code): ("health_care", ())
        for code in (2833, 2834, 2835, 2836, 3841, 3842, 3843, 3844, 3845)
    },
    **{
        str(code): ("technology", ("software",))
        for code in range(7370, 7380)
    },
    **{
        str(code): ("consumer_staples", ())
        for code in (5411, 5421, 5431, 5441, 5451, 5461, 5499, 5912)
    },
}


_RANGE_RULES = (
    (100, 999, "consumer_staples"),
    (1000, 1099, "materials"),
    (1200, 1399, "energy"),
    (1400, 1499, "materials"),
    (1500, 1799, "industrials"),
    (2000, 2199, "consumer_staples"),
    (2200, 2399, "consumer_discretionary"),
    (2400, 2499, "materials"),
    (2500, 2599, "consumer_discretionary"),
    (2600, 2699, "materials"),
    (2700, 2799, "communication"),
    (2800, 2829, "materials"),
    (2830, 2839, "health_care"),
    (2840, 2899, "materials"),
    (2900, 2999, "energy"),
    (3000, 3399, "materials"),
    (3400, 3569, "industrials"),
    (3570, 3579, "technology"),
    (3580, 3599, "industrials"),
    (3600, 3699, "technology"),
    (3700, 3799, "industrials"),
    (3800, 3839, "technology"),
    (3840, 3859, "health_care"),
    (3860, 3899, "technology"),
    (3900, 4799, "industrials"),
    (4800, 4899, "communication"),
    (4900, 4999, "utilities"),
    (5000, 5199, "industrials"),
    (5200, 5399, "consumer_discretionary"),
    (5400, 5499, "consumer_staples"),
    (5500, 5999, "consumer_discretionary"),
    (6000, 6499, "financials"),
    (6500, 6599, "real_estate"),
    (6700, 6799, "financials"),
    (7000, 7299, "consumer_discretionary"),
    (7300, 7369, "industrials"),
    (7370, 7379, "technology"),
    (7380, 7799, "industrials"),
    (7800, 7899, "communication"),
    (7900, 7999, "consumer_discretionary"),
    (8000, 8099, "health_care"),
    (8700, 8799, "industrials"),
)


def _normalize_sic(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,4}", text):
        return None
    number = int(text)
    if number <= 0:
        return None
    return f"{number:04d}"


def classify_sic(sic, description=""):
    normalized = _normalize_sic(sic)
    industry_description = str(description or "")
    if normalized is not None and normalized in _EXACT_RULES:
        sector_key, themes = _EXACT_RULES[normalized]
        confidence = 1.0
    elif normalized is not None:
        number = int(normalized)
        matched = next(
            (
                (sector_key, ())
                for start, finish, sector_key in _RANGE_RULES
                if start <= number <= finish
            ),
            None,
        )
        if matched is None:
            sector_key, themes, confidence = "unclassified", (), 0.0
        else:
            sector_key, themes = matched
            confidence = 0.8
    else:
        sector_key, themes, confidence = "unclassified", (), 0.0
    return SectorClassification(
        sector_key=sector_key,
        theme_keys=themes,
        sic=normalized,
        industry_description=industry_description,
        source="sec",
        rule_version=RULE_VERSION,
        confidence=confidence,
    )
