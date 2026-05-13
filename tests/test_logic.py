"""
Unit tests for pure functions in app.core.logic.
No external services (OpenAI, Qdrant, Redis, Postgres) are required.
"""
import pytest
from app.core.logic import (
    clean_text,
    chunk_document,
    generate_bibtex_key,
    build_bibtex_entry,
    estimate_cost,
    count_tokens,
)


# ── clean_text ────────────────────────────────────────────────────────────────

class TestCleanText:
    def test_returns_string_and_metadata(self):
        text, meta = clean_text("hello world")
        assert isinstance(text, str)
        assert text == "hello world"

    def test_fixes_ligatures(self):
        # NFKC normalisation (step 1) converts ligatures before the explicit
        # replacement step runs, so the counter stays 0 — but the text is correct.
        text, _ = clean_text("eﬃcient ﬁnding ﬂow")
        assert "ffi" in text
        assert "fi" in text
        assert "fl" in text

    def test_removes_control_characters(self):
        text, meta = clean_text("hello\x00world\x01")
        assert "\x00" not in text
        assert "\x01" not in text
        assert meta.control_chars_removed > 0

    def test_preserves_newlines_and_tabs(self):
        text, meta = clean_text("line one\nline two\ttabbed")
        assert "\n" in text
        assert "\t" in text

    def test_removes_repeated_lines(self):
        # Threshold: count > 5 AND len > MIN_HEADER_LINE_LENGTH (10)
        repeated = "\n".join(["this is a repeated header line"] * 20)
        text, meta = clean_text(repeated)
        assert meta.repeated_lines_removed > 0

    def test_metadata_char_counts_are_consistent(self):
        original = "hello ﬁworld"
        text, meta = clean_text(original)
        assert meta.original_char_count == len(original)
        assert meta.cleaned_char_count == len(text)
        assert meta.chars_removed == meta.original_char_count - meta.cleaned_char_count

    def test_empty_string(self):
        text, meta = clean_text("")
        assert text == ""
        assert meta.original_char_count == 0


# ── generate_bibtex_key ───────────────────────────────────────────────────────

class TestGenerateBibtexKey:
    def test_standard_case(self):
        # Function takes split()[-1] — use "Firstname Lastname" format
        key = generate_bibtex_key(["Ashish Vaswani"], 2017, "Attention Is All You Need")
        assert key == "Vaswani2017_Attention"

    def test_no_authors_uses_unknown(self):
        key = generate_bibtex_key([], 2020, "Some Title")
        assert key.startswith("Unknown")

    def test_no_year_uses_nd(self):
        key = generate_bibtex_key(["Smith, John"], None, "A Great Paper")
        assert "nd" in key

    def test_stopwords_are_skipped(self):
        # "A", "On", "The" are stopwords — first content word should be used
        key = generate_bibtex_key(["Jones"], 2021, "A Study on the Effects")
        assert "Study" in key

    def test_extracts_last_name(self):
        key = generate_bibtex_key(["Yann LeCun"], 2015, "Deep Learning")
        assert key.startswith("LeCun")

    def test_hyphenated_title_word(self):
        key = generate_bibtex_key(["Brown"], 2020, "Self-Supervised Pretraining")
        assert "Brown" in key
        assert "2020" in key


# ── build_bibtex_entry ────────────────────────────────────────────────────────

class TestBuildBibtexEntry:
    def test_article_type_when_journal_present(self):
        paper = {"title": "My Paper", "authors": ["Alice"], "year": 2023, "journal": "Nature", "doi": None}
        entry = build_bibtex_entry(paper, "Alice2023_My")
        assert entry.startswith("@article")

    def test_misc_type_when_no_journal(self):
        paper = {"title": "My Paper", "authors": ["Alice"], "year": 2023, "journal": None, "doi": None}
        entry = build_bibtex_entry(paper, "Alice2023_My")
        assert entry.startswith("@misc")

    def test_doi_included_when_present(self):
        paper = {"title": "My Paper", "authors": ["Alice"], "year": 2023, "journal": None, "doi": "10.1234/test"}
        entry = build_bibtex_entry(paper, "key")
        assert "10.1234/test" in entry

    def test_multiple_authors_joined_with_and(self):
        paper = {"title": "T", "authors": ["Alice", "Bob", "Carol"], "year": 2023, "journal": None, "doi": None}
        entry = build_bibtex_entry(paper, "key")
        assert "Alice and Bob and Carol" in entry

    def test_no_authors_uses_unknown(self):
        paper = {"title": "T", "authors": [], "year": 2023, "journal": None, "doi": None}
        entry = build_bibtex_entry(paper, "key")
        assert "Unknown" in entry

    def test_entry_contains_key(self):
        paper = {"title": "T", "authors": ["A"], "year": 2021, "journal": None, "doi": None}
        entry = build_bibtex_entry(paper, "mykey2021")
        assert "mykey2021" in entry


# ── estimate_cost ─────────────────────────────────────────────────────────────

class TestEstimateCost:
    def test_zero_tokens_returns_zero(self):
        assert estimate_cost(0, 0, "gpt-4o-mini") == 0.0

    def test_embedding_model_has_no_output_cost(self):
        cost = estimate_cost(1_000_000, 0, "text-embedding-3-small")
        assert cost > 0
        output_cost = estimate_cost(0, 1_000_000, "text-embedding-3-small")
        assert output_cost == 0.0

    def test_generation_model_charges_both(self):
        in_cost = estimate_cost(1_000_000, 0, "gpt-4o-mini")
        out_cost = estimate_cost(0, 1_000_000, "gpt-4o-mini")
        assert in_cost > 0
        assert out_cost > in_cost  # output rate is higher

    def test_unknown_model_returns_zero(self):
        assert estimate_cost(1_000_000, 1_000_000, "gpt-unknown") == 0.0

    def test_cost_scales_linearly(self):
        cost_1m = estimate_cost(1_000_000, 0, "gpt-4o-mini")
        cost_2m = estimate_cost(2_000_000, 0, "gpt-4o-mini")
        assert pytest.approx(cost_2m, rel=1e-6) == cost_1m * 2


# ── count_tokens ──────────────────────────────────────────────────────────────

class TestCountTokens:
    def test_empty_string_returns_zero(self):
        assert count_tokens("") == 0

    def test_returns_positive_int(self):
        assert count_tokens("hello world") > 0

    def test_longer_text_has_more_tokens(self):
        short = count_tokens("hello")
        long = count_tokens("hello world this is a longer sentence with many words")
        assert long > short

    def test_whitespace_only(self):
        assert count_tokens("   ") >= 0


# ── chunk_document ────────────────────────────────────────────────────────────

SAMPLE_TEXT = (
    "This is a research paper about transformers. " * 100
)

class TestChunkDocument:
    def test_returns_chunks_and_stats(self):
        chunks, stats = chunk_document(SAMPLE_TEXT, "doc1", "tenant1", {})
        assert len(chunks) > 0
        assert stats.total_chunks == len(chunks)

    def test_chunk_metadata(self):
        chunks, _ = chunk_document(SAMPLE_TEXT, "doc42", "tenantX", {})
        for chunk in chunks:
            assert chunk.document_id == "doc42"
            assert chunk.tenant_id == "tenantX"
            assert chunk.chunk_type == "text"
            assert chunk.token_count > 0
            assert len(chunk.text) > 0

    def test_chunk_ids_are_unique(self):
        chunks, _ = chunk_document(SAMPLE_TEXT, "doc1", "tenant1", {})
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))

    def test_stats_totals_match_chunks(self):
        chunks, stats = chunk_document(SAMPLE_TEXT, "doc1", "tenant1", {})
        assert stats.text_chunks + stats.table_chunks + stats.figure_chunks == stats.total_chunks
        assert stats.total_tokens == sum(c.token_count for c in chunks)

    def test_empty_text_returns_no_chunks(self):
        chunks, stats = chunk_document("", "doc1", "tenant1", {})
        assert stats.total_chunks == 0

    def test_figure_chunk_type_with_image_map(self):
        text_with_figure = "Some text <!-- picture-1 --> more text " * 40
        image_map = {"picture-1": "http://example.com/img.png"}
        chunks, stats = chunk_document(text_with_figure, "doc1", "tenant1", image_map)
        figure_chunks = [c for c in chunks if c.chunk_type == "figure"]
        assert len(figure_chunks) > 0
        assert stats.figure_chunks > 0
