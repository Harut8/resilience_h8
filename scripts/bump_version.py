#!/usr/bin/env python3
"""Script to bump version in pyproject.toml and create git tag."""

import re
import sys
from pathlib import Path


def get_current_version(pyproject_path: Path) -> str:
    """Get current version from pyproject.toml."""
    content = pyproject_path.read_text()
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    if not match:
        raise ValueError("Could not find version in pyproject.toml")
    return match.group(1)


def bump_version(version: str, bump_type: str) -> str:
    """Bump version based on type (major, minor, patch)."""
    major, minor, patch = map(int, version.split("."))

    if bump_type == "major":
        return f"{major + 1}.0.0"
    if bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    if bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Invalid bump type: {bump_type}")


def update_pyproject(pyproject_path: Path, old_version: str, new_version: str) -> None:
    """Update version in pyproject.toml."""
    content = pyproject_path.read_text()
    new_content = re.sub(
        rf'^version\s*=\s*["\']({re.escape(old_version)})["\']',
        f'version = "{new_version}"',
        content,
        flags=re.MULTILINE,
    )
    pyproject_path.write_text(new_content)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ["major", "minor", "patch"]:
        print("Usage: python bump_version.py [major|minor|patch]")
        print("\nVersion Bumping Guide:")
        print("  major: Breaking changes (0.1.6 → 1.0.0)")
        print("  minor: New features, backward compatible (0.1.6 → 0.2.0)")
        print("  patch: Bug fixes, backward compatible (0.1.6 → 0.1.7)")
        sys.exit(1)

    bump_type = sys.argv[1]
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    # Get current version
    current_version = get_current_version(pyproject_path)
    print(f"Current version: {current_version}")

    # Calculate new version
    new_version = bump_version(current_version, bump_type)
    print(f"New version: {new_version}")

    # Update pyproject.toml
    update_pyproject(pyproject_path, current_version, new_version)
    print("Updated pyproject.toml")

    print("\nNext steps:")
    print("   1. Review changes: git diff pyproject.toml")
    print(
        f"   2. Commit: git add pyproject.toml && git commit -m 'chore: bump version to {new_version}'"
    )
    print(f"   3. Tag: git tag v{new_version}")
    print("   4. Push: git push origin main --tags")
    print("   5. Build: make build")
    print("   6. Publish: make publish")


if __name__ == "__main__":
    main()
