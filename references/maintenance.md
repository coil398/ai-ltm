# 記憶のmaintenance

ユーザーが記憶の一覧、修正、削除、archive、cleanup、検索tuningを求めたときだけ読む。検索の実行方法とscoringは [search.md](search.md) を正とし、ここでは変更を伴うmaintenanceだけを扱う。変更前に対象と現在値をread-onlyで確認する。

## schema

`init.sql`が現行schemaのSSOTである。search経路はDBをread-onlyで開き、不足table、column、FTS、cached IDF、configを自動作成しない。

schema変更が必要なら、理由とmigrationを明示し、`init.sql`も更新して、backup・migration・rebuild・verificationを一つの保守作業として扱う。場当たり的な `ALTER TABLE` だけで済ませない。

## 一覧・修正・削除

```bash
sqlite3 ~/ai-ltm-data/memory.db "SELECT id, created_at, used_count, substr(summary, 1, 80), tags FROM episodes WHERE archived = 0 ORDER BY created_at DESC LIMIT 20;"
```

修正・削除はIDと現在内容を確認し、対象をユーザーが選んでから行う。UPDATE後は対象IDを再embedし、大量変更・大量削除後だけ全体rebuildを検討する。

```bash
python3 "$SKILL_DIR/scripts/vector_search.py" embed --db ~/ai-ltm-data/memory.db --id <ID>
python3 "$SKILL_DIR/scripts/vector_search.py" rebuild --db ~/ai-ltm-data/memory.db
```

## archive

まずdry-runで件数とsample IDを確認する。

```bash
python3 "$SKILL_DIR/scripts/vector_search.py" archive --db ~/ai-ltm-data/memory.db --dry-run
python3 "$SKILL_DIR/scripts/vector_search.py" archive --db ~/ai-ltm-data/memory.db
python3 "$SKILL_DIR/scripts/vector_search.py" unarchive --db ~/ai-ltm-data/memory.db --ids '1,2,3'
```

cleanupは最大10件ずつ候補を示し、`keep / archive / delete / merge` をユーザーが選ぶ。自動DELETEはしない。候補は高類似、参照切れ、長期未使用、粒度過多を対象とし、project固有であるだけでは削除理由にしない。

## 検索tuning

現在のconfig値と検索結果を測ってから、必要なkeyだけ変更する。

- `fts_weight` / `vector_weight`
- `time_decay_days`
- `usage_boost_weight` / `usage_recency_days`
- `archive_after_days`

episode追加数だけで儀式的にrebuildせず、schema変更、語彙変化、検索精度の実測から判断する。
