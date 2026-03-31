from pathlib import Path

import json

from private_agent.sync.server import MultiPeerInventorySyncStore, load_effective_inventory_snapshot


def test_multi_peer_sync_store_keeps_separate_databases_per_ip(tmp_path: Path) -> None:
    store = MultiPeerInventorySyncStore(
        root=tmp_path / "inventory_sync",
        knowledge_root=tmp_path / "knowledge",
    )

    first_summary = store.save_snapshot_for_ip(
        {
            "exported_at": "2026-03-31T10:00:00Z",
            "app_version": "android-a",
            "storages": [
                {"name": "Kitchen", "boxes": [{"name": "Top", "items": [{"name": "Milk", "quantity": 1, "unit": "Bottle"}]}]}
            ],
        },
        client_ip="192.168.1.10",
        source="android-a",
    )
    second_summary = store.save_snapshot_for_ip(
        {
            "exported_at": "2026-03-31T10:05:00Z",
            "app_version": "android-b",
            "storages": [
                {"name": "Garage", "boxes": [{"name": "Shelf", "items": [{"name": "Water", "quantity": 2, "unit": "Pack"}]}]}
            ],
        },
        client_ip="10.0.0.7",
        source="android-b",
    )

    assert first_summary["peer_key"] == "192.168.1.10"
    assert second_summary["peer_key"] == "10.0.0.7"
    assert (tmp_path / "inventory_sync" / "peers" / "192.168.1.10" / "current_inventory.json").exists()
    assert (tmp_path / "inventory_sync" / "peers" / "10.0.0.7" / "current_inventory.json").exists()


def test_multi_peer_sync_store_returns_changes_only_for_requesting_ip(tmp_path: Path) -> None:
    store = MultiPeerInventorySyncStore(
        root=tmp_path / "inventory_sync",
        knowledge_root=tmp_path / "knowledge",
    )

    kitchen_store = store._peer_store("192.168.1.10")
    kitchen_store.upsert_item(
        storage_name="Kitchen",
        box_name="Door",
        item_name="Juice",
        quantity=1.0,
        unit="Bottle",
        category=None,
        note=None,
    )
    garage_store = store._peer_store("10.0.0.7")
    garage_store.upsert_item(
        storage_name="Garage",
        box_name="Shelf",
        item_name="Water",
        quantity=2.0,
        unit="Pack",
        category=None,
        note=None,
    )

    first_changes = store.get_changes_for_ip(0, client_ip="192.168.1.10")
    second_changes = store.get_changes_for_ip(0, client_ip="10.0.0.7")

    assert len(first_changes["changes"]) == 1
    assert first_changes["changes"][0]["item_name"] == "Juice"
    assert first_changes["peer_key"] == "192.168.1.10"
    assert len(second_changes["changes"]) == 1
    assert second_changes["changes"][0]["item_name"] == "Water"
    assert second_changes["peer_key"] == "10.0.0.7"


def test_multi_peer_sync_store_replicates_telegram_changes_to_known_peers(tmp_path: Path) -> None:
    store = MultiPeerInventorySyncStore(
        root=tmp_path / "inventory_sync",
        knowledge_root=tmp_path / "knowledge",
    )
    store.save_snapshot_for_ip(
        {
            "exported_at": "2026-03-31T10:00:00Z",
            "app_version": "android-a",
            "storages": [{"name": "Kitchen", "boxes": [{"name": "Top", "items": []}]}],
        },
        client_ip="192.168.1.10",
        source="android-a",
    )

    result = store.upsert_item(
        storage_name="Kitchen",
        box_name="Top",
        item_name="Eggs",
        quantity=2.0,
        unit="Box",
        category="Food",
        note=None,
    )

    peer_changes = store.get_changes_for_ip(0, client_ip="192.168.1.10")
    assert result["change"]["item_name"] == "Eggs"
    assert len(peer_changes["changes"]) == 1
    assert peer_changes["changes"][0]["item_name"] == "Eggs"
    assert store._global_store().get_changes(0)["changes"] == []


def test_multi_peer_sync_store_preserves_pending_telegram_changes_in_global_snapshot(tmp_path: Path) -> None:
    store = MultiPeerInventorySyncStore(
        root=tmp_path / "inventory_sync",
        knowledge_root=tmp_path / "knowledge",
    )
    store.save_snapshot_for_ip(
        {
            "exported_at": "2026-03-31T10:00:00Z",
            "app_version": "android-a",
            "storages": [{"name": "Kitchen", "boxes": [{"name": "Top", "items": []}]}],
        },
        client_ip="192.168.1.10",
        source="android-a",
    )
    store.upsert_item(
        storage_name="Kitchen",
        box_name="Top",
        item_name="Eggs",
        quantity=2.0,
        unit="Box",
        category="Food",
        note=None,
    )

    store.save_snapshot_for_ip(
        {
            "exported_at": "2026-03-31T10:01:00Z",
            "app_version": "android-a",
            "storages": [{"name": "Kitchen", "boxes": [{"name": "Top", "items": []}]}],
        },
        client_ip="192.168.1.10",
        source="android-a",
    )

    global_snapshot = (tmp_path / "inventory_sync" / "current_inventory.json").read_text(encoding="utf-8")
    assert "Eggs" in global_snapshot


def test_load_effective_inventory_snapshot_overlays_peer_pending_changes(tmp_path: Path) -> None:
    store = MultiPeerInventorySyncStore(
        root=tmp_path / "inventory_sync",
        knowledge_root=tmp_path / "knowledge",
    )
    store.save_snapshot_for_ip(
        {
            "exported_at": "2026-03-31T10:00:00Z",
            "app_version": "android-a",
            "storages": [{"name": "Kitchen", "boxes": [{"name": "Top", "items": []}]}],
        },
        client_ip="192.168.1.10",
        source="android-a",
    )
    store.upsert_item(
        storage_name="Kitchen",
        box_name="Top",
        item_name="Eggs",
        quantity=2.0,
        unit="Box",
        category="Food",
        note=None,
    )

    payload = load_effective_inventory_snapshot(tmp_path / "inventory_sync")

    assert payload is not None
    assert "Eggs" in json.dumps(payload, ensure_ascii=False)
