#!/usr/bin/env python3
"""Validate community Skill Bundles and build catalog.json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BUNDLE_SCHEMA_VERSION = "dramaclaw.skill-bundle.v1"
CATALOG_SCHEMA_VERSION = "dramaclaw.community-catalog.v1"
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

DANGEROUS_FIELD_NAMES = {
    "api_key",
    "code",
    "command",
    "commands",
    "entrypoint",
    "env",
    "executable",
    "password",
    "private_key",
    "script",
    "scripts",
    "secret",
    "secrets",
    "token",
}
SECRET_MARKERS = (
    "AKIA",
    "BEGIN PRIVATE KEY",
    "NEWAPI_API_KEY",
    "OPENAI_API_KEY",
    "ghp_",
    "github_pat_",
    "sk-",
)
SUPPLIER_MODEL_MARKERS = (
    "gpt-image-",
    "seedance-",
)


class CatalogValidationError(ValueError):
    """Raised when the repository contains an invalid or unsafe Skill Bundle."""


@dataclass(frozen=True)
class BundleRecord:
    bundle_id: str
    path: Path
    bundle: dict[str, Any]


def validate_repository(root: Path | str, *, allow_empty: bool = False) -> list[BundleRecord]:
    repo_root = Path(root)
    bundle_paths = sorted((repo_root / "skills").glob("*/bundle.json"))
    if not bundle_paths:
        if allow_empty:
            return []
        raise CatalogValidationError("No skill bundles found under skills/<skill-id>/bundle.json")

    records = [_load_and_validate_bundle(repo_root, path) for path in bundle_paths]
    duplicate_ids = _duplicates([record.bundle_id for record in records])
    if duplicate_ids:
        raise CatalogValidationError(f"duplicate Bundle ids: {', '.join(duplicate_ids)}")
    return records


def build_catalog(
    root: Path | str,
    *,
    raw_base_url: str,
    allow_empty: bool = False,
) -> dict[str, Any]:
    base = raw_base_url.rstrip("/")
    records = validate_repository(root, allow_empty=allow_empty)
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "items": [_catalog_item(record, base) for record in records],
    }


def write_catalog(
    root: Path | str,
    *,
    output: Path | str,
    raw_base_url: str,
    allow_empty: bool = False,
) -> dict[str, Any]:
    catalog = build_catalog(root, raw_base_url=raw_base_url, allow_empty=allow_empty)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return catalog


def _load_and_validate_bundle(repo_root: Path, path: Path) -> BundleRecord:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogValidationError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CatalogValidationError(f"{path}: Bundle must be a JSON object")

    _scan_unsafe(payload, label=str(path))
    _validate_bundle_shape(payload, path)
    bundle_id = str(payload["id"])
    expected_path = repo_root / "skills" / bundle_id / "bundle.json"
    if path != expected_path:
        raise CatalogValidationError(f"{path}: path must match bundle id: skills/{bundle_id}/bundle.json")
    return BundleRecord(bundle_id=bundle_id, path=path, bundle=payload)


def _validate_bundle_shape(bundle: dict[str, Any], path: Path) -> None:
    _require_string(bundle, "schema_version", path, expected=BUNDLE_SCHEMA_VERSION)
    _require_id(bundle, "id", path)
    _require_string(bundle, "name", path)
    _require_semver(bundle, "version", path)
    _require_string(bundle, "description", path)
    _require_string(bundle, "author", path)
    _require_string(bundle, "license", path)
    _require_semver(bundle, "min_dramaclaw_version", path)
    _require_string_list(bundle, "tags", path, allow_empty=True)

    skill = bundle.get("skill")
    recipes = bundle.get("recipes")
    if not isinstance(skill, dict):
        raise CatalogValidationError(f"{path}: skill must be an object")
    if not isinstance(recipes, list) or any(not isinstance(recipe, dict) for recipe in recipes):
        raise CatalogValidationError(f"{path}: recipes must be an array of objects")

    _validate_skill(skill, bundle_id=str(bundle["id"]), path=path)
    recipe_ids = [_validate_recipe(recipe, path=path) for recipe in recipes]
    duplicate_recipe_ids = _duplicates(recipe_ids)
    if duplicate_recipe_ids:
        raise CatalogValidationError(f"{path}: duplicate Recipe ids: {', '.join(duplicate_recipe_ids)}")
    missing = sorted(set(skill.get("allowed_recipe_ids", [])) - set(recipe_ids))
    if missing:
        raise CatalogValidationError(f"{path}: missing referenced Recipes: {', '.join(missing)}")


def _validate_skill(skill: dict[str, Any], *, bundle_id: str, path: Path) -> None:
    _require_string(skill, "schema_version", path, expected="dramaclaw.workflow-skill.v1")
    _require_id(skill, "id", path)
    if skill["id"] != bundle_id:
        raise CatalogValidationError(f"{path}: skill.id must match bundle id")
    _require_string(skill, "name", path)
    _require_semver(skill, "version", path)
    _require_string(skill, "description", path)
    _require_string(skill, "category", path)
    _require_id_list(skill, "allowed_recipe_ids", path, allow_empty=True)


def _validate_recipe(recipe: dict[str, Any], *, path: Path) -> str:
    _require_string(recipe, "schema_version", path, expected="dramaclaw.recipe.v1")
    _require_id(recipe, "id", path)
    _require_string(recipe, "name", path)
    _require_semver(recipe, "version", path)
    _require_string(recipe, "output_kind", path)
    _require_string_list(recipe, "action_keys", path)
    return str(recipe["id"])


def _catalog_item(record: BundleRecord, raw_base_url: str) -> dict[str, Any]:
    bundle = record.bundle
    bundle_path = f"skills/{record.bundle_id}/bundle.json"
    item = {
        "id": bundle["id"],
        "name": bundle["name"],
        "version": bundle["version"],
        "description": bundle["description"],
        "author": bundle["author"],
        "license": bundle["license"],
        "min_dramaclaw_version": bundle["min_dramaclaw_version"],
        "tags": bundle.get("tags", []),
        "bundle_url": f"{raw_base_url}/{bundle_path}",
    }
    cover_path = record.path.parent / "cover.webp"
    if cover_path.is_file():
        item["cover_url"] = f"{raw_base_url}/skills/{record.bundle_id}/cover.webp"
    return item


def _require_string(
    payload: dict[str, Any],
    key: str,
    path: Path,
    *,
    expected: str | None = None,
) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogValidationError(f"{path}: {key} must be a non-empty string")
    if expected is not None and value != expected:
        raise CatalogValidationError(f"{path}: {key} must equal {expected}")


def _require_semver(payload: dict[str, Any], key: str, path: Path) -> None:
    _require_string(payload, key, path)
    if not SEMVER.fullmatch(str(payload[key])):
        raise CatalogValidationError(f"{path}: {key} must use major.minor.patch")


def _require_id(payload: dict[str, Any], key: str, path: Path) -> None:
    _require_string(payload, key, path)
    if not SAFE_ID.fullmatch(str(payload[key])):
        raise CatalogValidationError(f"{path}: {key} contains unsafe characters")


def _require_id_list(
    payload: dict[str, Any],
    key: str,
    path: Path,
    *,
    allow_empty: bool = False,
) -> None:
    _require_string_list(payload, key, path, allow_empty=allow_empty)
    for item in payload[key]:
        if not SAFE_ID.fullmatch(item):
            raise CatalogValidationError(f"{path}: {key} contains unsafe id: {item}")


def _require_string_list(
    payload: dict[str, Any],
    key: str,
    path: Path,
    *,
    allow_empty: bool = False,
) -> None:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise CatalogValidationError(f"{path}: {key} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise CatalogValidationError(f"{path}: {key} must not be empty")


def _scan_unsafe(value: Any, *, label: str, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if key_text.lower() in DANGEROUS_FIELD_NAMES:
                raise CatalogValidationError(f"{label}: dangerous field at {path}.{key_text}")
            _scan_unsafe(child, label=label, path=f"{path}.{key_text}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_unsafe(child, label=label, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        for marker in SECRET_MARKERS:
            if marker in value:
                raise CatalogValidationError(f"{label}: possible secret at {path}")
        lowered = value.lower()
        for marker in SUPPLIER_MODEL_MARKERS:
            if marker in lowered:
                raise CatalogValidationError(f"{label}: supplier model name is not allowed at {path}")


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate skills/<id>/bundle.json files.")
    validate_parser.add_argument("--root", type=Path, default=Path.cwd())
    validate_parser.add_argument("--allow-empty", action="store_true")

    build_parser = subparsers.add_parser("build", help="Build catalog.json from community Skill Bundles.")
    build_parser.add_argument("--root", type=Path, default=Path.cwd())
    build_parser.add_argument("--output", type=Path, default=Path("catalog.json"))
    build_parser.add_argument(
        "--raw-base-url",
        default="https://raw.githubusercontent.com/dramaclaw/dramaclaw-skills/main",
    )
    build_parser.add_argument("--allow-empty", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv or sys.argv[1:]))
    try:
        if args.command == "validate":
            records = validate_repository(args.root, allow_empty=args.allow_empty)
            print(f"Validated {len(records)} Skill Bundle(s).")
            return 0
        if args.command == "build":
            catalog = write_catalog(
                args.root,
                output=args.output,
                raw_base_url=args.raw_base_url,
                allow_empty=args.allow_empty,
            )
            print(f"Wrote {args.output} with {len(catalog['items'])} item(s).")
            return 0
    except CatalogValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
