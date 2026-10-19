import json

from pmbot.demo import build, render


def test_demo_builds_and_embeds_its_data():
    data = build(seed=3, days=0.6, warmup_days=0, log=lambda s: None)  # midnight to 14:24, includes core hours
    assert data["markets"], "expected at least one showcased market"
    m = data["markets"][0]
    assert len(m["under"]) == len(m["fair"]) == len(m["bid"]) == len(m["ask"]) == data["meta"]["market_len_s"]
    assert m["orders"] and all("why" in o for o in m["orders"])
    assert data["runs"]["ungated"]["half_hours"]

    page = render(data)
    assert page.startswith("<!doctype html>") and "<title>prediction-market-engine</title>" in page
    assert "/*__DEMO_DATA__*/" not in page
    embedded = page.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    assert json.loads(embedded.replace("<\\/", "</"))["meta"]["seed"] == 3

    fragment = render(data, standalone=False)
    assert not fragment.startswith("<!doctype") and "<script>" in fragment
