"""Agent package: a small, model-agnostic plan -> act -> observe loop.

The orchestrator (`loop.run_agent`) asks the LLM for ONE JSON decision at a time
— either call a tool from the registry, or produce a final answer. Tools do the
deterministic work (SymPy math, portfolio retrieval, date/time, network math);
the LLM only decides *which* tool to use and narrates the result. Every step is
recorded in a trace so the UI can show the reasoning.
"""
