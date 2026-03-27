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

## 7. P2Pネットワークモジュール（IPFS / Torrent統合）

### 7.1 概要

リソースをIPFS風のコンテンツアドレッシングで管理し、Torrent風のマルチピア並列転送でやり取りするP2Pネットワーク層です。メッシュネットワーク上でピア発見・コンテンツルーティング・チャンク転送を行います。

### 7.2 アーキテクチャ

```
┌──────────────────────────────────────────────────────┐
│              NetworkCoordinator                       │
│  ┌────────────────┐  ┌──────────────┐               │
│  │  ContentStore   │  │  PeerStore   │               │
│  │ (IPFS-like CAS) │  │ (ピア管理)    │               │
│  └───────┬────────┘  └──────┬───────┘               │
│          │                  │                         │
│  ┌───────▼──────────────────▼───────┐               │
│  │          MeshNetwork             │               │
│  │  (Gossip + Kademlia DHT)         │               │
│  └───────┬──────────────────┬───────┘               │
│          │                  │                         │
│  ┌───────▼────────┐ ┌──────▼───────┐               │
│  │ TorrentManager │ │ StreamManager│               │
│  │ (チャンク転送)   │ │ (ストリーミング)│               │
│  └────────────────┘ └──────────────┘               │
└──────────────────────────────────────────────────────┘
         ▲                      ▲
         │                      │
    他のピア               Clioモジュール
```

### 7.3 コンポーネント詳細

#### ContentStore（コンテンツアドレッシング）

IPFS風のコンテンツアドレスストレージ。データをチャンクに分割し、各チャンクのハッシュ（CID）で一意に識別します。

- **CID (Content Identifier)**: SHA-256/BLAKE2bハッシュによるコンテンツの一意識別子
- **Chunker**: ファイルを固定サイズ（デフォルト256KB）のチャンクに分割
- **Merkle DAG**: チャンクCIDの木構造でファイル全体の整合性を検証
- **ContentManifest**: ファイルの全チャンク情報を含むメタデータ（.torrentファイル相当）

```
File → Chunker → [Chunk₁, Chunk₂, ...] → CID(hash) → Merkle DAG → Root CID
```

#### PeerStore（ピア管理）

ネットワーク上のピアを管理し、コンテンツの提供者を追跡します。

- **PeerID**: 公開鍵ハッシュによるピアの一意識別（libp2p互換）
- **XOR距離**: Kademliaスタイルのルーティング用距離計算
- **レピュテーション**: 転送成功率に基づくピア信頼度スコアリング
- **CIDプロバイダ索引**: どのピアがどのCIDを持つかの逆引きマップ

#### MeshNetwork（メッシュネットワーク）

Gossipプロトコル + Kademlia DHTによるハイブリッドP2Pネットワーク。

- **Gossipプロトコル**: コンテンツアナウンスの伝播（TTL付きフラッディング）
- **Kademlia DHT**: O(log n)のコンテンツルーティング（k-bucket構造）
- **メッセージ種別**: PING/PONG, FIND_PEER, ANNOUNCE, FIND_CONTENT, WANT_BLOCK, HAVE_BLOCK, BLOCK_DATA
- **メッセージ重複排除**: メッセージIDによるデデュプリケーション

#### TorrentManager（Torrent転送）

BitTorrent風のマルチピア並列チャンク転送プロトコル。

- **Bitfield**: 各ピアが持つチャンクのビットマップ管理
- **ピース選択戦略**:
  - `rarest_first`: 最も希少なピースを優先（スウォーム健全性の最大化）
  - `sequential`: 順次取得（ストリーミング用）
  - `random`: ランダム取得（初期フェーズ用）
  - `endgame`: 最後の数ピースを複数ピアに同時リクエスト
- **ピース検証**: CIDハッシュによるデータ整合性検証
- **スウォーム管理**: 複数ピアからの同時ダウンロード調整

#### StreamManager（ストリーミング）

ダウンロード中のコンテンツを逐次再生/処理するためのストリーミング層。

- **StreamBuffer**: 読み取り位置前方のスライディングウィンドウバッファ
- **バッファヘルス**: 先読みウィンドウ内のバッファ充填率監視
- **シーク**: 任意位置への読み取り位置移動

### 7.4 データフロー

```
【パブリッシュ（シーディング）】
1. ローカルファイル → ContentStore.ingest_file() → チャンク分割・CID生成
2. TorrentManager → シーディング転送を作成
3. MeshNetwork.announce_content() → 各チャンクCIDをネットワークに告知

【ダウンロード】
1. ContentManifest受信 → 必要なチャンクCIDのリスト取得
2. MeshNetwork.find_content() → プロバイダピアの探索
3. TorrentTransfer → rarest-firstでピース選択 → 複数ピアに並列リクエスト
4. 各ピースをCIDで検証 → ContentStoreに保存 → Bitfield更新
5. 完了後 → シーディング状態に移行

【ストリーミング】
1. ContentManifest → TorrentTransfer(SEQUENTIAL) 作成
2. StreamBuffer → 読み取り位置前方のチャンクを優先リクエスト
3. FileStream.read_piece() → バッファ済みチャンクを順次返却
4. バッファ不足時 → BUFFERING状態で待機
```

### 7.5 ネットワーク設定

```yaml
# config/network/network.yaml
storage:
  chunk_size: 262144        # 256KB
  hash_algorithm: "sha256"

peers:
  max_peers: 200
  max_gossip_peers: 8

transfer:
  default_strategy: "rarest_first"
  max_concurrent_requests: 10
  request_timeout: 30.0

streaming:
  buffer_ahead: 8

bootstrap_peers:
  - host: "192.168.1.10"
    port: 4001
```

### 7.6 使用例

```python
from src.network.coordinator import NetworkCoordinator, NetworkConfig

# ネットワーク初期化
coordinator = NetworkCoordinator(NetworkConfig())

# ローカルファイルをパブリッシュ（シーディング開始）
manifest = coordinator.publish_file(Path("data/resource.bin"))
print(f"Published: {manifest.root_cid}")

# リモートコンテンツをダウンロード
transfer = coordinator.download(manifest)
print(f"Progress: {transfer.progress:.1%}")

# ストリーミング再生
stream = coordinator.stream(manifest)
for chunk_data in stream.iter_pieces():
    process(chunk_data)

# コンテンツをファイルに保存
coordinator.save_content(manifest.root_cid, Path("output/resource.bin"))
```

### 7.7 ディレクトリ構成（ネットワーク追加分）

```
Clio/
├── config/
│   └── network/
│       └── network.yaml        # ネットワーク設定
├── src/
│   └── network/
│       ├── __init__.py
│       ├── content_store.py    # IPFS風コンテンツアドレスストレージ
│       ├── peer.py             # ピア管理・発見
│       ├── mesh.py             # メッシュネットワーク（Gossip + DHT）
│       ├── torrent.py          # Torrent風チャンク転送プロトコル
│       ├── stream.py           # ファイルストリーミング
│       └── coordinator.py      # ネットワーク統合コーディネーター
└── tests/
    └── test_network.py         # ネットワークモジュールテスト
```

## 8. 設計方針

- **疎結合**: 各コンポーネントは独立して差し替え・テスト可能
- **設定駆動**: リソース定義やレンジはYAML設定ファイルで管理し、コード変更なしで調整可能
- **プラグイン方式**: 嗜好モジュールはアダプタ経由で接続し、異なる嗜好データソースに対応
- **コンテンツアドレッシング**: CIDベースの重複排除と整合性検証による信頼性の高いデータ管理
- **分散型転送**: Torrent方式のマルチピア並列転送でスループット最大化・単一障害点の排除
- **段階的実装**: Depot → Metadata → Acquirer → Adapter → Resolver → Network の順で実装
