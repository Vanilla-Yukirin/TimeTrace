"""Internal agent layer: a tool-calling loop over the local LLM.

Shares its activity tools (``tools.py``) with the external MCP server so the
read/label surface can't drift between the web chat agent and Claude Code over
MCP. ``runner.py`` drives the OpenAI-style tool-calling loop; the FastAPI route
``server/api/routes/agent.py`` streams it to the browser as SSE.
"""
