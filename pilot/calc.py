def add(a, b):
    return a + b


def clamp(x, lo, hi):
    if lo > hi:
        raise ValueError("lo > hi")
    return max(lo, min(hi, x))
