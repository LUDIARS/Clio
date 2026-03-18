# Clio モジュール設計書

## 1. 概要

Clioはリソースを抽象化し、個人の嗜好に基づいたオブジェクトをリザーブされた環境から自動取得するモジュールです。

### 名前の由来

Clio（クレイオ）— 記憶と記録を司るミューズに由来し、リソースの発見・取得・管理を担います。

## 2. アーキテクチャ

```
┌─────────────────────────────────────────────────┐
│                   Clio Module                    │
│                                                  │
│  ┌───────────┐  ┌────────────┐  ┌────────────┐  │
│  │  Depot    │  │  Acquirer  │  │  Metadata  │  │
│  │ (共用デポ) │  │ (取得レンジ)│  │  Store     │  │
│  └─────┬─────┘  └─────┬──────┘  └─────┬──────┘  │
│        │              │               │          │
│        └──────┬───────┴───────┬───────┘          │
│               │               │                  │
│        ┌──────▼──────┐ ┌─────▼───────┐          │
│        │  Resolver   │ │  Preference │          │
│        │ (リソース解決)│ │  Adapter    │          │
│        └─────────────┘ │ (嗜好連携)   │          │
│                        └─────────────┘          │
└─────────────────────────────────────────────────┘
         ▲                      ▲
         │                      │
   リザーブ環境            外部嗜好モジュール
```

## 3. コンポーネント詳細

### 3.1 Depot（共用デポ）

リソースの設定・定義を一元管理する共用ストアです。

- **役割**: 利用可能なリソースの登録・参照・更新
- **データ構造**: リソースID、リソース種別、設定パラメータ、利用可否状態
- **管理場所**: `config/depot/`

```yaml
# config/depot/resources.yaml（例）
resources:
  - id: "res-001"
    type: "game"
    name: "リソース名"
    available: true
    reserve_pool: "default"
    parameters:
      region: "jp"
      tier: "standard"
```

### 3.2 Acquirer（取得レンジ）

ゲーム等のオブジェクトを取得する際のレンジ（範囲・条件）を管理します。

- **役割**: 取得対象の検索範囲の定義、フィルタリングルールの適用
- **データ構造**: レンジ定義（範囲、優先度、フィルタ条件）
- **管理場所**: `config/acquirer/`

```yaml
# config/acquirer/ranges.yaml（例）
ranges:
  - name: "default"
    scope: "all"
    priority: 1
    filters:
      - field: "type"
        operator: "eq"
        value: "game"
      - field: "available"
        operator: "eq"
        value: true
    limits:
      max_results: 50
      timeout_ms: 5000
```

### 3.3 Metadata Store（メタデータ管理）

各リソースに紐づくメタデータを管理します。

- **役割**: リソースのタグ、カテゴリ、属性情報の保持・検索
- **データ構造**: メタデータスキーマに基づくキー・バリュー形式
- **管理場所**: `config/metadata/`

```yaml
# config/metadata/schema.yaml（例）
schema:
  fields:
    - name: "tags"
      type: "array"
      description: "リソースに付与するタグ"
    - name: "category"
      type: "string"
      description: "リソースのカテゴリ"
    - name: "rating"
      type: "number"
      description: "リソースの評価値"
    - name: "attributes"
      type: "object"
      description: "リソース固有の属性"
```

### 3.4 Resolver（リソース解決）

嗜好データとリソース定義を突合し、最適なオブジェクトを選定するコアロジックです。

- **役割**: 嗜好スコアリング、リソースランキング、取得候補の決定
- **配置場所**: `src/resolver/`

### 3.5 Preference Adapter（嗜好連携）

外部の嗜好モジュールとの接続を担うアダプタです。

- **役割**: 外部嗜好データの取得・変換・キャッシュ
- **配置場所**: `src/adapter/`
- **インターフェース**: プラグイン方式で嗜好モジュールを差し替え可能

## 4. ディレクトリ構成

```
Clio/
├── DESIGN.md              # 本設計書
├── README.md
├── LICENSE
├── config/
│   ├── depot/             # 共用デポ（リソース設定）
│   │   └── resources.yaml
│   ├── acquirer/          # 取得レンジ定義
│   │   └── ranges.yaml
│   └── metadata/          # メタデータスキーマ・データ
│       └── schema.yaml
├── src/
│   ├── depot/             # Depot モジュール実装
│   │   ├── __init__.py
│   │   └── store.py
│   ├── acquirer/          # Acquirer モジュール実装
│   │   ├── __init__.py
│   │   └── range.py
│   ├── metadata/          # Metadata Store 実装
│   │   ├── __init__.py
│   │   └── store.py
│   ├── resolver/          # Resolver 実装
│   │   ├── __init__.py
│   │   └── engine.py
│   ├── adapter/           # Preference Adapter 実装
│   │   ├── __init__.py
│   │   └── base.py
│   └── clio.py            # Clio メインエントリポイント
└── tests/
    ├── test_depot.py
    ├── test_acquirer.py
    ├── test_metadata.py
    ├── test_resolver.py
    └── test_adapter.py
```

## 5. データフロー

```
1. 外部嗜好モジュール → Preference Adapter → 嗜好データ取得
2. Depot → 利用可能リソース一覧を取得
3. Acquirer → 取得レンジ条件でフィルタリング
4. Metadata Store → 候補リソースのメタデータ付与
5. Resolver → 嗜好データ × メタデータ でスコアリング・ランキング
6. リザーブ環境 → 上位候補を自動取得
```

## 6. 外部インターフェース

### 6.1 嗜好モジュール連携

```python
class PreferenceAdapter:
    """外部嗜好モジュールとの連携インターフェース"""

    def fetch_preferences(self, user_id: str) -> dict:
        """ユーザーの嗜好データを取得する"""
        ...

    def score(self, resource_metadata: dict, preferences: dict) -> float:
        """リソースと嗜好の適合スコアを算出する"""
        ...
```

### 6.2 Clio メインAPI

```python
class Clio:
    """リソース自動取得のメインインターフェース"""

    def resolve(self, user_id: str) -> list[Resource]:
        """嗜好に基づきリソース候補を解決する"""
        ...

    def acquire(self, user_id: str, limit: int = 10) -> list[Resource]:
        """リソースを自動取得する"""
        ...

    def list_resources(self, filters: dict = None) -> list[Resource]:
        """Depotのリソース一覧を取得する"""
        ...
```

## 7. 設計方針

- **疎結合**: 各コンポーネントは独立して差し替え・テスト可能
- **設定駆動**: リソース定義やレンジはYAML設定ファイルで管理し、コード変更なしで調整可能
- **プラグイン方式**: 嗜好モジュールはアダプタ経由で接続し、異なる嗜好データソースに対応
- **段階的実装**: Depot → Metadata → Acquirer → Adapter → Resolver の順で実装
