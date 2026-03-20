-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Function to create standardized domain memory tables
CREATE OR REPLACE FUNCTION create_domain_memories_table(domain_name TEXT)
RETURNS VOID AS $$
DECLARE
    table_name TEXT;
    versions_table TEXT;
BEGIN
    table_name := domain_name || '_memories';
    versions_table := domain_name || '_memory_versions';

    EXECUTE format('
        CREATE TABLE IF NOT EXISTS %I (
            id VARCHAR(50) PRIMARY KEY,
            content TEXT NOT NULL,
            embedding vector(768),
            tags TEXT[] NOT NULL DEFAULT ''{}''::TEXT[],
            metadata JSONB NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            expires_at TIMESTAMP WITH TIME ZONE DEFAULT NULL
        )', table_name);

    -- Create indexes for performance
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I USING gin (metadata)',
                   table_name || '_metadata_idx', table_name);

    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (updated_at DESC)',
                   table_name || '_updated_idx', table_name);

    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I USING gin (to_tsvector(''english'', content))',
                   table_name || '_content_idx', table_name);

    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I USING gin (tags)',
                   table_name || '_tags_idx', table_name);

    -- Create version history table for rollback support
    EXECUTE format('
        CREATE TABLE IF NOT EXISTS %I (
            version_id SERIAL PRIMARY KEY,
            memory_id VARCHAR(50) NOT NULL,
            content TEXT NOT NULL,
            embedding vector(768),
            tags TEXT[] NOT NULL DEFAULT ''{}''::TEXT[],
            metadata JSONB NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )', versions_table);

    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I (memory_id, version_id DESC)',
                   versions_table || '_memory_idx', versions_table);

    -- Create checkpoints table for atomic multi-memory rollback
    EXECUTE format('
        CREATE TABLE IF NOT EXISTS %I (
            id VARCHAR(50) PRIMARY KEY,
            name TEXT NOT NULL,
            tags TEXT[] NOT NULL DEFAULT ''{}''::TEXT[],
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )', domain_name || '_checkpoints');
END;
$$ LANGUAGE plpgsql;

-- Create default domain table
SELECT create_domain_memories_table('default');

-- Example: Create additional domain tables
-- SELECT create_domain_memories_table('startup');
-- SELECT create_domain_memories_table('health');
