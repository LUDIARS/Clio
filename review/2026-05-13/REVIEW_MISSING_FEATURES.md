# REVIEW_MISSING_FEATURES — Clio 機能欠落レビュー (2026-05-13)

## 評価: **D**

DESIGN.md で計画されている機能と現状の差分が「ほぼ 100%」未着手。 さらに LUDIARS 全体観点から見ると、 DESIGN.md 自体にも未記述の必須機能 (認証・観測性・データソース連携) が複数存在。

## DESIGN 計画 vs 現状

| 機能 | DESIGN 記載 | 現状 | 評価 |
|------|-------------|------|------|
| Depot (リソース登録/参照) | DESIGN.md:37-56 | 未実装 | D |
| Acquirer (取得レンジ) | DESIGN.md:58-82 | 未実装 | D |
| Metadata Store | DESIGN.md:84-108 | 未実装 | D |
| Resolver (スコアリング) | DESIGN.md:110-115 | 未実装 | D |
| Preference Adapter | DESIGN.md:117-123 | 未実装 | D |
| Clio メイン API (resolve/acquire/list_resources) | DESIGN.md:194-209 | 未実装 | D |
| YAML config 例示 | DESIGN.md:45-82, 92-108 | サンプル未配置 | D |
| 単体テスト 5 種 | DESIGN.md:157-161 | tests/ 不在 | D |

## DESIGN にも記述されていない LUDIARS 必須機能

### High (LUDIARS 整合性として必須)

1. **Cernere 認証連携**: 全 LUDIARS サービスが Cernere の accessToken を HMAC ローカル検証する原則 (memory: `feedback_cernere_auth_only_endpoints.md`)。 DESIGN.md には認証章が無い。
2. **個人データ非保管**: 嗜好データは Memoria や別の単一情報源にあるべきで、 Clio はキャッシュも持たないべき (memory: `project_personal_data_rule.md`)。 DESIGN.md:121 のキャッシュ記述は要見直し。
3. **Codex 監査ログ**: リソース取得行為は LUDIARS 横断の Codex (memory: `project_codex.md`) に署名済イベントとして残すのが望ましい。 DESIGN.md 言及なし。
4. **Excubitor 連携 (観測性)**: メトリクス・ヘルスチェック・auto-fix の入り口 (memory: `project_excubitor.md`)。 設計書に運用層の記述なし。

### Medium

5. **エラーレスポンス仕様**: API がエラー時に何を返すか (HTTP code / error code / message i18n)。 DESIGN.md:194-209 はハッピーパスのみ。
6. **ページネーション**: `list_resources(filters)` (DESIGN.md:206) が膨大件数を返す場合の cursor/limit 設計なし。
7. **並行性**: 複数 user の同時 resolve に対する rate limit / connection pool 設計なし。
8. **i18n**: リソース名や category のローカライズ (Memoria / Actio に倣う日英対応)。
9. **イベント発火**: Acquirer 結果 / 取得失敗を MQTT/Imperativus 経路で外部通知する仕組み。
10. **データ永続化**: YAML だけだと数千件超で破綻。 SQLite / Drizzle / 既存 LUDIARS DB 流用の選択肢が未記述。

### Low

11. **管理 UI**: Depot / Acquirer ルール編集の Web UI (Memoria/Actio 流の Tauri or Web)。
12. **メトリクス計装**: prometheus 互換 / Excubitor 受け口。
13. **CHANGELOG.md** / **CONTRIBUTING.md** / **CLAUDE.md** / **AGENTS.md** が無い。
14. **README.md** の最小限ドキュメント (現状 1 行)。

## 推奨優先度

| Pri | 機能 | 着手目安 |
|-----|------|----------|
| P0 | README.md / CLAUDE.md / spec/ skeleton | 即時 |
| P0 | Cernere 認証層 + 個人データ非保管原則を DESIGN §7 へ追記 | 即時 |
| P1 | pyproject.toml + CI 雛形 + Depot 最小実装 | 設計確定後 |
| P1 | Resolver スコアリング数式と weights.yaml | Adapter と並行 |
| P2 | Codex 監査連携 / Excubitor metrics | MVP 後 |

## 件数: Critical 0 / High 4 / Medium 6 / Low 4
