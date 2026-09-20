# 記憶の検索

セッション開始時のrecallを補って、前回の続き、類似問題、過去の失敗や意思決定を明示的に調べるときに読む。検索は必要な問いに限定し、同じqueryを根拠なく反復しない。

## 検索command

通常はFTSとvectorを組み合わせる `combined` を使う。

```bash
python3 "$SKILL_DIR/scripts/vector_search.py" combined --db ~/ai-ltm-data/memory.db --query "検索クエリ" --limit 5
```

vector類似度だけを確認する場合は `search` を使う。

```bash
python3 "$SKILL_DIR/scripts/vector_search.py" search --db ~/ai-ltm-data/memory.db --query "検索クエリ" --limit 5
```

`--tags` はspace区切りのAND filter、`--since` / `--until` は `YYYY-MM-DD` の作成日filter。activeな記憶だけでは不足すると確認した場合、またはユーザーがarchiveを対象にした場合だけ `--include-archived` を付ける。

```bash
python3 "$SKILL_DIR/scripts/vector_search.py" combined --db ~/ai-ltm-data/memory.db --query "auth" --tags "learning typescript" --since 2026-01-01 --until 2026-04-14
python3 "$SKILL_DIR/scripts/vector_search.py" combined --db ~/ai-ltm-data/memory.db --query "検索クエリ" --include-archived
```

queryをshellへ安全に渡せない場合は、raw user inputをcommand文字列へ埋め込まない。既存scriptのargument境界を保てる方法を使う。

## scoringとread-only境界

`combined` は次を順に反映する。

1. SQLite FTS5のBM25を正規化したFTS score
2. TF-IDF cosine similarityを正規化したvector score
3. `fts_weight * fts_score + vector_weight * vector_score`
4. `time_decay_days` による時間減衰
5. `used_count` と `usage_recency_days` によるrecent-use boost

`search` / `combined` はDBをread-onlyで開く。不足table、column、FTS、cached IDF、configを検索中に作成・変更しない。schemaやconfig errorは検索失敗として報告し、結果0件や「関連記憶なし」に読み替えない。初期化は [setup.md](setup.md)、schema更新・weight調整・rebuildは [maintenance.md](maintenance.md) を読む。

## 利用後

検索結果を実際の判断へ使った場合だけ、該当IDを `mark-used` する。セッション開始recallのadvisory lockが解放された後に別の非同期処理として実行し、本命を待たせない。

```bash
python3 "$SKILL_DIR/scripts/vector_search.py" mark-used --db ~/ai-ltm-data/memory.db --ids 42,17,3
```

検索結果は過去の記録であり、現在のcode・設定・外部状態の証拠ではない。現在の事実が判断に必要なら対象を別途実測する。
