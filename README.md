[![Actions Status](https://github.com/languitar/pass-git-helper/workflows/CI%20build/badge.svg)](https://github.com/languitar/pass-git-helper/actions) [![codecov](https://codecov.io/gh/languitar/pass-git-helper/branch/master/graph/badge.svg)](https://codecov.io/gh/languitar/pass-git-helper)

# pass-git-helper

A [git] credential helper implementation that allows using [pass] as the credential backend for your https-based git repositories.
When [git] tries to interact with an https-based upstream and needs credentials, this helper will be called to look up the credentials from the user's password store.
Instead of enforcing a specific layout of the password store, a configuration file with explicitly defining mappings between hosts and entries in the password store is used, giving full flexibility to the user on how to structure or reuse existing password databases for [git] authentication.
pass-git-helper will use the mappings to find the correct entry in the user's password store based on the request URL and then provides [git] with the credentials from this entry.

## Preconditions

It is recommended to configure GPG to use a graphical pinentry program.
That way, you can also use this helper when [git] is invoked via GUI programs such as your IDE.
For a configuration example, refer to the [ArchWiki](https://wiki.archlinux.org/index.php/GnuPG#pinentry).
In case you really want to use the terminal for pinentry (via `pinentry-curses`), be sure to [appropriately configure the environment variable `GPG_TTY`](https://www.gnupg.org/documentation/manuals/gnupg/Invoking-GPG_002dAGENT.html), most likely by adding the following lines to your shell initialization:

```sh
GPG_TTY=$(tty)
export GPG_TTY
```

If you use this setup for remote work via SSH, also consider the alternative of [GPG agent forwarding](https://wiki.gnupg.org/AgentForwarding).

## Installation

### Official Packages

If possible, use an available package for your Linux distribution or operating system such as the ones linked below.

[![Packaging status](https://repology.org/badge/vertical-allrepos/pass-git-helper.svg)](https://repology.org/project/pass-git-helper/versions)

### From Source

```sh
sudo pip install .
```

This might potentially install Python packages without the knowledge of your system's package manager.
If all package preconditions are already met, you can also copy the script file to to your system to avoid this problem:

```sh
sudo cp passgithelper.py /usr/local/bin/pass-git-helper
```

Another option is to install the script in an isolated [virtualenv](https://virtualenv.pypa.io/en/latest/):

```sh
virtualenv /your/env
/your/env/pip install .
```

## Usage

### Configure git to use pass-git-helper

To instruct git to use the helper, set the `credential.helper` configuration option of git to `/full/path/to/pass-git-helper`.
In case you do not want to include a full path, a workaround using a shell fragment needs to be used, i.e. `!pass-git-helper $@` must be the option value.
The option can be set using the CLI with:

```sh
git config credential.helper '!pass-git-helper $@'
```

This will result in the following contents in `~/.gitconfig`:

```ini
[credential]
    helper = !pass-git-helper $@
```

In case you share the `~/.gitconfig` across multiple machines and `pass-git-helper` is not available on all of them, the following version does not bail out if pass git helper is missing:

```ini
[credential]
    helper = !type pass-git-helper >/dev/null && pass-git-helper $@
```

`pass-git-helper` can be combined with other helpers.
For instance, the following configuration first tries the git built-in `cache` helper for in-memory password access before falling back to `pass-git-helper` if a cache miss occurs:

```ini
[credential]
    helper = cache
    helper = !type pass-git-helper >/dev/null && pass-git-helper$@
```

### Define Mappings

Create the file `~/.config/pass-git-helper/git-pass-mapping.ini`.
This file uses ini syntax to specify the mapping of hosts to entries in the password store database.
The first matching mapping from the configuration file is used to select the entry from the password store database.
This search process is based on the order of definition in the configuration file.

A section header is a pattern consisting of a host part and, optionally, a path part separated from it by the first `/`:

```text
host-pattern[/path-pattern]
```

Both parts support wildcards, but with different rules:

* The **host part** is matched per DNS label, that is per dot-separated component of the host name.
  Pattern and host are split on `.` and their components matched pairwise, so a pattern only matches a host with the same number of labels, and a wildcard stays inside the label it appears in.
  `*.example.com` matches `a.example.com` but not `a.b.example.com`, and `github.com*` matches `github.community` but not `github.com.evil.com`.
  Within a label, ordinary [fnmatch](https://docs.python.org/3/library/fnmatch.html) syntax applies, character classes included.
  A bare `*` is a catch-all matching any host regardless of its number of labels (see [Catch-All Sections](#catch-all-sections)).
* In the **path part**, ordinary [fnmatch](https://docs.python.org/3/library/fnmatch.html) rules apply and `*` does cross `/`.
* A section with **no path part applies to every path** on a matching host.

Each section needs to contain a `target` entry pointing to the entry in the password store with the password (and optionally username) to use.

Example:

```ini
[github.com]
target=dev/github

[*.fooo-bar.com]
target=dev/fooo-bar
```

To match on the path as well as the host, set `credential.useHttpPath` to `true` in your git config, e.g. via:

```sh
git config credential.useHttpPath true
```

Afterwards, entries can also be matched against `host.com/path/to/repo` in the mapping.
This means that in order to use a specific account for a certain Github project, you can then use the following mapping pattern:

```ini
[github.com/username/project*]
target=dev/github
```

A section without a path part keeps applying to every path, so using the same account for all Github projects needs no wildcard at all:

```ini
[github.com]
target=dev/github
```

#### Variables

The parts of the request can be used as variables to address a pass entry.
Given the remote url `https://languitar@github.com/languitar/pass-git-helper.git`, they are filled in as follows:

| var | value |
| --- | --- |
| `${host}` | `github.com` |
| `${username}` | `languitar` |
| `${path}` | `languitar/pass-git-helper.git` |
| `${protocol}` | `https` |

These values come from the request and therefore from the remote git talks to, so they are validated before use:
a value must not be empty, must not contain control characters, must not contain a path separator except for `${path}`, and cannot escape your password store.

#### Catch-All Sections

`${host}` is especially helpful for a catch-all section:

```ini
[*]
target=git-logins/${host}
```

The above configuration directive will lead to any host that did not match any previous section in the ini file to being looked up under the `git-logins` directory in your password store.

A section that matches any host **must** use `${host}` in its target, and a mapping which does otherwise is rejected.
Without it, the credentials in that one entry would be handed to whatever host git is asked to authenticate against, including a host an attacker controls (see [Security](#security)).
With it, the section is self-limiting: an unknown host resolves to an entry that does not exist, so nothing is decrypted and nothing is returned.
Creating the entry for a host is what grants access to it.

#### DEFAULT Section

Defaults suitable for all entries of the mapping file can be specified in a special section of the configuration file named `[DEFAULT]`.
Everything configure in this section will automatically be available for all further entries in the file, but can be overridden there, too.

### Using Different Mappings Depending on the Working Directory

Using the `includeIf` directive available in git >= 2.13, it is possible to perform matching based on the current working directory by invoking `pass-git-helper` with a conditional `MAPPING-FILE`.
To achieve this, edit your `.gitconfig`, e.g. like this:

```ini
[includeIf "gitdir:~/src/user1/"]
    path=~/.config/git/gitconfig_user1
[includeIf "gitdir:~/src/user2/"]
    path=~/.config/git/gitconfig_user2
```

With the following contents of `gitconfig_user1` (and `gitconfig_user2` respectively), `mapping_user1.ini`, which could contain a `target` entry to e.g. `github.com/user1` would always be invoked in `~/src/user1`:

```ini
[user]
    name = user1
[credential]
    helper=/full/path/to/pass-git-helper -m /full/path/to/mapping_user1.ini
```

See also the official [documentation](https://git-scm.com/docs/git-config#_includes) for `.gitconfig`.

### Switching Password Stores per Mapping

To select a different password store for certain entries, the `password_store_dir` configuration key can be set.
If set to a non-empty value, `pass` is directed to a different data directory by defining the `PASSWORD_STORE_DIR` environment variable when calling `pass`.
If the `password_store_dir` value starts with a tilde (`~`), it will be replaced with the user's *HOME* directory (i.e. with the value of the `HOME` or, on Windows `USERPROFILE`, environment variable, see [os.path.expanduser()](https://docs.python.org/3/library/os.path.html#os.path.expanduser) for details).

The following config demonstrates this practices

```init
[github.com/mycompany]
password_store_dir=~/.work-passwords
```

## Security

### What this helper trusts

git asks a credential helper for the credentials of whatever host it is about to authenticate against, and whatever the helper answers is sent to that host.
The host in a request is **not** necessarily one you chose: it can also come from

* a clone URL you were given (`https://github.com.evil.com/x`),
* a submodule URL inside a `.gitmodules` file of a repository you cloned,
* an HTTP redirect away from a legitimate remote to a host that then asks for authentication.

Your mapping file is therefore the boundary that decides who gets your passwords, and the patterns in it are security-relevant.
Prefer sections that name hosts exactly, and reach for wildcards only where you really need them.

### How mappings limit exposure

* The host part of a section is matched per DNS label, so a wildcard stays inside its own label and a pattern cannot silently extend into a neighbouring domain.
  `[github.com*]` does not match `github.com.evil.com`.
* A section matching any host must address its entry via `${host}`, so it cannot hand one fixed credential to an arbitrary host.
* Credentials are only served over `https` unless a section opts in with `allow_insecure_protocol=true`.
* Values substituted from the request cannot escape your password store, contain path separators or control characters where they do not belong, or be empty.

### Insecure Protocols

By default, credentials are only provided for `https` requests, because git would otherwise transmit them in the clear.
A request that carries no protocol at all is refused for the same reason.
If a remote genuinely needs a plain-text protocol, such as a local test server, allow it per section:

```ini
[localhost]
target=dev/local-test
allow_insecure_protocol=true
```

### Debug Logging

`-l` deliberately does not log decrypted entry contents, extracted passwords or usernames, since git captures the stderr of credential helpers and those logs tend to outlive the command.
It does log which mapping section matched, which includes that section's configuration.

## Migration to 5.0

Version 5.0 tightens mapping semantics to close credential leaks. Check your mapping file against the following changes:

* **Host wildcards no longer cross dots.**
  `[github.com*]` still matches `github.com`, but no longer `github.com.evil.com`.
  `[*.example.com]` now covers exactly one label, so `a.b.example.com` needs `[*.*.example.com]`.
  If you relied on a wildcard spanning several labels, spell the labels out.
* **Sections without a path part now match requests that carry a path.**
  Previously a host-only section stopped matching once `credential.useHttpPath` was enabled, which is why earlier versions of this document told you to append a trailing `*`.
  That is no longer needed: `[github.com]` covers every path on the host. Sections that should only apply to certain paths must name them, as in `[github.com/user/project*]`.
* **A section matching any host must use `${host}` in its target.**
  `[*]` with `target=dev/github` is now rejected; use `target=git-logins/${host}`, or replace the section with ones naming the hosts it should serve.
* **Only `https` is served by default.**
  Add `allow_insecure_protocol=true` to sections used with `http` or other plain-text protocols.

## Password Store Layout and Data Extraction

### Password

As usual with [pass], this helper assumes that the password is contained in the first line of the password store entry.
Although uncommon, it is possible to strip a prefix from the data of the first line (such as `password:` by specifying an amount of characters to leave out in the `skip_password` field for an entry or also in the `[DEFAULT]` section to apply for all entries:

```ini
[DEFAULT]
# length of "password: "
skip_password=10

[somedomain]
# for some reasons, this entry doesn't have a password prefix
skip_password=0
target=special/noprefix
```

However, other two strategies for extracting passwords are implemented, allowing for more flexibility in handling prefixes.

The following strategies can be configured:

#### Specific Line Extraction (default)

Extracts the password from a specified line indexed by its line number.
Optionally, a fixed-length prefix can be stripped before returning the line contents.

Configuration:

* `line_password`: Line number containing the password, **0-based**. Default: 0 (first line).

```ini
[DEFAULT]
# This overrides the default and assumes the password is on the second line (line 1)
line_password=1

[example.com]
# This assumes the password is on the first line of the pass entry (default)
line_password=0
```

#### Regex Extraction

Uses a regular expression to search for the password in the entry.
The first line that matches the provided regular expression will be used, and the contents captured in a regular expression capture group will be returned.

Configuration:

* `regex_password`: The regular expression to apply. It must contain a single capture group for indicating the data to extract. Default: `^password: +(.*)$`.
* `skip_password`: Number of characters to skip at the beginning of the matched line. Default: 0.

```ini

[DEFAULT]
password_extractor=specific_line
line_password=0
# length of "password: "
skip_password=10

[example.com]
# For some reason, this entry doesn't have a password prefix
password_extractor=regex_search
regex_password=^token: +(.*)$
skip_password=0
```

### Username

`pass-git-helper` can also provide the username necessary for authenticating at a server.
Unlike the password (which is expected to be in the **first line** (by default) of a `pass` entry), the username has no fixed convention.

However, if you want `pass-git-helper` to return a `username`, you **must ensure your `pass` entry actually contains it**. (Or if you're using the `static` stratey, your mapping must have the `username` key. See more below.)

Therefore, multiple strategies to extract the username are implemented and can be selected globally for the whole password store in the `[DEFAULT]` section, or individually for certain entries using the `username_extractor` key:

```ini
[DEFAULT]
username_extractor=regex_search
regex_username=^user: (.*)$

[differingdomain.com]
# use a fixed line here instead of a regex search
username_extractor=specific_line
line_username=1
```

The following strategies can be configured:

#### Strategy "specific_line" (default)

Extracts the data from a line indexed by its line number.
Optionally a fixed-length prefix can be stripped before returning the line contents.

Configuration:

* `line_username`: Line number containing the username, **0-based**. Default: 1 (second line)

```ini
[DEFAULT]
# This assumes the username is on the second line of the pass entry (default)
line_username=1

[example.com]
# This overrides the default and assumes the username is on the first line (line 0)
line_username=0
```

#### Strategy "regex_search"

Searches for the first line that matches a provided regular expressions and returns the contents of that line that are captured in a regular expression capture group.

Internally, [Python regular expressions](https://docs.python.org/3/library/re.html#regular-expression-syntax) are used, and you have access to all provided syntax features.

Configuration:

* `regex_username`: The regular expression to apply. Has to contain a single capture group for indicating the data to extract.
  In case your regular expression requires parentheses for matching parts that are not the username, you can use non-capturing parentheses (i.e., `(?:...)`).
  Default: `^username: +(.*)$`.

#### Strategy "entry_name"

Returns the last path fragment of the password store entry as the username.
For instance, if a regular [pass] call would be `pass show dev/github.com/languitar`, the returned username would be `languitar`.

No configuration options.

#### Strategy "static"

Allows defining the username in the mapping file using the `username` key.

Example:

```ini
[DEFAULT]
username_extractor=static
username = myuser

[example.com]
username = myexampleusername
```

### File Encoding

By default, password store entries are assumed to use UTF-8 encoding.
If all or some of your entries use a different encoding, use the `encoding` key (for instance, in the `DEFAULT` section) to specify the used encoding.

## Command Line Options

`-l` can be given as an option to the script to produce logging output on stderr.
This might be useful to understand how the mapping is applied.

`-m MAPPING_FILE` can be specified to use an alternative mapping file location.

`--skip-fs-checks` disables any filesystem level checks which are, by default, run to ensure the presence of a password store (`.gpg`) file before running `pass`. As an alternative to the command line option, the `PASS_GIT_HELPER_SKIP_FS_CHECKS` environment variable can be used to disable the checks (see [Environment Variables](#environment-variables) for details). When both, CLI option and environment variable are used, the former takes precedence. Skipping the checks can support the use of `pass-git-helper` even when `pass` is just a wrapper for a different password management tool (which e.g. does not store password entries as `.gpg` files).

## Environment Variables

* `PASS_GIT_HELPER_SKIP`: See section [Skipping Processing](#skipping-processing).
* `PASS_GIT_HELPER_SKIP_FS_CHECKS`: When set to a non-empty value different from `0`, filesystem level checks for password store files are disabled (any other value will be ignored). See `--skip-fs-checks` in section [Command Line Options](#command-line-options) for more details.

## Skipping Processing

In some automated contexts it might be necessary to prevent GPG from asking for the passphrase (via the agent).
To achieve this, you can disable the complete processing of this helper by defining the environment variable `PASS_GIT_HELPER_SKIP` with any content (or no content at all).
pass-git-helper will return immediately in this case, indicating to git that no suitable credentials could be found.

## Implementation Details

This section contains details about the implementation which are mostly for developers.

## Exit status

The following exit status codes are used:

* 0: Request has been processed successfully
* 1: Unknown/unexpected error
* 2: Error related to command line option processing
* 3: Error related to password retrieval
* 4: Error related to mapping (ini) file processing
* 5: Unsupported `action` (i.e. anything except from `get`)
* 6: Script terminated due to [`PASS_GIT_HELPER_SKIP`](#skipping-processing)

## License

This library is [free software](https://en.wikipedia.org/wiki/Free_software); you can redistribute it and/or modify it under the terms of the [GNU Lesser General Public License](https://en.wikipedia.org/wiki/GNU_Lesser_General_Public_License) as published by the [Free Software Foundation](https://en.wikipedia.org/wiki/Free_Software_Foundation); either version 3 of the License, or any later version. This work is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the [GNU Lesser General Public License](https://www.gnu.org/copyleft/lgpl.html) for more details.

[git]: https://git-scm.com/
[pass]: http://www.passwordstore.org/ "pass - the standard unix password manager"
