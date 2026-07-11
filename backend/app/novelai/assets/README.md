# Bundled tokenizer assets

`t5_spiece.model` — the T5 SentencePiece model used to count prompt tokens for the v4/v4.5 usage
indicator (`app/novelai/tokenizer.py`). It is the standard **t5-v1_1** tokenizer
(`google/t5-v1_1-base`, Apache-2.0), vocab ≈ 32000.

NovelAI does not publish the exact vocab their v4 T5 uses; this standard T5 tokenizer matches the web
UI's count closely for English tag prompts. If a count drifts from the web UI, either swap this model
for the correct one or flip `_INCLUDE_EOS` in `tokenizer.py`. It is static data — no secret, safe to
commit.
