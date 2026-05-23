import pytest
from server.vector.store import VectorStore


class TestVectorStore:
    @pytest.fixture
    def store(self, tmp_data_dir):
        return VectorStore(persist_dir=str(tmp_data_dir / "chroma"), collection_name="test_kb")

    def test_add_and_search(self, store):
        ids = ["chunk-1", "chunk-2", "chunk-3"]
        texts = ["苹果是一种水果", "汽车需要加油", "香蕉也是水果"]
        metadatas = [
            {"document_id": "doc-1", "title": "水果百科"},
            {"document_id": "doc-1", "title": "汽车百科"},
            {"document_id": "doc-1", "title": "水果百科"},
        ]
        store.add(ids=ids, texts=texts, metadatas=metadatas)

        results = store.search("水果有哪些", top_k=2)
        assert len(results) > 0
        assert any("苹果" in r["content"] for r in results)
        assert all("document_id" in r for r in results)

    def test_delete_by_document_id(self, store):
        store.add(
            ids=["chunk-a"], texts=["测试内容A"],
            metadatas=[{"document_id": "doc-del"}]
        )
        store.add(
            ids=["chunk-b"], texts=["测试内容B"],
            metadatas=[{"document_id": "doc-keep"}]
        )
        store.delete_by_document_id("doc-del")
        results = store.search("测试内容", top_k=5)
        doc_ids = {r.get("document_id", "") for r in results}
        assert "doc-del" not in doc_ids
        assert "doc-keep" in doc_ids

    def test_count(self, store):
        store.add(
            ids=["c1", "c2"], texts=["a", "b"],
            metadatas=[{"document_id": "d1"}, {"document_id": "d1"}]
        )
        assert store.count() == 2
