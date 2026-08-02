"""Retrieval-augmented grounding over the portfolio knowledge base.

`build_index.py` turns knowledge/*.md into an embedded index (portfolio_index.json);
`retriever.py` loads it and returns the top-k most similar chunks for a query.
Pure-Python cosine similarity — the corpus is small, so no numpy / vector DB.
"""
