"""应用观测到的每次实际 HTTP 尝试用量；这些数据不代表账户账单。"""
from __future__ import annotations

import json
import logging
import math
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from app.database.db import connect

METRICS = ("input_tokens", "output_tokens", "total_tokens", "cache_hit_tokens", "cache_miss_tokens", "cache_write_tokens", "reasoning_tokens")
logger = logging.getLogger(__name__)
usage_context = ContextVar("usage_context", default=None)


def connection():
    conn = connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS model_usage (
        attempt_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, model TEXT NOT NULL,
        capability TEXT NOT NULL, source TEXT NOT NULL, started_at TEXT NOT NULL,
        completed INTEGER NOT NULL, counters_json TEXT NOT NULL, raw_json TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS usage_time_provider ON model_usage(started_at,provider_id,model)")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(model_usage)")}
    for column in ("request_id", "run_id"):
        if column not in columns:
            conn.execute(f"ALTER TABLE model_usage ADD COLUMN {column} TEXT")
    return conn


def numeric_leaves(value, prefix=""):
    """只保留已知的数值计数器；供应商返回的用量对象可能含有任意文本。"""
    result = {}
    if not isinstance(value, dict):
        return result
    allowed = {"prompt_tokens", "completion_tokens", "input_tokens", "output_tokens", "total_tokens", "cached_tokens",
               "cache_read_input_tokens", "cache_creation_input_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
               "reasoning_tokens", "prompt_eval_count", "eval_count"}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if key in allowed and type(item) is int and 0 <= item <= 2 ** 53:
            result[path] = item
        elif key in {"prompt_tokens_details", "completion_tokens_details", "input_tokens_details", "output_tokens_details"}:
            result.update(numeric_leaves(item, path))
    return result


class UsageAttempt:
    def __init__(self, provider_id, model, protocol, capability="chat", source="api"):
        self.attempt_id = uuid4().hex
        self.provider_id, self.model, self.protocol = provider_id, model, protocol
        self.capability, self.source = capability, source
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.raw = {}
        self.audio_seconds = None
        self.completed = False
        context = usage_context.get() or {}
        self.request_id = context.get("request_id") or uuid4().hex
        self.run_id = context.get("run_id")

    def observe(self, data):
        if not isinstance(data, dict):
            return
        duration = data.get("audio_seconds", data.get("duration"))
        if self.capability in {"transcription", "speaker_matching"} and type(duration) in (int, float) and math.isfinite(duration) and 0 <= duration <= 7200:
            self.audio_seconds = max(self.audio_seconds or 0, duration)
        values = [data.get("usage"), (data.get("message") or {}).get("usage") if isinstance(data.get("message"), dict) else None,
                  (data.get("response") or {}).get("usage") if isinstance(data.get("response"), dict) else None]
        if self.protocol == "ollama":
            values.append(data)
        for value in values:
            for key, count in numeric_leaves(value).items():
                self.raw[key] = max(self.raw.get(key, 0), count)
        if data.get("type") in {"[DONE]", "response.completed", "message_stop"} or data.get("done") is True:
            self.completed = True

    def counters(self):
        raw = self.raw
        def first(*names):
            return next((raw[name] for name in names if name in raw), None)
        inputs = first("input_tokens", "prompt_tokens", "prompt_eval_count")
        outputs = first("output_tokens", "completion_tokens", "eval_count")
        hit = first("cache_read_input_tokens", "prompt_cache_hit_tokens", "input_tokens_details.cached_tokens", "prompt_tokens_details.cached_tokens")
        write = first("cache_creation_input_tokens")
        miss = first("prompt_cache_miss_tokens")
        if self.protocol == "anthropic_messages":
            miss = inputs
            inputs = inputs + hit + write if inputs is not None and hit is not None and write is not None else None
        elif miss is None and inputs is not None and hit is not None and 0 <= hit <= inputs:
            miss = inputs - hit
        if hit is not None and inputs is not None and hit > inputs:
            hit, miss = None, None
        return dict(audio_seconds=self.audio_seconds, input_tokens=inputs, output_tokens=outputs,
                    total_tokens=inputs + outputs if inputs is not None and outputs is not None else first("total_tokens"),
                    cache_hit_tokens=hit, cache_miss_tokens=miss, cache_write_tokens=write,
                    reasoning_tokens=first("output_tokens_details.reasoning_tokens", "completion_tokens_details.reasoning_tokens"))

    def persist(self):
        from app.operation_logs import log_event
        log_event('providers', 'model.request_finished', level='INFO' if self.completed else 'WARNING',
                  provider_id=self.provider_id, model=self.model, run_id=self.run_id,
                  request_id=self.request_id, source=self.source,
                  status='completed' if self.completed else 'incomplete')
        try:
            with closing(connection()) as conn:
                conn.execute("INSERT OR REPLACE INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    self.attempt_id, self.provider_id, self.model, self.capability, self.source, self.started_at,
                    int(self.completed), json.dumps(self.counters()), json.dumps(self.raw), self.request_id, self.run_id))
        except Exception:
            logger.warning("Usage persistence failed; model response remains available")


def aggregate(start, end, provider_id=None, model=None, source=None, timezone_offset=0):
    query = "SELECT counters_json,completed,capability,started_at,source,provider_id,model FROM model_usage WHERE started_at>=? AND started_at<?"
    args = [start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()]
    for column, value in (("provider_id", provider_id), ("model", model), ("source", source)):
        if value:
            query += f" AND {column}=?"
            args.append(value)
    with closing(connection()) as conn:
        rows = conn.execute(query, args).fetchall()
        options = conn.execute("SELECT DISTINCT provider_id,model,source FROM model_usage ORDER BY provider_id,model").fetchall()
    # 日历分桶使用调用方的 UTC 偏移量；缺失的计数器保持为 null。
    zone = timezone(timedelta(minutes=timezone_offset))
    first = start.astimezone(zone).date()
    last = (end - timedelta(microseconds=1)).astimezone(zone).date()
    days = (last - first).days + 1
    step = max(1, (days + 89) // 90)
    series = []
    for offset in range(0, days, step):
        date = first + timedelta(days=offset)
        series.append({"date": date.isoformat(), "end_date": (first + timedelta(days=min(days-1, offset+step-1))).isoformat(),
                       "local": {"requests": 0, "totals": {key: None for key in METRICS}, "coverage": {key: 0 for key in METRICS}, "models": {}},
                       "api": {"requests": 0, "totals": {key: None for key in METRICS}, "coverage": {key: 0 for key in METRICS}, "models": {}}})
    totals = {key: None for key in METRICS}
    coverage = {key: 0 for key in METRICS}
    hits, eligible_input, cache_requests = 0, 0, 0
    audio_requests, audio_covered, audio_seconds = 0, 0, None
    for row in rows:
        if row[2] in {"transcription", "speaker_matching"}:
            audio_requests += 1
        counts = json.loads(row[0])
        date = datetime.fromisoformat(row[3]).astimezone(zone).date()
        bucket = series[(date - first).days // step][row[4]]
        bucket['requests'] += 1
        model_key = json.dumps([row[5], row[6]], ensure_ascii=False)
        part = bucket['models'].setdefault(model_key, {'key': model_key, 'provider_id': row[5], 'model': row[6], 'requests': 0, 'totals': {key: None for key in METRICS}, 'coverage': {key: 0 for key in METRICS}})
        part['requests'] += 1
        for key in METRICS:
            if counts.get(key) is not None:
                part['totals'][key] = (part['totals'][key] or 0) + counts[key]
                part['coverage'][key] += 1
        for key in METRICS:
            if counts.get(key) is not None:
                bucket['totals'][key] = (bucket['totals'][key] or 0) + counts[key]
                bucket['coverage'][key] += 1
        if counts.get("audio_seconds") is not None:
            audio_covered += 1
            audio_seconds = (audio_seconds or 0) + counts["audio_seconds"]
        for key in METRICS:
            if counts.get(key) is not None:
                totals[key] = (totals[key] or 0) + counts[key]
                coverage[key] += 1
        if counts.get("cache_hit_tokens") is not None and counts.get("cache_miss_tokens") is not None:
            hits += counts["cache_hit_tokens"]
            eligible_input += counts["input_tokens"] if counts.get("input_tokens") is not None else counts["cache_hit_tokens"] + counts["cache_miss_tokens"]
            cache_requests += 1
    for bucket in series:
        for origin in ('local', 'api'):
            bucket[origin]['models'] = sorted(bucket[origin]['models'].values(), key=lambda item: item['key'])
    return {"audio_request_count": audio_requests, "audio_seconds": audio_seconds, "audio_covered_requests": audio_covered, "totals": totals, "coverage": coverage, "request_count": len(rows),
            "complete_requests": sum(row[1] for row in rows), "cache_covered_requests": cache_requests,
            "cache_hit_rate": hits / eligible_input if eligible_input else None,
            "options": [dict(row) for row in options], "start": start, "end": end,
            "scope": "application_observed_usage", "series": series, "timezone_offset": timezone_offset}
