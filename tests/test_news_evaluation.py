from scripts.evaluate_news import evaluate


def test_evaluation_reports_policy_results_and_semantic_gaps():
    report = evaluate()
    assert report['metrics']['retrieval_passed'] == report['metrics']['retrieval_cases'] == 60
    assert report['metrics']['guardrail_expectations_met'] == report['metrics']['claim_probes']
    assert report['metrics']['unsupported_probes_accepted'] == 0
    assert report['external_calls'] == 0
