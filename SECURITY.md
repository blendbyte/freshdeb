# Security policy

## Supported versions

| What | Supported |
|---|---|
| The current package in each component whose upstream line is still supported | Yes |
| Older package versions in a supported component | No, please upgrade first |
| Components past their end-of-life date (`eol` in `packages/*/package.yaml`) | No, please move to a supported line |
| Distributions listed in the README | Yes |

Security fixes reach you as new packages in the same component. We publish upstream's fixed releases. We don't backport fixes to release lines upstream no longer supports. Please upgrade to the current package in your component before checking whether a reported issue still applies.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through [GitHub's vulnerability reporting form](https://github.com/blendbyte/freshdeb/security/advisories/new). If the form is unavailable, email security@blendbyte.com and ask for a private channel before sending sensitive details.

Include the affected package and version, your distribution and architecture, the APT component you use, steps to reproduce the issue, and its potential impact. Please remove credentials, personal data, and any information belonging to other users.

Please avoid public issues, discussions, or pull requests that disclose a vulnerability before a fix is available. We will review reports, discuss any additional information needed, and coordinate disclosure and a fix with the reporter. Response and fix times depend on the severity and complexity of the issue.

## Scope

- Build infrastructure: Dockerfiles, scripts, CI workflows
- Packaging: our patches and overlays, maintainer scripts, dependencies
- The repository at deb.freshdeb.com and its signing

Vulnerabilities in the packaged software itself belong to its upstream project; once they release a fix, watch.yml picks it up and we publish it. Issues in Debian's packaging that also affect Debian should go to Debian as well. If you're not sure, report it to us and we'll route it.

## Signing key

The repository and its packages are signed with a key used only for deb.freshdeb.com.

- **Fingerprint**: `3BBD F03A 9C0B F890 F8CA  9510 7595 CC55 468F 3161`
- **User ID**: `freshdeb Archive Signing Key <packages@freshdeb.com>` (RSA 4096)
- **Key**: https://deb.freshdeb.com/freshdeb-archive-keyring.gpg

```bash
curl -fsSL https://deb.freshdeb.com/freshdeb-archive-keyring.gpg | gpg --show-keys --with-fingerprint
```

If the fingerprint doesn't match, don't use the key and open an issue.

## Supply chain

- Upstream sources are pinned to commit hashes in `package.yaml`. If a tag moves, the build fails instead of following it.
- Packaging comes from Debian, imported unmodified. Every change we make is a visible patch in this repository.
- Builds run in fresh containers on GitHub-hosted runners, as an unprivileged user.
- The signing key exists only as a secret of the `production` environment, used by the release job.
- Releases never delete or replace published files. Each release keeps a backup of the previous repository state.
