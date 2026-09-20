---
name: ai-ltm
description: セッション開始・再開や過去の失敗・意思決定の参照で長期記憶をrecallし、学び・決定・中断点をrecordする。セッション終了時はsummaryを保存・syncする。短期方針はfield-notes、感想はai-diaryを使う。
---

# AI Long-Term Memory

`~/ai-ltm-data/memory.db` を使い、プロジェクト横断の学び・失敗・意思決定・中断点を保存・検索する。

## 発動とrouting

| 場面 | 行動 |
|---|---|
| 会話の最初または長い中断からの再開 | 下の非同期recallを起動し、本命を待たせない |
| 前回の続き、類似問題、過去の失敗の参照 | [search.md](references/search.md) の追加search |
| 再利用価値のある学び・失敗・意思決定・中断点が確定 | [record-and-sync.md](references/record-and-sync.md) のrecord手順 |
| 明示的なセッション終了・長い離席、または会話が自然に終わる | 同referenceのsession summaryとsync手順 |
| DBがない・初期化が必要 | [setup.md](references/setup.md) |
| 記憶の修正・削除・cleanup・検索tuning | [maintenance.md](references/maintenance.md) |

毎tool成功、単なる進捗、短期の試行方針、感想はrecordしない。今のcampaignで次の判断を変える短期方針はfield-notes、後から横断検索する経緯はai-ltm、感想はai-diaryへ置き、同じ内容を二重保存しない。

このSkillの実体directoryを `SKILL_DIR` とし、scriptはそこから絶対pathで解決する。

## セッション開始時の非同期recall

現在のtaskから具体的なplain-textのqueryとsummaryを作り、runtimeのUTF-8 base64でencodeする。raw textをshell interpolationへ埋め込まない。

Claude Codeでは `general-purpose` Agentを `run_in_background: true` で1体起動し、次を1回だけ実行させる。

```text
python3 "<SKILL_DIR>/scripts/session_recall.py"   --repo ~/ai-ltm-data   --db ~/ai-ltm-data/memory.db   --query-b64 "<CURRENT_TASK_QUERY_B64>"   --summary-b64 "<CURRENT_TASK_SUMMARY_B64>"   --limit 5
```

workerにはscriptの絶対pathとencoded値だけを渡し、JSONLのstage eventとterminal recordだけを返させる。個別のgit/search commandやfallbackを追加しない。

メインClaudeはworkerをawait、blocking read、wait、follow-upで待たず、直ちに本命へ進む。Agent起動失敗、setup不足、dirty、pull/search/schema/timeoutの失敗はいずれも本命をblockしない。

scriptの順序は `preflight → optional pull → read-only combined search → report`。terminal statusは `completed` / `dirty` / `setup-needed` / `pull-failed` / `search-failed` / `timed-out` / `failed` のいずれかとして扱い、失敗を成功に読み替えない。

scriptはrepository外のadvisory lockを使う。terminal recordを観測するまで、episode insert、embed、mark-used、archive、git syncなどのai-ltm writeをdeferまたはskipする。検索結果を実際に本命へ使った場合だけ、mark-usedを別の非同期処理として行い、その完了を待たない。native async Agentが使えない場合はrecallを省略して本命を続ける。

## 記録内容

recordするのは次のいずれかで、summaryは1〜2文にする。

- 新しく得た技術的な学びや再現条件
- 失敗したapproachと、その原因
- 選択肢から決めた内容と理由
- 中断時の状態と次のstep

contextには再現に必要な情報だけを含め、コード全文、password、token、secret keyを保存しない。tagは種別、公開技術名、project識別子、topicをspace区切りで付ける。

記録・session終了・手動管理の直前に、該当referenceだけを読む。検索経路でschemaを自動変更せず、`init.sql`を現行schemaのSSOTとする。
