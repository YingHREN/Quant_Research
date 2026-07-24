from dataclasses import dataclass

from marketdata.base import SubscriptionRequest


@dataclass(frozen=True)
class SubscriptionChange:
    subscribe: tuple[str, ...]
    unsubscribe: tuple[str, ...]


def build_pool(selected, peers, candidates, fixed=("SPY", "QQQ", "SOXX"), limit=30):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ValueError("limit must be a positive integer at most 30")

    ordered = []

    def append_unique(values, group_limit):
        added = 0
        for value in values:
            normalized = SubscriptionRequest((value,), max_symbols=1).symbols[0]
            if normalized in ordered:
                continue
            ordered.append(normalized)
            added += 1
            if added == group_limit or len(ordered) == limit:
                break

    append_unique(fixed, len(fixed))
    if selected and len(ordered) < limit:
        append_unique((selected,), 1)
    if len(ordered) < limit:
        append_unique(peers, 16)
    if len(ordered) < limit:
        append_unique(candidates, 10)
    return tuple(ordered[:limit])


def plan_change(current, desired):
    current_set = set(current)
    desired_set = set(desired)
    return SubscriptionChange(
        subscribe=tuple(symbol for symbol in desired if symbol not in current_set),
        unsubscribe=tuple(symbol for symbol in current if symbol not in desired_set),
    )
