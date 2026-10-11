# -*- coding: utf-8 -*-
"""Blank comments out of a unified diff in place. Never delete lines.

Lens-phase consumer of a *copy* of the prepared diff. The original
``review_full_diff`` is left untouched so verify, findings.md, and
summary.json line citations still map to the real files. A line-number
shift here would make every finding cite the wrong line in the report
a human reads, and would make ``post_review_comment.py`` post inline
PR comments on the wrong lines.

The plugin itself asserts equal per-file line counts (and equal total
line count) before returning. Callers must still assert in the
playbook: a unit test is not a substitute for the pipeline invariant.
"""
from __future__ import annotations

from ansible.plugins.action import ActionBase

_DIFF_FILE_PREFIX = "diff --git "


def _split_keepends(text: str) -> list[str]:
    if text == "":
        return []
    return text.splitlines(keepends=True)


def _line_parts(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _is_diff_file_header(line: str) -> bool:
    body, _ = _line_parts(line)
    return body.startswith(_DIFF_FILE_PREFIX)


def _is_hunk_header(line: str) -> bool:
    body, _ = _line_parts(line)
    return body.startswith("@@")


def _is_hunk_content_line(line: str) -> bool:
    """Added, removed, or context. Caller must already be inside a hunk.

    An added source line that begins with ``++ `` is serialized as
    ``+++ ``, which is also how file headers look. Prefix alone cannot
    tell those apart; in-hunk state can.
    """
    body, _ = _line_parts(line)
    return body[:1] in ("+", "-", " ")


def _strip_ab_prefix(path: str) -> str:
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


_GIT_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _parse_git_quoted_string(s: str, start: int) -> tuple[str, int]:
    """C-style quoted git path starting at ``s[start] == '"'``."""
    n = len(s)
    i = start + 1
    out: list[str] = []
    while i < n:
        c = s[i]
        if c == '"':
            return "".join(out), i + 1
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt in _GIT_SIMPLE_ESCAPES:
                out.append(_GIT_SIMPLE_ESCAPES[nxt])
                i += 2
                continue
            if nxt in "01234567":
                j = i + 1
                digits: list[str] = []
                while j < n and len(digits) < 3 and s[j] in "01234567":
                    digits.append(s[j])
                    j += 1
                out.append(chr(int("".join(digits), 8)))
                i = j
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out), n


def _parse_git_path_token(s: str, start: int = 0) -> tuple[str, int]:
    n = len(s)
    i = start
    while i < n and s[i] == " ":
        i += 1
    if i >= n:
        return "", i
    if s[i] == '"':
        return _parse_git_quoted_string(s, i)
    j = i
    while j < n and s[j] not in " \t":
        j += 1
    return s[i:j], j


def _path_from_ab_file_header(line: str, marker: str) -> str | None:
    """Path from ``+++ ...`` / ``--- ...``. ``/dev/null`` is ``""``.

    Call only on pre-hunk file headers. Unquoted ``b/path with spaces``
    keeps the remainder after the ``a/`` or ``b/`` prefix.
    """
    body, _ = _line_parts(line)
    if not body.startswith(marker):
        return None
    rest = body[len(marker) :]
    if rest in ("/dev/null", '"/dev/null"'):
        return ""
    if rest.startswith('"'):
        token, _ = _parse_git_path_token(rest, 0)
        if token in ("/dev/null",):
            return ""
        return _strip_ab_prefix(token)
    if rest.startswith("a/") or rest.startswith("b/"):
        return rest[2:]
    return rest


def _path_from_plus_plus_line(line: str) -> str | None:
    """New-file path from ``+++ b/path``. Prefer this over ``diff --git``."""
    return _path_from_ab_file_header(line, "+++ ")


def _path_from_minus_minus_line(line: str) -> str | None:
    """Old-file path from ``--- a/path``. Used when ``+++`` is ``/dev/null``."""
    return _path_from_ab_file_header(line, "--- ")


def _paths_from_diff_header(header_body: str) -> tuple[str, str]:
    """``diff --git a/<old> b/<new>``, including quoted paths with spaces."""
    rest = header_body[len(_DIFF_FILE_PREFIX) :]
    first, idx = _parse_git_path_token(rest, 0)
    second, _ = _parse_git_path_token(rest, idx)
    old = _strip_ab_prefix(first) if first and first != "/dev/null" else ""
    new = _strip_ab_prefix(second) if second and second != "/dev/null" else ""
    return old, new


def language_for_path(path: str) -> str:
    lower = path.lower()
    jinja = lower.endswith((".j2", ".jinja", ".jinja2"))
    base = lower
    if jinja:
        for suffix in (".j2", ".jinja2", ".jinja"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
    if base.endswith((".py", ".pyi")):
        lang = "python"
    elif base.endswith((".yml", ".yaml")):
        lang = "yaml"
    elif base.endswith((".md", ".markdown")):
        lang = "markdown"
    elif jinja:
        lang = "jinja"
    else:
        lang = "unknown"
    # Ansible YAML is Jinja-templated even without a .j2 suffix.
    # Markdown and ordinary Python can carry `{# #}` the same way.
    # Python Jinja comments are applied inside the Python lexer so
    # `{# #}` inside string literals is kept.
    if lang in ("yaml", "markdown", "python"):
        return f"{lang}+jinja"
    if jinja and lang != "jinja":
        return f"{lang}+jinja"
    return lang


class _ScreenState:
    __slots__ = (
        "py_triple",
        "py_triple_is_docstring",
        "py_quote",
        "jinja_comment",
        "html_comment",
        "yaml_single",
        "yaml_double",
        "yaml_block",
        "yaml_block_header_indent",
        "yaml_block_content_indent",
    )

    def __init__(self) -> None:
        self.py_triple: str | None = None
        self.py_triple_is_docstring = False
        self.py_quote: str | None = None
        self.jinja_comment = False
        self.html_comment = False
        self.yaml_single = False
        self.yaml_double = False
        self.yaml_block = False
        self.yaml_block_header_indent = 0
        self.yaml_block_content_indent: int | None = None


def _python_docstring_prefix_ok(prefix: str) -> bool:
    # A docstring is a triple-quoted string that is the first statement
    # on the line, optionally with a string prefix (r/u/f/b and the
    # usual two-letter combinations). Assignments and call args are not.
    s = prefix.strip()
    if s == "":
        return True
    return s.lower() in {"r", "u", "ur", "ru"}


def _emit_python_string(content: str, i: int, state: _ScreenState, out: list[str]) -> int:
    """Scan an ordinary (non-triple) Python string starting at ``i``.

    Persists ``state.py_quote`` across physical lines only when the line
    ends with an odd number of backslashes (backslash-newline continuation).
    An unclosed string without continuation does not poison the next line.
    """
    n = len(content)
    quote = state.py_quote
    if quote is None:
        return i
    while i < n:
        c = content[i]
        out.append(c)
        i += 1
        if c == "\\":
            if i < n:
                out.append(content[i])
                i += 1
            continue
        if c == quote:
            state.py_quote = None
            break
    else:
        trail = 0
        j = len(out) - 1
        while j >= 0 and out[j] == "\\":
            trail += 1
            j -= 1
        if trail % 2 == 0:
            state.py_quote = None
    return i


def _emit_python_triple_char(ch: str, state: _ScreenState, screen_docstrings: bool, out: list[str]) -> None:
    if screen_docstrings and state.py_triple_is_docstring:
        out.append(" " if ch != "\t" else "\t")
    else:
        out.append(ch)


def _screen_python_line(content: str, state: _ScreenState, screen_docstrings: bool) -> str:
    out: list[str] = []
    i = 0
    n = len(content)
    while i < n:
        if state.py_triple:
            closer = state.py_triple
            # Python deletes a backslash-newline before the next physical
            # line is tokenized. Do not treat that next line's first
            # character as escaped, or a closing quote stays inside the
            # string and later comments are never screened.
            if content[i] == "\\":
                _emit_python_triple_char("\\", state, screen_docstrings, out)
                i += 1
                if i < n:
                    _emit_python_triple_char(content[i], state, screen_docstrings, out)
                    i += 1
                continue
            if content.startswith(closer, i):
                if screen_docstrings and state.py_triple_is_docstring:
                    out.append(" " * len(closer))
                else:
                    out.append(closer)
                i += len(closer)
                state.py_triple = None
                state.py_triple_is_docstring = False
                continue
            _emit_python_triple_char(content[i], state, screen_docstrings, out)
            i += 1
            continue

        if state.py_quote:
            i = _emit_python_string(content, i, state, out)
            continue

        if state.jinja_comment:
            if content.startswith("#}", i):
                out.append("  ")
                i += 2
                state.jinja_comment = False
                continue
            out.append(" " if content[i] != "\t" else "\t")
            i += 1
            continue

        if content.startswith("{#", i):
            out.append("  ")
            i += 2
            state.jinja_comment = True
            continue

        if content.startswith(('"""', "'''"), i):
            quote = content[i : i + 3]
            prefix = "".join(out)
            is_doc = _python_docstring_prefix_ok(prefix)
            state.py_triple = quote
            state.py_triple_is_docstring = is_doc
            if screen_docstrings and is_doc:
                out.append(" " * 3)
            else:
                out.append(quote)
            i += 3
            continue

        ch = content[i]
        if ch in ("'", '"'):
            state.py_quote = ch
            out.append(ch)
            i += 1
            i = _emit_python_string(content, i, state, out)
            continue

        if ch == "#":
            rest = content[i:]
            out.append(" " * len(rest))
            break

        out.append(ch)
        i += 1
    return "".join(out)


def _yaml_skip_ws(content: str, i: int) -> int:
    n = len(content)
    while i < n and content[i] in " \t":
        i += 1
    return i


def _yaml_scan_quoted(content: str, i: int, quote: str) -> int | None:
    """Index just after a closing quote, or None when the quote never closes."""
    n = len(content)
    i += 1
    if quote == "'":
        while i < n:
            if content[i] == "'":
                if i + 1 < n and content[i + 1] == "'":
                    i += 2
                    continue
                return i + 1
            i += 1
        return None
    while i < n:
        if content[i] == "\\":
            i += 2 if i + 1 < n else 1
            continue
        if content[i] == '"':
            return i + 1
        i += 1
    return None


def _yaml_try_key(content: str, i: int) -> int | None:
    """Index after ``key:`` and its trailing spaces, or None.

    A quoted key may contain colons. A plain key stops at the first colon.
    """
    n = len(content)
    if i >= n:
        return None
    if content[i] in "'\"":
        end = _yaml_scan_quoted(content, i, content[i])
        if end is None or end >= n or content[end] != ":":
            return None
        return _yaml_skip_ws(content, end + 1)
    if content[i] in ":#":
        return None
    j = i + 1
    while j < n and content[j] != ":":
        j += 1
    if j >= n or content[j] != ":":
        return None
    return _yaml_skip_ws(content, j + 1)


def _yaml_try_tag_or_anchor(content: str, i: int) -> int | None:
    """Index after one ``!name`` / ``!!name`` / ``&name`` token, or None.

    Names exclude the marker characters, and at most two tokens are
    accepted by the caller, so a run of ``!&`` cannot backtrack.
    """
    n = len(content)
    if i >= n:
        return None
    if content[i] == "&":
        j = i + 1
        if j >= n or content[j].isspace() or content[j] in "!&":
            return None
        while j < n and not content[j].isspace() and content[j] not in "!&":
            j += 1
        return j
    if content[i] != "!":
        return None
    j = i + 1
    if j < n and content[j] == "!":
        j += 1
    if j >= n or content[j].isspace() or content[j] == "!":
        return None
    while j < n and not content[j].isspace() and content[j] != "!":
        j += 1
    return j


def _yaml_rest_is_block_indicator(content: str, i: int) -> bool:
    """True when ``content[i:]`` is ``|`` or ``>`` plus an optional chomp/indent."""
    n = len(content)
    if i >= n or content[i] not in ">|":
        return False
    i += 1
    if i < n and content[i] in "+-":
        i += 1
        while i < n and content[i].isdigit():
            i += 1
    elif i < n and content[i].isdigit():
        while i < n and content[i].isdigit():
            i += 1
        if i < n and content[i] in "+-":
            i += 1
    i = _yaml_skip_ws(content, i)
    if i < n and content[i] == "#":
        return True
    return i >= n


def _is_yaml_block_header(content: str) -> bool:
    """True for a block-scalar header, including a quoted key that contains ``:``.

    One forward scan. Sequence markers, the key, and at most two tag or
    anchor tokens are consumed without retrying earlier choices.
    """
    i = _yaml_skip_ws(content, 0)
    n = len(content)
    saw_sequence = False
    while i + 1 < n and content[i] == "-" and content[i + 1] == " ":
        i = _yaml_skip_ws(content, i + 2)
        saw_sequence = True
    key_end = _yaml_try_key(content, i)
    if key_end is None:
        if not saw_sequence:
            return False
    else:
        i = key_end
    for _ in range(2):
        token_end = _yaml_try_tag_or_anchor(content, i)
        if token_end is None:
            break
        i = _yaml_skip_ws(content, token_end)
    return _yaml_rest_is_block_indicator(content, i)


def _yaml_leading_ws(content: str) -> int:
    i = 0
    n = len(content)
    while i < n and content[i] in " \t":
        i += 1
    return i


def _yaml_in_block_content(content: str, state: _ScreenState) -> bool:
    """True when this line is still inside a `|` / `>` scalar (data, not comments)."""
    stripped = content.lstrip(" \t")
    if stripped == "":
        return True
    indent = _yaml_leading_ws(content)
    if state.yaml_block_content_indent is None:
        if indent <= state.yaml_block_header_indent:
            return False
        state.yaml_block_content_indent = indent
        return True
    return indent >= state.yaml_block_content_indent


def _screen_yaml_flow_line(content: str, state: _ScreenState) -> str:
    # YAML comments: `#` at column 0 or preceded by whitespace, outside quotes
    # and outside block scalars (handled by the caller).
    out: list[str] = []
    i = 0
    n = len(content)
    while i < n:
        ch = content[i]
        if state.yaml_single:
            out.append(ch)
            i += 1
            if ch == "'":
                if i < n and content[i] == "'":
                    out.append("'")
                    i += 1
                else:
                    state.yaml_single = False
            continue
        if state.yaml_double:
            out.append(ch)
            i += 1
            if ch == "\\" and i < n:
                out.append(content[i])
                i += 1
                continue
            if ch == '"':
                state.yaml_double = False
            continue
        if ch == "'":
            state.yaml_single = True
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            state.yaml_double = True
            out.append(ch)
            i += 1
            continue
        if ch == "#" and (i == 0 or content[i - 1].isspace()):
            out.append(" " * (n - i))
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _screen_yaml_line(content: str, state: _ScreenState) -> str:
    in_quotes = state.yaml_single or state.yaml_double
    if not in_quotes and state.yaml_block:
        if _yaml_in_block_content(content, state):
            return content
        state.yaml_block = False
        state.yaml_block_content_indent = None
    screened = _screen_yaml_flow_line(content, state)
    if not (state.yaml_single or state.yaml_double) and _is_yaml_block_header(content):
        state.yaml_block = True
        state.yaml_block_header_indent = _yaml_leading_ws(content)
        state.yaml_block_content_indent = None
    return screened


def _screen_jinja_line(content: str, state: _ScreenState) -> str:
    out: list[str] = []
    i = 0
    n = len(content)
    while i < n:
        if state.jinja_comment:
            if content.startswith("#}", i):
                out.append("  ")
                i += 2
                state.jinja_comment = False
                continue
            out.append(" " if content[i] != "\t" else "\t")
            i += 1
            continue
        if content.startswith("{#", i):
            out.append("  ")
            i += 2
            state.jinja_comment = True
            continue
        out.append(content[i])
        i += 1
    return "".join(out)


def _screen_markdown_line(content: str, state: _ScreenState) -> str:
    out: list[str] = []
    i = 0
    n = len(content)
    while i < n:
        if state.html_comment:
            if content.startswith("-->", i):
                out.append("   ")
                i += 3
                state.html_comment = False
                continue
            out.append(" " if content[i] != "\t" else "\t")
            i += 1
            continue
        if content.startswith("<!--", i):
            out.append("    ")
            i += 4
            state.html_comment = True
            continue
        out.append(content[i])
        i += 1
    return "".join(out)


_SCREEN_LANG_ORDER = ("jinja", "markdown", "python", "yaml")


def screen_content_line(content: str, languages: list[str], state: _ScreenState, screen_docstrings: bool) -> str:
    """Mask comments per language. Jinja/HTML run before YAML so `{# #}`
    cannot poison YAML quotes. Python applies `{# #}` itself outside
    strings, so the standalone Jinja pass is skipped for Python files.
    """
    text = content
    active = set(languages)
    if "python" in active:
        active.discard("jinja")
    for lang in _SCREEN_LANG_ORDER:
        if lang not in active:
            continue
        if lang == "python":
            text = _screen_python_line(text, state, screen_docstrings)
        elif lang == "yaml":
            text = _screen_yaml_line(text, state)
        elif lang == "jinja":
            text = _screen_jinja_line(text, state)
        elif lang == "markdown":
            text = _screen_markdown_line(text, state)
    return text


def _languages_for(lang: str) -> list[str]:
    if "+" in lang:
        return [part for part in lang.split("+") if part != "unknown"]
    if lang == "unknown":
        return []
    return [lang]


def _merge_comment_masks(original: str, new_screened: str, old_screened: str) -> str:
    """Blank a character if either stream treated it as comment text."""
    if not (len(original) == len(new_screened) == len(old_screened)):
        return new_screened
    out: list[str] = []
    for orig, new_ch, old_ch in zip(original, new_screened, old_screened):
        if new_ch != orig:
            out.append(new_ch)
        elif old_ch != orig:
            out.append(old_ch)
        else:
            out.append(orig)
    return "".join(out)


def screen_hunk_body_line(
    line: str,
    new_languages: list[str],
    old_languages: list[str],
    new_state: _ScreenState,
    old_state: _ScreenState,
    screen_docstrings: bool,
) -> str:
    body, ending = _line_parts(line)
    prefix = body[0]
    content = body[1:]
    if prefix == "-":
        screened = screen_content_line(content, old_languages, old_state, screen_docstrings)
    elif prefix == "+":
        screened = screen_content_line(content, new_languages, new_state, screen_docstrings)
    else:
        new_screened = screen_content_line(content, new_languages, new_state, screen_docstrings)
        old_screened = screen_content_line(content, old_languages, old_state, screen_docstrings)
        screened = _merge_comment_masks(content, new_screened, old_screened)
    return prefix + screened + ending


def screen_hunk_header_line(
    line: str,
    new_languages: list[str],
    old_languages: list[str],
    new_state: _ScreenState,
    old_state: _ScreenState,
    screen_docstrings: bool,
) -> str:
    """Keep ``@@ -l,s +l,s @@`` ranges; blank comment text in the source suffix.

    Uses the hunk's lexer states so a multiline comment opened in the
    suffix stays open for the following body lines.
    """
    body, ending = _line_parts(line)
    first = body.find("@@")
    second = body.find("@@", first + 2) if first >= 0 else -1
    if first < 0 or second < 0:
        return line
    ranges = body[: second + 2]
    suffix = body[second + 2 :]
    if not suffix:
        return line
    lead = ""
    rest = suffix
    if rest.startswith(" "):
        lead = " "
        rest = rest[1:]
    if not rest or not (new_languages or old_languages):
        return line
    new_screened = screen_content_line(rest, new_languages, new_state, screen_docstrings)
    old_screened = screen_content_line(rest, old_languages, old_state, screen_docstrings)
    screened = _merge_comment_masks(rest, new_screened, old_screened)
    return ranges + lead + screened + ending


def _iter_file_sections(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    if not lines:
        return []
    sections: list[tuple[str, str, list[str]]] = []
    old_path = ""
    new_path = ""
    current: list[str] = []
    preamble: list[str] = []
    in_hunk = False
    for line in lines:
        if _is_diff_file_header(line):
            if current:
                sections.append((old_path, new_path, current))
            elif preamble:
                sections.append(("", "", preamble))
                preamble = []
            body, _ = _line_parts(line)
            old_path, new_path = _paths_from_diff_header(body)
            current = [line]
            in_hunk = False
        elif current:
            if _is_hunk_header(line):
                in_hunk = True
            elif not in_hunk:
                # ``+++ `` / ``--- `` are file headers only before the first
                # @@. An in-hunk added line ``++ foo`` serializes as ``+++ foo``
                # and must not retarget the path or skip screening.
                minus_path = _path_from_minus_minus_line(line)
                plus_path = _path_from_plus_plus_line(line)
                if minus_path is not None:
                    old_path = minus_path
                if plus_path is not None:
                    new_path = plus_path
            current.append(line)
        else:
            preamble.append(line)
    if current:
        sections.append((old_path, new_path, current))
    elif preamble:
        sections.append(("", "", preamble))
    return sections


def screen_unified_diff(diff_content: str, *, screen_docstrings: bool = False) -> dict:
    """Return screened_diff plus per-file line-count comparison.

    Never deletes lines. On a count mismatch the screened text is still
    returned so the caller can fail with context, but ``line_counts_match``
    is False and the action plugin treats that as a failure.
    """
    original_lines = _split_keepends(diff_content)
    screened_lines: list[str] = []
    per_file: list[dict] = []

    for old_path, new_path, section in _iter_file_sections(original_lines):
        path = new_path or old_path
        new_languages = _languages_for(language_for_path(new_path)) if new_path else []
        old_languages = _languages_for(language_for_path(old_path)) if old_path else []
        languages = new_languages or old_languages
        new_state = _ScreenState()
        old_state = _ScreenState()
        out_section: list[str] = []
        in_hunk = False
        for line in section:
            if languages and _is_hunk_header(line):
                # Omitted lines between hunks are not in the diff: do not
                # carry lexer state across them. The suffix after the second
                # @@ is source context (often a function line + inline comment).
                in_hunk = True
                new_state = _ScreenState()
                old_state = _ScreenState()
                out_section.append(
                    screen_hunk_header_line(
                        line, new_languages, old_languages, new_state, old_state, screen_docstrings
                    )
                )
            elif languages and in_hunk and _is_hunk_content_line(line):
                out_section.append(
                    screen_hunk_body_line(
                        line, new_languages, old_languages, new_state, old_state, screen_docstrings
                    )
                )
            else:
                # Diff metadata, binary notices, unknown langs.
                out_section.append(line)
        per_file.append(
            {
                "path": path or "(preamble)",
                "original_lines": len(section),
                "screened_lines": len(out_section),
            }
        )
        screened_lines.extend(out_section)

    screened = "".join(screened_lines)
    # Preserve a missing trailing newline: splitlines(keepends=True) already
    # does. If the original was empty, stay empty.
    line_counts_match = len(screened_lines) == len(original_lines) and all(
        item["original_lines"] == item["screened_lines"] for item in per_file
    )
    return {
        "screened_diff": screened,
        "line_counts_match": line_counts_match,
        "original_line_count": len(original_lines),
        "screened_line_count": len(screened_lines),
        "per_file": per_file,
    }


class ActionModule(ActionBase):
    """Blank comments in a copy of the prepared diff. Never mutates the original."""

    _requires_connection = False
    _VALID_ARGS = frozenset(("diff_content", "screen_docstrings"))

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        result = super().run(tmp, task_vars)
        del tmp

        diff_content = self._task.args.get("diff_content")
        if diff_content is None:
            result["failed"] = True
            result["msg"] = "screen_diff_comments requires a 'diff_content' argument"
            return result

        screen_docstrings = bool(self._task.args.get("screen_docstrings", False))
        payload = screen_unified_diff(diff_content, screen_docstrings=screen_docstrings)
        result["changed"] = False
        result.update(payload)
        if not payload["line_counts_match"]:
            result["failed"] = True
            result["msg"] = (
                "screen_diff_comments shifted line counts "
                f"(original={payload['original_line_count']} "
                f"screened={payload['screened_line_count']} per_file={payload['per_file']}); "
                "refusing to return an unmappable diff"
            )
        return result
