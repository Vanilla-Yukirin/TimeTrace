"""AI-generated insight dashboards ("看板").

Not a real-time chart view — an AI-authored HTML insight report, generated on a
schedule (the LLM call is expensive). ``generator.ReportGenerator`` reuses the
chat agent's tool-calling loop to pull real activity and write the report; the
30-min scheduler lives in ``server/bootstrap.py``; the routes are in
``server/api/routes/reports.py``.
"""
