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
from pathlib import Path
import re
import subprocess
import sys
from typing import IO, Mapping, Optional, Pattern, Sequence, Text

import xdg.BaseDirectory


__version__ = "4.0.0"

LOGGER = logging.getLogger()
CONFIG_FILE_NAME = "git-pass-mapping.ini"
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
    LOGGER.debug('Received request "%s"', in_lines)

    request = {}
    for line in in_lines:
        # skip empty lines to be a bit resilient against protocol errors
        if not line.strip():
            continue

        parts = line.split("=", 1)
        assert len(parts) == 2
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


_line_extractor_name = "specific_line"
_username_extractors = {
    _line_extractor_name: SpecificLineExtractor(1, 0, option_suffix="_username"),
    "regex_search": RegexSearchExtractor(
        r"^username: +(.*)$", option_suffix="_username"
    ),
    "entry_name": EntryNameExtractor(option_suffix="_username"),
    "static": StaticUsernameExtractor(),
}
_password_extractors = {
    _line_extractor_name: SpecificLineExtractor(0, 0, option_suffix="_password"),
    "regex_search": RegexSearchExtractor(
        r"^password: +(.*)$", option_suffix="_password"
    ),
}


def find_mapping_section(
    mapping: configparser.ConfigParser, request_header: str
) -> configparser.SectionProxy:
    """Select the mapping entry matching the request header."""
    LOGGER.debug('Searching mapping to match against header "%s"', request_header)
    for section in mapping.sections():
        if fnmatch.fnmatch(request_header, section):
            LOGGER.debug(
                'Section "%s" matches requested header "%s"', section, request_header
            )
            return mapping[section]

    raise ValueError(
        f"No mapping section in {mapping.sections()} matches request {request_header}"
    )


def get_request_section_header(request: Mapping[str, str]) -> str:
    """Return the canonical host + optional path for section header matching."""
    if "host" not in request:
        LOGGER.error("host= entry missing in request. Cannot query without a host")
        raise ValueError("Request lacks host entry")

    host = request["host"]
    if "path" in request:
        host = "/".join([host, request["path"]])
    return host


def define_pass_target(
    section: configparser.SectionProxy, request: Mapping[str, str]
) -> str:
    """Determine the pass target by filling in potentially used variables."""
    pass_target = section["target"].replace("${host}", request["host"])

    if "path" in request:
        pass_target = pass_target.replace("${path}", request["path"])
    if "username" in request:
        pass_target = pass_target.replace("${username}", request["username"])
    if "protocol" in request:
        pass_target = pass_target.replace("${protocol}", request["protocol"])
    return pass_target


def compute_pass_environment(section: configparser.SectionProxy) -> Mapping[str, str]:
    environment = os.environ.copy()
    password_store_dir = section.get("password_store_dir")
    if password_store_dir:
        LOGGER.debug('Setting PASSWORD_STORE_DIR to "%s"', password_store_dir)
        environment["PASSWORD_STORE_DIR"] = password_store_dir
    return environment


def get_password(
    request: Mapping[str, str], mapping: configparser.ConfigParser
) -> None:
    """Resolve the given credential request in the provided mapping definition.

    The result is printed automatically.

    Args:
        request:
            The credential request specified as a dict of key-value pairs.
        mapping:
            The mapping configuration as a ConfigParser instance.
    """
    LOGGER.debug('Received request "%s"', request)

    header = get_request_section_header(request)
    section = find_mapping_section(mapping, header)

    pass_target = define_pass_target(section, request)

    password_extractor_name: str = section.get("password_extractor")  # type: ignore

    if password_extractor_name:
        password_extractor = _password_extractors.get(password_extractor_name)
    else:
        password_extractor = SpecificLineExtractor(0, 0, option_suffix="_password")

    if password_extractor is None:
        raise ValueError(
            f"A password_extractor of type '{password_extractor_name}' does not exist"
        )
    password_extractor.configure(section)

    username_extractor_name: str = section.get(
        "username_extractor", fallback=_line_extractor_name
    )
    username_extractor = _username_extractors.get(username_extractor_name)
    if username_extractor is None:
        raise ValueError(
            f"A username_extractor of type '{username_extractor_name}' does not exist"
        )
    username_extractor.configure(section)

    environment = compute_pass_environment(section)

    LOGGER.debug('Requesting entry "%s" from pass', pass_target)
    # silence the subprocess injection warnings as it is the user's
    # responsibility to provide a safe mapping and execution environment
    output = subprocess.check_output(
        ["pass", "show", pass_target], env=environment
    ).decode(section.get("encoding", "UTF-8"))
    lines = output.splitlines()

    password = password_extractor.get_value(pass_target, lines)
    username = username_extractor.get_value(pass_target, lines)
    if password:
        print(f"password={password}")  # noqa: T201
    if "username" not in request and username:
        print(f"username={username}")  # noqa: T201


def handle_skip() -> None:
    """Terminate the process if skipping is requested via an env variable."""
    if "PASS_GIT_HELPER_SKIP" in os.environ:
        LOGGER.info("Skipping processing as requested via environment variable")
        sys.exit(1)


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
    request = parse_request()
    LOGGER.debug("Received action %s with request:\n%s", action, request)

    try:
        mapping = parse_mapping(args.mapping)
    except Exception as error:  # ok'ish for the main function
        LOGGER.critical("Unable to parse mapping file", exc_info=True)
        print(f"Unable to parse mapping file: {error}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    if action == "get":
        try:
            get_password(request, mapping)
        except Exception as error:  # ok'ish for the main function
            print(f"Unable to retrieve entry: {error}", file=sys.stderr)  # noqa: T201
            sys.exit(1)
    else:
        LOGGER.info("Action %s is currently not supported", action)
        sys.exit(1)


if __name__ == "__main__":
    main()
