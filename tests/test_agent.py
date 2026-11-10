"""Agent-loop tests with a scripted fake client -- no API key or network needed."""

from types import SimpleNamespace as NS

from t2s.agent import TextToSQLAgent


def tool_use(id, name, **inp):
    return NS(type="tool_use", id=id, name=name, input=inp)


def text(t):
    return NS(type="text", text=t)


def reply(*content, stop="tool_use"):
    return NS(stop_reason=stop, content=list(content), usage=NS(input_tokens=100, output_tokens=20))


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        return self.replies.pop(0)


def test_explore_fix_and_answer(db):
    client = FakeClient([
        reply(tool_use("t1", "list_tables"), tool_use("t2", "describe_table", table="Genre")),
        reply(tool_use("t3", "run_query", sql="SELECT Nme FROM Genre")),  # typo -> error
        reply(tool_use("t4", "final_answer", sql="SELECT COUNT(*) FROM Genre", answer="There are 25 genres.")),
    ])
    agent = TextToSQLAgent(db, client=client)
    steps = []
    res = agent.ask("How many genres?", on_step=steps.append)

    assert res.ok and res.answer == "There are 25 genres."
    assert res.result.rows == [(25,)]
    assert [s.tool for s in steps] == ["list_tables", "describe_table", "run_query", "final_answer"]
    assert steps[2].is_error and "no such column" in steps[2].output
    assert res.turns == 3 and res.input_tokens == 300

    # Both tool results from turn 1 went back in ONE user message, matched by id.
    second = client.requests[1]["messages"]
    results = second[-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    # The error was fed back to the model with is_error set.
    third = client.requests[2]["messages"][-1]["content"][0]
    assert third["is_error"] is True


def test_blocks_writes_and_lets_model_recover(db):
    client = FakeClient([
        reply(tool_use("t1", "run_query", sql="DELETE FROM Genre")),
        reply(tool_use("t2", "final_answer", sql="SELECT 1", answer="ok")),
    ])
    res = TextToSQLAgent(db, client=client).ask("delete everything")
    assert res.steps[0].is_error and "Only SELECT" in res.steps[0].output
    assert res.ok
    assert db.run_query("SELECT COUNT(*) FROM Genre").rows == [(25,)]


def test_invalid_final_answer_is_retried(db):
    client = FakeClient([
        reply(tool_use("t1", "final_answer", sql="SELECT * FROM Nope", answer="x")),
        reply(tool_use("t2", "final_answer", sql="SELECT COUNT(*) FROM Artist", answer="275 artists")),
    ])
    res = TextToSQLAgent(db, client=client).ask("How many artists?")
    assert res.ok and res.sql == "SELECT COUNT(*) FROM Artist" and res.turns == 2


def test_plain_text_end_falls_back_to_last_query(db):
    client = FakeClient([
        reply(tool_use("t1", "run_query", sql="SELECT COUNT(*) FROM Customer")),
        reply(text("There are 59 customers."), stop="end_turn"),
    ])
    res = TextToSQLAgent(db, client=client).ask("How many customers?")
    assert res.answer == "There are 59 customers."
    assert res.sql == "SELECT COUNT(*) FROM Customer"


def test_refusal_and_turn_limit(db):
    res = TextToSQLAgent(db, client=FakeClient([reply(stop="refusal")])).ask("x")
    assert not res.ok and "declined" in res.answer

    loop = [reply(tool_use(f"t{i}", "list_tables")) for i in range(3)]
    res = TextToSQLAgent(db, client=FakeClient(loop), max_turns=3).ask("x")
    assert not res.ok and "3 turns" in res.answer


def test_request_shape(db):
    client = FakeClient([reply(tool_use("t1", "final_answer", sql="SELECT 1", answer="1"))])
    TextToSQLAgent(db, client=client).ask("q")
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["fallbacks"] == "default"
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in req["tools"])
