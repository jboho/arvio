import http.client
import json
import threading

import pytest
from conftest import finish_task, read_items, write_lines

from arvio import server, store
from arvio.task import load_task

JUDGE_SECRET = "judge-sentinel-7f3a"
META_SECRET = "meta-sentinel-c91e"


class Client:
    def __init__(self, app: server.App):
        self.app = app

    def raw(self, method, path, body=None, headers=None, token=True, length=None):
        """Send one request; returns (status, headers, bytes)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=5)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        sent = {"Host": f"127.0.0.1:{self.app.port}"}
        if token:
            sent[server.TOKEN_HEADER] = self.app.token
        if body is not None:
            sent["Content-Type"] = "application/json"
            sent["Content-Length"] = str(len(body))
        if length is not None:
            sent["Content-Length"] = str(length)
        sent.update(headers or {})
        for k, v in sent.items():
            if v is not None:
                conn.putheader(k, v)
        conn.endheaders()
        if body is not None:
            conn.send(body)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, dict(res.getheaders()), data

    def call(self, method, path, body=None, **kw):
        data = None if body is None else json.dumps(body).encode()
        if method == "POST" and data is None:
            # What a browser sends for fetch() with no body.
            kw.setdefault("length", 0)
        status, _, raw = self.raw(method, "/api" + path, data, **kw)
        return status, json.loads(raw)

    def ok(self, method, path, body=None):
        status, data = self.call(method, path, body)
        assert status == 200, data
        return data


@pytest.fixture
def folder(task_dir):
    return load_task(task_dir).dir


@pytest.fixture
def served(folder):
    store.register(folder)
    app = server.App(labeler="tester")
    app.open()
    httpd = server.make_server(app)
    # A short poll, so shutdown() returns quickly after each test.
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    yield Client(app)
    httpd.shutdown()
    httpd.server_close()
    app.close()


@pytest.fixture
def client(served):
    return served


def tid(folder):
    return server.task_id(folder)


def start(client, folder):
    return client.ok("POST", f"/tasks/{tid(folder)}/sessions")


def label_body(session, item, answer="match", **extra):
    return {
        "session": session["session"],
        "item_id": item["id"],
        "item_hash": item["hash"],
        "answer": answer,
        "followups": {},
        "note": "",
        "seconds": 12.3,
        **extra,
    }


def judge_of(folder):
    return {row["id"]: row["judge"] for row in read_items(folder)}


def agreeing_answer(judge):
    return "match" if judge["verdict"] == "supported" else "mismatch"


def full_answer(answer):
    """The answer plus the follow-up the card requires for it."""
    if answer == "mismatch":
        return {"answer": answer, "followups": {"why": "contradicts"}}
    return {"answer": answer}


def other_answer(answer):
    return "match" if answer == "mismatch" else "mismatch"


# The page and the request checks.


def test_page_carries_a_fresh_nonce_and_strict_csp(client):
    status, headers, body = client.raw("GET", "/", token=False)
    assert status == 200
    csp = headers["Content-Security-Policy"]
    nonce = csp.split("'nonce-", 1)[1].split("'", 1)[0]
    page = body.decode()
    assert f'nonce="{nonce}"' in page
    assert "{{nonce}}" not in page
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert headers["Cache-Control"] == "no-store"
    second = client.raw("GET", "/", token=False)[1]["Content-Security-Policy"]
    assert second != csp


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example"},
        {"Host": "127.0.0.1:1"},
        {"Host": None},
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
    ],
)
def test_wrong_host_or_origin_is_refused(client, headers):
    for path in ("/", "/api/inbox"):
        status, _, body = client.raw("GET", path, headers=headers)
        assert status == 403
        assert json.loads(body)["error"] == "wrong host"


def test_localhost_and_a_same_origin_are_allowed(client):
    port = client.app.port
    assert (
        client.raw("GET", "/api/inbox", headers={"Host": f"localhost:{port}"})[0] == 200
    )
    origin = {"Origin": f"http://127.0.0.1:{port}"}
    assert client.raw("GET", "/api/inbox", headers=origin)[0] == 200


@pytest.mark.parametrize("token", [None, "", "x" * 43])
def test_the_api_needs_the_launch_token(client, token):
    headers = {server.TOKEN_HEADER: token}
    status, _, body = client.raw("GET", "/api/inbox", headers=headers, token=False)
    assert status == 403
    assert "key" in json.loads(body)["error"]


def test_fonts_come_from_the_allow_list_only(client):
    for name in server.FONTS:
        status, headers, body = client.raw("GET", f"/fonts/{name}", token=False)
        assert status == 200 and headers["Content-Type"] == "font/woff2"
        assert body[:4] == b"wOF2"
    for path in (
        "/fonts/OFL.txt",
        "/fonts/../server.py",
        "/fonts/%2e%2e/server.py",
        "/fonts/",
        "/static/index.html",
        "/api",
    ):
        assert client.raw("GET", path, token=False)[0] in (403, 404), path


def test_unknown_routes_are_404(client):
    assert client.call("GET", "/nope")[0] == 404
    assert client.call("POST", "/inbox")[0] == 404
    assert client.call("GET", "/tasks/000000000000")[0] == 404


def test_a_post_needs_a_length_and_a_small_body(client, folder):
    path = f"/api/tasks/{tid(folder)}/labels"
    assert client.raw("POST", path)[0] == 411
    status, _, _ = client.raw("POST", path, length=server.MAX_BODY + 1)
    assert status == 413


def test_an_empty_post_is_an_empty_object(client, folder):
    status, _, body = client.raw("POST", f"/api/tasks/{tid(folder)}/sessions", length=0)
    assert status == 200, body
    status, _, _ = client.raw("POST", f"/api/tasks/{tid(folder)}/labels", length=0)
    assert status == 400
    assert not (folder / "labels.jsonl").exists()


@pytest.mark.parametrize(
    "raw",
    [
        b"{not json",
        b"[1, 2]",
        b'{"session": "a", "session": "b"}',
        b'{"seconds": NaN}',
        b'{"note": "\\ud83d"}',
        b"\xff\xfe{}",
        b"[" * 50000 + b"]" * 50000,
    ],
)
def test_bad_bodies_are_400(client, folder, raw):
    path = f"/api/tasks/{tid(folder)}/labels"
    status, _, body = client.raw("POST", path, raw)
    assert status == 400
    assert "error" in json.loads(body)


def test_unknown_keys_are_400_and_write_nothing(client, folder):
    status, data = client.call("POST", f"/tasks/{tid(folder)}/sessions", {"size": 3})
    assert status == 400 and "unknown keys: size" in data["error"]
    s = start(client, folder)
    client.ok("POST", f"/tasks/{tid(folder)}/labels", label_body(s, s["items"][0]))
    status, data = client.call(
        "POST", f"/tasks/{tid(folder)}/finish", {"session": s["session"], "all": 1}
    )
    assert status == 400 and "unknown keys: all" in data["error"]
    assert load_task(folder).labels.revealed == set()


def test_a_failed_write_answers_500_and_the_server_stays_up(
    client, folder, monkeypatch
):
    s = start(client, folder)

    def full_disk(path, row):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "append_jsonl", full_disk)
    path = f"/tasks/{tid(folder)}/labels"
    status, data = client.call("POST", path, label_body(s, s["items"][0]))
    assert status == 500 and "No space left on device" in data["error"]

    def leaky(path, row):
        raise ValueError(JUDGE_SECRET)

    monkeypatch.setattr(store, "append_jsonl", leaky)
    status, _, body = client.raw(
        "POST", "/api" + path, json.dumps(label_body(s, s["items"][0])).encode()
    )
    assert status == 500 and b"ValueError" in body
    assert JUDGE_SECRET.encode() not in body
    assert client.ok("GET", "/inbox")["tasks"]


# The inbox and a session.


def test_inbox_lists_the_task(client, folder):
    tasks = client.ok("GET", "/inbox")["tasks"]
    assert len(tasks) == 1
    t = tasks[0]
    assert t["id"] == tid(folder)
    assert t["dir"] == str(folder)
    assert (t["items"], t["waiting"], t["labeled"], t["revealed"]) == (12, 12, 0, 0)
    assert t["next_n"] == 10 and t["next_minutes"] == 5
    assert "problem" not in t


def test_archive_flag_round_trips_and_blocks_new_sessions(client, folder):
    path = f"/tasks/{tid(folder)}"
    status, data = client.call("POST", path + "/archive", {"archived": True})
    assert status == 409 and "still waiting" in data["error"]
    finish_task(folder)
    assert client.ok("GET", "/inbox")["tasks"][0]["archived"] is False
    assert client.ok("POST", path + "/archive", {"archived": True}) == {
        "id": tid(folder),
        "archived": True,
    }
    assert client.ok("GET", "/inbox")["tasks"][0]["archived"] is True
    status, data = client.call("POST", path + "/sessions")
    assert status == 409 and "archived" in data["error"]
    assert (
        client.ok("POST", path + "/archive", {"archived": False})["archived"] is False
    )
    assert client.ok("GET", "/inbox")["tasks"][0]["archived"] is False


def test_archive_route_checks_its_input(client, folder):
    path = f"/tasks/{tid(folder)}/archive"
    assert client.call("POST", path, {})[0] == 400
    assert client.call("POST", path, {"archived": "yes"})[0] == 400
    assert client.call("POST", path, {"archived": True, "extra": 1})[0] == 400
    assert client.call("POST", "/tasks/nope/archive", {"archived": True})[0] == 404
    assert client.ok("GET", "/inbox")["tasks"][0]["archived"] is False


def test_the_page_knows_whether_the_task_has_a_judge(client, folder):
    path = f"/tasks/{tid(folder)}"
    assert client.ok("GET", "/inbox")["tasks"][0]["has_judge"] is True
    assert client.ok("GET", path)["card"]["has_judge"] is True
    card = folder / "task.toml"
    text = card.read_text(encoding="utf-8")
    card.write_text(text[: text.index("[agreement]")], encoding="utf-8")
    assert client.ok("GET", "/inbox")["tasks"][0]["has_judge"] is False
    s = start(client, folder)
    assert s["card"]["has_judge"] is False
    client.ok("POST", path + "/labels", label_body(s, s["items"][0]))
    done = client.ok("POST", path + "/finish", {"session": s["session"]})
    assert (done["has_judge"], done["revealed"], done["compared"]) == (False, 1, 0)
    assert done["items"][0]["agrees"] is None


def test_a_session_sends_items_in_hash_order_without_judge_or_meta(client, folder):
    s = start(client, folder)
    assert s["session"].startswith("s-")
    assert len(s["items"]) == 10 and s["waiting"] == 12
    assert s["seconds_per_item"] == 30 and s["recent_seconds"] == []
    ids = [i["id"] for i in s["items"]]
    file_order = [row["id"] for row in read_items(folder)]
    assert ids != file_order[:10]
    version = load_task(folder).card.version
    assert ids == sorted(ids, key=lambda i: server.order_key(version, i))
    for item in s["items"]:
        assert set(item) == {"id", "hash", "display", "prefill"}
    assert not {"agreement", "gate", "map"} & set(s["card"])


def test_judge_and_meta_stay_hidden_until_finish(client, folder):
    rows = read_items(folder)
    for row in rows:
        row["judge"]["evidence"] = JUDGE_SECRET
        row["meta"]["secret"] = META_SECRET
    write_lines(folder / "items.jsonl", rows)
    seen = []
    status, _, page = client.raw("GET", "/", token=False)
    seen.append(page.decode())
    seen.append(json.dumps(client.ok("GET", "/inbox")))
    seen.append(json.dumps(client.ok("GET", f"/tasks/{tid(folder)}")))
    s = start(client, folder)
    seen.append(json.dumps(s))
    judge = judge_of(folder)
    for item in s["items"]:
        body = label_body(s, item, **full_answer(agreeing_answer(judge[item["id"]])))
        seen.append(json.dumps(client.ok("POST", f"/tasks/{tid(folder)}/labels", body)))
    for text in seen:
        assert JUDGE_SECRET not in text and META_SECRET not in text
    done = client.ok("POST", f"/tasks/{tid(folder)}/finish", {"session": s["session"]})
    text = json.dumps(done)
    assert JUDGE_SECRET in text
    assert META_SECRET not in text


# Labels.


def test_a_label_row_has_exactly_the_logged_fields(client, folder):
    s = start(client, folder)
    item = s["items"][0]
    body = label_body(s, item, note="  looks fine  ", seconds=7.26)
    assert client.ok("POST", f"/tasks/{tid(folder)}/labels", body) == {"ok": True}
    lines = (folder / "labels.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert set(row) == {
        "kind",
        "item_id",
        "item_hash",
        "task_version",
        "answer",
        "followups",
        "note",
        "seconds",
        "labeled_at",
        "labeler",
        "session",
    }
    assert row["kind"] == "label" and row["item_id"] == item["id"]
    assert row["task_version"] == load_task(folder).card.version
    assert row["note"] == "looks fine" and row["seconds"] == 7.3
    assert row["labeler"] == "tester" and row["session"] == s["session"]


def test_the_labeler_name_comes_from_the_env(monkeypatch):
    monkeypatch.setenv("ARVIO_LABELER", "sam")
    assert server.App().labeler == "sam"


@pytest.mark.parametrize("error", [KeyError("uid 501"), OSError("no user")])
def test_a_login_name_that_cant_be_read_is_blank(monkeypatch, error):
    def fail():
        raise error

    monkeypatch.delenv("ARVIO_LABELER", raising=False)
    monkeypatch.setattr(server.getpass, "getuser", fail)
    assert server.labeler_name() == ""


def test_the_latest_label_wins(client, folder):
    s = start(client, folder)
    item = s["items"][0]
    path = f"/tasks/{tid(folder)}/labels"
    client.ok("POST", path, label_body(s, item, "match"))
    client.ok("POST", path, label_body(s, item, "cant_tell"))
    assert load_task(folder).labels.current[item["id"]]["answer"] == "cant_tell"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"extra": 1}, "unknown keys: extra"),
        ({"answer": 3}, "answer: must be a choice key"),
        ({"answer": "maybe"}, "doesn't fit the card"),
        ({"answer": "mismatch"}, "doesn't fit the card"),
        ({"followups": {"why": "contradicts"}}, "doesn't fit the card"),
        ({"followups": []}, "doesn't fit the card"),
        ({"note": "x" * (server.MAX_NOTE + 1)}, "note: must be text"),
        ({"note": 5}, "note: must be text"),
        ({"seconds": -1}, "seconds: must be"),
        ({"seconds": 10**9}, "seconds: must be"),
        ({"seconds": "10"}, "seconds: must be"),
        ({"seconds": True}, "seconds: must be"),
        ({"item_hash": None}, "item_hash"),
        ({"item_id": "not-in-session"}, "not in this session"),
        ({"session": "s-forged"}, "unknown session"),
        ({"session": 7}, "unknown session"),
    ],
)
def test_bad_labels_are_400_and_write_nothing(client, folder, change, message):
    s = start(client, folder)
    body = {**label_body(s, s["items"][0]), **change}
    status, data = client.call("POST", f"/tasks/{tid(folder)}/labels", body)
    assert status == 400
    assert message in data["error"]
    assert not (folder / "labels.jsonl").exists()


def test_a_missing_required_follow_up_is_named(client, folder):
    s = start(client, folder)
    body = label_body(s, s["items"][0], "mismatch")
    status, data = client.call("POST", f"/tasks/{tid(folder)}/labels", body)
    assert status == 400
    assert data["problems"] == ["followups: 'why' is required for 'mismatch'"]


def test_a_required_edit_must_change_a_field(client, folder):
    card = folder / "task.toml"
    text = card.read_text(encoding="utf-8")
    old = 'kind = "edit"\n'
    assert old in text
    card.write_text(text.replace(old, old + "required = true\n", 1), encoding="utf-8")
    s = start(client, folder)
    item = s["items"][0]
    path = f"/tasks/{tid(folder)}/labels"
    same = {"why": "contradicts", "fix": dict(item["prefill"])}
    status, data = client.call(
        "POST", path, label_body(s, item, "mismatch", followups=same)
    )
    assert status == 400
    assert data["problems"] == ["followups: 'fix' must change at least one field"]
    edited = {"why": "contradicts", "fix": {"sentence": "It leaves at 08:15."}}
    client.ok("POST", path, label_body(s, item, "mismatch", followups=edited))


def test_an_item_that_changed_on_disk_is_409(client, folder):
    s = start(client, folder)
    item = s["items"][0]
    rows = read_items(folder)
    for row in rows:
        if row["id"] == item["id"]:
            row["display"][1]["text"] += " Changed."
    write_lines(folder / "items.jsonl", rows)
    status, data = client.call(
        "POST", f"/tasks/{tid(folder)}/labels", label_body(s, item)
    )
    assert status == 409 and "changed on disk" in data["error"]


def test_a_changed_card_is_409(client, folder):
    s = start(client, folder)
    card = folder / "task.toml"
    text = card.read_text(encoding="utf-8")
    card.write_text(text.replace("say only what", "say just what"), encoding="utf-8")
    status, data = client.call(
        "POST", f"/tasks/{tid(folder)}/labels", label_body(s, s["items"][0])
    )
    assert status == 409 and "card changed" in data["error"]


def test_a_revealed_item_is_locked(client, folder):
    s = start(client, folder)
    item = s["items"][0]
    path = f"/tasks/{tid(folder)}/labels"
    client.ok("POST", path, label_body(s, item))
    client.ok("POST", f"/tasks/{tid(folder)}/finish", {"session": s["session"]})
    status, data = client.call("POST", path, label_body(s, item, "cant_tell"))
    assert status == 409 and "locked" in data["error"]
    assert load_task(folder).labels.current[item["id"]]["answer"] == "match"


# Finish.


def test_finish_counts_agreement_and_reveals_stranded_labels(client, folder):
    judge = judge_of(folder)
    path = f"/tasks/{tid(folder)}/labels"
    first = start(client, folder)
    a = first["items"][0]
    client.ok("POST", path, label_body(first, a, **full_answer("cant_tell")))
    second = start(client, folder)
    assert a["id"] not in [i["id"] for i in second["items"]]
    agree, disagree = second["items"][0], second["items"][1]
    right = agreeing_answer(judge[agree["id"]])
    wrong = other_answer(agreeing_answer(judge[disagree["id"]]))
    client.ok("POST", path, label_body(second, agree, **full_answer(right)))
    client.ok("POST", path, label_body(second, disagree, **full_answer(wrong)))

    done = client.ok(
        "POST", f"/tasks/{tid(folder)}/finish", {"session": second["session"]}
    )
    assert done["has_judge"] is True and done["judge_field"] == "verdict"
    assert done["compare"] == [{"followup": "why", "judge_field": "reason"}]
    assert done["judge_map"] == {"supported": ["match"], "unsupported": ["mismatch"]}
    assert done["revealed"] == 3
    assert (done["compared"], done["agreed"]) == (2, 1)
    by_id = {r["id"]: r for r in done["items"]}
    assert by_id[a["id"]]["agrees"] is None
    assert by_id[agree["id"]]["agrees"] is True
    assert by_id[disagree["id"]]["agrees"] is False
    assert by_id[disagree["id"]]["judge"] == judge[disagree["id"]]
    assert [r["id"] for r in done["items"]][:2] == [agree["id"], disagree["id"]]

    task = load_task(folder)
    assert task.labels.revealed == {a["id"], agree["id"], disagree["id"]}
    again = client.ok(
        "POST", f"/tasks/{tid(folder)}/finish", {"session": second["session"]}
    )
    assert again["revealed"] == 0
    lines = (folder / "labels.jsonl").read_text(encoding="utf-8").splitlines()
    assert sum(1 for line in lines if '"kind": "reveal"' in line) == 1


def test_finish_with_nothing_labeled_writes_nothing(client, folder):
    s = start(client, folder)
    done = client.ok("POST", f"/tasks/{tid(folder)}/finish", {"session": s["session"]})
    assert (done["revealed"], done["compared"], done["items"]) == (0, 0, [])
    assert not (folder / "labels.jsonl").exists()


def test_finish_after_a_card_change_is_409_and_reveals_nothing(client, folder):
    s = start(client, folder)
    client.ok("POST", f"/tasks/{tid(folder)}/labels", label_body(s, s["items"][0]))
    card = folder / "task.toml"
    text = card.read_text(encoding="utf-8")
    card.write_text(text.replace("say only what", "say just what"), encoding="utf-8")
    status, data = client.call(
        "POST", f"/tasks/{tid(folder)}/finish", {"session": s["session"]}
    )
    assert status == 409 and "card changed" in data["error"]
    lines = (folder / "labels.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["kind"] for line in lines] == ["label"]


def test_finish_needs_a_session_of_this_task(client, folder):
    status, data = client.call("POST", f"/tasks/{tid(folder)}/finish", {})
    assert status == 400 and "unknown session" in data["error"]


# Locks and broken tasks.


def test_a_second_server_is_refused(served):
    other = server.App()
    with pytest.raises(store.StoreError, match="already running"):
        other.open()
    other.close()


def test_a_folder_held_elsewhere_is_shown_not_opened(folder):
    store.register(folder)
    with store.locked(folder, wait=False):
        app = server.App()
        app.open()
        try:
            [t] = app.inbox()["tasks"]
            assert t["problem"] == "open in another arvio"
            with pytest.raises(server.ApiError) as caught:
                app.start(tid(folder), {})
            assert caught.value.status == 409
        finally:
            app.close()


def test_a_task_that_broke_shows_its_problem(client, folder):
    (folder / "items.jsonl").write_text("{broken\n", encoding="utf-8")
    [t] = client.ok("GET", "/inbox")["tasks"]
    assert t["problem"] == server.BROKEN
    status, data = client.call("POST", f"/tasks/{tid(folder)}/sessions")
    assert status == 409 and data["error"] == server.BROKEN


def test_a_folder_that_is_gone_shows_its_problem(client, folder, tmp_path):
    store.register(tmp_path / "gone")
    tasks = client.ok("GET", "/inbox")["tasks"]
    assert [t.get("problem") for t in tasks] == [
        None,
        "the folder is gone; run `arvio remove` on it",
    ]


def test_run_refuses_a_second_copy(folder, capsys):
    store.register(folder)
    first = server.App()
    first.open()
    try:
        assert server.run(open_browser=False) == 1
        assert "already running" in capsys.readouterr().err
    finally:
        first.close()
