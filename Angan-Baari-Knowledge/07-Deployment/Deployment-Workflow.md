# Deployment Workflow

```
local:          python manage.py makemigrations
local:          git commit, git push
PythonAnywhere: git pull
PythonAnywhere: python manage.py migrate      # NEVER makemigrations on production
PythonAnywhere: python manage.py collectstatic
PythonAnywhere: Web tab → Reload
```

## Known gotcha: venv activation

PythonAnywhere's venv lives at `~/angan-baari/venv/` and **must be explicitly activated** (`source ~/angan-baari/venv/bin/activate`) before `pip install` — otherwise pip silently installs into the account's global `~/.local/lib/python3.13/site-packages/`, which the live WSGI process doesn't use. New packages silently never reach the running site if this is missed.

## Migrations

Any model field change needs `makemigrations` + `migrate`, **locally then on PythonAnywhere after pulling**. Local having the migration applied does not mean production has it — always confirm the PythonAnywhere-side `migrate` actually ran before assuming a model change is live.

See `01-Project/Current-State.md` for what's currently uncommitted/unmigrated.
