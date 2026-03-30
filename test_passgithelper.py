import configparser
import logging
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError
from typing import Any, Iterable, Optional, Sequence, Text
from unittest.mock import ANY

import pytest
from pytest_mock import MockerFixture

import passgithelper


@dataclass
class HelperConfig:
    xdg_dir: Optional[str]
    request: str
    entry_data: Optional[bytes]
    entry_name: Optional[str] = None


@pytest.fixture
def helper_config(mocker: MockerFixture, request: Any) -> Iterable[Any]:
    xdg_mock = mocker.patch("xdg.BaseDirectory.load_first_config")
    xdg_mock.return_value = request.param.xdg_dir

    mocker.patch(
        "sys.stdin.readlines",
        return_value=request.param.request.splitlines(keepends=True),
    )

    subprocess_mock = mocker.patch("subprocess.check_output")
    if request.param.entry_data:
        subprocess_mock.return_value = request.param.entry_data
    else:
        subprocess_mock.side_effect = CalledProcessError(1, ["pass"], "pass failed")

    yield subprocess_mock

    if request.param.entry_name is not None:
        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(
            ["pass", "show", request.param.entry_name], env=ANY
        )


def test_handle_skip_nothing(monkeypatch: Any) -> None:
    monkeypatch.delenv("PASS_GIT_HELPER_SKIP", raising=False)
    passgithelper.handle_skip()
    # should do nothing normally


def test_handle_skip_exits(monkeypatch: Any) -> None:
    monkeypatch.setenv("PASS_GIT_HELPER_SKIP", "1")
    with pytest.raises(SystemExit, match=r"^6$"):
        passgithelper.handle_skip()


def test_mapping_option_with_non_existing_file(capsys: Any) -> None:
    """Test handling of ``--mapping`` option with a non-existing file name."""
    nonexisting_ini_file = "___this_file_d0es_not_exist___.notIn1"
    err_expected = f"No such file or directory: '{nonexisting_ini_file}'"
    with pytest.raises(SystemExit, match=r"^2$"):
        passgithelper.main(["--mapping", nonexisting_ini_file])

    out, err = capsys.readouterr()
    assert not out
    assert err_expected in err


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
                entry_data=None,
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_request_with_invalid_line(self, capsys: Any) -> None:
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
                entry_data=None,
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_invalid_mapping_file(self, capsys: Any) -> None:
        with pytest.raises(SystemExit, match=r"^4$"):
            passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert not out
        assert "Unable to parse mapping file: File contains no section headers." in err

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
    @pytest.mark.usefixtures("helper_config")
    def test_fails_gracefully_on_pass_errors(self, capsys: Any) -> None:
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
        self, capsys: Any, helper_config: Any
    ) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=test\n"
        assert not err
        assert (
            helper_config.mock_calls[-1].kwargs["env"]["PASSWORD_STORE_DIR"]
            == "/some/dir"
        )

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir=None,
                request="protocol=https\nhost=example.com\n",
                entry_data=b"ignored",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_uses_password_store_dir_relative_to_home(
        self, mocker: MockerFixture, helper_config: Any
    ) -> None:
        host = "example.com"
        config = configparser.ConfigParser()
        config[host] = {
            "password_store_dir": "~/some/dir",
            "target": "dev/mytest",
        }
        mocker.patch("passgithelper.parse_mapping", return_value=config)

        passgithelper.main(["get"])

        mock_co = helper_config
        assert "env" in mock_co.call_args.kwargs
        env = mock_co.call_args.kwargs["env"]
        assert "PASSWORD_STORE_DIR" in env
        password_store_dir = Path(env["PASSWORD_STORE_DIR"])
        assert password_store_dir.is_absolute()
        assert password_store_dir == Path("~/some/dir").expanduser()
