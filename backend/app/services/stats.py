"""The small-n statistics the product promises, in one place.

`docs/INSIGHTS.md` states the discipline: "Medians over means everywhere".
Keeping these here is what makes that checkable — portfolio and insights both
import them, so there is no second implementation to drift. There was one:
`flow_metrics` computed `sorted(days)[n // 2]`, which takes the UPPER of the
two middle values, and reported 9.0 where the median of [1, 9] is 5.0.
"""

import math


def median(values: list[float]) -> float | None:
    """None for an empty sample — a median of nothing is not zero."""
    if not values:
        return None
    v = sorted(values)
    n = len(v)
    mid = n // 2
    return round(v[mid] if n % 2 else (v[mid - 1] + v[mid]) / 2, 1)


def p85(values: list[float]) -> float | None:
    """Nearest-rank P85, not interpolated — with samples this small an
    interpolated percentile invents a value nobody measured.

    ceil(0.85n) - 1, not int(0.85n): the two agree except when 0.85n is
    exactly an integer (n a multiple of 20), where int() lands one rank too
    high. At n=20 over 1..20 that read 18.0 where nearest-rank is 17.0.
    """
    if not values:
        return None
    v = sorted(values)
    rank = max(1, math.ceil(0.85 * len(v)))
    return round(v[rank - 1], 1)
