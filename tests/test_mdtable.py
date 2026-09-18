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


def test_backticked_keeps_only_tokens_that_look_like_model_ids() -> None:
    cell = "`gpt-4-0613` \\| `gpt-4`, `b` (`reasoning.mode: pro`) and `/v1/assistants`"

    assert backticked(cell) == ("gpt-4-0613", "gpt-4", "b")


def test_backticked_accepts_bedrock_and_versioned_ids() -> None:
    assert backticked("`anthropic.claude-3-haiku-20240307-v1:0`") == (
        "anthropic.claude-3-haiku-20240307-v1:0",
    )
