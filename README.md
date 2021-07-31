[![Actions Status](https://github.com/languitar/pass-git-helper/workflows/CI%20build/badge.svg)](https://github.com/languitar/pass-git-helper/actions) [![codecov](https://codecov.io/gh/languitar/pass-git-helper/branch/master/graph/badge.svg)](https://codecov.io/gh/languitar/pass-git-helper)

# pass-git-helper

A [git] credential helper implementation that allows using [pass] as the credential backend for your git repositories.
This is achieved by explicitly defining mappings between hosts and entries in the password store.

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

To instruct git to use the helper, set the `credential.helper` configuration option of git to `/full/path/to/pass-git-helper`.
In case you do not want to include a full path, a workaround using a shell fragment needs to be used, i.e. `!pass-git-helper $@` must be the option value.
The option can be set using the CLI with:

```sh
git config credential.helper '!pass-git-helper $@'
```

If you want to match entries not only based on the host, but also based on the path on a host, set `credential.useHttpPath` to `true` in your git config, e.g. via:

```sh
git config credential.useHttpPath true
```

Afterwards, entries can be matched against `host.com/path/to/repo` in the mapping.
This means that in order to use a specific account for a certain Github project, you can then use the following mapping pattern:

```ini
[github.com/username/project*]
target=dev/github
```

Please note that when including the path in the mapping, the mapping expressions need to match against the whole path.
As a consequence, in case you want to use the same account for all Github projects, you need to make sure that a wildcard covers the path of the URL, as shown here:

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

### section with protocol

github.com has deprecated passwords.
One can use ssh or, for https, tokens.
For tokens the protocol needs to be considered.
A secion header for the host with the fitting protocol is used before
a section header for the host without protocol (backward compatibility).

Example for user `replace_with_your_org` with the https token saved by
`pass insert https_github.com/replace_with_your_org`:

```ini
[DEFAULT]
username_extractor=entry_name

[https://github.com/**]
target=${protocol}_${host}/replace_with_your_org
```


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
* `skip_username`: Number of characters to skip at the beginning of the line, for instance to skip a `user:` prefix. Similar to `skip_password`. Default: 0.

#### Strategy "regex_search"

Searches for the first line that matches a provided regular expressions and returns the contents of that line that are captured in a regular expression capture group.

Configuration:

* `regex_username`: The regular expression to apply. Has to contain a single capture group for indicating the data to extract. Default: `^username: +(.*)$`.

#### Strategy "entry_name"

Returns the last path fragment of the passwordstore entry as the username.
For instance, if a regular [pass] call would be `pass show dev/github.com/languitar`, the returned username would be `languitar`.

No configuration options.

### File Encoding

By default, passwordstore entries are assumed to use UTF-8 encoding.
If all or some of your entries use a different encoding, use the `encoding` key (for instance, in the `DEFAULT` section) to specify the used encoding.

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
