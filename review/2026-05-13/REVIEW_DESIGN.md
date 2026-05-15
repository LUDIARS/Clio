# REVIEW_DESIGN — Clio 設計レビュー (2026-05-13)

## 評価: **B**

DESIGN.md 単体としては LUDIARS の他モジュール (例: Memoria, Actio) の初期設計書と比較して標準水準。アーキテクチャ図・コンポーネント責務・データフロー・API・実装順序が明示されている。一方で LUDIARS 横断原則 (個人データ非保管、 Cernere 認証、 Codex 監査) との接続が欠落。

## 良い点

- **コンポーネント分離**: DESIGN.md:13-33 で Depot / Acquirer / Metadata / Resolver / Preference Adapter を疎結合化。責務境界が明確で各コンポーネントの差し替え可能性が高い (DESIGN.md:213 「疎結合」方針と整合)。
- **設定駆動**: DESIGN.md:45-82 でリソース・取得レンジ・メタデータを YAML で外出し。 hot-reload や運用時調整の素地ができている (DESIGN.md:214)。
- **プラグイン方式の嗜好アダプタ**: DESIGN.md:117-123 で外部嗜好モジュールを差し替え可能と明記。 Memoria 由来の好み / 外部 ML スコアラを後付け可能。
- **段階的実装計画**: DESIGN.md:216 「Depot → Metadata → Acquirer → Adapter → Resolver」 の順序が依存逆転を避けて妥当。
- **データフロー**: DESIGN.md:164-173 の 6 ステップが線形で理解しやすい。

## 設計上の懸念

- **LUDIARS 整合章の欠落**: 個人データ保管禁止原則 (memory: `project_personal_data_rule.md`) との関係不明。 user_id (DESIGN.md:183, 198, 202) を内部で保持するのか、 Cernere ID を都度参照するのかの記述なし。 **High**
- **嗜好データのソース未定**: DESIGN.md:117-123 は「外部嗜好モジュール」と抽象化されているが、 LUDIARS 内で具体的に Memoria / Imperativus / 別途新設なのかが不明。 設計書の段階で 1 つは想定実装を示すべき。 **Medium** (DESIGN.md:32, 117)
- **Resolver スコアリングの数式不在**: DESIGN.md:110-115, 187-189 で「スコアリング」「ランキング」とあるが、 重み付け・正規化・タイブレークの方針が一切ない。 後で実装者ごとに解釈分散する恐れ。 **Medium**
- **メタデータスキーマの拡張性**: DESIGN.md:94-108 で固定 4 フィールド (tags/category/rating/attributes) を例示しているが、 schema migration 方針がない。 リソース種別を増やすたびに schema.yaml を書き換える運用は破綻しがち。 **Medium**
- **取得レンジの「scope: all」の意味**: DESIGN.md:71 の `scope: "all"` が config/depot/resources.yaml の全件か、 reserve_pool 単位かが不明。 **Low**
- **データ永続化層が未指定**: Metadata Store (DESIGN.md:84-108) は config/ 配下の YAML 前提に読めるが、 数千リソースを越えた時の検索・更新性能が出ない。 SQLite / Drizzle / Memoria DB のどれを使うかの記述が欲しい。 **Medium**
- **エラー時の挙動が完全に未記述**: 取得失敗・嗜好取得不可・metadata 欠損時の fallback がない。 **High** (Detail は REVIEW_QUALITY 参照)

## 推奨アクション

1. DESIGN.md §7 末尾に LUDIARS 整合節を追加し、 Cernere accessToken 検証 (memory: `feedback_cernere_auth_only_endpoints.md`) と 個人データ非保管 (Cernere sub のみ保持、 嗜好データは Memoria から都度 fetch) を明記。
2. Resolver のスコアリング数式を `score = Σ wᵢ · normalize(featureᵢ)` 形式で例示し、 重み定義場所 (config/resolver/weights.yaml) を仕様化。
3. Metadata 永続化を SQLite + JSON Schema validate (Ajv 相当) に格上げし、 schema migration を versioned に。
4. spec/ ディレクトリを新設し、 API 仕様 (OpenAPI) / データモデル / エラーコード一覧を分離。
