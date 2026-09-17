"""Utility modules for TelemFFB.

Flat category modules, star-imported below in dependency order; the
package namespace is the union of each module's ``__all__`` plus the
``conversions`` submodule.  ``_math`` and ``_logging`` carry leading
underscores because the original monolith star-exported the stdlib
``math``/``logging`` modules into this package, and ~84 consumer files
still import those names from here.

Category modules: ``_math``, ``filesystem``, ``device``, ``settings``,
``_logging``, ``network``, ``integration``, ``misc``.  One-class files
(``Vector``, ``SharedMemReader``, ``TransformExpr``,
``TurbulenceModulator``, ``AxisJitter``) and ``conversions`` keep their
own module paths.
"""

from . import conversions
from ._math import *
from .filesystem import *
from .device import *
from .settings import *
from ._logging import *
from .network import *
from .integration import *
from .misc import *
