"""Memory-pyramid write-time layer: the time-window summary cascade.

Rolls L1 frames (``records`` + ``analysis_results``) up through fixed time
windows ``5min → 1h → 6h → day → week`` (see
``infra/architecture/episode-and-rollup-pipeline.md``). This slice ships the
deterministic, LLM-free *metrics* cascade:

- :mod:`windows` — pure time-window math (boundaries / scope keys / logical day).
- :mod:`metrics` — pure-SQL per-window aggregates + a Python child-merge.
- :mod:`rollup` — :class:`MetricsCascadeBuilder`, which builds the metric rows.

The LLM narrative (description / evaluation / compression / drill-down hint),
its claim queue, the ``_rollup_loop`` scheduler wiring, and re-emission live in
later slices; the ``summaries`` row + DDL already reserve their columns.
"""
