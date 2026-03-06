## Summary

<!-- One sentence: what does this PR do? -->

## Motivation

<!-- Why is this change needed? Link to an issue if one exists. -->

Closes #

## Changes

<!-- Bullet list of what changed and why. -->

-
-

## How to test

```bash
# Commands to verify the change works
pytest tests/ -q
zbx validate configs/
```

## Checklist

- [ ] Tests pass (`pytest tests/ -q`)
- [ ] New behaviour has a test
- [ ] Public functions/methods have docstrings
- [ ] `zbx validate configs/` passes for any new/changed check YAML
- [ ] CHANGELOG.md updated under `[Unreleased]`
- [ ] No credentials or secrets in the diff

## Type of change

- [ ] 🐛 Bug fix
- [ ] ✨ New feature
- [ ] 📦 New bundled check
- [ ] 📝 Documentation
- [ ] ♻️ Refactor (no behaviour change)
- [ ] 🔧 CI / tooling
