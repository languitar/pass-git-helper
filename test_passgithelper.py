import configparser
from dataclasses import dataclass
import io
from typing import Any, Iterable, Optional, Sequence, Text

import pytest
from pytest_mock import MockFixture

import passgithelper


@dataclass
class HelperConfig:
    xdg_dir: Optional[str]
    request: str
    entry_data: bytes
    entry_name: Optional[str] = None


@pytest.fixture()
def _helper_config(mocker: MockFixture, request: Any) -> Iterable[None]:
    xdg_mock = mocker.patch("xdg.BaseDirectory.load_first_config")
    xdg_mock.return_value = request.param.xdg_dir

    mocker.patch("sys.stdin.readlines").return_value = io.StringIO(
        request.param.request
    )

    subprocess_mock = mocker.patch("subprocess.check_output")
    subprocess_mock.return_value = request.param.entry_data

    yield

    if request.param.entry_name is not None:
        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", request.param.entry_name])


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
            self, entry_text: Text, entry_lines: Sequence[Text]
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
    "_helper_config",
    [
        HelperConfig(
            None,
            "",
            b"ignored",
        ),
    ],
    indirect=True,
)
@pytest.mark.usefixtures("_helper_config")
def test_parse_mapping_file_missing() -> None:
    with pytest.raises(RuntimeError):
        passgithelper.parse_mapping(None)


@pytest.mark.parametrize(
    "_helper_config",
    [
        HelperConfig(
            "test_data/smoke",
            "",
            b"ignored",
        ),
    ],
    indirect=True,
)
@pytest.mark.usefixtures("_helper_config")
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
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_smoke_resolve(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=narf\n"

    @pytest.mark.parametrize(
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_path_used_if_present_fails(self) -> None:
        with pytest.raises(ValueError, match="No mapping section"):
            passgithelper.main(["get"])

    @pytest.mark.parametrize(
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_path_used_if_present(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=narf\n"

    @pytest.mark.parametrize(
        "_helper_config",
        [
            HelperConfig(
                "test_data/wildcard",
                """
protocol=https
host=wildcard.com
username=wildcard
path=subpath/bar.git""",
                b"narf-wildcard",
                "dev/wildcard.com/wildcard",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("_helper_config")
    def test_wildcard_matching(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=narf-wildcard\n"

    @pytest.mark.parametrize(
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_username_provided(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=password\nusername=username\n"

    @pytest.mark.parametrize(
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_username_skipped_if_provided(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=password\n"

    @pytest.mark.parametrize(
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_custom_mapping_used(self, capsys: Any) -> None:
        # this would fail for the default file from with-username
        passgithelper.main(["-m", "test_data/smoke/git-pass-mapping.ini", "get"])

        out, _ = capsys.readouterr()
        assert out == "password=narf\n"

    @pytest.mark.parametrize(
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_prefix_skipping(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=xyz\nusername=tester\n"

    @pytest.mark.parametrize(
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_select_unknown_extractor(self) -> None:
        with pytest.raises(KeyError):
            passgithelper.main(["get"])

    @pytest.mark.parametrize(
        "_helper_config",
        [
            HelperConfig(
                "test_data/regex-extraction",
                """
protocol=https
host=mytest.com""",
                b"xyz\nsomeline\nmyuser: tester\n" b"morestuff\nmyuser: ignore",
                "dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("_helper_config")
    def test_regex_username_selection(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=xyz\nusername=tester\n"

    @pytest.mark.parametrize(
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_entry_name_is_user(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=xyz\nusername=myuser\n"

    @pytest.mark.parametrize(
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_uses_configured_encoding(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == f"password=täßt\n"

    @pytest.mark.parametrize(
        "_helper_config",
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
    @pytest.mark.usefixtures("_helper_config")
    def test_uses_utf8_by_default(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=täßt\n"

    @pytest.mark.parametrize(
        "_helper_config",
        [
            HelperConfig(
                "test_data/with-protocol",
                """
protocol=https
host=github.com
path=organization/bar.git""",
                b"github_https_token",
                "https_github.com/organization",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("_helper_config")
    def test_with_protocol_there(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=github_https_token\nusername=organization\n"

    @pytest.mark.parametrize(
        "_helper_config",
        [
            HelperConfig(
                "test_data/with-protocol",
                """
protocol=https
host=myhub.com
path=organization/bar.git""",
                b"myhub_https_token",
                "myhub.com/organization",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("_helper_config")
    def test_with_protocol_not_there(self, capsys: Any) -> None:
        passgithelper.main(["get"])

        out, _ = capsys.readouterr()
        assert out == "password=myhub_https_token\nusername=organization\n"

