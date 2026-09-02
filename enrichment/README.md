# LLM Enrichment

`enrich.py` adds a title, explicit input constraints, and non-spoiler hints to
a validated sandbox report through Groq. It leaves the source report unchanged
and writes the enriched copy to `enriched/report.json`.

When it starts, the script prompts for the path to the sandbox report JSON.
Set `GROQ_API_KEY` in `.env`, then run it from the repository root with the
required environment:

```bash
/home/daemon_bash/miniconda3/envs/ml-env/bin/python enrichment/enrich.py
```

The script is resumable: if the output already exists, it skips the API call.
`GROQ_MODEL` may optionally override the default `openai/gpt-oss-20b` model.
