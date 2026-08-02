#!/usr/bin/env python3
"""Tool registry.

A Tool is a deterministic capability the agent can call. Each tool exposes:
  - name         : the identifier the LLM uses in its JSON decision
  - description  : one line telling the LLM when to use it
  - args         : {arg_name: "description"} — the expected JSON args
  - run(args)    : does the work, returns a dict. By convention it includes a
                   "summary" (a short, deterministic string the LLM should quote)
                   and may include "citations": [{"source": ..., "text": ...}].

Tools never call the LLM themselves — they are pure/deterministic — so their
results are trustworthy and reproducible.
"""


class Tool:
    def __init__(self, name, description, args, run, examples=None, terminal=False):
        self.name = name
        self.description = description
        self.args = args or {}
        self._run = run
        self.examples = examples or []
        # terminal: the tool's `summary` is a complete, user-ready answer, so when
        # the model marks its call "final" we can return it directly and skip the
        # extra narration LLM call (saves free-tier quota).
        self.terminal = terminal

    def run(self, args):
        try:
            result = self._run(args or {})
            if not isinstance(result, dict):
                result = {"summary": str(result)}
            result.setdefault("ok", True)
            return result
        except Exception as e:  # never let a tool crash the loop
            return {"ok": False, "error": str(e)[:300],
                    "summary": "tool error: %s" % (str(e)[:200])}


class Registry:
    def __init__(self):
        self._tools = {}

    def register(self, tool):
        self._tools[tool.name] = tool
        return tool

    def get(self, name):
        return self._tools.get(name)

    def names(self):
        return list(self._tools)

    def run(self, name, args):
        tool = self._tools.get(name)
        if not tool:
            return {"ok": False, "error": "unknown tool: %s" % name,
                    "summary": "no such tool: %s" % name}
        return tool.run(args)

    def spec(self):
        """A compact, LLM-facing description of every tool and its args."""
        lines = []
        for t in self._tools.values():
            arg_str = ", ".join(
                '"%s": <%s>' % (k, v) for k, v in t.args.items()
            ) or "(none)"
            lines.append("- %s: %s\n    args: { %s }" % (t.name, t.description, arg_str))
            for ex in t.examples:
                lines.append("    example: %s" % ex)
        return "\n".join(lines)
