# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Redis-based distributed resilience patterns
  - RedisCircuitBreaker for distributed circuit breaking across multiple instances
  - RedisTokenBucketRateLimiter for distributed rate limiting with token bucket algorithm
  - RedisFixedWindowRateLimiter for distributed rate limiting with fixed window algorithm
  - RedisStorageBackend, RedisRateLimiterStorage, RedisCircuitBreakerStorage interfaces
  - Lua-based atomic operations for accuracy in distributed environments
- UV package manager integration for 10-100x faster dependency management
- Comprehensive integration tests with real Redis instances
- make test-redis command for Redis integration testing
- make redis-check to verify Redis connection
- Version management commands: bump-patch, bump-minor, bump-major, tag-version, release-check
- RELEASE.md comprehensive release guide
- scripts/bump_version.py automated version bumping utility

### Changed
- Migrated all Makefile commands to use UV (uv run, uv build, uv publish)
- Updated to modern Python type annotations (X | Y instead of Union[X, Y])
- Replaced deprecated Redis API calls (hmset -> hset, close -> aclose)
- Enhanced Makefile with UV-powered commands for better developer experience
- Improved test organization with @pytest.mark.integration for Redis tests
- Updated pyproject.toml with [tool.ruff] configuration replacing black/isort/flake8

### Fixed
- Fixed test_resilience_concurrent_batch_processing by processing operations in batches
- Fixed bulkhead rejection handling in concurrent scenarios
- Fixed Redis integration test skip logic to properly check --redis flag
- Fixed mock tests to use hset instead of deprecated hmset

### Dependencies
- Added optional Redis dependencies: redis[hiredis]>=5.0.0
- Install with: pip install resilience_h8[redis]

### Documentation
- Added Redis distributed patterns example (examples/redis_distributed_example.py)
- Enhanced README with Redis integration guide
- Added comprehensive release guide (RELEASE.md)

---

## [0.1.6] - 2024-XX-XX

### Added
- Rate limited retries functionality

### Changed
- Code cleanup and formatting with ruff

---

## [0.1.5] - 2024-XX-XX

### Changed
- Updated README documentation
- General improvements and bug fixes

---

## [0.1.0] - Initial Release

### Added
- Bulkhead pattern for limiting concurrent operations
- Circuit breaker pattern for failing fast
- Retry pattern with configurable backoff
- Timeout pattern for operation timeouts
- Rate limiting with token bucket and fixed window algorithms
- AsyncTaskManager for async task management
- StandardTaskManager for sync operations
- Comprehensive test suite
- Examples and documentation

---

[Unreleased]: https://github.com/Harut8/resilience_h8/compare/v0.1.6...HEAD
[0.1.6]: https://github.com/Harut8/resilience_h8/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/Harut8/resilience_h8/releases/tag/v0.1.5
[0.1.0]: https://github.com/Harut8/resilience_h8/releases/tag/v0.1.0
