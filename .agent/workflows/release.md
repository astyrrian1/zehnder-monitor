---
description: Tag a release with semver and publish GitHub release notes after every push to main
---

# Release Workflow

This workflow is **mandatory** for every push to `main`. HACS tracks releases via git tags — without a tagged release, users will not receive update notifications.

## Steps

1. **Determine the version bump** by reviewing what changed:
   - **Patch** (`v1.1.x → v1.1.y`): docs-only, typo fixes, minor non-functional changes
   - **Minor** (`v1.x.0 → v1.y.0`): new features, behavioural changes, sensor changes, config changes
   - **Major** (`vX.0.0 → vY.0.0`): breaking changes that require user migration steps

2. **Check the latest existing tag:**
   ```bash
   git tag -l --sort=-v:refname | head -5
   ```

3. **Stage, commit, and push all changes:**
   ```bash
   git add -A
   git commit -m "<type>: <concise summary of changes>"
   git push origin main
   ```
   Commit message prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`

// turbo
4. **Create the GitHub release with release notes:**
   ```bash
   gh release create v<X.Y.Z> \
     --title "v<X.Y.Z> — <Short Title>" \
     --notes '<release notes in markdown>'
   ```

   **Release notes MUST include:**
   - `## What Changed` — bullet list of every meaningful change
   - `## Migration Notes` — (if applicable) steps users must take after updating
   - Mention affected files/sensors/entities by name

5. **Verify the release is live:**
   ```bash
   gh release list
   ```

## Example

```bash
# Check current version
git tag -l --sort=-v:refname | head -1
# → v1.1.1

# Commit
git add -A
git commit -m "feat: add supply/exhaust pressure delta sensor"
git push origin main

# Release
gh release create v1.2.0 \
  --title "v1.2.0 — Pressure Delta Sensor" \
  --notes '## What Changed
- Added `sensor.zehnder_pressure_delta` showing real-time supply/exhaust pressure differential
- Updated MQTT payload to include `pressure_delta_pa` field

## Migration Notes
- No action required — new sensor appears automatically after AppDaemon restart'
```
