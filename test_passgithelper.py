import configparser
from dataclasses import dataclass
import io
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

    mocker.patch("sys.stdin.readlines").return_value = io.StringIO(
        request.param.request
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
    with pytest.raises(SystemExit):
        passgithelper.handle_skip()


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


class TestStaticUsernameExtractor:
    def test_extracts_username_from_config(self) -> None:
        config = configparser.ConfigParser()
        config.read_string(
            """[test]
username = address@example.com
"""
        )

        extractor = passgithelper.StaticUsernameExtractor()
        extractor.configure(config["test"])

        assert (
            extractor.get_value("any/entry", ["any", "lines"]) == "address@example.com"
        )

    def test_returns_none_when_no_username_configured(self) -> None:
        config = configparser.ConfigParser()
        config.read_string(
            """[test]
target = some/target
"""
        )

        extractor = passgithelper.StaticUsernameExtractor()
        extractor.configure(config["test"])

        assert extractor.get_value("any/entry", ["any", "lines"]) is None

    def test_inherits_from_default_section(self) -> None:
        config = configparser.ConfigParser()
        config.read_string(
            """[DEFAULT]
username = default@example.com

[test]
target = some/target
"""
        )

        extractor = passgithelper.StaticUsernameExtractor()
        extractor.configure(config["test"])

        assert (
            extractor.get_value("any/entry", ["any", "lines"]) == "default@example.com"
        )

    def test_section_overrides_default(self) -> None:
        config = configparser.ConfigParser()
        config.read_string(
            """[DEFAULT]
username = default@example.com

[test]
target = some/target
username = override@example.com
"""
        )

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

        out, _ = capsys.readouterr()
        assert out == "password=daniele-tentoni-path-wildcard\n"

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
    def test_select_unknown_username_extractor(self, capsys: Any) -> None:
        with pytest.raises(SystemExit):
            passgithelper.main(["get"])
        _, err = capsys.readouterr()
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
        with pytest.raises(SystemExit):
            passgithelper.main(["get"])
        _, err = capsys.readouterr()
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

        out, _ = capsys.readouterr()
        assert out == "password=mygreattoken\nusername=tester\n"

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
