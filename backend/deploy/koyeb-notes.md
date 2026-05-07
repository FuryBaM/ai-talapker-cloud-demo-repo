# Koyeb settings

Use the same project files as Render.

Root directory:

```text
assistant
```

Build command:

```bash
pip install --upgrade pip && pip install -r requirements-cloud.txt
```

Run command:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Instance: Free.

Add the variables from `.env.free-cloud.example` in the Koyeb dashboard.
