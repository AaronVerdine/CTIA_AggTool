from __future__ import annotations

import pytest
import yaml

from aggtool.errors import ProfileError
from aggtool.profile import Profile, builtin_profiles, get_profile, load_profile_file


def test_builtin_profile_loads():
    assert "tmo-csca" in builtin_profiles()


def test_unknown_profile():
    with pytest.raises(ProfileError, match="Unknown profile 'nope'"):
        get_profile("nope")


def _base() -> dict:
    return get_profile("tmo-csca").model_dump()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["primary"]["columns"].pop("short_code"), "short_code"),
        (lambda d: d["output"].append({"field": "ghost", "header": "G"}), "ghost"),
        (lambda d: d["filters"]["require_fields"].append("lease_holder"), "lease_holder"),
        (lambda d: d["lookup"]["columns"].update(aggregator=["X"]), "both sources"),
        (lambda d: d["primary"]["columns"].update(extra=[]), "at least one"),
        (lambda d: d.update(unexpected=1), "unexpected"),
    ],
)
def test_invalid_profiles_rejected(mutate, message):
    data = _base()
    mutate(data)
    with pytest.raises(ValueError, match=message):
        Profile.model_validate(data)


def test_load_profile_file(tmp_path):
    path = tmp_path / "p.yaml"
    data = _base() | {"name": "custom"}
    path.write_text(yaml.safe_dump(data))
    assert load_profile_file(path).name == "custom"
    path.write_text("name: [unclosed")
    with pytest.raises(ProfileError):
        load_profile_file(path)
