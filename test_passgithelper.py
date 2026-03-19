import configparser
import logging
import os
from collections.abc import Generator, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError
from typing import Any, Optional, Text, TypeAlias
from unittest.mock import ANY, MagicMock, call

import pytest
from pytest_mock import MockerFixture

import passgithelper

CapsysType: TypeAlias = Generator[pytest.CaptureFixture[str]]


@dataclass
class HelperConfig:
    xdg_dir: str | None = None
    request: str = ""
    entry_data: bytes | None = None
    entry_name: str | None = None
    mock_co_expected_calls: int = 2
    # The ``out_expected``/``err_expected fields`` can be automatically checked
    # by the ``teardown_helper_capsys_checks(..)`` function.
    out_expected: str = ""
    err_expected: str = ""

    def get_host(self) -> Optional[str]:
        if not self.request:
            return None
        for line in self.request.splitlines():
            param = line.split("=")
            if len(param) > 1 and param[0] == "host":
                return param[1]
        return None

    def get_pass_target(self) -> Optional[str]:
        return self.entry_name


@dataclass
class HelperConfigAndMock:
    """Generated value of the ``helper_config`` fixture.

    Attributes:
        test_params: Test parametrization data.
        mock_co: Mock for subprocess.check_output as used in the
            passgithelber.pass_get_entry(..) function.
    """

    test_params: HelperConfig
    mock_co: MagicMock


def setup_helper_load_first_config_and_request(
    mocker: MockerFixture, test_params: HelperConfig
) -> None:
    """Use values from ``test_params`` to setup ``load_first_config()`` and ``readlines()`` mocks.

    Intended to be called from a test fixture or directly from a test.

    """
    _ = mocker.patch(
        "xdg.BaseDirectory.load_first_config", return_value=test_params.xdg_dir
    )

    _ = mocker.patch(
        "sys.stdin.readlines",
        return_value=test_params.request.splitlines(keepends=True),
    )


def setup_helper_parse_request_mock(
    mocker: MockerFixture,
    test_params: HelperConfig,
    use_wildcard_section: bool = False,
    **kwargs: str,
) -> None:
    """Create simple mapping/config from ``test_params`` and use it to mock ``parse_mapping``.

    The mapping gets created as follows:

    1. The ``host`` request parameter gets used to create a section. If
       ``use_wildcard_section`` is True, a ``*`` gets appended to ``host``
       before adding the section.

    2. As a next step, if ``test_params.get_pass_target()`` returns a non-empty
       string, it gets added as ``target`` to the ``host`` section.

    3. Any ``kwargs`` also get added to the ``host`` section.

    The resulting ini/mapping object is then used as return value of the
    ``passgithelper.parse_mapping`` function using a mock object.

    Intended to be called from a test fixture or directly from a test.

    """
    # git host to use as section in created config
    host = test_params.get_host()
    if not host:
        return
    else:
        host = f"{host}*" if use_wildcard_section else host
    pass_target = test_params.get_pass_target()

    # create ini (mapping)
    ini = configparser.ConfigParser()
    ini.add_section(host)
    section = ini[host]
    if pass_target:
        section.update(target=pass_target)
    section.update(kwargs)

    # mock parse_request
    _ = mocker.patch("passgithelper.parse_mapping", return_value=ini)


def teardown_helper_capsys_checks(
    capsys: CapsysType, test_params: HelperConfig
) -> None:
    """Teardown helper which checks stdout/stderr dependening on ``test_params``.

    Intended to be called from a test fixture or directly from a test.

    """
    out, err = capsys.readouterr()

    if test_params.out_expected:
        assert test_params.out_expected in out
    else:
        assert not out

    if test_params.err_expected:
        assert test_params.err_expected in err
    else:
        assert not err


@pytest.fixture
def helper_config(
    mocker: MockerFixture, request: pytest.FixtureRequest
) -> Generator[HelperConfigAndMock]:
    test_params: HelperConfig = request.param

    setup_helper_load_first_config_and_request(mocker, test_params)

    mock_co: MagicMock = mocker.patch("subprocess.check_output")
    if test_params.entry_data:
        mock_co.side_effect = [
            test_params.entry_data,
            CalledProcessError(
                returncode=1, cmd=["pass", "show", test_params.entry_name]
            ),
        ]
    else:
        mock_co.side_effect = CalledProcessError(
            returncode=1, cmd=["pass", "show", test_params.entry_name]
        )
        test_params.mock_co_expected_calls = 1

    yield HelperConfigAndMock(test_params, mock_co)

    if test_params.entry_name is not None:
        assert mock_co.call_count == test_params.mock_co_expected_calls
        calls = [call(["pass", "show", test_params.entry_name], env=ANY)]
        if test_params.mock_co_expected_calls > 1:
            calls.append(call(["pass", "show", f"{test_params.entry_name}/"], env=ANY))
        mock_co.assert_has_calls(calls)


def test_handle_skip_nothing(monkeypatch: Any) -> None:
    monkeypatch.delenv("PASS_GIT_HELPER_SKIP", raising=False)
    passgithelper.handle_skip()
    # should do nothing normally


def test_handle_skip_exits(monkeypatch: Any) -> None:
    monkeypatch.setenv("PASS_GIT_HELPER_SKIP", "1")
    with pytest.raises(SystemExit, match=r"^6$"):
        passgithelper.handle_skip()


def test_mapping_option_with_non_existing_file(capsys: CapsysType) -> None:
    """Test handling of ``--mapping`` option with a non-existing file name."""
    nonexisting_ini_file = "___this_file_d0es_not_exist___.notIn1"
    err_expected = f"No such file or directory: '{nonexisting_ini_file}'"
    with pytest.raises(SystemExit, match=r"^2$"):
        passgithelper.main(["--mapping", nonexisting_ini_file])

    out, err = capsys.readouterr()
    assert not out
    assert err_expected in err


def test_pass_get_entry_with_invalid_pass_target() -> None:
    """A valid pass target shall never end with a '/'."""
    pass_target = "git/github.com/"
    with pytest.raises(ValueError, match=f"'{pass_target}' is not a valid pass-name"):
        passgithelper.pass_get_entry(pass_target, {})


class TestPasswordStoreDirSelection:
    """Test password store directory selection.

    The password store directory used by ``pass`` can either be set via the
    ``PASSWORD_STORE_DIR`` environment variable or via the
    ``password_store_dir`` option in the ini file. In case both are present, the
    ini file option overrides the environment variable.
    """

    def test_ini_file_option_overrides_environment(self, monkeypatch: Any) -> None:
        ini = configparser.ConfigParser()

        expected = "/tmp/password-store-from-ini"
        # `PASSWORD_STORE_DIR` in environemnt, `password_store_dir` in ini file
        # section -> value from ini file overrides env. variable
        monkeypatch.setenv("PASSWORD_STORE_DIR", "/tmp/password-store-from-env")
        ini["example.com"] = {"password_store_dir": expected}
        env = passgithelper.compute_pass_environment(ini["example.com"])
        assert env.get("PASSWORD_STORE_DIR") == expected


class TestSkippingDataExtractor:
    class ExtractorImplementation(passgithelper.SkippingDataExtractor):
        def configure(self, config: configparser.SectionProxy) -> None:
            pass

        def __init__(self, skip_characters: int = 0) -> None:
            super().__init__(skip_characters)

        def _get_raw(
            self, entry_text: Text, entry_lines: Sequence[Text]  # noqa: ARG002
        ) -> Optional[Text]:
            return entry_lines[0]

    def test_smoke(self) -> None:
        extractor = self.ExtractorImplementation(4)
        assert extractor.get_value("foo", ["testthis"]) == "this"

    def test_too_short(self) -> None:
        extractor = self.ExtractorImplementation(8)
        assert extractor.get_value("foo", ["testthis"]) == ""
        extractor = self.ExtractorImplementation(10)
        assert extractor.get_value("foo", ["testthis"]) == ""


class TestSpecificLineExtractor:
    def test_smoke(self) -> None:
        extractor = passgithelper.SpecificLineExtractor(1, 6)
        assert (
            extractor.get_value("foo", ["line 1", "user: bar", "more lines"]) == "bar"
        )

    def test_no_such_line(self) -> None:
        extractor = passgithelper.SpecificLineExtractor(3, 6)
        assert extractor.get_value("foo", ["line 1", "user: bar", "more lines"]) is None


class TestRegexSearchExtractor:
    def test_smoke(self) -> None:
        extractor = passgithelper.RegexSearchExtractor("^username: (.*)$", "")
        assert (
            extractor.get_value(
                "foo",
                [
                    "thepassword",
                    "somethingelse",
                    "username: user",
                    "username: second ignored",
                ],
            )
            == "user"
        )

    def test_missing_group(self) -> None:
        with pytest.raises(ValueError, match="must contain"):
            passgithelper.RegexSearchExtractor("^username: .*$", "")

    def test_configuration(self) -> None:
        extractor = passgithelper.RegexSearchExtractor("^username: (.*)$", "_username")
        config = configparser.ConfigParser()
        config.read_string(r"""[test]
regex_username=^foo: (.*)$""")
        extractor.configure(config["test"])
        assert extractor._regex.pattern == r"^foo: (.*)$"

    def test_configuration_checks_groups(self) -> None:
        extractor = passgithelper.RegexSearchExtractor("^username: (.*)$", "_username")
        config = configparser.ConfigParser()
        config.read_string(r"""[test]
regex_username=^foo: .*$""")
        with pytest.raises(ValueError, match="must contain"):
            extractor.configure(config["test"])


class TestEntryNameExtractor:
    def test_smoke(self) -> None:
        assert passgithelper.EntryNameExtractor().get_value("foo/bar", []) == "bar"


class TestStaticUsernameExtractor:
    def test_extracts_username_from_config(self) -> None:
        config = configparser.ConfigParser()
        config.read_string("""[test]
username = address@example.com
""")

        extractor = passgithelper.StaticUsernameExtractor()
        extractor.configure(config["test"])

        assert (
            extractor.get_value("any/entry", ["any", "lines"]) == "address@example.com"
        )

    def test_returns_none_when_no_username_configured(self) -> None:
        config = configparser.ConfigParser()
        config.read_string("""[test]
target = some/target
""")

        extractor = passgithelper.StaticUsernameExtractor()
        extractor.configure(config["test"])

        assert extractor.get_value("any/entry", ["any", "lines"]) is None

    def test_inherits_from_default_section(self) -> None:
        config = configparser.ConfigParser()
        config.read_string("""[DEFAULT]
username = default@example.com

[test]
target = some/target
""")

        extractor = passgithelper.StaticUsernameExtractor()
        extractor.configure(config["test"])

        assert (
            extractor.get_value("any/entry", ["any", "lines"]) == "default@example.com"
        )

    def test_section_overrides_default(self) -> None:
        config = configparser.ConfigParser()
        config.read_string("""[DEFAULT]
username = default@example.com

[test]
target = some/target
username = override@example.com
""")

        extractor = passgithelper.StaticUsernameExtractor()
        extractor.configure(config["test"])

        assert (
            extractor.get_value("any/entry", ["any", "lines"]) == "override@example.com"
        )


@pytest.mark.parametrize(
    "helper_config",
    [
        HelperConfig(
            None,
            "",
            b"ignored",
        ),
    ],
    indirect=True,
)
@pytest.mark.usefixtures("helper_config")
def test_parse_mapping_file_missing() -> None:
    with pytest.raises(
        RuntimeError, match=r"No mapping configured so far at any XDG config location."
    ):
        passgithelper.parse_mapping(None)


@pytest.mark.parametrize(
    "helper_config",
    [
        HelperConfig(
            "test_data/smoke",
            "",
            b"ignored",
        ),
    ],
    indirect=True,
)
@pytest.mark.usefixtures("helper_config")
def test_parse_mapping_from_xdg() -> None:
    config = passgithelper.parse_mapping(None)
    assert "mytest.com" in config
    assert config["mytest.com"]["target"] == "dev/mytest"


# Parameters for tests related to the ``pass_git_entry(..)`` function
test_params_pass_get_entry = [
    # 1. pass_target pointing to non-existing entry
    HelperConfig(
        entry_name="git/gitlab.com/doesnotexist",
        # skip entry_data to properly configure the subprocess.check_output mock in the
        # helper_config fixture
        request="\nprotocol=https\nhost=gitlab.com\n",
        err_expected="returned non-zero exit status 1.",
    ),
    # 2. pass_target pointing to directory
    HelperConfig(
        entry_name="git/gitlab.com",
        # simulate a directory list generated by pass (just to be a bit more realistic)
        entry_data="git/gitlab.com\n├── user1\n└── user2\n".encode(),
        request="\nprotocol=https\nhost=gitlab.com\n",
        err_expected="'git/gitlab.com' is not a password store entry (looks like a directory)",
    ),
    # 3. pass_target pointing to password store entry with identically named directory
    HelperConfig(
        entry_name="git/github.com",
        entry_data=b"secret\nusername: ghuser0\n",
        request="\nprotocol=https\nhost=github.com\n",
        out_expected="password=secret\nusername=ghuser0\n",
    ),
    # 4. pass_target pointing to valid (dummy) password store entry
    HelperConfig(
        entry_name="git/gitlab.com/user1",
        entry_data=b"secret\nusername: gluser1\n",
        request="\nprotocol=https\nhost=gitlab.com\npath=example/example.git\n",
        out_expected="password=secret\nusername=gluser1\n",
    ),
]


class TestScript:
    def test_help(self, capsys: Any) -> None:
        with pytest.raises(SystemExit, match=r"^0$"):
            passgithelper.main(["--help"])

        out, err = capsys.readouterr()
        assert "usage: " in out
        assert not err

    def test_skip(self, monkeypatch: Any, capsys: Any) -> None:
        monkeypatch.setenv("PASS_GIT_HELPER_SKIP", "1")
        with pytest.raises(SystemExit, match=r"^6$"):
            passgithelper.main(["get"])
        out, err = capsys.readouterr()
        assert not out
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/smoke",
                """
protocol=https
host=mytest.com""",
                b"narf",
                "dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_smoke_resolve(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=narf\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir=None,
                request="host=ignored",
                entry_data=None,
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_unsupported_action_option(self, caplog: Any) -> None:
        """Check handling of unsupported ``action`` option."""
        with pytest.raises(SystemExit, match=r"^5$"):
            passgithelper.main(["store"])

        assert caplog.record_tuples[-1] == (
            "root",
            logging.INFO,
            "Action 'store' is currently not supported",
        )

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/smoke",
                request="",
                entry_data=None,
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_request_without_host(self, capsys: Any) -> None:
        """Check handling of request without ``host``.

        Note: The current handling is not optimal since the error message
        indicates that the error occurs while retrieving a password store entry
        instead of clearly stating that the received request is invalid.

        """
        with pytest.raises(SystemExit, match=r"^3$"):
            passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert not out
        assert "Unable to retrieve entry:" in err  # TODO: check if this can be changed
        assert "Request lacks host entry" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/smoke",
                request="request_line_without_equals_sign",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_request_with_invalid_line(self, capsys: CapsysType) -> None:
        """Check handling of invalid request line (i.e. line which does not contain a ``=``).

        Note: The current handling is not optimal since the ValueError exception
        'escapes' the error handling in main(..).

        """
        with pytest.raises(
            ValueError,
            match=r"^Missing '=' in request line, cannot be parsed as key/value pair: '.*'$",
        ):
            passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert not out
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/smoke",
                """
protocol=https
host=mytest.com
path=/foo/bar.git""",
                b"ignored",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_path_used_if_present_fails(self, capsys: Any) -> None:
        """Request contains `path` which does not have a corresponding section in mapping file."""
        with pytest.raises(SystemExit, match=r"^3$"):
            passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert not out
        assert "No mapping section" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/with-path",
                """
protocol=https
host=mytest.com
path=subpath/bar.git""",
                b"narf",
                "dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_path_used_if_present(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=narf\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/with-invalid-mapping",
                request="host=ignored",
                err_expected="Unable to parse mapping file: File contains no section headers.",
            ),
        ],
        indirect=True,
    )
    def test_invalid_mapping_file(
        self, capsys: CapsysType, helper_config: HelperConfigAndMock
    ) -> None:
        test_params = helper_config.test_params

        with pytest.raises(SystemExit, match=r"^4$"):
            passgithelper.main(["get"])

        teardown_helper_capsys_checks(capsys, test_params)

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/wildcard",
                """
protocol=https
host=wildcard.com
username=wildcard
path=subpath/bar.git""",
                b"narf-wildcard",
                "dev/https/wildcard.com/wildcard",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_wildcard_matching(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=narf-wildcard\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/wildcard_path",
                """
protocol=https
host=path_wildcard.com
username=path_wildcard
path=subpath/bar.git""",
                b"daniele-tentoni-path-wildcard",
                "dev/https/path_wildcard.com/path_wildcard/subpath/bar.git",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_wildcard_path_matching(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=daniele-tentoni-path-wildcard\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/with-username",
                """
host=plainline.com""",
                b"password\nusername",
                "dev/plainline",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_username_provided(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=password\nusername=username\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/with-username",
                """
host=plainline.com
username=narf""",
                b"password\nusername",
                "dev/plainline",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_username_skipped_if_provided(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=password\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/with-username",
                """
protocol=https
host=mytest.com""",
                b"narf",
                "dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_custom_mapping_used(self, capsys: Any) -> None:
        # this would fail for the default file from with-username
        passgithelper.main(["-m", "test_data/smoke/git-pass-mapping.ini", "get"])

        out, err = capsys.readouterr()
        assert out == "password=narf\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/with-username-skip",
                """
protocol=https
host=mytest.com""",
                b"password: xyz\nuser: tester",
                "dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_prefix_skipping(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=xyz\nusername=tester\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/unknown-username-extractor",
                """
protocol=https
host=mytest.com""",
                b"ignored",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_select_unknown_username_extractor(self, capsys: Any) -> None:
        with pytest.raises(SystemExit, match=r"^3$"):
            passgithelper.main(["get"])
        out, err = capsys.readouterr()
        assert not out
        assert "username_extractor of type 'doesntexist' does not exist" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/regex-username-extraction",
                """
protocol=https
host=mytest.com""",
                b"xyz\nsomeline\nmyuser: tester\n morestuff\nmyuser: ignore",
                "dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_regex_username_selection(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=xyz\nusername=tester\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/entry-name-extraction",
                """
protocol=https
host=mytest.com""",
                b"xyz",
                "dev/mytest/myuser",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_entry_name_is_user(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=xyz\nusername=myuser\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/unknown-password-extractor",
                """
protocol=https
host=mytest.com""",
                b"ignored",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_select_unknown_password_extractor(self, capsys: Any) -> None:
        with pytest.raises(SystemExit, match=r"^3$"):
            passgithelper.main(["get"])
        out, err = capsys.readouterr()
        assert not out
        assert "password_extractor of type 'doesntexist' does not exist" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/regex-password-extraction",
                """
protocol=https
host=mytest.com""",
                b"xyz\nsomeline\nmyauth: mygreattoken\nmorestuff\nmyuser: tester\n",
                "dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_regex_password_selection(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=mygreattoken\nusername=tester\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/with-encoding",
                """
protocol=https
host=mytest.com""",
                "täßt".encode("LATIN1"),
                "dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_uses_configured_encoding(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=täßt\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/smoke",
                """
protocol=https
host=mytest.com""",
                "täßt".encode("UTF-8"),
                "dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_uses_utf8_by_default(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=täßt\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/smoke",
                """
protocol=https
host=mytest.com""",
                None,
                "dev/mytest",
            ),
        ],
        indirect=True,
    )
    def test_fails_gracefully_on_pass_errors(
        self,
        capsys: CapsysType,
        helper_config: HelperConfigAndMock,
    ) -> None:
        helper_config.test_params.mock_co_expected_calls = 1
        with pytest.raises(SystemExit, match=r"^3$"):
            passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert not out
        assert "Unable to retrieve" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/smoke",
                """
protocol=https
host=unknown""",
                "ignored".encode("UTF-8"),
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_fails_gracefully_on_missing_entries(self, capsys: Any) -> None:
        with pytest.raises(SystemExit, match=r"^3$"):
            passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert not out
        assert "Unable to retrieve" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/password_store_dir",
                """
host=example.com""",
                "test".encode("UTF-8"),
            ),
        ],
        indirect=True,
    )
    def test_supports_switching_password_store_dirs(
        self,
        capsys: CapsysType,
        helper_config: HelperConfigAndMock,
    ) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=test\n"
        assert not err
        assert (
            helper_config.mock_co.call_args.kwargs["env"]["PASSWORD_STORE_DIR"]
            == "/some/dir"
        )

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                request="protocol=https\nhost=example.com\n",
                entry_data=b"ignored",
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    def test_uses_password_store_dir_relative_to_home(
        self, mocker: MockerFixture, helper_config: HelperConfigAndMock
    ) -> None:
        test_params = helper_config.test_params
        pws_dir_expected = "~/some/dir"
        setup_helper_parse_request_mock(
            mocker, test_params, password_store_dir=pws_dir_expected
        )

        passgithelper.main(["get"])

        mock_co: MagicMock = helper_config.mock_co
        assert "env" in mock_co.call_args.kwargs
        env = mock_co.call_args.kwargs["env"]
        assert "PASSWORD_STORE_DIR" in env
        password_store_dir = Path(env["PASSWORD_STORE_DIR"])
        assert password_store_dir.is_absolute()
        assert password_store_dir == Path(pws_dir_expected).expanduser()

    @pytest.mark.parametrize(
        "helper_config",
        test_params_pass_get_entry,
        indirect=True,
    )
    def test_verifies_pass_get_entry_function(
        self,
        mocker: MockerFixture,
        capsys: CapsysType,
        helper_config: HelperConfigAndMock,
    ) -> None:
        test_params = helper_config.test_params

        setup_helper_parse_request_mock(
            mocker,
            test_params,
            use_wildcard_section=True,
            username_extractor="regex_search",
        )

        # get mock objects set up by helper_config function
        mock_co: MagicMock = helper_config.mock_co

        pass_target = test_params.get_pass_target()
        if pass_target == "git/gitlab.com/doesnotexist":
            # 1. simulate pass_target pointing to non-existing password store
            #    entry by changing the 1st check_output mock call to raise an
            #    exception
            mock_co.side_effect = CalledProcessError(
                returncode=1, cmd=["pass", "show", pass_target]
            )
            test_params.mock_co_expected_calls = 1
        elif pass_target == "git/gitlab.com":
            # 2. simulate pass_target pointing to directory by changing the
            #    check_output mock calls to return twice the same (simulated)
            #    directory listing
            mock_co.side_effect = [
                test_params.entry_data,
            ] * 2
        elif pass_target == "git/github.com":
            # 3. simulate pass_target pointing to valid password store entry
            #    with a identically named directory by changing the return value
            #    of the 2nd mock_co call to something resembling a `pass`
            #    directory listing
            mock_co.side_effect = [
                test_params.entry_data,
                f"{pass_target}\n├── user1\n└── user2\n".encode(),
            ]
        else:
            # 4. simulate pass_target pointing to valid password store entry

            # Note: This corresponds to the default setup in the
            # ``helper_config`` fixture, so there is nothing which needs to be
            # done.
            pass

        with (
            pytest.raises(SystemExit, match=r"^3$")
            if test_params.err_expected
            else nullcontext()
        ):
            passgithelper.main(["get"])

        teardown_helper_capsys_checks(capsys, test_params)


@pytest.fixture
def setup_mock_gpg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manipulatest ``PATH`` in order to get ``pass`` to use the local mock gpg(2)."""
    gpg_mock_dir = str(Path.cwd() / "test_data" / "dummy-password-store" / "bin")
    monkeypatch.setenv("PATH", gpg_mock_dir, prepend=os.pathsep)


@pytest.fixture
def setup_teardown_wo_subprocess_mock(
    capsys: CapsysType,
    mocker: MockerFixture,
    request: pytest.FixtureRequest,
) -> Generator[HelperConfig]:
    """Simplified setup/teardown fixture for ``passgithelper.main`` tests.

    This is for tests which don't require the ``subprocess.check_output`` mock.
    """
    test_params: HelperConfig = request.param

    setup_helper_load_first_config_and_request(mocker, test_params)

    password_store_dir = Path.cwd() / "test_data" / "dummy-password-store"
    setup_helper_parse_request_mock(
        mocker,
        test_params,
        use_wildcard_section=True,
        # make sure that `pass` uses the dummy password store
        password_store_dir=str(password_store_dir),
        username_extractor="regex_search",
    )

    yield test_params

    teardown_helper_capsys_checks(capsys, test_params)


@pytest.mark.skipif(
    not os.getenv("PGH_ENABLE_TESTS_WITH_REAL_PASS"),
    reason="Tests require the `pass`word store CLI command.",
)
@pytest.mark.usefixtures("setup_mock_gpg")
class TestWithPassCli:
    """Tests using the real `pass`word store CLI tool."""

    @pytest.mark.parametrize(
        "setup_teardown_wo_subprocess_mock",
        test_params_pass_get_entry,
        indirect=True,
    )
    def test_pass_get_entry_function_with_mock_gpg(
        self,
        setup_teardown_wo_subprocess_mock: HelperConfig,
    ) -> None:
        # The test uses a dummy password store
        # (`test_data/dummy-password-store`, containing unencrypted password
        # store entries) together with gpg/gpg2 mocks (in
        # `test_data/dummy-password-store/bin`) which just `cat` the content of
        # the first command line option which matches an existing file to
        # stdout or, if no such file is found, exit with exit_code=1.
        test_params = setup_teardown_wo_subprocess_mock

        with (
            pytest.raises(SystemExit, match=r"^3$")
            if test_params.err_expected
            else nullcontext()
        ):
            passgithelper.main(["get"])
