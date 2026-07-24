# DramaClaw Skills

Community Skill repository for DramaClaw.

## Layout

Each published Skill lives in its own folder:

```text
skills/<skill-id>/bundle.json
skills/<skill-id>/cover.webp
```

`bundle.json` must use `schema_version: "dramaclaw.skill-bundle.v1"` and include one Skill plus the Recipes referenced by `skill.allowed_recipe_ids`.

## Local Checks

```bash
python3 -m unittest discover -s tests
python3 scripts/build_catalog.py validate --root . --allow-empty
python3 scripts/build_catalog.py build --root . --output catalog.json --allow-empty
```

The CI rejects unsafe Bundle content such as scripts, command/env fields, secrets, and hardcoded supplier model names.
