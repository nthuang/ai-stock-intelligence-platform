from backend.ai_summary import generate_summary


def test_generate_summary_without_signals_uses_rule_fallback():
    result = generate_summary([])

    assert result["source"] == "rule"
    assert result["model"] == "none"
    assert "No significant market movements" in result["summary"]


def test_generate_summary_without_api_key_uses_fallback(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    result = generate_summary(
        [{"Ticker": "AAPL", "Signal": "BIG_GAIN", "Daily Return %": 4.2}]
    )

    assert result["source"] == "fallback"
    assert result["model"] == "none"
    assert "1 of 1 stocks advanced" in result["summary"]
    assert "equal-weight daily return was +4.20%" in result["summary"]
    assert "do not explain the cause" in result["summary"]



def test_provider_success_and_empty_response(monkeypatch):
    from types import SimpleNamespace
    from backend import ai_summary

    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"focus": ["Participation"]}'))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: completion)))
    monkeypatch.setattr(ai_summary, '_get_client', lambda: client)
    signals = [{'Ticker': 'AAPL', 'Signal': 'BIG_GAIN', 'Daily Return %': 4.2}]
    assert generate_summary(signals)['source'] == 'groq'
    assert generate_summary(signals)['focus'] == ['Participation']
    completion.choices[0].message.content = ''
    assert generate_summary(signals)['source'] == 'fallback'


def test_provider_timeout_uses_fallback(monkeypatch, caplog):
    from backend import ai_summary

    def fail():
        raise TimeoutError('secret provider details')
    monkeypatch.setattr(ai_summary, '_get_client', fail)
    result = generate_summary([{'Ticker': 'AAPL', 'Signal': 'BIG_GAIN', 'Daily Return %': 4}])
    assert result['source'] == 'fallback'
    assert 'secret provider details' not in caplog.text


def test_summary_provider_cannot_invent_an_interpretation(monkeypatch):
    from types import SimpleNamespace
    from backend import ai_summary
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"focus": ["Price is below both averages"]}'))])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))
    monkeypatch.setattr(ai_summary, '_get_client', lambda: fake)
    result = generate_summary([{'Ticker': 'AAPL', 'Daily Return %': 4, 'Signal': 'BIG_GAIN'}])
    assert result['source'] == 'fallback'
    assert 'Price is below both averages' not in result['summary']
    assert '1 of 1 stocks advanced' in result['summary']
