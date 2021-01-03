import configparser
import io
from typing import Any, Optional, Sequence, Text

import pytest
from pytest_mock import MockFixture

import passgithelper


@pytest.fixture()
def _xdg_dir(request: Any, mocker: MockFixture) -> None:
    xdg_mock = mocker.patch("xdg.BaseDirectory.load_first_config")
    xdg_mock.return_value = request.param


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
    "_xdg_dir",
    [None],
    indirect=True,
)
@pytest.mark.usefixtures("_xdg_dir")
def test_parse_mapping_file_missing() -> None:
    with pytest.raises(RuntimeError):
        passgithelper.parse_mapping(None)


@pytest.mark.parametrize(
    "_xdg_dir",
    ["test_data/smoke"],
    indirect=True,
)
@pytest.mark.usefixtures("_xdg_dir")
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
        "_xdg_dir",
        ["test_data/smoke"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_smoke_resolve(
        self, monkeypatch: Any, mocker: MockFixture, capsys: Any
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=mytest.com"""
            ),
        )
        subprocess_mock = mocker.patch("subprocess.check_output")
        subprocess_mock.return_value = b"narf"

        passgithelper.main(["get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", "dev/mytest"])

        out, _ = capsys.readouterr()
        assert out == "password=narf\n"

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/smoke"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_path_used_if_present_fails(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=mytest.com
path=/foo/bar.git"""
            ),
        )

        with pytest.raises(ValueError, match="No mapping section"):
            passgithelper.main(["get"])

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/with-path"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_path_used_if_present(
        self, monkeypatch: Any, mocker: MockFixture, capsys: Any
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=mytest.com
path=subpath/bar.git"""
            ),
        )

        subprocess_mock = mocker.patch("subprocess.check_output")
        subprocess_mock.return_value = b"narf"

        passgithelper.main(["get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", "dev/mytest"])

        out, _ = capsys.readouterr()
        assert out == "password=narf\n"

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/wildcard"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_wildcard_matching(
        self, monkeypatch: Any, mocker: MockFixture, capsys: Any
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=wildcard.com
username=wildcard
path=subpath/bar.git"""
            ),
        )

        subprocess_mock = mocker.patch("subprocess.check_output")
        subprocess_mock.return_value = b"narf-wildcard"

        passgithelper.main(["get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(
            ["pass", "show", "dev/wildcard.com/wildcard"]
        )

        out, _ = capsys.readouterr()
        assert out == "password=narf-wildcard\n"

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/with-username"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_username_provided(
        self, monkeypatch: Any, mocker: MockFixture, capsys: Any
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=plainline.com"""
            ),
        )

        subprocess_mock = mocker.patch("subprocess.check_output")
        subprocess_mock.return_value = b"password\nusername"

        passgithelper.main(["get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", "dev/plainline"])

        out, _ = capsys.readouterr()
        assert out == "password=password\nusername=username\n"

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/with-username"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_username_skipped_if_provided(
        self, monkeypatch: Any, mocker: MockFixture, capsys: Any
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=plainline.com
username=narf"""
            ),
        )

        subprocess_mock = mocker.patch("subprocess.check_output")
        subprocess_mock.return_value = b"password\nusername"

        passgithelper.main(["get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", "dev/plainline"])

        out, _ = capsys.readouterr()
        assert out == "password=password\n"

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/with-username"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_custom_mapping_used(
        self, monkeypatch: Any, mocker: MockFixture, capsys: Any
    ) -> None:
        # this would fail for the default file from with-username
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=mytest.com"""
            ),
        )
        subprocess_mock = mocker.patch("subprocess.check_output")
        subprocess_mock.return_value = b"narf"

        passgithelper.main(["-m", "test_data/smoke/git-pass-mapping.ini", "get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", "dev/mytest"])

        out, _ = capsys.readouterr()
        assert out == "password=narf\n"

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/with-username-skip"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_prefix_skipping(
        self, monkeypatch: Any, mocker: MockFixture, capsys: Any
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=mytest.com"""
            ),
        )
        subprocess_mock = mocker.patch("subprocess.check_output")
        subprocess_mock.return_value = b"password: xyz\nuser: tester"

        passgithelper.main(["get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", "dev/mytest"])

        out, _ = capsys.readouterr()
        assert out == "password=xyz\nusername=tester\n"

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/unknown-username-extractor"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_select_unknown_extractor(self, monkeypatch: Any, capsys: Any) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=mytest.com"""
            ),
        )

        with pytest.raises(KeyError):
            passgithelper.main(["get"])

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/regex-extraction"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_regex_username_selection(
        self, monkeypatch: Any, mocker: MockFixture, capsys: Any
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=mytest.com"""
            ),
        )
        subprocess_mock = mocker.patch("subprocess.check_output")
        subprocess_mock.return_value = (
            b"xyz\nsomeline\nmyuser: tester\n" b"morestuff\nmyuser: ignore"
        )

        passgithelper.main(["get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", "dev/mytest"])

        out, _ = capsys.readouterr()
        assert out == "password=xyz\nusername=tester\n"

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/entry-name-extraction"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_entry_name_is_user(
        self, monkeypatch: Any, mocker: MockFixture, capsys: Any
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=mytest.com"""
            ),
        )
        subprocess_mock = mocker.patch("subprocess.check_output")
        subprocess_mock.return_value = b"xyz"

        passgithelper.main(["get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", "dev/mytest/myuser"])

        out, _ = capsys.readouterr()
        assert out == "password=xyz\nusername=myuser\n"

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/with-encoding"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_uses_configured_encoding(
        self, monkeypatch: Any, mocker: MockFixture, capsys: Any
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=mytest.com"""
            ),
        )
        subprocess_mock = mocker.patch("subprocess.check_output")
        password = "täßt"
        subprocess_mock.return_value = password.encode("LATIN1")

        passgithelper.main(["get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", "dev/mytest"])

        out, _ = capsys.readouterr()
        assert out == f"password={password}\n"

    @pytest.mark.parametrize(
        "_xdg_dir",
        ["test_data/smoke"],
        indirect=True,
    )
    @pytest.mark.usefixtures("_xdg_dir")
    def test_uses_utf8_by_default(
        self, mocker: MockFixture, monkeypatch: Any, capsys: Any
    ) -> None:
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                """
protocol=https
host=mytest.com"""
            ),
        )

        subprocess_mock = mocker.patch("subprocess.check_output")
        password = "täßt"
        subprocess_mock.return_value = password.encode("UTF-8")

        passgithelper.main(["get"])

        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(["pass", "show", "dev/mytest"])

        out, _ = capsys.readouterr()
        assert out == f"password={password}\n"
