# AI Long-Term Memory (ai-ltm)

AIアシスタント（Claude）にセッションを跨いだ長期記憶を提供するシステム。
プロジェクト横断で過去の学び・失敗・意思決定・中断点をSQLiteに蓄積し、ハイブリッド検索（FTS + ベクトル類似度）で関連記憶を呼び出す。

## 特徴

- **セッション横断の記憶**: 過去の経験を蓄積し、次回セッションで自動的に参照
- **ハイブリッド検索**: SQLite FTS5による全文検索とTF-IDFベクトル類似度検索の組み合わせ
- **外部依存なし**: Python標準ライブラリとSQLiteのみで動作
- **Git同期**: プライベートGitHubリポジトリでデータをバックアップ・同期
- **CJK対応**: 日本語・中国語・韓国語テキストのバイグラムトークナイズに対応

## 構成

```
ai-ltm/
├── SKILL.md                 # Claude Code スキル定義
├── init.sql                 # SQLiteスキーマ初期化
├── scripts/
│   └── vector_search.py     # 検索エンジン (TF-IDF + コサイン類似度)
└── references/
    └── setup.md             # 初回セットアップガイド
```

## 必要環境

- Python 3
- SQLite3
- Git

外部パッケージのインストールは不要です。

## セットアップ

### 1. ai-ltmリポジトリのクローン

```bash
git clone <repository-url> ~/ai-ltm
```

### 2. データディレクトリの作成

```bash
mkdir -p ~/ai-ltm-data && cd ~/ai-ltm-data
git init
sqlite3 ~/ai-ltm-data/memory.db < ~/ai-ltm/init.sql
cp ~/ai-ltm/.gitignore ~/ai-ltm-data/.gitignore
git remote add origin <your-private-repo-url>
git add memory.db .gitignore
git commit -m "init: AI長期記憶システム初期化"
git push -u origin main
```

既存のリモートリポジトリがある場合:

```bash
git clone <remote-url> ~/ai-ltm-data
```

## 使い方

### 記憶の記録

```bash
sqlite3 ~/ai-ltm-data/memory.db <<'EOSQL'
INSERT INTO episodes (summary, context, tags)
VALUES (
  '学んだことの要約',
  '詳細なコンテキスト',
  'learning typescript react'
);
EOSQL

# エンベディング生成
EPISODE_ID=$(sqlite3 ~/ai-ltm-data/memory.db "SELECT last_insert_rowid();")
python3 ~/ai-ltm/scripts/vector_search.py embed \
  --db ~/ai-ltm-data/memory.db \
  --id "$EPISODE_ID"
```

### 記憶の検索

```bash
# 複合検索（FTS + ベクトル類似度）
python3 ~/ai-ltm/scripts/vector_search.py combined \
  --db ~/ai-ltm-data/memory.db \
  --query "検索クエリ" \
  --limit 10

# ベクトル類似度検索のみ
python3 ~/ai-ltm/scripts/vector_search.py search \
  --db ~/ai-ltm-data/memory.db \
  --query "検索クエリ" \
  --limit 10

# 全エンベディング再構築
python3 ~/ai-ltm/scripts/vector_search.py rebuild \
  --db ~/ai-ltm-data/memory.db
```

### 検索パラメータの調整

```bash
# FTS/ベクトル検索の重み調整（デフォルト: 各0.5）
sqlite3 ~/ai-ltm-data/memory.db "UPDATE config SET value = '0.3' WHERE key = 'fts_weight';"
sqlite3 ~/ai-ltm-data/memory.db "UPDATE config SET value = '0.7' WHERE key = 'vector_weight';"

# 時間減衰の調整（デフォルト: 30日）
sqlite3 ~/ai-ltm-data/memory.db "UPDATE config SET value = '90' WHERE key = 'time_decay_days';"
```

## DBスキーマ

| テーブル | 説明 |
|---|---|
| `episodes` | 記憶本体（summary, context, tags, embedding, created_at） |
| `episodes_fts` | FTS5仮想テーブル（全文検索用） |
| `config` | 検索チューニングパラメータ |

## タグ規約

- **種別**: `learning`, `failure`, `decision`, `checkpoint`, `schema-change`
- **技術**: 言語・フレームワーク名（例: `typescript`, `react`）
- **プロジェクト**: プロジェクト識別子
- **トピック**: 具体的なキーワード（例: `auth`, `performance`）
