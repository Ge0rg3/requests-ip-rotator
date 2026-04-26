# AGENTS.md

## Project Overview

`requests-ip-rotator` is a small Python library that uses AWS API Gateway as
a rotating HTTP proxy for the `requests` library. It lets the caller bypass
IP-based rate limits by routing each request through a regional API Gateway
endpoint, which forwards the request from one of AWS's many egress IPs to
the target site.

- Distributed on PyPI as `requests-ip-rotator`.
- Single runtime module: [requests_ip_rotator/ip_rotator.py](requests_ip_rotator/ip_rotator.py).
- Public surface re-exported from [requests_ip_rotator/__init__.py](requests_ip_rotator/__init__.py).
- Packaging configured in [setup.py](setup.py); current version is `1.0.15`.
- License: GPLv3+ (see [LICENSE](LICENSE)).

### Architecture in one paragraph

[`ApiGateway`](requests_ip_rotator/ip_rotator.py#L33) subclasses
`requests.adapters.HTTPAdapter`. On `start()` it concurrently provisions one
REST API per AWS region (or reuses an existing one with the same name),
configures a `{proxy+}` wildcard resource, an `HTTP_PROXY` integration
pointing at the target site, and deploys it as `ProxyStage`. When mounted on
a `requests.Session`, the adapter's [`send()`](requests_ip_rotator/ip_rotator.py#L56)
rewrites each outgoing request's URL to a randomly chosen endpoint, sets the
`Host` header, and shifts `X-Forwarded-For` into `X-My-X-Forwarded-For` (the
Gateway integration maps it back) so AWS does not leak the caller's real IP.
`shutdown()` deletes the matching REST APIs across all configured regions.

### Region constants

Defined at the top of [ip_rotator.py](requests_ip_rotator/ip_rotator.py#L13-L29):

- `DEFAULT_REGIONS` — 9 widely-available US/EU/CA regions, used when none are passed.
- `EXTRA_REGIONS` — `DEFAULT_REGIONS` plus 7 APAC/SA regions.
- `ALL_REGIONS` — `EXTRA_REGIONS` plus 5 regions that require manual opt-in
  in the AWS account; failures here are caught (`UnrecognizedClientException`)
  and skipped rather than raised.

## Build and Test Commands

There is no test suite, no `Makefile`, and no `requirements*.txt` in the repo.
The only configured tooling is flake8 lint and the PyPI release workflow.

| Task | Command |
| --- | --- |
| Install for development | `pip install -e .` |
| Install runtime deps only | `pip install requests boto3` |
| Lint | `flake8` (config in [tox.ini](tox.ini); only `E501` is ignored) |
| Build sdist + wheel | `python -m build` |
| Publish to PyPI | Cut a GitHub Release — [.github/workflows/python-publish.yml](.github/workflows/python-publish.yml) handles it |

The publish workflow runs `flake8` before building, so a lint failure will
block release. Bump `version=` in [setup.py](setup.py#L13) before tagging.

## Code Style Guidelines

- **Lint:** flake8 with `E501` (line length) disabled — long lines are
  acceptable, but every other PEP 8 rule applies. Run `flake8` from the repo
  root before committing.
- **Python versions:** classifiers in [setup.py](setup.py) advertise 3.7 and
  3.9; keep code compatible with 3.7+. Avoid `match`/`:=` in library code.
- **Dependencies:** runtime deps are `requests` and `boto3` only. Do not add
  new dependencies without a strong reason — this library is intentionally tiny.
- **Style notes from existing code:**
  - Four-space indentation, double-quoted strings.
  - Public API is exported via `from .ip_rotator import *` — keep new public
    names at module scope in `ip_rotator.py`.
  - Boto3 clients are constructed per-call inside the worker methods
    (`init_gateway`, `delete_gateway`); this is intentional because each call
    runs in its own thread inside a `ThreadPoolExecutor(max_workers=10)`.
  - `verbose` controls all status prints; respect that flag for any new
    output.
  - When catching boto exceptions, branch on
    `e.response["Error"]["Code"]` and re-raise unknown codes — don't swallow
    silently.

### Pre-commit checks

**Always run `flake8` from the repo root before creating any commit.** The
publish workflow ([.github/workflows/python-publish.yml](.github/workflows/python-publish.yml))
runs `flake8` and a lint failure blocks the PyPI release, so a dirty commit
on `main` will break the next release. Fix all reported issues before
committing — do not commit and "fix lint in a follow-up". This applies to
every change, including one-line edits and version bumps.

### Commit messages

Never mention Claude, Anthropic, or any AI assistant in commit messages,
trailers, or other commit metadata. No `Co-Authored-By: Claude ...`, no
"generated with" footers, no model names in the subject or body. Commits
should read as if written by the human author.

## Testing Instructions

There is no automated test suite. The `.gitignore` excludes a top-level
`test.py`, suggesting the maintainer iterates with a local scratch script.
When verifying changes:

1. **Smoke test against a benign target** (e.g. `https://httpbin.org/ip` or
   your own endpoint) using valid AWS credentials. The library will create
   real billable resources — see *Security considerations* below.
2. **Verify rotation:** issue ~10 requests through the mounted session and
   confirm the observed origin IP varies across responses.
3. **Verify cleanup:** call `shutdown()` (or use the `with` context manager)
   and confirm in the AWS console that no `*- IP Rotate API* REST APIs
   remain in any of the configured regions.
4. **Test the opt-in region path** by passing `ALL_REGIONS` and confirming
   regions the account hasn't enabled produce only the
   `Could not create region ...` notice (not an exception).
5. **Lint must pass:** `flake8` from the repo root.

If you add tests, the natural home is a new `tests/` directory; mock
`boto3.session.Session` rather than hitting AWS in CI.

## Security Considerations

This library is a tool for bypassing IP-based rate limits, and it provisions
real AWS infrastructure. Treat it accordingly.

- **Authorized use only.** Rotating IPs to evade rate limits or WAFs on
  systems you do not own or do not have written authorization to test is
  abusive and likely illegal. Pentest engagements, CTFs, and your own
  infrastructure are fine; mass scraping of third parties is not.
- **Detectability.** As called out in the README, AWS API Gateway requests
  carry identifying headers (`X-Amzn-Trace-Id`, the `*.execute-api.*.amazonaws.com`
  Host) and are trivially fingerprintable. Do not represent this as
  anonymous traffic.
- **Cost / resource leaks.** `start()` creates REST APIs that AWS bills for.
  `shutdown()` must run, or the caller will keep paying. The `with
  ApiGateway(...)` form in the README is the safest pattern. Gateways
  started with `require_manual_deletion=True` are *intentionally* skipped by
  `shutdown()` and must be deleted manually — preserve that behavior.
- **Credential handling.** Prefer environment variables / the default boto3
  credential chain. The `access_key_id` / `access_key_secret` constructor
  params override env vars; never log them, never commit them, and never
  add them to error messages.
- **Header handling.** The `X-Forwarded-For` → `X-My-X-Forwarded-For`
  rewrite in [`send()`](requests_ip_rotator/ip_rotator.py#L67-L73) exists
  specifically to stop AWS from inserting the caller's real IP into
  `X-Forwarded-For`. Any change to that block needs to preserve the
  invariant that the caller's true IP never reaches the upstream site.
- **Verbose output.** `verbose=True` is the default and prints to stdout. It
  currently only emits region names, endpoint counts, and the configured
  site — keep it that way; do not start logging credentials, full URLs with
  query strings, request bodies, or response data.
- **Concurrency.** `ThreadPoolExecutor(max_workers=10)` is used for both
  start and shutdown. AWS occasionally returns `TooManyRequestsException` —
  the existing `delete_gateway` loop sleeps 1s and retries; mirror that
  pattern for any new throttled call rather than failing.
