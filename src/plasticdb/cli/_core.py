"""The machinery every command shares: global options, output, errors and exit codes.

Commands are plain functions whose first parameter is the :class:`State`. The
:func:`command` decorator adds the global options (accepted before or after the
subcommand), ``--format`` / ``-o`` for commands that return data, filter options
generated from the SDK's ``TypedDict``\\ s, and the mapping from SDK exceptions
to documented exit codes. Commands return the SDK's result; the decorator
renders it.
"""

from __future__ import annotations

import enum
import functools
import inspect
import logging
import sys
import types
import typing
import warnings
from collections import abc
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar, Union

import typer

from .. import errors
from .._client import Client
from . import _config
from ._output import OutputError, format_for, render, write_file

F = TypeVar("F", bound=Callable[..., Any])

# ── exit codes ──────────────────────────────────────────────────────────────


class ExitCode(enum.IntEnum):
    OK = 0
    ERROR = 1
    USAGE = 2
    NOT_FOUND = 3
    AUTH = 4
    RATE_LIMITED = 5
    RELEASE = 6
    JOB_FAILED = 7
    TIMEOUT = 8
    UNAVAILABLE = 9
    INTERRUPTED = 130


EXIT_CODES: tuple[tuple[ExitCode, str], ...] = (
    (ExitCode.OK, "success"),
    (ExitCode.ERROR, "any other error"),
    (ExitCode.USAGE, "bad command line, option or parameter value (also a server 422)"),
    (ExitCode.NOT_FOUND, "no such record, or it was withdrawn or merged"),
    (ExitCode.AUTH, "no API key, an invalid one, or not allowed"),
    (ExitCode.RATE_LIMITED, "rate limited (429) beyond the retry budget"),
    (ExitCode.RELEASE, "release unknown, unavailable, or unable to answer this"),
    (ExitCode.JOB_FAILED, "the job failed on the server"),
    (ExitCode.TIMEOUT, "timed out (a request, or --timeout while waiting for a job)"),
    (ExitCode.UNAVAILABLE, "server unreachable or failing (5xx): retry later"),
    (ExitCode.INTERRUPTED, "interrupted (Ctrl-C)"),
)


def exit_code_epilog() -> str:
    lines = [f"  {int(code):>3}  {text}" for code, text in EXIT_CODES]
    return "Exit codes:\n\n\b\n" + "\n".join(lines)


def classify(exc: BaseException) -> ExitCode:
    """The exit code for an exception (order matters: some classes have two parents)."""
    if isinstance(exc, errors.ReleaseError):
        return ExitCode.RELEASE
    if isinstance(exc, errors.JobFailedError):
        return ExitCode.JOB_FAILED
    if isinstance(exc, (errors.JobTimeoutError, errors.APITimeoutError)):
        return ExitCode.TIMEOUT
    if isinstance(exc, (errors.AuthenticationError, errors.PermissionDenied)):
        return ExitCode.AUTH
    if isinstance(exc, errors.RateLimitError):
        return ExitCode.RATE_LIMITED
    if isinstance(exc, (errors.NotFoundError, errors.GoneError)):
        return ExitCode.NOT_FOUND
    if isinstance(
        exc,
        (
            errors.ValidationError,
            errors.BadRequestError,
            errors.PayloadTooLargeError,
            errors.EngineeredVariantError,
            TypeError,
            ValueError,
        ),
    ):
        return ExitCode.USAGE
    if isinstance(exc, (errors.APIConnectionError, errors.ServerError)):
        return ExitCode.UNAVAILABLE
    return ExitCode.ERROR


def describe(exc: BaseException) -> str:
    """The error message, with what the user can do about it."""
    text = str(exc) or type(exc).__name__
    if isinstance(exc, errors.MergedError):
        text += f" (merged into {exc.merged_into}; pass --follow-merges to get it)"
    elif isinstance(exc, errors.AuthenticationError):
        text += " (run `plasticdb auth login`, or set PLASTICDB_API_KEY)"
    elif isinstance(exc, errors.UnsupportedFilterError) and exc.parameters:
        text += f" (not answerable by this release: {', '.join(exc.parameters)})"
    elif isinstance(exc, errors.ValidationError) and exc.errors:
        problems = [
            f"{'.'.join(str(p) for p in (e.get('loc') or [])[1:]) or '?'}: {e.get('msg')}"
            for e in exc.errors
        ]
        text += " (" + "; ".join(problems) + ")"
    elif (
        isinstance(exc, (errors.RateLimitError, errors.ServiceUnavailableError)) and exc.retry_after
    ):
        text += f" (retry after {exc.retry_after:g} s)"
    return text


# ── state ───────────────────────────────────────────────────────────────────


@dataclass
class State:
    """The global options, and the client built from them."""

    api_key: str | None = None
    release: str | None = None
    base_url: str | None = None
    cache_dir: str | None = None
    quiet: bool = False
    verbose: int = 0
    interactive: bool = False
    _client: Client | None = field(default=None, repr=False)

    def merge(self, values: Mapping[str, Any]) -> None:
        """Options given after the subcommand win over those given before it."""
        for name, value in values.items():
            if name == "verbose":
                self.verbose += int(value or 0)
            elif value not in (None, False):
                setattr(self, name, value)

    def key(self) -> tuple[str | None, str]:
        """The API key and where it came from: option, environment, config file or none."""
        if self.api_key and self.api_key.strip():
            return self.api_key.strip(), "--api-key"
        env = (_env("PLASTICDB_API_KEY") or "").strip()
        if env:
            return env, "PLASTICDB_API_KEY"
        try:
            stored = _config.stored_key()
        except ValueError as exc:
            self.warn(str(exc))
            stored = None
        if stored:
            path = _config.config_path()
            if _config.too_open(path):
                self.warn(f"{path} is readable by other users; run: chmod 600 {path}")
            return stored, str(path)
        return None, "none"

    @property
    def client(self) -> Client:
        if self._client is None:
            key, _ = self.key()
            self._client = Client(
                api_key=key,
                release=self.release,
                base_url=self.base_url,
                cache_dir=self.cache_dir,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def say(self, message: str) -> None:
        """A message for the user (stderr), unless ``--quiet``."""
        if not self.quiet:
            typer.echo(message, err=True)

    def warn(self, message: str) -> None:
        if not self.quiet:
            typer.echo(f"warning: {message}", err=True)


def _env(name: str) -> str | None:
    import os

    return os.environ.get(name)


def root_state(ctx: typer.Context) -> State:
    root = ctx.find_root()
    if not isinstance(root.obj, State):
        root.obj = State()
    return root.obj


# ── option building ─────────────────────────────────────────────────────────

GLOBAL_PANEL = "Global options"
FILTER_PANEL = "Filters"
OUTPUT_PANEL = "Output"

KW = inspect.Parameter.KEYWORD_ONLY


def _global_params() -> list[inspect.Parameter]:
    def opt(name: str, annotation: Any, default: Any) -> inspect.Parameter:
        return inspect.Parameter(name, KW, default=default, annotation=annotation)

    return [
        opt(
            "g_release",
            Annotated[
                str | None,
                typer.Option(
                    "--release",
                    metavar="VERSION",
                    help="Pin every read to a dataset release (env PLASTICDB_RELEASE).",
                    rich_help_panel=GLOBAL_PANEL,
                ),
            ],
            None,
        ),
        opt(
            "g_api_key",
            Annotated[
                str | None,
                typer.Option(
                    "--api-key",
                    metavar="KEY",
                    help="API key (env PLASTICDB_API_KEY, else `plasticdb auth login`'s).",
                    rich_help_panel=GLOBAL_PANEL,
                ),
            ],
            None,
        ),
        opt(
            "g_base_url",
            Annotated[
                str | None,
                typer.Option(
                    "--base-url",
                    metavar="URL",
                    help="API base URL (env PLASTICDB_BASE_URL).",
                    rich_help_panel=GLOBAL_PANEL,
                ),
            ],
            None,
        ),
        opt(
            "g_cache_dir",
            Annotated[
                str | None,
                typer.Option(
                    "--cache-dir",
                    metavar="DIR",
                    help="Keep pinned answers and release files here (env PLASTICDB_CACHE_DIR).",
                    rich_help_panel=GLOBAL_PANEL,
                ),
            ],
            None,
        ),
        opt(
            "g_quiet",
            Annotated[
                bool,
                typer.Option(
                    "--quiet",
                    "-q",
                    help="Only errors on stderr.",
                    rich_help_panel=GLOBAL_PANEL,
                ),
            ],
            False,
        ),
        opt(
            "g_verbose",
            Annotated[
                int,
                typer.Option(
                    "--verbose",
                    "-v",
                    count=True,
                    help="More detail on stderr (-vv: every request and retry).",
                    rich_help_panel=GLOBAL_PANEL,
                ),
            ],
            0,
        ),
        opt(
            "g_interactive",
            Annotated[
                bool,
                typer.Option(
                    "--interactive",
                    help="Allow prompts (only `auth login` ever asks).",
                    rich_help_panel=GLOBAL_PANEL,
                ),
            ],
            False,
        ),
    ]


GLOBAL_NAMES = {p.name: p.name[2:] for p in _global_params()}


def choice_enum(name: str, values: Iterable[str]) -> type[enum.Enum]:
    """A ``str`` Enum, which Typer shows and checks as a choice."""
    members = [(v, v) for v in values]
    return enum.Enum(name, members, type=str)  # type: ignore[return-value]


def _unwrap_optional(tp: Any) -> Any:
    origin = typing.get_origin(tp)
    if origin is Union or (hasattr(types, "UnionType") and origin is types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        return Union[tuple(args)]  # noqa: UP007
    return tp


def cli_type(tp: Any, name: str) -> tuple[Any, bool]:
    """A Python annotation as something Typer takes: ``(type, is_flag)``.

    ``Literal``s become choices, a union (``str | int``, ``datetime | str``) a
    string, a sequence a repeatable option.
    """
    tp = _unwrap_optional(tp)
    origin = typing.get_origin(tp)
    if origin is Literal:
        return choice_enum(_camel(name), [str(a) for a in typing.get_args(tp)]), False
    if tp is bool:
        return bool, True
    if tp in (int, float, str):
        return tp, False
    if origin is not None and isinstance(origin, type) and issubclass(origin, abc.Sequence):
        args = typing.get_args(tp)
        inner, _ = cli_type(args[0], name) if args else (str, False)
        return list[inner], False  # type: ignore[valid-type]
    if tp is datetime:
        return str, False
    return str, False


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.strip("_").split("_")) or "Choice"


def option_name(key: str) -> str:
    return "--" + key.rstrip("_").replace("_", "-")


def filter_params(
    hints: Mapping[str, Any],
    *,
    prefix: str = "f__",
    panel: str = FILTER_PANEL,
    help: Mapping[str, str] | None = None,
) -> list[inspect.Parameter]:
    """One option per filter, named as in the SDK (``class_`` is ``--class``)."""
    out = []
    for key, tp in hints.items():
        kind, flag = cli_type(tp, key)
        decls = [option_name(key)]
        if flag:
            negative = "--no-" + key.rstrip("_").replace("_", "-")
            decls = [f"{decls[0]}/{negative}"]
        if key == "q":
            decls.append("--query")
        text = (help or {}).get(key) or FILTER_HELP.get(key, "")
        annotation = annotated(
            kind | None,
            typer.Option(*decls, help=text, rich_help_panel=panel, show_default=False),
        )
        out.append(inspect.Parameter(prefix + key, KW, default=None, annotation=annotation))
    return out


def typed_dict_hints(*schemas: Any, exclude: Iterable[str] = ()) -> dict[str, Any]:
    """The keys of one or more TypedDicts with their types (the first schema wins)."""
    skip = set(exclude)
    out: dict[str, Any] = {}
    for schema in schemas:
        for key, tp in typing.get_type_hints(schema).items():
            if key not in skip and key not in out:
                out[key] = tp
    return out


FILTER_HELP = {
    "q": "Free-text search.",
    "name": "Name contains.",
    "plastic": "Plastic name or abbreviation (e.g. PET).",
    "include_subtypes": "Include the plastic's subtypes.",
    "microorganism": "Microorganism tax-id, accession or name.",
    "polarity": "Evidence outcome (default positive).",
    "include_negative": "Also list records with only negative results.",
    "evidence_method": "How degradation was shown.",
    "min_year": "Evidence published in or after this year.",
    "max_year": "Evidence published in or before this year.",
    "year": "Evidence published in this year.",
    "updated_since": "Changed at or after this ISO 8601 time.",
    "sort_by": "Sort key.",
    "sort_order": "Sort direction.",
    "kind": "Wild types, engineered variants or both.",
    "lineage": "Lineage contains (any rank).",
    "class_": "Taxonomic class.",
    "evidence_scope": "Count enzyme assays, organism assays, or both.",
    "has_structure": "Only records with (or without) a structure.",
    "has_sequence": "Only records with (or without) a sequence.",
    "protein": "Protein accession.",
    "doi": "Paper DOI.",
}


# ── the decorator ───────────────────────────────────────────────────────────


def command(
    group: typer.Typer,
    name: str,
    *,
    formats: tuple[str, ...] | None = None,
    default_format: str | None = None,
    single: bool = False,
    filters: Mapping[str, Any] | None = None,
    extra: list[inspect.Parameter] | None = None,
    choices: Mapping[str, Iterable[str]] | None = None,
    help: str | None = None,
) -> Callable[[F], F]:
    """Register ``func`` as ``group``'s ``name`` command.

    * ``formats``: add ``--format`` (and ``-o`` unless ``func`` takes ``output``);
      the returned value is rendered in it. ``single``: JSON is the object.
    * ``filters``: ``{name: type}``; one option each, passed to ``func`` as the
      ``filters`` dict (only those given).
    * ``extra``: more generated parameters (named ``x__<name>``), passed as ``extra``.
    * ``choices``: restrict ``func``'s own ``str`` options to these values.
    """

    def decorate(func: F) -> F:
        sig = inspect.signature(func)
        hints = typing.get_type_hints(func, include_extras=True)
        own = list(sig.parameters.values())[1:]  # drop the State
        wants_filters = "filters" in sig.parameters
        wants_extra = "extra" in sig.parameters
        wants_output = "output" in sig.parameters
        wants_fmt = "fmt" in sig.parameters

        params: list[inspect.Parameter] = [
            inspect.Parameter(
                "_ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=typer.Context
            )
        ]
        for p in own:
            if p.name in {"filters", "extra"}:
                continue
            annotation = _strip_implicit_optional(hints.get(p.name, p.annotation))
            if choices and p.name in choices:
                annotation = _with_choices(annotation, p.name, choices[p.name], p.default)
            # Keyword-only, so a defaulted option may precede a required argument.
            params.append(p.replace(annotation=annotation, kind=KW))

        if formats is not None:
            fmt_enum = choice_enum(f"{_camel(name)}Format", formats)
            if not wants_fmt:
                params.append(
                    inspect.Parameter(
                        "fmt",
                        KW,
                        default=None,
                        annotation=Annotated[
                            fmt_enum | None,
                            typer.Option(
                                "--format",
                                help=f"Output format (default {default_format}, "
                                "or the -o file's extension).",
                                rich_help_panel=OUTPUT_PANEL,
                                show_default=False,
                            ),
                        ],
                    )
                )
            if not wants_output:
                params.append(
                    inspect.Parameter(
                        "output",
                        KW,
                        default=None,
                        annotation=Annotated[
                            str | None,
                            typer.Option(
                                "--output",
                                "-o",
                                metavar="FILE",
                                help="Write the data here instead of stdout.",
                                rich_help_panel=OUTPUT_PANEL,
                            ),
                        ],
                    )
                )
        if filters:
            params.extend(filter_params(filters))
        params.extend(extra or [])
        params.extend(_global_params())

        @functools.wraps(func)
        def wrapper(**kwargs: Any) -> None:
            ctx: typer.Context = kwargs.pop("_ctx")
            state = root_state(ctx)
            state.merge({GLOBAL_NAMES[k]: kwargs.pop(k) for k in list(kwargs) if k in GLOBAL_NAMES})
            call: dict[str, Any] = {}
            filter_values: dict[str, Any] = {}
            extra_values: dict[str, Any] = {}
            for key, value in kwargs.items():
                value = _plain(value)
                if key.startswith("f__"):
                    if value is not None and value != []:
                        filter_values[key[3:]] = value
                elif key.startswith("x__"):
                    if value is not None and value != []:
                        extra_values[key[3:]] = value
                else:
                    call[key] = value
            fmt = call.pop("fmt", None) if not wants_fmt else call.get("fmt")
            output = call.get("output") if wants_output else call.pop("output", None)
            if wants_filters:
                call["filters"] = filter_values
            if wants_extra:
                call["extra"] = extra_values
            run(state, lambda: func(state, **call), formats, default_format, fmt, output, single)

        wrapper.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
        wrapper.__annotations__ = {p.name: p.annotation for p in params}
        del wrapper.__wrapped__
        group.command(name, help=help or inspect.getdoc(func))(wrapper)
        return func

    return decorate


def annotated(base: Any, *meta: Any) -> Any:
    """``Annotated[base, *meta]`` for a type built at runtime."""
    return Annotated[tuple([base, *meta])]


def _strip_implicit_optional(annotation: Any) -> Any:
    """Undo Python 3.10's ``get_type_hints``, which wraps ``Annotated[...] = None`` in Optional."""
    if typing.get_origin(annotation) is Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1 and typing.get_origin(args[0]) is Annotated:
            return args[0]
    return annotation


def _with_choices(annotation: Any, name: str, values: Iterable[str], default: Any) -> Any:
    kind = choice_enum(_camel(name), values)
    if typing.get_origin(annotation) is Annotated:
        _, *meta = typing.get_args(annotation)
        new_base = kind | None if default is None else kind
        return annotated(new_base, *meta)
    return kind | None if default is None else kind


def _plain(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


# ── running a command ───────────────────────────────────────────────────────


class _StderrHandler(logging.Handler):
    """Logs to whatever ``sys.stderr`` is now (the test runner swaps it)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            typer.echo(f"plasticdb: {record.getMessage()}", err=True)
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)


def run(
    state: State,
    call: Callable[[], Any],
    formats: tuple[str, ...] | None,
    default_format: str | None,
    fmt: str | None,
    output: str | None,
    single: bool,
) -> None:
    """Call the command, render its result, and turn exceptions into exit codes."""
    logger = logging.getLogger("plasticdb")
    handler = _StderrHandler()
    level = (
        logging.ERROR
        if state.quiet
        else (logging.WARNING, logging.INFO, logging.DEBUG)[min(state.verbose, 2)]
    )
    handler.setLevel(level)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    if state.cache_dir is None:
        state.cache_dir = _env("PLASTICDB_CACHE_DIR") or None
    code = ExitCode.OK
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                result = call()
                if result is not None:
                    chosen = fmt or format_for(output, formats or (), default_format or "json")
                    text = render(result, chosen, single=single)
                    if output:
                        path = write_file(output, text)
                        state.say(f"Wrote {path}")
                    else:
                        _stdout(text)
            finally:
                for w in caught:
                    state.warn(str(w.message))
    except (typer.Exit, typer.Abort):
        raise
    except KeyboardInterrupt:
        typer.echo("plasticdb: interrupted", err=True)
        code = ExitCode.INTERRUPTED
    except OutputError as exc:
        typer.echo(f"plasticdb: error: {exc}", err=True)
        code = ExitCode.USAGE
    except (errors.PlasticDBError, TypeError, ValueError) as exc:
        code = classify(exc)
        typer.echo(f"plasticdb: error: {describe(exc)}", err=True)
    except OSError as exc:
        typer.echo(f"plasticdb: error: {exc}", err=True)
        code = ExitCode.ERROR
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        state.close()
    if code:
        raise typer.Exit(int(code))


def _stdout(text: str) -> None:
    try:
        typer.echo(text, nl=False)
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader went away (`| head`): that is not an error. Point stdout at
        # devnull so the interpreter's final flush does not complain either.
        import os

        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            pass
