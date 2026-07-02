# 🎈 Blank app template

A simple Streamlit app template for you to modify!

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://blank-app-template.streamlit.app/)

### How to run it on your own machine

Prerequisite: install `uv` if you don't already have it.

```
$ curl -LsSf https://astral.sh/uv/install.sh | sh
```

1. Sync the dependencies

   ```
   $ uv sync
   ```

2. Run the app

   ```
   $ uv run streamlit run streamlit_app.py
   ```

### Configure Turso credentials

Create `.streamlit/secrets.toml` with your Turso connection details:

```toml
[turso]
url = "https://<your-turso-url>/v1/execute"
token = "<your-turso-token>"
```

This file is already ignored by Git via `.gitignore`. Alternatively, you can set environment variables:

```bash
export TURSO_URL="https://<your-turso-url>/v1/execute"
export TURSO_TOKEN="<your-turso-token>"
```
