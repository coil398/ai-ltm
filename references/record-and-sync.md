# 記録と同期

episodeの記録またはセッション終了時だけ読む。

## episodeの記録

recall処理のadvisory lockが解放されたことを確認してから書く。summaryとcontextのsingle quoteは `''` にescapeし、INSERTと `last_insert_rowid()` は同じsqlite3 sessionで実行する。

```bash
EPISODE_ID=$(sqlite3 ~/ai-ltm-data/memory.db <<'EOSQL'
INSERT INTO episodes (summary, context, tags)
VALUES (
  '簡潔なサマリ',
  '再現に必要な文脈',
  'learning project topic'
);
SELECT last_insert_rowid();
EOSQL
)
python3 "$SKILL_DIR/scripts/vector_search.py" embed --db ~/ai-ltm-data/memory.db --id "$EPISODE_ID"
```

実際の値をshellへ安全に渡せない場合は、適切なparameter bindingを持つ既存scriptを使う。SQLやshellへ未escapedのユーザー入力を埋め込まない。

## セッション終了

ユーザーが終了・長い離席を明示した場合、または会話が自然に終わる場合に、作業、決定、残件を1 episodeへ要約してembedする。その後、古く未使用のepisodeをarchiveし、memory DBを同期する。

```bash
python3 "$SKILL_DIR/scripts/vector_search.py" archive --db ~/ai-ltm-data/memory.db
python3 "$SKILL_DIR/scripts/sync_memory.py" push --repo ~/ai-ltm-data --db ~/ai-ltm-data/memory.db --message "session: $(date +%Y-%m-%d) 簡潔な説明"
```

`sync_memory.py push` は同期前にもremote変更を取り込み、`memory.db`だけをcommit・pushする。失敗を成功扱いにせず、作成済みlocal commitやDBを勝手に戻さず、表示された原因を報告する。

## 同期の安全境界

同期scriptは共通祖先・local・remoteのDBを3-way mergeし、index整合を検証してから原子的に置換する。次の場合はGitとDBを変更せず停止する。

- `memory.db` 以外の未commit変更がある。
- `memory.db` が想定したunstaged変更以外の状態にある。
- merge、rebase、cherry-pickが進行中。
- detached HEAD、upstream未設定、別のLTM同期が実行中。

fetch、merge、DB検証、統合commitが失敗した場合の復元はscriptに任せる。手動で片側DBを選んだり、競合中にcommitしたりしない。
