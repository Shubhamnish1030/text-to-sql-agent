from t2s.agent import AgentResult
from t2s.db import QueryResult
from t2s.slack_bot import _clean, render_blocks, render_table


def test_table_alignment_and_truncation():
    rows = [(i, "x" * 60) for i in range(20)]
    out = render_table(["id", "name"], rows).splitlines()
    assert out[0].startswith("id") and set(out[1]) <= {"-", " "}
    assert len(out) == 2 + 15 + 1 and out[-1] == "... 5 more rows"
    assert out[2].endswith("x" * 39 + "…")


def test_blocks_include_answer_table_and_sql():
    res = AgentResult(question="q", answer="Rock wins.", sql="SELECT 1",
                      result=QueryResult(["genre", "n"], [("Rock", 1297), ("Latin", 579)], False, 1.0))
    blocks = render_blocks(res)
    texts = [b.get("text", {}).get("text", "") for b in blocks]
    assert texts[0] == "Rock wins." and "Latin" in texts[1] and "SELECT 1" in texts[2]
    assert blocks[-1]["type"] == "context"


def test_clean_strips_mentions():
    assert _clean("<@U123ABC> top customers?") == "top customers?"
