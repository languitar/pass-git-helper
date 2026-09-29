#!/usr/bin/env python3

"""Implementation of the pass-git-helper utility.

.. codeauthor:: Johannes Wienke
"""

import abc
import argparse
import configparser
import fnmatch
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import IO, Mapping, Optional, Pattern, Sequence, Text

import xdg.BaseDirectory

__version__ = "5.0.0"

LOGGER = logging.getLogger(__name__)
CONFIG_FILE_NAME = "git-pass-mapping.ini"
# C0 controls plus DEL, none of which belong in a password store entry name.
CONTROL_CHARACTERS = dict.fromkeys([*range(32), 127])
DEFAULT_CONFIG_FILE = (
    Path(xdg.BaseDirectory.save_config_path("pass-git-helper")) / CONFIG_FILE_NAME
)


def parse_arguments(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse the command line arguments.

    Args:
        argv:
            If not ``None``, use the provided command line arguments for
            parsing. Otherwise, extract them automatically.

    Returns:
        The argparse object representing the parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Git credential helper using pass as the data source.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-m",
        "--mapping",
        type=argparse.FileType("r"),
        metavar="MAPPING_FILE",
        default=None,
        help="A mapping file to be used, specifying how hosts "
        "map to pass entries. Overrides the default mapping files from "
        "XDG config locations, usually: {config_file}".format(
            config_file=DEFAULT_CONFIG_FILE
        ),
    )
    parser.add_argument(
        "-l",
        "--logging",
        action="store_true",
        default=False,
        help="Print debug messages on stderr. Might include sensitive information",
    )
    parser.add_argument(
        "--skip-fs-checks",
        action="store_true",
        default=os.environ.get("PASS_GIT_HELPER_SKIP_FS_CHECKS", "") not in ("0", ""),
        help=(
            "Skip filesystem level checks to ensure the presence of an actual"
            " password store (.gpg) file before running `pass`. As an"
            " alternative, setting the `PASS_GIT_HELPER_SKIP_FS_CHECKS` environment"
            " variable to a non-empty value different from `0` achieves the same"
            " result."
        ),
    )
    parser.add_argument(
        "action",
        type=str,
        metavar="ACTION",
        help="Action to perform as specified in the git credential API",
    )

    return parser.parse_args(argv)


def parse_mapping(mapping_file: Optional[IO]) -> configparser.ConfigParser:
    """Parse the file containing the mappings from hosts to pass entries.

    Args:
        mapping_file:
            Name of the file to parse. If ``None``, the default file from the
            XDG location is used.
    """
    LOGGER.debug("Parsing mapping file. Command line: %s", mapping_file)

    def parse(mapping_file: IO) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config.read_file(mapping_file)
        return config

    # give precedence to the user-specified file
    if mapping_file is not None:
        LOGGER.debug("Parsing command line mapping file")
        return parse(mapping_file)

    # fall back on XDG config location
    xdg_config_dir = xdg.BaseDirectory.load_first_config("pass-git-helper")
    if xdg_config_dir is None:
        raise RuntimeError(
            "No mapping configured so far at any XDG config location. "
            "Please create {config_file}".format(config_file=DEFAULT_CONFIG_FILE)
        )
    default_file = Path(xdg_config_dir) / CONFIG_FILE_NAME
    LOGGER.debug("Parsing mapping file %s", default_file)
    with default_file.open("r") as file_handle:
        return parse(file_handle)


def parse_request() -> dict[str, str]:
    """Parse the request of the git credential API from stdin.

    Returns:
        A dictionary with all key-value pairs of the request
    """
    in_lines = sys.stdin.readlines()
    LOGGER.debug('Received request (raw) "%s"', in_lines)

    request = {}
    for line in in_lines:
        # skip empty lines to be a bit resilient against protocol errors
        if not line.strip():
            continue

        parts = line.split("=", 1)
        if len(parts) != 2:
            raise ValueError(
                f"Missing '=' in request line, cannot be parsed as key/value pair: '{line}'"
            )
        request[parts[0].strip()] = parts[1].strip()

    return request


class DataExtractor(abc.ABC):
    """Interface for classes that extract values from pass entries."""

    def __init__(self, option_suffix: Text = "") -> None:
        """Create a new instance.

        Args:
            option_suffix:
                Suffix to put behind names of configuration keys for this
                instance. Subclasses must use this for their own options.
        """
        self._option_suffix = option_suffix

    @abc.abstractmethod
    def configure(self, config: configparser.SectionProxy) -> None:
        """Configure the extractor from the mapping section.

        Args:
            config:
                configuration section for the entry
        """

    @abc.abstractmethod
    def get_value(
        self, entry_name: Text, entry_lines: Sequence[Text]
    ) -> Optional[Text]:
        """Return the extracted value.

        Args:
            entry_name:
                Name of the pass entry the value shall be extracted from
            entry_lines:
                The entry contents as a sequence of text lines

        Returns:
            The extracted value or ``None`` if nothing applicable can be found
            in the entry.
        """


class SkippingDataExtractor(DataExtractor):
    """Extracts data from a pass entry and optionally strips a prefix.

    The prefix is a fixed amount of characters.
    """

    def __init__(self, prefix_length: int, option_suffix: Text = "") -> None:
        """Create a new instance.

        Args:
            prefix_length:
                Amount of characters to skip at the beginning of the entry
            option_suffix:
                Suffix to put behind names of configuration keys for this
                instance. Subclasses must use this for their own options.
        """
        super().__init__(option_suffix)
        self._prefix_length = prefix_length

    def configure(self, config: configparser.SectionProxy) -> None:
        """Configure the amount of characters to skip."""
        self._prefix_length = config.getint(
            f"skip{self._option_suffix}",
            fallback=self._prefix_length,
        )

    @abc.abstractmethod
    def _get_raw(self, entry_name: Text, entry_lines: Sequence[Text]) -> Optional[Text]:
        pass

    def get_value(
        self, entry_name: Text, entry_lines: Sequence[Text]
    ) -> Optional[Text]:
        """See base class method."""
        raw_value = self._get_raw(entry_name, entry_lines)
        if raw_value is not None:
            return raw_value[self._prefix_length :]
        else:
            return None


class SpecificLineExtractor(SkippingDataExtractor):
    """Extracts a specific line number from an entry."""

    def __init__(self, line: int, prefix_length: int, option_suffix: Text = "") -> None:
        """Create a new instance.

        Args:
            line:
                the line to extract, counting from zero
            prefix_length:
                Amount of characters to skip at the beginning of the line
            option_suffix:
                Suffix for each configuration option
        """
        super().__init__(prefix_length, option_suffix)
        self._line = line

    def configure(self, config: configparser.SectionProxy) -> None:
        """See base class method."""
        super().configure(config)
        self._line = config.getint(f"line{self._option_suffix}", fallback=self._line)

    def _get_raw(
        self, entry_name: Text, entry_lines: Sequence[Text]  # noqa: ARG002
    ) -> Optional[Text]:
        if len(entry_lines) > self._line:
            return entry_lines[self._line]
        else:
            return None


class RegexSearchExtractor(DataExtractor):
    """Extracts data using a regular expression with capture group."""

    def __init__(self, regex: str, option_suffix: str) -> None:
        """Create a new instance.

        Args:
            regex:
                The regular expression describing the entry line to match. The
                first matching line is selected. The expression must contain a
                single capture group that contains the data to return.
            option_suffix:
                Suffix for each configuration option
        """
        super().__init__(option_suffix)
        self._regex = self._build_matcher(regex)

    def _build_matcher(self, regex: str) -> Pattern:
        matcher = re.compile(regex)
        if matcher.groups != 1:
            raise ValueError(
                f'Provided regex "{regex}" must contain a single '
                "capture group for the value to return."
            )
        return matcher

    def configure(self, config: configparser.SectionProxy) -> None:
        """See base class method."""
        self._regex = self._build_matcher(
            config.get(
                f"regex{self._option_suffix}",
                fallback=self._regex.pattern,
            )
        )

    def get_value(
        self, entry_name: Text, entry_lines: Sequence[Text]  # noqa: ARG002
    ) -> Optional[Text]:
        """See base class method."""
        # Search through all lines and return the first matching one
        for line in entry_lines:
            match = self._regex.match(line)
            if match:
                return match.group(1)
        # nothing matched
        return None


class EntryNameExtractor(DataExtractor):
    """Return the last path fragment of the pass entry as the desired value."""

    def configure(self, config: configparser.SectionProxy) -> None:
        """Configure nothing."""

    def get_value(
        self, entry_name: Text, entry_lines: Sequence[Text]  # noqa: ARG002
    ) -> Optional[Text]:
        """See base class method."""
        return os.path.split(entry_name)[1]


class StaticUsernameExtractor(DataExtractor):
    """Extract username from a static field in the mapping configuration."""

    def __init__(self) -> None:
        self._username: str | None = None

    def configure(self, config: configparser.SectionProxy) -> None:
        """Store the username from the mapping configuration."""
        self._username = config.get("username")

    def get_value(
        self, entry_name: Text, entry_lines: Sequence[Text]  # noqa: ARG002
    ) -> Optional[Text]:
        """Return the stored username."""
        return self._username


class ExtractorContainer:
    """Contains predefined username and password extractors required by ``get_password()``."""

    _line_extractor_name: str = "specific_line"
    _password_extractors: dict[str, DataExtractor]
    _username_extractors: dict[str, DataExtractor]

    def __init__(self) -> None:
        self._password_extractors = {
            self._line_extractor_name: SpecificLineExtractor(
                0, 0, option_suffix="_password"
            ),
            "regex_search": RegexSearchExtractor(
                r"^password: +(.*)$", option_suffix="_password"
            ),
        }
        self._username_extractors = {
            self._line_extractor_name: SpecificLineExtractor(
                1, 0, option_suffix="_username"
            ),
            "regex_search": RegexSearchExtractor(
                r"^username: +(.*)$", option_suffix="_username"
            ),
            "entry_name": EntryNameExtractor(option_suffix="_username"),
            "static": StaticUsernameExtractor(),
        }

    def password_extractor(self, name: str | None = None) -> DataExtractor | None:
        """Provides access to the predefined password extractors.

        Args:
            name:
                Name of the password extractor or None for the default password
                extractor.

        Returns:
            Password extractor for the given ``name`` or None if a matching extractor
            was not found.
        """
        return (
            self._password_extractors.get(name)
            if name is not None
            else self._password_extractors[self._line_extractor_name]
        )

    def username_extractor(self, name: str | None = None) -> DataExtractor | None:
        """Provides access to the predefined username extractors.

        Args:
            name:
                Name of the username extractor or None for the default username
                extractor.

        Returns:
            Username extractor for the given ``name`` or None if a matching extractor
            was not found.
        """
        return (
            self._username_extractors.get(name)
            if name is not None
            else self._username_extractors[self._line_extractor_name]
        )


def split_section_into_host_and_path(pattern: str) -> tuple[str, str]:
    """Split a mapping section name into its host and path patterns.

    Args:
        pattern:
            The section name from the mapping file.

    Returns:
        A tuple (host_pattern, path_pattern). ``path_pattern`` is empty if the
        section does not restrict the path.
    """
    host_pattern, _, path_pattern = pattern.partition("/")
    return host_pattern, path_pattern


def match_host_pattern(host_pattern: str, host: str) -> bool:
    """Match a host against the host part of a mapping section name.

    Matching is per DNS label, i.e. per dot-separated component of the host
    name: the pattern and the host are split on ``.`` and the components are
    matched pairwise, so a pattern only ever matches a host with the same number
    of labels. Wildcards are thereby confined to the label they appear in and a
    pattern cannot extend into a neighbouring domain: ``github.com*`` matches
    ``github.community`` but not ``github.com.evil.com``, which would otherwise
    hand the credentials for one host to an entirely different one.

    Within a single label, ordinary ``fnmatch`` syntax applies, character
    classes included. A bare ``*`` is a catch-all matching any host regardless
    of how many labels it has.

    Args:
        host_pattern:
            The host part of a mapping section name.
        host:
            The host from the credential request.

    Returns:
        Whether the pattern matches the host.
    """
    # A host from git never contains a path separator, but no pattern must be
    # able to match one if it ever did, the catch-all included.
    if "/" in host:
        return False

    if host_pattern == "*":
        return True

    pattern_labels = host_pattern.split(".")
    host_labels = host.split(".")
    if len(pattern_labels) != len(host_labels):
        return False

    return all(
        fnmatch.fnmatch(host_label, pattern_label)
        for pattern_label, host_label in zip(pattern_labels, host_labels)
    )


def is_unbounded_host_pattern(host_pattern: str) -> bool:
    """Whether a host pattern matches hosts the user never explicitly named.

    Such a pattern matches hosts an attacker controls just as readily as the
    user's own, so it must not resolve to a fixed password store entry.

    The test is whether anything is left of the pattern once the wildcards and
    the label separator are removed: what remains is the literal host content
    the user actually committed to. ``.`` is removed along with the wildcards
    because it only delimits labels and names no host by itself, so ``*.*``
    names no host any more than ``*`` does. This deliberately is not a
    comparison against ``*``: matching is per label, but ``*.*`` still matches
    every two-label host.

    Args:
        host_pattern:
            The host part of a mapping section name.

    Returns:
        Whether the pattern matches hosts indiscriminately.
    """
    return not host_pattern.strip("*?.")


def ensure_protocol_is_secure(
    section: configparser.SectionProxy, request: Mapping[str, str]
) -> None:
    """Refuse to serve a request over a protocol that transmits in the clear.

    git sends the credentials this helper returns over whatever protocol the
    request names, so answering an ``http`` request means handing the password
    to the network. A remote can reach this through a plain ``http`` clone URL
    or a redirect, which is why it is refused rather than merely warned about.

    Args:
        section:
            The matched mapping section, which may opt out via
            ``allow_insecure_protocol``.
        request:
            The credential request.

    Raises:
        ValueError
            when the protocol is not https and the section does not allow it.
    """
    protocol = request.get("protocol")
    if protocol == "https":
        return
    if section.getboolean("allow_insecure_protocol", fallback=False):
        LOGGER.warning(
            "Serving credentials over insecure protocol '%s' as requested via "
            "allow_insecure_protocol",
            protocol,
        )
        return

    described = (
        f"insecure protocol '{protocol}'"
        if protocol
        else "a request without a protocol"
    )
    raise ValueError(
        f"Refusing to provide credentials for {described}, which would risk "
        "transmitting them in clear text without encryption. Set "
        "allow_insecure_protocol=true for this mapping section if that is "
        "really intended."
    )


def ensure_target_is_host_specific(section_name: str, target: str) -> None:
    """Reject a catch-all section whose target does not depend on the host.

    A section matching any host, paired with a target that is the same for every
    host, hands one fixed credential to whatever host a remote can steer git
    towards. Requiring ``${host}`` in the target keeps such a section
    self-limiting: an unknown host resolves to an entry that does not exist, so
    nothing is decrypted and nothing is returned.

    Args:
        section_name:
            Name of the matched mapping section.
        target:
            The raw ``target`` value of that section, before substitution, so
            that a target which merely happens to contain the host's text does
            not satisfy the requirement.

    Raises:
        ValueError
            when the section matches any host but its target does not use
            ``${host}``.
    """
    host_pattern, _ = split_section_into_host_and_path(section_name)
    if is_unbounded_host_pattern(host_pattern) and "${host}" not in target:
        raise ValueError(
            f"Mapping section '{section_name}' matches any host, but its target "
            f"'{target}' is the same for every host. That combination would hand "
            "the credentials in that one entry to whatever host git is asked to "
            "authenticate against, including a host an attacker controls, so it "
            "is refused. Either add ${host} to the target, which makes the "
            "section resolve to a different entry per host, or replace the "
            "section with ones naming the hosts it should serve."
        )


def match_section_pattern(pattern: str, host: str, path: Optional[str]) -> bool:
    """Match a credential request against a mapping section name.

    Args:
        pattern:
            The section name from the mapping file.
        host:
            The host from the credential request.
        path:
            The path from the credential request, or ``None`` if the request
            carries none (i.e. ``credential.useHttpPath`` is not enabled).

    Returns:
        Whether the section applies to the request.
    """
    host_pattern, path_pattern = split_section_into_host_and_path(pattern)

    if not match_host_pattern(host_pattern, host):
        return False

    if not path_pattern:
        # The section does not restrict the path, so it applies to every path
        # on a matching host.
        return True

    if path is None:
        LOGGER.debug(
            'Section "%s" restricts the path, but the request carries none. '
            "Enable credential.useHttpPath in git to match on paths.",
            pattern,
        )
        return False

    # Inside the path, a wildcard crossing "/" is intended.
    return fnmatch.fnmatch(path, path_pattern)


def find_mapping_section(
    mapping: configparser.ConfigParser, host: str, path: Optional[str]
) -> configparser.SectionProxy:
    """Select the mapping entry matching the request host and path."""
    LOGGER.debug('Searching mapping to match against host "%s", path "%s"', host, path)
    for section in mapping.sections():
        if match_section_pattern(section, host, path):
            LOGGER.debug(
                'Section "%s" matches requested host "%s", path "%s"',
                section,
                host,
                path,
            )
            return mapping[section]

    raise ValueError(
        f"No mapping section in {mapping.sections()} matches request "
        f"{get_request_header(host, path)}"
    )


def get_request_host_and_path(
    request: Mapping[str, str],
) -> tuple[str, Optional[str]]:
    """Return the host and optional path of a credential request.

    Raises:
        ValueError
            when the request carries no host.
    """
    if "host" not in request:
        LOGGER.error("host= entry missing in request. Cannot query without a host")
        raise ValueError("Request lacks host entry")

    return request["host"], request.get("path")


def get_request_header(host: str, path: Optional[str]) -> str:
    """Return a human readable "host/path" for log and error messages."""
    return "/".join([host, path]) if path is not None else host


def split_path_segments(value: str) -> list[str]:
    r"""Split a value on both path separators.

    ``\\`` is treated as a separator alongside ``/`` so that a Windows-style
    path cannot slip a ``..`` past the checks below on a platform where the
    filesystem would later honour it.
    """
    return re.split(r"[/\\]", value)


def ensure_request_value_is_target_safe(
    variable: str, value: str, allow_separators: bool
) -> None:
    """Ensure a request value is safe to substitute into a pass target.

    The values substituted into the ``target`` of a mapping section come from
    the credential request and are therefore influenced by the remote git talks
    to. They name the password store entry that is about to be decrypted, so
    they must not be able to change the shape of that name: a value carrying
    path separators or ``..`` segments could otherwise address an entry the
    mapping never intended, including one outside the password store.

    Args:
        variable:
            Name of the variable being substituted, for error messages.
        value:
            The request value to check.
        allow_separators:
            Whether path separators are a legitimate part of this value. Only
            true for ``${path}``.

    Raises:
        ValueError
            when the value is empty or contains something that must not end up
            in a pass target.
    """
    if not value:
        # An empty value collapses the target onto a directory, and `pass show`
        # prints a tree of entry names for a directory instead of failing.
        raise ValueError(f"Request value for ${{{variable}}} is empty")
    if value.translate(CONTROL_CHARACTERS) != value:
        raise ValueError(
            f"Request value for ${{{variable}}} contains a control character"
        )
    if not allow_separators and len(split_path_segments(value)) > 1:
        raise ValueError(f"Request value for ${{{variable}}} contains a path separator")
    if ".." in split_path_segments(value):
        raise ValueError(
            f"Request value for ${{{variable}}} contains a '..' path segment"
        )


def is_absolute_target(target: str) -> str | None:
    """Return why a pass target is absolute, or ``None`` if it is relative.

    An absolute target would escape the password store, and joining one onto the
    store directory discards the store entirely. Both separators and a Windows
    drive letter are considered, so that the check does not depend on the
    platform the helper happens to run on.
    """
    if target.startswith(("/", "\\")):
        return "starts with a path separator"
    if re.match(r"^[A-Za-z]:", target):
        return "starts with a drive letter"
    return None


def define_pass_target(
    section: configparser.SectionProxy, request: Mapping[str, str]
) -> str:
    """Determine the pass target by filling in potentially used variables.

    Raises:
        ValueError
            when a request value that the target substitutes is unsafe to use
            in a password store entry name, or when the resulting target would
            point outside of the password store.
    """
    target = section["target"]

    # Only validate what is actually substituted, so that a request value which
    # is never used cannot make an otherwise fine lookup fail.
    for variable, allow_separators in (
        ("host", False),
        ("path", True),
        ("username", False),
        ("protocol", False),
    ):
        placeholder = f"${{{variable}}}"
        if placeholder not in target or variable not in request:
            continue
        ensure_request_value_is_target_safe(
            variable, request[variable], allow_separators
        )
        target = target.replace(placeholder, request[variable])

    absolute_reason = is_absolute_target(target)
    if absolute_reason is not None:
        raise ValueError(f"Pass target '{target}' {absolute_reason}")

    return target


def compute_pass_environment(
    section: configparser.SectionProxy,
) -> tuple[dict[str, str], Path]:
    """Returns the environment variables needed to start the ``pass`` subprocess.

    The main task of this function is to determine the password store directory
    to be used by ``pass``. It does this by:

    1. using the value of ``password_store_dir`` in ``section`` (if defined and
       non-empty),
    2. using the value of the ``PASSWORD_STORE_DIR`` environment variable (if
       defined and non-empty),
    3. falling back to the default: ``~/password-store``.

    In the next step, a leading ``~`` (tilde) in the resulting path gets
    replaced by the users ``$HOME`` (on Windows: ``%USERPROFILE%``) directory.
    See ``os.path.expanduser()`` for more details.

    Finally, the result is used to add or update ``PASSWORD_STORE_DIR`` to/in a
    copy of the current process environment.

    Args:
        section:
            Ini file section which applies to the current password target.

    Returns:
        A tuple (env, dir) where ``env`` is a dictionary comprising a copy of
        the current process environment wth updated/added ``PASSWORD_STORE_DIR``
        value and ``dir`` is the value of ``PASSWORD_STORE_DIR`` as a ``Path``
        instance (for the callers convenience).

    """
    environment = os.environ.copy()
    password_store_dir = Path(
        section.get("password_store_dir")
        or environment.get("PASSWORD_STORE_DIR")
        or "~/.password-store"
    ).expanduser()
    LOGGER.debug('Setting PASSWORD_STORE_DIR to "%s"', password_store_dir)
    environment["PASSWORD_STORE_DIR"] = str(password_store_dir)
    return environment, password_store_dir


def ensure_password_is_file(password_store_dir: Path, pass_target: str) -> None:
    """Check that the password file exists and that it is a file (or symlink).

    There is no return value, when a problem is detected, the function raises an
    exception.

    Args:
        password_store_dir:
            Password store directory which contains the password file to be checked.
        pass_target:
            Path to the actual password file within ``password_store_dir``
            (without ``.gpg`` extension).

    Raises:
        ValueError
            when the password file is outside of ``password_store_dir``, does
            not exist, or is not a file (e.g. if it is a directory).

    """
    pass_target_file = Path(password_store_dir / f"{pass_target}.gpg")

    # Confine the lookup to the password store. Note that joining an absolute
    # pass_target would otherwise discard password_store_dir entirely.
    store = password_store_dir.expanduser().resolve()
    resolved = pass_target_file.expanduser().resolve()
    if not resolved.is_relative_to(store):
        raise ValueError(f"'{pass_target}' is outside of the password store")

    # A single message for every remaining failure: distinguishing "does not
    # exist" from "is not a file" would let a remote use this as an oracle for
    # the existence of arbitrary files.
    # TODO: Add `follow_symlinks=True` to the is_file call after removing support for
    # python < 3.13.
    if not resolved.is_file():
        raise ValueError(f"'{pass_target_file}' is not a usable password store entry")


def get_password(
    request: Mapping[str, str],
    mapping: configparser.ConfigParser,
    extractors: ExtractorContainer,
    skip_fs_checks: bool,
) -> None:
    """Resolve the given credential request in the provided mapping definition.

    The result is printed automatically.

    Args:
        request:
            The credential request specified as a dict of key-value pairs.
        mapping:
            The mapping configuration as a ConfigParser instance.
        extractors:
            The predefined password and username extractors.
        skip_fs_checks:
            Skip filesystem level checks for the presence of password store
            (.gpg) files.
    """
    host, path = get_request_host_and_path(request)
    section = find_mapping_section(mapping, host, path)
    LOGGER.debug("Found mapping section:\n%s", dict(section))

    ensure_protocol_is_secure(section, request)
    ensure_target_is_host_specific(section.name, section["target"])

    pass_target = define_pass_target(section, request)

    password_extractor_name = section.get("password_extractor")
    if password_extractor_name == "":
        LOGGER.warning(
            "Mapping file contains empty 'password_extractor', please check!"
        )
        LOGGER.warning(
            "Fallback to default password extractor: use first line of password file"
        )
    password_extractor = extractors.password_extractor(
        password_extractor_name if password_extractor_name else None
    )

    if password_extractor is None:
        raise ValueError(
            f"A password_extractor of type '{password_extractor_name}' does not exist"
        )
    password_extractor.configure(section)
    LOGGER.debug('Password extractor: "%s"', type(password_extractor))

    username_extractor_name: str | None = section.get("username_extractor")
    username_extractor = extractors.username_extractor(username_extractor_name)
    if username_extractor is None:
        raise ValueError(
            f"A username_extractor of type '{username_extractor_name}' does not exist"
        )
    username_extractor.configure(section)
    LOGGER.debug('Username extractor: "%s"', type(username_extractor))

    environment, password_store_dir = compute_pass_environment(section)
    if skip_fs_checks:
        LOGGER.debug("Filesystem level checks for password store files are disabled")
    else:
        ensure_password_is_file(password_store_dir, pass_target)

    LOGGER.debug('Requesting entry "%s" from pass', pass_target)
    # silence the subprocess injection warnings as it is the user's
    # responsibility to provide a safe mapping and execution environment.
    # "--" terminates pass' option list so that a target starting with a dash
    # can never be interpreted as an option (e.g. "-c1" acting as --clip=1).
    output = subprocess.check_output(
        ["pass", "show", "--", pass_target], env=environment
    ).decode(section.get("encoding", "UTF-8"))
    lines = output.splitlines()
    # Never log the entry contents or the extracted values: git captures the
    # stderr of credential helpers, so under GIT_TRACE or in CI this would end
    # up in persistent and often shared logs.
    LOGGER.debug("Password store entry has %d line(s)", len(lines))

    password = password_extractor.get_value(pass_target, lines)
    username = username_extractor.get_value(pass_target, lines)
    LOGGER.debug(
        "Extraction results: password found: %s, username found: %s",
        password is not None,
        username is not None,
    )
    if password:
        print(f"password={password}")  # noqa: T201
    if "username" not in request and username:
        print(f"username={username}")  # noqa: T201


def handle_skip() -> None:
    """Terminate the process if skipping is requested via an env variable."""
    if "PASS_GIT_HELPER_SKIP" in os.environ:
        LOGGER.info("Skipping processing as requested via environment variable")
        sys.exit(6)


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Start the pass-git-helper script.

    Args:
        argv:
            If not ``None``, use the provided command line arguments for
            parsing. Otherwise, extract them automatically.
    """
    args = parse_arguments(argv=argv)

    if args.logging:
        logging.basicConfig(level=logging.DEBUG)

    handle_skip()

    action = args.action
    if action != "get":
        LOGGER.info("Action '%s' is currently not supported", action)
        sys.exit(5)

    request = parse_request()
    LOGGER.debug("Received action '%s' with request:\n%s", action, request)

    try:
        mapping = parse_mapping(args.mapping)
    except Exception as error:  # ok'ish for the main function
        LOGGER.critical("Unable to parse mapping file", exc_info=True)
        print(f"Unable to parse mapping file: {error}", file=sys.stderr)  # noqa: T201
        sys.exit(4)

    try:
        get_password(request, mapping, ExtractorContainer(), args.skip_fs_checks)
    except Exception as error:  # ok'ish for the main function
        print(  # noqa: T201
            f'Unable to retrieve entry: "{type(error).__name__}: {error}"',
            file=sys.stderr,
        )
        sys.exit(3)  # 1: uncaught exceptions, 2: already used by argparse


if __name__ == "__main__":
    main()
