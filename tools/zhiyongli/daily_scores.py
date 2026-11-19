"""日总分仅归一已观察维度的原权重；不补缺维度，不改变周月合同。"""
from fractions import Fraction
from runtime import RUBRIC

AGGREGATION_VERSION = 'daily-observed-weighted-20261119.2'


def observed_total(dimensions):
    observed = [d for d in dimensions if d['exact'] is not None]
    if not observed:
        return None
    weights = {d['code']: Fraction(RUBRIC['dimension_weights'][d['code']]) for d in observed}
    return sum((Fraction(d['exact']) * weights[d['code']] for d in observed), Fraction()) / sum(weights.values(), Fraction())
