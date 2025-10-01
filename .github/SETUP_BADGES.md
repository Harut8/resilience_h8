# Setting Up Coverage and Badges for GitHub

## Overview
Your repository now has GitHub Actions CI/CD and coverage reporting configured. After pushing to GitHub, you'll see badges for build status, code coverage, and more on your README.

## What's Included

### GitHub Actions Workflows
- **CI Workflow** (`.github/workflows/ci.yml`): Runs on every push/PR
  - Tests on Python 3.11 and 3.12
  - Includes Redis for integration tests
  - Generates coverage reports
  - Runs linting and type checking

- **Publish Workflow** (`.github/workflows/publish.yml`): Runs on version tags
  - Automatically publishes to PyPI when you create a release

- **CodeQL Workflow** (`.github/workflows/codeql.yml`): Security scanning
  - Runs weekly and on every push to main

### Badges in README
- CI status
- Code coverage
- PyPI version
- Python versions
- License
- Code style (ruff)

## Setup Steps

### 1. Push to GitHub
```bash
git add .
git commit -m "Add CI/CD with coverage reporting"
git push origin main
```

### 2. Enable GitHub Actions
- Go to your repository on GitHub
- Click "Actions" tab
- If prompted, enable Actions for your repository
- The workflows will run automatically

### 3. Set Up Codecov (For Coverage Badge)

**Option A: Using Codecov (Free for open source)**
1. Go to https://codecov.io/
2. Sign in with GitHub
3. Add your repository: https://github.com/Harut8/resilience_h8
4. Get your upload token from the repository settings
5. Add the token as a GitHub secret:
   - Go to: Settings > Secrets and variables > Actions
   - Click "New repository secret"
   - Name: `CODECOV_TOKEN`
   - Value: [paste your token]

**Option B: Skip Codecov**
If you don't want Codecov, remove these lines from `.github/workflows/ci.yml`:
```yaml
- name: Upload coverage to Codecov
  uses: codecov/codecov-action@v4
  ...
```

And remove this badge from README.md:
```markdown
[![codecov](https://codecov.io/gh/Harut8/resilience_h8/branch/main/graph/badge.svg)](https://codecov.io/gh/Harut8/resilience_h8)
```

### 4. Set Up PyPI Publishing

To enable automatic publishing when you create a release:

1. Create a PyPI API token:
   - Go to https://pypi.org/manage/account/token/
   - Create a new token with "Entire account" scope

2. Add the token as a GitHub secret:
   - Go to: Settings > Secrets and variables > Actions
   - Click "New repository secret"
   - Name: `PYPI_API_TOKEN`
   - Value: [paste your token with `pypi-` prefix]

3. When ready to publish:
```bash
make bump-minor  # or bump-patch, bump-major
git add pyproject.toml
git commit -m "chore: bump version to X.Y.Z"
git push origin main
make tag-version
git push origin main --tags
```

The publish workflow will automatically run and deploy to PyPI!

## Viewing Coverage Reports

### On GitHub
After the CI workflow runs:
1. Go to Actions tab
2. Click on a workflow run
3. Download the coverage artifacts

### Locally
```bash
# Run tests with coverage
make test-cov

# Open HTML report
open htmlcov/index.html
```

## Checking Build Status

After pushing, you can:
1. Go to the "Actions" tab in your GitHub repository
2. See all workflow runs
3. Click on any run to see detailed logs
4. The badges in README will automatically update

## Troubleshooting

### Badges show "unknown"
- Wait a few minutes after first push for workflows to complete
- Make sure the workflow file names match the badge URLs

### CI fails
- Check the Actions tab for error logs
- Ensure all dependencies are in pyproject.toml
- Redis tests require Redis service (included in CI workflow)

### Coverage not uploading
- Verify CODECOV_TOKEN is set correctly
- Check workflow logs for upload errors
- May need to wait for Codecov to process first upload

## Useful Commands

```bash
# Check if workflows are valid
make format-check
make lint
make type-check
make test

# Run full CI pipeline locally
make ci

# Run with Redis integration tests
make test-redis
```

## Next Steps

1. Push your changes to GitHub
2. Monitor the Actions tab for first workflow run
3. Set up Codecov token for coverage badge
4. Set up PyPI token for automated releases
5. Create your first release!

For more information, see:
- GitHub Actions: https://docs.github.com/en/actions
- Codecov: https://docs.codecov.com/
- PyPI Publishing: https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/
