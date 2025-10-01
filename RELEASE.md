# Release Guide for resilience_h8

## Version Numbering (Semantic Versioning)

We follow [Semantic Versioning](https://semver.org/) (MAJOR.MINOR.PATCH):

- **MAJOR** (X.0.0): Breaking changes, incompatible API changes
- **MINOR** (0.X.0): New features, backward compatible
- **PATCH** (0.0.X): Bug fixes, backward compatible

### Current Version: 0.1.6

### Recommended Next Version: 0.2.0
**Reason**: We've added major new features:
- Redis-based distributed resilience patterns
- UV package manager integration
- Comprehensive integration tests
- Enhanced documentation

---

## Pre-Release Checklist

### 1. Identify Version Bump Type
```bash
# Check current version
make version

# View recent changes
git log --oneline --since="2 weeks ago"
```

**Decision Matrix:**
- Added Redis support -> `minor` (new feature)
- Fixed bugs only -> `patch`
- Breaking API changes -> `major`

### 2. Run All Quality Checks
```bash
# Run comprehensive checks (includes Redis tests)
make release-check

# Or run individually:
make format        # Format code
make lint          # Lint check
make type-check    # Type checking
make test          # Unit tests
make test-redis    # Integration tests with Redis
```

### 3. Update Documentation
- [ ] Update README.md with new features
- [ ] Update CHANGELOG.md with release notes
- [ ] Check all examples work

---

## Release Process

### Option A: Automated (Recommended)

```bash
# 1. Run pre-release checks
make release-check

# 2. Bump version (choose one)
make bump-patch    # 0.1.6 -> 0.1.7
make bump-minor    # 0.1.6 -> 0.2.0  <- Recommended
make bump-major    # 0.1.6 -> 1.0.0

# 3. Review changes
git diff pyproject.toml

# 4. Commit version bump
git add pyproject.toml
git commit -m "chore: bump version to $(make version | grep -o '[0-9]\+\.[0-9]\+\.[0-9]\+')"

# 5. Create and push git tag
make tag-version
git push origin main --tags

# 6. Build distribution packages
make build

# 7. Publish to PyPI
make publish        # Production PyPI
# OR
make publish-test   # Test PyPI (recommended first)
```

### Option B: Manual

```bash
# 1. Edit pyproject.toml manually
vim pyproject.toml  # Change version = "0.1.6" to "0.2.0"

# 2. Commit and tag
git add pyproject.toml
git commit -m "chore: bump version to 0.2.0"
git tag -a "v0.2.0" -m "Release v0.2.0"
git push origin main --tags

# 3. Build with UV (faster!)
uv build

# 4. Publish
uv publish  # Or: make publish
```

---

## Publishing Targets

### Test PyPI (Safe Testing)
```bash
make publish-test
# View at: https://test.pypi.org/project/resilience-h8/
```

### Production PyPI
```bash
make publish
# View at: https://pypi.org/project/resilience-h8/
```

---

## Version Tag Management

### List all version tags:
```bash
git tag -l
```

### View tag details:
```bash
git show v0.2.0
```

### Delete a tag (if needed):
```bash
# Local
git tag -d v0.2.0

# Remote
git push origin :refs/tags/v0.2.0
```

---

## Post-Release Verification

### 1. Verify Package on PyPI
```bash
# Wait 1-2 minutes for propagation, then:
pip install --upgrade resilience_h8

# Or test from TestPyPI:
pip install --index-url https://test.pypi.org/simple/ resilience_h8
```

### 2. Test Installation
```bash
# In a fresh environment
python -m venv test_env
source test_env/bin/activate
pip install resilience_h8[redis]

# Test imports
python -c "from resilience_h8 import RedisCircuitBreaker; print('Success')"
```

### 3. Monitor PyPI
- Package page: https://pypi.org/project/resilience-h8/
- Download stats: https://pepy.tech/project/resilience-h8

---

## Release Notes Template

```markdown
## v0.2.0 - 2025-10-01

### Features
- Added Redis-based distributed resilience patterns
  - RedisCircuitBreaker for distributed circuit breaking
  - RedisTokenBucketRateLimiter and RedisFixedWindowRateLimiter
  - Lua-based atomic operations for accuracy
- Migrated to UV package manager for 10-100x faster installs
- Added comprehensive integration tests with real Redis

### Bug Fixes
- Fixed concurrent batch processing test
- Fixed bulkhead rejection handling

### Improvements
- Updated to modern type annotations (X | Y instead of Union[X, Y])
- Removed deprecated Redis API calls (hmset -> hset, close -> aclose)
- Added make test-redis for Redis integration testing
- Enhanced Makefile with UV-powered commands

### Dependencies
- Added optional Redis dependencies: pip install resilience_h8[redis]

### Documentation
- Added Redis integration examples
- Updated README with distributed patterns guide
```

---

## Troubleshooting

### Problem: "Version already exists on PyPI"
**Solution**: Bump version and try again. PyPI doesn't allow overwriting versions.

### Problem: "Authentication failed"
**Solution**: Set up PyPI API token:
```bash
# Create token at: https://pypi.org/manage/account/token/
# Then configure:
uv publish --token YOUR_TOKEN_HERE
# Or use ~/.pypirc
```

### Problem: "Build failed"
**Solution**: Clean and rebuild:
```bash
make clean
make build
```

### Problem: "Tests failing"
**Solution**: Run full test suite before release:
```bash
make redis-start  # Ensure Redis is running
make test-redis   # Run all tests
```

---

## Quick Reference

| Command | Description |
|---------|-------------|
| `make version` | Show current version |
| `make bump-patch` | Bump patch version |
| `make bump-minor` | Bump minor version |
| `make bump-major` | Bump major version |
| `make release-check` | Run all pre-release checks |
| `make build` | Build distribution packages |
| `make publish-test` | Publish to TestPyPI |
| `make publish` | Publish to PyPI |
| `make tag-version` | Create git tag |

---

## Release Schedule

- **Patch releases**: As needed for bug fixes
- **Minor releases**: Monthly or when significant features are added
- **Major releases**: When breaking changes are necessary

---

**Need help?** Check the [GitHub Issues](https://github.com/Harut8/resilience_h8/issues) or contact the maintainers.
