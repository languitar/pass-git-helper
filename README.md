[![Actions Status](https://github.com/languitar/pass-git-helper/workflows/CI%20build/badge.svg)](https://github.com/languitar/pass-git-helper/actions) [![codecov](https://codecov.io/gh/languitar/pass-git-helper/branch/master/graph/badge.svg)](https://codecov.io/gh/languitar/pass-git-helper)

# pass-git-helper

[![Debian CI](https://badges.debian.net/badges/debian/testing/pass-git-helper/version.svg)](https://buildd.debian.org/pass-git-helper) [![AUR](https://img.shields.io/aur/version/pass-git-helper.svg)](https://aur.archlinux.org/packages/pass-git-helper/)

A [git] credential helper implementation that allows using [pass] as the credential backend for your git repositories.
This is achieved by explicitly defining mappings between hosts and entries in the password store.

## Preconditions

GPG must be configured to use a graphical pinentry dialog.
The shell cannot be used due to the interaction required by [git].

## Installation

System-wide:
```sh
sudo python3 setup.py install
```

For a single user:
```sh
python3 setup.py install --user
```

Ensure that `~/.local/bin` is in your `PATH` for the single-user installation.

## Usage

Create the file `~/.config/pass-git-helper/git-pass-mapping.ini`.
This file uses ini syntax to specify the mapping of hosts to entries in the passwordstore database.
Section headers define patterns which are matched against the host part of a URL with a git repository.
Matching supports wildcards (using the python [fnmatch module](https://docs.python.org/3.7/library/fnmatch.html)).
Each section needs to contain a `target` entry pointing to the entry in the password store with the password (and optionally username) to use.

Example:
```ini
[github.com*]
target=dev/github

[*.fooo-bar.*]
target=dev/fooo-bar
```

To instruct git to use the helper, set the `credential.helper` configuration option of git to:
```
/full/path/to/pass-git-helper
```
In case you do not want to include a full path, a workaround using a shell fragment needs to be used, i.e.:
```
!pass-git-helper $@
```

The option can be set e.g. via:
```sh
git config credential.helper '!pass-git-helper $@'
```

If you want to match entries not only based on the host, but also based on the path on a host, set `credential.useHttpPath` to `true` in your git config, e.g. via:
```sh
git config credential.useHttpPath true
```
Afterwards, entries can be matched against `host.com/path/to/repo` in the mapping.
This means that in order to use a specific account for a certain github project, you can then use the following mapping pattern:
```ini
[github.com/username/project*]
target=dev/github
```
Please note that when including the path in the mapping, the mapping expressions need to match against the whole path.
As a consequence, in case you want to use the same account for all github projects, you need to make sure that a wildcard covers the path of the URL, as shown here:
```ini
[github.com*]
target=dev/github
```
The host can be used as a variable to address a pass entry.
This is especially helpful for wildcard matches:
```ini
[*]
target=git-logins/${host}
```
The above configuration directive will lead to any host that did not match any previous section in the ini file to being looked up under the `git-logins` directory in your passwordstore.

Using the `includeIf` directive available in git >= 2.13, it is also possible to perform matching based on the current working directory by invoking `pass-git-helper` with a conditional `MAPPING-FILE`.
To achieve this, edit your `.gitconfig`, e.g. like this:
```ini
[includeIf "gitdir:~/src/user1/"]
    path=~/.config/git/gitconfig_user1
[includeIf "gitdir:~/src/user2/"]
    path=~/.config/git/gitconfig_user2
```
With the following contents of `gitconfig_user1` (and `gitconfig_user2` repspectively), `mapping_user1.ini`, which could contain a `target` entry to e.g. `github.com/user1` would always be invoked in `~/src/user1`:
```ini
[user]
    name = user1
[credential]
    helper=/full/path/to/pass-git-helper -m /full/path/to/mapping_user1.ini
```
See also the offical [documentation](https://git-scm.com/docs/git-config#_includes) for `.gitconfig`.  

### DEFAULT section

Defaults suitable for all entries of the mapping file can be specified in a special section of the configuration file named `[DEFAULT]`.
Everything configure in this section will automatically be available for all further entries in the file, but can be overriden there, too.

## Passwordstore Layout and Data Extraction

### Password

As usual with [pass], this helper assumes that the password is contained in the first line of the passwordstore entry.
Though uncommon, it is possible to strip a prefix from the data of the first line (such as `password:` by specifying an amount of characters to leave out in the `skip_password` field for an entry or also in the `[DEFAULT]` section to apply for all entries:

```ini
[DEFAULT]
# length of "password: "
skip_password=10

[somedomain]
# for some reasons, this entry doesn't have a password prefix
skip_password=0
target=special/noprefix
```

### Username

`pass-git-helper` can also provide the username necessary for authenticating at a server.
In contrast to the password, no clear convention exists how username information is stored in password entries.
Therefore, multiple strategies to extract the username are implemented and can be selected globally for the whole passwordstore in the `[DEFAULT]` section, or individually for certain entries using the `username_extractor` key:

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
* `skip_username`: Number of characters to skip at the beginning of the line, for instance to skip a `user: ` prefix. Similar to `skip_password`. Default: 0.

#### Strategy "regex_search"

Searches for the first line that matches a provided regular expressions and returns the contents of that line that are captured in a regular expression capture group.

Configuration:
* `regex_username`: The regular expression to apply. Has to contain a single capture group for indicating the data to extract. Default: `^username: +(.*)$`.

#### Strategy "entry_name"

Returns the last path fragment of the passwordstore entry as the username.
For instance, if a regular [pass] call would be `pass show dev/github.com/languitar`, the returned username would be `languitar`.

No configuration options.

## Command Line Options

`-l` can be given as an option to the script to produce logging output on stderr.
This might be useful to understand how the mapping is applied.

`-m MAPPING_FILE` can be specified to use an alternative mapping file location.

## Skipping Processing

In some automated contexts it might be necessary to prevent GPG from asking for the passphrase (via the agent).
To achieve this, you can disable the complete processing of this helper by defining the environment variable `PASS_GIT_HELPER_SKIP` with any content (or no content at all).
pass-git-helper will return immediately in this case, indicating to git that no suitable credentials could be found.

## License

This library is [free software](https://en.wikipedia.org/wiki/Free_software); you can redistribute it and/or modify it under the terms of the [GNU Lesser General Public License](https://en.wikipedia.org/wiki/GNU_Lesser_General_Public_License) as published by the [Free Software Foundation](https://en.wikipedia.org/wiki/Free_Software_Foundation); either version 3 of the License, or any later version. This work is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the [GNU Lesser General Public License](https://www.gnu.org/copyleft/lgpl.html) for more details.

[git]: https://git-scm.com/
[pass]: http://www.passwordstore.org/ "pass - the standard unix password manager"
