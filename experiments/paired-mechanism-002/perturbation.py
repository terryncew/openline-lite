from __future__ import annotations

import math


def final_quarter_truncate(text: str) -> str:
    n = len(text)
    remove = math.ceil(n / 4)
    return text[: n - remove]


class OneShotEligibleReadDelivery:
    """Enforces exactly one delivery decision for the frozen eligible read event."""

    def __init__(self):
        self._delivered = False

    def deliver(self, text: str, *, alter: bool) -> str:
        if self._delivered:
            raise RuntimeError("eligible read result may be delivered exactly once per branch")
        self._delivered = True
        return final_quarter_truncate(text) if alter else text
