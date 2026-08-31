# LLM Enrichment

`enrich.py` adds a title, explicit input constraints, and non-spoiler hints to
a validated sandbox report through Groq. It leaves the source report unchanged
and writes the enriched copy to `enriched/report.json`.

For the temporary handoff, the input is deliberately fixed to the supplied
`/home/daemon_bash/Downloads/report.json`. Set `GROQ_API_KEY` in `.env`, then
run it from the repository root with the required environment:

```bash
conda run -n ml-env python enrichment/enrich.py
```

The script is resumable: if the output already exists, it skips the API call.
`GROQ_MODEL` may optionally override the default `openai/gpt-oss-20b` model.
