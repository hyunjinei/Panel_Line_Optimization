# [AGENT-ADD] ConstraintValidator composition from split validator modules.

from .core import ConstraintValidatorCore
from .saw import SawValidationMixin
from .assembly import AssemblyValidationMixin
from .routing import RoutingValidationMixin
from .bay_rules import BayValidationMixin


class ConstraintValidator(
    ConstraintValidatorCore,
    SawValidationMixin,
    AssemblyValidationMixin,
    RoutingValidationMixin,
    BayValidationMixin,
):
    """Composed validator; behavior mirrors legacy constraint_validator.ConstraintValidator."""

