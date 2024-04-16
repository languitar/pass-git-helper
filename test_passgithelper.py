import configparser
import logging
from collections.abc import Generator, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError
from typing import ClassVar, Text, TypeAlias, cast
from unittest.mock import ANY

import pytest
from pytest_mock import MockerFixture, MockType

import passgithelper

CapsysType: TypeAlias = pytest.CaptureFixture[str]
CaplogType: TypeAlias = pytest.LogCaptureFixture


@dataclass
class HelperConfig:
    """Used by the ``helper_config`` fixture to configure test setup and teardown.

    Check the attributes documentation below for more details.

    The ``helper_config`` fixture also makes the ``HelperConfig`` data available to the
    test implementation by yielding it as part of the ``HelperConfigAndMock`` object
    after test setup is done. To access it, tests must explicitly add ``helper_config``
    to their function signature. Used in this way, it allows tests to adapt their
    behavior to the provided parametrization data. It also allows tests to dynamically
    configure the teardown part of the ``helper_function`` fixture from their
    implementatoin (see ``mock_co_expect_call`` below).

    Attributes:
        xdg_dir:
            Used by ``helper_config`` to configure the directory containing the
            pass-git-helper ini/mapping file (default: None).
        request:
            Used by ``helper_config`` to simulate the request data provided by git via
            ``stdin`` (default: "").
        entry_data:
            Used by ``helper_config`` to simulated the password store entry data
            returned for ``entry_name`` by ``pass``. Also used to configure the
            ``subprocess.check_output`` mock which simulates calling ``pass``. If
            ``entry_data`` is provides, the setup done by ``helper_config`` mimics a
            successful ``pass``word retrieval. With ``entry_data=None`` (the default) or
            ``entry_data=b""``, ``helper_config`` mimics a missing password store entry.
        entry_name:
            Name of the password store entry (aka `pass_target`) to retrieve. It mainly
            gets used in the teardown part of ``helper_config`` to check that it is
            contained in the the command line options provided to ``pass`` (default:
            None). It can also be used to adapt test implementation behaviour to a given
            parametrization.
        patch_ensure_password_is_file:
            When set to False (which is the default), ``helper_config`` will patch the
            ``patch_ensure_password_is_file(..)`` function. Only change this to ``True``
            for tests of the ``patch_ensure_password_is_file(..)`` function.
        mock_co_expect_call:
            With the default (``True``), ``helper_config`` will add ``assert_called_*``
            checks for the ``subprocess.check_output`` mock during teardown. When set to
            ``False``, these asserts will be skipped.
        out_expected:
            Document the expected output on ``stdout`` in the test parametrization. Will
            automatically be checked when using the
            ``teardown_helper_capsys_checks(..)`` function.
        err_expected:
            Same as ``out_expected``, but for ``stderr`` instead of ``stdout``.

    """

    xdg_dir: str | None = None
    request: str = ""
    entry_data: bytes | None = None
    entry_name: str | None = None
    patch_ensure_password_is_file: bool = True
    mock_co_expect_call: bool = True
    out_expected: str = ""
    err_expected: str = ""

    def get_host(self) -> str | None:
        """Get the host contained in ``request``."""
        if not self.request:
            return None
        for line in self.request.splitlines():
            param = line.split("=")
            if len(param) > 1 and param[0] == "host":
                return param[1]
        return None

    def get_pass_target(self, default: str | None = None) -> str | None:
        """Get the ``entry_name`` (aka `pass_target`) with optional default."""
        return self.entry_name if self.entry_name is not None else default


@dataclass
class HelperConfigAndMock:
    """Generated value of the ``helper_config`` fixture.

    Attributes:
        test_params:
            Test parametrization data.
        mock_co:
            Mock for ``subprocess.check_output`` as used in the
            passgithelper.get_password(..) function.

    """

    test_params: HelperConfig
    mock_co: MockType


def setup_helper_parse_request_mock(
    mocker: MockerFixture | None,
    test_params: HelperConfig,
    use_wildcard_section: bool = False,
    **kwargs: str,
) -> configparser.ConfigParser | None:
    """Create simple mapping/config from ``test_params`` and use it to mock ``parse_mapping``.

    The mapping gets created as follows:

    1. The ``host`` request parameter gets used to create a section. If
       ``use_wildcard_section`` is True, a ``*`` gets appended to ``host`` before adding
       the section.

    2. As a next step, if ``test_params.get_pass_target()`` returns a non-empty string,
       it gets added as ``target`` to the ``host`` section.

    3. Any ``kwargs`` also get added to the ``host`` section.

    The resulting ini/mapping object is then used as return value of the
    ``passgithelper.parse_mapping`` function using a mock object (this step is skipped
    if ``mocker`` is ``None``).

    Intended to be called from a test fixture or directly from a test.

    Note: If you want a ``target`` in the ``host`` section of the created ConfigParser
    object it should be added via ``test_params.entry_name``, not via ``kwargs``.
    Otherwise automatic ``assert_called_*`` performed by the ``helper_config`` fixture
    will fail or will need to be disabled.

    Args:
        mocker:
            Used to patch the ``passgithelper.parse_mapping`` function. May be none to
            skip the patching.
        test_params:
            Test parameters.
        use_wildcard_section:
            If True, the host section is created with a trailing ``*``.
        kwargs:
            Any ``kwargs`` are added to the ``host`` section of the created
            ConfigParser object.

    Returns:
        The created ``ConfigParser`` object in case of success, ``None`` otherwise.

    """
    # git host to use as section in created config
    host = test_params.get_host()
    if not host:
        return None
    else:
        host = f"{host}*" if use_wildcard_section else host

    # create ini (mapping)
    ini = configparser.ConfigParser()
    ini.add_section(host)
    section = ini[host]
    if pass_target := test_params.get_pass_target():
        section.update(target=pass_target)
    section.update(kwargs)

    if mocker is not None:
        # mock parse_request
        _ = mocker.patch("passgithelper.parse_mapping", return_value=ini)

    return ini


def teardown_helper_capsys_checks(
    capsys: CapsysType,
    test_params: HelperConfig,
    out_use_equals: bool = False,
    err_use_equals: bool = False,
) -> None:
    """Teardown helper which checks stdout/stderr dependening on ``test_params``.

    Asserts that ``capsys.readouterr()`` matches
    ``test_params..{out,err}_expected``.

    Intended to be called from a test fixture or directly from a test.

    Args:
        capsys:
            pytest capsys fixture used to access stout/sterr.
        test_params:
            ``HelperConfig`` object containing ``out_expected``/``err_expected``
        out_use_equals:
            By default, ``test_params.out_expected`` is checked using ``in``.
            With ``out_use_equals=True`` the comparison is done using ``==``.
        err_use_equals:
            By default, ``test_params.err_expected`` is checked using ``in``.
            With ``err_use_equals=True`` the comparison is done using ``==``.
    """
    out, err = capsys.readouterr()

    if test_params.out_expected:
        if out_use_equals:
            assert out == test_params.out_expected
        else:
            assert test_params.out_expected in out
    else:
        assert not out

    if test_params.err_expected:
        if err_use_equals:
            assert err == test_params.err_expected
        else:
            assert test_params.err_expected in err
    else:
        assert not err


@pytest.fixture
def helper_config(
    mocker: MockerFixture, request: pytest.FixtureRequest
) -> Generator[HelperConfigAndMock]:
    test_params = cast("HelperConfig", request.param)
    _ = mocker.patch(
        "xdg.BaseDirectory.load_first_config", return_value=test_params.xdg_dir
    )

    _ = mocker.patch(
        "sys.stdin.readlines",
        return_value=test_params.request.splitlines(keepends=True),
    )

    if test_params.patch_ensure_password_is_file:
        _ = mocker.patch("passgithelper.ensure_password_is_file")

    subprocess_mock = mocker.patch("subprocess.check_output")
    if test_params.entry_data:
        subprocess_mock.return_value = test_params.entry_data
    else:
        subprocess_mock.side_effect = CalledProcessError(
            returncode=1,
            cmd=["pass", "show", test_params.get_pass_target() or "unknown"],
        )

    yield HelperConfigAndMock(test_params, subprocess_mock)

    if test_params.mock_co_expect_call:
        subprocess_mock.assert_called_once()
        subprocess_mock.assert_called_with(
            ["pass", "show", test_params.entry_name], env=ANY
        )
    else:
        subprocess_mock.assert_not_called()


def test_handle_skip_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PASS_GIT_HELPER_SKIP", raising=False)
    passgithelper.handle_skip()
    # should do nothing normally


def test_handle_skip_exits(monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestPasswordStoreDirSelection:
    """Test password store directory selection.

    The password store directory used by ``pass`` can either be set via the
    ``PASSWORD_STORE_DIR`` environment variable or via the
    ``password_store_dir`` option in the ini file. In case both are present, the
    ini file option overrides the environment variable.

    Empty values are ignored (as if they have not been set).

    The different variants are tested here.

    """

    def test_uses_default_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_uses_env_var_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ini = configparser.ConfigParser()

        expected = "/tmp/password-store-from-env"
        # `PASSWORD_STORE_DIR` in environment, empty ini file section -> value of
        # env. variable overrides default
        monkeypatch.setenv("PASSWORD_STORE_DIR", expected)
        ini["example.com"] = {}
        env, pwd_store_dir = passgithelper.compute_pass_environment(ini["example.com"])
        assert str(pwd_store_dir) == env.get("PASSWORD_STORE_DIR")
        assert pwd_store_dir == Path(expected)

    def test_ini_file_option_overrides_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ini = configparser.ConfigParser()

        expected = "/tmp/password-store-from-ini"
        # `PASSWORD_STORE_DIR` in environemnt, `password_store_dir` in ini file
        # section -> value from ini file overrides env. variable
        monkeypatch.setenv("PASSWORD_STORE_DIR", "/tmp/password-store-from-env")
        ini["example.com"] = {"password_store_dir": expected}
        env, pwd_store_dir = passgithelper.compute_pass_environment(ini["example.com"])
        assert str(pwd_store_dir) == env.get("PASSWORD_STORE_DIR")
        assert pwd_store_dir == Path(expected)

    def test_ignores_empty_ini_file_option(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ini = configparser.ConfigParser()

        expected = "/tmp/password-store-from-env"
        # `PASSWORD_STORE_DIR` in environment, empty `password_store_dir` in ini
        # file section -> empty ini value ignored, fall back to env. variable
        monkeypatch.setenv("PASSWORD_STORE_DIR", expected)
        ini["example.com"] = {"password_store_dir": ""}
        env, pwd_store_dir = passgithelper.compute_pass_environment(ini["example.com"])
        assert str(pwd_store_dir) == env.get("PASSWORD_STORE_DIR")
        assert pwd_store_dir == Path(expected)

    def test_ignores_empty_env_var_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        ) -> Text | None:
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
            entry_data=b"ignored",
            mock_co_expect_call=False,
        ),
    ],
    indirect=True,
)
@pytest.mark.usefixtures("helper_config")
def test_parse_mapping_file_missing() -> None:
    with pytest.raises(
        RuntimeError, match=r"No mapping configured so far at any XDG config location."
    ):
        _ = passgithelper.parse_mapping(None)


@pytest.mark.parametrize(
    "helper_config",
    [
        HelperConfig(
            xdg_dir="test_data/smoke",
            entry_data=b"ignored",
            mock_co_expect_call=False,
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
    def test_help(self, capsys: CapsysType) -> None:
        with pytest.raises(SystemExit, match=r"^0$"):
            passgithelper.main(["--help"])

        out, err = capsys.readouterr()
        assert "usage: " in out
        assert not err

    def test_skip(self, monkeypatch: pytest.MonkeyPatch, capsys: CapsysType) -> None:
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
                xdg_dir="test_data/smoke",
                request="""
protocol=https
host=mytest.com""",
                entry_data=b"narf",
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_smoke_resolve(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=narf\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                request="host=ignored",
                mock_co_expect_call=False,
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_unsupported_action_option(self, caplog: CaplogType) -> None:
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
                mock_co_expect_call=False,
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_request_without_host(self, capsys: CapsysType) -> None:
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
                mock_co_expect_call=False,
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
                xdg_dir="test_data/smoke",
                request="""
protocol=https
host=mytest.com
path=/foo/bar.git""",
                entry_data=b"ignored",
                mock_co_expect_call=False,
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_path_used_if_present_fails(self, capsys: CapsysType) -> None:
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
                xdg_dir="test_data/with-path",
                request="""
protocol=https
host=mytest.com
path=subpath/bar.git""",
                entry_data=b"narf",
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_path_used_if_present(self, capsys: CapsysType) -> None:
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
                mock_co_expect_call=False,
                err_expected="Unable to parse mapping file: File contains no section headers.",
            ),
        ],
        indirect=True,
    )
    def test_invalid_mapping_file(
        self, capsys: CapsysType, helper_config: HelperConfigAndMock
    ) -> None:
        with pytest.raises(SystemExit, match=r"^4$"):
            passgithelper.main(["get"])
        teardown_helper_capsys_checks(capsys, helper_config.test_params)

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/wildcard",
                request="""
protocol=https
host=wildcard.com
username=wildcard
path=subpath/bar.git""",
                entry_data=b"narf-wildcard",
                entry_name="dev/https/wildcard.com/wildcard",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_wildcard_matching(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=narf-wildcard\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/wildcard_path",
                request="""
protocol=https
host=path_wildcard.com
username=path_wildcard
path=subpath/bar.git""",
                entry_data=b"daniele-tentoni-path-wildcard",
                entry_name="dev/https/path_wildcard.com/path_wildcard/subpath/bar.git",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_wildcard_path_matching(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=daniele-tentoni-path-wildcard\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/with-username",
                request="""
host=plainline.com""",
                entry_data=b"password\nusername",
                entry_name="dev/plainline",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_username_provided(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=password\nusername=username\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/with-username",
                request="""
host=plainline.com
username=narf""",
                entry_data=b"password\nusername",
                entry_name="dev/plainline",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_username_skipped_if_provided(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=password\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/with-username",
                request="""
protocol=https
host=mytest.com""",
                entry_data=b"narf",
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_custom_mapping_used(self, capsys: CapsysType) -> None:
        # this would fail for the default file from with-username
        passgithelper.main(["-m", "test_data/smoke/git-pass-mapping.ini", "get"])

        out, err = capsys.readouterr()
        assert out == "password=narf\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/with-username-skip",
                request="""
protocol=https
host=mytest.com""",
                entry_data=b"password: xyz\nuser: tester",
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_prefix_skipping(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=xyz\nusername=tester\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/unknown-username-extractor",
                request="""
protocol=https
host=mytest.com""",
                entry_data=b"ignored",
                mock_co_expect_call=False,
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_select_unknown_username_extractor(self, capsys: CapsysType) -> None:
        with pytest.raises(SystemExit, match=r"^3$"):
            passgithelper.main(["get"])
        out, err = capsys.readouterr()
        assert not out
        assert "username_extractor of type 'doesntexist' does not exist" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/regex-username-extraction",
                request="""
protocol=https
host=mytest.com""",
                entry_data=b"xyz\nsomeline\nmyuser: tester\n morestuff\nmyuser: ignore",
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_regex_username_selection(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=xyz\nusername=tester\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/entry-name-extraction",
                request="""
protocol=https
host=mytest.com""",
                entry_data=b"xyz",
                entry_name="dev/mytest/myuser",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_entry_name_is_user(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=xyz\nusername=myuser\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/unknown-password-extractor",
                request="""
protocol=https
host=mytest.com""",
                entry_data=b"ignored",
                mock_co_expect_call=False,
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_select_unknown_password_extractor(self, capsys: CapsysType) -> None:
        with pytest.raises(SystemExit, match=r"^3$"):
            passgithelper.main(["get"])
        out, err = capsys.readouterr()
        assert not out
        assert "password_extractor of type 'doesntexist' does not exist" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                request="protocol=https\nhost=example.com",
                entry_data=b"secret\nmyuser\nmore text\n",
                entry_name="dev/mytest",
                out_expected="password=secret\nusername=myuser\n",
            ),
        ],
        indirect=True,
    )
    def test_empty_name_selects_default_password_extractor(
        self,
        mocker: MockerFixture,
        capsys: CapsysType,
        caplog: CaplogType,
        helper_config: HelperConfigAndMock,
    ) -> None:
        test_params = helper_config.test_params
        _ = setup_helper_parse_request_mock(mocker, test_params, password_extractor="")

        passgithelper.main(["-l", "get"])

        teardown_helper_capsys_checks(capsys, test_params, out_use_equals=True)

        assert (
            "root",
            logging.WARNING,
            "Mapping file contains empty 'password_extractor', please check!",
        ) in caplog.record_tuples

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/regex-password-extraction",
                request="""
protocol=https
host=mytest.com""",
                entry_data=b"xyz\nsomeline\nmyauth: mygreattoken\nmorestuff\nmyuser: tester\n",
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_regex_password_selection(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=mygreattoken\nusername=tester\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/with-encoding",
                request="""
protocol=https
host=mytest.com""",
                entry_data="täßt".encode("LATIN1"),
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_uses_configured_encoding(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=täßt\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/smoke",
                request="""
protocol=https
host=mytest.com""",
                entry_data="täßt".encode("UTF-8"),
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_uses_utf8_by_default(self, capsys: CapsysType) -> None:
        passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert out == "password=täßt\n"
        assert not err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/smoke",
                request="""
protocol=https
host=mytest.com""",
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_fails_gracefully_on_pass_errors(self, capsys: CapsysType) -> None:
        with pytest.raises(SystemExit, match=r"^3$"):
            passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert not out
        assert "Unable to retrieve" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/smoke",
                request="""
protocol=https
host=unknown""",
                entry_data="ignored".encode("UTF-8"),
                mock_co_expect_call=False,
            ),
        ],
        indirect=True,
    )
    @pytest.mark.usefixtures("helper_config")
    def test_fails_gracefully_on_missing_entries(self, capsys: CapsysType) -> None:
        with pytest.raises(SystemExit, match=r"^3$"):
            passgithelper.main(["get"])

        out, err = capsys.readouterr()
        assert not out
        assert "Unable to retrieve" in err

    @pytest.mark.parametrize(
        "helper_config",
        [
            HelperConfig(
                xdg_dir="test_data/password_store_dir",
                request="""
host=example.com""",
                entry_data="test".encode("UTF-8"),
                entry_name="dev/mytest",
            ),
        ],
        indirect=True,
    )
    def test_supports_switching_password_store_dirs(
        self, capsys: CapsysType, helper_config: HelperConfigAndMock
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
        _ = setup_helper_parse_request_mock(
            mocker, test_params, password_store_dir=pws_dir_expected
        )

        passgithelper.main(["get"])

        mock_co = cast("MockType", helper_config.mock_co)
        assert "env" in mock_co.call_args.kwargs
        env = cast("dict[str, str]", mock_co.call_args.kwargs["env"])
        assert "PASSWORD_STORE_DIR" in env
        password_store_dir = Path(env["PASSWORD_STORE_DIR"])
        assert password_store_dir.is_absolute()
        assert password_store_dir == Path(pws_dir_expected).expanduser()

    # test parametrization
    argvalues_ensure_password_is_file: ClassVar[list[HelperConfig]] = [
        # 1. pass_target pointing to valid (but unencrypted) password store file
        HelperConfig(
            entry_name="git/example.com/user1",
            entry_data=b"secret\nusername: user1\n",
            request="\nprotocol=https\nhost=example.com\npath=user1/repo.git\n",
            patch_ensure_password_is_file=False,
            out_expected="password=secret\nusername=user1\n",
        ),
        # 2. pass_target pointing to symlink to valid (but unencrypted) password
        #    store file
        HelperConfig(
            entry_name="git/example.com/user2",
            entry_data=b"secret\nusername: user1\n",
            request="\nprotocol=https\nhost=example.com\npath=user1/repo.git\n",
            patch_ensure_password_is_file=False,
            out_expected="password=secret\nusername=user1\n",
        ),
        # 3. pass_target pointing to non-existing entry
        HelperConfig(
            entry_name="git/example.com/doesnotexist",
            request="\nprotocol=https\nhost=example.com\n",
            patch_ensure_password_is_file=False,
            mock_co_expect_call=False,
            err_expected="/example.com/doesnotexist.gpg' does not exist",
        ),
        # 4. pass_target pointing to a directory
        HelperConfig(
            entry_name="git/example.com",
            request="\nprotocol=https\nhost=example.com\n",
            patch_ensure_password_is_file=False,
            mock_co_expect_call=False,
            err_expected="/git/example.com.gpg' does not exist",
        ),
        # 5. pass_target pointing to a directory `<dir>` with another directory
        #    `<dir>.gpg`
        HelperConfig(
            entry_name="git/github.com",
            request="\nprotocol=https\nhost=github.com\n",
            patch_ensure_password_is_file=False,
            mock_co_expect_call=False,
            err_expected="/git/github.com.gpg' is not a file",
        ),
        # 6. pass_target pointing to password store file with identically named
        #    directory
        HelperConfig(
            entry_name="git/gitlab.com",
            entry_data=b"secret\nusername: gluser\n",
            request="\nprotocol=https\nhost=gitlab.com\n",
            patch_ensure_password_is_file=False,
            out_expected="password=secret\nusername=gluser\n",
        ),
    ]

    @pytest.mark.parametrize(
        argnames="helper_config",
        argvalues=argvalues_ensure_password_is_file,
        indirect=True,
    )
    def test_function_ensure_password_is_file_with_mocked_pass_cli(
        self,
        mocker: MockerFixture,
        capsys: CapsysType,
        helper_config: HelperConfigAndMock,
    ) -> None:
        """Test function ``ensure_password_is_file(..)`` with mocked ``pass`` executable.

        The test runs ``passgithelper.main(..)`` while mocking sub-process calls
        to the ``pass`` executable. The filesystem checks of
        ``ensure_password_is_file(..)`` operate on ``test_data/dummy-password-store``.

        """
        test_params = helper_config.test_params
        password_store_dir = str(Path.cwd() / "test_data/dummy-password-store")

        _ = setup_helper_parse_request_mock(
            mocker,
            test_params,
            use_wildcard_section=True,
            username_extractor="regex_search",
            # make sure that ensure_password_is_file uses
            # test_data/dummy-password-store
            password_store_dir=password_store_dir,
        )

        with (
            pytest.raises(SystemExit, match=r"^3$")
            if test_params.err_expected
            else nullcontext()
        ):
            passgithelper.main(["get"])

        teardown_helper_capsys_checks(capsys, test_params)
