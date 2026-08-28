"""Python 3.10-compatible string enum base for manager models."""

from enum import Enum


class StringEnum(str, Enum):
    """Provide string values without depending on Python 3.11 StrEnum."""
