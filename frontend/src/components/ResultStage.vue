<script setup lang="ts">
import type { GenResult } from '../types'

defineProps<{
  selected: GenResult | null
  results: GenResult[]
  busy: boolean
  error: string
}>()
defineEmits<{ select: [GenResult] }>()
</script>

<template>
  <section class="stage">
    <div class="hd">
      <h2>Generate</h2>
      <span class="count" v-if="results.length">· {{ results.length }} result(s) this session</span>
    </div>

    <div class="canvas">
      <div v-if="error" class="notice error">
        <div class="ttl">Generation failed</div>
        <div class="msg">{{ error }}</div>
      </div>
      <div v-else-if="busy" class="notice">
        <div class="spinner"></div>
        <div class="msg">Generating…</div>
      </div>
      <div v-else-if="selected" class="frame">
        <img :src="selected.url" alt="generation" />
        <div class="imgbar">
          <span class="meta">
            <span v-if="selected.mock" class="mock">mock</span>
            seed {{ selected.params.seed ?? 'random' }} · {{ selected.params.width }}×{{ selected.params.height }} ·
            {{ selected.params.steps }} steps
          </span>
        </div>
      </div>
      <div v-else class="notice empty">
        <div class="ttl">Nothing generated yet</div>
        <div class="msg">Set your prompt and press Generate — results show up here.</div>
      </div>
    </div>

    <div class="filmstrip" v-if="results.length">
      <div
        v-for="r in results"
        :key="r.id"
        class="thumb"
        :class="{ sel: selected?.id === r.id }"
        @click="$emit('select', r)"
      >
        <img :src="r.url" alt="result" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.stage{display:flex;flex-direction:column;min-width:0;background:var(--bg)}
.hd{display:flex;align-items:center;gap:10px;padding:15px 20px;border-bottom:1px solid var(--border);flex-shrink:0}
.hd h2{margin:0;font-size:15px;font-weight:600}
.hd .count{font-size:12px;color:var(--text-faint)}
.canvas{flex:1;display:flex;align-items:center;justify-content:center;padding:22px;min-height:0}
.frame{position:relative;height:100%;max-height:560px;max-width:100%;border-radius:var(--radius-lg);
  overflow:hidden;border:1px solid var(--border);background:var(--surface-2)}
.frame img{height:100%;max-width:100%;object-fit:contain;display:block}
.imgbar{position:absolute;left:10px;right:10px;bottom:10px;display:flex;align-items:center;gap:8px;
  padding:7px 11px;border-radius:6px;background:color-mix(in srgb,#000 58%,transparent);color:#fff;font-size:12px}
.imgbar .meta{flex:1;opacity:.92;font-variant-numeric:tabular-nums;display:flex;align-items:center;gap:8px}
.mock{font-size:10px;font-weight:700;letter-spacing:.4px;background:#b65c02;color:#fff;padding:1px 6px;border-radius:20px}
.notice{display:flex;flex-direction:column;align-items:center;gap:10px;text-align:center;max-width:340px}
.notice .ttl{font-weight:600;font-size:15px}
.notice .msg{color:var(--text-dim);font-size:13px}
.notice.error{color:var(--text)}
.notice.error .ttl{color:#e2483d}
.spinner{width:26px;height:26px;border-radius:50%;border:3px solid var(--border-strong);
  border-top-color:var(--accent);animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.filmstrip{flex-shrink:0;display:flex;gap:10px;padding:12px 20px 18px;overflow-x:auto;align-items:center;
  border-top:1px solid var(--border)}
.thumb{width:58px;height:84px;border-radius:6px;overflow:hidden;border:1px solid var(--border);cursor:pointer;flex-shrink:0}
.thumb.sel{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 35%,transparent)}
.thumb img{width:100%;height:100%;object-fit:cover}
</style>
