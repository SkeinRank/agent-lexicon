# Security Policy

## Supported versions

Agent Lexicon is an early, actively developed project (0.x). Security fixes are
applied to the latest released version on the `main` branch. Older 0.x releases
are not separately patched; please upgrade to the latest version.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.**

Instead, report privately through GitHub's built-in
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
go to the repository's **Security** tab and choose **Report a vulnerability**.

Please include:

- A description of the issue and its impact.
- Steps to reproduce, or a proof-of-concept if you have one.
- The affected version and your environment.
- Any suggested mitigation, if known.

## What to expect

- We aim to acknowledge a report within a few business days.
- We will confirm the issue and determine the affected versions.
- We will work on a fix and coordinate a release, keeping you updated on
  progress.
- We are happy to credit you in the release notes once the fix is public,
  unless you prefer to remain anonymous.

## Scope

Agent Lexicon runs locally and has a dependency-free core, which keeps its
attack surface small. Areas that are especially relevant to security reports:

- **Prompt-injection scanning** (`safety/prompt_injection.py`) failing to flag
  content it is meant to flag.
- **Unicode normalization** allowing an obfuscated surface (invisible
  separators, full-width characters, bidi-control tricks) to slip a different
  term past the matcher or steer a guard decision.
- **Tool guarding** allowing a tool call it should have blocked.
- **Workspace storage** allowing torn reads or corruption of the local SQLite
  workspace or provenance log.

Determinism and auditability are core guarantees. A reproducible way to make a
decision non-reproducible, or to break the provenance trail, is in scope.
