# Security Policy

## Supported Versions

bramble is currently pre-1.0. Until a 1.0 release, security fixes are made
only against the latest published release / `main` branch.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| older   | :x:                |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

If you believe you've found a security issue in bramble — for example, a way
to crash, hang, or gain unintended access via a malicious GraphQL request,
schema, or configuration — please report it privately:

* Email: **security@bramble.dev** (replace with your preferred contact
  address before publishing)
* Alternatively, if the repository is hosted on GitHub, you can use
  [GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
  feature under the Security tab.

Please include:

* A description of the issue and its potential impact
* Steps to reproduce, or a minimal example schema/query that triggers it
* The bramble version / commit affected
* Whether you believe the issue is exploitable by an untrusted remote client
  (e.g. via a public GraphQL endpoint) or only by a trusted schema author

### What to expect

* We'll acknowledge your report within **3 business days**.
* We'll aim to provide an initial assessment (confirmed, not a bug, needs
  more info) within **7 business days**.
* We'll credit reporters in the release notes for confirmed issues, unless
  you'd prefer to remain anonymous.
* We ask that you give us a reasonable window to ship a fix before any
  public disclosure. For most issues this will be on the order of 30-90
  days depending on severity and complexity.

### Scope

In scope:

* `bramble-core` and `bramble-py` (the Rust parsing/validation/execution
  engine and its Python bindings)
* The `bramble` Python package, including the HTTP and subscription
  transport adapters
* Denial-of-service vectors reachable by an untrusted client sending
  arbitrary GraphQL documents to a server built with bramble (e.g.
  unbounded recursion or cyclic structures in query validation/execution)
* Authentication/authorization bypass in directive or dependency-injection
  handling, where bramble's own code is responsible for the bypass

Out of scope:

* Vulnerabilities in application code that *uses* bramble (e.g. a resolver
  that itself has a SQL injection bug)
* Vulnerabilities in third-party dependencies — please report those
  upstream; we'll track and update as fixes become available
* Issues that require an already-compromised or fully-trusted schema author
  (bramble, like GraphQL itself, assumes the schema author is trusted; the
  threat model is untrusted *clients*, not untrusted schema code)

## Disclosure Policy

Once a fix is released, we'll publish a security advisory describing the
issue, its severity, affected versions, and the fixed version. Reporters who
want credit will be named unless they ask otherwise.
