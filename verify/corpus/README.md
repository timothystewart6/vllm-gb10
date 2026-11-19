# Vendored llama-benchy benchmark corpus

`verify/run-verify.sh` serves the file in this directory to the in-container
llama-benchy over loopback HTTP (`--book-url`), so the bench stages never
depend on an external website at runtime. On 2026-11-19 Project Gutenberg
returned HTTP 503 and every bench stage in build-image.yaml run 44567984711
died with `Error downloading or processing book: 503`; benchy 0.4.0 fetches
its corpus with `requests.get` at startup and exits 1 when the fetch fails.

## Provenance

| Field | Value |
| --- | --- |
| File | `the-adventures-of-sherlock-holmes.txt` |
| Title | The Adventures of Sherlock Holmes |
| Author | Arthur Conan Doyle |
| First published | 1892 |
| Copyright status | Public domain in the United States (published before 1930), per the Project Gutenberg license statement in the file header |
| Upstream release | Project Gutenberg ebook #1661, `https://www.gutenberg.org/files/1661/1661-0.txt` (the exact upstream default corpus for llama-benchy) |
| Retrieved from | Internet Archive Wayback Machine raw-bytes snapshot `https://web.archive.org/web/20261101033725id_/https://www.gutenberg.org/files/1661/1661-0.txt` (snapshot of the original Gutenberg path, unmodified by the replay layer) |
| Downloaded | 2026-11-19 |
| Size | 607,504 bytes |
| Format | UTF-8 with BOM, CRLF line endings, stored byte-for-byte as served upstream |
| sha256 | `b9c105a5c0cf9fcb37c7d0211658851ed56074519c9455d15e20f04c0c5a3174` |

## Why this source

- `gutenberg.org` (direct files and cache paths) was returning HTTP 503 from
  the CI network on 2026-11-19, which is the outage this vendoring fixes.
- The `gutenberg.net.au` mirror did not work for this file: its catalog uses
  its own numbering, and `ebooks01/0100011.txt` is a Project Gutenberg
  Australia edition of a different book (animal farm), not a copy of
  Gutenberg ebook #1661. It was checked on 2026-11-19, failed the content
  marker checks (no title, author, or Gutenberg START/END markers), and was
  not used.
- Wayback's `id_` variant serves the untouched archived bytes, so this file
  is identical to what llama-benchy downloaded from gutenberg.org when the
  site was healthy, including the Gutenberg license header.

## Rules

- Keep the file byte-for-byte as published. Do not reformat, rewrap, or
  strip the Gutenberg header. benchy itself truncates everything before the
  `*** START OF THE PROJECT GUTENBERG EBOOK` line at load time.
- `tests/test-verify-harness.py` asserts the file exists, is sized like the
  real text, and contains the title, author, and Gutenberg START/END
  markers. If you replace the corpus, update the sha256, size, and retrieval
  fields in this README in the same commit.
- This provenance note is intentionally local to `verify/corpus/`; the
  top-level `checksums/` registry tracks build inputs, not verify test data.
