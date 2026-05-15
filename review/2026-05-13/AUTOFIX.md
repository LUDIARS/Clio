# AUTOFIX — Clio 自動修正候補 (2026-05-13)

## 自動修正実施数: **0**

本レビューでは AUTOFIX を **列挙のみ** に留め、 実コミットは行わない。
理由: 対象リポジトリにソースコードが存在せず (README.md 1 行 + DESIGN.md + LICENSE のみ)、 自動修正対象のコード/設定が皆無。 ドキュメント追記は本来の設計者意図の確認が必要なため、 機械的 fix の対象外。

## 列挙のみ (candidate, NOT applied)

### A. ドキュメント補完 (要設計者承認)

1. **README.md の最小化解消** — 該当: `README.md:1`
   - 候補: LUDIARS 他リポ準拠の README skeleton (Overview / Status / Spec link / License) を追加。
   - 適用条件: 設計者が概要文を確定したら手作業で追加。

2. **CLAUDE.md 追加** — 該当: ファイル不在
   - 候補: AI 編集時の指示書を 30 行程度で配置 (他リポのテンプレ流用)。
   - 適用条件: LUDIARS テンプレが固まってから。

3. **.gitignore 追加** — 該当: ファイル不在
   - 候補: Python 標準 `.gitignore` (GitHub 公式テンプレ)。
   - 適用条件: 実装言語が Python で確定したら自動投入可能。

### B. 設計書補強 (要設計者承認)

4. **DESIGN.md §7 LUDIARS 整合節追加** — 該当: `DESIGN.md:211-216` の末尾
   - 候補: Cernere 認証 + 個人データ非保管 + Codex 監査 + Excubitor 観測の 4 項目を追記。
   - 適用条件: 設計者と方針合意後。

5. **DESIGN.md §8 用語集追加** — 該当: `DESIGN.md:217` の後
   - 候補: 「リザーブ環境 / Depot / Acquirer / Resolver / 嗜好」 の用語定義表。
   - 適用条件: 用語ヒアリング後。

### C. 安全な機械的修正 (将来適用可)

6. **DESIGN.md 表記揺れ統一**: 「リソース」 と 「オブジェクト」 の用語統一 — 該当: `DESIGN.md:5, 60`
   - 候補: `replace_all` で「オブジェクト」→「リソース」。
   - 適用条件: 設計者承認。 本セッションでは未実施。

7. **改行コード LF 統一の .gitattributes** — 該当: ファイル不在
   - 候補: `* text=auto eol=lf` のみの最小設定。
   - 適用条件: コミット時に自動で text 改行が変わると履歴汚染のため、 1 リポ初期段階の今だけ可能 (本セッションでは未実施)。

### D. 実装着手時の skeleton (極端に scope が広いため非対象)

8. `src/__init__.py` 等の空ファイル作成 — 設計書実装フェーズで人が行うべき。

## 集計

```
autofix_count: 0
autofix_categories:
  formatting: 0
  typo: 0
  lint: 0
  doc_skeleton: 0  # 列挙のみで実施せず
  security_simple: 0
```

## メモ

- 設計段階リポでは「機械的に直せる箇所」がほぼ無いため AUTOFIX 数 0 は妥当。
- 実装着手 (Depot / Acquirer の最初の .py コミット) 後に再レビューを行い、 typo / lint / unused-import を AUTOFIX 対象に追加可能。
