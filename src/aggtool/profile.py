"""Aggregation profiles: the README rules expressed as validated configuration.

A profile describes two input reports (a *primary* list whose rows drive the
output, and a *lookup* list joined onto it), how to recognise their columns,
which rows to drop, and the output layout. New report layouts are new YAML
files, not code changes.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aggtool.errors import ProfileError

KEY_FIELD = "short_code"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceSpec(_Strict):
    """One input report."""

    label: str = Field(description="Human name shown in reports and errors.")
    columns: dict[str, list[str]] = Field(
        description="Canonical field name -> accepted header spellings (case-insensitive)."
    )

    @field_validator("columns")
    @classmethod
    def _key_present(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        if KEY_FIELD not in v:
            raise ValueError(f"every source must map a '{KEY_FIELD}' column")
        for field, aliases in v.items():
            if not aliases:
                raise ValueError(f"field '{field}' needs at least one header alias")
        return v


class FilterSpec(_Strict):
    """Row filters applied to the primary report, in this order."""

    drop_emails: bool = True
    numeric_only: bool = True
    allowed_code_lengths: list[int] = Field(default_factory=lambda: [5, 6])
    require_fields: list[str] = Field(default_factory=list)
    deduplicate: bool = True


class OutputColumn(_Strict):
    field: str
    header: str


class Profile(_Strict):
    name: str
    version: str = "1"
    description: str = ""
    primary: SourceSpec
    lookup: SourceSpec
    filters: FilterSpec = FilterSpec()
    output: list[OutputColumn]

    @model_validator(mode="after")
    def _fields_exist(self) -> Profile:
        known = set(self.primary.columns) | set(self.lookup.columns)
        for col in self.output:
            if col.field not in known:
                raise ValueError(f"output field '{col.field}' is not defined by either source")
        for field in self.filters.require_fields:
            if field not in self.primary.columns:
                raise ValueError(f"required field '{field}' is not a primary column")
        overlap = (set(self.primary.columns) & set(self.lookup.columns)) - {KEY_FIELD}
        if overlap:
            raise ValueError(f"fields defined on both sources: {sorted(overlap)}")
        return self


def load_profile_file(path: str | Path) -> Profile:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return Profile.model_validate(data)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ProfileError(f"Invalid profile {path}: {exc}") from exc


def builtin_profiles() -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for entry in resources.files("aggtool.profiles").iterdir():
        if entry.name.endswith((".yaml", ".yml")):
            data = yaml.safe_load(entry.read_text(encoding="utf-8"))
            profile = Profile.model_validate(data)
            profiles[profile.name] = profile
    return profiles


def get_profile(name: str) -> Profile:
    profiles = builtin_profiles()
    if name not in profiles:
        raise ProfileError(f"Unknown profile '{name}'. Available: {', '.join(sorted(profiles))}")
    return profiles[name]
