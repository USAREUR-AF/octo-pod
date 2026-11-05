# Security Policy

octo-pod sits at the edge of a data fabric: it terminates sensor feeds, holds a fabric
identity, gates what leaves the site and runs a host firewall. Security reports come
before feature work.

## Report a Vulnerability

**Don't open a public issue.** A public report tells an attacker before a fix exists.

Use GitHub private vulnerability reporting: on this repository, open **Security**, then
**Report a vulnerability**. It creates an advisory only the maintainers can see.

Include:

- What the issue is and what an attacker gains.
- Steps or a proof of concept that reproduce it.
- The affected versions, commits or configurations.
- A suggested fix, if you have one.
- Whether you want credit in the advisory.

## What to Expect

| Stage | Target |
|---|---|
| Acknowledgement | Within 3 business days |
| Triage and severity | Within 7 business days |
| Fix and advisory | Depends on severity; you're kept informed |
| Disclosure | Coordinated, by agreement |

A fix ships first, then the advisory is published. Reporters are credited by name
unless they ask not to be.

## Scope

In scope:

- The code, scripts and Ansible in this repository.
- Container images, packages and release artifacts built from this repository by its
  own CI.
- The default configuration a release installs, including the pod firewall and the
  release gates.

Out of scope:

- Pods and fabrics operated by others. Report those to their operators.
- Vulnerabilities in third-party dependencies. Report them upstream, and tell us if
  octo-pod is affected.
