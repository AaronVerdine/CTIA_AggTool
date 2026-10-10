"""Exceptions whose messages are safe and useful to show to end users."""


class AggToolError(Exception):
    """Base class for expected, user-correctable failures."""


class ProfileError(AggToolError):
    """A profile is missing or invalid."""


class InputFormatError(AggToolError):
    """An input file cannot be read as a spreadsheet."""


class SchemaError(AggToolError):
    """An input file is readable but lacks a required column."""
