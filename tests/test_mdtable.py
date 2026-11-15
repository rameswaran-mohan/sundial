from sundial_llm.mdtable import backticked, iter_tables

DOC = """# Title

## Alpha

### Beta

| **Model** | **Shutdown date** |
|---|---|
| `m-1` | 2026-01-01 |
| Preview models ||
| `m-2` | — |

## Gamma

| Shutdown date | Model snapshot | Substitute |
| ------------- | -------------- | ---------- |
| October 23, 2026 | `a-1` \\| `a`, `a-completions` | `b` (`reasoning.mode: pro`) |
"""


def test_tables_carry_the_heading_path_they_sit_under() -> None:
    tables = list(iter_tables(DOC))

    assert [t.headings for t in tables] == [("Title", "Alpha", "Beta"), ("Title", "Gamma")]


def test_header_cells_lose_bold_markers_and_separator_is_not_data() -> None:
    first = next(iter_tables(DOC))

    assert first.header == ("Model", "Shutdown date")
    assert first.rows[0] == ("`m-1`", "2026-01-01")
    assert len(first.rows) == 3


def test_divider_row_is_returned_so_callers_can_switch_mode() -> None:
    first = next(iter_tables(DOC))

    assert first.rows[1][0] == "Preview models"


def test_escaped_pipe_does_not_split_a_cell() -> None:
    second = list(iter_tables(DOC))[1]

    assert len(second.rows[0]) == 3
    assert "`a-1`" in second.rows[0][1]
    assert "`a-completions`" in second.rows[0][1]


def test_raw_rows_keep_the_original_line_as_evidence() -> None:
    first = next(iter_tables(DOC))

    assert first.raw_rows[0] == "| `m-1` | 2026-01-01 |"


# Markdoc table blocks as the Gemini page serves them since 2026-11-04. `\x20` keeps the trailing
# space of a bare `  * ` empty cell visible in the source.
MARKDOC = """# Title

## Family  {% id="family" %}

{% table %}
  * **Model**
  * Shutdown date
  * Recommended replacement

  ---

  * `m-1`
  * June 1, 2026
  * `m-2` or `m-3`

  ---

  * `m-4`
  *
  *\x20
{% /table %}

### Preview models  {% id="family-preview-models" %}

{% table %}
  * Model
  * Shutdown date
  * Recommended replacement

  ---

  * `p-1`
  * No shutdown date announced
  * `p-2` (Effective December 3, 2026, the endpoint is
    automatically routing API requests to
    `p-3`)
{% /table %}
"""


def test_markdoc_table_yields_header_and_rows_like_a_pipe_table() -> None:
    first = next(iter_tables(MARKDOC))

    assert first.header == ("Model", "Shutdown date", "Recommended replacement")
    assert first.rows[0] == ("`m-1`", "June 1, 2026", "`m-2` or `m-3`")
    assert len(first.rows) == 2


def test_markdoc_empty_cells_are_empty_strings() -> None:
    first = next(iter_tables(MARKDOC))

    assert first.rows[1] == ("`m-4`", "", "")


def test_heading_markdoc_id_suffix_is_stripped() -> None:
    tables = list(iter_tables(MARKDOC))

    assert [t.headings for t in tables] == [("Title", "Family"), ("Title", "Family", "Preview models")]


def test_markdoc_cell_continuation_lines_join_the_cell() -> None:
    second = list(iter_tables(MARKDOC))[1]

    assert second.rows == (
        (
            "`p-1`",
            "No shutdown date announced",
            "`p-2` (Effective December 3, 2026, the endpoint is automatically routing API requests to `p-3`)",
        ),
    )


def test_markdoc_raw_rows_are_one_readable_line_built_from_the_cells() -> None:
    first = next(iter_tables(MARKDOC))

    assert first.raw_rows == ("| `m-1` | June 1, 2026 | `m-2` or `m-3` |", "| `m-4` |  |  |")


def test_pipe_and_markdoc_tables_are_yielded_in_page_order() -> None:
    tables = list(iter_tables(DOC + "\n" + MARKDOC))

    assert [t.header[0] for t in tables] == ["Model", "Shutdown date", "Model", "Model"]
    assert tables[2].headings == ("Title", "Family")


def test_unclosed_markdoc_table_ends_at_the_next_heading() -> None:
    page = "## A\n\n{% table %}\n  * Model\n  ---\n  * `m-1`\n\n## B\n\n{% table %}\n  * Model\n  ---\n  * `m-2`\n{% /table %}\n"

    tables = list(iter_tables(page))

    assert [(t.headings, t.rows) for t in tables] == [(("A",), (("`m-1`",),)), (("B",), (("`m-2`",),))]


def test_unclosed_markdoc_table_ends_at_prose_after_it() -> None:
    page = "{% table %}\n  * Model\n  * Recommended replacement\n  ---\n  * `m-1`\n  * `m-2`\n\nSee `other-model` too.\n"

    table = next(iter_tables(page))

    assert table.rows == (("`m-1`", "`m-2`"),)
    assert not any("other-model" in cell for row in table.rows for cell in row)


def test_unclosed_markdoc_table_ends_at_any_other_tag() -> None:
    page = (
        "{% table %}\n  * Model\n  * Recommended replacement\n  ---\n  * `m-1`\n  * `m-2`\n"
        "{% callout %}\n  `zzz-9` is coming.\n{% /callout %}\n"
        "{% table %}\n  * Model\n  ---\n  * `m-3`\n{% /table %}\n"
    )

    tables = list(iter_tables(page))

    assert [t.rows for t in tables] == [(("`m-1`", "`m-2`"),), (("`m-3`",),)]


def test_closed_markdoc_table_keeps_unindented_lines_up_to_its_close_tag() -> None:
    page = "{% table %}\n* Model\n* Note\n---\n* `m-1`\n* routed to\n`m-2` today\n{% /table %}\n"

    table = next(iter_tables(page))

    assert table.rows == (("`m-1`", "routed to `m-2` today"),)


def test_backticked_keeps_only_tokens_that_look_like_model_ids() -> None:
    cell = "`gpt-4-0613` \\| `gpt-4`, `b` (`reasoning.mode: pro`) and `/v1/assistants`"

    assert backticked(cell) == ("gpt-4-0613", "gpt-4", "b")


def test_backticked_accepts_bedrock_and_versioned_ids() -> None:
    assert backticked("`anthropic.claude-3-haiku-20240307-v1:0`") == (
        "anthropic.claude-3-haiku-20240307-v1:0",
    )
