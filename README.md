# pass-git-helper

A [git] credential helper implementation which allows to use [pass] as the credential backend for your git repositories.
This is achieved by explicitly defining mappings between hosts and entries in the password store.

## Preconditions

GPG must be configured to use a graphical pinentry dialog.
The shell cannot be used due to the interaction required by [git]

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
Matching supports wildcards (using the python [fnmatch module](https://docs.python.org/3.4/library/fnmatch.html)).
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

## Passwordstore Layout

As usual with [pass], this helper assumes that the password is contained in the first line of the passwordstore entry.
Additionally, if a second line is present, this line is interpreted as the username and also returned back to the git process invoking this helper.
In case you use markers at the start of lines to identify what is contained in this line, e.g. like `Username: fooo`, the options `skip_username` and `skip_password` can be defined in each mapping to skip the given amount of characters from the beginning of the respective line.
Additionally, global defaults can be configured via the `DEFAULT` section:
```ini
[DEFAULT]
# this is actually the default
skip_password=0
# Lenght of "Username: "
skip_username=10

[somedomain]
target=special/somedomain
# somehow this entry does not have a prefix for the username
skip_username=0
```

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
