"""Operator surface for governed scheduled AI proposal jobs."""
from __future__ import annotations

import json
from flask import flash, g, redirect, render_template_string, request, url_for

from authority import requires_action
from governed_ai import ALLOWED_CONTEXT_SCOPES


PAGE = """
{% extends 'base.html' %}
{% block content %}
<h1>Governed AI Jobs</h1>
<p>Scheduled AI is proposal-only. Scheduling does not grant financial authority.</p>
<form method="post" action="{{ url_for('governed_ai_create') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <label>Job ID <input name="job_id" required></label>
  <label>Name <input name="name" required></label>
  <label>Prompt version <input name="prompt_version" value="v1" required></label>
  <label>Model <input name="model" value="gpt-5.6-luna" required></label>
  <label>Interval seconds <input name="interval_seconds" type="number" min="60" value="86400" required></label>
  <label>Context scopes
    <select name="context_scopes" multiple>
      {% for scope in scopes %}<option value="{{ scope }}">{{ scope }}</option>{% endfor %}
    </select>
  </label>
  <label>Prompt <textarea name="prompt" rows="7" required></textarea></label>
  <label><input type="checkbox" name="start_immediately" value="1"> Run on next scheduler poll</label>
  <button type="submit">Save governed job</button>
</form>
<h2>Jobs</h2>
<table><tr><th>ID</th><th>Name</th><th>Authority</th><th>Scopes</th><th>Next run</th></tr>
{% for j in jobs %}<tr><td>{{j['job_id']}}</td><td>{{j['name']}}</td><td>{{j['authority']}}</td><td>{{j['context_scopes']}}</td><td>{{j['next_run_at']}}</td></tr>{% endfor %}</table>
<h2>Recent runs</h2>
<table><tr><th>Run</th><th>Job</th><th>Status</th><th>Model</th><th>Output / Error</th></tr>
{% for r in runs %}<tr><td>{{r['run_id']}}</td><td>{{r['job_id']}}</td><td>{{r['status']}}</td><td>{{r['model']}}</td><td><pre>{{r['output_text'] or r['error'] or ''}}</pre></td></tr>{% endfor %}</table>
{% endblock %}
"""


def install_governed_ai(app, store) -> None:
    @app.get("/ai-jobs")
    def governed_ai_jobs():
        return render_template_string(PAGE, jobs=store.jobs(), runs=store.recent_runs(), scopes=sorted(ALLOWED_CONTEXT_SCOPES))

    @app.post("/ai-jobs")
    @requires_action(app, "ai_job.configure")
    def governed_ai_create():
        try:
            store.upsert_job(
                job_id=request.form.get("job_id", "").strip(),
                name=request.form.get("name", "").strip(),
                prompt=request.form.get("prompt", ""),
                prompt_version=request.form.get("prompt_version", "v1").strip() or "v1",
                provider="openai",
                model=request.form.get("model", "").strip(),
                context_scopes=request.form.getlist("context_scopes"),
                authority="propose",
                interval_seconds=int(request.form.get("interval_seconds", "86400")),
                enabled=True,
                start_immediately=request.form.get("start_immediately") == "1",
            )
            audit = app.config.get("AUDIT_LOG")
            if audit is not None:
                user = getattr(g, "current_user", None)
                audit.append("ai.job.configured", f"AIJOB:{request.form.get('job_id','').strip()}",
                             {"authority": "propose", "context_scopes": request.form.getlist("context_scopes")},
                             actor=(f"{user.display_name} [{user.username}] ({user.role})" if user else "unknown"))
            flash("Governed AI job saved", "success")
        except (ValueError, RuntimeError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("governed_ai_jobs"))
