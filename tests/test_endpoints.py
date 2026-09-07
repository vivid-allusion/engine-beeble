import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine_beeble  # noqa: E402, F401

ENDPOINTS_DIR = Path(__file__).resolve().parent.parent / "engine_beeble" / "endpoints"

CATEGORY_DIRS = {
    "IMG": "IMG-Models",
    "VID": "VID-Models",
    "TXT": "TXT-Models",
    "VISION": "Vision-Models",
}

VALID_TYPES = {"select", "text", "integer", "number", "boolean"}


def _load_all() -> dict[str, list[dict]]:
    out = {}
    for category, dir_name in CATEGORY_DIRS.items():
        entries = []
        for toml_file in sorted((ENDPOINTS_DIR / dir_name).glob("*.toml")):
            with open(toml_file, "rb") as f:
                entries.append(tomllib.load(f))
        out[category] = entries
    return out


def test_catalog_counts():
    catalog = _load_all()
    assert len(catalog["IMG"]) == 0
    assert len(catalog["VID"]) == 1
    assert len(catalog["TXT"]) == 0
    assert len(catalog["VISION"]) == 0


def test_every_toml_has_required_keys():
    catalog = _load_all()
    for category, entries in catalog.items():
        for ep in entries:
            assert ep["id"], f"{category}: missing id"
            assert ep["label"], f"{ep['id']}: missing label"
            assert ep["category"] == category, f"{ep['id']}: category mismatch"
            assert ep["platform"] == "beeble", f"{ep['id']}: platform must be beeble"
            assert ep["endpoint"], f"{ep['id']}: missing endpoint"
            assert ep["description"], f"{ep['id']}: missing description"


def test_ids_unique_within_category():
    catalog = _load_all()
    for category, entries in catalog.items():
        ids = [ep["id"] for ep in entries]
        assert len(ids) == len(set(ids)), f"duplicate ids in {category}"


def test_select_params_have_options():
    catalog = _load_all()
    for category, entries in catalog.items():
        for ep in entries:
            for name, param in ep.get("params", {}).items():
                if param.get("type") == "select":
                    options = param.get("options") or []
                    assert options, f"{ep['id']}: select param {name} has no options"
                    values = [o["value"] for o in options]
                    assert param.get("default") in values, (
                        f"{ep['id']}: default for {name} not in options"
                    )


def test_params_have_valid_types():
    catalog = _load_all()
    for category, entries in catalog.items():
        for ep in entries:
            for name, param in ep.get("params", {}).items():
                assert param.get("type") in VALID_TYPES, f"{ep['id']}: bad type for {name}"
                assert param.get("label"), f"{ep['id']}: missing label for {name}"


def test_general_image_url_param_is_source_uri():
    catalog = _load_all()
    for category, entries in catalog.items():
        for ep in entries:
            general = ep.get("general", {})
            assert general.get("image_url_param") == "source_uri", (
                f"{ep['id']}: image_url_param must be source_uri"
            )
            assert "reference_image_uri" in general.get("slots", [])
            assert "alpha_uri" in general.get("slots", [])
            assert "source_uri" in general.get("required_slots", [])


def test_generation_type_default_matches_category():
    catalog = _load_all()
    for category, entries in catalog.items():
        for ep in entries:
            default = ep["params"]["generation_type"]["default"]
            assert default == "video", f"{ep['id']}: generation_type default mismatch"


def test_alpha_mode_options_cover_enum():
    catalog = _load_all()
    for category, entries in catalog.items():
        for ep in entries:
            values = [o["value"] for o in ep["params"]["alpha_mode"]["options"]]
            assert values == ["auto", "fill", "select", "custom"]