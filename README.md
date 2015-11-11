# pass-git-helper

A [git] credential helper implementation which allows to use [pass] as the credential backend for your git repositories.
This is achieved by explicitly defining mappings between hosts and entries in the password store.

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

Create the file `~/.git-pass-mapping`.
This file uses ini syntax to specify the mapping of hosts to entries in the passwordstore database.
Section headers define patterns which are matched against the host part of a URL with a git repository.
Matching supports wildcards (using the python [fnmatch module](https://docs.python.org/3.4/library/fnmatch.html)).
Each section needs to contain a `target` entry pointing to the entry in the password store with the password (and optionally username) to use.

Example:
```ini
[github.com]
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

## Passwordstore Layout

As usual with [pass], this helper assumes that the password is contained in the first line of the passwordstore entry.
Additionally, if a second line is present, this line is interpreted as the username and also returned back to the git process invoking this helper.

## Command Line Options

`-l` can be given as an option to the script to produce logging output on stderr.
This might be useful to understand how the mapping is applied.

`-m MAPPING_FILE` can be specified to use an alternative mapping file location.

## License

This library is [free software](https://en.wikipedia.org/wiki/Free_software); you can redistribute it and/or modify it under the terms of the [GNU Lesser General Public License](https://en.wikipedia.org/wiki/GNU_Lesser_General_Public_License) as published by the [Free Software Foundation](https://en.wikipedia.org/wiki/Free_Software_Foundation); either version 3 of the License, or any later version. This work is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the [GNU Lesser General Public License](https://www.gnu.org/copyleft/lgpl.html) for more details.

[git]: https://git-scm.com/
[pass]: http://www.passwordstore.org/ "pass - the standard unix password manager"
