# Bundled tokenizer assets

`t5_spiece.model` — the T5 SentencePiece model used to count prompt tokens for the v4/v4.5 usage
indicator (`app/novelai/tokenizer.py`). It is the standard **t5-v1_1** tokenizer
(`google/t5-v1_1-base`, Apache-2.0), vocab ≈ 32000.

NovelAI does not publish the exact vocab their v4 T5 uses; this standard T5 tokenizer matches the web
UI's count closely for English tag prompts. If a count drifts from the web UI, either swap this model
for the correct one or flip `_INCLUDE_EOS` in `tokenizer.py`. It is static data — no secret, safe to
commit.

**Integrity.** `t5_spiece.model` sha256:

```
d60acb128cf7b7f2536e8f38a5b18a05535c9e14c7a355904270e15b0945ea86
```

Recorded so a silent swap of this bundled binary is visible in review. Verify with
`sha256sum backend/app/novelai/assets/t5_spiece.model`.

---

`qwen35_tokenizer.def` — the **Qwen 3.5** byte-level BPE vocabulary used to count prompt tokens for the
v5 usage indicator. It is the exact file NovelAI's web client loads
(`https://novelai.net/tokenizer/compressed/qwen35_tokenizer.def`): raw-deflate JSON with `config`
(pre-split regex, NFC normalization), `specialTokens`, `vocab` (248,070) and `merges` (247,587).
`tokenizer.py` ports the web client's encoder; on a 320-prompt corpus (tags, weights, Japanese, special
tokens, random unicode) its counts matched the web encoder run on this same file exactly. The vocabulary
is the Qwen team's (Qwen3.5, Apache-2.0). To update it, replace the file with the web client's current
copy and re-check the reference counts in `tests/test_tokenize.py`.

**Integrity.** `qwen35_tokenizer.def` sha256:

```
f4040d875827d2f9edc30dd4d736bc4a853a19ceeb0d04f6e3648f17796d1667
```
