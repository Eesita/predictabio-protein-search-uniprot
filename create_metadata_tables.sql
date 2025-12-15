-- PostgreSQL Table Creation Script for Metadata Pipeline
-- Creates 4 tables for each stage of metadata processing

-- Stage 1: Initial fetched metadata
CREATE TABLE IF NOT EXISTS metadata_fetched (
    id TEXT,
    title TEXT,
    abstract TEXT,
    authors TEXT,
    journal TEXT,
    "publication year" INTEGER,
    doi TEXT,
    pmid TEXT,
    citations INTEGER,
    "open access" BOOLEAN,
    source TEXT,
    links JSONB,
    fetched_at TIMESTAMP,
    "pubmed link" TEXT,
    "external download url" TEXT,
    content_uuid TEXT,
    accession TEXT
);

-- Stage 2: After growth factor filtering
CREATE TABLE IF NOT EXISTS metadata_growth_factor_filtered (
    id TEXT,
    title TEXT,
    abstract TEXT,
    authors TEXT,
    journal TEXT,
    "publication year" INTEGER,
    doi TEXT,
    pmid TEXT,
    citations INTEGER,
    "open access" BOOLEAN,
    source TEXT,
    links JSONB,
    fetched_at TIMESTAMP,
    "pubmed link" TEXT,
    "external download url" TEXT,
    content_uuid TEXT,
    Growth_Factor_Name TEXT,
    accession TEXT
);

-- Stage 3: After parameter extraction
CREATE TABLE IF NOT EXISTS metadata_parameters_extracted (
    id TEXT,
    title TEXT,
    abstract TEXT,
    authors TEXT,
    journal TEXT,
    "publication year" INTEGER,
    doi TEXT,
    pmid TEXT,
    citations INTEGER,
    "open access" BOOLEAN,
    source TEXT,
    links JSONB,
    fetched_at TIMESTAMP,
    "pubmed link" TEXT,
    "external download url" TEXT,
    content_uuid TEXT,
    Growth_Factor_Name TEXT,
    Q1_Protein_Production TEXT,
    Q2_Reasoning TEXT,
    Q3_Recombinant_Proteins TEXT,
    Q4_Species TEXT,
    Q5_Host_Organism TEXT,
    accession TEXT
);

-- Stage 4: Final filtered results
CREATE TABLE IF NOT EXISTS metadata_final_filtered (
    id TEXT,
    title TEXT,
    abstract TEXT,
    authors TEXT,
    journal TEXT,
    "publication year" INTEGER,
    doi TEXT,
    pmid TEXT,
    citations INTEGER,
    "open access" BOOLEAN,
    source TEXT,
    links JSONB,
    fetched_at TIMESTAMP,
    "pubmed link" TEXT,
    "external download url" TEXT,
    content_uuid TEXT,
    Growth_Factor_Name TEXT,
    Q1_Protein_Production TEXT,
    Q2_Reasoning TEXT,
    Q3_Recombinant_Proteins TEXT,
    Q4_Species TEXT,
    Q5_Host_Organism TEXT,
    "pdf found" BOOLEAN,
    accession TEXT
);

-- Verification: List all created tables
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name LIKE 'metadata_%'
ORDER BY table_name;

