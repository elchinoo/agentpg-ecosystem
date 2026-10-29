CREATE INDEX item_embedding_idx ON item USING hnsw (embedding vector_cosine_ops);
