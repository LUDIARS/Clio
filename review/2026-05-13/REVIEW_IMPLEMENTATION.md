# REVIEW_IMPLEMENTATION — Clio 実装レビュー (2026-05-13)

## 評価: **D**

実装ファイルが 1 つも存在しない。 `git ls-files` で取得できるソースは README.md / DESIGN.md / LICENSE のみで、 DESIGN.md:139-161 に列挙された `src/depot/store.py` 等は 1 ファイルも作成されていない。 実装レビューの対象が無いため D 評価。

## 現状の追跡ファイル

- `README.md` (1 行: `# Clio` のみ) — 該当: README.md:1
- `DESIGN.md` (217 行: 設計書) — 該当: DESIGN.md:1-217
- `LICENSE`
- (`src/` ディレクトリ自体が存在しない、 `tests/` も同様、 `config/` も同様)

## 不在ファイル一覧 (DESIGN.md の計画と現状の差分)

DESIGN.md:139-155 で計画されている下記ファイルが全て未作成:

- `src/clio.py` (メインエントリポイント) — 該当: DESIGN.md:155
- `src/depot/__init__.py`, `src/depot/store.py` — 該当: DESIGN.md:140-142
- `src/acquirer/__init__.py`, `src/acquirer/range.py` — 該当: DESIGN.md:143-145
- `src/metadata/__init__.py`, `src/metadata/store.py` — 該当: DESIGN.md:146-148
- `src/resolver/__init__.py`, `src/resolver/engine.py` — 該当: DESIGN.md:149-151
- `src/adapter/__init__.py`, `src/adapter/base.py` — 該当: DESIGN.md:152-154
- `config/depot/resources.yaml`, `config/acquirer/ranges.yaml`, `config/metadata/schema.yaml` — 該当: DESIGN.md:133-138
- `tests/test_depot.py`, `tests/test_acquirer.py`, `tests/test_metadata.py`, `tests/test_resolver.py`, `tests/test_adapter.py` — 該当: DESIGN.md:157-161

## 言語・ランタイム選定の懸念

DESIGN.md:180-209 のコード片は Python 想定 (`def`, `dict`, `list[Resource]`) だが:

- pyproject.toml / requirements.txt / poetry.lock が無く、 Python バージョン (3.11/3.12/3.13) が決まっていない。
- LUDIARS の他 Web 系 (Memoria/Actio) は TypeScript + Drizzle が主流。 単独 Python を選ぶ理由を README/DESIGN に明記すべき。
- ergo (memory: `project_ergo_tools.md`) は Node 単一サーバに集約しており、 Clio の嗜好取得層を ergo plugin 化 (Node) する選択肢もある。 設計書段階で判断保留が望ましい。

## CI / 開発体験

- `.github/workflows/` 不在: lint / test / type-check の自動実行が無い。
- `pre-commit` 設定不在: 設計書しかないため当面は markdownlint だけで十分だが、 LUDIARS 標準の Codex 監査 hook が後で必要。
- `.editorconfig` / `.gitattributes` 不在 (LF 統一の保証なし)。

## 推奨アクション (実装着手時の最小単位)

1. `pyproject.toml` (poetry / uv 想定) を追加し、 Python 3.12 ピン留め + ruff / mypy / pytest を dev 依存に登録。
2. DESIGN.md §4 のディレクトリ構造を `mkdir -p` で空でも生成し、 `src/__init__.py` だけ先に commit (プロジェクトレイアウト確定)。
3. 最初の PR は `Depot` (DESIGN.md:37-56) の `load_resources()` 1 関数 + その pytest 1 件 (#happy path) に絞る。 LUDIARS の `feedback_ai_pr_size.md` (一気通貫実装は 1 PR 集約) との両立は、 1 機能 = 1 PR を厳守し scope creep を防ぐ。
4. `.github/workflows/ci.yml` で `ruff check . && mypy src && pytest -q` を Python 3.12 でラン。

## Critical 0 / High 1 (実装着手前) / Medium 3 / Low 2
