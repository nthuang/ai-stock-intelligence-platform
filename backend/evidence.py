"""Bounded news interpretation: model selects evidence; server owns all commentary."""
import re

# These explain mechanisms conditionally; they do not assert an event occurred,
# predict direction, or establish that the report caused a price movement.
TOPICS = {
    'earnings': (r'\b(earnings|revenue|profit|guidance|forecast|sales)\b',
                 'Earnings and outlook coverage may affect expectations for future cash flows. Compare reported results with prior expectations; the excerpt alone does not establish the reason for the price move.'),
    'product': (r'\b(product|launch\w*|unveil\w*|chip|chips|device|devices)\b',
                'Product coverage may affect expectations for demand, competition, and spending. An announcement alone does not establish future sales or explain the observed move.'),
    'leadership': (r'\b(CEO|CFO|executive|leadership|chief|board)\b',
                   'Leadership coverage may affect expectations about strategy and execution. The direction and size of any market response remain uncertain.'),
    'deal': (r'\b(acqui\w*|merger|takeover|partnership|agreement|deal|contract)\b',
             'Deal coverage may affect expectations for growth, costs, or execution risk. Check whether the report describes a proposal, rumor, denial, or completed agreement before drawing conclusions.'),
    'legal': (r'\b(lawsuit|antitrust|regulat\w*|litigation|court|investigation)\b',
              'Legal and regulatory coverage may affect expectations for costs, restrictions, or uncertainty. Allegations and investigations do not establish an outcome or a cause of the price move.'),
    'capital_returns': (r'\b(dividend|buyback|repurchase)\b',
                        'Capital-return coverage may affect expectations for cash distributions and share count. The terms and timing matter; this report alone does not establish price impact.'),
    'analyst': (r'\b(analyst|upgrade\w*|downgrade\w*|rating|price target)\b',
                'Analyst coverage may influence expectations, but an opinion or rating is not a company result. Check its assumptions and whether the information was already anticipated.'),
    'macro': (r'\b(inflation|interest rates?|Federal Reserve|tariff\w*|economy|economic)\b',
              'Macroeconomic coverage may affect discount rates, costs, or demand across many companies. Its relevance to this stock is indirect and does not establish a company-specific catalyst.'),
}


def normalize(value):
    return ' '.join(value.split()) if isinstance(value, str) else ''


def interpret_evidence(item, source):
    if source.get('coverage') == 'Holdings report':
        return None
    topic, quote = item.get('topic'), normalize(item.get('evidence_quote'))
    excerpt = normalize(source.get('content'))
    if not isinstance(topic, str) or topic not in TOPICS or not 20 <= len(quote) <= 300:
        return None
    # Match the cited source's excerpt, not another article or just its headline.
    if quote not in excerpt or not re.search(TOPICS[topic][0], quote, re.I):
        return None
    influence = TOPICS[topic][1]
    if source.get('coverage') == 'Opinion / comparison':
        influence = 'This is opinion or comparison coverage. ' + influence
    elif source.get('coverage') == 'Market context':
        influence = 'This is broader market context. ' + influence
    return {'source_id': source['id'], 'reported_event': source['title'],
            'topic': topic, 'evidence_quote': quote,
            'possible_influence': influence, 'interpretation_method': 'evidence-selected-template'}
