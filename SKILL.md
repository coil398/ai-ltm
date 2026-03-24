---
description: "AI長期記憶システム。セッション開始時に関連記憶をロードし、セッション中に学び・失敗・意思決定を記録し、セッション終了時にサマリを保存してGitHub同期する。「前回何やったっけ」「過去の学びを活かして」「前回の続きから」「失敗を記録して」「セッション終了」といった要望や、プロジェクト横断で過去の経験を参照したい場面で使う。"
---

# AI Long-Term Memory (ai-ltm)

あなたには `~/ai-ltm-data/memory.db` (SQLite) を使った長期記憶がある。
全プロジェクト横断で、過去の学び・失敗・意思決定・中断点を蓄積・活用する。

スクリプトのベースパス: このSKILL.mdと同じディレクトリに `scripts/` がある。

---

## セッション開始時

会話の最初のターンで以下を実行する:

```bash
if [ -d ~/ai-ltm-data/.git ]; then
  cd ~/ai-ltm-data && git pull --rebase --quiet 2>/dev/null; echo "ltm-sync: ok"
else
  echo "ltm-setup-needed"
fi
```

`ltm-setup-needed` が返った場合は `references/setup.md` を読んで初回セットアップを案内する。

その後、現在のタスクに関連する記憶を**combined search**（FTS + ベクトル類似度の複合検索）で検索する:

```bash
python3 ~/ai-ltm/scripts/vector_search.py combined \
  --db ~/ai-ltm-data/memory.db \
  --query '<現在のタスクに関連するキーワード>' \
  --limit 5
```

検索キーワードは現在の作業内容から判断する。関連する記憶があれば活用し、なければそのまま作業を進める。

FTS検索でエラーになる場合（クエリ構文の問題など）は、ベクトル検索にフォールバックする:

```bash
python3 ~/ai-ltm/scripts/vector_search.py search \
  --db ~/ai-ltm-data/memory.db \
  --query '<キーワード>' \
  --limit 5
```

---

## セッション中の記録

以下のいずれかに該当する場合、episodesに記録する:

- **学び**: 新しく知った技術的知見、ライブラリの癖、ハマりポイント
- **失敗**: 試みて失敗したアプローチとその理由
- **意思決定**: 複数の選択肢から選んだ理由
- **中断点**: 作業を中断する場合の状態と次のステップ

記録する際は、シングルクォートのエスケープに注意する。サマリやコンテキストに `'` が含まれる場合は `''` に置換する:

```bash
sqlite3 ~/ai-ltm-data/memory.db <<'EOSQL'
INSERT INTO episodes (summary, context, tags)
VALUES (
  '簡潔なサマリ（シングルクォートは''で二重化）',
  '詳細な文脈',
  'スペース区切りのタグ'
);
EOSQL
```

記録後、ベクトル埋め込みを生成する（last_insert_rowidで直前のINSERTのIDを取得）:

```bash
EPISODE_ID=$(sqlite3 ~/ai-ltm-data/memory.db "SELECT last_insert_rowid();")
python3 ~/ai-ltm/scripts/vector_search.py embed \
  --db ~/ai-ltm-data/memory.db \
  --id "$EPISODE_ID"
```

### タグの付け方

タグはスペース区切りの自然言語。以下のカテゴリを組み合わせる:

- **種別**: `learning`, `failure`, `decision`, `checkpoint`, `schema-change`
- **技術**: 使用した言語・フレームワーク・ツール名（例: `typescript`, `react`, `sqlite`）
- **プロジェクト**: 作業中のプロジェクト名
- **トピック**: 作業内容のキーワード（例: `auth`, `migration`, `performance`）

---

## スキーマの自己拡張

既存のスキーマに収まらない情報が出てきた場合:

1. `ALTER TABLE` または `CREATE TABLE` で拡張する
2. 変更の経緯をepisodesに記録する（タグに `schema-change` を含める）
3. 埋め込みをリビルドする（スキーマ変更でテキストカラムが増えた場合）

```bash
sqlite3 ~/ai-ltm-data/memory.db "ALTER TABLE episodes ADD COLUMN <新カラム> <型>;"

sqlite3 ~/ai-ltm-data/memory.db <<'EOSQL'
INSERT INTO episodes (summary, context, tags)
VALUES (
  'スキーマ変更: episodesに<新カラム>を追加',
  'なぜこのカラムが必要になったかの説明',
  'schema-change sqlite'
);
EOSQL
```

---

## 検索のスコアリング

combined searchスクリプトは以下のロジックで統合スコアを算出する:

1. **FTS スコア**: SQLite FTS5 の BM25 ランキング（正規化済み）
2. **ベクトルスコア**: TF-IDF cosine similarity（正規化済み）
3. **統合**: `fts_weight * fts_score + vector_weight * vector_score`
4. **時間減衰**: `combined * 1/(1 + 経過日数/time_decay_days)`

各重みは `config` テーブルで調整できる:

```bash
# ベクトル検索を重視する場合
sqlite3 ~/ai-ltm-data/memory.db "UPDATE config SET value = '0.3' WHERE key = 'fts_weight';"
sqlite3 ~/ai-ltm-data/memory.db "UPDATE config SET value = '0.7' WHERE key = 'vector_weight';"

# 古い記憶もよく引くようにする場合（減衰を緩やかに）
sqlite3 ~/ai-ltm-data/memory.db "UPDATE config SET value = '90' WHERE key = 'time_decay_days';"
```

episodesが大量に増えた場合や検索精度が落ちたと感じた場合、埋め込みをリビルドする:

```bash
python3 ~/ai-ltm/scripts/vector_search.py rebuild --db ~/ai-ltm-data/memory.db
```

---

## セッション終了時

ユーザーが作業を終了するとき（明示的に終了を伝えた場合、または会話が自然に終わる場合）:

1. 会話全体のサマリをepisodesに記録する
2. 埋め込みを生成する
3. git pushで同期する

```bash
sqlite3 ~/ai-ltm-data/memory.db <<'EOSQL'
INSERT INTO episodes (summary, context, tags)
VALUES (
  'セッション全体の簡潔なサマリ',
  '何をやって、何が決まって、何が残っているか',
  'session-summary プロジェクト名 主要トピック'
);
EOSQL

EPISODE_ID=$(sqlite3 ~/ai-ltm-data/memory.db "SELECT last_insert_rowid();")
python3 ~/ai-ltm/scripts/vector_search.py embed \
  --db ~/ai-ltm-data/memory.db \
  --id "$EPISODE_ID"

cd ~/ai-ltm-data && git add memory.db && git commit -m "session: $(date +%Y-%m-%d) 簡潔な説明" && git push
```

`git add` は `memory.db` のみを対象にする。`-A` は使わない（一時ファイルの混入を防ぐため）。

---

## コンフリクト対処

`git pull` でコンフリクトが発生した場合（SQLiteはバイナリなので通常のマージはできない）:

ポリシー: **両方のデータを保持する**。ローカルのepisodesをエクスポートし、リモート版をチェックアウトしてからローカル分をインポートする。

```bash
cd ~/ai-ltm-data

# 1. ローカルのepisodesをダンプ
sqlite3 memory.db "SELECT summary, context, tags, embedding, created_at FROM episodes;" > /tmp/ltm_local_dump.txt

# 2. リモート版を採用
git checkout --theirs memory.db
git add memory.db

# 3. ローカルのepisodesをリモートDBに追記（重複はcreated_at + summaryで判定）
sqlite3 memory.db <<'EOSQL'
-- /tmp/ltm_local_dump.txt から手動で INSERT
-- ただし既に同じ summary + created_at の組み合わせがあればスキップ
EOSQL

# 4. 埋め込みをリビルドしてコミット
python3 ~/ai-ltm/scripts/vector_search.py rebuild --db ~/ai-ltm-data/memory.db
git add memory.db
git commit -m "merge: resolve binary conflict, merged episodes"
git push
```

実際にはダンプの行をパースしてINSERTする必要があるため、状況に応じてPythonスクリプトで処理する。重要なのは**データを失わないこと**。

---

## 注意事項

- 記録は簡潔に。1つのepisodeのsummaryは1-2文に収める
- contextには再現に必要な情報を入れるが、コード全体のコピーは避ける
- 機密情報（パスワード、トークン、秘密鍵）は絶対に記録しない
- 検索は控えめに。毎回全検索するのではなく、関連しそうなときだけ引く
- SQLにユーザー入力を埋め込む際は、シングルクォートを `''` にエスケープする
