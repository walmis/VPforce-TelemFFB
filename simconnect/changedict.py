from collections import OrderedDict
from time import time
import math
from typing import Callable, Any
from itertools import takewhile


def now():
    return round(time() * 1000)


def equal_or_close(rel_tol=1e-6, abs_tol=1e-6) -> Callable[[Any, Any], bool]:
    """Return a function that decides if a ~= b"""
    def comparator(a, b) -> bool:
        try:
            v = math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)
        except Exception:
            v = a == b
        return v
    return comparator


class ChangeDict(OrderedDict):
    """An ordered dict that records timestamp of changed items"""
    def __init__(self, default_comparator=equal_or_close(), comparators={}):
        self.__default_comparator = default_comparator
        self.__comparators = comparators
        self.__times = {}

    def __setitem__(self, k, v):
        if k in self:
            vv = self[k]
            eq = self.__comparators.get(k, self.__default_comparator)
            if eq(v, vv):
                return
        super().__setitem__(k, v)
        self.move_to_end(k)
        self.__times[k] = now()

    def __delitem__(self, k, v):
        super().__delitem__(k, v)
        del self.__times[k]

    def latest(self):
        if not self.__times:
            return 0
        k = next(reversed(self))
        return self.__times[k]

    def changedsince(self, millis):
        return dict(
            takewhile(
                lambda kv: self.__times[kv[0]] > millis,
                reversed(self.items())
            )
        )


if __name__ == '__main__':
    from time import sleep

    t = now()
    d = ChangeDict(comparators=dict(froz=lambda a, b: abs(a-b) < 8))
    d.update(a=1, b=2, z='zzz', froz=1024)
    print(d.changedsince(0), d.latest())
    sleep(1)
    d.update(a=1, b=3, c=3, froz=1031)
    print(d.changedsince(t), d.latest())
    sleep(1)
    d.update(a=4, froz=1033)
    print(d.changedsince(t+1500), d.latest())
    print(d)
