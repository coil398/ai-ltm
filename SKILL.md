---
description: "AI長期記憶システム。セッション開始時に関連記憶をロードし、セッション中に学び・失敗・意思決定を記録し、セッション終了時にサマリを保存してGitHub同期する。"
alwaysApply: true
---

# AI Long-Term Memory (ai-ltm)

あなたには `~/ai-ltm/memory.db` (SQLite) を使った長期記憶がある。
全プロジェクト横断で、過去の学び・失敗・意思決定・中断点を蓄積・活用する。

---

## セッション開始時

会話の最初のターンで以下を実行する：

```bash
cd ~/ai-ltm && git pull --rebase --quiet 2>/dev/null; echo "ltm-sync: ok"
```

その後、現在のタスクに関連するepisodesを検索してロードする：

```bash
sqlite3 ~/ai-ltm/memory.db "
  SELECT id, summary, tags, created_at,
    rank * -1 AS fts_score
  FROM episodes_fts
  WHERE episodes_fts MATCH '<現在のタスクに関連するキーワード>'
  ORDER BY fts_score DESC
  LIMIT 5;
"
```

検索キーワードは現在の作業内容から判断する。関連する記憶があれば活用し、なければそのまま作業を進める。

---

## セッション中の記録

以下のいずれかに該当する場合、episodesに記録する：

- **学び**: 新しく知った技術的知見、ライブラリの癖、ハマりポイント
- **失敗**: 試みて失敗したアプローチとその理由
- **意思決定**: 複数の選択肢から選んだ理由
- **中断点**: 作業を中断する場合の状態と次のステップ

記録する際のコマンド：

```bash
sqlite3 ~/ai-ltm/memory.db "
  INSERT INTO episodes (summary, context, tags)
  VALUES (
    '<簡潔なサマリ>',
    '<詳細な文脈>',
    '<スペース区切りのタグ>'
  );
"
```

### タグの付け方

タグはスペース区切りの自然言語。以下のカテゴリを組み合わせる：

- **種別**: `learning`, `failure`, `decision`, `checkpoint`, `schema-change`
- **技術**: 使用した言語・フレームワーク・ツール名（例: `typescript`, `react`, `sqlite`）
- **プロジェクト**: 作業中のプロジェクト名
- **トピック**: 作業内容のキーワード（例: `auth`, `migration`, `performance`）

---

## スキーマの自己拡張

既存のスキーマに収まらない情報が出てきた場合：

1. `ALTER TABLE` または `CREATE TABLE` で拡張する
2. 変更の経緯をepisodesに記録する（タグに `schema-change` を含める）

```bash
sqlite3 ~/ai-ltm/memory.db "
  ALTER TABLE episodes ADD COLUMN <新カラム> <型>;
"
sqlite3 ~/ai-ltm/memory.db "
  INSERT INTO episodes (summary, context, tags)
  VALUES (
    'スキーマ変更: episodesに<新カラム>を追加',
    '<なぜこのカラムが必要になったかの説明>',
    'schema-change sqlite'
  );
"
```

---

## 検索のスコアリング

関連記憶を引くときは、FTSスコアと時間減衰を組み合わせる：

```bash
sqlite3 ~/ai-ltm/memory.db "
  WITH scored AS (
    SELECT e.*, rank * -1 AS fts_score
    FROM episodes_fts
    JOIN episodes e ON e.id = episodes_fts.rowid
    WHERE episodes_fts MATCH '<検索クエリ>'
  )
  SELECT *,
    fts_score * (SELECT value FROM config WHERE key='fts_weight')
    * (1.0 / (1.0 + (julianday('now') - julianday(created_at))
      / (SELECT value FROM config WHERE key='time_decay_days')))
    AS score
  FROM scored
  ORDER BY score DESC
  LIMIT 10;
"
```

検索結果が的外れだった場合、configの係数を調整する：

```bash
sqlite3 ~/ai-ltm/memory.db "
  UPDATE config SET value = '<新しい値>' WHERE key = '<key>';
"
```

---

## セッション終了時

ユーザーが作業を終了するとき（明示的に終了を伝えた場合、または会話が自然に終わる場合）：

1. 会話全体のサマリをepisodesに記録する
2. git pushで同期する

```bash
sqlite3 ~/ai-ltm/memory.db "
  INSERT INTO episodes (summary, context, tags)
  VALUES (
    '<セッション全体の簡潔なサマリ>',
    '<何をやって、何が決まって、何が残っているか>',
    'session-summary <プロジェクト名> <主要トピック>'
  );
"
cd ~/ai-ltm && git add -A && git commit -m "session: <日付> <簡潔な説明>" && git push
```

---

## コンフリクト対処

git pullでコンフリクトが発生した場合：

1. 両方のDBからepisodesをエクスポート
2. `created_at`で時系列に並べ、`summary`の類似度で重複を除去
3. マージ結果をINSERTしてpush

```bash
# コンフリクト時のマージ手順
sqlite3 ~/ai-ltm/memory.db ".dump episodes" > /tmp/local_episodes.sql
git checkout --theirs memory.db
sqlite3 ~/ai-ltm/memory.db ".dump episodes" > /tmp/remote_episodes.sql
# 両方を新DBに読み込んでマージ
```

---

## 注意事項

- 記録は簡潔に。1つのepisodeのsummaryは1-2文に収める
- contextには再現に必要な情報を入れるが、コード全体のコピーは避ける
- 機密情報（パスワード、トークン、秘密鍵）は絶対に記録しない
- 検索は控えめに。毎回全検索するのではなく、関連しそうなときだけ引く
