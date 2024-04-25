import configparser
from dataclasses import dataclass
import io
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


@pytest.fixture()
def helper_config(mocker: MockerFixture, request: Any) -> Iterable[Any]:
    xdg_mock = mocker.patch("xdg.BaseDirectory.load_first_config")
    xdg_mock.return_value = request.param.xdg_dir

    mocker.patch("sys.stdin.readlines").return_value = io.StringIO(
        request.param.request
    )

    if request.param.entry_data != b"check-password-file":
        mocker.patch("passgithelper.check_password_file")

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
    with pytest.raises(SystemExit):
        passgithelper.handle_skip()


class TestPasswordStoreDirSelection:
    """Test password store directory selection.

    The password store directory used by ``pass`` can either be set via the
    ``PASSWORD_STORE_DIR`` environment variable or via the
    ``password_store_dir`` option in the ini file. In case both are present, the
    ini file option overrides the environment variable.

    Empty values are ignored (as if they have not been set).

    The different variants are tested here.

    """

    def test_uses_default_value(self, monkeypatch: Any) -> None:
        ini = configparser.ConfigParser()

        expected = str(Path("~/.password-store").expanduser())
        # no password store configured (neither per env. variable nor ini file
        # option) -> default value (`~/.password-store`)
        monkeypatch.delenv("PASSWORD_STORE_DIR", raising=False)
        ini["example.com"] = {}
        env, pwd_store_dir = passgithelper.compute_pass_environment(ini["example.com"])
        assert str(pwd_store_dir) == env.get("PASSWORD_STORE_DIR")
        assert pwd_store_dir == Path(expected)
        # also: check `~` expansion
        assert pwd_store_dir.is_absolute()

    def test_uses_env_var_value(self, monkeypatch: Any) -> None:
        ini = configparser.ConfigParser()

        expected = "/tmp/password-store-from-env"
        # `PASSWORD_STORE_DIR` in environment, empty ini file section -> value of
        # env. variable overrides default
        monkeypatch.setenv("PASSWORD_STORE_DIR", expected)
        ini["example.com"] = {}
        env, pwd_store_dir = passgithelper.compute_pass_environment(ini["example.com"])
        assert str(pwd_store_dir) == env.get("PASSWORD_STORE_DIR")
        assert pwd_store_dir == Path(expected)

    def test_ini_file_option_overrides_environment(self, monkeypatch: Any) -> None:
        ini = configparser.ConfigParser()

        expected = "/tmp/password-store-from-ini"
        # `PASSWORD_STORE_DIR` in environemnt, `password_store_dir` in ini file
        # section -> value from ini file overrides env. variable
        monkeypatch.setenv("PASSWORD_STORE_DIR", "/tmp/password-store-from-env")
        ini["example.com"] = {"password_store_dir": expected}
        env, pwd_store_dir = passgithelper.compute_pass_environment(ini["example.com"])
        assert str(pwd_store_dir) == env.get("PASSWORD_STORE_DIR")
        assert pwd_store_dir == Path(expected)

    def test_ignores_empty_ini_file_option(self, monkeypatch: Any) -> None:
        ini = configparser.ConfigParser()

        expected = "/tmp/password-store-from-env"
        # `PASSWORD_STORE_DIR` in environment, empty `password_store_dir` in ini
        # file section -> empty ini value ignored, fall back to env. variable
        monkeypatch.setenv("PASSWORD_STORE_DIR", expected)
        ini["example.com"] = {"password_store_dir": ""}
        env, pwd_store_dir = passgithelper.compute_pass_environment(ini["example.com"])
        assert str(pwd_store_dir) == env.get("PASSWORD_STORE_DIR")
        assert pwd_store_dir == Path(expected)

    def test_ignores_empty_env_var_value(self, monkeypatch: Any) -> None:
        ini = configparser.ConfigParser()

        expected = str(Path("~/.password-store").expanduser())
        # empty `PASSWORD_STORE_DIR` in environment, empty ini file section -> empty
        # env. var value ignored, fall back to default
        monkeypatch.setenv("PASSWORD_STORE_DIR", "")
        ini["example.com"] = {}
        env, pwd_store_dir = passgithelper.compute_pass_environment(ini["example.com"])
        assert str(pwd_store_dir) == env.get("PASSWORD_STORE_DIR")
        assert pwd_store_dir == Path(expected)
        # also: check `~` expansion
        assert pwd_store_dir.is_absolute()


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
        config.read_string(
            r"""[test]
regex_username=^foo: (.*)$"""
        )
        extractor.configure(config["test"])
        assert extractor._regex.pattern == r"^foo: (.*)$"

    def test_configuration_checks_groups(self) -> None:
        extractor = passgithelper.RegexSearchExtractor("^username: (.*)$", "_username")
        config = configparser.ConfigParser()
        config.read_string(
            r"""[test]
regex_username=^foo: .*$"""
        )
        with pytest.raises(ValueError, match="must contain"):
            extractor.configure(config["test"])


class TestEntryNameExtractor:
    def test_smoke(self) -> None:
        assert passgithelper.EntryNameExtractor().get_value("foo/bar", []) == "bar"


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
    with pytest.raises(RuntimeError):
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
        with pytest.raises(SystemExit):
            passgithelper.main(["--help"])

        assert "usage: " in capsys.readouterr().out

    def test_skip(self, monkeypatch: Any, capsys: Any) -> None:
        monkeypatch.setenv("PASS_GIT_HELPER_SKIP", "1")
        with pytest.raises(SystemExit):
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

        out, _ = capsys.readouterr()
        assert out == "password=narf\n"

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
        with pytest.raises(SystemExit):
            passgithelper.main(["get"])

        _, err = capsys.readouterr()
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

        out, _ = capsys.readouterr()
        assert out == "password=narf\n"

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

        out, _ = capsys.readouterr()
        assert out == "password=narf-wildcard\n"

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

        out, _ = capsys.readouterr()
        assert out == "password=password\nusername=username\n"

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

        out, _ = capsys.readouterr()
        assert out == "password=password\n"

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

        out, _ = capsys.readouterr()
        assert out == "password=narf\n"

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

        out, _ = capsys.readouterr()
        assert out == "password=xyz\nusername=tester\n"

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
    def test_select_unknown_extractor(self, capsys: Any) -> None:
        with pytest.raises(SystemExit):
            passgithelper.main(["get"])
        _, err = capsys.readouterr()
        assert "username_extractor of type 'doesntexist' does not exist" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                "test_data/regex-extraction",
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

        out, _ = capsys.readouterr()
        assert out == "password=xyz\nusername=tester\n"

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

        out, _ = capsys.readouterr()
        assert out == "password=xyz\nusername=myuser\n"

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

        out, _ = capsys.readouterr()
        assert out == "password=täßt\n"

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

        out, _ = capsys.readouterr()
        assert out == "password=täßt\n"

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
        with pytest.raises(SystemExit):
            passgithelper.main(["get"])

        _, err = capsys.readouterr()
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
        with pytest.raises(SystemExit):
            passgithelper.main(["get"])

        _, err = capsys.readouterr()
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

        out, _ = capsys.readouterr()
        assert out == "password=test\n"
        assert (
            helper_config.mock_calls[-1].kwargs["env"]["PASSWORD_STORE_DIR"]
            == "/some/dir"
        )

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                None,
                "\nhost=example.com",
                b"ignored",
            ),
        ],
        indirect=True,
    )
    def test_uses_password_store_dir_relative_to_home(
        self, mocker: MockerFixture, helper_config: Any
    ) -> None:
        config = configparser.ConfigParser()
        config["example.com"] = {
            "password_store_dir": "~/some/dir",
            "target": "dev/mytest",
        }
        mocker.patch("passgithelper.parse_mapping").return_value = config

        passgithelper.main(["get"])
        password_store_dir = Path(
            helper_config.mock_calls[-1].kwargs["env"]["PASSWORD_STORE_DIR"]
        )
        assert password_store_dir.is_absolute()
        assert password_store_dir == Path("~/some/dir").expanduser()

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                None,
                "",
                b"check-password-file",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_verifies_check_password_file_function(
        self, monkeypatch: Any, mocker: MockerFixture, capsys: Any
    ) -> None:
        monkeypatch.setenv(
            "PASSWORD_STORE_DIR", str(Path.cwd() / "test_data/dummy-password-store")
        )
        config = configparser.ConfigParser()
        config["example.com"] = {"target": ""}
        mocker.patch("passgithelper.parse_mapping").return_value = config
        mocker.patch("passgithelper.parse_request").return_value = {
            "host": "example.com"
        }

        config["example.com"]["target"] = "example.com"
        with pytest.raises(SystemExit):
            passgithelper.main(["get"])
        out, err = capsys.readouterr()
        assert err.endswith("example.com.gpg' is not a file\n")
        assert not out

        config["example.com"]["target"] = "example.com/doesnotexist"
        with pytest.raises(SystemExit):
            passgithelper.main(["get"])
        _, err = capsys.readouterr()
        assert err.endswith("example.com/doesnotexist.gpg' does not exist\n")
        assert not out

        config["example.com"]["target"] = "example.com/fakepwd"
        passgithelper.main(["get"])
        out, err = capsys.readouterr()
        assert out == "password=check-password-file\n"
        assert not err
